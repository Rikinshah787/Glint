"""Assist: chord-driven drafting and proactive suggestion cards.

The native shell (mac/Assist.swift) detects the assist chord and reads the focused field; this
module writes drafts, decides whether a suggestion card is worth showing, validates card actions
before the shell applies them, keeps undo tokens and stores the history of everything offered.

Routes
  POST /api/assist/draft     {app, title, url_host?, field_text, instruction?, tweak?, draft_id?}
  POST /api/assist/card      {app, title, url_host?, force?}            -> {card} | {card: null}
  POST /api/assist/action    {card_id, action_id, app?, title?}         -> {result, undo_token?, ...}
  POST /api/assist/undo      {undo_token}                               -> {result, type?}
  POST /api/assist/outcome   {id, outcome}
  GET  /api/assist/history   ?kind=&outcome=&app=&q=&limit=
  POST /api/assist/history/delete {id?} (no id = everything)
  POST /api/assist/settings  validated subset of the assist_* settings
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime

import ai
import engine as E

CHORDS = {"cmd_opt": "Option + Command", "cmd_ctrl": "Control + Command"}
RESERVED_CHORDS = {"ctrl_opt": "Control + Option is used by Dictate"}

E.DEFAULT_SETTINGS.update({
    "assist_enabled": True,
    "assist_chord": "cmd_opt",
    "assist_cards_enabled": True,
    "assist_card_interval_s": 90,
    "assist_edge_panel": True,
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS assist_items (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,                 -- draft | card
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    app         TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    url_host    TEXT,
    field_text  TEXT NOT NULL DEFAULT '',
    instruction TEXT,
    tweak       TEXT,
    parent_id   TEXT,
    mode        TEXT,                          -- drafts: write | continue | rewrite
    card_kind   TEXT,
    headline    TEXT,
    body        TEXT NOT NULL DEFAULT '',      -- the draft text or the card body
    actions     TEXT NOT NULL DEFAULT '[]',
    expires_at  INTEGER,
    outcome     TEXT NOT NULL DEFAULT 'offered'
);
CREATE INDEX IF NOT EXISTS idx_assist_items_created ON assist_items(created_at DESC);
CREATE TABLE IF NOT EXISTS assist_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL,
    action_id   TEXT NOT NULL,
    type        TEXT NOT NULL,
    result      TEXT NOT NULL,
    undo_token  TEXT UNIQUE,
    created_at  INTEGER NOT NULL,
    undone_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_assist_actions_item ON assist_actions(item_id);
"""

CARD_TTL_MS = 10 * 60_000        # actions on an older card are stale
UNDO_TTL_MS = 60_000             # undo is offered for a minute
CARD_MIN_GAP_MS = 20_000         # never two cards closer than this
CARD_MAX_GAP_MS = 10 * 60_000    # back-off ceiling after repeated dismissals
SAME_SCREEN_MS = 5 * 60_000      # don't re-evaluate an unchanged screen within this window
OUTCOMES = {"offered", "shown", "drafted", "inserted", "applied", "copied", "opened", "dismissed",
            "undone", "stale", "failed", "ignored"}
NEVER_ASSIST = {"glint", "1password", "keychain access", "passwords", "bitwarden", "lastpass"}

_lock = threading.Lock()
_last_eval: dict[str, tuple[str, int]] = {}   # app -> (screen hash, at ms)


# ------------------------------------------------------------------ storage

def _db():
    d = E.db()
    d.executescript(SCHEMA)
    return d


def _query(sql: str, params=()) -> list[tuple]:
    with E.DB_LOCK:
        d = _db()
        rows = d.execute(sql, params).fetchall()
        d.close()
    return rows


def _exec(sql: str, params=()) -> int:
    with E.DB_LOCK:
        d = _db()
        n = d.execute(sql, params).rowcount
        d.commit()
        d.close()
    return n


ITEM_COLS = ("id", "kind", "created_at", "updated_at", "app", "title", "url_host", "field_text",
             "instruction", "tweak", "parent_id", "mode", "card_kind", "headline", "body", "actions",
             "expires_at", "outcome")


def _item(item_id: str) -> dict | None:
    rows = _query(f"SELECT {', '.join(ITEM_COLS)} FROM assist_items WHERE id=?", (item_id,))
    if not rows:
        return None
    it = dict(zip(ITEM_COLS, rows[0]))
    it["actions"] = json.loads(it["actions"] or "[]")
    return it


def _insert_item(**fields) -> str:
    iid = fields.pop("id", None) or str(uuid.uuid4())
    ts = E.now_ms()
    fields = {"created_at": ts, "updated_at": ts, **fields}
    if isinstance(fields.get("actions"), list):
        fields["actions"] = json.dumps(fields["actions"])
    cols = ["id", *fields]
    _exec(f"INSERT INTO assist_items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
          (iid, *fields.values()))
    return iid


def set_outcome(item_id: str, outcome: str, only_if: tuple[str, ...] | None = None) -> bool:
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown outcome '{outcome}'.")
    sql, params = "UPDATE assist_items SET outcome=?, updated_at=? WHERE id=?", [outcome, E.now_ms(), item_id]
    if only_if:
        sql += f" AND outcome IN ({', '.join('?' * len(only_if))})"
        params += list(only_if)
    return _exec(sql, params) > 0


# ------------------------------------------------------------------ context

def _clip(text: str, n: int, tail: bool = False) -> str:
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    if len(text) <= n:
        return text
    return "…" + text[-n:] if tail else text[:n] + "…"


def _screen(app: str, title: str) -> dict | None:
    """Latest captured text for this window (or, failing that, this app)."""
    rows = _query("SELECT app_id, window_title, url_host, captured_at, text FROM memory_chunks "
                  "WHERE lower(app_id)=lower(?) AND window_title=? AND source != 'title_only' "
                  "ORDER BY captured_at DESC LIMIT 1", (app, title)) if title else []
    if not rows:
        rows = _query("SELECT app_id, window_title, url_host, captured_at, text FROM memory_chunks "
                      "WHERE lower(app_id)=lower(?) AND source != 'title_only' "
                      "ORDER BY captured_at DESC LIMIT 1", (app,))
    if not rows:
        return None
    a, t, host, at, text = rows[0]
    return {"app": a, "title": t, "url_host": host, "at": at, "text": text}


def _memory_hits(query: str, limit: int = 6) -> list[str]:
    query = re.sub(r"\s+", " ", query or "").strip()[:300]
    if len(query) < 3:
        return []
    with E.DB_LOCK:
        d = E.db()
        try:
            try:
                results, _, _ = E.mem.search(d, query, None, None, limit)
            except Exception:
                results, _, _ = E.mem.search(d, query, None, None, limit, "keyword")
        except Exception as e:
            E.log(f"assist memory search skipped: {e}")
            results = []
        finally:
            d.close()
    return [f"- ({E.fmt_time(r['captured_at'])}) {r['app_id']}"
            f"{' · ' + r['url_host'] if r.get('url_host') else ''} — {r['window_title']}: "
            f"{_clip(r['passage'], 400)}" for r in results]


def _style(app: str, url_host: str | None) -> tuple[str, str]:
    s = E.load_settings()
    cat = E.style_category(app, url_host)
    style = s.get("dictation_styles", {}).get(cat, "formal")
    return cat, E.STYLE_RULES.get(style, E.STYLE_RULES["formal"])


def _voice_samples(app: str, n: int = 3) -> list[str]:
    """Drafts the user actually sent from this app recently — tone reference only."""
    rows = _query("SELECT body FROM assist_items WHERE kind='draft' AND outcome IN ('inserted','copied') "
                  "AND lower(app)=lower(?) ORDER BY updated_at DESC LIMIT ?", (app, n))
    return [_clip(r[0], 300) for r in rows if r[0]]


def _personal_context() -> str:
    parts = [f"Now: {datetime.now().strftime('%A %Y-%m-%d %H:%M')}"]
    bio, doc = E.kv_get("bio", ""), E.kv_get("memory_doc", "")
    if bio:
        parts.append(f"About the user:\n{_clip(bio, 2000)}")
    if doc:
        parts.append(f"The user's standing preferences and notes:\n{_clip(doc, 3000)}")
    return "\n\n".join(parts)


# ------------------------------------------------------------------ drafts

DRAFT_SYSTEM = """You write text that Glint will type into the field the user is focused on, in \
their voice. You see the field's current text, the window it is in (captured from their screen) and \
related things from their memory.

Choose a mode:
- "write": the field is empty. Write the whole message the situation calls for — usually a reply to \
the latest message addressed to the user on screen.
- "continue": the field has a sentence in progress. Write only the words that should follow it \
(start with a space if needed). If nothing useful should follow, return an empty text.
- "rewrite": the field holds rough notes, fragments or a draft the user asked to change. Return the \
finished message that replaces the whole field, keeping every point they made.

Rules:
- Sound like the user, follow the requested style, and keep it as short as the situation allows.
- Use only facts from the screen, the user's memory or the field itself. Never invent dates, numbers, \
names or commitments; leave a short [placeholder] when a fact is missing.
- No greetings or sign-offs unless the medium normally uses them (email does, chat usually doesn't).
- Output the text to insert only — no quotes, labels or commentary."""

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"mode": {"type": "string", "enum": ["write", "continue", "rewrite"]},
                   "text": {"type": "string"}},
    "required": ["mode", "text"], "additionalProperties": False,
}

TWEAKS = {
    "shorter": "Make it noticeably shorter — keep the essential point and drop the rest.",
    "formal": "Make it more formal and polished while keeping the meaning.",
    "casual": "Make it more relaxed and conversational.",
    "again": "Write a different version.",
}


def draft(b: dict) -> dict:
    s = E.load_settings()
    if not s["assist_enabled"]:
        raise ValueError("Assist is turned off in Settings.")
    app = (b.get("app") or "").strip()[:80]
    title = (b.get("title") or "").strip()[:300]
    field_text = (b.get("field_text") or "")[-8000:]
    instruction = (b.get("instruction") or "").strip()[:600] or None
    tweak = b.get("tweak") or None
    if tweak and tweak not in TWEAKS:
        raise ValueError("tweak must be shorter, formal, casual or again.")
    previous = _item(b["draft_id"]) if b.get("draft_id") else None
    if app.lower() in NEVER_ASSIST:
        raise ValueError("Assist doesn't draft in this app.")

    screen = _screen(app, title)
    url_host = b.get("url_host") or (screen or {}).get("url_host")
    cat, style_rule = _style(app, url_host)
    hits = _memory_hits(" ".join(x for x in (instruction, title, field_text[-200:]) if x))
    samples = _voice_samples(app)

    lines = [_personal_context(),
             f"App: {app or 'unknown'}{' · ' + url_host if url_host else ''} ({cat})",
             f"Window: {title or '(untitled)'}",
             f"Style: {style_rule}"]
    if samples:
        lines.append("Messages the user sent from this app before (tone reference only):\n"
                     + "\n".join(f"- {x}" for x in samples))
    if screen:
        lines.append(f"Window text captured {E.fmt_time(screen['at'])} (most recent part):\n"
                     f"{_clip(screen['text'], 6000, tail=True)}")
    if hits:
        lines.append("Related from the user's memory:\n" + "\n".join(hits))
    lines.append(f"Current field text:\n<<<\n{field_text}\n>>>" if field_text.strip()
                 else "Current field text: (empty)")
    if previous:
        lines.append(f"Your previous draft ({previous.get('mode') or 'write'}):\n<<<\n{previous['body']}\n>>>")
    if tweak:
        lines.append(f"Change request: {TWEAKS[tweak]} Keep the same mode unless it no longer fits.")
    if instruction:
        lines.append(f"The user's instruction: {instruction}")

    result = E.provider(s).complete_json(DRAFT_SYSTEM, "\n\n".join(lines), DRAFT_SCHEMA)
    mode = result.get("mode") if result.get("mode") in ("write", "continue", "rewrite") else "write"
    text = (result.get("text") or "").strip("\n")
    if mode != "continue":
        text = text.strip()
    if not field_text.strip() and mode != "write":
        mode = "write"
    did = _insert_item(kind="draft", app=app, title=title, url_host=url_host, field_text=field_text,
                       instruction=instruction, tweak=tweak, parent_id=previous["id"] if previous else None,
                       mode=mode, body=text, outcome="drafted")
    if previous and previous["outcome"] == "drafted":
        set_outcome(previous["id"], "dismissed", only_if=("drafted",))
    return {"id": did, "draft": text, "mode": mode, "app": app, "empty": not text.strip()}


# ------------------------------------------------------------------ cards

CARD_SYSTEM = """You decide whether to offer the user one small, concrete piece of help about the \
window they are looking at right now. Glint shows it as a quiet card they can act on with one click.

Good cards: a reply ready to insert for a message clearly waiting on the user; a fact, link or \
number from their memory that the window is asking about; an open task of theirs this window \
relates to. Every card must be specific to what is on screen and grounded in the provided text.

Stay quiet (useful=false) when the help would be generic, obvious, already on screen, about Glint \
itself, or when the window is a login, password, payment, private or idle screen. When in doubt, \
stay quiet — an unhelpful card costs more than a missing one.

When useful: kind is one of reply, fact, link, task, reminder; title is at most 8 words; body is \
1-3 short sentences saying what you noticed and why it matters. Give 1-2 actions:
- type "insert": value is the exact text to type into the focused field (replies, answers).
- type "copy": value is the exact text to copy.
- type "open_url": value is a full http(s) URL taken from the provided text.
Labels are 1-3 words (e.g. "Insert reply", "Copy number", "Open doc")."""

CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "useful": {"type": "boolean"},
        "kind": {"type": "string", "enum": ["reply", "fact", "link", "task", "reminder"]},
        "title": {"type": "string"},
        "body": {"type": "string"},
        "actions": {"type": "array", "items": {
            "type": "object",
            "properties": {"label": {"type": "string"},
                           "type": {"type": "string", "enum": ["insert", "copy", "open_url"]},
                           "value": {"type": "string"}},
            "required": ["label", "type", "value"], "additionalProperties": False}},
    },
    "required": ["useful", "kind", "title", "body", "actions"], "additionalProperties": False,
}


def _card_gap_ms() -> int:
    """Spacing grows with each card in a row the user dismissed or ignored."""
    rows = _query("SELECT outcome FROM assist_items WHERE kind='card' ORDER BY created_at DESC LIMIT 6")
    streak = 0
    for (o,) in rows:
        if o in ("dismissed", "ignored"):
            streak += 1
        elif o != "offered":
            break
    return min(CARD_MAX_GAP_MS, CARD_MIN_GAP_MS * (2 ** streak))


def _public_card(it: dict) -> dict:
    return {"id": it["id"], "kind": it["card_kind"], "title": it["headline"], "body": it["body"],
            "app": it["app"], "expires_at": it["expires_at"],
            "actions": [{"id": a["id"], "label": a["label"], "type": a["type"],
                         **({"preview": a["value"]} if a["type"] != "open_url" else {"url": a["value"]})}
                        for a in it["actions"]]}


def _quiet(reason: str) -> dict:
    return {"card": None, "reason": reason}


def card(b: dict) -> dict:
    s = E.load_settings()
    if not (s["assist_enabled"] and s["assist_cards_enabled"]):
        return _quiet("off")
    app = (b.get("app") or "").strip()[:80]
    title = (b.get("title") or "").strip()[:300]
    if not app:
        return _quiet("no_app")
    blocked = {x.lower() for x in s["blocked_apps"]} | NEVER_ASSIST | E.SELF_APPS
    if app.lower() in blocked or re.search(r"incognito|private browsing", title, re.I):
        return _quiet("private")
    if E.CAPTURER.paused_until and E.CAPTURER.paused_until > time.time():
        return _quiet("paused")
    force = bool(b.get("force"))
    now = E.now_ms()
    last = _query("SELECT created_at FROM assist_items WHERE kind='card' ORDER BY created_at DESC LIMIT 1")
    if not force and last and now - last[0][0] < _card_gap_ms():
        return _quiet("spacing")
    screen = _screen(app, title)
    if not screen or len((screen["text"] or "").strip()) < 40:
        return _quiet("no_text")
    digest = hashlib.sha1(f"{app}\n{title}\n{screen['text'][-4000:]}".encode()).hexdigest()
    with _lock:
        prev = _last_eval.get(app.lower())
        if not force and prev and prev[0] == digest and now - prev[1] < SAME_SCREEN_MS:
            return _quiet("unchanged")
        _last_eval[app.lower()] = (digest, now)

    tasks = _query("SELECT title, progress FROM tasks WHERE progress < 100 ORDER BY updated_at DESC LIMIT 8")
    hits = _memory_hits(title, limit=5)
    prompt = "\n\n".join(x for x in (
        _personal_context(),
        f"Foreground: {app}{' · ' + screen['url_host'] if screen.get('url_host') else ''} — {title or screen['title']}",
        f"Window text captured {E.fmt_time(screen['at'])} (most recent part):\n{_clip(screen['text'], 6000, tail=True)}",
        ("Open tasks:\n" + "\n".join(f"- {t} ({p}%)" for t, p in tasks)) if tasks else "",
        ("Related from memory:\n" + "\n".join(hits)) if hits else "",
    ) if x)
    try:
        r = E.provider(s).complete_json(CARD_SYSTEM, prompt, CARD_SCHEMA)
    except ai.ProviderError as e:
        E.log(f"assist card skipped: {e}")
        return _quiet("provider")
    if not r.get("useful"):
        return _quiet("nothing_useful")
    actions = []
    for a in (r.get("actions") or [])[:2]:
        value = (a.get("value") or "").strip()
        if not value or a.get("type") not in ("insert", "copy", "open_url"):
            continue
        if a["type"] == "open_url" and not re.match(r"^https?://\S+$", value):
            continue
        actions.append({"id": f"a{len(actions) + 1}", "label": _clip(a.get("label") or a["type"], 24),
                        "type": a["type"], "value": value[:4000]})
    headline, body = _clip(r.get("title"), 80), _clip(r.get("body"), 500)
    if not headline or not body:
        return _quiet("nothing_useful")
    cid = _insert_item(kind="card", app=app, title=title, url_host=screen.get("url_host"),
                       card_kind=r.get("kind") or "fact", headline=headline, body=body, actions=actions,
                       expires_at=now + CARD_TTL_MS, outcome="offered")
    return {"card": _public_card(_item(cid))}


# ------------------------------------------------------------------ actions + undo

def action(b: dict) -> dict:
    it = _item(b.get("card_id") or "")
    if not it or it["kind"] != "card":
        raise ValueError("Unknown card.")
    aid = b.get("action_id") or ""
    if aid == "dismiss":
        set_outcome(it["id"], "dismissed")
        return {"result": "dismissed"}
    act = next((a for a in it["actions"] if a["id"] == aid), None)
    if not act:
        raise ValueError("Unknown action.")
    now = E.now_ms()

    def record(result: str, undo: bool = False) -> dict:
        token = uuid.uuid4().hex if undo else None
        _exec("INSERT INTO assist_actions (item_id, action_id, type, result, undo_token, created_at) "
              "VALUES (?,?,?,?,?,?)", (it["id"], aid, act["type"], result, token, now))
        set_outcome(it["id"], result)
        out = {"result": result, "type": act["type"]}
        if token:
            out["undo_token"] = token
        return out

    if it["expires_at"] and now > it["expires_at"]:
        return record("stale")
    if act["type"] == "insert":
        current = (b.get("app") or "").strip()
        if current and it["app"] and current.lower() != it["app"].lower():
            return record("stale")  # the window changed since the card was made
        return {**record("applied", undo=True), "text": act["value"]}
    if act["type"] == "copy":
        return {**record("copied", undo=True), "text": act["value"]}
    if act["type"] == "open_url":
        if not re.match(r"^https?://\S+$", act["value"]):
            return record("failed")
        return {**record("opened"), "url": act["value"]}
    return record("failed")


def report(b: dict) -> dict:
    """The shell reports when an applied action failed natively (e.g. paste was blocked)."""
    token = b.get("undo_token")
    rows = _query("SELECT item_id FROM assist_actions WHERE undo_token=?", (token,)) if token else []
    if rows and b.get("result") == "failed":
        _exec("UPDATE assist_actions SET result='failed', undone_at=? WHERE undo_token=?", (E.now_ms(), token))
        set_outcome(rows[0][0], "failed")
    return {"ok": bool(rows)}


def undo(b: dict) -> dict:
    token = b.get("undo_token") or ""
    rows = _query("SELECT item_id, type, created_at, undone_at FROM assist_actions WHERE undo_token=?", (token,))
    if not rows:
        return {"result": "unavailable"}
    item_id, kind, at, undone = rows[0]
    if undone:
        return {"result": "unavailable"}
    if E.now_ms() - at > UNDO_TTL_MS:
        return {"result": "stale"}
    _exec("UPDATE assist_actions SET undone_at=? WHERE undo_token=?", (E.now_ms(), token))
    set_outcome(item_id, "undone")
    return {"result": "undone", "type": kind}


def outcome(b: dict) -> dict:
    iid, o = b.get("id") or "", b.get("outcome") or ""
    only = ("offered", "shown") if o in ("shown", "ignored") else None
    return {"ok": set_outcome(iid, o, only_if=only)}


# ------------------------------------------------------------------ history + settings

def history(q: dict) -> dict:
    where, params = ["1=1"], []
    if q.get("kind") in ("draft", "card"):
        where.append("kind=?")
        params.append(q["kind"])
    o = q.get("outcome")
    if o == "used":
        where.append("outcome IN ('inserted','applied','copied','opened')")
    elif o == "dismissed":
        where.append("outcome IN ('dismissed','ignored','undone')")
    elif o in OUTCOMES:
        where.append("outcome=?")
        params.append(o)
    if q.get("app"):
        where.append("lower(app)=lower(?)")
        params.append(q["app"])
    if q.get("q"):
        where.append("(body LIKE ? OR headline LIKE ? OR title LIKE ? OR instruction LIKE ?)")
        params += [f"%{q['q']}%"] * 4
    if q.get("since"):
        where.append("created_at >= ?")
        params.append(int(q["since"]))
    limit = min(max(int(q.get("limit") or 100), 1), 500)
    rows = _query(f"SELECT {', '.join(ITEM_COLS)} FROM assist_items WHERE {' AND '.join(where)} "
                  "ORDER BY created_at DESC LIMIT ?", (*params, limit))
    items = []
    for r in rows:
        it = dict(zip(ITEM_COLS, r))
        it["actions"] = [{k: v for k, v in a.items() if k != "value"} | {"preview": a.get("value", "")[:300]}
                         for a in json.loads(it["actions"] or "[]")]
        it["field_text"] = _clip(it["field_text"], 300, tail=True)
        items.append(it)
    lo, _, _ = E.day_bounds(None)
    counts = dict(_query("SELECT outcome, count(*) FROM assist_items WHERE created_at >= ? GROUP BY 1", (lo,)))
    apps = [a for (a,) in _query("SELECT DISTINCT app FROM assist_items WHERE app != '' ORDER BY app")]
    return {"items": items, "apps": apps, "today": counts}


def delete_history(b: dict) -> dict:
    if b.get("id"):
        n = _exec("DELETE FROM assist_items WHERE id=?", (b["id"],))
        _exec("DELETE FROM assist_actions WHERE item_id=?", (b["id"],))
    else:
        n = _exec("DELETE FROM assist_items")
        _exec("DELETE FROM assist_actions")
    return {"deleted": n}


def _validate_settings(patch: dict) -> dict:
    patch = dict(patch)
    if "assist_chord" in patch:
        c = patch["assist_chord"]
        if c in RESERVED_CHORDS:
            raise ValueError(f"{RESERVED_CHORDS[c]}; pick Option + Command or Control + Command.")
        if c not in CHORDS:
            raise ValueError("assist_chord must be cmd_opt or cmd_ctrl.")
    if "assist_card_interval_s" in patch:
        patch["assist_card_interval_s"] = max(20, min(3600, int(patch["assist_card_interval_s"])))
    for k in ("assist_enabled", "assist_cards_enabled", "assist_edge_panel"):
        if k in patch:
            patch[k] = bool(patch[k])
    return patch


def save_settings(b: dict) -> dict:
    patch = _validate_settings({k: v for k, v in b.items() if k.startswith("assist_")})
    return {"settings": E.save_settings(patch), "chords": CHORDS}


def register():
    with E.DB_LOCK:
        d = _db()
        d.close()
    # Guard the shared settings route too, so the reserved Dictate chord can never be saved.
    core_settings = E.POST_ROUTES.get("/api/settings")

    def guarded_settings(b):
        return core_settings(_validate_settings(b))

    if core_settings:
        E.POST_ROUTES["/api/settings"] = guarded_settings
    E.POST_ROUTES.update({
        "/api/assist/draft": draft,
        "/api/assist/card": card,
        "/api/assist/action": action,
        "/api/assist/action/report": report,
        "/api/assist/undo": undo,
        "/api/assist/outcome": outcome,
        "/api/assist/history/delete": delete_history,
        "/api/assist/settings": save_settings,
    })
    E.GET_ROUTES.update({
        "/api/assist/history": history,
        "/api/assist/config": lambda q: {"chords": CHORDS, "reserved": RESERVED_CHORDS,
                                         "settings": {k: v for k, v in E.load_settings().items()
                                                      if k.startswith("assist_")}},
    })
