#!/usr/bin/env python3
"""Glint demo mode: a fictional week of work, so anyone can try the UI without granting a permission.

    python engine/demo.py seed  --home DIR [--now 2026-11-18T16:30] [--no-embed]
    python engine/demo.py serve --home DIR [--port 47408] [--mcp-port 47409] [--live]

`seed` fills DIR with a deterministic week for "Maya Chen", a product manager at the fictional
company Northwind: captures with window text, summarised work sessions with scores and insights,
a knowledge graph, tasks, chats (one with a chart), skills, Assist history and autocomplete stats.
It refuses Glint's real data folder and any folder that holds non-demo data, and re-seeding a demo
folder replaces it. Timestamps are relative to now (today's sessions stop before the current time).

`serve` runs the normal engine on DIR with capture off, the Keychain replaced by an in-memory store
(nothing is read from or written to your real Keychain) and the importers that read real local data
(browser history, other assistants' memories, Google sign-in) disabled. scripts/demo.sh wraps both.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REAL_HOME = Path.home() / "Library" / "Application Support" / "Glint"
DEMO_KEY = "demo:seeded"
SEED = 46


# ------------------------------------------------------------------ safety

def _guard_home(home: Path) -> Path:
    home = home.expanduser().resolve()
    real = REAL_HOME.resolve()
    if home == real or real in home.parents:
        sys.exit(f"demo: refusing to use Glint's real data folder ({home}). Pass a temporary folder.")
    db = home / "memory.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            row = con.execute("SELECT value FROM kv WHERE key=?", (DEMO_KEY,)).fetchone()
            con.close()
        except sqlite3.Error:
            row = None
        if not row:
            sys.exit(f"demo: {db} exists and is not demo data. Pick an empty folder.")
    return home


def _wipe(home: Path) -> None:
    """Remove a previous demo seed (only called after _guard_home confirmed it is demo data)."""
    for name in ("memory.db", "memory.db-wal", "memory.db-shm", "settings.json"):
        (home / name).unlink(missing_ok=True)
    shutil.rmtree(home / "icons", ignore_errors=True)


def _import_engine(home: Path):
    os.environ["GLINT_HOME"] = str(home)
    os.environ["GLINT_NO_CAPTURE"] = "1"
    sys.path.insert(0, str(ROOT))
    import engine as E  # noqa: E402  (reads GLINT_HOME at import time)

    vault: dict[str, str] = {}   # never touch the real Keychain from a demo

    def keychain_get(account):
        return vault.get(account)

    def keychain_set(account, secret):
        if secret:
            vault[account] = secret
        else:
            vault.pop(account, None)

    E.keychain_get, E.keychain_set = keychain_get, keychain_set
    return E


# ------------------------------------------------------------------ seeding

def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class Seeder:
    def __init__(self, E, now: datetime, embed: bool):
        import demo_data as D
        self.E, self.D, self.mem = E, D, E.mem
        self.now, self.embed = now, embed
        self.rng = random.Random(SEED)
        self.today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.counts = {"captures": 0, "sessions": 0}
        self.sessions: list[dict] = []

    def uid(self) -> str:
        return str(uuid.UUID(int=self.rng.getrandbits(128), version=4))

    def at(self, day: int, hhmm: str) -> datetime:
        h, m = map(int, hhmm.split(":"))
        return self.today + timedelta(days=day, hours=h, minutes=m)

    # -- plan: concrete start/end per session, today's clipped to the current time
    def plan(self) -> list[tuple]:
        out = []
        for day, items in sorted(self.D.DAYS.items()):
            cursor = None
            day_rows = []
            for key, hhmm in items:
                tpl = self.D.CATCHUPS[key[1]] if isinstance(key, tuple) else self.D.T[key]
                start = self.at(day, hhmm)
                if cursor and start < cursor:
                    start = cursor
                minutes = sum(b[2] for b in tpl["beats"])
                end = start + timedelta(minutes=minutes)
                day_rows.append([tpl, start, end])
                cursor = end + timedelta(minutes=6)
            if day == 0 and day_rows:
                # Shift today's schedule back if it would run past now; drop what lands before 6am.
                limit = self.now - timedelta(minutes=8)
                shift = min(timedelta(0), limit - day_rows[-1][2])
                floor = self.today + timedelta(hours=6)
                day_rows = [[t, s + shift, e + shift] for t, s, e in day_rows if s + shift >= floor]
            out += [tuple(r) for r in day_rows]
        return out

    def run(self) -> dict:
        E = self.E
        t0 = time.time()
        E.init_db()
        E.load_features()
        mods = {name: sys.modules.get(name) for name in ("graph", "shell", "assist", "autocomplete", "connectors")}
        with E.DB_LOCK:
            d = E.db()
            for m in mods.values():
                schema = getattr(m, "SCHEMA_SQL", None) or getattr(m, "SCHEMA", None)
                if isinstance(schema, str):
                    d.executescript(schema)
            for tpl, start, end in self.plan():
                self.add_session(d, tpl, start, end)
            d.commit()
            self.add_tasks(d)
            self.add_threads(d)
            self.add_extras(d, mods)
            d.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)",
                      (DEMO_KEY, json.dumps({"at": E.now_ms(), "seed": SEED, "person": "Maya Chen (fictional)"})))
            d.commit()
            d.close()
        graph = mods.get("graph")
        if graph:
            with graph._Db() as gd:
                for s in self.sessions:
                    graph.index_session(gd, s["id"], embed=False)
        if self.embed:
            with E.DB_LOCK:
                d = E.db()
                self.mem.embed_pending(d, verbose=False)
                d.close()
        E.save_settings({"onboarding_done": True, "persona": "Product or design", "theme": "system",
                         "provider": "auto", "summarize_sessions": False, "capture_enabled": True,
                         "autocomplete_enabled": True})
        return {**self.counts, "seconds": round(time.time() - t0, 1)}

    # -- one session: captures (progressively revealed window text) + summary + raw entities
    def add_session(self, d, tpl, start: datetime, end: datetime) -> None:
        mem, sid = self.mem, self.uid()
        apps: dict[str, float] = {}
        t = start
        for app, title, minutes, text, host in tpl["beats"]:
            beat_end = t + timedelta(minutes=minutes)
            apps[app] = apps.get(app, 0) + minutes * 60.0
            # ~every 40 s (< the 60 s gap the timeline merges across), as with a focused window.
            n = max(2, int(minutes * 60 / 40))
            prev = 0
            for i in range(n):
                ts = t + timedelta(seconds=(minutes * 60) * i / n + self.rng.uniform(0, 6))
                if ts >= beat_end:
                    break
                # The text grows a little each time (typing, scrolling, a chat filling up), so every
                # snapshot differs and is stored, like a real focused window.
                k = max(prev + 1, int(len(text) * (0.45 + 0.55 * (i + 1) / n)), 40)
                if k > len(text) and prev >= len(text):
                    continue
                body, prev = text[:k], k
                cid = mem.add_chunk(d, body, app_id=app.lower(), title=title, url_host=host,
                                    source="ax", captured_at=_ms(ts))
                if cid:
                    d.execute("UPDATE memory_chunks SET session_id=? WHERE id=?", (sid, cid))
                    self.counts["captures"] += 1
            t = beat_end
        q = tpl["quality"]
        overall = round(sum(q[k] for k in ("focus", "depth", "consistency", "recovery")) / 4)
        d.execute("INSERT INTO sessions (id, started_at, ended_at, last_seen, apps, title, summary, state_tag, "
                  "focus_score, insights, state_name, quality, project, status) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'summarized')",
                  (sid, _ms(start), _ms(end), _ms(end), json.dumps(apps), tpl["title"], tpl["summary"],
                   tpl["state"], overall, json.dumps(tpl["insights"]), tpl["state_name"], json.dumps(q),
                   tpl.get("project")))
        d.execute("CREATE TABLE IF NOT EXISTS graph_session_state (session_id TEXT PRIMARY KEY, raw TEXT, "
                  "source TEXT NOT NULL, updated_at INTEGER NOT NULL)")
        d.execute("INSERT OR REPLACE INTO graph_session_state VALUES (?,?,?,?)",
                  (sid, json.dumps(tpl.get("entities", [])), "summary", _ms(end)))
        self.sessions.append({"id": sid, "start": start, "end": end, "title": tpl["title"],
                              "project": tpl.get("project"), "seconds": sum(apps.values())})
        self.counts["sessions"] += 1

    def ago(self, days: float, hour: int | None = None) -> int:
        dt = self.today - timedelta(days=days)
        dt = dt + timedelta(hours=hour) if hour is not None else self.now - timedelta(days=days)
        return _ms(min(dt, self.now - timedelta(minutes=5)))

    def add_tasks(self, d) -> None:
        for title, progress, created, logs in self.D.TASKS:
            entries = [{"at": self.ago(a, h), "text": txt} for a, h, txt in logs]
            updated = max([e["at"] for e in entries] or [self.ago(created, 9)])
            d.execute("INSERT INTO tasks (id, title, status, notes, created_at, updated_at, progress, logs) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (self.uid(), title, "done" if progress >= 100 else "open", "", self.ago(created, 9),
                       updated, progress, json.dumps(entries)))

    def project_hours(self) -> tuple[list[str], list[float]]:
        hours: dict[str, float] = {}
        for s in self.sessions:
            key = s["project"] or "Other"
            hours[key] = hours.get(key, 0) + s["seconds"] / 3600
        order = sorted(hours, key=lambda k: (k == "Other", -hours[k]))
        return order, [round(hours[k], 1) for k in order]

    def thread(self, d, title: str, at: int, turns: list[tuple]) -> None:
        tid = self.uid()
        d.execute("INSERT INTO chat_threads (id, title, created_at, updated_at, renamed) VALUES (?,?,?,?,0)",
                  (tid, title, at, at + 60_000 * len(turns)))
        for i, (role, content, tools) in enumerate(turns):
            d.execute("INSERT INTO chat_messages (thread_id, role, content, tools, created_at) VALUES (?,?,?,?,?)",
                      (tid, role, content, json.dumps(tools or []), at + i * 30_000))

    def add_threads(self, d) -> None:
        labels, values = self.project_hours()
        total = round(sum(values), 1)
        top = labels[0]
        chart = {"type": "chart", "chart_type": "bar", "title": "Hours by project, last 7 days",
                 "subtitle": f"{total} tracked hours across {len(self.sessions)} work sessions", "unit": "h",
                 "labels": labels, "series": [{"name": "Hours", "values": values}]}
        self.thread(d, "Hours by project this week", self.ago(0, 12), [
            ("user", "Chart where my time went this week, by project", None),
            ("assistant",
             f"Here's your week by project. **{top}** took the most time ({values[0]}h), mostly the proration "
             f"fix, the launch checklist and the go/no-go prep.\n\n"
             + "\n".join(f"- **{k}**: {v}h" for k, v in zip(labels, values))
             + "\n\nMeetings were concentrated on Tuesday and Thursday; your longest focus blocks were "
               "the PRD review and the roadmap deck.",
             [{"name": "get_today_activity", "input": {}},
              {"name": "search_memory", "input": {"query": "work sessions this week by project"}},
              {"name": "create_chart", "input": {"title": chart["title"], "chart_type": "bar"}},
              {"event": "visual", "payload": chart}]),
        ])
        self.thread(d, "Billing v2 launch status", self.ago(0, 11), [
            ("user", "Where are we on the Billing v2 launch? Anything still blocking?", None),
            ("assistant",
             "**Billing v2 is ready for go/no-go.** All 12 launch criteria are done as of this morning.\n\n"
             "- **BIL-142 (proration)**: you verified the fix on staging today: a 12 → 20 seat upgrade "
             "produced one prorated line item of $412.90.\n"
             "- **Finance**: Aisha signed off on the October reconciliation (difference: $38, rounding).\n"
             "- **Support**: Tom's macros are live and all 9 affected accounts were credited.\n"
             "- **Go/no-go**: Thursday 10:00. Your doc recommends GO for new self-serve signups on Monday.\n\n"
             "Nothing is blocking. One open question from the Bluefield interview: whether usage CSV export "
             "belongs in phase 1.",
             [{"name": "search_memory", "input": {"query": "Billing v2 launch blockers"}},
              {"name": "get_recent_context", "input": {"since_hours": 24}},
              {"name": "list_tasks", "input": {}}]),
        ])
        self.thread(d, "Bluefield interview takeaways", self.ago(4, 15), [
            ("user", "What did Bluefield Health say about invoices?", None),
            ("assistant",
             "From your interview with **Ana Lima** and **Marco Silva** (Bluefield Health):\n\n"
             "- They reconcile every invoice line by line against usage in their own sheet.\n"
             "- Mid-month seat upgrades showed **two charges** (the same bug Harbor & Pine hit, BIL-142).\n"
             "- They want a **daily usage CSV export** and an email at **80% of their tier**.\n"
             "- They'd move to usage-based pricing if invoices are \"explainable to our CFO\".\n\n"
             "Follow-ups you noted: send the Billing v2 invoice mockups and invite them to the beta.",
             [{"name": "search_memory", "input": {"query": "Bluefield Health interview invoices"}}]),
        ])
        self.thread(d, "Standup for today", self.ago(0, 9), [
            ("user", "/standup", None),
            ("assistant",
             "**Yesterday**\n- Billing v2: launch checklist at 11/12; go/no-go email drafted for Lena\n"
             "- Wrote and posted the week 46 update in #product\n\n"
             "**Today**\n- Billing v2: verify the BIL-142 fix on staging, then prep go/no-go\n"
             "- Q3 Planning: finalize capacity numbers and send to eng leads\n\n"
             "**Blockers**\n- None",
             [{"name": "read_skill", "input": {"name": "standup"}},
              {"name": "get_today_activity", "input": {}}]),
        ])

    def add_extras(self, d, mods) -> None:
        E, now = self.E, self.E.now_ms()
        kv = {"bio": self.D.BIO, "memory_doc": self.D.MEMORY_DOC,
              "activity:last": {"app": "Notion", "title": "Billing v2 — Go/no-go summary", "at": now - 600_000}}
        if mods.get("autocomplete"):
            days = [(self.today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
            kv["autocomplete:stats"] = {"shown": 412, "accepted": 163, "accepted_chars": 7480, "dismissed": 88,
                                        "ignored": 161, "days": days, "since": self.ago(6, 9),
                                        "apps": {"slack": 71, "mail": 38, "notion": 29, "docs.google.com": 17,
                                                 "linear": 8}}
            samples = [("slack", "Thanks both. Let's keep the launch date for now and decide at go/no-go on Thursday."),
                       ("slack", "Love it. Ship it to the experiment branch and let's look at the numbers Friday."),
                       ("mail", "Happy to review the next version Thursday. Let me know if anything changes before then."),
                       ("notion", "Recommendation: move invites to the post-setup checklist; promote Stripe connect."),
                       ("linear", "Verified on staging. Upgrade 12 to 20: single prorated item, matches expected.")]
            for i, (app, text) in enumerate(samples):
                d.execute("INSERT INTO autocomplete_samples (app, text, created_at) VALUES (?,?,?)",
                          (app, text, self.ago(5 - i, 10)))
        for k, v in kv.items():
            d.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (k, json.dumps(v)))
        if mods.get("shell"):
            for i, (name, trigger, desc, body) in enumerate(self.D.SKILLS):
                d.execute("INSERT INTO skills (id, name, trigger, description, body, uses, created_at, updated_at) "
                          "VALUES (?,?,?,?,?,?,?,?)",
                          (self.uid(), name, trigger, desc, body, [9, 3, 2][i], self.ago(6, 9), self.ago(1, 9)))
            first = self.sessions[1]["id"] if len(self.sessions) > 1 else None
            if first:
                d.execute("INSERT OR REPLACE INTO insight_ratings VALUES (?,?,?,?,?,?)",
                          (first, 0, "up", None, "Roadmap and capacity numbers live in different places", self.ago(5, 11)))
        if mods.get("assist"):
            items = [
                ("draft", MAIL_APP, "Re: Seat upgrade question", "write", None,
                 "reply: fix ships with Billing v2 this week, confirm date after go/no-go",
                 "Hi Ana, thanks for flagging this. The fix for prorated upgrades ships with Billing v2 this week. "
                 "I'll confirm the exact date after our go/no-go on Thursday.\nMaya", "inserted", 0),
                ("draft", "Slack", "#billing-v2 — Northwind", "continue", None, None,
                 "Summary for BIL-142: fix = update quantity with prorations; downgrades at next cycle; "
                 "auto-credit the 9 accounts.", "inserted", 4),
                ("card", "Linear", "BIL-142 Proration double-charges on mid-cycle upgrades", None, "follow_up", None,
                 "Tom attached affected-accounts.csv. Want a task to credit the 9 accounts before launch?",
                 "applied", 4),
                ("card", "Notion", "Q3 Roadmap (draft)", None, "link", None,
                 "Your Q3 capacity plan has newer numbers (Data: 4 free weeks). Link it here?", "dismissed", 5),
            ]
            for kind, app, title, mode, card_kind, instr, body, outcome, days in items:
                ts = self.ago(days, 10 + len(body) % 6)
                d.execute("INSERT INTO assist_items (id, kind, created_at, updated_at, app, title, field_text, "
                          "instruction, mode, card_kind, headline, body, actions, outcome) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (self.uid(), kind, ts, ts, app, title, "", instr, mode, card_kind,
                           body.split(".")[0][:80] if kind == "card" else None, body, "[]", outcome))
        for value, alias in (("Northwind", None), ("Priya Raman", "Priya"), ("BIL-142", "bill one forty two"),
                             ("Bluefield Health", "blue field"), ("go/no-go", "go no go")):
            d.execute("INSERT OR IGNORE INTO dictation_dictionary (value, alias, source, created_at, updated_at) "
                      "VALUES (?,?,?,?,?)", (value, alias, "manual", now, now))
        if mods.get("connectors"):
            d.execute("INSERT INTO connector_mcp_clients (id, name, token_hash, token_hint, created_at, last_used_at) "
                      "VALUES (?,?,?,?,?,?)",
                      (self.uid(), "Claude Desktop", uuid.UUID(int=self.rng.getrandbits(128)).hex * 2, "x7Qk",
                       self.ago(5, 10), self.ago(0, 11)))


MAIL_APP = "Mail"


def seed(home: Path, now: datetime | None, embed: bool) -> dict:
    home = _guard_home(home)
    home.mkdir(parents=True, exist_ok=True)
    _wipe(home)
    E = _import_engine(home)
    return Seeder(E, now or datetime.now(), embed).run()


# ------------------------------------------------------------------ serving

BLOCKED = ("/api/import/browser", "/api/import/history", "/api/connectors/memory_import/local",
           "/api/connectors/google/connect", "/api/connectors/google/open_console")


def serve(home: Path, port: int, mcp_port: int | None, live: bool) -> None:
    home = _guard_home(home)
    if not (home / "memory.db").exists():
        sys.exit(f"demo: {home} has no demo data yet. Run: python engine/demo.py seed --home {home}")
    os.environ["GLINT_PORT"] = str(port)
    E = _import_engine(home)
    # Feature settings (MCP, onboarding) only exist once features are loaded, so apply them then.
    patch = {"onboarding_done": True, "mcp_enabled": bool(mcp_port)}
    if mcp_port:
        patch["mcp_port"] = mcp_port

    def demo_only(path):
        def refuse(_arg=None):
            raise ValueError("Disabled in demo mode: it would read data from this Mac.")
        return refuse

    original = E.load_features

    def load_features():
        original()
        E.save_settings(patch)
        for path in BLOCKED:
            for table in (E.GET_ROUTES, E.POST_ROUTES):
                if path in table:
                    table[path] = demo_only(path)
        if live:  # screenshot mode: show what the UI looks like while capturing
            E.CAPTURER.status.update(state="capturing", app="Notion", title="Billing v2 — Go/no-go summary",
                                     at=E.now_ms(), ax_trusted=True, trigger="window_focus")
        else:
            E.CAPTURER.status.update(state="demo", note="Demo data (fictional). Capture is off.")
        E.log("DEMO MODE: fictional data, capture off, Keychain not used")

    E.load_features = load_features
    E.main()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="fill a folder with the fictional demo week")
    s.add_argument("--home", required=True, type=Path)
    s.add_argument("--now", help="pretend the current time is this (ISO, e.g. 2026-11-18T16:30)")
    s.add_argument("--no-embed", action="store_true", help="skip local embeddings (keyword search only)")
    v = sub.add_parser("serve", help="run the engine on a demo folder (capture off, no Keychain)")
    v.add_argument("--home", required=True, type=Path)
    v.add_argument("--port", type=int, default=47408)
    v.add_argument("--mcp-port", type=int, default=None, help="also start the MCP server on this port")
    v.add_argument("--live", action="store_true", help="show a 'capturing' status (for screenshots)")
    a = ap.parse_args(argv)
    if a.cmd == "seed":
        r = seed(a.home, datetime.fromisoformat(a.now) if a.now else None, not a.no_embed)
        print(json.dumps({"ok": True, "home": str(a.home.expanduser().resolve()), **r}))
    else:
        serve(a.home, a.port, a.mcp_port, a.live)


if __name__ == "__main__":
    main()
