#!/usr/bin/env python3
"""glint-memory: local, private "search everything I've seen" tool.

How memory works:

  capture -> memory_chunks    one row per screen/window snapshot, skipped when
                              (content_hash, app_id, window_title) already exists
          -> memory_passages  chunk split into passages, globally deduped by a
                              64-bit hash, linked by next_hash for context expansion
          -> SQLite FTS5 (bm25) + all-MiniLM-L6-v2 embeddings (384-d, cosine)
  search  -> hybrid: keyword and semantic ranked lists fused with Reciprocal
             Rank Fusion; falls back to keyword-only if embedding fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import warnings
from pathlib import Path
from urllib.parse import urlparse

warnings.filterwarnings("ignore", message=".*OpenSSL.*")

ROOT = Path(__file__).resolve().parent            # <repo>/engine
HOME = Path(os.environ.get("GLINT_HOME", Path.home() / "Library" / "Application Support" / "Glint"))
DB_PATH = HOME / "memory.db"
MODEL_DIR = HOME / "models"
CAPTURE_SRC = ROOT.parent / "mac" / "capture" / "GlintCapture.swift"
CAPTURE_BIN = ROOT.parent / "bin" / "glint-capture"

# Sentence embeddings: all-MiniLM-L6-v2 via fastembed (ONNX, 384-d, runs locally).
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
PASSAGE_CHARS = 450   # greedy line packing: current + next line <= 450 chars
MIN_PASSAGE_CHARS = 20
# Hybrid search parameters:
#   limit  = clamp(limit, 1, 48)
#   depth  = clamp(3 * limit, 24, 96)   candidates fetched per ranked list
#   score += 1 / (60 + rank)            RRF, k = 60.0f, equal weights per list
RRF_K = 60
MAX_LIMIT = 48
MIN_COSINE = 0.20     # semantic hits below this are noise for MiniLM (glint-memory only)


def candidate_depth(limit: int) -> int:
    return min(max(3 * limit, 24), 96)

# Query stopwords: conversational filler and common English function words.
STOPWORDS = set("""
he us having further im doesnt arent wasnt go gonna thing things lot bit ve ll re
don doesn isn won yet a an and are as at be but by can could did do does for from
had has have her his how i if in into is it its just me my of on or our she so
than that the their them then there these they this to too was we were what when
where which who why will with would you your about find show tell
""".split())

DEFAULT_BLOCKED_APPS = {"1password", "keychain access", "bitwarden", "dashlane",
                        "lastpass", "passwords", "keeper password manager"}

TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".org", ".tex", ".csv", ".json",
            ".yaml", ".yml", ".toml", ".html", ".css", ".sql", ".sh", ".py", ".js",
            ".ts", ".tsx", ".jsx", ".rs", ".go", ".swift", ".java", ".kt", ".c",
            ".h", ".cpp", ".rb", ".php"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "data", "bin",
             "dist", "build", "target"}

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS memory_chunks (
    id           TEXT PRIMARY KEY,
    app_id       TEXT NOT NULL,
    window_title TEXT NOT NULL DEFAULT '',
    url_host     TEXT,
    captured_at  INTEGER NOT NULL,              -- unix ms
    source       TEXT NOT NULL,                 -- ax | ocr | file | browser | manual
    text         TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    char_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chunks_captured ON memory_chunks(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_chunks_app ON memory_chunks(app_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON memory_chunks(content_hash, app_id, window_title);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_chunks_fts USING fts5(
    text, app_id, window_title,
    content='memory_chunks', content_rowid='rowid', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS memory_chunks_ai AFTER INSERT ON memory_chunks BEGIN
    INSERT INTO memory_chunks_fts(rowid, text, app_id, window_title)
    VALUES (new.rowid, new.text, new.app_id, new.window_title);
END;
CREATE TRIGGER IF NOT EXISTS memory_chunks_ad AFTER DELETE ON memory_chunks BEGIN
    INSERT INTO memory_chunks_fts(memory_chunks_fts, rowid, text, app_id, window_title)
    VALUES ('delete', old.rowid, old.text, old.app_id, old.window_title);
END;

CREATE TABLE IF NOT EXISTS memory_passages (
    id          INTEGER PRIMARY KEY,
    chunk_id    TEXT NOT NULL,
    hash        INTEGER NOT NULL UNIQUE,
    next_hash   INTEGER,
    captured_at INTEGER NOT NULL,
    text        TEXT NOT NULL,
    embedding   BLOB                            -- float32[384], L2-normalised
);
CREATE INDEX IF NOT EXISTS idx_passages_chunk ON memory_passages(chunk_id);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_passages_fts USING fts5(
    text, content='memory_passages', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS memory_passages_ai AFTER INSERT ON memory_passages BEGIN
    INSERT INTO memory_passages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memory_passages_ad AFTER DELETE ON memory_passages BEGIN
    INSERT INTO memory_passages_fts(memory_passages_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS memory_chunks_passages_ad AFTER DELETE ON memory_chunks BEGIN
    DELETE FROM memory_passages WHERE chunk_id = old.id;
END;
"""


# --------------------------------------------------------------------------- utils

def now_ms() -> int:
    return int(time.time() * 1000)


def warn(msg: str) -> None:
    print(msg, file=sys.stderr)


def open_db() -> sqlite3.Connection:
    HOME.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    return db


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def hash64(text: str) -> int:
    digest = hashlib.blake2b(normalize(text).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def ago(ms: int) -> str:
    s = max(0, (now_ms() - ms) // 1000)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit} ago"
    return "just now"


# ---------------------------------------------------------------------- embedding

class Embedder:
    _model = None

    @classmethod
    def embed(cls, texts: list[str]):
        import numpy as np
        if cls._model is None:
            from fastembed import TextEmbedding
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            cls._model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(MODEL_DIR))
        vecs = np.array(list(cls._model.embed(texts, batch_size=64)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


# ----------------------------------------------------------------------- indexing

def split_passages(text: str) -> list[str]:
    """Pack lines into ~PASSAGE_CHARS passages, hard-wrapping very long lines."""
    pieces: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        while len(line) > PASSAGE_CHARS:
            cut = max(line.rfind(". ", 0, PASSAGE_CHARS), line.rfind("? ", 0, PASSAGE_CHARS))
            if cut < PASSAGE_CHARS // 2:
                cut = line.rfind(" ", 0, PASSAGE_CHARS)
            if cut <= 0:
                cut = PASSAGE_CHARS - 1
            pieces.append(line[:cut + 1].strip())
            line = line[cut + 1:].strip()
        if line:
            pieces.append(line)

    passages: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + len(piece) > PASSAGE_CHARS:
            passages.append(cur)
            cur = piece
        else:
            cur = f"{cur}\n{piece}" if cur else piece
    if cur:
        passages.append(cur)

    kept = [p for p in passages if len(p) >= MIN_PASSAGE_CHARS]
    if kept:
        return kept
    joined = " ".join(pieces).strip()
    return [joined] if joined else []


def add_chunk(db, text, app_id, title="", url_host=None, source="manual", captured_at=None):
    """Insert a snapshot. Returns the chunk id, or None if unchanged/empty."""
    text = (text or "").strip()
    if not text:
        return None
    title = title or ""
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    if db.execute(
        "SELECT 1 FROM memory_chunks WHERE content_hash=? AND app_id=? AND window_title=? LIMIT 1",
        (content_hash, app_id, title),
    ).fetchone():
        return None

    cid = str(uuid.uuid4())
    ts = captured_at or now_ms()
    db.execute(
        "INSERT INTO memory_chunks (id, app_id, window_title, url_host, captured_at, source,"
        " text, content_hash, char_count) VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, app_id, title, url_host, ts, source, text, content_hash, len(text)),
    )
    passages = split_passages(text)
    hashes = [hash64(p) for p in passages]
    for i, (passage, h) in enumerate(zip(passages, hashes)):
        nxt = hashes[i + 1] if i + 1 < len(hashes) else None
        db.execute(
            "INSERT OR IGNORE INTO memory_passages (chunk_id, hash, next_hash, captured_at, text)"
            " VALUES (?,?,?,?,?)",
            (cid, h, nxt, ts, passage),
        )
    return cid


def embed_pending(db, batch: int = 256, verbose: bool = True) -> int:
    rows = db.execute(
        "SELECT p.id, c.window_title, p.text FROM memory_passages p"
        " JOIN memory_chunks c ON c.id = p.chunk_id WHERE p.embedding IS NULL"
    ).fetchall()
    if not rows:
        return 0
    done = 0
    try:
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            # Prefix the window title so "the PR about auth" can match on context.
            vecs = Embedder.embed([f"{t}\n{x}" if t else x for _, t, x in part])
            db.executemany(
                "UPDATE memory_passages SET embedding=? WHERE id=?",
                [(v.tobytes(), r[0]) for v, r in zip(vecs, part)],
            )
            db.commit()
            done += len(part)
            if verbose and len(rows) > batch:
                warn(f"  embedded {done}/{len(rows)} passages")
    except Exception as e:  # keyword search still works without vectors
        warn(f"[memory] embedding failed ({e}); keyword search still available")
    return done


# ------------------------------------------------------------------------- search

def build_fts_query(query: str) -> tuple[str, list[str]]:
    toks = re.findall(r"\w+", query.lower())
    kept = [t for t in toks if t not in STOPWORDS] or toks
    terms = list(dict.fromkeys(kept))
    return " OR ".join(f'"{t}"*' for t in terms), terms


def filter_sql(since_hours, app) -> tuple[str, list]:
    clauses, params = [], []
    if since_hours:
        clauses.append("c.captured_at >= ?")
        params.append(now_ms() - int(float(since_hours) * 3_600_000))
    if app:
        clauses.append("(c.app_id LIKE ? OR c.url_host LIKE ?)")
        params += [f"%{app}%", f"%{app}%"]
    return ("AND " + " AND ".join(clauses)) if clauses else "", params


def keyword_passages(db, fts, filt, params, depth):
    rows = db.execute(
        "SELECT p.chunk_id, p.id, bm25(memory_passages_fts) FROM memory_passages_fts"
        " JOIN memory_passages p ON p.id = memory_passages_fts.rowid"
        " JOIN memory_chunks c ON c.id = p.chunk_id"
        f" WHERE memory_passages_fts MATCH ? {filt}"
        " ORDER BY bm25(memory_passages_fts) LIMIT ?",
        [fts, *params, depth],
    ).fetchall()
    return [(cid, pid, -score) for cid, pid, score in rows]


def keyword_titles(db, fts, filt, params, depth):
    rows = db.execute(
        "SELECT c.id, bm25(memory_chunks_fts) FROM memory_chunks_fts"
        " JOIN memory_chunks c ON c.rowid = memory_chunks_fts.rowid"
        f" WHERE memory_chunks_fts MATCH ? {filt}"
        " ORDER BY bm25(memory_chunks_fts) LIMIT ?",
        [f"{{app_id window_title}} : ({fts})", *params, depth],
    ).fetchall()
    return [(cid, None, -score) for cid, score in rows]


def semantic_passages(db, query, filt, params, depth):
    import numpy as np
    rows = db.execute(
        "SELECT p.chunk_id, p.id, p.embedding FROM memory_passages p"
        f" JOIN memory_chunks c ON c.id = p.chunk_id WHERE p.embedding IS NOT NULL {filt}",
        params,
    ).fetchall()
    if not rows:
        return []
    qv = Embedder.embed([query])[0]
    mat = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    sims = mat @ qv
    order = np.argsort(-sims)[:depth]
    return [(rows[i][0], rows[i][1], float(sims[i])) for i in order if sims[i] >= MIN_COSINE]


def rrf(lists: dict):
    """Reciprocal Rank Fusion at chunk level; keeps each chunk's best-ranked passage."""
    scores, best, why = {}, {}, {}
    for name, items in lists.items():
        seen = set()
        for cid, pid, raw in items:
            if cid in seen:
                continue
            seen.add(cid)
            rank = len(seen)
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
            why.setdefault(cid, {})[name] = (rank, raw)
            if pid is not None and (cid not in best or rank < best[cid][0]):
                best[cid] = (rank, pid)
    ranked = sorted(scores, key=lambda c: -scores[c])
    return ranked, scores, {c: p for c, (_, p) in best.items()}, why


def snippet(text: str, terms: list[str], width: int = 320) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    low = flat.lower()
    hits = [low.find(t) for t in terms if low.find(t) >= 0]
    start = max(0, min(hits) - width // 3) if hits else 0
    out = flat[start:start + width]
    return ("…" if start else "") + out + ("…" if start + width < len(flat) else "")


def search(db, query, since_hours=None, app=None, limit=10, mode="hybrid", sort="relevance"):
    limit = min(max(int(limit), 1), MAX_LIMIT)
    depth = candidate_depth(limit)
    fts, terms = build_fts_query(query)
    filt, params = filter_sql(since_hours, app)
    lists, notes = {}, []

    if mode in ("hybrid", "keyword") and fts:
        lists["keyword"] = keyword_passages(db, fts, filt, params, depth)
        lists["title"] = keyword_titles(db, fts, filt, params, depth)
    if mode in ("hybrid", "semantic"):
        try:
            lists["semantic"] = semantic_passages(db, query, filt, params, depth)
        except Exception as e:
            if mode == "semantic":
                raise
            notes.append(f"[memory] DEGRADED: vector search failed ({e}); results are keyword-only")

    ranked, scores, best, why = rrf(lists)
    results, seen_keys = [], set()
    for cid in ranked:
        c = db.execute(
            "SELECT app_id, window_title, url_host, captured_at, source, text"
            " FROM memory_chunks WHERE id=?", (cid,)).fetchone()
        if not c:
            continue
        pid = best.get(cid)
        if pid is None:  # title-only hit: show the chunk's first passage
            row = db.execute("SELECT id FROM memory_passages WHERE chunk_id=? ORDER BY id LIMIT 1",
                             (cid,)).fetchone()
            pid = row[0] if row else None
        # Repeated captures of the same window collapse to one result.
        key = (c[0], c[1], pid) if pid is not None else (c[0], c[1])
        if key in seen_keys:
            continue
        seen_keys.add(key)

        passage, following = c[5], None
        if pid is not None:
            ptext, next_hash = db.execute(
                "SELECT text, next_hash FROM memory_passages WHERE id=?", (pid,)).fetchone()
            passage = ptext
            if next_hash is not None:  # widen the hit with the following passage
                nxt = db.execute("SELECT text FROM memory_passages WHERE hash=?",
                                 (next_hash,)).fetchone()
                following = nxt[0] if nxt else None
        results.append({
            "chunk_id": cid, "app_id": c[0], "window_title": c[1], "url_host": c[2],
            "captured_at": c[3], "source": c[4], "score": round(scores[cid], 5),
            "signals": {k: {"rank": r, "raw": round(v, 4)} for k, (r, v) in why[cid].items()},
            "passage": passage, "next_passage": following,
        })
        if len(results) >= limit:
            break

    if sort == "recent":
        results.sort(key=lambda r: -r["captured_at"])
    return results, terms, notes


def print_results(results, terms, explain=False):
    tty = sys.stdout.isatty()
    bold = (lambda s: f"\033[1m{s}\033[0m") if tty else (lambda s: s)
    dim = (lambda s: f"\033[2m{s}\033[0m") if tty else (lambda s: s)

    def hl(s):
        if not tty or not terms:
            return s
        pat = re.compile("|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)), re.I)
        return pat.sub(lambda m: f"\033[1;33m{m.group(0)}\033[0m", s)

    if not results:
        print("No matches.")
        return
    for i, r in enumerate(results, 1):
        where = r["app_id"] + (f" · {r['url_host']}" if r["url_host"] else "")
        meta = f"{where} · {ago(r['captured_at'])} · {r['source']} · {'+'.join(r['signals'])}"
        print(f"{bold(f'{i:>2}.')} {bold(r['window_title'] or '(untitled)')}")
        print(f"    {dim(meta)}")
        print(f"    {hl(snippet(r['passage'], terms))}")
        if explain:
            detail = ", ".join(f"{k} #{v['rank']} ({v['raw']})" for k, v in r["signals"].items())
            print(dim(f"    rrf={r['score']}  {detail}"))
        print()


# ------------------------------------------------------------------------ sources

def ensure_capture_bin() -> None:
    if CAPTURE_BIN.exists() and CAPTURE_BIN.stat().st_mtime >= CAPTURE_SRC.stat().st_mtime:
        return
    if not shutil.which("swiftc"):
        sys.exit("swiftc not found; install Xcode command line tools to use `capture`.")
    CAPTURE_BIN.parent.mkdir(parents=True, exist_ok=True)
    warn("compiling capture helper…")
    subprocess.run(["swiftc", "-O", str(CAPTURE_SRC), "-o", str(CAPTURE_BIN)], check=True)


def capture_once(db, blocked, ocr):
    proc = subprocess.run([str(CAPTURE_BIN)] + (["--ocr"] if ocr else []),
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        warn(f"capture failed: {proc.stderr.strip()}")
        return None
    snap = json.loads(proc.stdout)
    app = snap.get("app") or "unknown"
    title = snap.get("title") or ""
    if app.lower() in blocked:
        return f"skip  {app} (blocked app)"
    if re.search(r"incognito|private browsing", title, re.I):
        return f"skip  {app} (private window)"
    if snap.get("note"):
        warn(f"  note: {snap['note']}")
    url = snap.get("url")
    cid = add_chunk(db, snap.get("text", ""), app_id=app.lower(), title=title,
                    url_host=urlparse(url).hostname if url else None,
                    source=snap.get("source", "ax"))
    db.commit()
    n = len(snap.get("text", ""))
    return f"{'saved' if cid else 'same '} {app} — {title[:60]!r} ({n} chars via {snap.get('source')})"


CHROMIUM = {
    "chrome": "Google/Chrome", "brave": "BraveSoftware/Brave-Browser",
    "edge": "Microsoft Edge", "arc": "Arc/User Data", "chromium": "Chromium",
}


def import_chromium(db, days: int) -> int:
    support = Path.home() / "Library" / "Application Support"
    cutoff_us = int((time.time() - days * 86400 + 11644473600) * 1_000_000)
    added = 0
    for browser, rel in CHROMIUM.items():
        for hist in sorted((support / rel).glob("*/History")):
            if not (hist.parent.name == "Default" or hist.parent.name.startswith("Profile")):
                continue
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "History"
                shutil.copy2(hist, copy)  # the live file is locked while the browser runs
                try:
                    con = sqlite3.connect(copy)
                    rows = con.execute(
                        "SELECT u.url, u.title, MAX(v.visit_time) FROM urls u"
                        " JOIN visits v ON v.url = u.id WHERE v.visit_time > ?"
                        " GROUP BY u.id", (cutoff_us,)).fetchall()
                    con.close()
                except sqlite3.DatabaseError as e:
                    warn(f"  {browser}/{hist.parent.name}: {e}")
                    continue
            n = 0
            for url, title, vt in rows:
                if not url.startswith(("http://", "https://")):
                    continue
                ms = vt // 1000 - 11_644_473_600_000
                title = title or url
                if add_chunk(db, f"{title}\n{url}", app_id=browser, title=title,
                             url_host=urlparse(url).hostname, source="browser", captured_at=ms):
                    n += 1
            db.commit()
            warn(f"  {browser}/{hist.parent.name}: {n} new of {len(rows)} pages")
            added += n
    return added


def ingest_paths(db, paths: list[str]) -> int:
    added = 0
    for root in paths:
        root = Path(root).expanduser().resolve()
        files = [root] if root.is_file() else [
            p for p in root.rglob("*")
            if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(root).parts)
        ]
        for f in files:
            if f.suffix.lower() not in TEXT_EXT or f.stat().st_size > 1_000_000:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            # A changed file replaces its previous version.
            db.execute("DELETE FROM memory_chunks WHERE app_id='files' AND window_title=?"
                       " AND content_hash != ?",
                       (str(f), hashlib.sha256(text.strip().encode()).hexdigest()))
            if add_chunk(db, text, app_id="files", title=str(f), source="file",
                         captured_at=int(f.stat().st_mtime * 1000)):
                added += 1
        db.commit()
    return added


# ------------------------------------------------------------------- web viewer
# Helpers shared with the Glint engine API.

def to_hit(r: dict) -> dict:
    """Shape a search result for the web UI."""
    return {
        "chunkId": r["chunk_id"], "appId": r["app_id"], "windowTitle": r["window_title"],
        "urlHost": r["url_host"], "capturedAt": r["captured_at"], "source": r["source"],
        "textPreview": re.sub(r"\s+", " ", r["passage"]).strip()[:900],
        "charCount": r.get("char_count", len(r["passage"])), "score": r["score"],
        "titleOnly": set(r["signals"]) == {"title"}, "signals": r["signals"],
    }


def recent_hits(db, limit, since_hours=None, app=None):
    """Empty query: newest captures first (what the memory manager shows on open)."""
    filt, params = filter_sql(since_hours, app)
    rows = db.execute(
        "SELECT c.id, c.app_id, c.window_title, c.url_host, c.captured_at, c.source, c.text,"
        f" c.char_count FROM memory_chunks c WHERE 1=1 {filt}"
        " ORDER BY c.captured_at DESC LIMIT ?", [*params, limit]).fetchall()
    return [{"chunkId": r[0], "appId": r[1], "windowTitle": r[2], "urlHost": r[3],
             "capturedAt": r[4], "source": r[5],
             "textPreview": re.sub(r"\s+", " ", r[6]).strip()[:900], "charCount": r[7],
             "score": 0, "titleOnly": False, "signals": {}} for r in rows]


def get_meta(db, key, default=None):
    db.execute("CREATE TABLE IF NOT EXISTS lite_meta (key TEXT PRIMARY KEY, value TEXT)")
    row = db.execute("SELECT value FROM lite_meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_meta(db, key, value):
    db.execute("CREATE TABLE IF NOT EXISTS lite_meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT OR REPLACE INTO lite_meta VALUES (?, ?)", (key, json.dumps(value)))
    db.commit()


def apply_retention(db) -> int:
    days = get_meta(db, "retention_days")
    if not days:
        return 0
    n = db.execute("DELETE FROM memory_chunks WHERE captured_at < ?",
                   (now_ms() - int(days) * 86_400_000,)).rowcount
    db.commit()
    return n


def memory_stats(db) -> dict:
    q = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "chunk_count": q("SELECT count(*) FROM memory_chunks"),
        "bytes_approx": q("SELECT coalesce(sum(char_count), 0) FROM memory_chunks"),
        "oldest_at": q("SELECT min(captured_at) FROM memory_chunks"),
        "newest_at": q("SELECT max(captured_at) FROM memory_chunks"),
        "last_indexed_at": q("SELECT max(captured_at) FROM memory_chunks"),
        "passages": q("SELECT count(*) FROM memory_passages"),
        "embedded": q("SELECT count(*) FROM memory_passages WHERE embedding IS NOT NULL"),
        "embedder_ready": Embedder._model is not None,
        "vector_backend": "sqlite (brute-force cosine)",
        "db_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
    }


# --------------------------------------------------------------------------- CLI

def cmd_search(db, a):
    results, terms, notes = search(db, " ".join(a.query), a.since_hours, a.app, a.limit,
                                   a.mode, a.sort)
    for n in notes:
        warn(n)
    if a.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print_results(results, terms, a.explain)


def cmd_add(db, a):
    text = sys.stdin.read() if a.text == ["-"] else " ".join(a.text)
    cid = add_chunk(db, text, app_id=a.app, title=a.title, url_host=a.url, source="manual")
    db.commit()
    print(f"added {cid}" if cid else "unchanged (already stored)")
    if cid and not a.no_embed:
        embed_pending(db)


def cmd_ingest(db, a):
    n = ingest_paths(db, a.paths)
    print(f"ingested {n} new/changed file(s)")
    if not a.no_embed:
        print(f"embedded {embed_pending(db)} passage(s)")


def cmd_import_browser(db, a):
    n = import_chromium(db, a.days)
    print(f"imported {n} page(s) from the last {a.days} days")
    if not a.no_embed:
        print(f"embedded {embed_pending(db)} passage(s)")


def cmd_capture(db, a):
    ensure_capture_bin()
    blocked = DEFAULT_BLOCKED_APPS | {b.lower() for b in a.block}
    i = 0
    try:
        while True:
            line = capture_once(db, blocked, a.ocr)
            if line:
                print(f"[{time.strftime('%H:%M:%S')}] {line}", flush=True)
            if not a.no_embed:
                embed_pending(db, verbose=False)
            i += 1
            if a.every <= 0 or (a.count and i >= a.count):
                break
            time.sleep(a.every)
    except KeyboardInterrupt:
        pass


def cmd_reindex(db, a):
    print(f"embedded {embed_pending(db)} passage(s)")


def cmd_stats(db, a):
    def q(sql):
        return db.execute(sql).fetchone()[0]
    print(f"db:        {DB_PATH} ({DB_PATH.stat().st_size / 1e6:.1f} MB)")
    print(f"chunks:    {q('SELECT count(*) FROM memory_chunks')}")
    print(f"passages:  {q('SELECT count(*) FROM memory_passages')}"
          f" ({q('SELECT count(*) FROM memory_passages WHERE embedding IS NOT NULL')} embedded)")
    lo, hi = db.execute("SELECT MIN(captured_at), MAX(captured_at) FROM memory_chunks").fetchone()
    if lo:
        print(f"range:     {ago(lo)} → {ago(hi)}")
    for app, n in db.execute("SELECT app_id, count(*) FROM memory_chunks GROUP BY 1"
                             " ORDER BY 2 DESC LIMIT 10"):
        print(f"  {n:>6}  {app}")


def cmd_forget(db, a):
    if not (a.all or a.app or a.older_than_days):
        sys.exit("say what to forget: --all, --app NAME, or --older-than-days N")
    where, params = [], []
    if a.app:
        where.append("app_id LIKE ?")
        params.append(f"%{a.app}%")
    if a.older_than_days:
        where.append("captured_at < ?")
        params.append(now_ms() - a.older_than_days * 86_400_000)
    sql = "DELETE FROM memory_chunks" + (" WHERE " + " AND ".join(where) if where else "")
    n = db.execute(sql, params).rowcount
    db.commit()
    db.execute("VACUUM")
    print(f"forgot {n} chunk(s)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="glint-memory", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="hybrid search over everything captured")
    s.add_argument("query", nargs="+")
    s.add_argument("--since-hours", type=float, help="only captures from the last N hours")
    s.add_argument("--app", help="filter by app name or URL host (substring)")
    s.add_argument("-n", "--limit", type=int, default=10)
    s.add_argument("--mode", choices=["hybrid", "keyword", "semantic"], default="hybrid")
    s.add_argument("--sort", choices=["relevance", "recent"], default="relevance")
    s.add_argument("--explain", action="store_true", help="show per-signal ranks and RRF score")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("capture", help="snapshot the frontmost window's text (AX, OCR fallback)")
    s.add_argument("--every", type=float, default=0, help="repeat every N seconds")
    s.add_argument("--count", type=int, default=0, help="stop after N captures (0 = forever)")
    s.add_argument("--ocr", action="store_true", help="OCR the window when AX text is thin")
    s.add_argument("--block", action="append", default=[], help="app name to never capture")
    s.add_argument("--no-embed", action="store_true")
    s.set_defaults(fn=cmd_capture)

    s = sub.add_parser("add", help="add text by hand (use '-' to read stdin)")
    s.add_argument("text", nargs="+")
    s.add_argument("--app", default="manual")
    s.add_argument("--title", default="")
    s.add_argument("--url")
    s.add_argument("--no-embed", action="store_true")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("ingest", help="index text files / folders")
    s.add_argument("paths", nargs="+")
    s.add_argument("--no-embed", action="store_true")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("import-browser", help="import Chrome/Brave/Edge/Arc history")
    s.add_argument("--days", type=int, default=14)
    s.add_argument("--no-embed", action="store_true")
    s.set_defaults(fn=cmd_import_browser)

    sub.add_parser("reindex", help="embed any passages missing vectors").set_defaults(fn=cmd_reindex)
    sub.add_parser("stats", help="show what's stored").set_defaults(fn=cmd_stats)

    s = sub.add_parser("forget", help="delete captured memory")
    s.add_argument("--all", action="store_true")
    s.add_argument("--app")
    s.add_argument("--older-than-days", type=int)
    s.set_defaults(fn=cmd_forget)

    a = p.parse_args(argv)
    db = open_db()
    try:
        a.fn(db, a)
    finally:
        db.close()


if __name__ == "__main__":
    main()
