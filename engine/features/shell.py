"""Shell completeness: setup wizard state, Home data, insight ratings, focus nudges and daily notices,
markdown skills (with /trigger expansion in Ask), automation ideas, and Safari/Firefox history import.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import ai
import engine as E

mem = E.mem

DEFAULT_DISTRACTING_SITES = ["youtube.com", "x.com", "twitter.com", "reddit.com", "facebook.com",
                             "instagram.com", "tiktok.com", "netflix.com", "twitch.tv",
                             "news.ycombinator.com"]
DEFAULT_DISTRACTING_APPS = ["TV", "Steam", "Netflix"]

E.DEFAULT_SETTINGS.update({
    "onboarding_done": False,
    "persona": "",
    "session_hud": True,
    "notifications": True,
    "focus_nudges": False,
    "focus_vignette": False,
    "nudge_grace_s": 60,                 # seconds on a distracting app/site before a nudge
    "distracting_apps": DEFAULT_DISTRACTING_APPS,
    "distracting_sites": DEFAULT_DISTRACTING_SITES,
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS insight_ratings (
    session_id TEXT NOT NULL, idx INTEGER NOT NULL, vote TEXT NOT NULL, reason TEXT,
    headline TEXT, created_at INTEGER NOT NULL, PRIMARY KEY (session_id, idx));
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, trigger TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
    uses INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
"""

STARTED = time.time()
CHECK_EVERY_S = 30
NUDGE_COOLDOWN_S = 10 * 60
QUIET_HOUR = 14


def _db():
    d = E.db()
    d.executescript(SCHEMA)
    return d


def _query(sql, params=()):
    with E.DB_LOCK:
        d = E.db()
        try:
            return d.execute(sql, params).fetchall()
        finally:
            d.close()


def _exec(sql, params=()) -> int:
    with E.DB_LOCK:
        d = E.db()
        try:
            n = d.execute(sql, params).rowcount
            d.commit()
            return n
        finally:
            d.close()


def _day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# ------------------------------------------------------------ onboarding

def onboarding_state(q=None) -> dict:
    s = E.load_settings()
    return {"onboarding_done": bool(s["onboarding_done"]), "step": E.kv_get("shell:setup_step", 0),
            "persona": s["persona"]}


def set_onboarding(b: dict) -> dict:
    patch = {}
    if "step" in b:
        E.kv_set("shell:setup_step", max(0, int(b["step"] or 0)))
    if "persona" in b:
        patch["persona"] = str(b["persona"] or "")[:80]
    if "done" in b:
        patch["onboarding_done"] = bool(b["done"])
        if b["done"]:
            E.kv_set("shell:setup_step", 0)
    if b.get("open_at_login") is not None:
        patch["open_at_login"] = bool(b["open_at_login"])
    if patch:
        E.save_settings(patch)
    return onboarding_state()


def _migrate_existing_users() -> None:
    """Existing installs with real history skip the first-run wizard."""
    try:
        raw = json.loads(E.SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        raw = {}
    if "onboarding_done" in raw:
        return
    n = _query("SELECT count(*) FROM memory_chunks")[0][0]
    if n > 50:
        E.save_settings({"onboarding_done": True})


# ------------------------------------------------------------ permissions
# The native shell reports what only it can see (Accessibility for the app, microphone, screen
# recording, running apps); capture's own Accessibility probe is the fallback.

NATIVE: dict = {}


def native_report(b: dict) -> dict:
    NATIVE.update({k: b[k] for k in ("ax", "mic", "screen", "apps", "notifications") if k in b})
    NATIVE["at"] = E.now_ms()
    return {"ok": True}


def ax_trusted():
    fresh = NATIVE.get("at") and E.now_ms() - NATIVE["at"] < 120_000
    if fresh and NATIVE.get("ax") is not None:
        return bool(NATIVE["ax"])
    return E.CAPTURER.status.get("ax_trusted")


def permissions(q=None) -> dict:
    q = q or {}
    out = {"ax_trusted": ax_trusted(), "capture_ax_trusted": E.CAPTURER.status.get("ax_trusted"),
           "mic": NATIVE.get("mic", "unknown"), "screen": NATIVE.get("screen", "unknown"),
           "notifications": NATIVE.get("notifications", "unknown"),
           "running_apps": NATIVE.get("apps", []), "native_at": NATIVE.get("at")}
    if q.get("fda"):
        out["full_disk_access"] = safari_access()
    return out


# ------------------------------------------------------------ home / now

def now_state(q=None) -> dict:
    s = E.load_settings()
    st = E.CAPTURER.status
    paused_until = E.CAPTURER.paused_until * 1000 if E.CAPTURER.paused_until > time.time() else None
    sid = E.CAPTURER.session_id
    started = None
    title = None
    if sid:
        row = _query("SELECT started_at, title FROM sessions WHERE id=?", (sid,))
        if row:
            started, title = row[0]
    app = st.get("app")
    if not s["capture_enabled"]:
        mode = "off"
    elif paused_until:
        mode = "paused"
    elif ax_trusted() is False:
        mode = "needs_access"
    elif st.get("state") == "skipped":
        mode = "private"
    elif st.get("state") == "capturing":
        text_ok = s["capture_text_all_apps"] or (app or "").lower() in {a.lower() for a in s["text_apps"]}
        mode = "remembering" if text_ok else "title_only"
    else:
        mode = st.get("state") or "idle"
    return {"mode": mode, "state": st.get("state"), "app": app,
            "title": st.get("title") if mode != "private" else None,
            "session_id": sid, "session_title": title, "session_started_at": started,
            "elapsed_s": round((E.now_ms() - started) / 1000) if started else 0,
            "paused_until": paused_until, "capture_enabled": s["capture_enabled"],
            "session_hud": s["session_hud"]}


def _session_seconds(apps_json: str) -> float:
    try:
        return sum(float(v) for v in json.loads(apps_json or "{}").values())
    except (ValueError, TypeError, AttributeError):
        return 0.0


def home(q=None) -> dict:
    today = _day_start(datetime.now())
    lo = today - timedelta(days=6)
    rows = _query("SELECT started_at, apps, focus_score FROM sessions WHERE started_at >= ?", (_ms(lo),))
    days = []
    for i in range(7):
        d0 = lo + timedelta(days=i)
        a, b = _ms(d0), _ms(d0 + timedelta(days=1))
        secs = sum(_session_seconds(r[1]) for r in rows if a <= r[0] < b)
        days.append({"date": d0.strftime("%Y-%m-%d"), "label": d0.strftime("%a"),
                     "minutes": round(secs / 60)})
    t = E.today_activity(None)
    scored = [x["focus_score"] for x in t["sessions"] if x["focus_score"] is not None]
    t0 = _ms(today)
    asks = _query("SELECT count(*) FROM chat_messages WHERE role='user' AND created_at >= ?", (t0,))[0][0]
    done = _query("SELECT count(*) FROM tasks WHERE progress >= 100 AND updated_at >= ?", (t0,))[0][0]
    open_tasks = _query("SELECT count(*) FROM tasks WHERE progress < 100")[0][0]
    caps = _query("SELECT count(*) FROM memory_chunks WHERE captured_at >= ? AND source != 'browser'", (t0,))[0][0]
    n_skills = _query("SELECT count(*) FROM skills")[0][0]
    return {
        "now": now_state(),
        "week": days,
        "today": {"tracked_seconds": t["tracked_seconds"], "sessions": len(t["sessions"]),
                  "avg_focus": round(sum(scored) / len(scored)) if scored else None,
                  "asks": asks, "tasks_done": done, "tasks_open": open_tasks, "captures": caps,
                  "top_apps": t["app_totals"][:5],
                  "latest": [{"id": x["id"], "title": x["title"], "started_at": x["started_at"],
                              "ended_at": x["ended_at"], "state_name": x["state_name"],
                              "focus_score": x["focus_score"], "active": x["active"]}
                             for x in t["sessions"][:4]]},
        "onboarding": onboarding_state(),
        "skills": n_skills,
    }


# ------------------------------------------------------------ insight ratings

VOTES = {"up", "down", "none"}
REASONS = {"obvious", "wrong", "not_useful"}


def rate_insight(b: dict) -> dict:
    sid, vote = b.get("session_id"), b.get("vote")
    idx = int(b.get("index", -1))
    if not sid or vote not in VOTES or idx < 0:
        raise ValueError("session_id, index and vote (up|down) are required.")
    reason = b.get("reason") if vote == "down" else None
    if reason is not None and reason not in REASONS:
        raise ValueError("reason must be obvious, wrong or not_useful.")
    row = _query("SELECT insights FROM sessions WHERE id=?", (sid,))
    if not row:
        raise ValueError("unknown session")
    ins = json.loads(row[0][0] or "[]")
    if idx >= len(ins):
        raise ValueError("no insight at that index")
    if vote == "none":
        _exec("DELETE FROM insight_ratings WHERE session_id=? AND idx=?", (sid, idx))
    else:
        _exec("INSERT OR REPLACE INTO insight_ratings VALUES (?,?,?,?,?,?)",
              (sid, idx, vote, reason, ins[idx].get("headline"), E.now_ms()))
    return {"ok": True, "session_id": sid, "index": idx, "vote": vote, "reason": reason}


def insight_ratings(q=None) -> dict:
    rows = _query("SELECT session_id, idx, vote, reason FROM insight_ratings")
    return {"ratings": {f"{s}:{i}": {"vote": v, "reason": r} for s, i, v, r in rows}}


def _ratings_context():
    rows = _query("SELECT headline, reason FROM insight_ratings WHERE vote='down' AND headline IS NOT NULL "
                  "ORDER BY created_at DESC LIMIT 8")
    if not rows:
        return None
    return ("[Insight feedback] The user marked these session insights as unhelpful; avoid similar ones:\n"
            + "\n".join(f"- {h} ({(r or 'not useful').replace('_', ' ')})" for h, r in rows))


# ------------------------------------------------------------ nudges and notices

EVENTS: deque = deque(maxlen=60)
EVENTS_LOCK = threading.Lock()
_seq = [0]
FOCUS = {"since": None, "key": None, "last_nudge": 0.0, "allowed": set(), "session": None}


def push_event(kind: str, title: str, body: str, **extra) -> dict:
    with EVENTS_LOCK:
        _seq[0] += 1
        ev = {"id": _seq[0], "kind": kind, "title": title, "body": body, "at": E.now_ms(), **extra}
        EVENTS.append(ev)
    E.log(f"shell {kind}: {title}")
    return ev


def nudges(q=None) -> dict:
    since = int((q or {}).get("since") or 0)
    with EVENTS_LOCK:
        items = [e for e in EVENTS if e["id"] > since]
        latest = _seq[0]
    s = E.load_settings()
    return {"events": items, "latest": latest,
            "settings": {"notifications": s["notifications"], "focus_vignette": s["focus_vignette"],
                         "focus_nudges": s["focus_nudges"]}}


def dismiss_nudge(b: dict) -> dict:
    """{id, focused}: focused=true means 'I was on task' — stop nudging for that app this session."""
    with EVENTS_LOCK:
        ev = next((e for e in EVENTS if e["id"] == int(b.get("id") or 0)), None)
        if ev:
            ev["dismissed"] = True
    if ev and b.get("focused") and ev.get("match"):
        FOCUS["allowed"].add(ev["match"].lower())
    return {"ok": True}


def _distraction_match(app: str, title: str, host: str | None, s: dict) -> str | None:
    a = (app or "").lower()
    for name in s["distracting_apps"]:
        if name and a == name.lower():
            return name
    t = (title or "").lower()
    for site in s["distracting_sites"]:
        site = (site or "").lower().strip()
        if not site:
            continue
        if host and (host == site or host.endswith("." + site)):
            return site
        stem = site.split(".")[0]
        if len(stem) >= 4 and re.search(rf"\b{re.escape(stem)}\b", t):
            return site
    return None


def _current_host(sid: str, app: str) -> str | None:
    row = _query("SELECT app_id, url_host FROM memory_chunks WHERE session_id=? ORDER BY captured_at DESC LIMIT 1",
                 (sid,))
    if row and row[0][0] == (app or "").lower():
        return row[0][1]
    return None


def check_focus(now: float | None = None) -> dict | None:
    now = now or time.time()
    s = E.load_settings()
    st, sid = E.CAPTURER.status, E.CAPTURER.session_id
    if FOCUS["session"] != sid:
        FOCUS.update(session=sid, since=None, key=None, allowed=set())
    if not (s["focus_nudges"] and sid and st.get("state") == "capturing"):
        FOCUS.update(since=None, key=None)
        return None
    app, title = st.get("app") or "", st.get("title") or ""
    match = _distraction_match(app, title, _current_host(sid, app), s)
    if not match or match.lower() in FOCUS["allowed"]:
        FOCUS.update(since=None, key=None)
        return None
    if FOCUS["key"] != match:
        FOCUS.update(key=match, since=now)
    if now - FOCUS["since"] < int(s["nudge_grace_s"]) or now - FOCUS["last_nudge"] < NUDGE_COOLDOWN_S:
        return None
    FOCUS["last_nudge"] = now
    mins = max(1, round((now - FOCUS["since"]) / 60))
    return push_event("nudge", "Drifting off?",
                      f"You've been on {match} for about {mins} min during a work session.",
                      match=match, app=app, vignette=bool(s["focus_vignette"]))


def _once_per_day(key: str) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    if E.kv_get(f"shell:notice:{key}") == today:
        return False
    E.kv_set(f"shell:notice:{key}", today)
    return True


def check_notices(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    s = E.load_settings()
    out = []
    if not s["notifications"] or not s["onboarding_done"]:
        return out
    if ax_trusted() is False and _once_per_day("ax"):
        out.append(push_event("notice", "Glint lost Accessibility access",
                              "Window text isn't being captured. Re-enable Glint in System Settings › "
                              "Privacy & Security › Accessibility.", notice="ax", action="requestAccessibility"))
    if not s["capture_enabled"]:
        if _once_per_day("capture_off"):
            out.append(push_event("notice", "Capture is off",
                                  "Glint isn't remembering anything right now. Turn capture back on from the menu bar.",
                                  notice="capture_off"))
    elif (now.weekday() < 5 and now.hour >= QUIET_HOUR and time.time() - STARTED > 30 * 60
          and E.CAPTURER.paused_until <= time.time()):
        caps = _query("SELECT count(*) FROM memory_chunks WHERE captured_at >= ? AND source != 'browser'",
                      (_ms(_day_start(now)),))[0][0]
        if caps == 0 and _once_per_day("quiet"):
            out.append(push_event("notice", "Quiet day so far",
                                  "Glint hasn't captured anything today. If you've been working, check that "
                                  "capture is on and Accessibility is granted.", notice="quiet"))
    return out


def _monitor():
    while not E.STOP.wait(CHECK_EVERY_S):
        try:
            check_focus()
            check_notices()
        except Exception as e:
            E.log(f"shell monitor: {e}")


# ------------------------------------------------------------ skills

RESERVED = {"/task", "/log", "/summarize", "/find"}
FRONTMATTER = re.compile(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40].strip("-")


def norm_trigger(trigger: str | None, name: str) -> str:
    slug = slugify((trigger or "").lstrip("/")) or slugify(name)
    if not slug:
        raise ValueError("A skill needs a name or trigger.")
    return "/" + slug


def _skill_dict(r) -> dict:
    return {"id": r[0], "name": r[1], "trigger": r[2], "description": r[3], "body": r[4], "uses": r[5],
            "created_at": r[6], "updated_at": r[7]}


SKILL_COLS = "id, name, trigger, description, body, uses, created_at, updated_at"


def list_skills(q=None) -> list[dict]:
    term = ((q or {}).get("q") or "").strip().lower()
    rows = [_skill_dict(r) for r in _query(f"SELECT {SKILL_COLS} FROM skills ORDER BY name COLLATE NOCASE")]
    if term:
        rows = [r for r in rows if term in f"{r['name']} {r['trigger']} {r['description']} {r['body']}".lower()]
    return rows


def find_skill(ref: str) -> dict | None:
    ref = (ref or "").strip()
    if not ref:
        return None
    trig = ref if ref.startswith("/") else "/" + slugify(ref)
    rows = _query(f"SELECT {SKILL_COLS} FROM skills WHERE id=? OR trigger=? COLLATE NOCASE "
                  "OR name=? COLLATE NOCASE LIMIT 1", (ref, trig, ref))
    return _skill_dict(rows[0]) if rows else None


def save_skill(b: dict) -> dict:
    name = (b.get("name") or "").strip()[:80]
    existing = find_skill(b["id"]) if b.get("id") else None
    if b.get("id") and not existing:
        raise ValueError("No skill with that id.")
    if not name and existing:
        name = existing["name"]
    if not name:
        raise ValueError("A skill needs a name.")
    trigger = norm_trigger(b.get("trigger") if b.get("trigger") is not None
                           else (existing or {}).get("trigger"), name)
    if trigger.lower() in RESERVED:
        raise ValueError(f"{trigger} is a built-in shortcut; pick another trigger.")
    clash = _query("SELECT id FROM skills WHERE trigger=? COLLATE NOCASE", (trigger,))
    if clash and (not existing or clash[0][0] != existing["id"]):
        raise ValueError(f"Another skill already uses {trigger}.")
    desc = (b.get("description") if b.get("description") is not None
            else (existing or {}).get("description", "")) or ""
    body = (b.get("body") if b.get("body") is not None else (existing or {}).get("body", "")) or ""
    desc, body, ts = desc.strip()[:300], body.strip()[:20000], E.now_ms()
    if existing:
        _exec("UPDATE skills SET name=?, trigger=?, description=?, body=?, updated_at=? WHERE id=?",
              (name, trigger, desc, body, ts, existing["id"]))
        sid = existing["id"]
    else:
        sid = str(uuid.uuid4())
        _exec("INSERT INTO skills (id, name, trigger, description, body, created_at, updated_at) "
              "VALUES (?,?,?,?,?,?,?)", (sid, name, trigger, desc, body, ts, ts))
    return find_skill(sid)


def delete_skill(b: dict) -> dict:
    return {"deleted": _exec("DELETE FROM skills WHERE id=?", (b.get("id"),))}


def parse_skill_markdown(text: str, filename: str = "") -> dict:
    meta, body = {}, text or ""
    m = FRONTMATTER.match(body)
    if m:
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep:
                meta[k.strip().lower()] = v.strip().strip("\"'")
        body = m.group(2)
    name = meta.get("name")
    if not name:
        h = re.search(r"^#\s+(.+)$", body, re.M)
        name = h.group(1).strip() if h else re.sub(r"\.(md|markdown|txt)$", "", Path(filename).name, flags=re.I)
    return {"name": name or "Imported skill", "trigger": meta.get("trigger"),
            "description": meta.get("description", ""), "body": body.strip()}


def import_skill(b: dict) -> dict:
    parsed = parse_skill_markdown(b.get("markdown") or "", b.get("filename") or "")
    try:
        return save_skill(parsed)
    except ValueError as e:
        if "already uses" not in str(e):
            raise
        parsed["trigger"] = norm_trigger(parsed.get("trigger"), parsed["name"]) + "-" + uuid.uuid4().hex[:4]
        return save_skill(parsed)


def skill_markdown(sk: dict) -> str:
    return (f"---\nname: {sk['name']}\ntrigger: {sk['trigger']}\ndescription: {sk['description']}\n---\n\n"
            f"{sk['body']}\n")


def _expand_skills(prev):
    def apply_quick_action(message: str) -> str:
        cmd, _, rest = message.partition(" ")
        if cmd.lower() not in RESERVED:
            sk = find_skill(cmd) if re.fullmatch(r"/[A-Za-z0-9-]+", cmd) else None
            if sk:
                _exec("UPDATE skills SET uses = uses + 1 WHERE id=?", (sk["id"],))
                return (f"{rest.strip() or '(no extra input)'}\n\n[Skill {sk['trigger']} — {sk['name']}. "
                        f"Follow these instructions for the request above]\n{sk['body']}")
        return prev(message)
    return apply_quick_action


def _skills_context():
    rows = _query("SELECT name, trigger, description FROM skills ORDER BY uses DESC, name LIMIT 40")
    if not rows:
        return None
    return ("[Skills] Saved instructions the user can run with a trigger; read_skill gives the full body. "
            "Follow a skill when the user's request matches it.\n"
            + "\n".join(f"- {t} {n}: {d}" for n, t, d in rows))


def tool_create_skill(args: dict) -> str:
    sk = save_skill({"name": args.get("name"), "trigger": args.get("trigger"),
                     "description": args.get("description"), "body": args.get("body")})
    return f"Created skill {sk['name']} ({sk['trigger']})."


def tool_update_skill(args: dict) -> str:
    sk = find_skill(args.get("skill") or "")
    if not sk:
        return "Error: no skill with that id, trigger or name."
    sk = save_skill({"id": sk["id"], "name": args.get("name"), "trigger": args.get("trigger"),
                     "description": args.get("description"), "body": args.get("body")})
    return f"Updated skill {sk['name']} ({sk['trigger']})."


def tool_read_skill(args: dict) -> str:
    sk = find_skill(args.get("skill") or "")
    if not sk:
        names = ", ".join(f"{s['trigger']}" for s in list_skills()) or "none"
        return f"No skill matches. Available: {names}"
    return skill_markdown(sk)


# ------------------------------------------------------------ automation ideas

IDEAS_SCHEMA = {"type": "object", "properties": {"ideas": {"type": "array", "items": {
    "type": "object", "properties": {
        "title": {"type": "string", "description": "short name of the repeated work"},
        "pattern": {"type": "string", "description": "what the user keeps doing, concretely"},
        "evidence": {"type": "string", "description": "which sessions/apps show it and how often"},
        "suggestion": {"type": "string", "description": "how to automate or speed it up"},
        "effort": {"type": "string", "enum": ["low", "medium", "high"]},
        "minutes_per_week": {"type": "integer", "description": "rough time it costs per week"},
        "skill_name": {"type": "string"},
        "skill_body": {"type": "string", "description": "markdown instructions for an assistant skill "
                                                         "that would handle this, or empty"}},
    "required": ["title", "pattern", "evidence", "suggestion", "effort", "minutes_per_week",
                 "skill_name", "skill_body"], "additionalProperties": False}}},
    "required": ["ideas"], "additionalProperties": False}

IDEAS_SYSTEM = (
    "You look for repetitive work in a person's recent work sessions on their Mac (titles, summaries, "
    "apps and time) and suggest what could be automated, templated or delegated to an AI assistant. "
    "Only report patterns that recur (at least twice) and cite the evidence. Prefer concrete, low-effort "
    "ideas. For each, draft a short assistant skill (markdown steps) if an AI assistant with access to "
    "the user's screen history could do it. Return 0-6 ideas; return none rather than invent patterns.")


def automation_scan(b: dict) -> dict:
    days = max(1, min(60, int(b.get("days") or 14)))
    since = E.now_ms() - days * 86_400_000
    sessions = E.session_rows("s.started_at >= ? AND s.summary IS NOT NULL", (since,), 150)
    if len(sessions) < 2:
        result = {"ideas": [], "at": E.now_ms(), "days": days, "sessions": len(sessions),
                  "note": "Not enough summarized sessions yet. Ideas appear after a few days of work."}
        E.kv_set("shell:automation_ideas", result)
        return result
    lines = [f"- {E.fmt_time(s['started_at'])} ({round((s['ended_at'] - s['started_at']) / 60000)} min) "
             f"{s['title'] or 'Session'} [{s['state_name'] or s['state_tag'] or ''}] apps: "
             f"{', '.join(a for a, _ in s['apps'][:4])} — {E._clip(s['summary'] or '', 400)}"
             for s in reversed(sessions)]
    tasks = _query("SELECT title FROM tasks ORDER BY updated_at DESC LIMIT 30")
    prompt = (f"Work sessions from the last {days} days:\n" + "\n".join(lines)[:40000]
              + ("\n\nTasks:\n" + "\n".join(f"- {t[0]}" for t in tasks) if tasks else ""))
    r = E.provider().complete_json(IDEAS_SYSTEM, prompt, IDEAS_SCHEMA)
    result = {"ideas": r.get("ideas", [])[:6], "at": E.now_ms(), "days": days, "sessions": len(sessions)}
    E.kv_set("shell:automation_ideas", result)
    return result


# ------------------------------------------------------------ browser history import

HOME = Path.home()


def safari_dir() -> Path:
    return Path(os.environ.get("GLINT_SAFARI_DIR") or HOME / "Library" / "Safari")


def firefox_dir() -> Path:
    return Path(os.environ.get("GLINT_FIREFOX_DIR")
                or HOME / "Library" / "Application Support" / "Firefox" / "Profiles")


def safari_access() -> str:
    """'ok' | 'denied' (needs Full Disk Access) | 'missing'."""
    try:
        with open(safari_dir() / "History.db", "rb") as f:
            f.read(16)
        return "ok"
    except PermissionError:
        return "denied"
    except FileNotFoundError:
        return "missing"
    except OSError as e:
        return "denied" if getattr(e, "errno", None) in (1, 13) else "missing"


def _copy_sqlite(src: Path, tmp: str) -> Path:
    dst = Path(tmp) / src.name
    shutil.copy2(src, dst)
    for ext in ("-wal", "-shm"):  # recent visits may still be in the write-ahead log
        side = src.with_name(src.name + ext)
        try:
            shutil.copy2(side, dst.with_name(dst.name + ext))
        except FileNotFoundError:
            pass
    return dst


def _read_rows(src: Path, sql: str, params: tuple) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        con = sqlite3.connect(_copy_sqlite(src, tmp))
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()


def _add_visits(rows, app_id: str) -> int:
    n = 0
    with E.DB_LOCK:
        d = E.db()
        try:
            for url, title, ms in rows:
                if not url or not url.startswith(("http://", "https://")):
                    continue
                title = title or url
                if mem.add_chunk(d, f"{title}\n{url}", app_id=app_id, title=title,
                                 url_host=urlparse(url).hostname, source="browser", captured_at=int(ms)):
                    n += 1
            d.commit()
        finally:
            d.close()
    return n


def import_safari(days: int) -> dict:
    access = safari_access()
    if access == "missing":
        return {"imported": 0, "status": "missing", "message": "Safari history not found."}
    if access == "denied":
        return {"imported": 0, "status": "needs_full_disk_access",
                "message": "macOS protects Safari history. Give Glint Full Disk Access in System Settings › "
                           "Privacy & Security › Full Disk Access, then try again."}
    cutoff = time.time() - days * 86400 - 978307200  # Safari stores seconds since 2001-01-01
    try:
        rows = _read_rows(safari_dir() / "History.db",
                          "SELECT i.url, v.title, MAX(v.visit_time) FROM history_items i "
                          "JOIN history_visits v ON v.history_item = i.id WHERE v.visit_time > ? "
                          "GROUP BY i.id", (cutoff,))
    except PermissionError:
        return {"imported": 0, "status": "needs_full_disk_access", "message": "Full Disk Access is required."}
    except sqlite3.DatabaseError as e:
        return {"imported": 0, "status": "error", "message": f"Couldn't read Safari history: {e}"}
    rows = [(u, t, (vt + 978307200) * 1000) for u, t, vt in rows]
    return {"imported": _add_visits(rows, "safari"), "pages": len(rows), "status": "ok"}


def import_firefox(days: int) -> dict:
    base = firefox_dir()
    profiles = sorted(base.glob("*/places.sqlite")) if base.exists() else []
    if not profiles:
        return {"imported": 0, "status": "missing", "message": "No Firefox profiles found."}
    cutoff_us = int((time.time() - days * 86400) * 1_000_000)
    total, pages, errors = 0, 0, []
    for places in profiles:
        try:
            rows = _read_rows(places, "SELECT p.url, p.title, MAX(v.visit_date) FROM moz_places p "
                                      "JOIN moz_historyvisits v ON v.place_id = p.id WHERE v.visit_date > ? "
                                      "GROUP BY p.id", (cutoff_us,))
        except (sqlite3.DatabaseError, OSError) as e:
            errors.append(f"{places.parent.name}: {e}")
            continue
        pages += len(rows)
        total += _add_visits([(u, t, vd // 1000) for u, t, vd in rows], "firefox")
    out = {"imported": total, "pages": pages, "profiles": len(profiles), "status": "ok"}
    if errors:
        out.update(status="partial" if total or pages else "error", message="; ".join(errors))
    return out


def import_chrome(days: int) -> dict:
    with E.DB_LOCK:
        d = E.db()
        try:
            return {"imported": mem.import_chromium(d, days), "status": "ok"}
        finally:
            d.close()


IMPORTERS = {"chrome": import_chrome, "safari": import_safari, "firefox": import_firefox}


def import_history(b: dict) -> dict:
    browsers = [x for x in (b.get("browsers") or ["chrome", "safari", "firefox"]) if x in IMPORTERS]
    if not browsers:
        raise ValueError("Pick at least one browser: chrome, safari or firefox.")
    days = max(1, min(365, int(b.get("days") or 14)))
    results = {}
    for name in browsers:
        try:
            results[name] = IMPORTERS[name](days)
        except Exception as e:
            results[name] = {"imported": 0, "status": "error", "message": str(e)}
    total = sum(r.get("imported", 0) for r in results.values())
    if total:
        threading.Thread(target=E._embed_now, daemon=True).start()
    return {"imported": total, "days": days, "results": results}


def import_status(q=None) -> dict:
    ff = firefox_dir()
    return {"safari": safari_access(),
            "firefox": "ok" if ff.exists() and any(ff.glob("*/places.sqlite")) else "missing",
            "chrome": "ok"}


# ------------------------------------------------------------ registration

def _context():
    s = E.load_settings()
    return f"[Role] The user describes their work as: {s['persona']}" if s["persona"] else None


def register():
    with E.DB_LOCK:
        _db().close()
    G, P = E.GET_ROUTES, E.POST_ROUTES
    G["/api/shell/onboarding"] = onboarding_state
    P["/api/shell/onboarding"] = set_onboarding
    G["/api/shell/permissions"] = permissions
    P["/api/shell/native"] = native_report
    G["/api/shell/home"] = home
    G["/api/shell/now"] = now_state
    G["/api/shell/nudges"] = nudges
    P["/api/shell/nudges"] = nudges  # native shell: {since} in the body (its request helper has no query string)
    P["/api/shell/nudge/dismiss"] = dismiss_nudge
    P["/api/insights/rate"] = rate_insight
    G["/api/insights/ratings"] = insight_ratings
    G["/api/skills"] = list_skills
    G["/api/skill"] = lambda q: find_skill(q.get("id", "")) or {"error": "not found"}
    G["/api/skill/markdown"] = lambda q: {"markdown": skill_markdown(find_skill(q.get("id", "")) or {})}
    P["/api/skills"] = save_skill
    P["/api/skill/delete"] = delete_skill
    P["/api/skills/import"] = import_skill
    P["/api/shell/automation_scan"] = automation_scan
    G["/api/shell/automation_ideas"] = lambda q: E.kv_get("shell:automation_ideas") or {"ideas": [], "at": None}
    P["/api/import/history"] = import_history
    G["/api/import/history/status"] = import_status

    opt = E.NULLABLE_STR
    E.register_tool(ai.Tool(
        "create_skill", "Save a reusable skill: markdown instructions the user can run later by typing its "
        "/trigger in Ask. Use when the user asks to save a workflow, prompt or procedure as a skill.",
        E._schema({"name": {"type": "string"}, "trigger": opt, "description": {"type": "string"},
                   "body": {"type": "string"}}), tool_create_skill))
    E.register_tool(ai.Tool(
        "update_skill", "Edit a saved skill (by id, /trigger or name). Null fields stay unchanged.",
        E._schema({"skill": {"type": "string"}, "name": opt, "trigger": opt, "description": opt, "body": opt}),
        tool_update_skill))
    E.register_tool(ai.Tool(
        "read_skill", "Read a saved skill's full markdown (by id, /trigger or name).",
        E._schema({"skill": {"type": "string"}}), tool_read_skill))
    E.CONTEXT_HOOKS.extend([_skills_context, _context, _ratings_context])
    E.apply_quick_action = _expand_skills(E.apply_quick_action)
    E.STARTUP_HOOKS.append(_migrate_existing_users)
    E.STARTUP_HOOKS.append(_monitor)
