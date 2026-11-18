"""Autocomplete everywhere: short AI continuations for whatever the user is typing.

The native shell (mac/Autocomplete.swift) watches the focused text field, and after a typing
pause asks POST /api/autocomplete/suggest for continuations of the text before the caret. This
module builds a compact prompt (recent screen context, the user's own writing in that app, bio),
calls the user's provider with low effort and a short answer, filters the result through quiet
rules, and answers within a time budget so the chip can appear while it is still useful.

Outcomes (accepted / dismissed / ignored) feed local stats and a bounded per-app store of the
user's own writing, which later prompts use to match the user's voice. Nothing leaves the Mac
except the prompt sent to the user's own AI provider.
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import date

import ai
import engine as E

E.DEFAULT_SETTINGS.update({
    "autocomplete_enabled": True,
    "autocomplete_style": "under",          # under | normal | ghost
    "autocomplete_mode": "automatic",       # automatic | on_demand (Option+Tab summons)
    "autocomplete_accept_key": "tab",       # tab | backtick | both
    "autocomplete_disabled_apps": [],
    "autocomplete_min_chars": 12,
    "autocomplete_pause_ms": 450,
    "autocomplete_model": "",               # blank: the model chosen for Ask
})

STYLES = {"under", "normal", "ghost"}
TIME_BUDGET_S = 2.5          # answer "too slow" rather than show a stale chip
PROVIDER_TIMEOUT_S = 8.0     # hard stop for calls that outlive the budget (their result is cached)
MIN_GAP_S = 0.25             # minimum spacing between provider calls
MAX_PER_MINUTE = 40
MAX_INFLIGHT = 2
CACHE_SIZE = 300
CACHE_TTL_S = 600
SAMPLES_PER_APP = 40
SAMPLE_APPS_MAX = 80
SAMPLE_MAX_CHARS = 400
MAX_SUGGESTION_CHARS = 140

SCHEMA = """
CREATE TABLE IF NOT EXISTS autocomplete_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, app TEXT NOT NULL, text TEXT NOT NULL,
    created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_ac_samples_app ON autocomplete_samples(app, created_at DESC);
"""

SYSTEM = """You write inline autocomplete for a person typing on their Mac. You see the text \
before their cursor and must predict what THEY will type next, in their own words.

Rules:
- Continue exactly at the cursor. Output only the new characters, never the text already typed.
- If the cursor is mid-word, start by finishing that word (no leading space). Otherwise begin \
with a space when one is needed.
- Keep it short: finish the current sentence or clause, at most about 12 words. Never a newline.
- Match their language, tone, casing and punctuation habits (see their writing samples).
- Use the screen context only to get names, facts and topics right. Never invent specific \
facts (names, numbers, dates, links) that are not supported by the context.
- If you cannot predict confidently, or the field holds a password, URL, code or a search \
query, return an empty list.

Return "suggestions": up to {n} alternative continuations (the exact characters to insert, \
including any leading space), most likely first."""

OUTPUT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["suggestions"],
                 "properties": {"suggestions": {"type": "array", "items": {"type": "string"}}}}

_lock = threading.Lock()
_cache: OrderedDict = OrderedDict()          # key -> (ts, suggestions)
_recent_calls: deque = deque()               # provider call timestamps (rate limit)
_inflight = 0
_last_call = 0.0
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="autocomplete")
_provider_cache: dict = {"key": None, "at": 0.0, "value": None, "error": None}
_stats_lock = threading.Lock()


# ----------------------------------------------------------------- helpers

def _settings() -> dict:
    return E.load_settings()


def _app_key(app: str, url_host: str | None) -> str:
    """Writing samples are kept per site for browsers (mail vs chat differ), else per app."""
    host = (url_host or "").lower().removeprefix("www.")
    return host or (app or "unknown").strip().lower()


def _disabled(app: str, bundle_id: str, s: dict) -> bool:
    names = {(a or "").strip().lower() for a in s.get("autocomplete_disabled_apps") or []}
    return bool(names & {(app or "").lower(), (bundle_id or "").lower()} - {""})


def _provider(s: dict):
    """The user's provider (optionally with an autocomplete-specific model), cached for a minute
    because building one reads the Keychain."""
    if s.get("fast_on_device", True) and not (s.get("autocomplete_model") or "").strip() and ai.apple_available():
        return ai.AppleProvider()  # on-device: fast, free, private; no keychain read needed
    model = (s.get("autocomplete_model") or "").strip()
    key = (s["provider"], model, s["claude_model"], s["openai_model"])
    pc = _provider_cache
    if pc["key"] == key and time.time() - pc["at"] < 60:
        if pc["error"]:
            raise pc["error"]
        return pc["value"]
    models = {"claude": s["claude_model"], "openai": s["openai_model"]}
    if model:
        models[s["provider"]] = model
    try:
        keys = E.api_keys()
        name = "auto" if s["provider"] in ("claude", "openai") and not keys.get(s["provider"]) else s["provider"]
        p = ai.make_provider(name, keys, models)
        # Short, single-shot calls: no retries, and give up long before the SDK default.
        if hasattr(p, "client") and hasattr(p.client, "with_options"):
            p.client = p.client.with_options(timeout=PROVIDER_TIMEOUT_S, max_retries=0)
        pc.update(key=key, at=time.time(), value=p, error=None)
        return p
    except ai.ProviderError as e:
        pc.update(key=key, at=time.time(), value=None, error=e)
        raise


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


def clean_suggestions(cands: list, before: str, after: str, n: int) -> tuple[list[str], str | None]:
    """Normalise model continuations and drop ones that add nothing."""
    cands = [c for c in (cands or []) if isinstance(c, str) and c.strip() and c.strip().upper() != "NONE"]
    if not cands:
        return [], "unsure"
    out: list[str] = []
    tail = before[-200:]
    reason = None
    for c in cands:
        c = c.replace("\n", " ")
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
        if all(_norm(o) != core for o in out):
            out.append(c)
        if len(out) >= n:
            break
    return out, (None if out else reason or "unsure")


# --------------------------------------------------------------- context

def _screen_context(app: str, title: str, url_host: str | None, limit_chars: int = 1800) -> str:
    """Most recent captured text of the current window (falls back to the app), then one other
    recent window, so suggestions can use names and facts the user is looking at."""
    with E.DB_LOCK:
        d = E.db()
        try:
            rows = []
            if title:
                rows = d.execute("SELECT app_id, window_title, text FROM memory_chunks WHERE window_title=? "
                                 "AND source != 'title_only' AND captured_at > ? ORDER BY captured_at DESC LIMIT 1",
                                 (title, E.now_ms() - 6 * 3_600_000)).fetchall()
            if not rows and (app or url_host):
                rows = d.execute("SELECT app_id, window_title, text FROM memory_chunks WHERE (app_id=? OR url_host=?) "
                                 "AND source != 'title_only' AND captured_at > ? ORDER BY captured_at DESC LIMIT 1",
                                 (app or "", url_host or "", E.now_ms() - 3_600_000)).fetchall()
            other = d.execute("SELECT app_id, window_title, text FROM memory_chunks WHERE source != 'title_only' "
                              "AND window_title != ? AND captured_at > ? ORDER BY captured_at DESC LIMIT 1",
                              (title or "", E.now_ms() - 1_800_000)).fetchall()
        finally:
            d.close()
    parts = []
    for i, (a, t, txt) in enumerate(rows + other):
        budget = int(limit_chars * (0.7 if i == 0 else 0.3))
        body = re.sub(r"[ \t]+", " ", txt or "").strip()
        # The end of a window's text is usually the latest message / the part near the field.
        parts.append(f"({a} — {t})\n{_clip_tail(body, budget)}")
    return "\n\n".join(parts)


def samples_for(app_key: str, limit: int = 5) -> list[str]:
    with E.DB_LOCK:
        d = E.db()
        try:
            d.executescript(SCHEMA)
            rows = d.execute("SELECT text FROM autocomplete_samples WHERE app=? ORDER BY created_at DESC LIMIT ?",
                             (app_key, limit)).fetchall()
            if len(rows) < 2:  # thin history for this app: borrow from anywhere
                rows += d.execute("SELECT text FROM autocomplete_samples WHERE app!=? ORDER BY created_at DESC LIMIT ?",
                                  (app_key, limit - len(rows))).fetchall()
        finally:
            d.close()
    return [r[0] for r in rows]


def build_prompt(b: dict, n: int) -> str:
    app, title, host = b.get("app") or "", clean_title(b.get("title") or ""), b.get("url_host") or ""
    before = b.get("text_before") or ""
    after = b.get("text_after") or ""
    parts = [f"App: {app}" + (f" · {host}" if host else "") + (f"\nWindow: {title[:160]}" if title else "")]
    bio = (E.kv_get("bio", "") or "").strip()
    if bio:
        parts.append(f"About the user:\n{bio[:500]}")
    samples = samples_for(_app_key(app, host))
    if samples:
        parts.append("The user's own writing (for voice):\n" + "\n".join(f"- {_clip_tail(x, 220)}" for x in samples))
    try:
        terms = [e["value"] for e in E.dictionary()[:25]]
        if terms:
            parts.append("Spellings the user uses: " + ", ".join(terms))
    except Exception:
        pass
    screen = _screen_context(app, title, host)
    if screen:
        parts.append(f"On screen recently:\n{screen}")
    if after.strip():
        parts.append(f"Text after the cursor (do not repeat it):\n{after[:300]}")
    parts.append(f"Text before the cursor (continue from the very end, after the last character):\n"
                 f"<<<{_clip_tail(before, 1500)}>>>\n\nGive up to {n} continuation(s), or NONE.")
    return "\n\n".join(parts)


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
    """Rate limit: returns a quiet reason when a provider call must not start now."""
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


ON_DEVICE_INSTRUCTIONS = (
    "You are inline autocomplete. Start by repeating the last few words of the user's text exactly, "
    "then continue it naturally with at most 10 more words that finish their thought. Match their tone. "
    "Output only that, on one line: no quotes, no explanation. Never add people's names, numbers or dates "
    "that do not already appear in the user's text.")

BROWSER_SUFFIX = re.compile(r"\s+[-–—]\s+(Google Chrome|Chromium|Brave|Microsoft Edge|Arc|Safari|Firefox)"
                            r"(\s+[-–—]\s+.*)?$")


def clean_title(title: str) -> str:
    """Drop the browser name and profile label (e.g. ' - Google Chrome - Work') from window titles."""
    return BROWSER_SUFFIX.sub("", title or "").strip()


def align_continuation(before: str, out: str) -> str:
    """The model echoes the end of the text, then continues; strip the echo so only new text remains,
    keeping the model's own spacing at the boundary."""
    out = out.strip().strip('"').splitlines()[0] if out.strip() else ""
    norm_b = re.sub(r"\s+", " ", before).lower()
    low = out.lower()
    for k in range(min(len(out), 80), 2, -1):  # longest echoed prefix that ends the user's text
        if norm_b.endswith(re.sub(r"\s+", " ", low[:k])):
            return out[k:]
    # No echo found: treat it as a continuation of a finished word.
    if before and before[-1].isalnum() and out[:1].isalnum():
        return " " + out
    return out


def _call_on_device(b: dict) -> list[str]:
    app, title = b.get("app") or "", clean_title(b.get("title") or "")[:80]
    before = b.get("text_before") or ""
    samples = samples_for(_app_key(app, b.get("url_host")))[:2]
    prompt = (f"App: {app}" + (f" — {title}" if title else "") + "\n"
              + ("".join(f"Their style: {_clip_tail(x, 140)}\n" for x in samples))
              + f"User's text so far:\n{_clip_tail(before, 500)}")
    text = ai._AppleServer.request({"instructions": ON_DEVICE_INSTRUCTIONS, "prompt": prompt,
                                    "max_tokens": 28, "temperature": 0.2}, timeout=PROVIDER_TIMEOUT_S)
    cont = align_continuation(before, text)
    return [cont] if cont.strip() else []


def _call(provider, prompt: str, n: int, b: dict, key: tuple) -> tuple[list[str], str | None]:
    try:
        if isinstance(provider, ai.AppleProvider):  # lean prompt + plain text: well under a second
            sugg, reason = clean_suggestions(_call_on_device(b), b.get("text_before") or "",
                                             b.get("text_after") or "", 1)
            _cache_put(key, sugg)
            return sugg, reason
        out = provider.complete_json(SYSTEM.format(n=n), prompt, OUTPUT_SCHEMA)
        sugg, reason = clean_suggestions((out or {}).get("suggestions"), b.get("text_before") or "",
                                         b.get("text_after") or "", n)
        _cache_put(key, sugg)
        return sugg, reason
    finally:
        _release()


def _warm_up() -> None:
    """Load the on-device model at launch so the first real suggestion isn't a cold start."""
    try:
        s = _settings()
        if s.get("autocomplete_enabled") and s.get("fast_on_device", True) and ai.apple_available():
            t0 = time.time()
            _call_on_device({"app": "Notes", "title": "", "text_before": "Thanks for the update, I will"})
            E.log(f"autocomplete warm-up {int((time.time() - t0) * 1000)} ms")
    except Exception as e:
        E.log(f"autocomplete warm-up skipped: {e}")


def suggest(b: dict) -> dict:
    t0 = time.time()
    r = _suggest(b)
    E.log(f"autocomplete app={str(b.get('app'))[:30]!r} ms={int((time.time() - t0) * 1000)} "
          f"n={len(r.get('suggestions') or [])} quiet={r.get('quiet_reason')}")
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

    try:
        provider = _provider(s)
    except ai.ProviderError as e:
        return quiet("no_provider", error=str(e))
    blocked = _admit()
    if blocked:
        return quiet(blocked)
    try:
        prompt = build_prompt(b, n)
    except Exception as e:
        _release()
        E.log(f"autocomplete prompt failed: {e}")
        return quiet("error")
    fut = _pool.submit(_call, provider, prompt, n, b, key)
    limit = TIME_BUDGET_S + (1.0 if isinstance(provider, ai.AppleProvider) else 0.0)
    budget = max(0.2, limit - (time.time() - t0))
    try:
        sugg, reason = fut.result(timeout=budget)
    except FutureTimeout:
        return quiet("slow", retry_ms=1500)  # the call keeps running; its answer lands in the cache
    except ai.ProviderError as e:
        return quiet("provider_error", error=str(e))
    except Exception as e:
        E.log(f"autocomplete failed: {e}")
        return quiet("error")
    if not sugg:
        return quiet(reason or "unsure")
    return {"suggestions": sugg[:n], "ms": int((time.time() - t0) * 1000)}


# ------------------------------------------------------- outcomes, stats

def _stats() -> dict:
    st = E.kv_get("autocomplete:stats") or {}
    base = {"shown": 0, "accepted": 0, "accepted_chars": 0, "dismissed": 0, "ignored": 0,
            "days": [], "since": E.now_ms(), "apps": {}}
    return {**base, **st}


def add_sample(app_key: str, text: str) -> bool:
    """Keep a bounded set of the user's own writing per app. A draft that grows replaces its
    earlier, shorter copy instead of piling up near-duplicates."""
    text = re.sub(r"[ \t]+", " ", (text or "")).strip()
    # Keep the current paragraph: earlier paragraphs may be quoted text the user did not write.
    text = text.rsplit("\n\n", 1)[-1].strip()
    text = _clip_tail(text, SAMPLE_MAX_CHARS).lstrip("…").strip()
    if len(text) < 20 or len(text.split()) < 4:
        return False
    with E.DB_LOCK:
        d = E.db()
        try:
            d.executescript(SCHEMA)
            recent = d.execute("SELECT id, text FROM autocomplete_samples WHERE app=? ORDER BY created_at DESC LIMIT 6",
                               (app_key,)).fetchall()
            head = _norm(text)[:40]
            for rid, old in recent:
                o = _norm(old)
                if o == _norm(text) or (head and o[:40] == head) or o in _norm(text):
                    d.execute("DELETE FROM autocomplete_samples WHERE id=?", (rid,))
                elif _norm(text) in o:
                    d.close()
                    return False  # we already hold a longer version
            d.execute("INSERT INTO autocomplete_samples (app, text, created_at) VALUES (?,?,?)",
                      (app_key, text, E.now_ms()))
            d.execute("DELETE FROM autocomplete_samples WHERE app=? AND id NOT IN (SELECT id FROM autocomplete_samples "
                      "WHERE app=? ORDER BY created_at DESC LIMIT ?)", (app_key, app_key, SAMPLES_PER_APP))
            apps = d.execute("SELECT app FROM autocomplete_samples GROUP BY app ORDER BY max(created_at) DESC").fetchall()
            for (old_app,) in apps[SAMPLE_APPS_MAX:]:
                d.execute("DELETE FROM autocomplete_samples WHERE app=?", (old_app,))
            d.commit()
        finally:
            d.close()
    return True


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
    if b.get("text") and kind in ("accepted", "ignored") and not _disabled(b.get("app", ""), b.get("bundle_id", ""), s):
        learned = add_sample(app_k, str(b["text"])[-2000:])
    return {"ok": True, "learned": learned}


def stats(q: dict | None = None) -> dict:
    st = _stats()
    with E.DB_LOCK:
        d = E.db()
        try:
            d.executescript(SCHEMA)
            per_app = d.execute("SELECT app, count(*) FROM autocomplete_samples GROUP BY app ORDER BY 2 DESC").fetchall()
        finally:
            d.close()
    shown = max(int(st.get("shown", 0)), 1)
    top = sorted(st.get("apps", {}).items(), key=lambda kv: -kv[1])[:6]
    return {"shown": st.get("shown", 0), "accepted": st.get("accepted", 0),
            "accepted_chars": st.get("accepted_chars", 0), "dismissed": st.get("dismissed", 0),
            "ignored": st.get("ignored", 0), "accept_rate": round(min(1.0, st.get("accepted", 0) / shown), 3),
            "days_active": len(st.get("days", [])), "since": st.get("since"),
            "top_apps": [{"app": a, "accepted": n} for a, n in top],
            "samples": sum(n for _, n in per_app), "sample_apps": [{"app": a, "count": n} for a, n in per_app]}


def clear(b: dict) -> dict:
    removed = 0
    if b.get("samples", True):
        app = (b.get("app") or "").strip().lower()
        with E.DB_LOCK:
            d = E.db()
            try:
                d.executescript(SCHEMA)
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
    E.POST_ROUTES["/api/autocomplete/clear"] = clear
    E.GET_ROUTES["/api/autocomplete/stats"] = stats
