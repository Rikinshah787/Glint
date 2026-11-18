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
             Extra lists/adjustments (see RETRIEVAL): session-level documents
             (title/summary/project/insights) as their own keyword + semantic
             lists, alias expansion of the keyword query (memory_synonyms), a
             soft boost for explicit time phrases ("last week"), a light recency
             tie-break, and near-duplicate suppression of repeated captures.
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
SESSION_MIN_COSINE = 0.30   # session docs are short summaries; weak matches are mostly noise

# Retrieval features (all on by default; the retrieval eval turns them off for the baseline).
RETRIEVAL = {
    "sessions": True,     # session-level docs (summary text) fused into RRF as their own lists
    "expand": True,       # expand keyword query with aliases of entities named in the query
    "time_hints": True,   # "yesterday", "last week"… → soft boost for items inside that window
    "recency": True,      # light recency tie-break
    "dedup": True,        # suppress near-identical passages from repeated captures of one window
}
RECENCY_WEIGHT = 0.05        # at most +5% of an item's fused score…
RECENCY_HALF_LIFE_D = 7.0    # …halving every week of age
TIME_HINT_BOOST = 0.30       # +30% for items inside an explicitly named time window
MAX_EXPANSIONS = 12
DUP_OVERLAP_SAME_TITLE = 0.80   # word-set overlap (|A∩B| / min) at which two captures of one window match
DUP_JACCARD_ANY_TITLE = 0.90    # …or (Jaccard) of the same app under a different window title


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

-- Session-level documents (title + summary + project + insights), written by the engine/graph
-- feature once a work session has been summarised; searched as their own ranked lists.
CREATE TABLE IF NOT EXISTS memory_session_docs (
    session_id  TEXT PRIMARY KEY,
    started_at  INTEGER NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    apps        TEXT NOT NULL DEFAULT '',
    embedding   BLOB,                           -- float32[384], L2-normalised
    updated_at  INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_session_docs_fts USING fts5(
    title, text, content='memory_session_docs', content_rowid='rowid', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS memory_session_docs_ai AFTER INSERT ON memory_session_docs BEGIN
    INSERT INTO memory_session_docs_fts(rowid, title, text) VALUES (new.rowid, new.title, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memory_session_docs_ad AFTER DELETE ON memory_session_docs BEGIN
    INSERT INTO memory_session_docs_fts(memory_session_docs_fts, rowid, title, text)
    VALUES ('delete', old.rowid, old.title, old.text);
END;

-- Query expansion groups (e.g. an entity's name + aliases from the knowledge graph).
CREATE TABLE IF NOT EXISTS memory_synonyms (
    grp  TEXT NOT NULL,
    term TEXT NOT NULL,                         -- normalised: lower-case words joined by one space
    PRIMARY KEY (grp, term)
);
CREATE INDEX IF NOT EXISTS idx_synonyms_term ON memory_synonyms(term);
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
    done = embed_session_docs(db)
    rows = db.execute(
        "SELECT p.id, c.window_title, p.text FROM memory_passages p"
        " JOIN memory_chunks c ON c.id = p.chunk_id WHERE p.embedding IS NULL"
    ).fetchall()
    if not rows:
        return done
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


# ------------------------------------------------------------------ session docs
# High-level text per work session (title, project, summary, insights, entity names). The engine's
# graph feature writes these after a session is summarised; search() fuses them as their own lists.

def norm_term(text: str) -> str:
    """Normalised form used for synonym lookups: lower-case words joined by one space."""
    return " ".join(re.findall(r"\w+", (text or "").lower()))


def compose_session_doc(title: str, project: str = "", summary: str = "", insights=(),
                        mentions=()) -> str:
    """Session-level text: title, project, summary, insight headlines and the named things it
    mentions with their aliases — e.g. mentions=[("Project Ledger", ["billing migration"])]."""
    names = []
    for name, aliases in mentions or []:
        alias = [a for a in aliases or [] if a and a.lower() != name.lower()][:4]
        names.append(f"{name} ({', '.join(alias)})" if alias else name)
    return "\n".join(filter(None, [
        (title or "").strip(), f"Project: {project.strip()}" if (project or "").strip() else "",
        (summary or "").strip(),
        ("Insights: " + "; ".join(i for i in insights if i)) if any(insights or []) else "",
        ("Mentions: " + ", ".join(names[:16])) if names else ""]))


def upsert_session_doc(db, session_id: str, text: str, started_at: int, title: str = "",
                       apps: str = "", embed: bool = True) -> None:
    """Index (or re-index) one session's high-level text. Empty text removes it. Caller commits."""
    text = (text or "").strip()
    db.execute("DELETE FROM memory_session_docs WHERE session_id=?", (session_id,))
    if not text:
        return
    vec = None
    if embed:
        try:
            vec = Embedder.embed([text])[0].tobytes()
        except Exception as e:  # embed_pending() retries later
            warn(f"[memory] session doc embedding deferred ({e.__class__.__name__})")
    db.execute("INSERT INTO memory_session_docs (session_id, started_at, title, text, apps, embedding,"
               " updated_at) VALUES (?,?,?,?,?,?,?)",
               (session_id, int(started_at or 0), title or "", text, apps or "", vec, now_ms()))


def delete_session_doc(db, session_id: str) -> None:
    db.execute("DELETE FROM memory_session_docs WHERE session_id=?", (session_id,))


def session_doc_ids(db) -> set[str]:
    return {r[0] for r in db.execute("SELECT session_id FROM memory_session_docs")}


def embed_session_docs(db, batch: int = 128) -> int:
    rows = db.execute("SELECT rowid, text FROM memory_session_docs WHERE embedding IS NULL").fetchall()
    done = 0
    try:
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            vecs = Embedder.embed([t for _, t in part])
            db.executemany("UPDATE memory_session_docs SET embedding=? WHERE rowid=?",
                           [(v.tobytes(), r[0]) for v, r in zip(vecs, part)])
            db.commit()
            done += len(part)
    except Exception as e:
        warn(f"[memory] session doc embedding failed ({e}); keyword search still available")
    return done


def set_synonyms(db, group: str, terms) -> None:
    """Replace one expansion group (e.g. an entity's name + aliases). Caller commits."""
    db.execute("DELETE FROM memory_synonyms WHERE grp=?", (group,))
    clean = {norm_term(t) for t in terms or []}
    clean = sorted(t for t in clean if len(t) >= 2 and t not in STOPWORDS)
    if len(clean) >= 2:  # a lone term expands to nothing
        db.executemany("INSERT OR IGNORE INTO memory_synonyms (grp, term) VALUES (?,?)",
                       [(group, t) for t in clean])


def clear_synonyms(db, group: str | None = None) -> None:
    if group is None:
        db.execute("DELETE FROM memory_synonyms")
    else:
        db.execute("DELETE FROM memory_synonyms WHERE grp=?", (group,))


# ------------------------------------------------------------------------- search

def build_fts_query(query: str) -> tuple[str, list[str]]:
    toks = re.findall(r"\w+", query.lower())
    kept = [t for t in toks if t not in STOPWORDS] or toks
    terms = list(dict.fromkeys(kept))
    return " OR ".join(f'"{t}"*' for t in terms), terms


def expansion_terms(db, query: str) -> list[str]:
    """Aliases of every synonym group (entity) whose name or alias appears in the query."""
    words = re.findall(r"\w+", query.lower())
    grams = {" ".join(words[i:i + n]) for n in range(1, 5) for i in range(len(words) - n + 1)}
    grams = [g for g in grams if g not in STOPWORDS and len(g) >= 2]
    if not grams:
        return []
    groups = [r[0] for r in db.execute(
        f"SELECT DISTINCT grp FROM memory_synonyms WHERE term IN ({','.join('?' * len(grams))})", grams)]
    if not groups:
        return []
    have, out = set(words), []
    for (term,) in db.execute(
            f"SELECT term FROM memory_synonyms WHERE grp IN ({','.join('?' * len(groups))})", groups):
        if term not in grams and not all(w in have for w in term.split()):
            out.append(term)
    return list(dict.fromkeys(out))[:MAX_EXPANSIONS]


_DAY_MS = 86_400_000
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def time_hint(query: str, now: int | None = None):
    """An explicit time phrase in the query -> (start_ms, end_ms | None, query without it), else None."""
    from datetime import datetime, timedelta
    day0 = datetime.fromtimestamp((now or now_ms()) / 1000).replace(hour=0, minute=0, second=0, microsecond=0)
    ms = lambda d: int(d.timestamp() * 1000)  # noqa: E731
    week0 = day0 - timedelta(days=day0.weekday())
    month0 = day0.replace(day=1)
    prev_month0 = (month0 - timedelta(days=1)).replace(day=1)

    def weekday(m):
        back = (day0.weekday() - _WEEKDAYS.index(m.group(1).lower())) % 7 or 7
        start = day0 - timedelta(days=back)
        return start, start + timedelta(days=1)

    rules = [
        (r"\b(earlier )?today('s)?\b|\bthis (morning|afternoon|evening)\b", lambda m: (day0, None)),
        (r"\byesterday('s)?\b", lambda m: (day0 - timedelta(days=1), day0)),
        (r"\bthis week('s)?\b", lambda m: (week0, None)),
        (r"\b(last|previous) week('s)?\b", lambda m: (week0 - timedelta(days=7), week0)),
        (r"\bthis month('s)?\b", lambda m: (month0, None)),
        (r"\b(last|previous) month('s)?\b", lambda m: (prev_month0, month0)),
        (r"\b(past|last) (\d{1,3}) days\b", lambda m: (day0 - timedelta(days=int(m.group(2))), None)),
        (r"\b(\d{1,3}) days ago\b",
         lambda m: (day0 - timedelta(days=int(m.group(1))), day0 - timedelta(days=int(m.group(1)) - 1))),
        (r"\blast (" + "|".join(_WEEKDAYS) + r")\b", weekday),
    ]
    for pat, fn in rules:
        m = re.search(pat, query, re.I)
        if not m:
            continue
        lo, hi = fn(m)
        rest = re.sub(r"\s+", " ", query[:m.start()] + " " + query[m.end():]).strip()
        return ms(lo), (ms(hi) if hi else None), rest
    return None


def filter_sql(since_hours, app) -> tuple[str, list]:
    clauses, params = [], []
    if since_hours:
        clauses.append("c.captured_at >= ?")
        params.append(now_ms() - int(float(since_hours) * 3_600_000))
    if app:
        clauses.append("(c.app_id LIKE ? OR c.url_host LIKE ?)")
        params += [f"%{app}%", f"%{app}%"]
    return ("AND " + " AND ".join(clauses)) if clauses else "", params


def session_filter_sql(since_hours, app) -> tuple[str, list]:
    clauses, params = [], []
    if since_hours:
        clauses.append("d.started_at >= ?")
        params.append(now_ms() - int(float(since_hours) * 3_600_000))
    if app:
        clauses.append("d.apps LIKE ?")
        params.append(f"%{app}%")
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


def semantic_passages(db, query, filt, params, depth, qv_fn=None):
    import numpy as np
    rows = db.execute(
        "SELECT p.chunk_id, p.id, p.embedding FROM memory_passages p"
        f" JOIN memory_chunks c ON c.id = p.chunk_id WHERE p.embedding IS NOT NULL {filt}",
        params,
    ).fetchall()
    if not rows:
        return []
    qv = qv_fn() if qv_fn else Embedder.embed([query])[0]
    mat = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    sims = mat @ qv
    order = np.argsort(-sims)[:depth]
    return [(rows[i][0], rows[i][1], float(sims[i])) for i in order if sims[i] >= MIN_COSINE]


SESSION_PREFIX = "s:"   # session-level candidates share the RRF id space with chunk ids


def keyword_sessions(db, fts, filt, params, depth):
    rows = db.execute(
        "SELECT d.session_id, bm25(memory_session_docs_fts, 2.0, 1.0) FROM memory_session_docs_fts"
        " JOIN memory_session_docs d ON d.rowid = memory_session_docs_fts.rowid"
        f" WHERE memory_session_docs_fts MATCH ? {filt}"
        " ORDER BY bm25(memory_session_docs_fts, 2.0, 1.0) LIMIT ?",
        [fts, *params, depth],
    ).fetchall()
    return [(SESSION_PREFIX + sid, None, -score) for sid, score in rows]


def semantic_sessions(db, qv_fn, filt, params, depth):
    import numpy as np
    rows = db.execute(
        f"SELECT d.session_id, d.embedding FROM memory_session_docs d WHERE d.embedding IS NOT NULL {filt}",
        params).fetchall()
    if not rows:
        return []
    mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    sims = mat @ qv_fn()
    order = np.argsort(-sims)[:depth]
    return [(SESSION_PREFIX + rows[i][0], None, float(sims[i])) for i in order if sims[i] >= SESSION_MIN_COSINE]


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


def _has_column(db, table: str, col: str) -> bool:
    return any(r[1] == col for r in db.execute(f"PRAGMA table_info({table})"))


def _has_table(db, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _rows_in(db, sql: str, ids: list, chunk: int = 500):
    out = []
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        out += db.execute(sql.format(marks=",".join("?" * len(part))), part).fetchall()
    return out


def _wordset(text: str) -> frozenset:
    return frozenset(re.findall(r"\w+", (text or "").lower()))


def _near_duplicate(kept: list, app_id: str, title: str, words: frozenset) -> bool:
    if not words:
        return False
    for k_app, k_title, k_words in kept:
        if k_app != app_id or not k_words:
            continue
        shared = len(words & k_words)
        if k_title == title and shared / min(len(words), len(k_words)) >= DUP_OVERLAP_SAME_TITLE:
            return True
        if shared / len(words | k_words) >= DUP_JACCARD_ANY_TITLE:
            return True
    return False


def search(db, query, since_hours=None, app=None, limit=10, mode="hybrid", sort="relevance"):
    """Hybrid search. Returns (results, terms, notes); each result is a capture passage or — with
    kind == "session" — a summarised work session (app_id "session", passage = its summary text)."""
    limit = min(max(int(limit), 1), MAX_LIMIT)
    depth = candidate_depth(limit)
    hint = time_hint(query) if RETRIEVAL["time_hints"] else None
    text_q = hint[2] if hint and re.search(r"\w", hint[2]) else query
    fts, terms = build_fts_query(text_q)
    if RETRIEVAL["expand"] and fts:
        extra = expansion_terms(db, text_q)
        if extra:
            fts += " OR " + " OR ".join(f'"{t}"' if " " in t else f'"{t}"*' for t in extra)
            terms += [w for w in dict.fromkeys(w for t in extra for w in t.split()) if w not in terms]
    filt, params = filter_sql(since_hours, app)
    use_sessions = RETRIEVAL["sessions"]
    sfilt, sparams = session_filter_sql(since_hours, app)
    lists, notes = {}, []

    qv_cache: list = []

    def qv_fn():
        if not qv_cache:
            qv_cache.append(Embedder.embed([text_q])[0])
        return qv_cache[0]

    if mode in ("hybrid", "keyword") and fts:
        lists["keyword"] = keyword_passages(db, fts, filt, params, depth)
        lists["title"] = keyword_titles(db, fts, filt, params, depth)
        if use_sessions:
            lists["session"] = keyword_sessions(db, fts, sfilt, sparams, depth)
    if mode in ("hybrid", "semantic"):
        try:
            lists["semantic"] = semantic_passages(db, text_q, filt, params, depth, qv_fn)
            if use_sessions:
                lists["session_semantic"] = semantic_sessions(db, qv_fn, sfilt, sparams, depth)
        except Exception as e:
            if mode == "semantic":
                raise
            notes.append(f"[memory] DEGRADED: vector search failed ({e}); results are keyword-only")

    ranked, scores, best, why = rrf(lists)
    session_ids = [c[len(SESSION_PREFIX):] for c in ranked if c.startswith(SESSION_PREFIX)]
    chunk_ids = [c for c in ranked if not c.startswith(SESSION_PREFIX)]
    has_sid = _has_column(db, "memory_chunks", "session_id")
    meta = {r[0]: (r[1], r[2]) for r in _rows_in(
        db, "SELECT id, captured_at, " + ("session_id" if has_sid else "NULL") +
        " FROM memory_chunks WHERE id IN ({marks})", chunk_ids)}
    smeta: dict = {}
    if session_ids:
        smeta = {r[0]: r[1] for r in _rows_in(
            db, "SELECT session_id, started_at FROM memory_session_docs WHERE session_id IN ({marks})",
            session_ids)}
        if _has_table(db, "sessions"):  # drop docs whose session was deleted
            alive = {r[0] for r in _rows_in(db, "SELECT id FROM sessions WHERE id IN ({marks})", session_ids)}
            for sid in session_ids:
                if sid not in alive:
                    scores.pop(SESSION_PREFIX + sid, None)
                    smeta.pop(sid, None)
                    delete_session_doc(db, sid)

    adjusted = False
    if hint or RETRIEVAL["recency"]:
        now = now_ms()
        for cid in list(scores):
            ts = smeta.get(cid[len(SESSION_PREFIX):]) if cid.startswith(SESSION_PREFIX) \
                else meta.get(cid, (None,))[0]
            if ts is None:
                continue
            mult = 1.0
            if hint and ts >= hint[0] and (hint[1] is None or ts < hint[1]):
                mult += TIME_HINT_BOOST
            if RETRIEVAL["recency"]:
                mult += RECENCY_WEIGHT * 0.5 ** (max(0, now - ts) / _DAY_MS / RECENCY_HALF_LIFE_D)
            scores[cid] *= mult
        adjusted = True
    if adjusted or len(ranked) != len(scores):
        ranked = sorted(scores, key=lambda c: -scores[c])

    dedup = RETRIEVAL["dedup"]
    max_sessions = max(2, limit // 4)
    results, seen_keys, kept_words, n_sessions = [], set(), [], 0
    for cid in ranked:
        if cid.startswith(SESSION_PREFIX):
            sid = cid[len(SESSION_PREFIX):]
            if n_sessions >= max_sessions:
                continue
            row = db.execute("SELECT title, text, started_at FROM memory_session_docs WHERE session_id=?",
                             (sid,)).fetchone()
            if not row:
                continue
            rep = db.execute("SELECT id FROM memory_chunks WHERE session_id=? ORDER BY char_count DESC LIMIT 1",
                             (sid,)).fetchone() if has_sid else None
            n_sessions += 1
            results.append({
                "chunk_id": rep[0] if rep else None, "app_id": "session", "window_title": row[0] or "Session",
                "url_host": None, "captured_at": row[2], "source": "session", "score": round(scores[cid], 5),
                "signals": {k: {"rank": r, "raw": round(v, 4)} for k, (r, v) in why[cid].items()},
                "passage": row[1], "next_passage": None, "session_id": sid, "kind": "session",
            })
            if len(results) >= limit:
                break
            continue
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
        if dedup:  # …and so do near-identical passages from consecutive captures
            words = _wordset(passage)
            if _near_duplicate(kept_words, c[0], c[1], words):
                continue
            kept_words.append((c[0], c[1], words))
        results.append({
            "chunk_id": cid, "app_id": c[0], "window_title": c[1], "url_host": c[2],
            "captured_at": c[3], "source": c[4], "score": round(scores[cid], 5),
            "signals": {k: {"rank": r, "raw": round(v, 4)} for k, (r, v) in why[cid].items()},
            "passage": passage, "next_passage": following,
            "session_id": meta.get(cid, (None, None))[1], "kind": "capture",
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
        "sessionId": r.get("session_id"), "kind": r.get("kind", "capture"),
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
    if a.all:
        db.execute("DELETE FROM memory_session_docs")
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
