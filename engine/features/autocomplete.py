"""Autocomplete everywhere: short AI continuations for whatever the user is typing.

The native shell (mac/Autocomplete.swift) watches the focused text field, and after a typing
pause asks POST /api/autocomplete/suggest for continuations of the text before the caret. This
module gathers compact, relevant context (the conversation or document visible in the same window,
a couple of matching memory passages, how the user writes in this app, the app/site and a cleaned
window title), routes the call to the fastest suitable model (a cloud model when a key exists,
with the on-device model as the fallback), filters the result through quiet rules, and answers
within a time budget so the suggestion appears while it is still useful.

The user's own writing (finished sentences they typed, plus accepted suggestions) is kept as a
bounded per-app sample store that later prompts use to match their voice. It never leaves the Mac
except inside the prompt sent to the user's own AI provider (none when on-device).
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, wait, FIRST_COMPLETED
from datetime import date

import ai
import engine as E

E.DEFAULT_SETTINGS.update({
    "autocomplete_enabled": True,
    "autocomplete_style": "ghost",          # ghost (inline, falls back to under) | under | normal
    "autocomplete_mode": "automatic",       # automatic | on_demand (Option+Tab summons)
    "autocomplete_accept_key": "tab",       # tab | backtick | both
    "autocomplete_disabled_apps": [],
    "autocomplete_min_chars": 12,
    "autocomplete_pause_ms": 350,
    "autocomplete_model": "",               # blank: Claude Haiku / the OpenAI model chosen for Ask
    "autocomplete_model_mode": "auto",      # auto (cloud when a key exists) | on_device | cloud
    "autocomplete_learn_typing": True,      # learn the user's voice from sentences they finish typing
})

STYLES = {"under", "normal", "ghost"}
MODEL_MODES = {"auto", "on_device", "cloud"}
TIME_BUDGET_S = 2.5          # answer "too slow" rather than show a stale suggestion
CLOUD_TIMEOUT_S = 1.5        # auto mode: a cloud call slower than this falls back to on-device
CLOUD_HEDGE_S = 0.8          # auto mode: start the on-device call too if the cloud is this slow
ON_DEVICE_TIMEOUT_S = 6.0    # hard stop for on-device calls (their late result is still cached)
MIN_GAP_S = 0.2              # minimum spacing between model calls
MAX_PER_MINUTE = 40
MAX_INFLIGHT = 2
CACHE_SIZE = 300
CACHE_TTL_S = 600
SAMPLES_PER_APP = 40
SAMPLE_APPS_MAX = 80
SAMPLE_MAX_CHARS = 400
MAX_SUGGESTION_CHARS = 140
MAX_WORDS = 12
CONTEXT_CHARS = 6000         # whole prompt budget (the on-device model has a ~4k-token window)
WINDOW_CHARS = 1200
FACT_DAYS = 14
LEGACY = os.environ.get("GLINT_AC_LEGACY") == "1"   # run the previous prompt (eval baseline)

SCHEMA = """
CREATE TABLE IF NOT EXISTS autocomplete_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, app TEXT NOT NULL, text TEXT NOT NULL,
    created_at INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'accepted');
CREATE INDEX IF NOT EXISTS idx_ac_samples_app ON autocomplete_samples(app, created_at DESC);
"""

_lock = threading.Lock()
_cache: OrderedDict = OrderedDict()          # key -> (ts, suggestions)
_recent_calls: deque = deque()               # model call timestamps (rate limit)
_inflight = 0
_last_call = 0.0
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="autocomplete")
_cloud_cache: dict = {"key": None, "at": 0.0, "value": None}
_stats_lock = threading.Lock()
_schema_ok = False
_model_used = 0.0                            # last on-device call (the model cools after ~5 s idle)
_warming = threading.Event()


# ----------------------------------------------------------------- helpers

def _settings() -> dict:
    return E.load_settings()


def _db():
    """Feature DB connection with this module's table (and its `source` column) in place."""
    global _schema_ok
    d = E.db()
    if not _schema_ok:
        d.executescript(SCHEMA)
        cols = {r[1] for r in d.execute("PRAGMA table_info(autocomplete_samples)")}
        if "source" not in cols:
            d.execute("ALTER TABLE autocomplete_samples ADD COLUMN source TEXT NOT NULL DEFAULT 'accepted'")
            d.commit()
        _schema_ok = True
    return d


def _app_key(app: str, url_host: str | None) -> str:
    """Writing samples are kept per site for browsers (mail vs chat differ), else per app."""
    host = (url_host or "").lower().removeprefix("www.")
    return host or (app or "unknown").strip().lower()


def _disabled(app: str, bundle_id: str, s: dict) -> bool:
    names = {(a or "").strip().lower() for a in s.get("autocomplete_disabled_apps") or []}
    return bool(names & {(app or "").lower(), (bundle_id or "").lower()} - {""})


def _clip_tail(text: str, n: int) -> str:
    return text if len(text) <= n else "…" + text[-n:]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


URL_RE = re.compile(r"(https?://|www\.)\S*$|^\S+\.(com|org|net|io|dev|app|ai)(/\S*)?$", re.I)
CODE_RE = re.compile(r"(^|\n)\s*(def |class |function |import |from \S+ import|#include|const |let |var |public |private |return\b)|[{};]\s*$|=>|</?[a-z]+[^>]*>|\$\(|\b[a-z_]+\([^)]*\)\s*$", re.I)


def quiet_reason_for_input(before: str, after: str, s: dict) -> str | None:
    """Local quiet rules: cases where a continuation would be useless or unwelcome."""
    stripped = before.strip()
    if len(stripped) < max(1, int(s.get("autocomplete_min_chars") or 12)):
        return "too_short"
    if after.split("\n", 1)[0].strip():
        return "mid_line"
    last_line = before.rsplit("\n", 1)[-1]
    if not last_line.strip():
        return "empty_line"
    tail_token = last_line.split()[-1] if last_line.split() else ""
    if URL_RE.search(tail_token) or (" " not in stripped and URL_RE.search(stripped)):
        return "url"
    if " " not in stripped and len(stripped) >= 8 and re.search(r"\d", stripped) and re.search(r"[^\w\s]", stripped):
        return "password_like"
    if " " not in stripped and len(stripped) < 40:
        return "single_token"    # usernames, search terms, codes
    recent = "\n".join(before.splitlines()[-4:])
    if len(CODE_RE.findall(recent)) >= 2 or recent.count("`") >= 2 and "```" in recent:
        return "code"
    return None


def _cap_words(c: str, n: int = MAX_WORDS) -> str:
    """At most n words; prefer to stop at the last clause end inside the limit."""
    words = re.findall(r"\S+\s*", c.lstrip(" "))
    lead = c[:len(c) - len(c.lstrip(" "))]
    if len(words) <= n:
        return c
    kept = words[:n]
    for i in range(len(kept) - 1, max(2, n // 2) - 1, -1):
        if kept[i].rstrip()[-1:] in ".,;:!?":
            kept = kept[:i + 1]
            break
    return lead + "".join(kept).rstrip()


def clean_suggestions(cands: list, before: str, after: str, n: int) -> tuple[list[str], str | None]:
    """Normalise model continuations and drop ones that add nothing."""
    cands = [c for c in (cands or []) if isinstance(c, str) and c.strip() and c.strip().upper() != "NONE"]
    if not cands:
        return [], "unsure"
    out: list[str] = []
    tail = before[-200:]
    reason = None
    for c in cands:
        c = c.replace("\n", " ").replace(" ", " ")
        # The model sometimes repeats the end of the typed text; keep only what is new.
        for k in range(min(len(c), len(tail)), 3, -1):
            if tail.endswith(c[:k]):
                c = c[k:]
                break
        if not c.strip():
            reason = reason or "nothing_new"
            continue
        # Spacing at the seam between typed text and suggestion.
        if before and before[-1].isspace():
            c = c.lstrip(" ")
        elif before and before[-1] in ".,!?;:)" and c[:1].isalnum():
            c = " " + c
        c = _cap_words(c)
        if len(c) > MAX_SUGGESTION_CHARS:
            cut = c[:MAX_SUGGESTION_CHARS].rsplit(" ", 1)[0]
            c = cut if cut.strip() else c[:MAX_SUGGESTION_CHARS]
        c = c.rstrip()
        core = _norm(c)
        if len(core) < 2:
            reason = reason or "nothing_new"
            continue
        # Quiet: the continuation only repeats text that is already in the field.
        if len(core) >= 12 and (core in _norm(before[-1500:]) or core in _norm(after[:1500])):
            reason = reason or "repeats"
            continue
        # Quiet: it restates the sentence being written (a loop) rather than continuing it.
        last_sentence = _norm(re.split(r"(?<=[.!?])\s+", before.strip())[-1])
        if len(core) >= 12 and len(last_sentence) >= 12 and core.strip(" .") in last_sentence:
            reason = reason or "repeats"
            continue
        # Quiet: mostly the same words as the end of the text (a paraphrased echo).
        cw = re.findall(r"\w+", core)
        bw = set(re.findall(r"\w+", before[-300:].lower()))
        if len(cw) >= 5 and sum(w in bw for w in cw) / len(cw) >= 0.85:
            reason = reason or "repeats"
            continue
        if all(_norm(o) != core for o in out):
            out.append(c)
        if len(out) >= n:
            break
    return out, (None if out else reason or "unsure")


# ------------------------------------------------------------------ titles

BROWSER_SUFFIX = re.compile(r"\s+[-–—]\s+(Google Chrome|Chromium|Brave|Microsoft Edge|Arc|Safari|Firefox|Vivaldi|Opera)"
                            r"(\s+[-–—]\s+.*)?$")
UNREAD = re.compile(r"^\(\d+\+?\)\s*|^\*\s*")


def clean_title(title: str) -> str:
    """Drop the browser name and profile label (e.g. ' - Google Chrome - Work') from window titles."""
    return BROWSER_SUFFIX.sub("", title or "").strip()


def context_label(app: str, title: str, host: str | None) -> str:
    """A short human description of where the user is typing: 'Gmail, email "Re: Q3 budget"',
    'Slack #design', 'Slack, message to Dana Lee', 'Google Docs, "Launch plan"'."""
    t = UNREAD.sub("", clean_title(title))
    host = (host or "").lower().removeprefix("www.")
    app_l = (app or "").lower()
    if host == "mail.google.com" or t.endswith("- Gmail"):
        m = re.match(r"^(.*?)\s+-\s+\S+@\S+\s+-\s+Gmail$", t)
        subj = (m.group(1) if m else t.rsplit(" - ", 1)[0]).strip()
        return "Gmail" + (f', email "{subj}"' if subj and not subj.lower().startswith(("inbox", "sent", "drafts")) else "")
    if "outlook" in host or app_l == "microsoft outlook" or app_l == "mail":
        subj = re.sub(r"\s+[-–—]\s+(Outlook|Mail|Inbox).*$", "", t).strip()
        return f"{app or 'Mail'}" + (f', email "{subj}"' if subj else "")
    if app_l == "slack" or host.endswith("slack.com"):
        m = re.match(r"^(.*?)\s+\((Channel|DM|Private channel|Group DM)\)\s+-\s+(.*?)\s+-\s+Slack", t)
        if m:
            kind, name = m.group(2), m.group(1).strip()
            return f"Slack, message to {name}" if "DM" in kind else f"Slack #{name.lstrip('#')}"
        m = re.match(r"^Slack\s*\|\s*([^|]+)\|", t)
        if m:
            return f"Slack {m.group(1).strip()}"
        return "Slack"
    if app_l == "discord" or host == "discord.com":
        return "Discord" + (f" {t.split(' | ')[0]}" if t else "")
    if host == "web.whatsapp.com" or app_l == "whatsapp":
        return "WhatsApp"
    if app_l == "messages":
        return "Messages" + (f", chat with {t}" if t and t != "Messages" else "")
    if host == "docs.google.com":
        name = re.sub(r"\s+-\s+Google (Docs|Sheets|Slides)$", "", t)
        kind = re.search(r"Google (Docs|Sheets|Slides)", t)
        return f"Google {kind.group(1) if kind else 'Docs'}" + (f', "{name}"' if name else "")
    if host.endswith("linear.app") or app_l == "linear":
        return "Linear" + (f', "{t}"' if t else "")
    if host.endswith("notion.so") or app_l == "notion":
        return "Notion" + (f', "{t}"' if t else "")
    t = re.sub(r"\s+[-–—|]\s+" + re.escape(app or "§") + r"$", "", t) if app else t
    label = app or "an app"
    if host and host not in label.lower():
        label += f" ({host})"
    return label + (f', "{t[:100]}"' if t and t.lower() != (app or "").lower() else "")


# --------------------------------------------------------------- context

_WORDS: set | None = None


def _words() -> set:
    global _WORDS
    if _WORDS is None:
        try:
            with open("/usr/share/dict/words") as f:
                _WORDS = {w.strip().lower() for w in f}
        except OSError:
            _WORDS = set()
    return _WORDS


def _is_word(w: str) -> bool:
    return w.lower() in _words()


WORD_RE = re.compile(r"[A-Za-z][\w'’-]{2,}")
COMMON_CAPS = set("""I I'm I'll I've I'd Hi Hey Hello Thanks Thank Dear Best Regards Cheers Yes No Ok Okay Sure
Sounds Great Good Also And But So The This That These Those It It's We We're We'll You You're Let Let's Just
Please Sorry Re Fwd Fw Mr Mrs Ms Dr Maybe Could Would Should Can Will Did Do Does Is Are Was Were Not What When
Where Why How Who Which If Then There Here My Our Your Their Any All Some Happy Looking Glad Hope Of In On At
For To From With As After Before Since Today Tomorrow Yesterday Tonight Next Last Yeah Yep Nope Cool Nice Awesome
Perfect Agreed Noted Got Sent See Talk Call Remember Note Action Recap Timeline Goal Ideas Thursday's""".split())


VAGUE = set("""about above after again against almost already also always another anyone anything around away
back because been before being below best better between both came come coming could does doing done down during
each either else enough even ever every everyone everything fine first from going gone good great have having here
hope into just keep know last later least less like little long look looking made make many maybe more most much
must need never next nice night nothing once only other over people please pretty quick quite really right said same
says should since some someone something soon still such sure take tell than thank thanks that their them then there
these they thing things think this those though through time today together tomorrow tonight took totally under until
very want wanted week well were what when where which while will with without work would yeah year years your""".split())


def _keywords(text: str, limit: int = 6) -> tuple[list[str], list[str]]:
    """(names / codes, other content words) from the sentence being written, most recent first."""
    sentence = " ".join(re.split(r"(?<=[.!?\n])\s+", text.strip())[-2:])[-240:]
    proper, other = [], []
    prev = ""
    for tok in re.findall(r"\S+", sentence):
        start = not prev or prev[-1:] in ".!?:"
        prev = tok
        w = re.sub(r"^[^\w]+|[^\w]+$", "", tok)
        w = re.sub(r"['’]s$", "", w)
        low = w.lower()
        if re.fullmatch(r"#?\w*\d\w*", w) and len(w.strip("#")) >= 2:
            proper.append(w.strip("#"))          # numbers and codes: 1442, ENG-482, 4471
        elif len(w) < 3 or not WORD_RE.fullmatch(w) or low in E.mem.STOPWORDS or low in VAGUE:
            continue
        elif w[0].isupper() and w not in COMMON_CAPS and not (start and _is_word(low)):
            proper.append(w)
        elif len(w) >= 4 and (not w[0].isupper() or start):
            other.append(low)
    proper = list(dict.fromkeys(reversed(proper)))[:limit]
    other = list(dict.fromkeys(reversed(other)))
    return proper, other[:max(0, limit - len(proper))]


def _strip_draft(text: str, before: str) -> str:
    """Remove the user's own draft from a captured window (captures may include the text box)."""
    for line in before.splitlines()[-6:]:
        line = line.strip()
        if len(line) >= 15:
            text = text.replace(line, "")
    return text


def _relevant_tail(text: str, before: str, limit: int = WINDOW_CHARS) -> str:
    """The end of a window's text (usually the latest message / the part near the field), plus a
    few earlier lines that share words with what the user is writing."""
    lines, seen = [], set()
    for raw in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        key = line.lower()
        if len(line) < 3 or key in seen:
            continue
        seen.add(key)
        lines.append(line)
    if not lines:
        return ""
    tail, used = [], 0
    tail_budget = int(limit * 0.72)
    for line in reversed(lines):
        if used + len(line) > tail_budget and tail:
            break
        tail.insert(0, line[-tail_budget:])
        used += len(line) + 1
    start = len(lines) - len(tail)
    proper, other = _keywords(before, 8)
    terms = {t.lower() for t in proper + other}
    extra = []
    if terms and start > 0:
        scored = []
        for i, line in enumerate(lines[:start]):
            words = {w.lower() for w in WORD_RE.findall(line)}
            hit = len(words & terms)
            if hit:
                scored.append((hit, i, line[:260]))
        room = limit - used
        for hit, i, line in sorted(scored, key=lambda x: (-x[0], -x[1])):
            if room - len(line) < 0:
                continue
            extra.append((i, line))
            room -= len(line) + 1
            if len(extra) >= 3:
                break
    head = [line for _, line in sorted(extra)]
    return "\n".join(head + (["…"] if head else []) + tail)


def window_context(app: str, title: str, host: str | None, before: str, limit: int = WINDOW_CHARS) -> tuple[str, str | None]:
    """(relevant tail of the newest capture of this window, its chunk id)."""
    app_id = (app or "").strip().lower()
    clean = clean_title(title)
    now = E.now_ms()
    row = None
    with E.DB_LOCK:
        d = E.db()
        try:
            if clean:
                like = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                row = d.execute("SELECT id, text FROM memory_chunks WHERE (window_title=? OR window_title LIKE ? ESCAPE '\\') "
                                "AND source != 'title_only' AND captured_at > ? ORDER BY captured_at DESC LIMIT 1",
                                (title, like, now - 12 * 3_600_000)).fetchone()
            if not row and host and not clean:
                row = d.execute("SELECT id, text FROM memory_chunks WHERE (url_host=? OR url_host=?) AND source != 'title_only' "
                                "AND captured_at > ? ORDER BY captured_at DESC LIMIT 1",
                                (host, "www." + host.removeprefix("www."), now - 1_800_000)).fetchone()
            if not row and app_id and not host and not clean:
                row = d.execute("SELECT id, text FROM memory_chunks WHERE app_id=? AND source != 'title_only' "
                                "AND captured_at > ? ORDER BY captured_at DESC LIMIT 1",
                                (app_id, now - 900_000)).fetchone()
        finally:
            d.close()
    if not row:
        return "", None
    return _relevant_tail(_strip_draft(row[1] or "", before), before, limit), row[0]


def memory_facts(before: str, window_text: str, exclude_chunk: str | None, n: int = 2,
                 width: int = 260) -> list[str]:
    """1-2 short memory passages matching the names, codes and keywords of the sentence being
    written (and of the latest message on screen, which it often answers). Keyword (FTS) search
    only, last FACT_DAYS days: this must stay well under 50 ms. A passage needs two distinct
    matching terms, so a name that appears everywhere is not enough on its own."""
    proper, other = _keywords(before)
    wp, wo = _keywords(window_text[-300:], 9) if window_text else ([], [])
    terms = list(dict.fromkeys(proper + other + wp + wo))[:14]
    if len(terms) < 2:
        return []
    fts = " OR ".join(f'"{t.lower()}"*' for t in terms)
    since = E.now_ms() - FACT_DAYS * 86_400_000
    with E.DB_LOCK:
        d = E.db()
        try:
            rows = d.execute(
                "SELECT p.chunk_id, p.text, bm25(memory_passages_fts) FROM memory_passages_fts "
                "JOIN memory_passages p ON p.id = memory_passages_fts.rowid "
                "WHERE memory_passages_fts MATCH ? AND p.captured_at > ? "
                "ORDER BY bm25(memory_passages_fts) LIMIT 16", (fts, since)).fetchall()
        except Exception:
            rows = []
        finally:
            d.close()
    own = {t.lower() for t in proper + other}
    win = _norm(window_text)
    draft = _norm(before[-400:])
    scored = []
    for rank, (cid, text, _) in enumerate(rows):
        if cid == exclude_chunk:
            continue
        low = text.lower()
        hits = [t for t in terms if re.search(r"(?<!\w)" + re.escape(t.lower()), low)]
        kinds = [("code" if re.search(r"\d", t) else "name" if t[:1].isupper() else "word",
                  t.lower() in own) for t in hits]
        # It must match the sentence being written itself: one of its words or codes, or two of
        # its names (a single name appears everywhere). Window terms only add weight.
        if not any(o and k != "name" for k, o in kinds) and sum(o and k == "name" for k, o in kinds) < 2:
            continue
        weight = {("code", True): 3, ("word", True): 2, ("name", True): 1.5,
                  ("code", False): 1.5, ("word", False): 1, ("name", False): 0.5}
        score = sum(weight[k] for k in kinds)
        if score < 2.5:
            continue
        scored.append((-score, rank, text, hits))
    out: list[str] = []
    for _, _, text, hits in sorted(scored):
        snip = re.sub(r"\s+", " ", E.mem.snippet(text, [t.lower() for t in hits], width=width)).strip()
        ns = _norm(snip)
        if len(ns) < 20 or ns[:60] in win or ns[:60] in draft or any(ns[:60] in _norm(o) for o in out):
            continue
        out.append(snip)
        if len(out) >= n:
            break
    return out


def samples_for(app_key: str, limit: int = 5, like: str = "", borrow: bool = True) -> list[str]:
    """The user's own writing in this app, preferring typed sentences and ones that share words
    with what is being written now."""
    with E.DB_LOCK:
        d = _db()
        try:
            rows = d.execute("SELECT text, source FROM autocomplete_samples WHERE app=? ORDER BY created_at DESC LIMIT 16",
                             (app_key,)).fetchall()
            if borrow and len(rows) < limit:  # thin history for this app: borrow from anywhere
                rows += d.execute("SELECT text, source FROM autocomplete_samples WHERE app!=? ORDER BY created_at DESC LIMIT ?",
                                  (app_key, limit - len(rows))).fetchall()
        finally:
            d.close()
    if not like:
        return [r[0] for r in rows[:limit]]
    words = {w.lower() for w in WORD_RE.findall(like[-300:])}
    ranked = sorted(enumerate(rows), key=lambda ir: (-len(words & {w.lower() for w in WORD_RE.findall(ir[1][0])}),
                                                     ir[1][1] != "typed", ir[0]))
    return [r[0] for _, r in ranked[:limit]]


def gather_context(b: dict) -> dict:
    """Everything a prompt needs, within the on-device budget. Logged sizes only, never text."""
    t0 = time.time()
    app, title, host = b.get("app") or "", b.get("title") or "", b.get("url_host") or ""
    before = b.get("text_before") or ""
    ctx = {"label": context_label(app, title, host), "field": (b.get("field_hint") or "").strip()[:80],
           "window": "", "facts": [], "samples": [], "bio": "", "terms": []}
    try:
        ctx["window"], chunk = window_context(app, title, host, before)
    except Exception as e:
        E.log(f"autocomplete window context failed: {e}")
        chunk = None
    t1 = time.time()
    try:
        ctx["facts"] = memory_facts(before, ctx["window"], chunk)
    except Exception as e:
        E.log(f"autocomplete memory facts failed: {e}")
    t2 = time.time()
    try:
        ctx["samples"] = samples_for(_app_key(app, host), 2, like=before, borrow=False)
    except Exception:
        pass
    ctx["bio"] = (E.kv_get("bio", "") or "").strip()[:240]
    try:
        ctx["terms"] = [e["value"] for e in E.dictionary()[:12]]
    except Exception:
        pass
    ctx["timing"] = (int((t1 - t0) * 1000), int((t2 - t1) * 1000), int((time.time() - t2) * 1000))
    return ctx


# ---------------------------------------------------------------- prompts

INSTRUCTIONS = (
    "You are the user's inline autocomplete. Predict exactly what THEY will type next to finish "
    "their current sentence, in their voice.\n"
    "Answer format: copy the last 3 to 5 words of the user's text exactly, then continue with at most "
    "10 new words. One line. No quotes, no explanation.\n"
    "- If the text ends in the middle of a word, first finish that word.\n"
    "- Be specific: when the screen or notes contain the exact name, number, date, time, place or "
    "fact that finishes the thought, use it word for word.\n"
    "- Never invent names, numbers, dates, times, prices or places that are not in the context.\n"
    "- If the conversation asks something, the user is usually answering it.\n"
    "- Write as the user (first person), not as the other person. Match their tone and casing.")

LEGACY_INSTRUCTIONS = (
    "You are inline autocomplete. Start by repeating the last few words of the user's text exactly, "
    "then continue it naturally with at most 10 more words that finish their thought. Match their tone. "
    "Output only that, on one line: no quotes, no explanation. Never add people's names, numbers or dates "
    "that do not already appear in the user's text.")

CLOUD_SUFFIX = (
    "\nGive up to {n} different likely continuations, one per line, each in the answer format above "
    "(last words of the user's text, then the continuation). Most likely first. If you cannot predict "
    "anything useful, answer NONE.")


def unfinished_word(before: str) -> str | None:
    """The last word when it looks cut off (not in the system word list), e.g. 'Pri', 'turnar'."""
    m = re.search(r"([A-Za-z]{2,})$", before)
    if not m:
        return None
    w = m.group(1)
    return w if _words() and not _is_word(w.lower()) and len(w) < 12 else None


def complete_from_context(fragment: str, *texts: str) -> str | None:
    """The rest of a word from the context that starts with an unfinished fragment ('Pri' ->
    'ya' from 'Priya'), when exactly one such word stands out."""
    counts: dict = {}
    for text in texts:
        for w in re.findall(r"[A-Za-z][\w'’-]+", text or ""):
            if len(w) > len(fragment) and w.lower().startswith(fragment.lower()) \
                    and (not fragment[0].isupper() or w[0].isupper()):
                counts[w] = counts.get(w, 0) + 1
    if not counts:
        return None
    best = sorted(counts.items(), key=lambda kv: -kv[1])
    if len(best) > 1 and best[0][1] == best[1][1]:
        return None
    return best[0][0][len(fragment):]


def build_prompt(b: dict, ctx: dict, budget: int = CONTEXT_CHARS) -> str:
    """Context sections in order of usefulness, trimmed to the budget; the caret text is last."""
    before = b.get("text_before") or ""
    after = (b.get("text_after") or "").strip()
    head = f"Typing in: {ctx['label']}" + (f"\nText box: {ctx['field']}" if ctx.get("field") else "")
    cut = unfinished_word(before)
    caret = (f'(The last word "{cut}" is unfinished: complete it first.)\n' if cut else "") + \
        f"User's text so far:\n{_clip_tail(before, 700 if budget < CONTEXT_CHARS else 900)}"
    parts = [head]
    room = budget - len(head) - len(caret) - len(INSTRUCTIONS) - 300
    if ctx.get("window") and room > 200:
        w = _clip_tail(ctx["window"], min(WINDOW_CHARS, room - 100))
        parts.append(f"On screen in this window (latest last):\n{w}")
        room -= len(w) + 40
    if ctx.get("facts") and room > 150:
        fx = "\n".join(f"- {f}" for f in ctx["facts"])[:max(0, min(600, room))]
        parts.append(f"From the user's notes (use only if relevant):\n{fx}")
        room -= len(fx) + 40
    if ctx.get("samples") and room > 120:
        sm = "\n".join(f"- {_clip_tail(x, 160)}" for x in ctx["samples"][:2])
        parts.append(f"The user's tone in this app (examples only, never copy them):\n{sm}")
        room -= len(sm) + 30
    if ctx.get("bio") and room > 120:
        parts.append(f"About the user: {ctx['bio'][:min(240, room - 20)]}")
        room -= 260
    if ctx.get("terms") and room > 80:
        parts.append("Spellings they use: " + ", ".join(ctx["terms"])[:min(200, room)])
    if after:
        parts.append(f"Text after the cursor (do not repeat it):\n{after[:200]}")
    parts.append(caret)
    return "\n\n".join(parts)


def align_continuation(before: str, out: str) -> str:
    """The model echoes the end of the text, then continues; strip the echo so only new text remains,
    keeping the model's own spacing at the boundary. Leading ellipses ('...now') become a space."""
    cont = _align(before, out)
    if re.match(r"^\s*(\.{2,}|…)", cont):
        cont = " " + re.sub(r"^\s*(\.{2,}|…)\s*", "", cont)
    return cont if cont.strip() else ""


def _align(before: str, out: str) -> str:
    lines = [ln.strip() for ln in (out or "").strip().splitlines() if ln.strip()]
    out = " ".join(lines).strip().strip('"“”').replace("…", " ").strip()
    if not out:
        return ""
    norm_b = re.sub(r"\s+", " ", before).lower()
    low = out.lower()
    words = norm_b.split()
    for k in range(min(len(out), 160), 2, -1):  # longest echoed prefix that ends the user's text
        if norm_b.endswith(re.sub(r"\s+", " ", low[:k])):
            rest = out[k:]
            # Some answers echo twice ("see you … see you Thursday"): cut after the last echo.
            tail = " ".join(words[-3:])
            i = rest.lower().rfind(tail) if len(words) >= 3 and len(tail) >= 10 else -1
            return rest[i + len(tail):] if i >= 0 else rest
    # The echo may start part-way through the user's text, or be followed by a paraphrase: cut
    # after the last place the output contains the final words of the text.
    for take in range(min(6, len(words)), 1, -1):
        tail = " ".join(words[-take:])
        if len(tail) < 7:
            continue
        i = low.rfind(tail)
        if i >= 0:
            rest = out[i + len(tail):]
            head = out[:i].strip()
            # Answer first, echo last ("December 4 users on"): the part before the echo is new.
            if not rest.strip() and head and _norm(head) not in norm_b and len(head.split()) <= MAX_WORDS:
                return " " + head
            return rest
    # An echo of an earlier stretch of the text (not its end): drop that leading phrase.
    ow = out.split()
    recent = norm_b[-800:]
    for take in range(min(len(ow) - 1, 16), 3, -1):
        if " ".join(ow[:take]).lower() in recent:
            return " " + " ".join(ow[take:])
    # No echo found: treat it as a continuation of a finished word.
    if before and before[-1].isalnum() and out[:1].isalnum():
        return " " + out
    return out


DAYS_MONTHS = set("""monday tuesday wednesday thursday friday saturday sunday january february march april may june
july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec""".split())


def _new_sentence_midway(before: str, c: str, known: str) -> bool:
    """'…I'll review it and' + ' Nothing showing?': a capitalised ordinary word right after an
    unfinished clause starts a new sentence (usually text copied from the screen), not a
    continuation. Names, days and words capitalised mid-sentence in the context are fine."""
    if not re.search(r"[a-z,]$", before.rstrip()) or before[-1:] == "\n":
        return False
    m = re.match(r"\s*([A-Z][a-z]+)\b", c)
    if not m:
        return False
    w = m.group(1)
    if w in ("I", "Im") or w.lower() in DAYS_MONTHS or not _is_word(w.lower()):
        return False
    return not re.search(r"[a-z,]\s+" + re.escape(w) + r"\b", known)


GREETING_START = re.compile(r"^\s*(Great|Thanks|Thank you|Sure|Yes|No|Okay|Ok|Sounds good|Hi|Hello|Hey|Perfect|"
                            r"Awesome|Cool|Got it|Noted)\b")
DANGLING = set("a an the at on in to by for of from with around before after until and or but my your our".split())


def ground(sugg: list[str], before: str, ctx: dict) -> tuple[list[str], str | None]:
    """Keep suggestions honest: a number or name that appears nowhere in what the model was shown
    is cut off (with any dangling words before it); a reply that starts a new sentence in the
    middle of the user's (e.g. 'see you' + 'Great, ...') is dropped."""
    known_raw = " ".join([before, ctx.get("window") or "", " ".join(ctx.get("facts") or []),
                          " ".join(ctx.get("samples") or []), ctx.get("label") or "", ctx.get("field") or "",
                          ctx.get("bio") or "", " ".join(ctx.get("terms") or [])])
    known = known_raw.lower()
    out, reason = [], None
    for c in sugg:
        if (before[-1:].isalpha() and GREETING_START.match(c) and c[:1] == " ") or _new_sentence_midway(before, c, known_raw):
            reason = reason or "unsure"
            continue
        toks = re.findall(r"\S+\s*", c)
        lead = c[:len(c) - len(c.lstrip())]
        keep = []
        for i, tok in enumerate(toks):
            w = re.sub(r"^[^\w]+|[^\w]+$", "", tok.strip())
            w = re.sub(r"['’]s$", "", w)
            prev = (toks[i - 1].strip() if i else before.rstrip())[-1:]
            sentence_start = prev in ".!?\n" or not prev
            bad = any(n not in known for n in re.findall(r"\d+", w))
            if not bad and re.fullmatch(r"[A-Z][a-zA-Z]+", w) and w not in COMMON_CAPS and not sentence_start:
                bad = w.lower() not in known
            if bad:
                break
            keep.append(tok)
        if len(keep) < len(toks):
            while keep and re.sub(r"[^\w]", "", keep[-1]).lower() in DANGLING:
                keep.pop()
            text = lead + "".join(keep).strip()
            if len(re.findall(r"\w+", text)) < 2 or not text.strip():
                reason = reason or "ungrounded"
                continue
            c = text.rstrip(" ,;:-—")
        out.append(c)
    return out, (None if out else reason)


# ------------------------------------------------------------ model calls

def _on_device_legacy(b: dict) -> list[str]:
    """The previous on-device prompt: app, title, samples and the typed text only (eval baseline)."""
    app, title = b.get("app") or "", clean_title(b.get("title") or "")[:80]
    before = b.get("text_before") or ""
    samples = samples_for(_app_key(app, b.get("url_host")))[:2]
    prompt = (f"App: {app}" + (f" — {title}" if title else "") + "\n"
              + ("".join(f"Their style: {_clip_tail(x, 140)}\n" for x in samples))
              + f"User's text so far:\n{_clip_tail(before, 500)}")
    text = ai._AppleServer.request({"instructions": LEGACY_INSTRUCTIONS, "prompt": prompt,
                                    "max_tokens": 28, "temperature": 0.2}, timeout=ON_DEVICE_TIMEOUT_S)
    cont = align_continuation(before, text)
    return [cont] if cont.strip() else []


def finish_word(before: str, ctx: dict, conts: list[str]) -> list[str]:
    """When the text ends in a cut-off word the model did not complete, but the screen or notes
    contain exactly one word it could be, offer that word's ending instead."""
    frag = unfinished_word(before)
    if not frag:
        return conts
    if conts and conts[0][:1].isalpha():
        return conts
    rest = complete_from_context(frag, ctx.get("window", ""), " ".join(ctx.get("facts") or []),
                                 " ".join(ctx.get("samples") or []))
    return [rest] + [c for c in conts if c[:1].isalpha()] if rest else conts


def _on_device(b: dict, ctx: dict) -> list[str]:
    global _model_used
    _model_used = time.time()
    before = b.get("text_before") or ""
    # The on-device model's time grows with the prompt: keep it to what changes the answer most.
    prompt = build_prompt(b, {**ctx, "bio": "", "terms": []}, budget=4200)
    ctx["prompt_chars"] = len(INSTRUCTIONS) + len(prompt)
    text = ai._AppleServer.request({"instructions": INSTRUCTIONS, "prompt": prompt,
                                    "max_tokens": 32, "temperature": 0.0}, timeout=ON_DEVICE_TIMEOUT_S)
    cont = align_continuation(before, text)
    return finish_word(before, ctx, [cont] if cont.strip() else [])


def _cloud(provider, b: dict, ctx: dict, n: int) -> list[str]:
    before = b.get("text_before") or ""
    text = ai.fast_text(provider, INSTRUCTIONS + CLOUD_SUFFIX.format(n=n), build_prompt(b, ctx, budget=9000),
                        max_tokens=40 * n, timeout=CLOUD_TIMEOUT_S + 1.0)
    out = []
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(\d+[.)]|[-*•])\s+", "", line).strip()
        if not line or line.upper() == "NONE":
            continue
        cont = align_continuation(before, line)
        if cont.strip():
            out.append(cont)
    return finish_word(before, ctx, out)


def _cloud_provider(s: dict):
    """The user's cloud provider with a fast model, or None when no key is set. Cached for a
    minute because finding the keys reads the Keychain."""
    model = (s.get("autocomplete_model") or "").strip()
    key = (s["provider"], model, s["openai_model"])
    pc = _cloud_cache
    if pc["key"] == key and time.time() - pc["at"] < 60:
        return pc["value"]
    p = None
    try:
        keys = E.api_keys()
        order = ["claude", "openai"]
        if s["provider"] == "openai":
            order.reverse()
        name = next((k for k in order if keys.get(k)), None)
        if name == "claude":
            p = ai.ClaudeProvider(keys["claude"], model or ai.FAST_CLAUDE_MODEL)
        elif name == "openai":
            p = ai.OpenAIProvider(keys["openai"], model or s["openai_model"])
    except Exception as e:
        E.log(f"autocomplete cloud provider unavailable: {e}")
        p = None
    pc.update(key=key, at=time.time(), value=p)
    return p


def route(s: dict) -> tuple[object | None, bool]:
    """(cloud provider or None, on-device usable) for the current settings."""
    mode = s.get("autocomplete_model_mode") or "auto"
    if mode not in MODEL_MODES:
        mode = "auto"
    apple = ai.apple_available()
    if mode == "on_device":
        return None, apple
    cloud = _cloud_provider(s)
    if mode == "cloud":
        return cloud, apple and cloud is None   # on-device only when no key is set
    return cloud, apple


def _generate(b: dict, n: int, cloud, apple: bool, ctx: dict) -> tuple[list[str], str]:
    """Run the routed model(s). With both available, the cloud answer wins if it arrives within
    CLOUD_TIMEOUT_S; the on-device call starts once the cloud is CLOUD_HEDGE_S late."""
    if LEGACY:
        return _on_device_legacy(b), "on_device_legacy"
    if cloud is None:
        return _on_device(b, ctx), "on_device"
    if not apple:
        return _cloud(cloud, b, ctx, n), "cloud"
    t0 = time.time()
    fc = _pool.submit(_cloud, cloud, b, ctx, n)

    def good(f) -> bool:
        if not f.done():
            return False
        if f.exception():
            if f is fc:
                E.log(f"autocomplete cloud call failed, using on-device: {f.exception()}")
            return False
        return bool(f.result())

    wait([fc], timeout=CLOUD_HEDGE_S)
    if good(fc):
        return fc.result(), "cloud"
    fd = _pool.submit(_on_device, b, ctx)
    pending = {fc, fd}
    while pending:
        left = max(0.05, ON_DEVICE_TIMEOUT_S - (time.time() - t0))
        done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
        if not done:
            break
        if fc in done and good(fc):
            return fc.result(), "cloud" if time.time() - t0 <= CLOUD_TIMEOUT_S + 0.1 else "cloud_late"
        if fd in done and good(fd):
            # Prefer a cloud answer that is about to land inside its window.
            if fc in pending and time.time() - t0 < CLOUD_TIMEOUT_S:
                wait([fc], timeout=CLOUD_TIMEOUT_S - (time.time() - t0))
                if good(fc):
                    return fc.result(), "cloud"
            return fd.result(), "on_device"
    if fd.done() and fd.exception():
        raise fd.exception()
    return [], "none"


# --------------------------------------------------------------- suggest

def _cache_key(b: dict) -> tuple:
    return (_app_key(b.get("app", ""), b.get("url_host")), (b.get("text_before") or "")[-400:],
            (b.get("text_after") or "")[:80])


def _cache_get(key: tuple):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            _cache.move_to_end(key)
            return hit[1]
        # Typed-along reuse: an earlier answer whose suggestion the user has been typing out.
        app_k, before, after_k = key
        for (a, prev, aft), (ts, sugg) in reversed(_cache.items()):
            if a != app_k or aft != after_k or not sugg or time.time() - ts > CACHE_TTL_S:
                continue
            anchor = prev[-60:]
            idx = before.rfind(anchor) if len(anchor) >= 12 else -1
            if idx < 0:
                continue
            typed = before[idx + len(anchor):]
            if not typed or len(typed) > 80:
                continue
            rest = [x[len(typed):] for x in sugg if x.startswith(typed) and len(x.strip()) > len(typed.strip()) + 1]
            if rest:
                return rest
        return None


def _cache_put(key: tuple, sugg: list[str]) -> None:
    with _lock:
        _cache[key] = (time.time(), sugg)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


def _admit() -> str | None:
    """Rate limit: returns a quiet reason when a model call must not start now."""
    global _inflight, _last_call
    now = time.time()
    with _lock:
        while _recent_calls and now - _recent_calls[0] > 60:
            _recent_calls.popleft()
        if _inflight >= MAX_INFLIGHT:
            return "busy"
        if len(_recent_calls) >= MAX_PER_MINUTE:
            return "rate_limited"
        # Space calls out by holding this one back briefly (stays well inside the time budget).
        start = max(now, _last_call + MIN_GAP_S)
        _inflight += 1
        _last_call = start
        _recent_calls.append(start)
    if start > now:
        time.sleep(start - now)
    return None


def _release() -> None:
    global _inflight
    with _lock:
        _inflight = max(0, _inflight - 1)


def _call(b: dict, n: int, key: tuple, cloud, apple: bool) -> tuple[list[str], str | None, dict]:
    try:
        ctx = {} if LEGACY else gather_context(b)
        t_model = time.time()
        raw, used = _generate(b, n, cloud, apple, ctx)
        model_ms = int((time.time() - t_model) * 1000)
        sugg, reason = clean_suggestions(raw, b.get("text_before") or "", b.get("text_after") or "", n)
        if not LEGACY and sugg:
            sugg, why = ground(sugg, b.get("text_before") or "", ctx)
            reason = reason or why
        _cache_put(key, sugg)
        info = {"model": used, "model_ms": model_ms, "prompt_chars": ctx.get("prompt_chars"), "ctx_ms": list(ctx.get("timing", ())), "window": len(ctx.get("window") or ""),
                "facts": len(ctx.get("facts") or []), "samples": len(ctx.get("samples") or [])}
        return sugg, reason, info
    finally:
        _release()


def _warm_up() -> None:
    """Load the on-device model at launch so the first real suggestion isn't a cold start."""
    _migrate_defaults()
    try:
        s = _settings()
        if s.get("autocomplete_enabled") and s.get("autocomplete_model_mode") != "cloud" and ai.apple_available():
            t0 = time.time()
            _on_device({"app": "Notes", "title": "", "text_before": "Thanks for the update, I will"},
                       {"label": "Notes"})
            E.log(f"autocomplete warm-up {int((time.time() - t0) * 1000)} ms")
    except Exception as e:
        E.log(f"autocomplete warm-up skipped: {e}")


def prewarm(b: dict | None = None) -> dict:
    """POST /api/autocomplete/prewarm, sent when the user starts typing: the on-device model takes
    about twice as long after a few idle seconds, so a one-token call now makes the real request,
    a moment later, run hot."""
    global _model_used
    if _warming.is_set() or time.time() - _model_used < 4:
        return {"ok": True, "warming": False}
    s = _settings()
    if not s.get("autocomplete_enabled") or s.get("autocomplete_model_mode") == "cloud" or not ai.apple_available():
        return {"ok": True, "warming": False}
    _warming.set()
    _model_used = time.time()

    def run():
        try:
            ai._AppleServer.request({"instructions": "Reply with one word.", "prompt": "ok", "max_tokens": 1,
                                     "temperature": 0.0}, timeout=5)
        except Exception:
            pass
        finally:
            _warming.clear()
    _pool.submit(run)
    return {"ok": True, "warming": True}


def _migrate_defaults() -> None:
    """Once: move settings still at the old defaults (chip under the line, 450 ms pause) to the
    new ones (inline ghost text, 350 ms)."""
    try:
        if E.kv_get("autocomplete:defaults_v2"):
            return
        s = _settings()
        patch = {}
        if s.get("autocomplete_style") == "under":
            patch["autocomplete_style"] = "ghost"
        if s.get("autocomplete_pause_ms") == 450:
            patch["autocomplete_pause_ms"] = 350
        if patch:
            E.save_settings(patch)
        E.kv_set("autocomplete:defaults_v2", True)
    except Exception as e:
        E.log(f"autocomplete settings migration skipped: {e}")


def suggest(b: dict) -> dict:
    t0 = time.time()
    r = _suggest(b)
    info = r.pop("_info", {})
    E.log(f"autocomplete app={str(b.get('app'))[:30]!r} ms={int((time.time() - t0) * 1000)} "
          f"n={len(r.get('suggestions') or [])} quiet={r.get('quiet_reason')} src={b.get('source', 'ax')}"
          + (f" model={info.get('model')} model_ms={info.get('model_ms')} route_ms={info.get('route_ms')} "
             f"ctx_ms={info.get('ctx_ms')} prompt={info.get('prompt_chars')} window={info.get('window')} "
             f"facts={info.get('facts')} samples={info.get('samples')}" if info else ""))
    return r


def _suggest(b: dict) -> dict:
    t0 = time.time()
    s = _settings()
    before = str(b.get("text_before") or "")[-6000:]
    after = str(b.get("text_after") or "")[:2000]
    b = {**b, "text_before": before, "text_after": after}
    n = max(1, min(3, int(b.get("n") or 3)))

    def quiet(reason: str, **extra) -> dict:
        return {"suggestions": [], "quiet_reason": reason, "ms": int((time.time() - t0) * 1000), **extra}

    if not s.get("autocomplete_enabled"):
        return quiet("disabled")
    if _disabled(b.get("app", ""), b.get("bundle_id", ""), s):
        return quiet("app_disabled")
    if b.get("secure"):
        return quiet("secure_field")
    reason = quiet_reason_for_input(before, after, {**s, "autocomplete_min_chars":
                                                    1 if b.get("summoned") else s.get("autocomplete_min_chars")})
    if reason:
        return quiet(reason)

    key = _cache_key(b)
    hit = _cache_get(key)
    if hit is not None:
        return {"suggestions": hit[:n], "cached": True, "ms": int((time.time() - t0) * 1000),
                **({} if hit else {"quiet_reason": "unsure"})}

    t_route = time.time()
    cloud, apple = route(s)
    route_ms = int((time.time() - t_route) * 1000)
    if cloud is None and not apple:
        return quiet("no_provider", error="No model for autocomplete: turn on Apple Intelligence or add a key.")
    blocked = _admit()
    if blocked:
        return quiet(blocked)
    fut = _pool.submit(_call, b, n, key, cloud, apple)
    budget = max(0.2, TIME_BUDGET_S + (0.5 if cloud is None else 0.0) - (time.time() - t0))
    try:
        sugg, reason, info = fut.result(timeout=budget)
        info["route_ms"] = route_ms
    except FutureTimeout:
        return quiet("slow", retry_ms=1200)  # the call keeps running; its answer lands in the cache
    except ai.ProviderError as e:
        return quiet("provider_error", error=str(e))
    except Exception as e:
        E.log(f"autocomplete failed: {type(e).__name__}: {e}")
        return quiet("error")
    if not sugg:
        return {**quiet(reason or "unsure"), "_info": info}
    return {"suggestions": sugg[:n], "ms": int((time.time() - t0) * 1000), "model": info.get("model"), "_info": info}


# ------------------------------------------------------- outcomes, learning

def _stats() -> dict:
    st = E.kv_get("autocomplete:stats") or {}
    base = {"shown": 0, "accepted": 0, "accepted_chars": 0, "dismissed": 0, "ignored": 0,
            "days": [], "since": E.now_ms(), "apps": {}}
    return {**base, **st}


def add_sample(app_key: str, text: str, source: str = "accepted", min_chars: int = 20) -> bool:
    """Keep a bounded set of the user's own writing per app. A draft that grows replaces its
    earlier, shorter copy instead of piling up near-duplicates."""
    text = re.sub(r"[ \t]+", " ", (text or "")).strip()
    # Keep the current paragraph: earlier paragraphs may be quoted text the user did not write.
    text = text.rsplit("\n\n", 1)[-1].strip()
    text = _clip_tail(text, SAMPLE_MAX_CHARS).lstrip("…").strip()
    if len(text) < min_chars or len(text.split()) < 3:
        return False
    with E.DB_LOCK:
        d = _db()
        try:
            recent = d.execute("SELECT id, text FROM autocomplete_samples WHERE app=? ORDER BY created_at DESC LIMIT 8",
                               (app_key,)).fetchall()
            head = _norm(text)[:40]
            for rid, old in recent:
                o = _norm(old)
                if o == _norm(text) or (head and o[:40] == head) or o in _norm(text):
                    d.execute("DELETE FROM autocomplete_samples WHERE id=?", (rid,))
                elif _norm(text) in o:
                    return False  # we already hold a longer version
            d.execute("INSERT INTO autocomplete_samples (app, text, created_at, source) VALUES (?,?,?,?)",
                      (app_key, text, E.now_ms(), source))
            d.execute("DELETE FROM autocomplete_samples WHERE app=? AND id NOT IN (SELECT id FROM autocomplete_samples "
                      "WHERE app=? ORDER BY created_at DESC LIMIT ?)", (app_key, app_key, SAMPLES_PER_APP))
            apps = d.execute("SELECT app FROM autocomplete_samples GROUP BY app ORDER BY max(created_at) DESC").fetchall()
            for (old_app,) in apps[SAMPLE_APPS_MAX:]:
                d.execute("DELETE FROM autocomplete_samples WHERE app=?", (old_app,))
            d.commit()
        finally:
            d.close()
    return True


EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
SENSITIVE_APPS = ("1password", "bitwarden", "lastpass", "dashlane", "keepass", "keychain", "passwords",
                  "terminal", "iterm", "warp", "ghostty", "kitty", "alacritty", "wezterm", "xcode")


def learnable_sentences(text: str) -> list[str]:
    """Sentences worth keeping as voice samples: real prose, not URLs, code, addresses or numbers."""
    out = []
    for line in re.split(r"\n+", text or ""):
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 15 or len(line.split()) < 3:
            continue
        if URL_RE.search(line) or re.search(r"https?://|www\.", line) or EMAIL_RE.search(line):
            continue
        if CODE_RE.search(line) or re.search(r"[{}<>\[\]|\\]|\w+\(\)|==|->", line):
            continue
        visible = re.sub(r"\s", "", line)
        letters = sum(ch.isalpha() for ch in visible)
        digits = sum(ch.isdigit() for ch in visible)
        if letters < 0.7 * len(visible) or digits > 0.15 * len(visible):
            continue
        if max((len(w) for w in line.split()), default=0) > 30:
            continue  # tokens, hashes, run-together strings
        out.append(line)
    return out


def learn(b: dict) -> dict:
    """POST /api/autocomplete/learn: a sentence the user finished typing (sent, Return, long pause)."""
    s = _settings()
    app, bid = str(b.get("app") or ""), str(b.get("bundle_id") or "")
    if not s.get("autocomplete_learn_typing", True) or not s.get("autocomplete_enabled"):
        return {"ok": True, "learned": 0, "skipped": "off"}
    low = f"{app} {bid}".lower()
    if b.get("secure") or _disabled(app, bid, s) or any(x in low for x in SENSITIVE_APPS) \
            or app.strip().lower() in E.SELF_APPS:
        return {"ok": True, "learned": 0, "skipped": "app"}
    lines = learnable_sentences(str(b.get("text") or "")[-2000:])
    app_k = _app_key(app, b.get("url_host"))
    learned = sum(add_sample(app_k, line, source="typed", min_chars=15) for line in lines[-3:])
    return {"ok": True, "learned": learned}


def outcome(b: dict) -> dict:
    kind = (b.get("outcome") or "").strip()
    if kind not in ("accepted", "dismissed", "ignored", "shown"):
        raise ValueError("outcome must be accepted, dismissed, ignored or shown")
    chars = max(0, int(b.get("chars") or 0))
    app_k = _app_key(b.get("app", ""), b.get("url_host"))
    with _stats_lock:
        st = _stats()
        st[kind] = int(st.get(kind, 0)) + 1
        if kind == "accepted":
            st["accepted_chars"] = int(st.get("accepted_chars", 0)) + chars
            per = st.setdefault("apps", {})
            per[app_k] = int(per.get(app_k, 0)) + 1
            if len(per) > 50:  # keep the busiest apps only
                st["apps"] = dict(sorted(per.items(), key=lambda kv: -kv[1])[:50])
        today = date.today().isoformat()
        if kind in ("accepted", "shown") and today not in st["days"]:
            st["days"] = (st["days"] + [today])[-400:]
        E.kv_set("autocomplete:stats", st)
    learned = False
    s = _settings()
    learn_kinds = ("accepted", "ignored") if s.get("autocomplete_learn_typing", True) else ("accepted",)
    if b.get("text") and kind in learn_kinds and not _disabled(b.get("app", ""), b.get("bundle_id", ""), s):
        learned = add_sample(app_k, str(b["text"])[-2000:])
    return {"ok": True, "learned": learned}


def stats(q: dict | None = None) -> dict:
    st = _stats()
    with E.DB_LOCK:
        d = _db()
        try:
            per_app = d.execute("SELECT app, count(*) FROM autocomplete_samples GROUP BY app ORDER BY 2 DESC").fetchall()
            typed = d.execute("SELECT count(*) FROM autocomplete_samples WHERE source='typed'").fetchone()[0]
        finally:
            d.close()
    shown = max(int(st.get("shown", 0)), 1)
    top = sorted(st.get("apps", {}).items(), key=lambda kv: -kv[1])[:6]
    return {"shown": st.get("shown", 0), "accepted": st.get("accepted", 0),
            "accepted_chars": st.get("accepted_chars", 0), "dismissed": st.get("dismissed", 0),
            "ignored": st.get("ignored", 0), "accept_rate": round(min(1.0, st.get("accepted", 0) / shown), 3),
            "days_active": len(st.get("days", [])), "since": st.get("since"),
            "top_apps": [{"app": a, "accepted": n} for a, n in top],
            "samples": sum(n for _, n in per_app), "typed_samples": typed,
            "sample_apps": [{"app": a, "count": n} for a, n in per_app]}


def clear(b: dict) -> dict:
    removed = 0
    if b.get("samples", True):
        app = (b.get("app") or "").strip().lower()
        with E.DB_LOCK:
            d = _db()
            try:
                cur = d.execute("DELETE FROM autocomplete_samples" + (" WHERE app=?" if app else ""),
                                (app,) if app else ())
                removed = cur.rowcount
                d.commit()
            finally:
                d.close()
    if b.get("stats"):
        E.kv_set("autocomplete:stats", {})
    with _lock:
        _cache.clear()
    return {"removed_samples": removed, **stats()}


def register():
    E.POST_ROUTES["/api/autocomplete/suggest"] = suggest
    E.STARTUP_HOOKS.append(_warm_up)
    E.POST_ROUTES["/api/autocomplete/outcome"] = outcome
    E.POST_ROUTES["/api/autocomplete/learn"] = learn
    E.POST_ROUTES["/api/autocomplete/prewarm"] = prewarm
    E.POST_ROUTES["/api/autocomplete/clear"] = clear
    E.GET_ROUTES["/api/autocomplete/stats"] = stats
