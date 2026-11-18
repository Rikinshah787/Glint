#!/usr/bin/env python3
"""Glint engine: background capture, work sessions, memory search, Ask agent, local API.

Runs as a localhost sidecar of Glint.app (or standalone: `python engine.py`).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_SUPPORT = Path(os.environ.get("GLINT_HOME",
                                  Path.home() / "Library" / "Application Support" / "Glint"))
os.environ["GLINT_HOME"] = str(APP_SUPPORT)
ROOT = Path(__file__).resolve().parent           # <repo>/engine
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":  # let feature modules `import engine` and get this running module
    sys.modules.setdefault("engine", sys.modules[__name__])

import memory as mem  # noqa: E402  memory chunks, passages, hybrid search
import ai  # noqa: E402

WEB = REPO / "web"
FEATURES_DIR = ROOT / "features"
WEB_FEATURES = WEB / "features"
SETTINGS_PATH = APP_SUPPORT / "settings.json"
CAPTURE_BIN = REPO / "bin" / "glint-capture"

KEYCHAIN_SERVICE = "Glint"
SELF_APPS = {"glint"}  # never capture our own windows
# System authentication / lock surfaces: never captured, never counted.
SYSTEM_SKIP_APPS = {"securityagent", "coreautha", "loginwindow", "screensaverengine",
                    "universalaccessauthwarn", "coreservicesuiagent", "authenticationservicesagent",
                    "usernotificationcenter", "notificationcenter", "controlcenter", "dock", "systemuiserver",
                    "windowmanager", "spotlight"}


def web_host(url: str | None) -> str | None:
    """Host of a real web page; None for app-internal schemes (file:, vscode-file:, chrome:...)."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    host = urlparse(url).hostname
    return host if host and ("." in host or host == "localhost") else None

DEFAULT_SETTINGS = {
    "provider": "auto",                # auto | apple | claude | openai
    "fast_on_device": True,            # quick tasks (autocomplete, dictation cleanup) on Apple's model
    "claude_model": "claude-opus-5-5",
    "openai_model": "gpt-5",
    "capture_enabled": True,
    "capture_interval_s": 20,          # idle-fallback full snapshot while a window stays focused
    "idle_seconds": 180,               # session ends after this long without input
    "capture_text_all_apps": True,     # False: only apps in text_apps get window text
    "text_apps": [],
    "min_session_minutes": 2,
    "ocr_fallback": False,
    "blocked_apps": sorted(mem.DEFAULT_BLOCKED_APPS),
    "retention_days": None,
    "summarize_sessions": True,
    "web_search": True,
    "open_at_login": True,
    "site_icons": True,
    "dictation_enabled": True,
    "dictation_polish": True,
    "dictation_styles": {"personal": "very_casual", "work": "casual", "email": "formal", "other": "formal"},
    "transcribe_model": "whisper-1",
    "accent": "green",
    "theme": "system",
}

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    started_at  INTEGER NOT NULL,
    ended_at    INTEGER,
    last_seen   INTEGER NOT NULL,
    apps        TEXT NOT NULL DEFAULT '{}',
    title       TEXT,
    summary     TEXT,
    state_tag   TEXT,
    focus_score INTEGER,
    insights    TEXT NOT NULL DEFAULT '[]',
    state_name  TEXT,
    quality     TEXT,
    note        TEXT,
    project     TEXT,
    status      TEXT NOT NULL DEFAULT 'active'      -- active | ended | summarized
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE TABLE IF NOT EXISTS chat_threads (
    id TEXT PRIMARY KEY, title TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    renamed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY, thread_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
    tools TEXT NOT NULL DEFAULT '[]', created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_chat_thread ON chat_messages(thread_id, id);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
    notes TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0, logs TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dictation_dictionary (
    id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL COLLATE NOCASE UNIQUE,
    alias TEXT COLLATE NOCASE, source TEXT NOT NULL DEFAULT 'manual',
    created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000), updated_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000));
"""

DB_LOCK = threading.RLock()
STOP = threading.Event()


def now_ms() -> int:
    return int(time.time() * 1000)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ----------------------------------------------------------------- storage

def db():
    d = mem.open_db()
    d.executescript(EXTRA_SCHEMA)
    return d


def init_db() -> None:
    with DB_LOCK:
        d = db()
        cols = {r[1] for r in d.execute("PRAGMA table_info(memory_chunks)")}
        if "session_id" not in cols:
            d.execute("ALTER TABLE memory_chunks ADD COLUMN session_id TEXT")
        for table, col, ddl in (("tasks", "progress", "INTEGER NOT NULL DEFAULT 0"),
                                ("tasks", "logs", "TEXT NOT NULL DEFAULT '[]'"),
                                ("chat_threads", "renamed", "INTEGER NOT NULL DEFAULT 0"),
                                ("sessions", "state_name", "TEXT"), ("sessions", "quality", "TEXT"),
                                ("sessions", "note", "TEXT"), ("sessions", "project", "TEXT")):
            if col not in {r[1] for r in d.execute(f"PRAGMA table_info({table})")}:
                d.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        # One-time move of old per-line notes into the lasting-memory document.
        if not d.execute("SELECT 1 FROM kv WHERE key='memory_doc'").fetchone():
            old = [r[0] for r in d.execute("SELECT text FROM notes ORDER BY id")]
            if old:
                doc = "## Preferences\n\n## Corrections\n\n## Standing notes\n" + "".join(f"- {t}\n" for t in old)
                d.execute("INSERT INTO kv VALUES ('memory_doc', ?)", (json.dumps(doc),))
        # Sessions left open by a previous run end at their last activity.
        d.execute("UPDATE sessions SET status='ended', ended_at=last_seen WHERE status='active'")
        d.commit()
        d.close()


def kv_get(key, default=None):
    with DB_LOCK:
        d = db()
        row = d.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        d.close()
    return json.loads(row[0]) if row else default


def kv_set(key, value) -> None:
    with DB_LOCK:
        d = db()
        d.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value)))
        d.commit()
        d.close()


# ---------------------------------------------------------------- settings

def load_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}


def save_settings(patch: dict) -> dict:
    s = load_settings()
    for k, v in patch.items():
        if k in DEFAULT_SETTINGS:
            s[k] = v
    s["capture_interval_s"] = max(5, int(s["capture_interval_s"]))
    s["idle_seconds"] = max(30, int(s["idle_seconds"]))
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=2))
    return s


def keychain_get(account: str) -> str | None:
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                        "-a", account, "-w"], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def keychain_set(account: str, secret: str | None) -> None:
    if not secret:
        subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE,
                        "-a", account], capture_output=True)
        return
    if not re.fullmatch(r"[A-Za-z0-9_\-.]+", secret):
        raise ValueError("API key contains unexpected characters")
    # Fed through stdin (`security -i`) so the key never appears in a process listing.
    cmd = f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a {account} -w "{secret}"\n'
    r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("Could not save the key to the Keychain")


def api_keys() -> dict:
    return {
        "claude": keychain_get("claude") or os.environ.get("ANTHROPIC_API_KEY"),
        "openai": keychain_get("openai") or os.environ.get("OPENAI_API_KEY"),
    }


def provider(s: dict | None = None):
    s = s or load_settings()
    keys, models = api_keys(), {"claude": s["claude_model"], "openai": s["openai_model"]}
    if s["provider"] in ("claude", "openai") and not keys.get(s["provider"]):
        return ai.make_provider("auto", keys, models)  # chosen cloud model has no key: use what works
    return ai.make_provider(s["provider"], keys, models)


def fast_provider(s: dict | None = None):
    """Quick, latency-sensitive tasks: Apple's on-device model when enabled and available."""
    s = s or load_settings()
    if s.get("fast_on_device", True) and ai.apple_available():
        return ai.AppleProvider()
    return provider(s)


def ai_status() -> dict:
    s = load_settings()
    try:
        effective = provider(s).name
    except ai.ProviderError:
        effective = None
    return {"provider": s["provider"], "effective": effective, "apple": ai.apple_available(),
            "apple_reason": ai._AppleServer.reason, "fast_on_device": s.get("fast_on_device", True)}


# ----------------------------------------------------------------- capture
# Capture: poll the foreground window ~1/s, take a full text
# snapshot on session start, on a settled focus change and on an idle fallback timer; sessions end
# after 180 s without input (end time = last activity); sessions shorter than 5 s or spent only on
# the login/lock screen are discarded; pausing keeps the session open.

LOCK_SCREEN_APPS = {"loginwindow", "screensaverengine", "lockscreen", "universalaccessauthwarn"}


APP_BUNDLES: dict[str, str] = {}  # app display name → bundle id, for icons


def remember_bundle(app: str, bundle_id: str | None) -> None:
    if bundle_id and APP_BUNDLES.get(app) != bundle_id:
        if not APP_BUNDLES:
            APP_BUNDLES.update(kv_get("app_bundles", {}))
        APP_BUNDLES[app] = bundle_id
        kv_set("app_bundles", APP_BUNDLES)


def idle_seconds() -> float:
    out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True).stdout
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(m.group(1)) / 1e9 if m else 0.0


def run_helper(*flags: str) -> dict:
    if not CAPTURE_BIN.exists():
        mem.ensure_capture_bin()
    proc = subprocess.run([str(CAPTURE_BIN), *flags], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "capture helper failed")
    return json.loads(proc.stdout)


class Capturer(threading.Thread):
    POLL_S = 1.5
    SETTLE_S = 1.0

    def __init__(self):
        super().__init__(daemon=True, name="capture")
        self.session_id: str | None = None
        self.paused_until = 0.0
        self.wake = threading.Event()
        self.force = False
        self.focus_key: tuple | None = None
        self.focus_since = 0.0
        self.focus_pending = False
        self.last_full = 0.0
        self.last_poll = 0.0
        self.status = {"state": "starting", "app": None, "title": None, "at": None,
                       "ax_trusted": None, "note": None, "trigger": None}

    # -- session lifecycle
    def start_session(self, ts: int) -> str:
        sid = str(uuid.uuid4())
        _write("INSERT INTO sessions (id, started_at, last_seen) VALUES (?,?,?)", (sid, ts, ts))
        log(f"session started {sid[:8]}")
        return sid

    def end_session(self, reason: str) -> None:
        sid, self.session_id = self.session_id, None
        self.focus_key = None
        if not sid:
            return
        with DB_LOCK:
            d = db()
            row = d.execute("SELECT started_at, last_seen, apps FROM sessions WHERE id=?", (sid,)).fetchone()
            apps = {a.lower() for a in json.loads(row[2] or "{}")} if row else set()
            if not row or row[1] - row[0] < 5000 or (apps and apps <= LOCK_SCREEN_APPS):
                d.execute("DELETE FROM memory_chunks WHERE session_id=?", (sid,))
                d.execute("DELETE FROM sessions WHERE id=?", (sid,))
                d.commit()
                d.close()
                log(f"session discarded {sid[:8]} ({reason}, trivial)")
                return
            d.execute("UPDATE sessions SET status='ended', ended_at=last_seen WHERE id=?", (sid,))
            d.commit()
            d.close()
        log(f"session ended {sid[:8]} ({reason})")
        for hook in SESSION_END_HOOKS:
            threading.Thread(target=hook, args=(sid,), daemon=True).start()
        s = load_settings()
        if (row[1] - row[0]) >= s["min_session_minutes"] * 60_000 and s["summarize_sessions"]:
            threading.Thread(target=summarize_session_safe, args=(sid,), daemon=True).start()

    def pause(self, minutes: float | None) -> None:
        # Pausing keeps the current session open (it ends later on idle or shutdown).
        self.paused_until = time.time() + minutes * 60 if minutes else 0.0
        self.wake.set()

    def capture_now(self) -> None:
        self.force = True
        self.wake.set()

    # -- main loop
    def run(self) -> None:
        while not STOP.is_set():
            s = load_settings()
            try:
                if not s["capture_enabled"]:
                    self.status["state"] = "off"
                    self.end_session("capture off")
                elif time.time() < self.paused_until:
                    self.status["state"] = "paused"
                    self.last_poll = 0.0
                elif idle_seconds() > s["idle_seconds"]:
                    self.status["state"] = "idle"
                    self.last_poll = 0.0
                    self.end_session("idle")
                else:
                    self.poll(s)
            except Exception as e:  # keep running after a bad snapshot
                self.status.update(state="error", note=str(e))
                log(f"capture error: {e}")
            self.wake.wait(self.POLL_S)
            self.wake.clear()

    def poll(self, s: dict) -> None:
        peek = run_helper("--peek")
        app, title = peek.get("app") or "unknown", peek.get("title") or ""
        remember_bundle(app, peek.get("bundle_id"))
        self.status["ax_trusted"] = peek.get("ax_trusted")
        now = time.time()
        blocked = {b.lower() for b in s["blocked_apps"]} | SELF_APPS | SYSTEM_SKIP_APPS
        if app.lower() in blocked or re.search(r"incognito|private browsing", title, re.I):
            self.status.update(state="skipped", app=app, title=None, at=now_ms())
            self.last_poll = now
            return

        trigger = None
        key = (app, title)
        if key != self.focus_key:
            self.focus_key, self.focus_since, self.focus_pending = key, now, True
        if self.session_id is None:
            trigger = "session_start"
        elif self.force:
            trigger = "manual"
        elif self.focus_pending and now - self.focus_since >= self.SETTLE_S:
            trigger = "window_focus"
        elif now - self.last_full >= s["capture_interval_s"]:
            trigger = "idle_fallback"
        self.force = False

        if trigger:
            self.full_capture(s, trigger)
        if self.session_id:
            # Time accounting from poll gaps (capped so a stall doesn't inflate totals).
            elapsed = min(now - self.last_poll, self.POLL_S * 3) if self.last_poll else 0.0
            ts = now_ms()
            with DB_LOCK:
                d = db()
                apps = json.loads(d.execute("SELECT apps FROM sessions WHERE id=?",
                                            (self.session_id,)).fetchone()[0])
                apps[app] = round(apps.get(app, 0) + elapsed, 1)
                d.execute("UPDATE sessions SET apps=?, last_seen=? WHERE id=?",
                          (json.dumps(apps), ts, self.session_id))
                d.commit()
                d.close()
            self.status.update(state="capturing", app=app, title=title, at=ts)
        self.last_poll = now

    def full_capture(self, s: dict, trigger: str) -> None:
        text_ok = s["capture_text_all_apps"] or (self.focus_key and self.focus_key[0].lower()
                                                in {a.lower() for a in s["text_apps"]})
        snap = run_helper(*(["--ocr"] if s["ocr_fallback"] else [])) if text_ok else {}
        app, title = (self.focus_key or ("unknown", ""))
        if snap and snap.get("app") and snap["app"] != app:  # foreground changed mid-capture
            return
        self.status.update(note=snap.get("note") if snap else None, trigger=trigger)
        ts = now_ms()
        if not self.session_id:
            self.session_id = self.start_session(ts)
        text, source = (snap.get("text") or "", snap.get("source", "ax")) if snap else ("", "title_only")
        if not text.strip():
            text, source = f"{app} — {title}", "title_only"
        url = snap.get("url") if snap else None
        with DB_LOCK:
            d = db()
            cid = mem.add_chunk(d, text, app_id=app.lower(), title=title,
                                url_host=web_host(url), source=source, captured_at=ts)
            if cid:
                d.execute("UPDATE memory_chunks SET session_id=? WHERE id=?", (self.session_id, cid))
            d.execute("INSERT OR REPLACE INTO kv VALUES ('activity:last', ?)",
                      (json.dumps({"app": app, "title": title, "at": ts}),))
            d.commit()
            mem.embed_pending(d, verbose=False)
            d.close()
        self.last_full = time.time()
        self.focus_pending = False


CAPTURER = Capturer()


# --------------------------------------------------------- session insight
# Sessions are classified locally with the user's chosen model.

QUALITY = {"type": "object", "properties": {
    "focus": {"type": "integer"}, "depth": {"type": "integer"}, "consistency": {"type": "integer"},
    "recovery": {"type": "integer"}, "rationale": {"type": "string"}},
    "required": ["focus", "depth", "consistency", "recovery", "rationale"], "additionalProperties": False}

SESSION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "2-5 word name of what was worked on"},
        "summary": {"type": "string", "description": "1-3 sentences: what got done and where"},
        "project": {"type": "string", "description": "the main project, repo, product, client or topic "
                                                     "(1-4 words, as the user would name it); empty if none"},
        "state_tag": {"type": "string", "description": "activity slug, e.g. deep-work, communication, "
                                                       "research, meetings, admin, learning, break, mixed"},
        "state_name": {"type": "string", "description": "the activity in Title Case, 1-2 words"},
        "quality": QUALITY,
        "insights": {"type": "array", "items": {
            "type": "object",
            "properties": {"headline": {"type": "string"}, "implication": {"type": "string"},
                           "justification": {"type": "string"},
                           "entity_app_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["headline", "implication", "justification", "entity_app_ids"],
            "additionalProperties": False}},
    },
    "required": ["title", "summary", "project", "state_tag", "state_name", "quality", "insights"],
    "additionalProperties": False,
}

SESSION_SYSTEM = (
    "You review one work session reconstructed from screen captures on the user's own Mac. Name it "
    "(2-5 words, specific: the project or document, never 'Work session'), summarise what was actually "
    "done in 1-3 sentences (cite documents, people, sites), tag the dominant activity, and score four "
    "quality axes from 0-100: focus (staying on one thing), depth (substantive vs shallow work), "
    "consistency (steady vs fragmented) and recovery (getting back on track after interruptions), with "
    "a one-sentence rationale. Give 0-3 insights; each has a short headline, what it implies for the "
    "user, the evidence behind it, and the 1-3 apps involved. Never invent details not in the captures."
)

GENERIC_TITLE = re.compile(r"^(this|the user|user spent|focused on|working on|a session)\b", re.I)


def clean_title(title: str, fallback: str) -> str:
    t = re.sub(r"\s+", " ", title.strip().strip("\"'“”"))
    t = re.sub(r"\s+session$", "", t, flags=re.I)
    if not t or GENERIC_TITLE.match(t) or len(t.split()) > 8:
        t = fallback
    if len(t) > 40:
        cut = t.rfind(" ", 0, 40)
        t = t[:cut] if cut > 16 else t[:40]
    return t


def session_digest(sid: str, budget: int = 24000) -> str:
    with DB_LOCK:
        d = db()
        s = d.execute("SELECT started_at, last_seen, apps FROM sessions WHERE id=?", (sid,)).fetchone()
        rows = d.execute("SELECT app_id, window_title, captured_at, text FROM memory_chunks "
                         "WHERE session_id=? ORDER BY captured_at", (sid,)).fetchall()
        d.close()
    if not s:
        raise ValueError("unknown session")
    apps = json.loads(s[2])
    lines = [f"Session {fmt_time(s[0])} - {fmt_time(s[1])} ({round((s[1] - s[0]) / 60000)} min)",
             "Time per app: " + ", ".join(f"{a} {round(v / 60)}m" for a, v in
                                          sorted(apps.items(), key=lambda x: -x[1])),
             f"Window switches captured: {len(rows)}"]
    per = max(300, budget // max(1, len(rows)))
    for app, title, ts, text in rows:
        lines.append(f"\n[{fmt_time(ts)}] {app} — {title}\n{re.sub(r'\s+', ' ', text)[:per]}")
    return "\n".join(lines)[:budget]


SESSION_SCHEMA_EXTRA: dict = {}   # features add summary fields: {"name": json_schema}
SESSION_DELETE_HOOKS: list = []   # fn(session_id) after a session (and its captures) is deleted
SESSION_SUMMARY_HOOKS: list = []  # fn(session_id, summary_dict) after a summary is saved


def session_schema() -> dict:
    schema = json.loads(json.dumps(SESSION_SCHEMA))
    for k, v in SESSION_SCHEMA_EXTRA.items():
        schema["properties"][k] = v
        schema["required"].append(k)
    return schema


def summarize_session(sid: str) -> dict:
    r = provider().complete_json(SESSION_SYSTEM, session_digest(sid), session_schema())
    q = {k: max(0, min(100, int(r["quality"][k]))) for k in ("focus", "depth", "consistency", "recovery")}
    overall = round(sum(q.values()) / 4)
    top_app = (session_rows("s.id = ?", (sid,), 1) or [{"apps": [("Work", 0)]}])[0]["apps"][:1]
    title = clean_title(r["title"], f"{r['state_name']} in {top_app[0][0] if top_app else 'apps'}")
    _write("UPDATE sessions SET title=?, summary=?, project=?, state_tag=?, state_name=?, focus_score=?, quality=?, "
           "insights=?, status='summarized' WHERE id=?",
           (title, r["summary"], (r.get("project") or "").strip()[:60] or None, r["state_tag"], r["state_name"], overall,
            json.dumps({**q, "rationale": r["quality"]["rationale"]}), json.dumps(r["insights"][:3]), sid))
    for hook in SESSION_SUMMARY_HOOKS:
        try:
            hook(sid, r)
        except Exception as e:
            log(f"summary hook failed: {e}")
    return {**r, "title": title, "focus_score": overall}


def summarize_session_safe(sid: str) -> None:
    try:
        summarize_session(sid)
        log(f"session summarized {sid[:8]}")
    except ai.ProviderError as e:
        log(f"session summary skipped: {e}")
    except Exception as e:
        log(f"session summary failed: {e}")


# ---------------------------------------------------------------- queries

def fmt_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%a %H:%M")


def session_rows(where: str = "1=1", params: tuple = (), limit: int = 200) -> list[dict]:
    with DB_LOCK:
        d = db()
        rows = d.execute(
            "SELECT s.id, s.started_at, coalesce(s.ended_at, s.last_seen), s.apps, s.title, s.summary,"
            " s.state_tag, s.focus_score, s.insights, s.status, s.state_name, s.quality, s.note, s.project,"
            " (SELECT count(*) FROM memory_chunks c WHERE c.session_id = s.id)"
            f" FROM sessions s WHERE {where} ORDER BY s.started_at DESC LIMIT ?",
            (*params, limit)).fetchall()
        d.close()
    out = []
    for r in rows:
        apps = json.loads(r[3])
        out.append({"id": r[0], "started_at": r[1], "ended_at": r[2],
                    "apps": sorted(apps.items(), key=lambda x: -x[1]), "title": r[4],
                    "summary": r[5], "state_tag": r[6], "focus_score": r[7],
                    "insights": json.loads(r[8] or "[]"), "status": r[9], "state_name": r[10],
                    "quality": json.loads(r[11]) if r[11] else None, "note": r[12], "project": r[13],
                    "captures": r[14],
                    "active": r[0] == CAPTURER.session_id})
    return out


def day_bounds(date_str: str | None) -> tuple[int, int, str]:
    day = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000), \
        start.strftime("%Y-%m-%d")


def today_activity(date_str: str | None = None) -> dict:
    lo, hi, label = day_bounds(date_str)
    sessions = session_rows("s.started_at >= ? AND s.started_at < ?", (lo, hi))
    interval = load_settings()["capture_interval_s"] * 1000
    with DB_LOCK:
        d = db()
        caps = d.execute("SELECT app_id, captured_at FROM memory_chunks WHERE captured_at >= ? "
                         "AND captured_at < ? ORDER BY captured_at", (lo, hi)).fetchall()
        d.close()
    # Merge consecutive captures of the same app into timeline segments.
    segments: list[dict] = []
    for app, ts in caps:
        if segments and segments[-1]["app"] == app and ts - segments[-1]["end"] <= 3 * interval:
            segments[-1]["end"] = ts + interval
        else:
            segments.append({"app": app, "start": ts, "end": ts + interval})
    totals: dict[str, int] = {}
    for s in sessions:
        for app, secs in s["apps"]:
            totals[app] = totals.get(app, 0) + secs
    return {"date": label, "sessions": sessions, "segments": segments,
            "app_totals": sorted(totals.items(), key=lambda x: -x[1]),
            "tracked_seconds": sum(totals.values())}


# ------------------------------------------------------------- Ask agent
# Agent limits: search returns up to 36 results, recent context covers 24h / 300 captures;
# a wrap-up nudge at round 12 and a forced answer at round 14 (Quick Ask: 5 rounds).

MAIN_HISTORY_MESSAGES = 12
QUICK_HISTORY_TURNS = 8
BIO_MAX, MEMORY_MAX = 6000, 12000
MEMORY_SKELETON = "## Preferences\n\n## Corrections\n\n## Standing notes\n"


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def _budget(lines: list[str], limit_chars: int, total: int) -> str:
    out, used = [], 0
    for line in lines:
        if used + len(line) > limit_chars:
            out.append(f"[{total - len(out)} more captures omitted: character budget reached]")
            break
        out.append(line)
        used += len(line)
    return "\n".join(out)


def tool_search_memory(args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "No search query provided."
    with DB_LOCK:
        d = db()
        results, _, notes = mem.search(d, query, args.get("since_hours"), args.get("app_id"),
                                       int(args.get("limit") or 36))
        d.close()
    if not results:
        return "No matching screen captures found."
    lines = [f"- [{fmt_time(r['captured_at'])}] {r['app_id']}"
             f"{' · ' + r['url_host'] if r['url_host'] else ''} — {r['window_title']}\n  "
             f"{_clip(r['passage'] + ' ' + (r['next_passage'] or ''), 600)}" for r in results]
    return "\n".join(notes) + _budget(lines, 30000, len(lines))


def tool_recent_context(args: dict) -> str:
    hours = float(args.get("since_hours") or 24)
    limit = int(args.get("limit") or 300)
    params = [now_ms() - int(hours * 3_600_000)]
    app_sql = ""
    if args.get("app_id"):
        app_sql, params = " AND (app_id LIKE ? OR url_host LIKE ?)", params + [f"%{args['app_id']}%"] * 2
    with DB_LOCK:
        d = db()
        rows = d.execute("SELECT app_id, window_title, captured_at, text FROM memory_chunks "
                         f"WHERE captured_at >= ?{app_sql} ORDER BY captured_at DESC LIMIT ?",
                         (*params, limit)).fetchall()
        d.close()
    if not rows:
        return "No recent screen captures in this window."
    lines = [f"[{fmt_time(ts)}] {app} — {title}\n{_clip(text, 2500)}" for app, title, ts, text in rows]
    return _budget(lines, 40000, len(lines))


def tool_today_activity(args: dict) -> str:
    t = today_activity(None)
    lo, _, _ = day_bounds(None)
    with DB_LOCK:
        d = db()
        caps = d.execute("SELECT app_id, window_title, captured_at FROM memory_chunks WHERE captured_at >= ? "
                         "ORDER BY captured_at ASC LIMIT 400", (lo,)).fetchall()
        d.close()
    if not caps:
        return "No screen captures recorded today yet."
    lines = [f"Tracked {round(t['tracked_seconds'] / 60)} min today. Apps: "
             + ", ".join(f"{a} {round(s / 60)}m" for a, s in t["app_totals"][:10])]
    for s in reversed(t["sessions"]):
        lines.append(f"Session {s['id'][:8]} {fmt_time(s['started_at'])}–{fmt_time(s['ended_at'])}: "
                     f"{s['title'] or 'untitled'} — {s['summary'] or ''}")
    lines.append("Captures (chronological):")
    lines += [f"{fmt_time(ts)} {app} — {title}" for app, title, ts in caps]
    return _budget(lines, 30000, len(lines))


def _task_row(tid: str):
    with DB_LOCK:
        d = db()
        row = d.execute("SELECT id, title, progress, logs FROM tasks WHERE id LIKE ?", (tid + "%",)).fetchone()
        d.close()
    return row


def tool_list_tasks(args: dict) -> str:
    with DB_LOCK:
        d = db()
        rows = d.execute("SELECT id, title, progress, logs FROM tasks WHERE progress < 100 "
                         "ORDER BY updated_at DESC").fetchall()
        d.close()
    if not rows:
        return "No active tasks."
    out = ["Active tasks:"]
    for tid, title, progress, logs in rows:
        last = json.loads(logs or "[]")[-1:]
        out.append(f"- {tid[:8]} {title} ({progress}%)" + (f" — last log: {_clip(last[0]['text'], 160)}" if last else ""))
    return "\n".join(out)


def create_task(title: str, log: str | None = None, progress: int | None = None) -> dict:
    title = (title or "").strip()[:120]
    if not title:
        raise ValueError("Task title is required.")
    tid, ts = str(uuid.uuid4()), now_ms()
    logs = [{"at": ts, "text": log}] if log else []
    p = max(0, min(100, int(progress or 0)))
    with DB_LOCK:
        d = db()
        d.execute("INSERT INTO tasks (id, title, status, notes, created_at, updated_at, progress, logs) "
                  "VALUES (?,?,?,?,?,?,?,?)", (tid, title, "done" if p >= 100 else "open", "", ts, ts, p, json.dumps(logs)))
        d.commit()
        d.close()
    return {"id": tid, "title": title, "progress": p}


def update_task(task_id: str, progress: int | None = None, log: str | None = None) -> dict:
    if progress is None and not log:
        raise ValueError("progress or log is required.")
    row = _task_row(task_id)
    if not row:
        raise ValueError("No task with that id.")
    logs = json.loads(row[3] or "[]")
    if log:
        logs.append({"at": now_ms(), "text": log})
    p = row[2] if progress is None else max(0, min(100, int(progress)))
    _write("UPDATE tasks SET progress=?, status=?, logs=?, updated_at=? WHERE id=?",
           (p, "done" if p >= 100 else "open", json.dumps(logs), now_ms(), row[0]))
    return {"id": row[0], "title": row[1], "progress": p}


def tool_create_task(args: dict) -> str:
    t = create_task(args.get("title"), args.get("log"), args.get("progress"))
    return f"Created task: {t['title']} ({t['id'][:8]})"


def tool_update_task(args: dict) -> str:
    t = update_task(args["task_id"], args.get("progress"), args.get("log"))
    return f"Updated task: {t['title']} ({t['progress']}%)"


def tool_read_bio(args: dict) -> str:
    bio = kv_get("bio", "")
    return f"Current bio:\n{bio}" if bio else "No bio saved yet."


def tool_update_bio(args: dict) -> str:
    bio = (args.get("bio") or "")[:BIO_MAX]
    kv_set("bio", bio)
    return f"Bio updated ({len(bio)} chars)."


def tool_read_memory(args: dict) -> str:
    doc = kv_get("memory_doc", "")
    return f"Current lasting memory:\n{doc}" if doc else \
        f"No lasting memory saved yet. Suggested structure:\n{MEMORY_SKELETON}"


def tool_update_memory(args: dict) -> str:
    doc = (args.get("memory") or "")[:MEMORY_MAX]
    kv_set("memory_doc", doc)
    return f"Memory updated ({len(doc)} chars)."


def tool_revise_session(args: dict) -> str:
    fields = {k: args.get(k) for k in ("session_name", "summary", "state_tag", "focus_score", "insights")}
    if all(v is None for v in fields.values()):
        return "Provide at least one revision field (session_name, summary, state_tag, focus_score, insights)."
    sets, params = [], []
    for col, key in (("title", "session_name"), ("summary", "summary"), ("state_tag", "state_tag")):
        if fields[key] is not None:
            sets.append(f"{col}=?")
            params.append(fields[key])
    if fields["focus_score"] is not None:
        sets.append("focus_score=?")
        params.append(max(0, min(100, int(fields["focus_score"]))))
    if fields["insights"] is not None:
        sets.append("insights=?")
        params.append(json.dumps(fields["insights"][:3]))
    n = _write(f"UPDATE sessions SET {', '.join(sets)} WHERE id LIKE ?", (*params, args["session_id"] + "%"))
    if n:  # keep search and the knowledge graph in step with the corrected report
        row = session_rows("s.id LIKE ?", (args["session_id"] + "%",), 1)
        if row:
            for hook in SESSION_SUMMARY_HOOKS:
                try:
                    hook(row[0]["id"], {"title": row[0]["title"], "summary": row[0]["summary"],
                                        "project": row[0].get("project"), "insights": row[0]["insights"]})
                except Exception as e:
                    log(f"summary hook failed: {e}")
    return "Session report revised." if n else "Error: unknown session id."


def tool_foreground_text(args: dict) -> str:
    last = kv_get("activity:last") or {}
    with DB_LOCK:
        d = db()
        row = d.execute("SELECT app_id, window_title, captured_at, text FROM memory_chunks "
                        "WHERE source != 'title_only' ORDER BY captured_at DESC LIMIT 1").fetchone()
        d.close()
    if not row:
        return "Could not read foreground window text (nothing captured yet)."
    return (f"Foreground window: {last.get('app') or row[0]} — {last.get('title') or row[1]}\n"
            f"Text captured {fmt_time(row[2])}:\n{_clip(row[3], 12000)}")


NULLABLE_INT = {"type": ["integer", "null"]}
NULLABLE_STR = {"type": ["string", "null"]}


def _schema(props: dict, required: list[str] | None = None) -> dict:
    # Strict-style schema: every param listed in `required`, optional ones nullable.
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


AGENT_TOOLS = [
    ai.Tool("search_memory",
            "Search everything captured from the user's screen (window text, titles, apps, sites) with "
            "hybrid keyword + semantic search. Use for people, chats, documents, topics or anything seen "
            "before. Make one focused call; keep app_id and since_hours null unless the user clearly "
            "limited the app or time. Don't retry with cosmetic variations.",
            _schema({"query": {"type": "string"}, "since_hours": NULLABLE_INT,
                     "app_id": NULLABLE_STR, "limit": NULLABLE_INT}), tool_search_memory),
    ai.Tool("get_recent_context",
            "Full text of captures in a recent window, newest first (default 24 hours, up to 300 "
            "captures). Use for 'what was I just doing/reading' or when search is too narrow.",
            _schema({"since_hours": NULLABLE_INT, "app_id": NULLABLE_STR, "limit": NULLABLE_INT}),
            tool_recent_context),
    ai.Tool("get_today_activity",
            "Today's work sessions (titles, summaries), time per app and every capture in order.",
            _schema({}), tool_today_activity),
    ai.Tool("list_tasks", "List the user's active tasks with progress.", _schema({}), tool_list_tasks),
    ai.Tool("create_task", "Create a task. Title up to 120 characters; optional first log entry and "
            "progress 0-100.", _schema({"title": {"type": "string"}, "log": NULLABLE_STR,
                                        "progress": NULLABLE_INT}), tool_create_task),
    ai.Tool("update_task", "Update a task's progress (0-100, null = unchanged) and/or append a log entry. "
            "Needs progress or log.", _schema({"task_id": {"type": "string"}, "progress": NULLABLE_INT,
                                               "log": NULLABLE_STR}), tool_update_task),
    ai.Tool("read_user_bio", "Read the user's bio. It is already in your context; call only before editing.",
            _schema({}), tool_read_bio),
    ai.Tool("update_user_bio", "Replace the user's bio (full text, up to 6000 characters; empty clears).",
            _schema({"bio": {"type": "string"}, "note": NULLABLE_STR}), tool_update_bio),
    ai.Tool("read_memory", "Read the lasting memory document (markdown with Preferences, Corrections and "
            "Standing notes sections).", _schema({}), tool_read_memory),
    ai.Tool("update_memory", "Replace the lasting memory document (full markdown, up to 12000 characters). "
            "Save durable preferences, corrections and standing notes only. Don't announce the save "
            "unless asked.", _schema({"memory": {"type": "string"}, "note": NULLABLE_STR}), tool_update_memory),
    ai.Tool("revise_session", "Correct a work-session report when the user says it's wrong. Provide at "
            "least one field.", _schema({
                "session_id": {"type": "string"}, "session_name": NULLABLE_STR, "summary": NULLABLE_STR,
                "state_tag": NULLABLE_STR, "focus_score": NULLABLE_INT,
                "insights": {"type": ["array", "null"], "items": {"type": "object", "properties": {
                    "headline": {"type": "string"}, "implication": {"type": "string"}, "why": {"type": "string"}},
                    "required": ["headline", "implication", "why"], "additionalProperties": False}},
                "reason": NULLABLE_STR}), tool_revise_session),
]

QUICK_TOOLS = AGENT_TOOLS + [
    ai.Tool("read_foreground_text", "Read the text of the window the user was in when they opened Quick Ask.",
            _schema({}), tool_foreground_text),
]

ASK_SYSTEM = """You are Glint, a private assistant that lives on the user's Mac. Glint \
captures the text of the windows the user works in and keeps it on this device, so you can answer \
questions about anything they have seen or done.

How to work:
- Start from the opening context. When it isn't enough, look things up with the tools before \
answering — never ask whether you should search. One focused search usually beats several broad ones.
- Answer with specifics: what it was, when, and in which app or site. Quote short phrases from \
captures when they settle the question.
- If memory doesn't contain the answer, say so plainly and suggest what to search instead.
- Keep answers short unless the user asks for detail. Use Markdown lists for multiple items.
- For questions about a person, project, document or topic across time, memory_graph shows what it
  connects to and which sessions mention it.
- Save to lasting memory only durable preferences, corrections and standing notes."""

QUICK_SYSTEM = ASK_SYSTEM + """

You are answering in Quick Ask, a small floating panel. The user is usually asking about the window \
they were just in: use read_foreground_text when the question is about their screen. Answer in a few \
sentences."""

QUICK_ACTIONS = {
    "/task": "Create a task from what follows (and the current screen if relevant) with create_task, "
             "then confirm briefly.",
    "/log": "Find the matching task with list_tasks and add a progress log with update_task describing "
            "what follows (and what's on screen). Confirm briefly.",
    "/summarize": "Give a tight 2-4 sentence summary of what the user is working on right now, from "
                  "the foreground window and recent captures.",
    "/find": "Search recent captures for what follows with search_memory and answer with what you found: "
             "what, when, which app.",
}


def apply_quick_action(message: str) -> str:
    cmd, _, rest = message.partition(" ")
    hint = QUICK_ACTIONS.get(cmd.lower())
    return f"{rest.strip() or '(no details)'}\n\n[Quick action {cmd}: {hint}]" if hint else message


def ask_context() -> str:
    parts = [f"[Date] {datetime.now().strftime('%A %Y-%m-%d %H:%M')}"]
    bio, doc = kv_get("bio", ""), kv_get("memory_doc", "")
    if bio:
        parts.append(f"[Bio]\n{bio}")
    if doc:
        parts.append(f"[Lasting memory]\n{doc}")
    if CAPTURER.session_id:
        cur = session_rows("s.id = ?", (CAPTURER.session_id,), 1)
        if cur:
            parts.append(f"[Active session] started {fmt_time(cur[0]['started_at'])}, apps: "
                         + ", ".join(a for a, _ in cur[0]["apps"][:5]))
    with DB_LOCK:
        d = db()
        recent = d.execute("SELECT app_id, window_title, captured_at, text FROM memory_chunks "
                           "WHERE source != 'title_only' ORDER BY captured_at DESC LIMIT 3").fetchall()
        d.close()
    if recent:
        parts.append("[Recent captures]\n" + "\n".join(
            f"({fmt_time(ts)}) {app} — {title}: {_clip(text, 1500)}" for app, title, ts, text in recent))
    for hook in CONTEXT_HOOKS:
        try:
            extra = hook()
            if extra:
                parts.append(extra)
        except Exception as e:
            log(f"context hook failed: {e}")
    t = today_activity()
    if t["sessions"]:
        parts.append("[Today]\n" + "\n".join(
            f"- {fmt_time(s['started_at'])} {s['title'] or 'Session'} "
            f"({', '.join(a for a, _ in s['apps'][:3])}){': ' + s['summary'] if s['summary'] else ''}"
            for s in reversed(t["sessions"][:10])))
    return "\n\n".join(parts)


def thread_history(thread_id: str, scope: str) -> list[dict]:
    limit = QUICK_HISTORY_TURNS * 2 if scope == "quick" else MAIN_HISTORY_MESSAGES
    with DB_LOCK:
        d = db()
        rows = d.execute("SELECT role, content FROM chat_messages WHERE thread_id=? ORDER BY id DESC LIMIT ?",
                         (thread_id, limit)).fetchall()
        d.close()
    rows = list(reversed(rows))
    while rows and rows[0][0] != "user":  # history must start with a user turn
        rows.pop(0)
    return [{"role": r, "content": c} for r, c in rows]


def save_message(thread_id: str, role: str, content: str, tools: list | None = None) -> None:
    ts = now_ms()
    with DB_LOCK:
        d = db()
        d.execute("INSERT INTO chat_messages (thread_id, role, content, tools, created_at) "
                  "VALUES (?,?,?,?,?)", (thread_id, role, content, json.dumps(tools or []), ts))
        d.execute("UPDATE chat_threads SET updated_at=? WHERE id=?", (ts, thread_id))
        d.commit()
        d.close()


def ensure_thread(thread_id: str | None, first_message: str) -> tuple[str, bool]:
    if thread_id:
        return thread_id, False
    tid, ts = str(uuid.uuid4()), now_ms()
    title = first_message.strip()[:52]
    _write("INSERT INTO chat_threads (id, title, created_at, updated_at) VALUES (?,?,?,?)", (tid, title, ts, ts))
    return tid, True


def label_thread(thread_id: str, question: str, answer: str, llm) -> None:
    """Replace the provisional title with a short AI label (max 5 words / 40 chars) unless renamed."""
    try:
        label = llm.complete_text(
            "Write a label of at most 5 words for this conversation. Reply with the label only, no quotes.",
            f"Question: {question[:800]}\nAnswer: {answer[:800]}", max_tokens=200)
        label = re.sub(r'^["\'\s]+|["\'\s.]+$', "", label)[:40]
        if label:
            _write("UPDATE chat_threads SET title=? WHERE id=? AND coalesce(renamed, 0) = 0", (label, thread_id))
    except Exception as e:
        log(f"thread label skipped: {e}")


def ask(thread_id: str | None, message: str, emit, scope: str = "main") -> str:
    llm = provider()  # fail before creating a thread if no key/model is configured
    thread_id, is_new = ensure_thread(thread_id, message)
    emit("thread", {"id": thread_id})
    prompt = apply_quick_action(message) if message.startswith("/") else message
    history = thread_history(thread_id, scope) + [{"role": "user", "content": prompt}]
    save_message(thread_id, "user", message)
    tools_used: list[dict] = []

    def relay(kind, payload):
        if kind == "tool":
            tools_used.append(payload)
        elif kind not in ("text", "tool_result", "thread", "notice"):
            tools_used.append({"event": kind, "payload": payload})  # e.g. charts, kept for reload
        emit(kind, payload)

    TOOL_CTX.emit = relay

    quick = scope == "quick"
    TOOL_CTX.scope = scope
    system = f"{QUICK_SYSTEM if quick else ASK_SYSTEM}\n\n<opening_context>\n{ask_context()}\n</opening_context>"
    answer = llm.run(system, history, QUICK_TOOLS if quick else AGENT_TOOLS, relay,
                     max_steps=5 if quick else 14, wrap_at=4 if quick else 12,
                     web_search=load_settings()["web_search"]) or "(no answer)"
    save_message(thread_id, "assistant", answer, tools_used)
    if is_new:
        threading.Thread(target=label_thread, args=(thread_id, message, answer, llm), daemon=True).start()
    return answer


# ------------------------------------------------------------- Dictation
# Dictation: speech → transcript (cloud Whisper, or Apple's on-device
# recognizer in the shell when no OpenAI key) → "polish" pass with a per-context style and the user's
# dictionary → pasted into the focused app. Any polish failure inserts the raw transcript.

DICT_MAX, DICT_VALUE_MAX, DICT_ALIAS_MAX = 500, 60, 80
STYLE_CATEGORIES = {
    "email": {"mail", "outlook", "microsoft outlook", "superhuman", "spark", "airmail", "mimestream"},
    "work": {"slack", "microsoft teams", "teams", "notion", "linear", "jira", "asana", "confluence",
             "google docs", "zoom", "figma", "code", "cursor", "xcode"},
    "personal": {"messages", "whatsapp", "telegram", "discord", "signal", "facebook messenger", "instagram"},
}
EMAIL_HOSTS = ("mail.google.com", "outlook.live.com", "outlook.office.com")
STYLE_RULES = {
    "very_casual": "very casual: lowercase is fine, minimal punctuation, contractions, no sign-offs",
    "casual": "casual: natural sentence case, light punctuation, friendly and brief",
    "formal": "formal: complete sentences, proper capitalisation and punctuation, polite tone",
    "excited": "upbeat: energetic and warm, exclamation marks where natural (don't overdo it)",
}


def style_category(app: str, url_host: str | None = None) -> str:
    a = (app or "").lower()
    if url_host and url_host in EMAIL_HOSTS:
        return "email"
    for cat, apps in STYLE_CATEGORIES.items():
        if a in apps:
            return cat
    return "other"


def dictionary() -> list[dict]:
    with DB_LOCK:
        d = db()
        rows = d.execute("SELECT id, value, alias, source FROM dictation_dictionary ORDER BY updated_at DESC").fetchall()
        d.close()
    return [{"id": i, "value": v, "alias": a, "source": s} for i, v, a, s in rows]


def upsert_dictionary(value: str, alias: str | None, source: str = "manual") -> None:
    value = (value or "").strip()[:DICT_VALUE_MAX]
    alias = ((alias or "").strip()[:DICT_ALIAS_MAX]) or None
    if not value:
        raise ValueError("A dictionary word is required.")
    with DB_LOCK:
        d = db()
        n = d.execute("SELECT count(*) FROM dictation_dictionary").fetchone()[0]
        exists = d.execute("SELECT source FROM dictation_dictionary WHERE value=? COLLATE NOCASE", (value,)).fetchone()
        if not exists and n >= DICT_MAX:
            d.close()
            raise ValueError("Dictionary is full (500 entries).")
        # Manual entries are never downgraded to auto-learned ones.
        new_source = "manual" if (exists and exists[0] == "manual") or source == "manual" else "auto"
        d.execute("INSERT INTO dictation_dictionary (value, alias, source, updated_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(value) DO UPDATE SET alias=coalesce(excluded.alias, alias), source=?, updated_at=?",
                  (value, alias, new_source, now_ms(), new_source, now_ms()))
        d.commit()
        d.close()


def transcribe(audio: bytes, filename: str) -> str | None:
    """Cloud transcription with OpenAI Whisper; None means the shell should transcribe on-device."""
    key = api_keys().get("openai")
    if not key:
        return None
    import openai
    terms = ", ".join(e["value"] for e in dictionary()[:80])
    try:
        resp = openai.OpenAI(api_key=key).audio.transcriptions.create(
            model=load_settings()["transcribe_model"], file=(filename, audio),
            prompt=f"Vocabulary: {terms}" if terms else openai.NOT_GIVEN)
    except openai.APIError as e:
        raise ai.ProviderError(f"Transcription failed: {e}") from e
    return (resp.text or "").strip()


POLISH_SYSTEM = """You clean up dictated speech so it can be inserted where the user is typing.
Rules:
- Keep the user's meaning, wording and language. Remove filler words (um, uh, like, you know), \
false starts and repeated words; fix punctuation, capitalisation and obvious transcription errors.
- Apply self-corrections ("no wait", "I mean", "scratch that") by keeping only the corrected version.
- Format spoken lists as lists and spoken "new line"/"new paragraph" as breaks.
- Use the dictionary spellings exactly; when a word sounds like an alias, write its value.
- Never answer, comment on, or add to the text. Output only the cleaned text."""


def polish(text: str, app: str, url_host: str | None = None) -> dict:
    s = load_settings()
    cat = style_category(app, url_host)
    if not text.strip() or not s["dictation_polish"]:
        return {"text": text.strip(), "polished": False, "category": cat}
    style = s["dictation_styles"].get(cat, "formal")
    terms = "\n".join(f"- {e['value']}" + (f" (heard as: {e['alias']})" if e["alias"] else "")
                      for e in dictionary()[:200])
    prompt = (f"App: {app or 'unknown'} ({cat}). Style: {STYLE_RULES.get(style, STYLE_RULES['formal'])}.\n"
              + (f"Dictionary:\n{terms}\n" if terms else "") + f"\nDictated text:\n{text}")
    try:
        out = fast_provider().complete_text(POLISH_SYSTEM, prompt, max_tokens=4000)
        return {"text": out or text.strip(), "polished": bool(out), "category": cat}
    except Exception as e:  # never lose the user's words
        log(f"polish skipped: {e}")
        return {"text": text.strip(), "polished": False, "category": cat}


def _dictation(b):
    import base64
    audio = base64.b64decode(b.get("audio_b64") or "")
    if not audio:
        raise ValueError("No audio received.")
    if len(audio) > 100 * 1024 * 1024:
        raise ValueError("Recording too long.")
    raw = transcribe(audio, b.get("filename") or "dictation.m4a")
    if raw is None:
        return {"needs_local": True}
    return {**polish(raw, b.get("app", ""), b.get("url_host")), "raw": raw}


# ---------------------------------------------------------------- browse
# Session browser (search, filters, projects, related sessions), day timeline, app/site icons.

ICON_DIR = APP_SUPPORT / "icons"


def _safe_name(s: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "_", s.lower())[:80] or "_"


def app_icon_png(name: str) -> bytes | None:
    """PNG icon for an app seen in memory, found via Spotlight and rendered by the capture helper."""
    name = (name or "").strip()
    if not name:
        return None
    path = ICON_DIR / "apps" / f"{_safe_name(name)}.png"
    if path.exists():
        return path.read_bytes() or None
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = name.replace('"', "").replace("\\", "")
    if not APP_BUNDLES:
        APP_BUNDLES.update(kv_get("app_bundles", {}))
    bid = next((b for a, b in APP_BUNDLES.items() if a.lower() == clean.lower()), None)
    found = []
    if bid and re.fullmatch(r"[A-Za-z0-9.-]+", bid):
        found = subprocess.run(["mdfind", f'kMDItemCFBundleIdentifier == "{bid}"'],
                               capture_output=True, text=True, timeout=5).stdout.split("\n")
    if not any(found):
        found = subprocess.run(["mdfind", f'kMDItemContentType == "com.apple.application-bundle" && '
                                          f'(kMDItemFSName == "{clean}.app"c || kMDItemDisplayName == "{clean}"c)'],
                               capture_output=True, text=True, timeout=5).stdout.split("\n")
    bundle = next((p for p in found if p.startswith(("/Applications", "/System/Applications"))), None) \
        or next((p for p in found if p.endswith(".app")), None)
    if bundle:
        subprocess.run([str(CAPTURE_BIN), "--icon", bundle, str(path)], capture_output=True, timeout=10)
    if not path.exists():
        path.write_bytes(b"")  # negative cache: the UI falls back to a letter avatar
    return path.read_bytes() or None


def site_icon(host: str) -> tuple[bytes, str] | None:
    """Favicon for a site the user has visited (never for arbitrary hosts)."""
    import urllib.request
    host = (host or "").lower().strip()
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", host) or not load_settings()["site_icons"]:
        return None
    with DB_LOCK:
        d = db()
        known = d.execute("SELECT 1 FROM memory_chunks WHERE url_host=? LIMIT 1", (host,)).fetchone()
        d.close()
    if not known:
        return None
    path = ICON_DIR / "sites" / _safe_name(host)
    if path.exists():
        data = path.read_bytes()
        return (data, "image/png" if data[:4] == b"\x89PNG" else "image/x-icon") if data else None
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b""
    for url in (f"https://{host}/favicon.ico", f"https://{host}/apple-touch-icon.png"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Glint/0.1"})
            with urllib.request.urlopen(req, timeout=4) as r:
                body = r.read(200_000)
                if body and r.headers.get_content_type().startswith("image"):
                    data = body
                    break
        except Exception:
            continue
    path.write_bytes(data)
    return (data, "image/png" if data[:4] == b"\x89PNG" else "image/x-icon") if data else None


def _session_hosts(ids: list[str]) -> dict[str, list]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with DB_LOCK:
        d = db()
        rows = d.execute(f"SELECT session_id, url_host, count(*) FROM memory_chunks WHERE session_id IN ({marks})"
                         " AND url_host LIKE '%.%' GROUP BY 1, 2 ORDER BY 3 DESC", ids).fetchall()
        d.close()
    out: dict[str, list] = {}
    for sid, host, n in rows:
        out.setdefault(sid, []).append([host, n])
    return out


def with_hosts(sessions: list[dict]) -> list[dict]:
    hosts = _session_hosts([s["id"] for s in sessions])
    for s in sessions:
        s["hosts"] = hosts.get(s["id"], [])[:8]
    return sessions


def search_sessions(q: dict) -> dict:
    where, params = ["1=1"], []
    if q.get("days"):
        where.append("s.started_at >= ?")
        params.append(now_ms() - int(float(q["days"]) * 86_400_000))
    if q.get("project"):
        where.append("s.project = ?")
        params.append(q["project"])
    if q.get("app"):
        where.append("s.apps LIKE ?")
        params.append(f'%"{q["app"]}"%')
    rows = session_rows(" AND ".join(where), tuple(params), limit=500)
    query = (q.get("query") or "").strip().lower()
    if query:
        with DB_LOCK:
            d = db()
            hits, _, _ = mem.search(d, query, limit=48)
            sid_rank: dict[str, int] = {}
            for i, h in enumerate(hits):
                sid = d.execute("SELECT session_id FROM memory_chunks WHERE id=?", (h["chunk_id"],)).fetchone()
                if sid and sid[0] and sid[0] not in sid_rank:
                    sid_rank[sid[0]] = i
            d.close()
        terms = [t for t in re.findall(r"\w+", query) if t not in mem.STOPWORDS] or [query]

        def score(s: dict) -> float:
            text_hi = f"{s['title'] or ''} {s.get('project') or ''}".lower()
            text_lo = f"{s['summary'] or ''} {' '.join(a for a, _ in s['apps'])} {s['state_name'] or ''} " \
                      f"{' '.join(i.get('headline', '') for i in s['insights'])}".lower()
            sc = sum(3 for t in terms if t in text_hi) + sum(1.5 for t in terms if t in text_lo)
            if s["id"] in sid_rank:
                sc += 4 / (1 + sid_rank[s["id"]] / 6)
            return sc

        scored = [(score(s), s) for s in rows]
        rows = [s for sc, s in sorted(scored, key=lambda x: (-x[0], -x[1]["started_at"])) if sc > 0]
    limit = int(q.get("limit") or 100)
    return {"sessions": with_hosts(rows[:limit]), "total": len(rows)}


def session_facets(q: dict) -> dict:
    with DB_LOCK:
        d = db()
        projects = d.execute("SELECT project, count(*) FROM sessions WHERE coalesce(project, '') != '' "
                             "GROUP BY 1 ORDER BY 2 DESC").fetchall()
        app_rows = d.execute("SELECT apps FROM sessions").fetchall()
        d.close()
    apps: dict[str, float] = {}
    for (raw,) in app_rows:
        for a, secs in json.loads(raw or "{}").items():
            apps[a] = apps.get(a, 0) + secs
    return {"projects": projects, "apps": sorted(apps, key=lambda a: -apps[a])}


def related_sessions(s: dict, n: int = 5) -> list[dict]:
    if s.get("project"):
        rows = session_rows("s.project = ? AND s.id != ?", (s["project"], s["id"]), n)
        if rows:
            return rows
    top = s["apps"][0][0] if s["apps"] else None
    return session_rows("s.apps LIKE ? AND s.id != ?", (f'%"{top}"%', s["id"]), n) if top else []


def day_timeline(q: dict) -> dict:
    lo, hi, label = day_bounds(q.get("date"))
    gap = 3 * load_settings()["capture_interval_s"] * 1000
    with DB_LOCK:
        d = db()
        caps = d.execute("SELECT id, app_id, window_title, url_host, captured_at, session_id, source, char_count "
                         "FROM memory_chunks WHERE captured_at >= ? AND captured_at < ? ORDER BY captured_at",
                         (lo, hi)).fetchall()
        d.close()
    items: list[dict] = []
    for cid, app, title, host, ts, sid, source, chars in caps:
        last = items[-1] if items else None
        if last and last["app"] == app and last["title"] == title and ts - last["end"] <= gap:
            last["end"], last["chunk_id"], last["captures"] = ts, cid, last["captures"] + 1
            last["chars"] = max(last["chars"], chars or 0)
        else:
            items.append({"start": ts, "end": ts, "app": app, "title": title, "host": host, "chunk_id": cid,
                          "session_id": sid, "source": source, "captures": 1, "chars": chars or 0})
    return {"date": label, "items": items}


def _capture_text(q: dict) -> dict:
    with DB_LOCK:
        d = db()
        row = d.execute("SELECT app_id, window_title, url_host, captured_at, source, text FROM memory_chunks WHERE id=?",
                        (q["id"],)).fetchone()
        d.close()
    if not row:
        raise ValueError("unknown capture")
    return dict(zip(("app", "title", "host", "at", "source", "text"), row))


def tool_read_session(args: dict) -> str:
    sid = args["session_id"].strip()
    with DB_LOCK:
        d = db()
        row = d.execute("SELECT id FROM sessions WHERE id LIKE ?", (sid + "%",)).fetchone()
        d.close()
    if not row:
        return "Error: no session with that id."
    s = session_rows("s.id = ?", (row[0],), 1)[0]
    head = (f"Session {s['id'][:8]}: {s['title'] or 'untitled'} · project: {s.get('project') or '-'}\n"
            f"Summary: {s['summary'] or '-'}\n")
    return head + session_digest(row[0], budget=20000)


_READ_SESSION = (ai.Tool(
    "read_session", "Read one work session in full: its report plus every capture (app, window, text). Use "
    "when the user asks about a specific session.",
    {"type": "object", "properties": {"session_id": {"type": "string", "description": "Session id or its first 8 characters"}},
     "required": ["session_id"], "additionalProperties": False}, tool_read_session))
AGENT_TOOLS.append(_READ_SESSION)
QUICK_TOOLS.append(_READ_SESSION)


# -------------------------------------------------------------- features
# Plug-in modules in engine/features/*.py. Each may define register() and use:
#   DEFAULT_SETTINGS.update({...})          new settings (call at import time)
#   GET_ROUTES / POST_ROUTES[path] = fn      JSON API routes (fn(query_or_body) -> dict/list)
#   register_tool(ai.Tool(...), quick=True) agent tools for Ask (and Quick Ask)
#   CONTEXT_HOOKS.append(fn)                extra opening-context text for Ask: fn() -> str | None
#   STARTUP_HOOKS.append(fn)                run once after the engine starts
#   SESSION_END_HOOKS.append(fn)            fn(session_id) after a work session ends
#   emit_event(kind, payload)               stream a custom event (e.g. "visual") to the Ask UI

CONTEXT_HOOKS: list = []
STARTUP_HOOKS: list = []
SESSION_END_HOOKS: list = []
TOOL_CTX = threading.local()
LOADED_FEATURES: list[str] = []


def register_tool(tool, quick: bool = True) -> None:
    AGENT_TOOLS.append(tool)
    if quick:
        QUICK_TOOLS.append(tool)


def emit_event(kind: str, payload: dict) -> None:
    """Send a custom event to the Ask/Quick Ask stream that is calling the current tool."""
    fn = getattr(TOOL_CTX, "emit", None)
    if fn:
        fn(kind, payload)


def load_features() -> None:
    import importlib
    if not FEATURES_DIR.exists():
        return
    sys.path.insert(0, str(FEATURES_DIR))
    for path in sorted(FEATURES_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(path.stem)
            if hasattr(module, "register"):
                module.register()
            LOADED_FEATURES.append(path.stem)
        except Exception as e:  # one broken feature must not take the app down
            log(f"feature {path.stem} failed to load: {e}")


def feature_tags(page: str) -> str:
    """<script>/<link> tags for web/features: *.quick.js → quick page, other *.js → main page."""
    if not WEB_FEATURES.exists():
        return ""
    tags = []
    for f in sorted(WEB_FEATURES.iterdir()):
        is_quick = ".quick." in f.name
        if (page == "quick") != is_quick:
            continue
        if f.suffix == ".js":
            tags.append(f'<script src="/features/{f.name}"></script>')
        elif f.suffix == ".css":
            tags.append(f'<link rel="stylesheet" href="/features/{f.name}">')
    return "\n".join(tags)


# ------------------------------------------------------------- HTTP API

TOKEN = os.environ.get("GLINT_TOKEN") or secrets.token_urlsafe(24)
PORT = int(os.environ.get("GLINT_PORT", "47321"))


def status_payload() -> dict:
    s = load_settings()
    keys = api_keys()
    with DB_LOCK:
        d = db()
        stats = mem.memory_stats(d)
        d.close()
    return {"capture": {**CAPTURER.status, "session_id": CAPTURER.session_id,
                        "paused_until": CAPTURER.paused_until * 1000 if CAPTURER.paused_until else None,
                        "enabled": s["capture_enabled"]},
            "provider": s["provider"], "keys": {k: bool(v) for k, v in keys.items()}, "ai": ai_status(),
            "memory": stats}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _page(self, name):
        html = (WEB / name).read_text().replace("__GLINT_TOKEN__", TOKEN)
        html = html.replace("<!--FEATURE_TAGS-->", feature_tags("quick" if name == "quick.html" else "main"))
        self._send(200, html.encode(), "text/html; charset=utf-8")

    def _static_feature(self, name):
        f = (WEB_FEATURES / name).resolve()
        if f.parent != WEB_FEATURES.resolve() or not f.is_file():
            return self._send(404, {"error": "not found"})
        types = {".js": "text/javascript", ".css": "text/css", ".html": "text/html; charset=utf-8",
                 ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json"}
        data = f.read_bytes()
        if f.suffix == ".html":
            data = data.decode().replace("__GLINT_TOKEN__", TOKEN).encode()
        self._send(200, data, types.get(f.suffix, "application/octet-stream"))

    def _allowed(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):  # DNS-rebinding guard
            return False
        if not self.path.startswith("/api/"):
            return True  # pages, feature assets and icons carry no captured text
        return secrets.compare_digest(self.headers.get("X-Glint-Token", ""), TOKEN)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                return self._page("app.html")
            if url.path == "/quick":
                return self._page("quick.html")
            if url.path.startswith("/features/"):
                return self._static_feature(url.path[len("/features/"):])
            if url.path == "/icon/app":
                png = app_icon_png(q.get("name", ""))
                return self._send(200, png, "image/png") if png else self._send(404, {"error": "no icon"})
            if url.path == "/icon/site":
                got = site_icon(q.get("host", ""))
                return self._send(200, got[0], got[1]) if got else self._send(404, {"error": "no icon"})
            route = GET_ROUTES.get(url.path)
            if route:
                return self._send(200, route(q))
            self._send(404, {"error": "not found"})
        except (ai.ProviderError, ValueError, KeyError) as e:
            self._send(400, {"error": str(e) if not isinstance(e, KeyError) else f"missing field {e}"})
        except Exception as e:
            log(f"GET {url.path} failed: {e}")
            self._send(500, {"error": str(e)})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/ask":
                return self._stream_ask(body)
            route = POST_ROUTES.get(path)
            if route:
                return self._send(200, route(body))
            self._send(404, {"error": "not found"})
        except (ai.ProviderError, ValueError, KeyError) as e:
            self._send(400, {"error": str(e) if not isinstance(e, KeyError) else f"missing field {e}"})
        except Exception as e:
            log(f"POST {path} failed: {e}")
            self._send(500, {"error": str(e)})

    def _stream_ask(self, body):
        message = (body.get("message") or "").strip()
        if not message:
            return self._send(400, {"error": "empty message"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(kind, payload):
            try:
                self.wfile.write(f"event: {kind}\ndata: {json.dumps(payload)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        try:
            answer = ask(body.get("thread_id"), message, emit,
                         "quick" if body.get("scope") == "quick" else "main")
            emit("done", {"answer": answer})
        except ai.ProviderError as e:
            emit("error", {"message": str(e)})
        except Exception as e:
            log(f"ask failed: {e}")
            emit("error", {"message": f"Something went wrong: {e}"})


def _memory_search(q):
    limit = min(max(int(q.get("limit", 48)), 1), mem.MAX_LIMIT)
    since = float(q["sinceHours"]) if q.get("sinceHours") else None
    app, query = q.get("appId") or None, (q.get("query") or "").strip()
    with DB_LOCK:
        d = db()
        try:
            if not query:
                return {"hits": mem.recent_hits(d, limit, since, app), "terms": [], "notes": []}
            res, terms, notes = mem.search(d, query, since, app, limit, q.get("mode", "hybrid"))
            return {"hits": [mem.to_hit(r) for r in res], "terms": terms, "notes": notes}
        finally:
            d.close()


def _memory_stats(q):
    with DB_LOCK:
        d = db()
        apps = d.execute("SELECT app_id, count(*) FROM memory_chunks GROUP BY 1 ORDER BY 2 DESC").fetchall()
        stats = mem.memory_stats(d)
        d.close()
    return {**stats, "apps": apps, "retention_days": load_settings()["retention_days"]}


def _write(sql: str, params=()):
    with DB_LOCK:
        d = db()
        n = d.execute(sql, params).rowcount
        d.commit()
        d.close()
    return n


def _session_detail(q):
    rows = session_rows("s.id = ?", (q["id"],), 1)
    if not rows:
        raise ValueError("unknown session")
    with DB_LOCK:
        d = db()
        caps = d.execute("SELECT id, app_id, window_title, url_host, captured_at, source, char_count "
                         "FROM memory_chunks WHERE session_id=? ORDER BY captured_at", (q["id"],)).fetchall()
        d.close()
    s = with_hosts(rows)[0]
    return {**s, "related": related_sessions(s), "timeline": [
        dict(zip(("id", "app", "title", "host", "at", "source", "chars"), c)) for c in caps]}


def _thread(q):
    with DB_LOCK:
        d = db()
        msgs = d.execute("SELECT role, content, tools, created_at FROM chat_messages WHERE thread_id=? "
                         "ORDER BY id", (q["id"],)).fetchall()
        d.close()
    return {"id": q["id"], "messages": [{"role": r, "content": c, "tools": json.loads(t), "at": a}
                                        for r, c, t, a in msgs]}


def _threads(q):
    with DB_LOCK:
        d = db()
        rows = d.execute("SELECT id, title, updated_at FROM chat_threads ORDER BY updated_at DESC LIMIT 100").fetchall()
        d.close()
    return [{"id": i, "title": t, "updated_at": u} for i, t, u in rows]


def _tasks(q):
    with DB_LOCK:
        d = db()
        rows = d.execute("SELECT id, title, status, progress, logs, created_at, updated_at FROM tasks "
                         "ORDER BY progress >= 100, updated_at DESC").fetchall()
        d.close()
    return [{"id": i, "title": t, "status": st, "progress": p, "logs": json.loads(lg or "[]"),
             "created_at": c, "updated_at": u} for i, t, st, p, lg, c, u in rows]


def _profile(q):
    return {"bio": kv_get("bio", ""), "memory": kv_get("memory_doc", ""), "bio_max": BIO_MAX,
            "memory_max": MEMORY_MAX, "skeleton": MEMORY_SKELETON}


def _settings(q):
    return {"settings": load_settings(), "keys": {k: bool(v) for k, v in api_keys().items()}}


def _models(q):
    s = load_settings()
    which = q.get("provider", s["provider"])
    return {"provider": which, "models": provider({**s, "provider": which}).list_models()}


def _set_keys(b):
    for name in ("claude", "openai"):
        if name in b:
            keychain_set(name, (b[name] or "").strip() or None)
    return {"keys": {k: bool(v) for k, v in api_keys().items()}}


def _retention(b):
    days = int(b["days"]) if b.get("days") else None
    save_settings({"retention_days": days})
    removed = _write("DELETE FROM memory_chunks WHERE captured_at < ?",
                     (now_ms() - days * 86_400_000,)) if days else 0
    return {"retention_days": days, "removed": removed}


def _delete_session(b):
    with DB_LOCK:
        d = db()
        n = d.execute("DELETE FROM memory_chunks WHERE session_id=?", (b["id"],)).rowcount
        d.execute("DELETE FROM sessions WHERE id=?", (b["id"],))
        d.commit()
        d.close()
    if CAPTURER.session_id == b["id"]:
        CAPTURER.session_id = None
    for hook in SESSION_DELETE_HOOKS:
        try:
            hook(b["id"])
        except Exception as e:
            log(f"session delete hook failed: {e}")
    return {"deleted_captures": n}


def _capture_control(b):
    if "enabled" in b:
        save_settings({"capture_enabled": bool(b["enabled"])})
    if b.get("pause") == "tomorrow":  # until 9am tomorrow
        nine = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        CAPTURER.pause((nine - datetime.now()).total_seconds() / 60)
    elif b.get("pause") == "indefinite":
        CAPTURER.pause(None)
        save_settings({"capture_enabled": False})
    if "pause_minutes" in b:
        CAPTURER.pause(float(b["pause_minutes"]) if b["pause_minutes"] else None)
    if b.get("now"):
        CAPTURER.capture_now()
    return status_payload()


def _add_task(b):
    return create_task(b.get("title"), b.get("log"), b.get("progress"))


def _update_task(b):
    if b.get("title"):
        _write("UPDATE tasks SET title=?, updated_at=? WHERE id=?", (b["title"][:120], now_ms(), b["id"]))
    if b.get("progress") is not None or b.get("log"):
        return update_task(b["id"], b.get("progress"), b.get("log"))
    return {"ok": True}


def _import_browser(b):
    with DB_LOCK:
        d = db()
        n = mem.import_chromium(d, int(b.get("days") or 14))
        d.close()
    threading.Thread(target=lambda: _embed_now(), daemon=True).start()
    return {"imported": n}


def _embed_now():
    with DB_LOCK:
        d = db()
        mem.embed_pending(d, verbose=False)
        d.close()


GET_ROUTES = {
    "/api/status": lambda q: status_payload(),
    "/api/features": lambda q: {"features": LOADED_FEATURES},
    "/api/ai/status": lambda q: ai_status(),
    "/api/today": lambda q: today_activity(q.get("date")),
    "/api/sessions": lambda q: session_rows(limit=int(q.get("limit", 100))),
    "/api/session": _session_detail,
    "/api/sessions/search": search_sessions,
    "/api/sessions/facets": session_facets,
    "/api/timeline": day_timeline,
    "/api/capture/text": _capture_text,
    "/api/search": _memory_search,
    "/api/stats": _memory_stats,
    "/api/threads": _threads,
    "/api/thread": _thread,
    "/api/tasks": _tasks,
    "/api/profile": _profile,
    "/api/settings": _settings,
    "/api/models": _models,
    "/api/dictionary": lambda q: {"items": dictionary()},
}

POST_ROUTES = {
    "/api/settings": lambda b: {"settings": save_settings(b)},
    "/api/keys": _set_keys,
    "/api/capture": _capture_control,
    "/api/delete": lambda b: {"deleted": sum(_write("DELETE FROM memory_chunks WHERE id=?", (i,))
                                             for i in b.get("chunkIds", []) if isinstance(i, str))},
    "/api/purge": lambda b: {"deleted": _write("DELETE FROM memory_chunks")},
    "/api/retention": _retention,
    "/api/session/summarize": lambda b: summarize_session(b["id"]),
    "/api/session/rename": lambda b: {"ok": _write("UPDATE sessions SET title=? WHERE id=?",
                                                   (b["title"], b["id"]))},
    "/api/session/project": lambda b: {"ok": _write("UPDATE sessions SET project=? WHERE id=?",
                                                    ((b.get("project") or "").strip()[:60] or None, b["id"]))},
    "/api/session/delete": _delete_session,
    "/api/session/note": lambda b: {"ok": _write("UPDATE sessions SET note=? WHERE id=?", (b.get("note", ""), b["id"]))},
    "/api/thread/delete": lambda b: {"ok": _write("DELETE FROM chat_threads WHERE id=?", (b["id"],))
                                     + _write("DELETE FROM chat_messages WHERE thread_id=?", (b["id"],))},
    "/api/tasks": _add_task,
    "/api/task/update": _update_task,
    "/api/task/delete": lambda b: {"ok": _write("DELETE FROM tasks WHERE id=?", (b["id"],))},
    "/api/bio": lambda b: {"result": tool_update_bio({"bio": b.get("bio", "")})},
    "/api/memory": lambda b: {"result": tool_update_memory({"memory": b.get("memory", "")})},
    "/api/thread/rename": lambda b: {"ok": _write("UPDATE chat_threads SET title=?, renamed=1 WHERE id=?",
                                                  (b["title"][:60], b["id"]))},
    "/api/import/browser": _import_browser,
    "/api/dictation": _dictation,
    "/api/dictation/polish": lambda b: polish(b.get("text", ""), b.get("app", ""), b.get("url_host")),
    "/api/dictionary": lambda b: (upsert_dictionary(b.get("value"), b.get("alias")), {"items": dictionary()})[1],
    "/api/dictionary/delete": lambda b: (_write("DELETE FROM dictation_dictionary WHERE id=?", (int(b["id"]),)),
                                         {"items": dictionary()})[1],
}


def main():
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    init_db()
    load_features()
    days = load_settings()["retention_days"]
    if days:
        _retention({"days": days})
    threading.Thread(target=lambda: mem.Embedder.embed(["warmup"]), daemon=True).start()
    if os.environ.get("GLINT_NO_CAPTURE") != "1":
        CAPTURER.start()
    for hook in STARTUP_HOOKS:
        threading.Thread(target=hook, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(json.dumps({"ready": True, "port": PORT, "token": TOKEN}), flush=True)
    log(f"Glint engine on http://127.0.0.1:{PORT}/ (data: {APP_SUPPORT})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        CAPTURER.end_session("shutdown")


if __name__ == "__main__":
    main()
