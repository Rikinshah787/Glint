"""Autocomplete eval: realistic typing scenarios run against Apple's on-device model, scored by rules.

Each scenario seeds a temporary memory (the window the user is typing in, older captures that hold
facts, the user's past writing in that app) and asks for a continuation of the text before the
caret. The previous prompt (app + title + samples + typed text) is the baseline; the current one
adds the conversation on screen, matching memory passages and a cleaned context label.

Rules per suggestion: non-empty, does not repeat the typed text, at most 12 words, joins the typed
text with the right spacing, adds no names or numbers that appear nowhere in the context, and uses
the expected fact when the scenario has one. A scenario passes when every rule holds.

    .venv/bin/python -W ignore engine/tests/test_autocomplete_eval.py [-v]

Skips (exit 0) when the on-device model is unavailable (CI, older macOS, Intel, Apple Intelligence
off) or GLINT_SKIP_AC_EVAL=1. Exit code 1 if the current prompt scores below the baseline.
"""
import importlib.util
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

os.environ["GLINT_HOME"] = tempfile.mkdtemp(prefix="glint-ac-eval-")
ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
VERBOSE = "-v" in sys.argv

if os.environ.get("GLINT_SKIP_AC_EVAL") == "1":
    print("autocomplete eval: skipped (GLINT_SKIP_AC_EVAL=1)")
    sys.exit(0)

import ai  # noqa: E402

if sys.platform != "darwin" or not ai.apple_available():
    print(f"autocomplete eval: skipped (on-device model unavailable: {ai._AppleServer.reason or 'not macOS'})")
    sys.exit(0)

import engine as E  # noqa: E402

E.api_keys = lambda: {}  # never read the Keychain; the eval is on-device only
spec = importlib.util.spec_from_file_location("ac_feature", ENGINE / "features" / "autocomplete.py")
ac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ac)
E.init_db()
E.save_settings({"autocomplete_model_mode": "on_device", "autocomplete_min_chars": 1})

H = 3_600_000
NOW = E.now_ms()

# --------------------------------------------------------------- scenarios
# window: text captured from the window being typed in (None: nothing captured)
# facts: older captures elsewhere [(app, title, host, text, hours_ago)]
# samples: the user's own past writing in this app
# expect: any of these must appear in the suggestion (None: no particular fact expected)
S = []


def sc(id, app, title, before, host=None, window=None, facts=(), samples=(), expect=None, midword=False,
       after=""):
    S.append(dict(id=id, app=app, title=title, host=host, before=before, window=window, facts=list(facts),
                  samples=list(samples), expect=expect, midword=midword, after=after))


GMAIL = ("Google Chrome", "mail.google.com")
sc("gmail_move_sync", GMAIL[0], "Re: Sync this week - sam.k@acme.co - Gmail - Google Chrome", host=GMAIL[1],
   window="Re: Sync this week\nDana Lee <dana@acme.co>\nHi Sam, can we move our sync to Thursday at 3pm? "
          "The vendor review ran over.\nThanks, Dana\nReply Forward",
   before="Hi Dana,\n\nThursday works for me, see you", expect=["3pm", "3 pm", "3:00", "at 3"])
sc("gmail_send_budget", GMAIL[0], "Q3 budget draft - sam.k@acme.co - Gmail - Google Chrome", host=GMAIL[1],
   window="Q3 budget draft\nPriya Raman <priya@acme.co>\nHi Sam, could you send me the Q3 budget draft before "
          "Friday? Mainly the vendor costs section.\nPriya",
   before="Hi Priya, sure, I'll send over the", expect=["budget", "vendor"])
sc("slack_push_launch", "Slack", "design (Channel) - Acme - Slack",
   window="# design\nMaya Chen 10:02 AM\nare we still shipping the onboarding redesign on Tuesday?\n"
          "Leo Park 10:05 AM\nQA found two blockers in the signup flow, both in the email step",
   before="I think we should push the onboarding launch until", expect=["blocker", "signup", "QA", "fixed", "email step"])
sc("slack_flight_fact", "Slack", "Dana Lee (DM) - Acme - Slack",
   window="Dana Lee 4:12 PM\nwhat time does your flight land tomorrow? I can pick you up",
   facts=[("google chrome", "Your trip confirmation - Gmail", "mail.google.com",
           "United Airlines confirmation. Flight UA 1442 San Francisco (SFO) to New York (JFK). "
           "Departs 9:10 AM, arrives 6:45 PM. Seat 21C.", 30)],
   before="Thanks! UA 1442 lands at", expect=["6:45"])
sc("docs_beta_date", "Google Chrome", "Launch plan - Google Docs - Google Chrome", host="docs.google.com",
   window="Launch plan\nGoal: ship Glint 2.0 to beta users by December 4.\nOwners: Sam (engineering), Maya "
          "(design), Leo (QA)\nRisks: the signup flow blockers; App Store review time.",
   before="Timeline\nThe beta goes out to users on", expect=["December 4", "Dec 4", "4th"])
sc("midword_turnaround", "Mail", "Re: Contract - Inbox",
   before="Thanks for the quick turnar", midword=True, expect=["turnaround"])
sc("midword_questions", "Google Chrome", "Inbox - sam.k@acme.co - Gmail - Google Chrome", host=GMAIL[1],
   before="Let me know if you have any quest", midword=True, expect=["question"])
sc("form_refund", "Safari", "Contact support – Northwind Outfitters", host="northwind.example",
   window="Contact support\nOrder number\nTell us what happened\nSubmit",
   before="I'd like to request a refund for my order because")
sc("rent_paid_fact", "Mail", "New Message",
   facts=[("google chrome", "Payment confirmation - Zelle", "bank.example",
           "Payment sent. To: Mark Ruiz. Memo: Rent December. Amount $2,150.00. Date: Nov 28.", 60)],
   before="Hi Mark, the rent for December was sent on", expect=["Nov 28", "November 28", "28"])
sc("dentist_fact", "TextEdit", "todo.txt",
   facts=[("mail", "Appointment reminder", None,
           "Alvarez Dental: reminder of your cleaning appointment on Dec 2 at 10:30 AM. Reply C to confirm.", 50)],
   before="Call Alvarez Dental about the", expect=["cleaning", "appointment", "Dec 2"])
sc("linkedin_recruiter", "Google Chrome", "Messaging | LinkedIn - Google Chrome", host="www.linkedin.com",
   window="Alex Morgan · Recruiter at Northwind\nHi Sam, I'm hiring for a senior platform engineer role at "
          "Northwind (remote). Would you be open to a quick chat next week?",
   before="Hi Alex, thanks for reaching out. I'm")
sc("whatsapp_dinner", "Google Chrome", "WhatsApp - Google Chrome", host="web.whatsapp.com",
   window="Mom\nare you coming for dinner on Sunday? dad is making lasagna",
   samples=["yes!! can't wait, see you soon", "haha ok will do"],
   before="yes!! I'll be there, should I bring")
sc("linear_csv_bug", "Linear", "ENG-482 CSV export drops last row",
   window="ENG-482 CSV export drops last row\nWhen a file has a trailing newline, the CSV export drops the last "
          "row. Repro: export 'Invoices' with 3 rows, you get 2.",
   before="I can reproduce this. The export drops", expect=["last row", "row", "trailing newline"])
sc("notion_action_items", "Notion", "Weekly sync - Nov 17",
   window="Weekly sync — Nov 17\nAttendees: Sam, Maya, Leo\n- Maya: pricing page copy is ready for review\n"
          "- Leo: Stripe webhook retries fixed in staging",
   before="Action items\n- Maya to share the", expect=["pricing"])
sc("interview_thanks", GMAIL[0], "Thank you - sam.k@acme.co - Gmail - Google Chrome", host=GMAIL[1],
   window="Jordan Park <jordan@lumenlabs.io>\nThanks for meeting with us today about the Product Designer role. "
          "We'll be in touch by next week.\n— Jordan Park, Hiring Manager, Lumen Labs",
   before="Hi Jordan, thank you for taking the time to talk with me about the", expect=["Product Designer", "designer"])
sc("imessage_table", "Messages", "Alex",
   window="Alex: did you book the table at Nopa for 8 tonight?",
   before="Yes, I booked the table at", expect=["Nopa"])
sc("support_invoice", GMAIL[0], "Double charge - support@acme.co - Gmail - Google Chrome", host=GMAIL[1],
   window="Customer: Hi, my invoice #4471 was charged twice this month. Can you refund one of them?",
   before="Hi there, I'm sorry about the double charge on invoice", expect=["4471"])
sc("slack_pr_fact", "Slack", "eng (Channel) - Acme - Slack",
   window="Maya Chen: is the webhook issue still happening in prod?",
   facts=[("google chrome", "Fix webhook retries by leo-park · Pull Request #212 · acme/api", "github.com",
           "Fix webhook retries #212. leo-park merged 3 commits into main. Adds idempotency keys to the Stripe "
           "webhook handler so retries don't double-post payments.", 20)],
   before="Leo merged the fix for the webhook", expect=["retries", "retry", "212", "idempotency", "double"])
sc("offsite_venue_fact", GMAIL[0], "Offsite logistics - sam.k@acme.co - Gmail - Google Chrome", host=GMAIL[1],
   window="Maya Chen: Can you confirm the address for Saturday's offsite? People keep asking.",
   facts=[("google chrome", "Venue booking confirmed - Gmail", "mail.google.com",
           "Your booking is confirmed. Venue: The Foundry, 412 Harbor St, Oakland. Saturday 9 AM to 5 PM.", 70)],
   before="Hi Maya, the offsite is at", expect=["Foundry", "412 Harbor", "Harbor"])
sc("prose_morning", "TextEdit", "Draft.txt",
   before="The main reason I prefer working in the morning is that")
sc("notes_weekend", "Notes", "Notes",
   before="Ideas for the weekend: go for a hike, try the new")
sc("signoff_name", GMAIL[0], "Re: Proposal - sam.k@acme.co - Gmail - Google Chrome", host=GMAIL[1],
   samples=["Thanks again, see you Thursday. Best, Sam", "Sounds good to me. Cheers, Sam"],
   before="Let me know if you have any questions.\n\nBest,", expect=["Sam"])
sc("slack_review_style", "Slack", "Maya Chen (DM) - Acme - Slack",
   window="Maya Chen: can you review my PR for the pricing page when you get a chance?",
   samples=["yeah lgtm, ship it", "sounds good, will do after lunch"],
   before="yep, i'll take a look at the")
sc("midword_name", "Slack", "eng (Channel) - Acme - Slack",
   window="Priya Raman: I'll own the quarterly roadmap doc, comments welcome by Friday",
   before="I'll send my comments to Pri", midword=True, expect=["Priya"])
sc("package_delivery", GMAIL[0], "Your package - sam.k@acme.co - Gmail - Google Chrome", host=GMAIL[1],
   window="Northwind Shipping: Your package will arrive Wednesday between 2 and 4 PM. Someone must sign for it.",
   before="Thanks, I'll make sure I'm home on", expect=["Wednesday"])
sc("sprint_end_fact", "Slack", "eng (Channel) - Acme - Slack",
   window="Leo Park: when do we think the export fix lands?",
   facts=[("linear", "Sprint 14 · Acme", None, "Sprint 14 runs Nov 10 to Nov 21. Goals: CSV export fix, pricing page.", 80)],
   before="We should have the export fix in before Sprint 14 ends on", expect=["Nov 21", "November 21", "21"])
sc("hotel_fact", GMAIL[0], "Trip to NYC - sam.k@acme.co - Gmail - Google Chrome", host=GMAIL[1],
   window="Dana Lee: where are you staying in NYC next month?",
   facts=[("google chrome", "Reservation confirmed - Gmail", "mail.google.com",
           "Hilton Midtown reservation confirmed. Check-in Dec 8, check-out Dec 11. Confirmation 88213.", 100)],
   before="I'm staying at the", expect=["Hilton"])
sc("discord_ranked", "Discord", "#general | Squad - Discord",
   window="Kai: anyone up for ranked tonight?",
   before="I'm down, but only after")
sc("forum_fix", "Safari", "MacBook won't wake from sleep : r/mac", host="www.reddit.com",
   window="MacBook won't wake from sleep\nMy MacBook Air goes to sleep and the screen stays black until I hold power.",
   before="I had the same problem with my MacBook Air and what fixed it for me was")
sc("gmail_thanks_attach", GMAIL[0], "Re: Slides - sam.k@acme.co - Gmail - Google Chrome", host=GMAIL[1],
   window="Leo Park: Attached are the final slides for Thursday's board meeting. Let me know if anything is off.",
   before="Thanks Leo, the slides look great. I'll review them before", expect=["Thursday", "board"])
sc("page_tip_copy", "Google Chrome", "Try it - Acme Help Center - Google Chrome", host="help.acme.example",
   window="Try it\nClick the box and type the start of a sentence.\nThanks for sending the deck over, I'll review it and\n"
          "Nothing showing? Type at least a dozen characters and stop at the end of a line.",
   before="Thanks for sending the deck over, I'll review it and")
sc("textedit_meeting_recap", "TextEdit", "recap.txt",
   window=None, facts=[("zoom.us", "Zoom Meeting", None,
                        "Meeting notes: agreed to hire two contractors for the data migration; budget capped at $40k.", 6)],
   before="Recap: we agreed to hire two contractors for the", expect=["data migration", "migration"])


def seed() -> None:
    with E.DB_LOCK:
        d = E.db()
        for i, s in enumerate(S):
            app_id = s["app"].lower()
            if s["window"]:
                E.mem.add_chunk(d, s["window"], app_id, s["title"], s["host"], "ax", NOW - 10 * 60_000 - i)
            for (fapp, ftitle, fhost, text, hours) in s["facts"]:
                E.mem.add_chunk(d, text, fapp, ftitle, fhost, "ax", NOW - int(hours * H))
        # Background noise so keyword search has something to reject.
        for j in range(40):
            E.mem.add_chunk(d, f"Newsletter {j}: product updates, release notes and tips for getting more done "
                               f"with your calendar and inbox. Webinar on Tuesday.", "google chrome",
                            f"Newsletter {j}", "news.example", "ax", NOW - (j + 1) * 5 * H)
        d.commit()
        d.close()
    for s in S:
        for text in s["samples"]:
            ac.add_sample(ac._app_key(s["app"], s["host"]), text, source="typed", min_chars=10)


# ------------------------------------------------------------------- rules
NUM = re.compile(r"\d+")
CAP = re.compile(r"\b[A-Z][a-zA-Z]+\b")


def context_text(s: dict) -> str:
    parts = [s["before"], s["after"], s["window"] or "", s["title"], s["app"], s["host"] or ""]
    parts += [f[3] + " " + f[1] for f in s["facts"]] + s["samples"]
    return " ".join(parts).lower()


def invented(s: dict, sugg: str) -> list[str]:
    ctx = context_text(s)
    out = [n for n in NUM.findall(sugg) if n not in ctx]
    tokens = re.findall(r"\S+", sugg)
    for i, tok in enumerate(tokens):
        w = re.sub(r"^[^\w]+|[^\w]+$", "", tok)
        if not CAP.fullmatch(w) or w in ac.COMMON_CAPS:
            continue
        prev = tokens[i - 1] if i else s["before"].rstrip()[-1:]
        if i == 0 and (not prev or prev[-1:] in ".!?:\n,"):
            continue
        if i > 0 and prev[-1:] in ".!?:":
            continue
        if w.lower() not in ctx:
            out.append(w)
    return out


def check(s: dict, sugg: str) -> dict:
    before = s["before"]
    r = {"nonempty": bool(sugg.strip())}
    if not r["nonempty"]:
        return {**r, "pass": False}
    nb, ns = ac._norm(before), ac._norm(sugg)
    words = sugg.split()
    bw = re.findall(r"\w+", before.lower())
    sw = re.findall(r"\w+", sugg.lower())
    echo = len(sw) >= 3 and len(bw) >= 3 and sw[:3] == bw[-3:]
    seam_dup = not s["midword"] and sw and bw and sw[0] == bw[-1] and len(bw[-1]) > 2
    r["no_repeat"] = not (len(ns) >= 8 and ns in nb) and not echo and not seam_dup
    r["short"] = len(words) <= ac.MAX_WORDS
    if s["midword"]:
        r["seam"] = sugg[:1].isalpha()
    elif before[-1:].isalnum():
        r["seam"] = sugg[:1] in (" ", ",", ".", "!", "?", ";", ":", "'", "’")
    else:
        r["seam"] = True
    if ac._new_sentence_midway(before, sugg, " ".join([s["window"] or ""] + [f[3] for f in s["facts"]])):
        r["seam"] = False  # starts a new sentence in the middle of the user's clause
    bad = invented(s, sugg)
    r["grounded"] = not bad
    if bad:
        r["invented"] = bad
    if s["expect"]:
        hay = (before[-30:] + sugg).lower()
        r["uses_fact"] = any(e.lower() in hay for e in s["expect"])
    r["pass"] = all(v for k, v in r.items() if k in ("nonempty", "no_repeat", "short", "seam", "grounded", "uses_fact"))
    return r


def run(legacy: bool) -> dict:
    ac.LEGACY = legacy
    results, lat = [], []
    for s in S:
        b = {"app": s["app"], "title": s["title"], "url_host": s["host"], "text_before": s["before"],
             "text_after": s["after"], "n": 1}
        with ac._lock:
            ac._cache.clear()
        t0 = time.time()
        try:
            sugg, reason, info = ac._call(b, 1, ac._cache_key(b), None, True)
        except ai.ProviderError as e:
            sugg, reason, info = [], f"error: {e}", {}
        ms = (time.time() - t0) * 1000
        lat.append(ms)
        text = sugg[0] if sugg else ""
        r = check(s, text)
        results.append((s, text, r, ms, info))
        if VERBOSE:
            flag = "PASS" if r["pass"] else "fail"
            fails = [k for k, v in r.items() if v is False and k != "pass"]
            print(f"  [{flag}] {'old' if legacy else 'new'} {s['id']:<22} {int(ms):>5} ms  "
                  f"{json.dumps(s['before'][-40:])} + {json.dumps(text)}"
                  + (f"  ✗ {','.join(fails)}" if fails else "") + (f" {r.get('invented')}" if r.get("invented") else ""))
    n = len(results)
    with_fact = [x for x in results if x[0]["expect"]]
    agg = {
        "pass": sum(x[2]["pass"] for x in results) / n,
        "nonempty": sum(x[2]["nonempty"] for x in results) / n,
        "no_repeat": sum(x[2].get("no_repeat", True) for x in results if x[2]["nonempty"]) / max(1, sum(x[2]["nonempty"] for x in results)),
        "short": sum(x[2].get("short", True) for x in results if x[2]["nonempty"]) / max(1, sum(x[2]["nonempty"] for x in results)),
        "grounded": sum(x[2].get("grounded", True) for x in results if x[2]["nonempty"]) / max(1, sum(x[2]["nonempty"] for x in results)),
        "uses_fact": sum(x[2].get("uses_fact", False) for x in with_fact) / max(1, len(with_fact)),
        "p50_ms": statistics.median(lat),
        "p90_ms": sorted(lat)[int(0.9 * (n - 1))],
    }
    return agg


def main() -> int:
    seed()
    ac._on_device({"app": "Notes", "text_before": "Warming up the model, I will"}, {"label": "Notes"})  # warm
    print(f"autocomplete eval: {len(S)} scenarios on the on-device model")
    before = run(legacy=True)
    after = run(legacy=False)
    rows = [("scenario pass rate", "pass", "%"), ("non-empty", "nonempty", "%"), ("no repetition", "no_repeat", "%"),
            ("<= 12 words", "short", "%"), ("no invented names/numbers", "grounded", "%"),
            ("uses expected fact", "uses_fact", "%"), ("latency p50", "p50_ms", "ms"), ("latency p90", "p90_ms", "ms")]
    print(f"  {'metric':<28}{'before':>10}{'after':>10}")
    for label, k, unit in rows:
        fmt = (lambda v: f"{v * 100:.0f}%") if unit == "%" else (lambda v: f"{v:.0f} ms")
        print(f"  {label:<28}{fmt(before[k]):>10}{fmt(after[k]):>10}")
    if after["pass"] < before["pass"]:
        print("FAIL: the current prompt scores below the previous one")
        return 1
    print("ok: after >= before")
    return 0


if __name__ == "__main__":
    sys.exit(main())
