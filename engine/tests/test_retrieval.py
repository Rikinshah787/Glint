"""Retrieval eval: a synthetic multi-day corpus (sessions + noisy captures + session summaries) and
labelled queries. Reports recall@5 and MRR for the baseline (all RETRIEVAL features off, i.e. the
original passage/title/semantic RRF) versus the current pipeline, plus a per-feature ablation.

    .venv/bin/python -W ignore engine/tests/test_retrieval.py [-v]

Exit code 1 if the current pipeline scores below the baseline on either metric.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ["GLINT_HOME"] = tempfile.mkdtemp(prefix="glint-retrieval-test-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory as mem  # noqa: E402

mem.MODEL_DIR = Path.home() / "Library" / "Application Support" / "Glint" / "models"  # reuse the cache
VERBOSE = "-v" in sys.argv
K = 5
H, D = 3_600_000, 86_400_000

now = mem.now_ms()
day0 = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
week0 = int((datetime.fromtimestamp(day0 / 1000) - timedelta(days=datetime.now().weekday())).timestamp() * 1000)


def at(base: int, hour: float) -> int:
    return min(base + int(hour * H), now - 5 * 60_000)  # never in the future


# ------------------------------------------------------------------------- corpus
LEDGER_THREAD = [
    "Priya Shah: webhook retry bug again: invoice.paid arrives twice when our handler times out",
    "You: so Stripe retries and we double-post the payment?",
    "Priya Shah: yes, we need idempotency keys on the webhook handler",
    "Priya Shah: blocked on the sandbox keys from finance to test it end to end",
    "You: I'll ping finance about the keys",
    "Priya Shah: thanks, meanwhile I'll stub the retry in staging",
    "You: retry reproduced on staging, two payments for one invoice",
    "Priya Shah: ok, adding the event-id table now",
    "You: reconciliation script flags 14 drifted invoices from the retry storm",
    "Priya Shah: those are all from the retry storm, safe to void",
    "You: voided, rerunning the reconciliation",
    "Priya Shah: looks clean now",
]

# Each session: id, start, summary doc fields, captures [(label, app, title, host, text)].
SESSIONS = [
    ("S01", at(week0 - 12 * D, 10), dict(
        title="Ledger kickoff", project="Project Ledger",
        summary="Kicked off Project Ledger: scoped moving invoicing off the legacy PHP biller onto Stripe "
                "Billing with Priya Shah; drafted owners and milestones.",
        insights=["Scope agreed early; data migration is the riskiest piece"],
        entities=["Project Ledger", "Priya Shah", "Stripe", "Google Docs"]), [
        ("kickoff", "google chrome", "Ledger kickoff notes - Google Docs", "docs.google.com",
         "Ledger kickoff notes\nScope: move invoicing off the legacy PHP biller onto Stripe Billing.\n"
         "Owners: Priya (backend + webhooks), me (data migration, reconciliation).\n"
         "Milestones: sandbox parity, dual-write, cutover. Risks: proration differences, tax lines."),
        (None, "slack", "#ledger - Acme Eng", None,
         "#ledger\nPriya Shah: created the channel for the biller replacement\nYou: added the kickoff doc"),
    ]),
    ("S02", at(week0 - 5 * D, 14), dict(
        title="Invoice reconciliation script", project="Project Ledger",
        summary="Wrote the invoice reconciliation script that diffs legacy invoices against Stripe, and "
                "debugged webhook retries with Priya Shah in #ledger.",
        insights=["Webhook retries double-post invoice.paid events"],
        entities=["Project Ledger", "Priya Shah", "Stripe", "invoice_reconcile.py", "VS Code"]), [
        ("recon_code", "code", "invoice_reconcile.py — ledger", None,
         "def reconcile(legacy_rows, stripe_invoices):\n    \"\"\"Match legacy invoices to Stripe by customer"
         " and period; report amount drift over 1 cent.\"\"\"\n    by_key = {(i.customer, i.period): i for i in"
         " stripe_invoices}\n    for row in legacy_rows:\n        inv = by_key.get((row.customer, row.period))"
         "\n        if inv is None or abs(inv.total - row.total) > 1: yield row, inv"),
    ] + [  # the channel recaptured as messages arrive: a sliding window of near-identical text
        ("slack_ledger", "slack", "#ledger - Acme Eng", None,
         "#ledger\n" + "\n".join(LEDGER_THREAD[i:i + 8])) for i in range(5)
    ]),
    ("S03", at(week0 - 3 * D, 11), dict(
        title="Ledger cutover planning", project="Project Ledger",
        summary="Planned the Ledger cutover with finance and triaged LED-142, the webhook retry storm on "
                "invoice.paid.",
        insights=["Go-live depends on finance sign-off"],
        entities=["Project Ledger", "Linear", "LED-142", "Finance team"]), [
        ("linear", "google chrome", "LED-142 Webhook retry storm on invoice.paid - Linear", "linear.app",
         "LED-142 Webhook retry storm on invoice.paid\nStatus: In Progress · Priority: Urgent · Assignee: Priya\n"
         "When the handler exceeds 10s Stripe redelivers the event; we create duplicate payments.\n"
         "Fix: store event ids, return 200 early, process async."),
        ("cutover", "google chrome", "Ledger cutover plan - Google Docs", "docs.google.com",
         "Ledger cutover plan\nGo-live moved to the 14th; finance signs off Monday.\n"
         "Freeze legacy biller writes at 18:00, run final reconciliation, flip the feature flag."),
    ]),
    ("S04", at(week0 - 5 * D, 9), dict(
        title="Daily standup", project="",
        summary="Morning standup notes: blocked on Stripe sandbox keys; Dana out Thursday.",
        insights=[], entities=["Apple Notes"]), [
        ("standup_old", "notes", "Standup notes", None,
         "Standup notes\nYesterday: reconciliation script draft\nToday: webhook idempotency\n"
         "Blockers: waiting on Stripe sandbox keys from finance; Dana out Thursday"),
    ]),
    ("S05", at(day0 - 1 * D, 9), dict(
        title="Daily standup", project="",
        summary="Morning standup notes: QA environment down; need Marcus to review onboarding mockups.",
        insights=[], entities=["Apple Notes"]), [
        ("standup_new", "notes", "Standup notes", None,
         "Standup notes\nYesterday: cutover rehearsal\nToday: onboarding copy pass\n"
         "Blockers: QA environment down since 8am; need Marcus to review onboarding mockups"),
    ]),
    ("S06", at(day0 - 1 * D, 14), dict(
        title="Onboarding mockup review", project="Onboarding revamp",
        summary="Design review of the Onboarding v3 mockups in Figma with Marcus Lee; agreed to move the "
                "workspace invite to the last step and shorten step 3 copy.",
        insights=["Invite step placement was the main debate"],
        entities=["Marcus Lee", "Onboarding revamp", "Figma", "Zoom"]), [
        ("figma", "figma", "Onboarding v3 – mockups", None,
         "Onboarding v3 – mockups\nComments\nMarcus Lee: step 3 copy is too long, cut it to one line\n"
         "Marcus Lee: move the workspace invite to the end of the flow\nYou: agreed, updating frame 12"),
        (None, "zoom.us", "Zoom Meeting", None, "Zoom Meeting\nMarcus Lee is presenting\nRecording"),
    ]),
    ("S07", at(day0 - 2 * D, 10), dict(
        title="Onboarding funnel analysis", project="Onboarding revamp",
        summary="Looked at the onboarding funnel in Amplitude after a Slack DM from Dana Kim: 40% of new "
                "users drop at step 3.",
        insights=["Step 3 is the main drop-off"],
        entities=["Dana Kim", "Onboarding revamp", "Amplitude", "Slack"]), [
        ("dkim_dm", "slack", "dkim (DM) - Acme Eng", None,
         "dkim: the onboarding flow drops 40% of new users at step 3 (workspace invite)\n"
         "dkim: can we A/B test skipping it? funnel link in Amplitude\nYou: yes, let's try it next sprint"),
        ("amplitude", "google chrome", "Onboarding funnel - Amplitude", "analytics.amplitude.com",
         "Onboarding funnel · last 30 days\nStep 1 Sign up 100%\nStep 2 Profile 81%\nStep 3 Invite 48%\n"
         "Step 4 First project 41%"),
        (None, "figma", "Onboarding v2 – current", None,
         "Onboarding v2 – current\nStep 3: Invite your team to the workspace (required)"),
    ]),
    ("S08", at(day0 - 6 * D, 15), dict(
        title="Acme renewal negotiation", project="Acme account",
        summary="Negotiated Acme Corp's 3-year renewal over email: they asked for 15% off; countered at 8% "
                "with annual prepay.",
        insights=[], entities=["Acme Corp", "Gmail"]), [
        ("acme", "google chrome", "Re: Acme renewal — pricing - Gmail", "mail.google.com",
         "Re: Acme renewal — pricing\nFrom: Jordan (Acme Corp procurement)\nWe'd like 15% off list for a "
         "3-year term.\nMy reply: we can do 8% with annual prepay and a 3-year commitment."),
    ]),
    ("S09", at(day0 - 9 * D, 16), dict(
        title="Vector database research", project="Semantic search",
        summary="Researched vector databases for semantic search: compared pgvector and Qdrant benchmarks "
                "and read about HNSW indexes.",
        insights=["pgvector is good enough under 5M rows"],
        entities=["pgvector", "Qdrant", "HNSW", "Semantic search"]), [
        ("pgvector", "google chrome", "pgvector vs Qdrant: benchmarks at 1M rows", "benchmarks.example.dev",
         "pgvector vs Qdrant: benchmarks at 1M rows\nRecall@10 0.95 at 30ms (pgvector, HNSW m=16) vs 12ms "
         "(Qdrant).\nIndex build time 9 min vs 4 min."),
        (None, "google chrome", "HNSW explained - Pinecone", "www.pinecone.io",
         "Hierarchical Navigable Small World graphs: layered proximity graphs with greedy search; "
         "ef_construction trades build time for recall."),
    ]),
    ("S10", at(day0 - 4 * D, 13), dict(
        title="Q3 roadmap draft", project="Q3 planning",
        summary="Drafted the Q3 Roadmap doc: ship Ledger, Onboarding v3, then SOC 2 prep.",
        insights=[], entities=["Q3 Roadmap", "Project Ledger", "Onboarding revamp", "SOC 2"]), [
        ("roadmap", "google chrome", "Q3 Roadmap - Google Docs", "docs.google.com",
         "Q3 Roadmap\nPriorities\n1. Ship Ledger (billing migration) by mid-quarter\n2. Onboarding v3 with "
         "invite step moved\n3. SOC 2 Type I readiness\nNon-goals: mobile app rewrite"),
    ]),
    ("S11", at(day0 - 15 * D, 20), dict(
        title="Lisbon offsite flights", project="Offsite",
        summary="Booked flights to Lisbon for the team offsite on Google Flights (TAP nonstop).",
        insights=[], entities=["Lisbon", "Google Flights", "TAP Air Portugal"]), [
        ("flights", "google chrome", "Flights to Lisbon - Google Flights", "www.google.com",
         "SFO to LIS nonstop TAP Air Portugal 11h 05m $742 round trip, departs Oct 3, returns Oct 12."),
    ]),
    ("S12", at(day0 - 1 * D, 17), dict(
        title="1:1 with Priya", project="",
        summary="1:1 with Priya Shah: promo packet due Friday; Ledger on track for the 14th.",
        insights=[], entities=["Priya Shah", "Project Ledger"]), [
        ("notes_new", "notes", "Meeting notes", None,
         "Meeting notes — 1:1 Priya\nPromo packet due Friday\nLedger on track; she owns webhook idempotency"),
    ]),
    ("S13", at(day0 - 8 * D, 11), dict(
        title="Vendor sync", project="",
        summary="Vendor sync meeting notes about the Acme SSO bug and invoice export format.",
        insights=[], entities=["Acme Corp"]), [
        ("notes_old", "notes", "Meeting notes", None,
         "Meeting notes — vendor sync\nAcme SSO bug: SAML clock skew\nInvoice export: they want CSV not PDF"),
    ]),
    ("S14", at(day0, 0) if now - day0 > 3 * H else now - 2 * H, dict(
        title="Postgres index rebuild", project="Infra",
        summary="Watched the Postgres index rebuild on the read replica and discussed the retry in #eng-backend.",
        insights=[], entities=["Postgres", "Slack"]), [
        ("pg", "slack", "#eng-backend - Acme Eng", None,
         "#eng-backend\nMarcus Lee: the Postgres index rebuild on the replica is at 70%\n"
         "You: let's retry the migration after it catches up"),
        (None, "terminal", "psql — replica", None, "REINDEX INDEX CONCURRENTLY invoices_customer_idx;\n"
         "NOTICE: index build 70% complete"),
    ]),
    # Noise: everyday distractors that share vocabulary with the queries.
    ("S15", at(day0 - 3 * D, 19), dict(
        title="Personal admin", project="",
        summary="Paid bills and skimmed newsletters.", insights=[], entities=["AWS"]), [
        (None, "google chrome", "AWS Billing alert - Gmail", "mail.google.com",
         "AWS Billing alert: your estimated charges for this month exceed $50. Review the billing dashboard."),
        (None, "google chrome", "Weekly product newsletter", "news.example.com",
         "This week in product: onboarding best practices, design review rituals, and roadmap templates."),
        (None, "google chrome", "Hacker News", "news.ycombinator.com",
         "Show HN: a tiny vector database in 500 lines\nAsk HN: how do you run standups?"),
    ]),
    ("S16", at(day0 - 10 * D, 10), dict(
        title="Recruiting", project="Hiring",
        summary="Reviewed candidate profiles for the backend role.", insights=[], entities=["Greenhouse"]), [
        (None, "google chrome", "Candidate: backend engineer - Greenhouse", "app.greenhouse.io",
         "Backend engineer candidate\nExperience: payments, Stripe integrations, Postgres\nNotes: strong on "
         "idempotency and retries; schedule design review interview"),
    ]),
]

ALIASES = {  # what the knowledge graph would register via mem.set_synonyms
    "e_ledger": ["Project Ledger", "Ledger", "billing migration", "biller replacement"],
    "e_priya": ["Priya Shah", "Priya", "pshah"],
    "e_dana": ["Dana Kim", "Dana", "dkim"],
    "e_marcus": ["Marcus Lee", "Marcus"],
    "e_acme": ["Acme Corp", "Acme"],
    "e_onb": ["Onboarding revamp", "Onboarding v3"],
}

QUERIES = [  # (query, [target group, ...]); a group is labels "c:<capture>" or "s:<session>"
    ("postgres index rebuild", [["c:pg"]]),
    ("what did I do on the billing project last week?", [["s:S02"], ["s:S03"]]),
    ("when is the billing migration go-live", [["c:cutover"]]),
    ("what did Dana Kim say about onboarding", [["c:dkim_dm"]]),
    ("standup blockers", [["c:standup_new"]]),
    ("webhook retry bug", [["c:slack_ledger"], ["c:linear"]]),
    ("flights to lisbon", [["c:flights"]]),
    ("design review with Marcus", [["s:S06"]]),
    ("Acme renewal pricing", [["c:acme"]]),
    ("what was I researching about vector databases", [["s:S09"]]),
    ("yesterday's meeting notes", [["c:notes_new"]]),
    ("invoice reconciliation script", [["c:recon_code"]]),
    ("Q3 roadmap priorities", [["c:roadmap"]]),
    ("what is pshah blocked on", [["s:S02", "s:S04"]]),
]


def build():
    db = mem.open_db()
    db.execute("ALTER TABLE memory_chunks ADD COLUMN session_id TEXT")
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at INTEGER)")
    labels: dict[str, set] = {}
    for sid, start, doc, caps in SESSIONS:
        db.execute("INSERT INTO sessions VALUES (?, ?)", (sid, start))
        for i, (label, app, title, host, text) in enumerate(caps):
            cid = mem.add_chunk(db, text, app, title, url_host=host, source="ax",
                                captured_at=start + i * 40_000)
            assert cid, (sid, i)
            db.execute("UPDATE memory_chunks SET session_id=? WHERE id=?", (sid, cid))
            if label:
                labels.setdefault("c:" + label, set()).add(cid)
        # Mirrors the graph feature: mentioned entities carry their known aliases.
        alias_of = {terms[0].lower(): terms[1:] for terms in ALIASES.values()}
        text = mem.compose_session_doc(doc["title"], doc["project"], doc["summary"], doc["insights"],
                                       [(n, alias_of.get(n.lower(), [])) for n in doc["entities"]])
        apps = " ".join(sorted({c[1] for c in caps}))
        mem.upsert_session_doc(db, sid, text, start, title=doc["title"], apps=apps, embed=False)
    for grp, terms in ALIASES.items():
        mem.set_synonyms(db, grp, terms)
    db.commit()
    mem.embed_pending(db, verbose=False)
    return db, labels


def evaluate(db, labels, flags):
    saved = dict(mem.RETRIEVAL)
    mem.RETRIEVAL.update(flags)
    rows = []
    try:
        for q, groups in QUERIES:
            res, _, notes = mem.search(db, q, limit=10)
            assert not notes, notes
            ids = [{r["chunk_id"], "s:" + str(r.get("session_id") or db.execute(
                "SELECT session_id FROM memory_chunks WHERE id=?", (r["chunk_id"],)).fetchone()[0])}
                for r in res]
            sets = [set().union(*(labels.get(t, {t}) for t in g)) for g in groups]
            hit_rank = [next((i + 1 for i, got in enumerate(ids) if got & s), None) for s in sets]
            recall = sum(1 for r in hit_rank if r and r <= K) / len(sets)
            first = min((r for r in hit_rank if r), default=None)
            kept, dups = [], 0  # near-duplicate captures of one window wasting top-10 slots
            for r in res:
                w = mem._wordset(r["passage"])
                dups += any(a == r["app_id"] and t == r["window_title"] and w and kw and
                            len(w & kw) / min(len(w), len(kw)) >= 0.8 for a, t, kw in kept)
                kept.append((r["app_id"], r["window_title"], w))
            rows.append((q, recall, 1 / first if first else 0.0, [r["window_title"] for r in res[:K]], dups))
    finally:
        mem.RETRIEVAL.clear()
        mem.RETRIEVAL.update(saved)
    return rows


def summary(rows):
    return sum(r[1] for r in rows) / len(rows), sum(r[2] for r in rows) / len(rows), sum(r[4] for r in rows)


if __name__ == "__main__":
    db, labels = build()
    off = {k: False for k in mem.RETRIEVAL}
    base = evaluate(db, labels, off)
    cur = evaluate(db, labels, {k: True for k in mem.RETRIEVAL})
    print(f"{'query':<52} {'R@5 before':>10} {'after':>6} {'RR before':>10} {'after':>6}")
    for (q, r0, m0, _, _), (_, r1, m1, top, _) in zip(base, cur):
        flag = "  +" if (r1, m1) > (r0, m0) else ("  -" if (r1, m1) < (r0, m0) else "")
        print(f"{q[:52]:<52} {r0:>10.2f} {r1:>6.2f} {m0:>10.2f} {m1:>6.2f}{flag}")
        if VERBOSE:
            print("      " + " | ".join(t[:28] for t in top))
    (rb, mb, db_), (ra, ma, da) = summary(base), summary(cur)
    print(f"\nbaseline : recall@{K} {rb:.3f}  MRR {mb:.3f}  near-dup results in top-10: {db_}")
    print(f"current  : recall@{K} {ra:.3f}  MRR {ma:.3f}  near-dup results in top-10: {da}")
    print("ablation (one feature off):")
    for feat in mem.RETRIEVAL:
        r, m, d = summary(evaluate(db, labels, {**{k: True for k in mem.RETRIEVAL}, feat: False}))
        print(f"  -{feat:<11} recall@{K} {r:.3f}  MRR {m:.3f}  near-dups {d}")
    ok = ra >= rb and ma >= mb and da <= db_
    print("PASS" if ok else "FAIL", "current >= baseline")
    sys.exit(0 if ok else 1)
