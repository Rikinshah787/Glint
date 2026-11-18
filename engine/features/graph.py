"""Knowledge graph: the people, projects, organisations, documents, topics, tools, places and sites
that show up across work sessions, and how they co-occur.

Extraction costs no extra LLM call for new sessions: the session summary gains an `entities` field
(SESSION_SCHEMA_EXTRA) and SESSION_SUMMARY_HOOKS stores it. Cheap non-LLM signals add more nodes:
the session's project, the apps it used (tools) and the web hosts it visited (sites). Older sessions
can be backfilled with one small LLM call each (cancellable, rate-limited). Everything else — the
re-index, merges, renames, deletes — is local SQL.

Tables (in the engine's SQLite db):
  graph_entities         canonical name, type, aliases, first/last seen, mentions (= sessions)
  graph_names            normalised name/alias -> entity (case-insensitive canonicalisation)
  graph_entity_sessions  entity x session with salience 0-100 and where it came from
  graph_edges            co-occurrence per entity pair; weight decays with age (log-space, see EDGE_TAU_D)
  graph_session_state    raw LLM entities per session (so a re-index can rebuild everything)
  graph_blocked          names the user deleted; never re-created

It also feeds memory search: each summarised session becomes a session-level document
(mem.upsert_session_doc) and every entity with aliases becomes a query-expansion group
(mem.set_synonyms). Captured text is never logged.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime

import ai
import engine as E

mem = E.mem

TYPES = ("person", "project", "org", "document", "topic", "tool", "place")
GRAPH_TYPES = TYPES + ("site",)
TYPE_SYNONYMS = {"people": "person", "contact": "person", "organization": "org", "organisation": "org",
                 "company": "org", "team": "org", "client": "org", "file": "document", "doc": "document",
                 "repo": "project", "product": "project", "app": "tool", "application": "tool",
                 "software": "tool", "service": "tool", "website": "site", "location": "place",
                 "city": "place", "country": "place", "concept": "topic", "subject": "topic"}
SOURCE_RANK = {"llm": 0, "project": 1, "app": 2, "site": 3}   # which source "owns" a link
JUNK_NAMES = {"user", "the user", "me", "i", "you", "we", "session", "work", "meeting", "email", "mail",
              "project", "document", "file", "app", "application", "website", "browser", "computer", "mac",
              "screen", "window", "unknown", "none", "n a", "other", "misc"}
SKIP_HOSTS = {"localhost", "127.0.0.1", "accounts.google.com", "login.microsoftonline.com", "newtab"}
SKIP_APPS = {"glint", "loginwindow", "screensaverengine", "finder", "dock", "systemuiserver",
             "system settings", "system preferences", "notification center", "control center", "unknown"}
MAX_PER_SESSION = 8
EPOCH_MS = 1_735_689_600_000          # 2025-01-01; edge weights are stored relative to it
EDGE_TAU_D = 30.0                     # co-occurrence evidence decays with e-folding time of 30 days
DAY_MS = 86_400_000

E.DEFAULT_SETTINGS.update({"graph_backfill_delay_s": 2.0})

ENTITY_SCHEMA = {
    "type": "array",
    "description": "0-8 key named things in this session: people, projects, orgs, documents/files, topics, "
                   "tools, places",
    "items": {"type": "object", "properties": {
        "name": {"type": "string"},
        "type": {"type": "string", "enum": list(TYPES)},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "salience": {"type": "integer", "description": "0-100"}},
        "required": ["name", "type", "aliases", "salience"], "additionalProperties": False},
}

BACKFILL_SYSTEM = (
    "You list the key named things in one work session, from its report and an excerpt of the user's "
    "screen captures. Return 0-8 entities that matter to the work: people, projects, organisations, "
    "documents/files, topics, tools and places. Use the fullest proper name as `name`, other spellings, "
    "handles or abbreviations actually seen as `aliases`, and `salience` 0-100 for how central it was. "
    "Never invent names that do not appear."
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS graph_entities (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]',
    first_seen INTEGER, last_seen INTEGER, mentions INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS graph_names (key TEXT PRIMARY KEY, entity_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_graph_names_entity ON graph_names(entity_id);
CREATE TABLE IF NOT EXISTS graph_entity_sessions (
    entity_id INTEGER NOT NULL, session_id TEXT NOT NULL, salience INTEGER NOT NULL,
    source TEXT NOT NULL, at INTEGER NOT NULL, PRIMARY KEY (entity_id, session_id));
CREATE INDEX IF NOT EXISTS idx_ges_session ON graph_entity_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_ges_at ON graph_entity_sessions(at);
CREATE TABLE IF NOT EXISTS graph_edges (
    a INTEGER NOT NULL, b INTEGER NOT NULL, lw REAL NOT NULL, count INTEGER NOT NULL,
    last_seen INTEGER NOT NULL, PRIMARY KEY (a, b));
CREATE INDEX IF NOT EXISTS idx_graph_edges_b ON graph_edges(b);
CREATE TABLE IF NOT EXISTS graph_session_state (
    session_id TEXT PRIMARY KEY, raw TEXT, source TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS graph_blocked (key TEXT PRIMARY KEY);
"""


# ------------------------------------------------------------------ helpers

class _Db:
    """`with _Db() as d:` — engine connection under the global DB lock, committed on success."""

    def __enter__(self):
        E.DB_LOCK.acquire()
        try:
            self.d = E.db()
            self.d.executescript(SCHEMA_SQL)
        except Exception:
            E.DB_LOCK.release()
            raise
        return self.d

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.d.commit()
            self.d.close()
        finally:
            E.DB_LOCK.release()
        return False


def clean_name(s) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    s = s.strip("\"'“”‘’`*_:;,·•|").strip()
    return s[:80]


def key_of(s) -> str:
    k = mem.norm_term(s)
    k = re.sub(r"^(the|a|an) ", "", k)
    return k[:120]


def norm_type(t) -> str:
    t = str(t or "").strip().lower()
    return t if t in GRAPH_TYPES else TYPE_SYNONYMS.get(t, "topic")


def fmt_day(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%a %d %b %Y") if ms else "-"


def fmt_dt(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%a %d %b %H:%M") if ms else "-"


def edge_weight(lw: float, now: int | None = None) -> float:
    """Decayed co-occurrence weight of an edge right now."""
    return math.exp(lw + (EPOCH_MS - (now or E.now_ms())) / (EDGE_TAU_D * DAY_MS))


def _lw_term(w: float, at: int) -> float:
    return math.log(max(w, 1e-6)) + (at - EPOCH_MS) / (EDGE_TAU_D * DAY_MS)


def _logaddexp(a: float | None, b: float) -> float:
    if a is None:
        return b
    hi, lo = max(a, b), min(a, b)
    return hi + math.log1p(math.exp(lo - hi))


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _entity_row(d, eid: int) -> dict | None:
    r = d.execute("SELECT id, name, type, aliases, first_seen, last_seen, mentions FROM graph_entities "
                  "WHERE id=?", (eid,)).fetchone()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "type": r[2], "aliases": json.loads(r[3] or "[]"),
            "first_seen": r[4], "last_seen": r[5], "mentions": r[6]}


def _blocked(d, key: str) -> bool:
    return d.execute("SELECT 1 FROM graph_blocked WHERE key=?", (key,)).fetchone() is not None


# ------------------------------------------------------------- resolution

def _add_alias(d, eid: int, alias: str) -> bool:
    """Attach a display alias to an entity (and claim its key if free). Returns True if added."""
    alias = clean_name(alias)
    k = key_of(alias)
    if not k or len(k) < 2 or k in mem.STOPWORDS or _blocked(d, k):
        return False
    owner = d.execute("SELECT entity_id FROM graph_names WHERE key=?", (k,)).fetchone()
    if owner and owner[0] != eid:
        return False  # another entity already answers to this name
    if not owner:
        d.execute("INSERT INTO graph_names (key, entity_id) VALUES (?,?)", (k, eid))
    ent = _entity_row(d, eid)
    if not ent or key_of(ent["name"]) == k or any(key_of(a) == k for a in ent["aliases"]):
        return False
    d.execute("UPDATE graph_entities SET aliases=? WHERE id=?", (json.dumps((ent["aliases"] + [alias])[:24]), eid))
    return True


def resolve(d, name, etype, aliases=(), create: bool = True) -> int | None:
    """Find (or create) the entity for a mention, case-insensitively via its name or aliases."""
    name, etype = clean_name(name), norm_type(etype)
    k = key_of(name)
    if not k or len(k) < 2 or k in JUNK_NAMES or k.isdigit() or _blocked(d, k):
        return None
    alias_keys = [(a, key_of(a)) for a in aliases or [] if clean_name(a)]
    row = d.execute("SELECT entity_id FROM graph_names WHERE key=?", (k,)).fetchone()
    eid = row[0] if row else None
    if eid is None:  # a known alias of an entity of the same type also identifies it
        for _, ak in alias_keys:
            r = d.execute("SELECT n.entity_id FROM graph_names n JOIN graph_entities e ON e.id = n.entity_id "
                          "WHERE n.key=? AND e.type=?", (ak, etype)).fetchone()
            if r:
                eid = r[0]
                break
    if eid is None:
        if not create:
            return None
        eid = d.execute("INSERT INTO graph_entities (name, type, created_at) VALUES (?,?,?)",
                        (name, etype, E.now_ms())).lastrowid
        d.execute("INSERT OR REPLACE INTO graph_names (key, entity_id) VALUES (?,?)", (k, eid))
    else:
        _add_alias(d, eid, name)
    for a, ak in alias_keys[:6]:
        if ak and ak != k and ak not in JUNK_NAMES and len(ak) <= 60:
            _add_alias(d, eid, a)
    return eid


def find_entity(d, text: str) -> int | None:
    """Exact (case-insensitive) name/alias match, else the most-mentioned partial match."""
    k = key_of(text)
    if not k:
        return None
    r = d.execute("SELECT entity_id FROM graph_names WHERE key=?", (k,)).fetchone()
    if r:
        return r[0]
    r = d.execute("SELECT n.entity_id FROM graph_names n JOIN graph_entities e ON e.id = n.entity_id "
                  "WHERE n.key LIKE ? ORDER BY e.mentions DESC, length(n.key) LIMIT 1", (f"%{k}%",)).fetchone()
    return r[0] if r else None


# ---------------------------------------------------------------- indexing

def _session_signals(d, sid: str, apps_json: str, project: str | None) -> list[tuple]:
    """Non-LLM entity mentions: project, main apps (tools), visited hosts (sites)."""
    out = []
    if project and project.strip():
        out.append((project, "project", [], 80, "project"))
    apps = json.loads(apps_json or "{}")
    total = sum(apps.values()) or 1
    for app, secs in sorted(apps.items(), key=lambda x: -x[1])[:4]:
        share = secs / total
        if app.lower() in SKIP_APPS or app.lower() in E.SELF_APPS or (share < 0.1 and secs < 120):
            continue
        out.append((app, "tool", [], max(20, min(90, round(share * 100))), "app"))
    hosts = d.execute("SELECT url_host, count(*) FROM memory_chunks WHERE session_id=? AND url_host LIKE '%.%' "
                      "GROUP BY 1 ORDER BY 2 DESC LIMIT 8", (sid,)).fetchall()
    n = sum(c for _, c in hosts) or 1
    for host, c in hosts[:5]:
        host = host.lower()
        if host in SKIP_HOSTS or re.fullmatch(r"[\d.]+", host):
            continue
        site = re.sub(r"^www\d?\.", "", host)
        out.append((site, "site", [host] if host != site else [], max(20, min(90, round(100 * c / n))), "site"))
    return out


def _recompute_edges(d, ids) -> None:
    """Rebuild every edge whose two ends are both in `ids` from graph_entity_sessions."""
    ids = sorted(set(ids))
    if len(ids) < 2:
        return
    m = _marks(len(ids))
    d.execute(f"DELETE FROM graph_edges WHERE a IN ({m}) AND b IN ({m})", ids + ids)
    rows = d.execute(
        "SELECT x.entity_id, y.entity_id, x.salience, y.salience, x.source, y.source, x.at "
        "FROM graph_entity_sessions x JOIN graph_entity_sessions y "
        "ON x.session_id = y.session_id AND x.entity_id < y.entity_id "
        f"WHERE x.entity_id IN ({m}) AND y.entity_id IN ({m})", ids + ids).fetchall()
    _store_edges(d, rows)


def _store_edges(d, rows) -> None:
    agg: dict = {}
    for a, b, sa, sb, ra, rb, at in rows:
        w = math.sqrt(max(sa, 5) * max(sb, 5)) / 100
        if ra in ("app", "site") or rb in ("app", "site"):
            w *= 0.5  # tools/sites co-occur with everything; let named things dominate
        lw, cnt, last = agg.get((a, b), (None, 0, 0))
        agg[(a, b)] = (_logaddexp(lw, _lw_term(w, at)), cnt + 1, max(last, at))
    d.executemany("INSERT OR REPLACE INTO graph_edges (a, b, lw, count, last_seen) VALUES (?,?,?,?,?)",
                  [(a, b, lw, c, last) for (a, b), (lw, c, last) in agg.items()])


def _entity_edges(d, eid: int) -> None:
    """Rebuild all edges touching one entity."""
    d.execute("DELETE FROM graph_edges WHERE a=? OR b=?", (eid, eid))
    rows = d.execute(
        "SELECT x.entity_id, y.entity_id, x.salience, y.salience, x.source, y.source, x.at "
        "FROM graph_entity_sessions x JOIN graph_entity_sessions y ON x.session_id = y.session_id "
        "WHERE (x.entity_id = ? AND y.entity_id != ?) ", (eid, eid)).fetchall()
    _store_edges(d, [(a, b, sa, sb, ra, rb, at) if a < b else (b, a, sb, sa, rb, ra, at)
                     for a, b, sa, sb, ra, rb, at in rows])


def _update_stats(d, ids) -> None:
    ids = sorted(set(ids))
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        d.execute(
            "UPDATE graph_entities SET "
            "mentions=(SELECT count(*) FROM graph_entity_sessions s WHERE s.entity_id=graph_entities.id), "
            "first_seen=(SELECT min(at) FROM graph_entity_sessions s WHERE s.entity_id=graph_entities.id), "
            "last_seen=(SELECT max(at) FROM graph_entity_sessions s WHERE s.entity_id=graph_entities.id) "
            f"WHERE id IN ({_marks(len(part))})", part)
        gone = [r[0] for r in d.execute(
            f"SELECT id FROM graph_entities WHERE mentions=0 AND id IN ({_marks(len(part))})", part)]
        for eid in gone:
            _drop_entity(d, eid)
    for eid in ids:
        _sync_synonyms(d, eid)


def _drop_entity(d, eid: int) -> None:
    d.execute("DELETE FROM graph_entities WHERE id=?", (eid,))
    d.execute("DELETE FROM graph_names WHERE entity_id=?", (eid,))
    d.execute("DELETE FROM graph_entity_sessions WHERE entity_id=?", (eid,))
    d.execute("DELETE FROM graph_edges WHERE a=? OR b=?", (eid, eid))
    mem.clear_synonyms(d, f"g{eid}")


def _sync_synonyms(d, eid: int) -> None:
    ent = _entity_row(d, eid)
    if not ent:
        mem.clear_synonyms(d, f"g{eid}")
        return
    mem.set_synonyms(d, f"g{eid}", [ent["name"], *ent["aliases"]])


def refresh_session_doc(d, sid: str, row=None, embed: bool = True) -> None:
    """Session-level search document: report text + the named things it mentions (with aliases)."""
    row = row or d.execute("SELECT started_at, apps, title, summary, project, insights, status FROM sessions "
                           "WHERE id=?", (sid,)).fetchone()
    if not row or not (row[2] or row[3]):
        mem.delete_session_doc(d, sid)
        return
    started, apps_json, title, summary, project, insights_json, _ = row
    try:
        insights = [i.get("headline", "") for i in json.loads(insights_json or "[]") if isinstance(i, dict)]
    except ValueError:
        insights = []
    ents = d.execute("SELECT e.name, e.aliases FROM graph_entity_sessions s JOIN graph_entities e ON e.id = s.entity_id "
                     "WHERE s.session_id=? AND s.source IN ('llm', 'project') ORDER BY s.salience DESC LIMIT 12",
                     (sid,)).fetchall()
    hosts = [r[0] for r in d.execute("SELECT DISTINCT url_host FROM memory_chunks WHERE session_id=? "
                                     "AND url_host LIKE '%.%' LIMIT 12", (sid,))]
    text = mem.compose_session_doc(title or "", project or "", summary or "", insights,
                                   [(n, json.loads(a or "[]")) for n, a in ents])
    apps = " ".join([*json.loads(apps_json or "{}").keys(), *hosts]).lower()
    mem.upsert_session_doc(d, sid, text, started, title=title or "", apps=apps, embed=embed)


def index_session(d, sid: str, embed: bool = True) -> int:
    """(Re)build one session's links from its stored LLM entities + local signals. Returns #entities."""
    row = d.execute("SELECT started_at, apps, title, summary, project, insights, status FROM sessions "
                    "WHERE id=?", (sid,)).fetchone()
    old = {r[0] for r in d.execute("SELECT entity_id FROM graph_entity_sessions WHERE session_id=?", (sid,))}
    if not row:
        forget_session(d, sid)
        return 0
    state = d.execute("SELECT raw FROM graph_session_state WHERE session_id=?", (sid,)).fetchone()
    try:
        raw = json.loads(state[0]) if state and state[0] else []
    except ValueError:
        raw = []
    mentions = [[e.get("name"), e.get("type"), list(e.get("aliases") or []), e.get("salience"), "llm"]
                for e in raw if isinstance(e, dict)][:MAX_PER_SESSION]
    pk = key_of(row[4] or "")
    for m in mentions:  # the report's project and an extracted project that names it are one entity
        mk = key_of(m[0])
        if pk and norm_type(m[1]) == "project" and mk and (pk in mk or mk in pk):
            m[2].append(row[4])
            pk = ""
            break
    signals = _session_signals(d, sid, row[1], row[4] if pk else None)
    mentions += [list(x) for x in signals]
    links: dict[int, tuple[int, str]] = {}
    for name, etype, aliases, sal, src in mentions:
        eid = resolve(d, name, etype, [a for a in aliases if isinstance(a, str)])
        if eid is None:
            continue
        try:
            sal = max(5, min(100, int(sal if sal is not None else 50)))
        except (TypeError, ValueError):
            sal = 50
        prev = links.get(eid)
        if prev:
            sal = max(sal, prev[0])
            src = src if SOURCE_RANK[src] < SOURCE_RANK[prev[1]] else prev[1]
        links[eid] = (sal, src)
    d.execute("DELETE FROM graph_entity_sessions WHERE session_id=?", (sid,))
    d.executemany("INSERT INTO graph_entity_sessions (entity_id, session_id, salience, source, at) "
                  "VALUES (?,?,?,?,?)", [(eid, sid, s, src, row[0]) for eid, (s, src) in links.items()])
    if not state:
        d.execute("INSERT INTO graph_session_state (session_id, raw, source, updated_at) VALUES (?,?,?,?)",
                  (sid, None, "signals", E.now_ms()))
    affected = old | set(links)
    _recompute_edges(d, affected)
    _update_stats(d, affected)
    refresh_session_doc(d, sid, row, embed=embed)
    return len(links)


def on_session_deleted(sid: str) -> None:
    with _Db() as d:
        forget_session(d, sid)


def forget_session(d, sid: str) -> None:
    old = {r[0] for r in d.execute("SELECT entity_id FROM graph_entity_sessions WHERE session_id=?", (sid,))}
    d.execute("DELETE FROM graph_entity_sessions WHERE session_id=?", (sid,))
    d.execute("DELETE FROM graph_session_state WHERE session_id=?", (sid,))
    mem.delete_session_doc(d, sid)
    _recompute_edges(d, old)
    _update_stats(d, old)


def save_entities(d, sid: str, entities, source: str) -> int:
    clean = []
    for e in entities or []:
        if isinstance(e, dict) and clean_name(e.get("name")):
            clean.append({"name": clean_name(e.get("name")), "type": norm_type(e.get("type")),
                          "aliases": [clean_name(a) for a in (e.get("aliases") or []) if isinstance(a, str)
                                      and clean_name(a)][:6],
                          "salience": e.get("salience")})
    d.execute("INSERT OR REPLACE INTO graph_session_state (session_id, raw, source, updated_at) VALUES (?,?,?,?)",
              (sid, json.dumps(clean[:MAX_PER_SESSION]), source, E.now_ms()))
    return index_session(d, sid)


def prune(d) -> int:
    """Drop graph rows and session docs for sessions that no longer exist."""
    dead = [r[0] for r in d.execute(
        "SELECT DISTINCT session_id FROM graph_entity_sessions WHERE session_id NOT IN (SELECT id FROM sessions) "
        "UNION SELECT session_id FROM graph_session_state WHERE session_id NOT IN (SELECT id FROM sessions) "
        "UNION SELECT session_id FROM memory_session_docs WHERE session_id NOT IN (SELECT id FROM sessions)")]
    for sid in dead:
        forget_session(d, sid)
    return len(dead)


# ------------------------------------------------------------------- hooks

def on_summary(sid: str, summary: dict) -> None:
    with _Db() as d:
        n = save_entities(d, sid, summary.get("entities") or [], "summary")
    E.log(f"graph: session {sid[:8]} linked to {n} entities")


def on_session_end(sid: str) -> None:
    try:
        with _Db() as d:
            index_session(d, sid, embed=False)
    except Exception as e:
        E.log(f"graph: index failed for {sid[:8]}: {e.__class__.__name__}")


def catch_up() -> None:
    """At startup: prune, then index sessions the graph hasn't seen (local signals only, no LLM)."""
    try:
        with _Db() as d:
            pruned = prune(d)
            todo = [r[0] for r in d.execute(
                "SELECT id FROM sessions WHERE status != 'active' AND (id NOT IN (SELECT session_id FROM "
                "graph_session_state) OR ((title IS NOT NULL OR summary IS NOT NULL) AND id NOT IN "
                "(SELECT session_id FROM memory_session_docs))) ORDER BY started_at DESC LIMIT 5000")]
        for i, sid in enumerate(todo):
            if E.STOP.is_set():
                return
            with _Db() as d:
                index_session(d, sid, embed=False)
            if i % 50 == 49:
                time.sleep(0.05)  # let captures and Ask get the lock
        with _Db() as d:
            mem.embed_session_docs(d)
        if todo or pruned:
            E.log(f"graph: indexed {len(todo)} sessions, pruned {pruned}")
    except Exception as e:
        E.log(f"graph: catch-up failed: {e.__class__.__name__}: {e}")


# ----------------------------------------------------------- backfill job

JOB: dict = {"kind": None, "running": False, "done": 0, "total": 0, "failed": 0, "linked": 0,
             "error": None, "started_at": None, "finished_at": None}
JOB_LOCK = threading.Lock()
CANCEL = threading.Event()


def _pending_backfill(limit: int) -> list[str]:
    with _Db() as d:
        return [r[0] for r in d.execute(
            "SELECT s.id FROM sessions s LEFT JOIN graph_session_state g ON g.session_id = s.id "
            "WHERE s.status = 'summarized' AND (g.session_id IS NULL OR g.raw IS NULL) "
            "ORDER BY s.started_at DESC LIMIT ?", (limit,))]


def extract_entities(sid: str) -> list:
    """One small LLM call: the session report + a short capture excerpt -> entities."""
    s = E.session_rows("s.id = ?", (sid,), 1)
    if not s:
        raise ValueError("unknown session")
    s = s[0]
    head = (f"Title: {s['title'] or '-'}\nProject: {s.get('project') or '-'}\nSummary: {s['summary'] or '-'}\n"
            "Insights: " + "; ".join(i.get("headline", "") for i in s["insights"] if isinstance(i, dict)))
    prompt = f"{head}\n\nCapture excerpt:\n{E.session_digest(sid, budget=5000)}"
    schema = {"type": "object", "properties": {"entities": ENTITY_SCHEMA}, "required": ["entities"],
              "additionalProperties": False}
    r = E.provider().complete_json(BACKFILL_SYSTEM, prompt, schema)
    return r.get("entities") or []


def _run_backfill(ids: list[str], delay: float) -> None:
    streak = 0
    try:
        for i, sid in enumerate(ids):
            if CANCEL.is_set():
                break
            try:
                ents = extract_entities(sid)
                with _Db() as d:
                    JOB["linked"] += save_entities(d, sid, ents, "backfill")
                streak = 0
            except ai.ProviderError as e:
                JOB["error"] = str(e)
                break
            except Exception as e:
                JOB["failed"] += 1
                streak += 1
                E.log(f"graph: backfill failed for {sid[:8]}: {e.__class__.__name__}")
                if streak >= 5:
                    JOB["error"] = f"Stopped after repeated failures ({e.__class__.__name__})."
                    break
            JOB["done"] = i + 1
            if i + 1 < len(ids) and CANCEL.wait(delay):
                break
    finally:
        JOB.update(running=False, finished_at=E.now_ms())
        if CANCEL.is_set() and not JOB["error"]:
            JOB["error"] = "Cancelled."


def _run_reindex() -> None:
    try:
        with _Db() as d:
            pruned = prune(d)
            ids = [r[0] for r in d.execute("SELECT id FROM sessions WHERE status != 'active' "
                                           "ORDER BY started_at DESC")]
        JOB["total"] = len(ids)
        with _Db() as d:
            mem.clear_synonyms(d)
            for (eid,) in d.execute("SELECT id FROM graph_entities").fetchall():
                _sync_synonyms(d, eid)
        for i, sid in enumerate(ids):
            if CANCEL.is_set():
                JOB["error"] = "Cancelled."
                break
            with _Db() as d:
                JOB["linked"] += index_session(d, sid, embed=False)
            JOB["done"] = i + 1
            if i % 50 == 49:
                time.sleep(0.05)
        with _Db() as d:
            d.execute("DELETE FROM graph_edges")
            _store_edges(d, d.execute(
                "SELECT x.entity_id, y.entity_id, x.salience, y.salience, x.source, y.source, x.at "
                "FROM graph_entity_sessions x JOIN graph_entity_sessions y "
                "ON x.session_id = y.session_id AND x.entity_id < y.entity_id").fetchall())
            _update_stats(d, [r[0] for r in d.execute("SELECT id FROM graph_entities")])
            mem.embed_session_docs(d)
        if pruned:
            E.log(f"graph: re-index pruned {pruned} deleted sessions")
    except Exception as e:
        JOB["error"] = f"{e.__class__.__name__}: {e}"
        E.log(f"graph: re-index failed: {e.__class__.__name__}")
    finally:
        JOB.update(running=False, finished_at=E.now_ms())


def _start(kind: str, target, total: int) -> dict:
    with JOB_LOCK:
        if JOB["running"]:
            raise ValueError(f"A graph {JOB['kind']} is already running.")
        CANCEL.clear()
        JOB.update(kind=kind, running=True, done=0, total=total, failed=0, linked=0, error=None,
                   started_at=E.now_ms(), finished_at=None)
        threading.Thread(target=target, daemon=True, name=f"graph-{kind}").start()
    return status()


def start_backfill(b: dict) -> dict:
    limit = max(1, min(int(b.get("limit") or 50), 1000))
    ids = _pending_backfill(limit)
    if not ids:
        return {**status(), "message": "Every summarised session already has entities."}
    E.provider()  # fail fast (ProviderError -> 400) when no model is configured
    delay = float(b.get("delay_s") if b.get("delay_s") is not None else
                  E.load_settings().get("graph_backfill_delay_s", 2.0))
    return _start("backfill", lambda: _run_backfill(ids, max(0.0, min(delay, 60.0))), len(ids))


def start_reindex(b: dict) -> dict:
    return _start("reindex", _run_reindex, 0)


def cancel_job(b: dict) -> dict:
    CANCEL.set()
    return status()


def status(q: dict | None = None) -> dict:
    with _Db() as d:
        c = lambda sql: d.execute(sql).fetchone()[0]  # noqa: E731
        counts = {
            "entities": c("SELECT count(*) FROM graph_entities"),
            "edges": c("SELECT count(*) FROM graph_edges"),
            "sessions": c("SELECT count(*) FROM sessions WHERE status != 'active'"),
            "summarized": c("SELECT count(*) FROM sessions WHERE status = 'summarized'"),
            "with_entities": c("SELECT count(*) FROM graph_session_state WHERE raw IS NOT NULL"),
            "pending_backfill": c("SELECT count(*) FROM sessions s LEFT JOIN graph_session_state g "
                                  "ON g.session_id = s.id WHERE s.status = 'summarized' "
                                  "AND (g.session_id IS NULL OR g.raw IS NULL)"),
            "session_docs": c("SELECT count(*) FROM memory_session_docs"),
        }
        by_type = dict(d.execute("SELECT type, count(*) FROM graph_entities GROUP BY 1").fetchall())
    return {**counts, "by_type": by_type, "job": dict(JOB),
            "delay_s": E.load_settings().get("graph_backfill_delay_s", 2.0)}


# -------------------------------------------------------------- read API

def _types_filter(q: dict) -> list[str]:
    raw = q.get("types")
    if isinstance(raw, list):
        items = raw
    else:
        items = [t for t in str(raw or "").split(",") if t]
    return [t for t in (norm_type(x) for x in items) if t in GRAPH_TYPES]


def related(d, eid: int, limit: int = 12, types: list[str] | None = None) -> list[dict]:
    me = _entity_row(d, eid)
    if not me:
        return []
    now = E.now_ms()
    rows = d.execute(
        "SELECT CASE WHEN g.a = ? THEN g.b ELSE g.a END AS o, g.lw, g.count, g.last_seen, e.name, e.type, e.mentions "
        "FROM graph_edges g JOIN graph_entities e ON e.id = (CASE WHEN g.a = ? THEN g.b ELSE g.a END) "
        "WHERE g.a = ? OR g.b = ?", (eid, eid, eid, eid)).fetchall()
    out = []
    for o, lw, cnt, last, name, etype, ment in rows:
        if types and etype not in types:
            continue
        w = edge_weight(lw, now)
        assoc = cnt / math.sqrt(max(1, me["mentions"]) * max(1, ment))  # discounts hubs (browsers, Slack)
        out.append({"id": o, "name": name, "type": etype, "weight": round(w, 3), "count": cnt,
                    "last_seen": last, "mentions": ment, "score": w * (0.3 + assoc)})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def entity_sessions(d, eid: int, limit: int = 20) -> list[dict]:
    rows = d.execute(
        "SELECT s.id, coalesce(s.title, ''), s.started_at, coalesce(s.ended_at, s.last_seen), g.salience, g.source, "
        "s.project FROM graph_entity_sessions g JOIN sessions s ON s.id = g.session_id WHERE g.entity_id=? "
        "ORDER BY s.started_at DESC LIMIT ?", (eid, limit)).fetchall()
    return [{"id": r[0], "title": r[1] or "Untitled session", "started_at": r[2], "ended_at": r[3],
             "salience": r[4], "source": r[5], "project": r[6]} for r in rows]


def entity_detail(q: dict) -> dict:
    eid = int(q["id"])
    with _Db() as d:
        ent = _entity_row(d, eid)
        if not ent:
            raise ValueError("unknown entity")
        return {**ent, "related": [{k: v for k, v in r.items() if k != "score"} for r in related(d, eid, 24)],
                "sessions": entity_sessions(d, eid, int(q.get("sessions") or 30))}


def search_entities(q: dict) -> dict:
    text = key_of(q.get("q") or "")
    limit = max(1, min(int(q.get("limit") or 20), 100))
    types = _types_filter(q)
    with _Db() as d:
        if not text:
            rows = d.execute("SELECT id FROM graph_entities ORDER BY last_seen DESC LIMIT ?", (limit * 3,)).fetchall()
        else:
            rows = d.execute(
                "SELECT n.entity_id, min(CASE WHEN n.key = ? THEN 0 WHEN n.key LIKE ? THEN 1 ELSE 2 END) r "
                "FROM graph_names n WHERE n.key LIKE ? GROUP BY 1 ORDER BY r LIMIT ?",
                (text, f"{text}%", f"%{text}%", limit * 3)).fetchall()
        ents = [e for e in (_entity_row(d, r[0]) for r in rows) if e and (not types or e["type"] in types)]
    if text:
        rank = {r[0]: r[1] for r in rows}
        ents.sort(key=lambda e: (rank.get(e["id"], 3), -e["mentions"]))
    return {"entities": ents[:limit]}


def _activity_rows(d, since: int | None, types: list[str]):
    sql = ("SELECT g.entity_id, sum(g.salience * 1.0), count(*), max(g.at) FROM graph_entity_sessions g "
           "JOIN graph_entities e ON e.id = g.entity_id WHERE 1=1")
    params: list = []
    if since:
        sql += " AND g.at >= ?"
        params.append(since)
    if types:
        sql += f" AND e.type IN ({_marks(len(types))})"
        params += types
    return d.execute(sql + " GROUP BY 1", params).fetchall()


def _subgraph(d, ids: list[int], since: int | None) -> list[dict]:
    if len(ids) < 2:
        return []
    m = _marks(len(ids))
    sql = ("SELECT x.entity_id, y.entity_id, count(*), max(x.at), "
           "sum(sqrt(max(x.salience, 5) * max(y.salience, 5)) / 100.0 * "
           "CASE WHEN x.source IN ('app','site') OR y.source IN ('app','site') THEN 0.5 ELSE 1.0 END) "
           "FROM graph_entity_sessions x JOIN graph_entity_sessions y "
           "ON x.session_id = y.session_id AND x.entity_id < y.entity_id "
           f"WHERE x.entity_id IN ({m}) AND y.entity_id IN ({m})")
    params: list = ids + ids
    if since:
        sql += " AND x.at >= ?"
        params.append(since)
    rows = d.execute(sql + " GROUP BY 1, 2", params).fetchall()
    return [{"a": a, "b": b, "count": c, "last_seen": last, "weight": round(w, 3)} for a, b, c, last, w in rows]


def _node(e: dict, act=None) -> dict:
    return {"id": e["id"], "name": e["name"], "type": e["type"], "mentions": e["mentions"],
            "last_seen": e["last_seen"], "first_seen": e["first_seen"],
            "activity": round(act, 2) if act is not None else e["mentions"]}


def neighborhood(q: dict) -> dict:
    """With `id`: that entity's ego graph (depth 1-2). Without: the most active entities in the window.
    Query: days (0 = all time), types (comma list), limit (nodes)."""
    days = float(q.get("days") or 0)
    since = E.now_ms() - int(days * DAY_MS) if days > 0 else None
    types = _types_filter(q)
    limit = max(5, min(int(q.get("limit") or 80), 300))
    now = E.now_ms()
    with _Db() as d:
        act = {}
        for eid, sal, n, last in _activity_rows(d, since, types):
            # salience-weighted mentions, lightly favouring recent activity
            act[eid] = sal / 100.0 * (0.6 + 0.4 * 0.5 ** ((now - last) / (14 * DAY_MS)))
        center = int(q["id"]) if q.get("id") else None
        if center is not None:
            if not _entity_row(d, center):
                raise ValueError("unknown entity")
            depth = max(1, min(int(q.get("depth") or 1), 2))
            chosen, frontier = {center}, [center]
            for level in range(depth):
                nxt = []
                for f in frontier:
                    budget = 30 if level == 0 else 6
                    for r in related(d, f, 200, types):
                        if since and r["last_seen"] < since:
                            continue
                        if r["id"] not in chosen:
                            chosen.add(r["id"])
                            nxt.append(r["id"])
                            budget -= 1
                        if budget <= 0 or len(chosen) >= limit:
                            break
                frontier = nxt
            ids = list(chosen)
        else:
            ids = sorted(act, key=lambda i: -act[i])[:limit]
        ents = [e for e in (_entity_row(d, i) for i in ids) if e]
        edges = _subgraph(d, [e["id"] for e in ents], since)
    return {"nodes": [_node(e, act.get(e["id"])) for e in ents], "edges": edges, "center": center,
            "types": list(GRAPH_TYPES), "days": days}


# ------------------------------------------------------------- write API

def _sessions_of(d, eid: int) -> list[str]:
    return [r[0] for r in d.execute("SELECT session_id FROM graph_entity_sessions WHERE entity_id=?", (eid,))]


def _refresh_docs(d, sids) -> None:
    for sid in set(sids):
        refresh_session_doc(d, sid, embed=False)
    mem.embed_session_docs(d)


def merge(b: dict) -> dict:
    into = int(b["into"])
    ids = [int(x) for x in (b.get("ids") or [b.get("id")]) if x is not None and int(x) != into]
    if not ids:
        raise ValueError("nothing to merge")
    with _Db() as d:
        dst = _entity_row(d, into)
        if not dst:
            raise ValueError("unknown entity")
        touched = []
        for src_id in ids:
            src = _entity_row(d, src_id)
            if not src:
                continue
            touched += _sessions_of(d, src_id)
            d.execute("UPDATE graph_names SET entity_id=? WHERE entity_id=?", (into, src_id))
            for alias in [src["name"], *src["aliases"]]:
                _add_alias(d, into, alias)
            for sid, sal, source, at in d.execute("SELECT session_id, salience, source, at FROM "
                                                  "graph_entity_sessions WHERE entity_id=?", (src_id,)).fetchall():
                d.execute("INSERT INTO graph_entity_sessions (entity_id, session_id, salience, source, at) "
                          "VALUES (?,?,?,?,?) ON CONFLICT(entity_id, session_id) DO UPDATE SET "
                          "salience=max(salience, excluded.salience)", (into, sid, sal, source, at))
            d.execute("DELETE FROM graph_entity_sessions WHERE entity_id=?", (src_id,))
            d.execute("DELETE FROM graph_edges WHERE a=? OR b=?", (src_id, src_id))
            d.execute("DELETE FROM graph_entities WHERE id=?", (src_id,))
            mem.clear_synonyms(d, f"g{src_id}")
        _entity_edges(d, into)
        _update_stats(d, [into])
        _refresh_docs(d, touched)
        return {"entity": _entity_row(d, into)}


def rename(b: dict) -> dict:
    eid = int(b["id"])
    with _Db() as d:
        ent = _entity_row(d, eid)
        if not ent:
            raise ValueError("unknown entity")
        name = clean_name(b.get("name") or ent["name"])
        k = key_of(name)
        if not k:
            raise ValueError("name can't be empty")
        owner = d.execute("SELECT entity_id FROM graph_names WHERE key=?", (k,)).fetchone()
        if owner and owner[0] != eid:
            other = _entity_row(d, owner[0])
            raise ValueError(f"“{other['name'] if other else name}” already exists — merge them instead.")
        etype = norm_type(b.get("type") or ent["type"])
        aliases = [a for a in ent["aliases"] if key_of(a) != k]
        if key_of(ent["name"]) != k and b.get("keep_old_name", True):
            aliases.append(ent["name"])  # the old name keeps resolving to this entity
        elif key_of(ent["name"]) != k:
            d.execute("DELETE FROM graph_names WHERE key=?", (key_of(ent["name"]),))
        d.execute("INSERT OR REPLACE INTO graph_names (key, entity_id) VALUES (?,?)", (k, eid))
        d.execute("UPDATE graph_entities SET name=?, type=?, aliases=? WHERE id=?",
                  (name, etype, json.dumps(aliases[:24]), eid))
        _sync_synonyms(d, eid)
        _refresh_docs(d, _sessions_of(d, eid))
        return {"entity": _entity_row(d, eid)}


def edit_aliases(b: dict) -> dict:
    eid = int(b["id"])
    with _Db() as d:
        ent = _entity_row(d, eid)
        if not ent:
            raise ValueError("unknown entity")
        for alias in b.get("remove") or []:
            k = key_of(alias)
            ent["aliases"] = [a for a in ent["aliases"] if key_of(a) != k]
            if k != key_of(ent["name"]):
                d.execute("DELETE FROM graph_names WHERE key=? AND entity_id=?", (k, eid))
        d.execute("UPDATE graph_entities SET aliases=? WHERE id=?", (json.dumps(ent["aliases"]), eid))
        refused = []
        for alias in b.get("add") or []:
            k = key_of(alias)
            owner = d.execute("SELECT entity_id FROM graph_names WHERE key=?", (k,)).fetchone()
            if owner and owner[0] != eid:
                refused.append(alias)
                continue
            d.execute("DELETE FROM graph_blocked WHERE key=?", (k,))  # an explicit alias overrides a block
            _add_alias(d, eid, alias)
        _sync_synonyms(d, eid)
        _refresh_docs(d, _sessions_of(d, eid))
        return {"entity": _entity_row(d, eid), "refused": refused}


def delete(b: dict) -> dict:
    eid = int(b["id"])
    with _Db() as d:
        ent = _entity_row(d, eid)
        if not ent:
            raise ValueError("unknown entity")
        sids = _sessions_of(d, eid)
        neighbours = [r[0] for r in d.execute("SELECT CASE WHEN a=? THEN b ELSE a END FROM graph_edges "
                                              "WHERE a=? OR b=?", (eid, eid, eid))]
        if b.get("block", True):  # extraction won't bring it back
            keys = {key_of(ent["name"])} | {r[0] for r in d.execute("SELECT key FROM graph_names WHERE entity_id=?",
                                                                    (eid,))}
            d.executemany("INSERT OR IGNORE INTO graph_blocked (key) VALUES (?)", [(k,) for k in keys if k])
        _drop_entity(d, eid)
        _update_stats(d, neighbours)
        _refresh_docs(d, sids)
    return {"deleted": eid, "sessions": len(sids)}


# ---------------------------------------------------------- agent + context

def tool_memory_graph(args: dict) -> str:
    query = (args.get("entity") or "").strip()
    depth = max(1, min(int(args.get("depth") or 1), 2))
    with _Db() as d:
        if not query:
            top = neighborhood({"days": 30, "limit": 20})["nodes"]
            if not top:
                return "The knowledge graph is empty so far."
            return "Most active entities (last 30 days):\n" + "\n".join(
                f"- {n['name']} ({n['type']}, {n['mentions']} sessions, last {fmt_day(n['last_seen'])})"
                for n in top)
        eid = find_entity(d, query)
        if eid is None:
            close = search_entities({"q": query.split()[0] if query.split() else query, "limit": 6})["entities"]
            hint = ("; closest: " + ", ".join(f"{e['name']} ({e['type']})" for e in close)) if close else ""
            return f"No entity called “{query}” in the knowledge graph{hint}. Try search_memory instead."
        ent = _entity_row(d, eid)
        rel = related(d, eid, 30)
        lines = [f"{ent['name']} ({ent['type']})" + (f" — also: {', '.join(ent['aliases'][:8])}" if ent["aliases"] else ""),
                 f"Seen in {ent['mentions']} sessions, first {fmt_day(ent['first_seen'])}, "
                 f"last {fmt_day(ent['last_seen'])}."]
        named = [r for r in rel if r["type"] not in ("tool", "site")][:10]
        places = [r for r in rel if r["type"] in ("tool", "site")][:6]
        if named:
            lines.append("Related (co-occurrence weight; recent sessions count more):")
            for i, r in enumerate(named):
                lines.append(f"- {r['name']} ({r['type']}) · weight {r['weight']:.2f} · {r['count']} shared "
                             f"session{'s' if r['count'] != 1 else ''} · last {fmt_day(r['last_seen'])}")
                if depth > 1 and i < 5:
                    sub = [x for x in related(d, r["id"], 8) if x["id"] != eid
                           and x["type"] not in ("tool", "site")][:4]
                    if sub:
                        lines.append("    ↳ " + ", ".join(f"{x['name']} ({x['type']})" for x in sub))
        if places:
            lines.append("Usually in: " + ", ".join(f"{r['name']} ({r['type']})" for r in places))
        sess = entity_sessions(d, eid, 8)
        if sess:
            lines.append("Recent sessions (use read_session with the id for details):")
            lines += [f"- {s['id'][:8]} · {fmt_dt(s['started_at'])} · {s['title']}" for s in sess]
    return "\n".join(lines)


_CTX_CACHE: dict = {"at": 0.0, "text": None}


def context_hook() -> str | None:
    if time.time() - _CTX_CACHE["at"] < 120:
        return _CTX_CACHE["text"]
    since = E.now_ms() - 7 * DAY_MS
    with _Db() as d:
        rows = d.execute(
            "SELECT e.name, e.type, count(*), sum(g.salience) s FROM graph_entity_sessions g JOIN graph_entities e "
            "ON e.id = g.entity_id WHERE g.at >= ? AND e.type NOT IN ('tool', 'site') "
            "GROUP BY e.id ORDER BY s DESC LIMIT 10", (since,)).fetchall()
    text = None
    if rows:
        text = ("[Top people/projects/things this week — memory_graph(entity) shows relations and sessions]\n"
                + ", ".join(f"{n} ({t}, {c})" for n, t, c, _ in rows))
    _CTX_CACHE.update(at=time.time(), text=text)
    return text


# ------------------------------------------------------------------ register

def register():
    with _Db():
        pass  # create tables
    E.SESSION_SCHEMA_EXTRA["entities"] = ENTITY_SCHEMA
    E.SESSION_SUMMARY_HOOKS.append(on_summary)
    E.SESSION_END_HOOKS.append(on_session_end)
    if hasattr(E, "SESSION_DELETE_HOOKS"):
        E.SESSION_DELETE_HOOKS.append(on_session_deleted)
    E.STARTUP_HOOKS.append(catch_up)
    E.CONTEXT_HOOKS.append(context_hook)
    E.register_tool(ai.Tool(
        "memory_graph",
        "Look up a person, project, organisation, document, topic, tool, place or site in the user's "
        "knowledge graph (built from their work sessions): its aliases, the entities it most often appears "
        "with, and the recent sessions that mention it (ids for read_session). Use for 'who/what is X', "
        "'what's related to X', 'who works with X on Y'. Empty entity = the most active entities.",
        E._schema({"entity": {"type": "string", "description": "Name or alias; empty for an overview"},
                   "depth": {"type": ["integer", "null"],
                             "description": "1 = direct relations (default), 2 = also their relations"}}),
        tool_memory_graph), quick=True)
    E.GET_ROUTES.update({
        "/api/graph/status": status,
        "/api/graph/neighborhood": neighborhood,
        "/api/graph/entity": entity_detail,
        "/api/graph/search": search_entities,
    })
    E.POST_ROUTES.update({
        "/api/graph/merge": merge,
        "/api/graph/rename": rename,
        "/api/graph/aliases": edit_aliases,
        "/api/graph/delete": delete,
        "/api/graph/backfill": start_backfill,
        "/api/graph/cancel": cancel_job,
        "/api/graph/reindex": start_reindex,
    })
