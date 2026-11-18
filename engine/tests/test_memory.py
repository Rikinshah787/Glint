"""Smoke test: synthetic memories -> keyword, semantic, hybrid, filters, dedup, forget."""
import os, sys, tempfile, time
from pathlib import Path

os.environ["GLINT_HOME"] = tempfile.mkdtemp(prefix="glint-memory-test-")
# Reuse the real model cache so the test doesn't re-download it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory as ol
ol.MODEL_DIR = Path.home() / "Library" / "Application Support" / "Glint" / "models"

db = ol.open_db()
H = 3_600_000
now = ol.now_ms()
docs = [
    ("slack", "#eng-backend", "Priya: the Postgres migration for the billing service is blocked on the index rebuild. "
     "We'll retry Thursday after the read replica catches up.", now - 2 * H),
    ("chrome", "Flights to Lisbon - Google Flights", "TAP Air Portugal SFO to LIS nonstop 11h 05m $742 round trip, "
     "departs Oct 3, returns Oct 12. United via Newark $689.", now - 30 * H),
    ("notes", "Dentist", "Dentist appointment moved to Friday 3:30pm with Dr. Alvarez. Bring insurance card.", now - 5 * H),
    ("code", "auth.ts — api", "export function verifyJwt(token: string) { // checks signature and expiry of the "
     "session token before any request reaches the handlers }", now - 1 * H),
    ("chrome", "Sourdough starter guide", "Feed the starter twice daily with equal weights of flour and water; "
     "it is ready to bake when it doubles within 6 hours.", now - 100 * H),
]
for app, title, text, ts in docs:
    assert ol.add_chunk(db, text, app, title, source="manual", captured_at=ts)
# an identical snapshot of the same window is skipped
assert ol.add_chunk(db, docs[0][2], "slack", "#eng-backend") is None
db.commit()
assert ol.embed_pending(db) == 5

def top(q, **kw):
    res, _, notes = ol.search(db, q, **kw)
    assert not notes, notes
    return [r["window_title"] for r in res]

checks = [
    ("keyword", top("postgres migration", mode="keyword")[0] == "#eng-backend"),
    ("semantic: 'trip to portugal airfare'", top("trip to portugal airfare", mode="semantic")[0].startswith("Flights")),
    ("semantic: 'login token validation code'", top("login token validation code", mode="semantic")[0].startswith("auth.ts")),
    ("hybrid: 'when is my teeth cleaning'", top("when is my teeth cleaning")[0] == "Dentist"),
    ("hybrid: 'bread baking'", top("bread baking")[0] == "Sourdough starter guide"),
    ("title match: 'google flights'", top("google flights", mode="keyword")[0].startswith("Flights")),
    ("since filter excludes old", "Sourdough starter guide" not in top("bread baking", since_hours=24)),
    ("app filter", all(t in ("Flights to Lisbon - Google Flights", "Sourdough starter guide")
                       for t in top("guide flights", app="chrome"))),
]
for name, ok in checks:
    print(("PASS " if ok else "FAIL ") + name)

# next_hash context expansion on a multi-passage chunk
long = "\n".join(f"Paragraph {i}: " + "lorem ipsum dolor sit amet " * 6 for i in range(6)) + "\nzebra crossing"
ol.add_chunk(db, long, "notes", "Long doc"); db.commit()
res, _, _ = ol.search(db, "Paragraph 2", mode="keyword")
exp = any(r["next_passage"] for r in res if r["window_title"] == "Long doc")
print(("PASS " if exp else "FAIL ") + "next_hash context expansion")
checks.append(("expand", exp))

ol.main(["forget", "--app", "slack"])
gone = "#eng-backend" not in top("postgres migration", mode="keyword")
print(("PASS " if gone else "FAIL ") + "forget removes chunks + passages + fts")
checks.append(("forget", gone))
sys.exit(0 if all(ok for _, ok in checks) else 1)
