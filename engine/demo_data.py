"""Fictional content for Glint's demo mode (see demo.py).

Everything here is made up: Maya Chen is a product manager at "Northwind", a fictional software
company. People, customers, documents, numbers and email addresses (all @*.example) are invented.
"""
from __future__ import annotations

# ------------------------------------------------------------------ people / profile

BIO = """Maya Chen. Senior Product Manager at Northwind (billing and onboarding).
Reports to Lena Fischer (VP Product). Works most with Priya Raman (eng lead, Billing),
Diego Alvarez (product design), Sam Okafor (data) and Tom Becker (support lead).
Current focus: shipping Billing v2, the onboarding revamp, and Q3 planning.
Prefers short bullet answers, metric-first summaries and links to the source doc."""

MEMORY_DOC = """## Preferences
- Weekly update goes out Friday before 4pm, in #product, bullets only.
- Uses "Billing v2" (not "new billing") in anything customer-facing.

## Corrections
- Activation = workspace created + first invoice sent within 7 days (not just sign-up).

## Standing notes
- Go/no-go for Billing v2 needs sign-off from Finance (Aisha) and Support (Tom).
- Bluefield Health and Harbor & Pine are design partners for Billing v2.
"""

# ------------------------------------------------------------------ sessions
# Each beat: (app, window title, minutes, text, url_host). Captures reveal the text progressively,
# the way a growing chat or a scrolled document would read through Accessibility.

def B(app, title, minutes, text, host=None):
    return (app, title, minutes, text.strip("\n"), host)


SLACK, MAIL, NOTION, LINEAR, FIGMA = "Slack", "Mail", "Notion", "Linear", "Figma"
CHROME, ZOOM, CAL, CODE, KEYNOTE, NOTES = "Google Chrome", "zoom.us", "Calendar", "Code", "Keynote", "Notes"

P_BILL, P_Q3, P_ONB = "Billing v2", "Q3 Planning", "Onboarding revamp"


def E(name, etype, salience, *aliases):
    return {"name": name, "type": etype, "aliases": list(aliases), "salience": salience}


def I(headline, implication, justification, *apps):
    return {"headline": headline, "implication": implication, "justification": justification,
            "entity_app_ids": list(apps)}


def Q(focus, depth, consistency, recovery, rationale):
    return {"focus": focus, "depth": depth, "consistency": consistency, "recovery": recovery,
            "rationale": rationale}


PRIYA = E("Priya Raman", "person", 70, "Priya")
DIEGO = E("Diego Alvarez", "person", 70, "Diego")
SAM = E("Sam Okafor", "person", 65, "Sam")
LENA = E("Lena Fischer", "person", 65, "Lena")
TOM = E("Tom Becker", "person", 60, "Tom")
AISHA = E("Aisha Khan", "person", 60, "Aisha")
JONAH = E("Jonah Reyes", "person", 55, "Jonah")
BILLING = E("Billing v2", "project", 95, "billing migration", "BV2")
Q3 = E("Q3 Planning", "project", 90, "Q3 roadmap", "Q3 plan")
ONB = E("Onboarding revamp", "project", 90, "new onboarding", "ONB")
BLUEFIELD = E("Bluefield Health", "org", 70, "Bluefield")
HARBOR = E("Harbor & Pine", "org", 70, "Harbor and Pine", "H&P")
PRORATION = E("Proration", "topic", 75, "prorations", "mid-cycle upgrades")
ACTIVATION = E("Activation rate", "topic", 70, "activation", "7-day activation")


CATCHUPS = [
    dict(title="Morning inbox triage", state="communication", state_name="Communication",
         summary="Cleared the inbox and caught up on #billing-v2: Priya flagged the proration bug as the last "
                 "launch blocker and Lena moved the roadmap review to Thursday.",
         quality=Q(52, 38, 60, 70, "Short, fragmented triage across Mail and Slack; expected for a catch-up."),
         insights=[I("Launch questions are piling up in #billing-v2",
                     "A short pinned FAQ in the channel could save repeated answers this week.",
                     "Three separate threads asked about the launch date and the proration fix.", SLACK)],
         entities=[BILLING, PRIYA, LENA],
         beats=[B(MAIL, "Inbox (14 messages, 6 unread)", 9, """
Lena Fischer  Roadmap review moved to Thursday 2pm  Hi all, moving the Q3 roadmap review so Finance can join...
Aisha Khan  Re: Billing v2 revenue reconciliation  Numbers for October look right except two accounts on annual plans...
Tom Becker  Support volume this week  Billing tickets are up 12% week over week, mostly invoice PDF questions...
Linear  [BIL-142] Proration double-charges on mid-cycle upgrades  Priya Raman changed status to In Progress
Notion  Diego Alvarez commented on Onboarding revamp — flows v4  "Can we drop step 3 entirely?"
Calendar  Invitation: Bluefield Health — customer interview (Tue 11:00)
Jonah Reyes  Pricing page draft for review  Draft is in the Marketing folder, would love PM eyes by Friday
"""), B(SLACK, "#billing-v2 — Northwind", 12, """
#billing-v2
Priya Raman  9:02 AM  Morning! BIL-142 is the last real blocker for launch. Repro'd with an annual → monthly downgrade too.
Tom Becker  9:05 AM  Two more tickets overnight from Harbor & Pine about double charges after upgrading seats.
Maya Chen  9:11 AM  Thanks both. Let's keep the launch date for now and decide at go/no-go on Thursday.
Priya Raman  9:12 AM  Fix is in review, should land today. I'll post the PR here.
Aisha Khan  9:20 AM  Finance sign-off is pending only on the reconciliation for annual plans.
Maya Chen  9:21 AM  Great, I'll add it to the launch checklist.
""")]),
    dict(title="Slack and email catch-up", state="communication", state_name="Communication",
         summary="Answered design and data questions in Slack and replied to Jonah about the pricing page draft.",
         quality=Q(48, 35, 55, 66, "Many short replies across channels; little sustained work."),
         insights=[], entities=[JONAH, SAM, DIEGO],
         beats=[B(SLACK, "Sam Okafor (DM) — Northwind", 8, """
Sam Okafor  8:51 AM  The October activation cohort is ready. 7-day activation is 31.4%, up from 28.9% in September.
Sam Okafor  8:52 AM  Biggest drop-off is still "invite teammates" (step 3): 41% abandon there.
Maya Chen  8:58 AM  That's the step Diego wants to cut. Can you split it by company size?
Sam Okafor  9:00 AM  Yep, will add it to the sheet this morning.
"""), B(MAIL, "Re: Pricing page draft for review", 10, """
To: Jonah Reyes
Subject: Re: Pricing page draft for review

Hi Jonah,
Looks great overall. Three notes:
- Lead with "pay for what you use" rather than the per-seat table.
- The usage tiers should match the Billing v2 PRD (1k / 10k / 50k events).
- Let's hold the annual discount copy until Finance confirms the numbers.
Happy to review the next version Thursday.
Maya
""")]),
    dict(title="Inbox zero pass", state="communication", state_name="Communication",
         summary="Worked through support escalations and calendar invites, and confirmed the Bluefield interview.",
         quality=Q(55, 40, 62, 72, "Steady triage with few context switches."), insights=[],
         entities=[TOM, BLUEFIELD],
         beats=[B(MAIL, "Inbox (9 messages, 2 unread)", 9, """
Tom Becker  Escalation: Harbor & Pine charged twice for seat upgrade  Customer upgraded 12 → 20 seats on the 14th...
Ana Lima (Bluefield Health)  Re: Interview on Tuesday  Confirmed for 11:00, I'll bring our finance admin too.
Linear  Weekly digest: Billing v2 (14 done, 3 in progress, 1 blocked)
Notion  Lena Fischer mentioned you in Q3 OKRs
"""), B(CAL, "Calendar — Week", 4, """
Mon  Q3 roadmap working session  10:00–11:30
Tue  Bluefield Health — customer interview  11:00–11:45
Wed  Billing v2 pricing page sync  15:30–16:00
Thu  Billing v2 go/no-go  10:00–10:30
Thu  Q3 roadmap review  14:00–15:00
Fri  Weekly product sync  15:30–16:15
""")]),
    dict(title="Morning catch-up", state="communication", state_name="Communication",
         summary="Read overnight Slack, approved Diego's revised onboarding copy and replied to Lena about OKRs.",
         quality=Q(50, 36, 58, 64, "Short catch-up across Slack and Mail."), insights=[],
         entities=[DIEGO, LENA, ONB],
         beats=[B(SLACK, "#onboarding-revamp — Northwind", 9, """
#onboarding-revamp
Diego Alvarez  8:40 AM  Posted v5 of the welcome screen copy. Shorter headline, one CTA.
Diego Alvarez  8:41 AM  "Send your first invoice in 3 minutes" tested best in the hallway test (6/8 preferred it)
Maya Chen  9:02 AM  Love it. Ship it to the experiment branch.
Sam Okafor  9:05 AM  I'll make sure the event "first_invoice_sent" fires from the new flow.
"""), B(MAIL, "Re: Q3 OKRs — draft 2", 6, """
To: Lena Fischer
Subject: Re: Q3 OKRs — draft 2

Lena, agreed on dropping KR3. Proposed replacement:
KR3: Raise 7-day activation from 31% to 38% via the onboarding revamp.
I'll bring capacity numbers to Thursday's review.
""")]),
    dict(title="Triage after standup", state="communication", state_name="Communication",
         summary="Triaged support escalations from Tom and scheduled a follow-up call with Harbor & Pine.",
         quality=Q(54, 42, 60, 70, "Focused triage with one long Slack thread."), insights=[],
         entities=[TOM, HARBOR],
         beats=[B(SLACK, "#support-escalations — Northwind", 10, """
#support-escalations
Tom Becker  9:01 AM  Harbor & Pine is asking for a credit for the double charge on the 14th. $1,240 total.
Maya Chen  9:06 AM  Approved. Please credit it and link BIL-142 in the ticket.
Tom Becker  9:07 AM  Done. They also asked when the fix ships.
Maya Chen  9:08 AM  Fix lands this week, I'll confirm after go/no-go on Thursday.
""")]),
    dict(title="Morning messages", state="communication", state_name="Communication",
         summary="Caught up on the Billing v2 launch thread and confirmed Finance sign-off with Aisha.",
         quality=Q(58, 40, 64, 70, "Mostly one thread; steady."), insights=[],
         entities=[AISHA, BILLING],
         beats=[B(SLACK, "#billing-v2 — Northwind", 11, """
#billing-v2
Aisha Khan  8:48 AM  Reconciliation for annual plans is done. Finance signs off on Billing v2.
Priya Raman  8:55 AM  BIL-142 fix merged last night, deploying to staging now.
Maya Chen  9:00 AM  Amazing. I'll verify on staging this morning and update the checklist.
Tom Becker  9:03 AM  Support macros for the new invoice emails are ready.
""")]),
    dict(title="Inbox and Slack catch-up", state="communication", state_name="Communication",
         summary="Read the staging deploy notes for the proration fix and replied to two customer emails.",
         quality=Q(56, 41, 61, 69, "Short catch-up before deeper work."), insights=[],
         entities=[PRIYA, BILLING],
         beats=[B(SLACK, "#billing-v2 — Northwind", 8, """
#billing-v2
Priya Raman  8:40 AM  Staging has the proration fix. Test plan: upgrade 12 → 20 seats mid-cycle, then downgrade.
Priya Raman  8:41 AM  Expected: one prorated line item of $412.90, no second charge.
Maya Chen  8:44 AM  On it after standup.
"""), B(MAIL, "Re: Seat upgrade question", 6, """
To: Ana Lima
Subject: Re: Seat upgrade question

Hi Ana, thanks for flagging this. The fix for prorated upgrades ships with Billing v2 this week.
I'll confirm the exact date after our go/no-go on Thursday.
Maya
""")]),
]


T: dict[str, dict] = {}

T["prd_review"] = dict(
    title="Billing v2 PRD review", project=P_BILL, state="deep-work", state_name="Deep Work",
    summary="Reviewed the Billing v2 PRD in Notion, tightened the usage tiers (1k / 10k / 50k events) and resolved "
            "Priya's open comments on proration and invoice timing.",
    quality=Q(84, 80, 78, 74, "Long stretch in one document with brief Slack check-ins."),
    insights=[I("Your deepest work happens in the PRD before 12pm",
                "Protecting late mornings for doc work would keep Billing v2 decisions moving.",
                "This 70-minute session had only two context switches, both to #billing-v2.", NOTION, SLACK)],
    entities=[BILLING, PRIYA, E("Billing v2 PRD", "document", 85, "PRD"), PRORATION,
              E("Usage-based pricing", "topic", 60, "usage tiers")],
    beats=[B(NOTION, "Billing v2 — PRD", 30, """
Billing v2 — PRD
Owner: Maya Chen · Eng: Priya Raman · Design: Diego Alvarez · Status: In review
Problem
Customers on seat-based plans overpay when usage is low and churn when invoices surprise them.
Goals
1. Move new customers to usage-based pricing with three tiers: 1k / 10k / 50k events per month.
2. Prorate seat and tier changes mid-cycle with a single line item.
3. Send invoices that explain usage in plain language.
Non-goals
Migrating existing annual contracts before renewal.
Success metrics
Invoice-related tickets down 30% within 60 days. Net revenue retention unchanged or better.
Open questions
- Do we prorate downgrades immediately or at the next cycle? (Priya: next cycle is simpler)
- Invoice day: anniversary date or first of month? Decision: anniversary date.
"""), B(SLACK, "#billing-v2 — Northwind", 6, """
Maya Chen  10:41 AM  PRD updated: downgrades apply at the next cycle, upgrades prorate immediately.
Priya Raman  10:43 AM  👍 that matches what Stripe Billing does by default, less custom code for us.
"""), B(NOTION, "Billing v2 — PRD", 28, """
Rollout plan
Phase 1: new self-serve signups (week of launch)
Phase 2: monthly customers at renewal
Phase 3: annual customers at renewal, with an opt-in migration tool
Risks
Proration bugs on mid-cycle upgrades (BIL-142). Mitigation: credit affected customers automatically.
Support load from the new invoice format. Mitigation: macros + help center article before launch.
Launch criteria
Finance sign-off on revenue reconciliation. Support macros ready. BIL-142 fixed and verified on staging.
""")])

T["q3_roadmap"] = dict(
    title="Q3 roadmap draft", project=P_Q3, state="planning", state_name="Planning",
    summary="Drafted the Q3 roadmap in Notion: three bets (Billing v2 phase 2, onboarding revamp, usage insights) "
            "with rough sizing, and asked #product-leads for input.",
    quality=Q(76, 72, 70, 68, "Mostly one document with a short detour to the capacity sheet."),
    insights=[I("Roadmap and capacity numbers live in different places",
                "Linking the capacity sheet from the roadmap doc would make Thursday's review easier.",
                "You switched between the Notion draft and the Q3 capacity sheet 7 times.", NOTION, CHROME)],
    entities=[Q3, E("Q3 Roadmap", "document", 85, "roadmap draft"), BILLING, ONB, LENA],
    beats=[B(NOTION, "Q3 Roadmap (draft)", 34, """
Q3 Roadmap (draft)
Theme: make billing predictable and get new teams to value faster.
Bet 1 — Billing v2, phase 2: migrate monthly customers at renewal. Size: L. Team: Billing.
Bet 2 — Onboarding revamp: guided setup to first invoice in under 10 minutes. Size: M. Team: Growth.
Bet 3 — Usage insights: show customers their usage trend and forecast. Size: M. Team: Billing + Data.
Not this quarter: multi-currency invoices, partner API v2.
Dependencies: Data team support for activation events (Sam).
"""), B(CHROME, "Q3 capacity plan — Google Sheets", 12, """
Q3 capacity plan
Team | Engineers | Weeks available | Committed | Free
Billing | 6 | 72 | 58 | 14
Growth | 4 | 48 | 30 | 18
Data | 2 | 24 | 20 | 4
Platform | 5 | 60 | 60 | 0
""", "docs.google.com"), B(SLACK, "#product-leads — Northwind", 7, """
#product-leads
Maya Chen  10:52 AM  First draft of the Q3 roadmap is up. Three bets, all sized. Comments welcome before Thursday.
Lena Fischer  10:58 AM  Thanks! Can we see usage insights sized against Data capacity? Looks tight.
""")])

T["onb_flows"] = dict(
    title="Onboarding flow redesign", project=P_ONB, state="design-review", state_name="Design Review",
    summary="Reviewed Diego's onboarding flows v4 in Figma, agreed to cut the 'invite teammates' step from the "
            "first run, and left comments on the welcome screen copy.",
    quality=Q(72, 68, 66, 70, "Focused review in Figma with a few Slack replies to Diego."),
    insights=[I("Cutting step 3 targets the biggest drop-off",
                "Moving 'invite teammates' later could lift 7-day activation; worth an experiment.",
                "Sam's funnel shows 41% abandon at step 3; you and Diego agreed to move it.", FIGMA, SLACK)],
    entities=[ONB, DIEGO, E("Onboarding flows v4", "document", 80, "flows v4"), ACTIVATION],
    beats=[B(FIGMA, "Onboarding revamp — flows v4", 32, """
Onboarding revamp — flows v4
Page: First run
1 Welcome — "Send your first invoice in 3 minutes"
2 Connect your data (CSV, Stripe, or skip)
3 Invite teammates (optional)
4 Create your first invoice
5 Done — checklist with 3 next steps
Comments
Diego Alvarez: Can we drop step 3 entirely and ask later from the checklist?
Maya Chen: Yes. Sam's funnel says 41% abandon there. Move it to the checklist.
Diego Alvarez: Updated. Also tried a progress bar on top, thoughts?
Maya Chen: Keep it, but show 4 steps not 5.
"""), B(SLACK, "Diego Alvarez (DM) — Northwind", 8, """
Diego Alvarez  2:31 PM  v4 is ready for your eyes, I left two open questions in the file
Maya Chen  2:48 PM  Left comments. Big yes on moving invites to the checklist.
Diego Alvarez  2:49 PM  🙌 I'll do a hallway test with the support team tomorrow
""")])

T["weekly_sync"] = dict(
    title="Weekly product sync", project=None, state="meetings", state_name="Meetings",
    summary="Ran the weekly product sync on Zoom: Billing v2 on track pending BIL-142, onboarding experiment "
            "ready for spec, Q3 review on Thursday. Notes posted in Notion.",
    quality=Q(62, 55, 70, 74, "Meeting with live note-taking in Notion."),
    insights=[], entities=[BILLING, ONB, Q3, LENA, PRIYA, DIEGO],
    beats=[B(ZOOM, "Zoom Meeting — Weekly product sync", 30, """
Weekly product sync
Participants (6): Maya Chen, Lena Fischer, Priya Raman, Diego Alvarez, Sam Okafor, Tom Becker
Maya Chen is sharing screen
"""), B(NOTION, "Weekly product sync — notes", 14, """
Weekly product sync — notes
Billing v2: on track for next week. Blocker: BIL-142 (proration). Owner: Priya.
Onboarding revamp: flows v4 approved; experiment spec next (Maya).
Q3 planning: roadmap draft shared; review Thursday with Finance.
Support: invoice PDF questions up 12% WoW (Tom). Macros in progress.
Action items
- Maya: write onboarding experiment spec
- Priya: land BIL-142 fix
- Sam: activation split by company size
""")])

T["funnel"] = dict(
    title="Activation funnel analysis", project=P_ONB, state="research", state_name="Research",
    summary="Dug into Sam's October activation cohort: 7-day activation 31.4% (up from 28.9%), biggest drop at "
            "'invite teammates'. Small teams activate 2x more often when they skip it.",
    quality=Q(80, 82, 74, 70, "Sustained analysis in one sheet with a Slack thread to Sam."),
    insights=[I("Small teams activate twice as often without the invite step",
                "Supports moving invites later; target the experiment at teams under 10 seats first.",
                "In the Oct cohort sheet, teams <10 seats that skipped invites activated at 44% vs 22%.", CHROME, SLACK),
              I("Activation definition is now consistent",
                "Using 'first invoice sent within 7 days' everywhere avoids comparing different metrics.",
                "You updated the definition in the sheet and in the research doc.", CHROME, NOTION)],
    entities=[ONB, SAM, ACTIVATION, E("Activation funnel — Oct cohort", "document", 75, "activation sheet")],
    beats=[B(CHROME, "Activation funnel — Oct cohort — Google Sheets", 36, """
Activation funnel — Oct cohort (n = 2,184 new workspaces)
Step | Reached | Conversion
1 Welcome | 2,184 | 100%
2 Connect data | 1,790 | 82.0%
3 Invite teammates | 1,056 | 59.0%
4 First invoice created | 812 | 76.9%
5 First invoice sent within 7 days | 686 | 84.5%
7-day activation: 31.4% (Sep: 28.9%)
By company size (skipped invites vs. not)
1–9 seats: 44.1% vs 22.0%
10–49 seats: 33.5% vs 29.8%
50+ seats: 27.2% vs 30.4%
""", "docs.google.com"), B(SLACK, "Sam Okafor (DM) — Northwind", 9, """
Maya Chen  9:58 AM  The 1–9 seat split is the story. Can we target the experiment there first?
Sam Okafor  10:01 AM  Yes, that's ~70% of signups anyway. I'll set up the cohort filter.
Sam Okafor  10:02 AM  Activation = first invoice sent within 7 days, right? Not just workspace created.
Maya Chen  10:02 AM  Right, invoice sent within 7 days.
"""), B(NOTION, "Onboarding revamp — research synthesis", 12, """
Onboarding revamp — research synthesis
Finding 1: "Invite teammates" is the largest drop-off (41%).
Finding 2: Small teams want to see an invoice before inviting anyone.
Finding 3: Customers who connect Stripe in step 2 activate 1.6x more.
Recommendation: move invites to the post-setup checklist; promote Stripe connect.
""")])

T["interview"] = dict(
    title="Bluefield Health customer interview", project=P_BILL, state="meetings", state_name="Customer Call",
    summary="Interviewed Ana Lima and their finance admin at Bluefield Health about invoices: they reconcile by "
            "usage line and want a CSV export plus a monthly usage email.",
    quality=Q(70, 74, 72, 76, "One call with live notes; clear takeaways."),
    insights=[I("Design partners keep asking for CSV usage export",
                "Consider pulling CSV export into Billing v2 phase 1 instead of phase 2.",
                "Bluefield asked for it today; Harbor & Pine asked last week (in Mail).", ZOOM, NOTES)],
    entities=[BLUEFIELD, E("Ana Lima", "person", 70, "Ana"), BILLING, E("Usage CSV export", "topic", 60, "CSV export")],
    beats=[B(ZOOM, "Zoom Meeting — Bluefield Health interview", 35, """
Bluefield Health — customer interview
Participants (4): Maya Chen, Ana Lima (Bluefield Health), Marco Silva (Bluefield Health), Tom Becker
Recording is off
"""), B(NOTES, "Interview notes — Bluefield", 14, """
Interview notes — Bluefield Health (Ana Lima, ops lead; Marco Silva, finance admin)
- They reconcile invoices line by line against usage in their own sheet every month.
- Biggest pain: seat upgrades mid-month showed two charges (Harbor & Pine reported the same).
- Want: CSV export of usage per day, and an email when usage passes 80% of tier.
- Would move to usage-based pricing if invoices are "explainable to our CFO".
- Quote: "If I can't explain an invoice in one minute, it goes to a dispute."
Follow-ups: send Billing v2 invoice mockups; invite to beta.
""")])

T["capacity"] = dict(
    title="Q3 capacity planning", project=P_Q3, state="planning", state_name="Planning",
    summary="Updated the Q3 capacity plan with Priya: Billing has 14 free engineer-weeks, Data only 4, so usage "
            "insights is cut to a smaller first version.",
    quality=Q(78, 70, 72, 66, "Spreadsheet work with a short call-style Slack thread."),
    insights=[I("Data capacity is the constraint for Q3",
                "Usage insights should be scoped to what Data can support (4 free weeks).",
                "The capacity sheet shows Data at 20 of 24 weeks committed.", CHROME, SLACK)],
    entities=[Q3, PRIYA, E("Q3 capacity plan", "document", 80, "capacity sheet")],
    beats=[B(CHROME, "Q3 capacity plan — Google Sheets", 30, """
Q3 capacity plan (v2)
Team | Engineers | Weeks available | Committed | Free
Billing | 6 | 72 | 58 | 14
Growth | 4 | 48 | 34 | 14
Data | 2 | 24 | 20 | 4
Platform | 5 | 60 | 60 | 0
Bets
Billing v2 phase 2 | Billing | 40 weeks
Onboarding revamp | Growth | 30 weeks
Usage insights v1 (trend only, no forecast) | Billing + Data | 16 weeks
""", "docs.google.com"), B(SLACK, "Priya Raman (DM) — Northwind", 12, """
Maya Chen  1:52 PM  If usage insights is trend-only (no forecast), can Billing absorb most of it?
Priya Raman  1:55 PM  Yes, 12 of the 16 weeks. Data only needs to expose the daily usage table.
Maya Chen  1:56 PM  Perfect, updating the sheet.
""")])

T["code_review"] = dict(
    title="Billing webhook PR review", project=P_BILL, state="deep-work", state_name="Code Review",
    summary="Read Priya's PR #482 for the proration fix on GitHub and in VS Code, checked the test cases against "
            "the PRD and approved with one comment about downgrades.",
    quality=Q(80, 78, 74, 72, "Focused review between GitHub and the editor."),
    insights=[], entities=[BILLING, PRIYA, PRORATION, E("PR #482", "document", 70, "billing-service#482")],
    beats=[B(CHROME, "Fix proration on mid-cycle upgrades · Pull Request #482 · northwind/billing-service", 18, """
Fix proration on mid-cycle upgrades #482
priyar wants to merge 6 commits into main from fix/bil-142-proration
Fixes BIL-142. Upgrades now create one prorated invoice item instead of a new subscription item.
Files changed 7  +214 −58
Checks: 41 passed
Test plan
- upgrade 12 → 20 seats mid-cycle: one line item, prorated
- downgrade 20 → 12 seats: applies at next cycle, no credit note
- annual plan upgrade: prorated against remaining months
""", "github.com"), B(CODE, "proration.py — billing-service", 16, """
def prorate_upgrade(subscription, new_quantity, at):
    \"\"\"One prorated line item for a mid-cycle upgrade (BIL-142).\"\"\"
    remaining = subscription.period_end - at
    fraction = remaining / (subscription.period_end - subscription.period_start)
    delta = (new_quantity - subscription.quantity) * subscription.unit_price
    return InvoiceItem(amount=round(delta * fraction, 2), description="Prorated seat upgrade")

def test_upgrade_mid_cycle_single_item():
    sub = make_subscription(quantity=12, unit_price=49.0)
    item = prorate_upgrade(sub, 20, at=sub.period_start + days(14))
    assert item.amount == 206.45
"""), B(CHROME, "Fix proration on mid-cycle upgrades · Pull Request #482 · northwind/billing-service", 6, """
Review: Approve
Maya Chen: Matches the PRD. One ask: add a test for downgrade + upgrade in the same cycle (Harbor & Pine did exactly this).
priyar: Good call, adding it now.
""", "github.com")])

T["proration_triage"] = dict(
    title="Proration bug triage", project=P_BILL, state="problem-solving", state_name="Problem Solving",
    summary="Triaged BIL-142 with Priya and Tom: mid-cycle seat upgrades create a second subscription item. "
            "Confirmed 9 affected accounts, agreed on automatic credits and a fix before launch.",
    quality=Q(74, 76, 64, 80, "Several switches between Linear, Slack and Stripe docs, but recovered quickly each time."),
    insights=[I("BIL-142 affects 9 accounts, all on monthly plans",
                "Automatic credits before launch avoid a support spike on invoice day.",
                "Tom's export in #support-escalations lists 9 accounts; none are annual.", LINEAR, SLACK),
              I("Stripe docs answered the downgrade question",
                "Applying downgrades at the next cycle needs no custom code.",
                "You read the Stripe prorations page right before updating the ticket.", CHROME, LINEAR)],
    entities=[BILLING, PRORATION, PRIYA, TOM, HARBOR, E("BIL-142", "document", 85, "proration bug")],
    beats=[B(LINEAR, "BIL-142 Proration double-charges on mid-cycle upgrades", 20, """
BIL-142  Proration double-charges on mid-cycle upgrades
Status: In Progress · Priority: Urgent · Assignee: Priya Raman · Project: Billing v2 · Cycle 46
Description
When a customer upgrades seats mid-cycle, we create a new subscription item instead of updating
quantity, so the next invoice charges both the old and new quantity for the overlap.
Affected: 9 accounts (list from Tom), all monthly plans. Largest: Harbor & Pine ($1,240).
Plan
1. Fix: update quantity with proration_behavior=create_prorations
2. Credit affected accounts automatically before launch
3. Add regression tests for upgrade, downgrade, annual
Activity
Tom Becker attached affected-accounts.csv
Maya Chen set priority to Urgent
"""), B(CHROME, "Prorations | Stripe Documentation", 9, """
Prorations
When a customer changes their subscription mid-cycle, Stripe can calculate a prorated amount.
Proration behavior: create_prorations (default), always_invoice, none.
Downgrades: to apply a change at the end of the current period, schedule the update instead
of prorating immediately.
""", "docs.stripe.com"), B(SLACK, "#billing-v2 — Northwind", 12, """
Maya Chen  9:58 AM  Summary for BIL-142: fix = update quantity with prorations; downgrades at next cycle; auto-credit the 9 accounts.
Priya Raman  10:01 AM  Agreed. I can have a PR by tomorrow.
Tom Becker  10:03 AM  I'll prep a macro for the credit emails.
Maya Chen  10:04 AM  Thanks! Launch date holds unless the fix slips past Wednesday.
""")])

T["okr_lena"] = dict(
    title="OKR alignment with Lena", project=P_Q3, state="meetings", state_name="1:1",
    summary="1:1 with Lena on Q3 OKRs: replaced KR3 with a 7-day activation target (31% → 38%) and agreed the "
            "Billing v2 phase 2 scope.",
    quality=Q(66, 64, 72, 78, "One meeting with notes; clear decisions."),
    insights=[I("KR3 is now tied to the onboarding revamp",
                "The experiment spec should report against 7-day activation directly.",
                "Lena and you agreed on 'raise 7-day activation from 31% to 38%' in the OKR doc.", ZOOM, NOTION)],
    entities=[Q3, LENA, ACTIVATION, E("Q3 OKRs", "document", 80, "OKRs")],
    beats=[B(ZOOM, "Zoom Meeting — Lena / Maya 1:1", 28, """
Lena / Maya 1:1
Participants (2): Lena Fischer, Maya Chen
"""), B(NOTION, "Q3 OKRs", 14, """
Q3 OKRs — Product
O1: Make billing predictable
  KR1: Invoice-related tickets down 30% (baseline: 214 / month)
  KR2: 100% of new signups on Billing v2 by week 4
O2: Get new teams to value faster
  KR3: Raise 7-day activation from 31% to 38%
  KR4: Median time to first invoice under 10 minutes
""")])

T["invoice_copy"] = dict(
    title="Invoice email copy", project=P_BILL, state="writing", state_name="Writing",
    summary="Rewrote the Billing v2 invoice email in Google Docs so usage is explained in one sentence, and "
            "checked it against Diego's invoice template in Figma.",
    quality=Q(79, 72, 76, 70, "Steady writing with one design reference."),
    insights=[I("Writing sessions go faster with the design open",
                "Pairing copy and template review avoided a second pass this time.",
                "You alternated between the doc and the Figma template 4 times and finished in one session.", CHROME, FIGMA)],
    entities=[BILLING, DIEGO, E("Invoice email copy", "document", 80, "invoice email")],
    beats=[B(CHROME, "Invoice email copy v3 — Google Docs", 30, """
Invoice email copy v3
Subject: Your Northwind invoice for {month}: {amount}
Hi {first_name},
Your invoice for {month} is ready. You used {events} events this month, which falls in the {tier} tier.
What changed: {change_summary}
View invoice · Download usage CSV
Questions? Reply to this email and our team will help.
Notes
- Lead with the amount and the tier, not the line items.
- "What changed" only shows when seats or tier changed mid-cycle (prorations).
""", "docs.google.com"), B(FIGMA, "Billing v2 — Invoice templates", 12, """
Billing v2 — Invoice templates
Frame: Email / Monthly invoice
Header: amount + tier badge
Usage bar: 7,840 / 10,000 events
Line items (max 4) · "What changed" callout
Diego Alvarez: Swapped the table for a usage bar, much easier to scan.
""")])

T["pricing_sync"] = dict(
    title="Pricing page sync", project=P_BILL, state="meetings", state_name="Meetings",
    summary="Synced with Jonah on the Billing v2 pricing page: lead with usage tiers, hold annual discount copy "
            "until Finance confirms.",
    quality=Q(64, 58, 70, 72, "Short meeting plus follow-up notes."), insights=[],
    entities=[BILLING, JONAH, E("Pricing page", "document", 70)],
    beats=[B(ZOOM, "Zoom Meeting — Billing v2 pricing page sync", 25, """
Billing v2 pricing page sync
Participants (3): Maya Chen, Jonah Reyes, Aisha Khan
"""), B(NOTION, "Pricing page — decisions", 10, """
Pricing page — decisions
- Hero: "Pay for what you use."
- Tiers: Starter 1k events · Growth 10k events · Scale 50k events
- Annual discount copy: on hold until Finance confirms (Aisha, Friday)
- FAQ: add "What happens when I change seats mid-month?" (prorations)
""")])

T["roadmap_prep"] = dict(
    title="Roadmap review prep", project=P_Q3, state="deep-work", state_name="Deep Work",
    summary="Built the Q3 roadmap review deck in Keynote from the Notion draft and capacity sheet; added the "
            "activation KR and the usage insights trade-off slide.",
    quality=Q(82, 76, 80, 70, "Long, mostly uninterrupted deck work."),
    insights=[I("Deck work took 75 minutes with almost no interruptions",
                "Blocking calendar time worked; repeat it before the board update.",
                "Only one Slack check during the whole session.", KEYNOTE, NOTION)],
    entities=[Q3, LENA, E("Q3 Roadmap Review deck", "document", 80, "roadmap deck")],
    beats=[B(KEYNOTE, "Q3 Roadmap Review.key", 45, """
Q3 Roadmap Review
1 Where we are: Billing v2 launching next week; activation 31.4% (Oct)
2 Q3 theme: predictable billing, faster time to value
3 Bet 1: Billing v2 phase 2 — monthly customers at renewal (40 eng-weeks)
4 Bet 2: Onboarding revamp — KR3: 7-day activation 31% → 38% (30 eng-weeks)
5 Bet 3: Usage insights v1 — trend only (16 eng-weeks)
6 Trade-off: forecast moves to Q4 (Data capacity: 4 free weeks)
7 Asks: Finance review of pricing; one more Data engineer in Q4
"""), B(NOTION, "Q3 Roadmap (draft)", 15, """
Q3 Roadmap (draft) — updated
Usage insights v1: trend only. Forecast moved to Q4 because of Data capacity.
Review: Thursday 2pm with Lena, Finance (Aisha) and Eng leads.
""")])

T["experiment_spec"] = dict(
    title="Onboarding experiment spec", project=P_ONB, state="writing", state_name="Writing",
    summary="Wrote the experiment spec 'checklist vs. guided tour' for teams under 10 seats, primary metric 7-day "
            "activation, and filed ONB-37 in Linear.",
    quality=Q(81, 78, 74, 68, "Focused writing with a quick Linear detour."),
    insights=[I("The spec reuses the funnel numbers directly",
                "Baseline (31.4%) and target (38%) are already agreed, so review should be quick.",
                "You copied the Oct cohort numbers into the spec's baseline section.", NOTION, LINEAR)],
    entities=[ONB, SAM, DIEGO, ACTIVATION, E("Experiment: checklist vs. guided tour", "document", 80, "ONB-37")],
    beats=[B(NOTION, "Experiment: checklist vs. guided tour", 38, """
Experiment: checklist vs. guided tour
Owner: Maya Chen · Design: Diego Alvarez · Data: Sam Okafor
Hypothesis: moving "invite teammates" to a post-setup checklist raises 7-day activation for teams of 1–9 seats.
Variants
A (control): current 5-step flow
B: 4-step flow + checklist (invite teammates, connect Stripe, customize invoice)
Primary metric: 7-day activation (first invoice sent within 7 days). Baseline 31.4%.
Guardrails: teammates invited within 14 days; support tickets per new workspace.
Sample: ~1,500 workspaces per arm, 3 weeks.
"""), B(LINEAR, "ONB-37 Experiment: checklist vs. guided tour", 8, """
ONB-37  Experiment: checklist vs. guided tour
Status: Todo · Project: Onboarding revamp · Assignee: Diego Alvarez
Spec: Notion → Experiment: checklist vs. guided tour
Sub-issues: ONB-38 event tracking (Sam), ONB-39 checklist UI (Diego)
""")])

T["finance_recon"] = dict(
    title="Revenue reconciliation check", project=P_BILL, state="research", state_name="Analysis",
    summary="Walked through Aisha's October reconciliation for Billing v2: totals match within $38; two annual "
            "accounts need manual review before Finance signs off.",
    quality=Q(70, 72, 66, 70, "Spreadsheet review with a few Slack questions."), insights=[],
    entities=[BILLING, AISHA, E("Revenue reconciliation (Oct)", "document", 75, "reconciliation")],
    beats=[B(CHROME, "Billing v2 — revenue reconciliation (Oct) — Google Sheets", 26, """
Billing v2 — revenue reconciliation (Oct)
Source | Invoiced | Collected | Difference
Legacy billing | $412,880 | $409,102 | $3,778
Billing v2 (shadow) | $412,842 | — | −$38
Accounts needing review
Tidewater Co (annual) | proration on renewal date
Harbor & Pine (monthly) | double charge on the 14th (BIL-142, credited)
""", "docs.google.com"), B(SLACK, "Aisha Khan (DM) — Northwind", 9, """
Aisha Khan  2:21 PM  Difference is $38 total, rounding on 3 invoices. Fine for sign-off.
Aisha Khan  2:22 PM  I want to check Tidewater manually, the annual proration looks off.
Maya Chen  2:25 PM  Sounds good. Can you confirm by Wednesday so we keep Thursday's go/no-go?
""")])

T["escalation"] = dict(
    title="Harbor & Pine escalation", project=P_BILL, state="communication", state_name="Customer Support",
    summary="Handled the Harbor & Pine double-charge escalation: approved a $1,240 credit, explained the fix "
            "timeline and booked a follow-up call.",
    quality=Q(60, 54, 58, 76, "Several short replies, quick recovery after interruptions."),
    insights=[I("Escalations about BIL-142 are now handled in under an hour",
                "The credit macro is working; reuse it for the remaining accounts.",
                "From Tom's first message to your reply took 38 minutes.", MAIL, SLACK)],
    entities=[HARBOR, TOM, BILLING, PRORATION],
    beats=[B(MAIL, "Re: Double charge on our October invoice", 16, """
From: Dana Whitfield (Harbor & Pine)
Subject: Double charge on our October invoice
We upgraded from 12 to 20 seats on the 14th and were charged for both. Can you help?

Reply
Hi Dana, you're right, and I'm sorry. A bug charged the overlap twice.
We've credited $1,240 to your account today. The fix ships with Billing v2 next week, and
we'd love your feedback on the new invoices as a design partner.
Maya Chen, Product Manager, Northwind
"""), B(SLACK, "#support-escalations — Northwind", 10, """
Tom Becker  9:41 AM  Harbor & Pine credit is processed ✅
Maya Chen  9:44 AM  Thanks Tom. 8 more accounts to credit, can we do them all today with the macro?
Tom Becker  9:46 AM  Yes, I'll batch them this afternoon.
""")])

T["onb_v5"] = dict(
    title="Onboarding flows v5 review", project=P_ONB, state="design-review", state_name="Design Review",
    summary="Reviewed v5 of the onboarding flows with Diego: 4 steps, checklist after setup, Stripe connect promoted "
            "to step 2. Approved for the experiment.",
    quality=Q(75, 70, 72, 70, "Figma review with a short Slack exchange."), insights=[],
    entities=[ONB, DIEGO, E("Onboarding flows v5", "document", 80, "flows v5")],
    beats=[B(FIGMA, "Onboarding revamp — flows v5", 28, """
Onboarding revamp — flows v5
Page: First run (4 steps)
1 Welcome — "Send your first invoice in 3 minutes"
2 Connect Stripe (recommended) or upload CSV
3 Create your first invoice
4 Done — checklist: invite teammates · customize invoice · set usage alerts
Comments
Maya Chen: Approved for the experiment. Ship variant B behind the onboarding_v5 flag.
"""), B(SLACK, "#onboarding-revamp — Northwind", 6, """
Diego Alvarez  11:31 AM  v5 approved 🎉 handing off to eng for ONB-39
Sam Okafor  11:33 AM  Event plan for ONB-38 is in the spec, should be done Friday
""")])

T["roadmap_review"] = dict(
    title="Q3 roadmap review", project=P_Q3, state="meetings", state_name="Meetings",
    summary="Presented the Q3 roadmap to Lena, Finance and eng leads. Approved with one change: usage insights v1 "
            "ships trend-only; forecast deferred to Q4.",
    quality=Q(68, 66, 74, 72, "Presentation plus live Q&A notes."),
    insights=[I("The Q3 roadmap was approved in one review",
                "Capacity numbers in the deck pre-empted the usual sizing debate.",
                "Notes show no follow-up review was requested.", ZOOM, NOTION)],
    entities=[Q3, LENA, AISHA, PRIYA, E("Q3 Roadmap Review deck", "document", 70, "roadmap deck")],
    beats=[B(ZOOM, "Zoom Meeting — Q3 roadmap review", 45, """
Q3 roadmap review
Participants (7): Maya Chen, Lena Fischer, Aisha Khan, Priya Raman, Sam Okafor, Diego Alvarez, Ravi Patel
Maya Chen is sharing "Q3 Roadmap Review.key"
"""), B(NOTION, "Q3 roadmap review — notes", 12, """
Q3 roadmap review — notes
Decision: approved.
Change: usage insights v1 is trend-only; forecast deferred to Q4.
Finance: pricing review of annual discounts by next Friday (Aisha).
Follow-up: request one more Data engineer for Q4 (Lena).
""")])

T["launch_checklist"] = dict(
    title="Billing v2 launch checklist", project=P_BILL, state="planning", state_name="Planning",
    summary="Updated the Billing v2 launch checklist: Finance signed off, support macros ready, BIL-142 fix merged "
            "and waiting for staging verification. Drafted the go/no-go email to Lena.",
    quality=Q(77, 70, 74, 72, "Checklist and Linear cycle review, then a focused email."),
    insights=[I("Only one launch criterion is still open",
                "Verifying BIL-142 on staging is the single item between you and go/no-go.",
                "The checklist shows 11 of 12 items done.", NOTION, LINEAR)],
    entities=[BILLING, LENA, AISHA, TOM, E("Launch checklist", "document", 85, "Billing v2 launch checklist"),
              E("BIL-142", "document", 70, "proration bug")],
    beats=[B(NOTION, "Billing v2 — Launch checklist", 26, """
Billing v2 — Launch checklist (11 / 12 done)
✅ PRD signed off (Lena)
✅ Pricing page live behind flag (Jonah)
✅ Revenue reconciliation: Finance sign-off (Aisha)
✅ Support macros + help center article (Tom)
✅ Invoice email copy v3 (Maya)
✅ Credits for 9 accounts affected by BIL-142 (Tom)
✅ Dashboards: invoices, disputes, ticket volume (Sam)
☐ BIL-142 fix verified on staging (Maya)
Go/no-go: Thursday 10:00
"""), B(LINEAR, "Billing v2 — Cycle 47", 10, """
Billing v2 — Cycle 47
Done 14 · In Progress 2 · Todo 1
BIL-142 Proration double-charges on mid-cycle upgrades — In Review — Priya Raman
BIL-151 Usage CSV export — In Progress — Priya Raman
BIL-153 Invoice email template — Done — Diego Alvarez
"""), B(MAIL, "Billing v2 go/no-go — draft", 12, """
To: Lena Fischer
Cc: Priya Raman, Aisha Khan, Tom Becker
Subject: Billing v2 go/no-go — Thursday

Hi Lena,
We're on track for go on Thursday. 11 of 12 launch criteria are done; the last one is verifying the
BIL-142 proration fix on staging, which I'll do Wednesday morning.
Finance has signed off and support macros are live. Proposed rollout: new self-serve signups first.
Maya
""")])

T["status_update"] = dict(
    title="Weekly status update", project=None, state="writing", state_name="Writing",
    summary="Wrote and posted the weekly product update in #product: Billing v2 go/no-go Thursday, Q3 roadmap "
            "approved, onboarding experiment starting next week.",
    quality=Q(73, 62, 76, 70, "Short writing session; pulled numbers from earlier work."),
    insights=[I("Glint had every number for the update",
                "Starting the weekly update from Ask could save most of this session.",
                "All figures in the update appeared in this week's sessions.", NOTION, SLACK)],
    entities=[BILLING, Q3, ONB],
    beats=[B(NOTION, "Weekly update — Week 46", 18, """
Weekly update — Week 46
Billing v2: go/no-go Thursday. 11/12 launch criteria done. Finance signed off. 9 affected accounts credited.
Onboarding revamp: v5 flows approved; experiment (ONB-37) starts next week for teams of 1–9 seats.
Q3 planning: roadmap approved; usage insights v1 is trend-only.
Metrics: 7-day activation 31.4% (Sep 28.9%). Invoice tickets −8% WoW.
"""), B(SLACK, "#product — Northwind", 6, """
#product
Maya Chen  3:52 PM  Weekly update for week 46 is in Notion 👉 Billing v2 go/no-go Thursday, Q3 roadmap approved, onboarding experiment next week.
Lena Fischer  3:58 PM  Great week, thank you!
""")])

T["proration_verify"] = dict(
    title="Verify proration fix on staging", project=P_BILL, state="deep-work", state_name="Testing",
    summary="Verified the BIL-142 fix on staging: a 12 → 20 seat upgrade produced one prorated line item of "
            "$412.90 and the downgrade applied at the next cycle. Marked the last checklist item done.",
    quality=Q(86, 80, 82, 76, "Tight loop between staging, Stripe test dashboard and Linear."),
    insights=[I("Billing v2 is ready for go/no-go",
                "All 12 launch criteria are done; the go/no-go can be a short confirmation.",
                "You checked off 'BIL-142 fix verified on staging' at the end of this session.", CHROME, NOTION),
              I("The upgrade amount matched the expected $412.90",
                "No need for a second test pass; note the result in BIL-142 for Finance.",
                "Staging invoice in-test-0042 showed one prorated item of $412.90.", CHROME, LINEAR)],
    entities=[BILLING, PRORATION, PRIYA, E("BIL-142", "document", 90, "proration bug"),
              E("Launch checklist", "document", 70, "Billing v2 launch checklist")],
    beats=[B(CHROME, "Northwind Admin (staging) — Harbor Test Co — Subscription", 22, """
Northwind Admin — STAGING
Customer: Harbor Test Co · Plan: Growth (monthly) · Seats: 12
Change seats → 20 · Effective: now (prorated)
Preview invoice in-test-0042
Prorated seat upgrade (8 seats × 16 days remaining)  $412.90
Total due today  $412.90
Change seats → 12 · Effective: next billing period (Dec 14)
Scheduled change: 20 → 12 seats on Dec 14. No credit note created.
""", "admin.staging.northwind.example"), B(LINEAR, "BIL-142 Proration double-charges on mid-cycle upgrades", 8, """
BIL-142  Proration double-charges on mid-cycle upgrades
Status: Done · Priority: Urgent · Assignee: Priya Raman
Maya Chen commented: Verified on staging. Upgrade 12 → 20: single prorated item $412.90 (expected $412.90).
Downgrade 20 → 12 scheduled for next cycle, no credit note. 🎉
"""), B(NOTION, "Billing v2 — Launch checklist", 6, """
Billing v2 — Launch checklist (12 / 12 done)
✅ BIL-142 fix verified on staging (Maya)
Go/no-go: Thursday 10:00 — recommendation: GO
""")])

T["go_no_go"] = dict(
    title="Go/no-go prep", project=P_BILL, state="planning", state_name="Planning",
    summary="Prepared the Billing v2 go/no-go: rollout plan for new signups, rollback steps, owners for launch day, "
            "and a one-page summary for Lena.",
    quality=Q(80, 74, 78, 74, "Focused doc work with a quick Slack confirmation."),
    insights=[I("Rollback plan is the only new section",
                "Everything else in the go/no-go doc already existed in the checklist and PRD.",
                "You linked 5 existing docs and wrote only the rollback section from scratch.", NOTION, SLACK)],
    entities=[BILLING, LENA, PRIYA, TOM, E("Go/no-go summary", "document", 80, "go/no-go doc")],
    beats=[B(NOTION, "Billing v2 — Go/no-go summary", 32, """
Billing v2 — Go/no-go summary
Recommendation: GO for new self-serve signups on Monday.
Launch criteria: 12 / 12 done (checklist linked).
Rollout: Day 1 new signups (flag billing_v2_new), Day 8 monthly renewals (phase 2 starts Q3).
Rollback: flip billing_v2_new off; invoices fall back to legacy; no data migration needed.
Launch-day owners: Priya (eng on-call), Tom (support), Aisha (finance), Sam (dashboards), Maya (comms).
Risks: invoice email confusion → macros ready; proration edge cases → monitored in #billing-v2.
"""), B(SLACK, "#billing-v2 — Northwind", 7, """
Maya Chen  11:44 AM  Go/no-go doc is ready 👉 recommendation GO for Monday. Please add anything I missed.
Priya Raman  11:46 AM  Looks complete. I'll be on call Monday.
Tom Becker  11:47 AM  Support is ready 💪
""")])

T["q3_final"] = dict(
    title="Q3 plan final numbers", project=P_Q3, state="planning", state_name="Planning",
    summary="Finalized the Q3 plan after the review: updated the capacity sheet, moved forecast to Q4 and "
            "sent the summary to eng leads.",
    quality=Q(74, 66, 72, 70, "Sheet updates and a short email."), insights=[],
    entities=[Q3, LENA, E("Q3 capacity plan", "document", 75, "capacity sheet")],
    beats=[B(CHROME, "Q3 capacity plan — Google Sheets", 22, """
Q3 capacity plan (final)
Bet | Team | Eng-weeks | Status
Billing v2 phase 2 | Billing | 40 | Approved
Onboarding revamp | Growth | 30 | Approved
Usage insights v1 (trend only) | Billing + Data | 16 | Approved
Usage forecast | Data | — | Moved to Q4
""", "docs.google.com"), B(MAIL, "Q3 plan — final", 9, """
To: eng-leads@northwind.example
Subject: Q3 plan — final

Hi all, the Q3 plan is final. Three bets, sized in the capacity sheet. Forecasting moves to Q4.
Kickoffs next Monday. Thanks for the quick turnaround!
Maya
""")])

T["usage_research"] = dict(
    title="Usage-based pricing research", project=P_BILL, state="research", state_name="Reading",
    summary="Read about usage-based pricing and invoice design for the Billing v2 phase 2 plan, and saved three "
            "ideas to Notion.",
    quality=Q(68, 64, 60, 62, "Reading with a few tab switches."), insights=[],
    entities=[BILLING, E("Usage-based pricing", "topic", 70, "usage tiers")],
    beats=[B(CHROME, "Usage alerts | Stripe Documentation", 16, """
Usage-based billing: usage alerts
Notify customers when their usage crosses a threshold, e.g. 80% of their tier.
Alerts can be sent by email or webhook so you can show them in your product.
""", "docs.stripe.com"), B(NOTION, "Billing v2 — phase 2 ideas", 10, """
Billing v2 — phase 2 ideas
- Usage alert at 80% of tier (Bluefield asked for this)
- Daily usage CSV export (Bluefield, Harbor & Pine)
- "What changed" callout on every invoice with a proration
""")])


# Day plans: offset from today -> [(template key or ("catchup", n)), start "HH:MM")]
DAYS = {
    -6: [(("catchup", 0), "08:50"), ("q3_roadmap", "09:35"), ("prd_review", "11:00"), ("onb_flows", "13:40"),
         ("weekly_sync", "15:30")],
    -5: [(("catchup", 1), "08:45"), ("funnel", "09:25"), ("interview", "11:00"), ("capacity", "13:30"),
         ("code_review", "15:15")],
    -4: [(("catchup", 2), "09:05"), ("proration_triage", "09:40"), ("okr_lena", "11:30"), ("invoice_copy", "13:45"),
         ("pricing_sync", "15:30")],
    -3: [(("catchup", 3), "08:55"), ("roadmap_prep", "09:35"), ("experiment_spec", "11:15"),
         ("finance_recon", "14:00")],
    -2: [(("catchup", 4), "09:00"), ("escalation", "09:30"), ("onb_v5", "11:00"), ("roadmap_review", "14:00"),
         ("usage_research", "16:00")],
    -1: [(("catchup", 5), "08:50"), ("launch_checklist", "09:30"), ("status_update", "14:10")],
    0: [(("catchup", 6), "08:45"), ("proration_verify", "09:20"), ("go_no_go", "10:55"), ("q3_final", "13:30")],
}


# ------------------------------------------------------------------ tasks

TASKS = [
    # (title, progress, created days ago, [(days ago, hour, log)])
    ("Verify BIL-142 fix on staging", 100, 4, [(4, 10, "Repro confirmed with Priya; fix in review"),
                                               (0, 10, "Verified on staging: one prorated item, $412.90")]),
    ("Billing v2 go/no-go", 80, 3, [(1, 10, "11/12 criteria done"), (0, 11, "Go/no-go doc ready, recommending GO")]),
    ("Write onboarding experiment spec (ONB-37)", 100, 6, [(3, 12, "Spec written and filed in Linear")]),
    ("Send Bluefield invoice mockups", 40, 5, [(4, 14, "Diego exported the email template")]),
    ("Q3 roadmap review deck", 100, 5, [(3, 10, "Deck drafted"), (2, 15, "Approved in review")]),
    ("Pricing page feedback to Jonah", 100, 5, [(5, 9, "Sent three notes by email")]),
    ("Usage CSV export: decide phase 1 vs 2", 20, 2, [(2, 16, "Both design partners asked for it")]),
    ("Credit the 9 accounts hit by BIL-142", 100, 4, [(2, 9, "Harbor & Pine credited"), (2, 15, "All 9 done (Tom)")]),
    ("Request Data headcount for Q4", 10, 1, []),
]

SKILLS = [
    ("Daily standup", "/standup", "Yesterday, today and blockers from my work sessions.",
     "Look at my work sessions from the previous working day and today so far. Write a standup in three short "
     "sections: Yesterday, Today, Blockers. Bullets only, max 4 per section. Name the project for each bullet."),
    ("Weekly update", "/weekly-update", "Draft the Friday #product update from this week's sessions.",
     "Draft my weekly product update from this week's sessions and tasks. One line per project (Billing v2, "
     "Onboarding revamp, Q3 Planning), then a Metrics line. Use numbers I actually saw. Keep it under 120 words."),
    ("PRD review", "/prd-review", "Review a PRD for gaps: metrics, non-goals, risks, rollout.",
     "Review the PRD I'm looking at (read the foreground window). Check for: a clear problem, measurable success "
     "metrics, non-goals, risks with mitigations, and a rollout plan. List gaps as bullets, most important first."),
]
