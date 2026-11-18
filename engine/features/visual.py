"""Visual answers: inline charts, flowcharts and illustrations in Ask; a screenshot of the
foreground window attached to Quick Ask; on-screen overlays (callouts, badges, highlights)
drawn by the native shell; and Glance (a hotkey that explains the current window in a card).

Flow for Quick Ask screenshots:
  native shell, when the panel opens -> POST /api/visual/pending {opened_at}
                                     -> POST /api/visual/screenshot {image_b64, app, title, ...}
  wrapped engine.ask(scope="quick")  -> waits briefly for a pending shot, stores it on TOOL_CTX
  wrapped engine.thread_history      -> prepends a user turn holding the image
  CONTEXT_HOOK                        -> coordinate contract + overlay policy for the model
  draw_screen_overlay tool            -> emit_event("overlay", ...) -> quick page -> native overlay
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
import threading
import time

import ai
import engine as E

E.DEFAULT_SETTINGS.update({
    "visual_answers": True,           # create_chart / create_flowchart / create_illustration
    "visual_quick_screenshot": True,  # attach the foreground window to Quick Ask
    "visual_overlays": True,          # let Quick Ask point at things on screen
    "visual_glance": True,            # Control+Shift+G explains the current window
})

SHOT_MAX_AGE_S = 15 * 60       # a Quick Ask screenshot is reused for follow-ups this long
PENDING_WAIT_S = 3.0           # how long a question waits for a capture still in flight
MAX_IMAGE_BYTES = 6_000_000

PALETTE_TOKENS = {"accent", "muted", "text", "line", "surface", "series1", "series2", "series3",
                  "series4", "series5", "series6", "series7", "series8", "none"}
NAMED_COLORS = {"black", "white", "gray", "grey", "red", "orange", "yellow", "green", "teal", "blue",
                "purple", "pink", "brown", "transparent", "currentcolor"}
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
PATH_RE = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9eE.,\s+-]{1,6000}$")
POINTS_RE = re.compile(r"^[0-9eE.,\s+-]{1,4000}$")

# ------------------------------------------------------------------ screenshot state

_LOCK = threading.Lock()
_ARRIVED = threading.Condition(_LOCK)
_STATE: dict = {"shot": None, "pending": 0}   # pending: opened_at (ms) of a capture in flight


def _settings() -> dict:
    return E.load_settings()


def _blocked(app: str) -> bool:
    blocked = {b.lower() for b in _settings().get("blocked_apps", [])} | set(E.SELF_APPS)
    return (app or "").strip().lower() in blocked


def _decode_image(b64: str) -> tuple[str, str]:
    """Validate base64 image data; returns (clean_b64, media_type)."""
    if not isinstance(b64, str) or not b64:
        raise ValueError("image_b64 is required")
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[-1]
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("image_b64 is not valid base64")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image is too large")
    if raw[:3] == b"\xff\xd8\xff":
        return b64, "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return b64, "image/png"
    raise ValueError("image must be JPEG or PNG")


def _num(v, default=0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _pending(body: dict) -> dict:
    opened = int(_num(body.get("opened_at"), E.now_ms()))
    with _LOCK:
        shot = _STATE["shot"]
        if not shot or shot["opened_at"] < opened:
            _STATE["shot"] = None
            _STATE["pending"] = opened
    return {"ok": True}


def _store_screenshot(body: dict) -> dict:
    opened = int(_num(body.get("opened_at"), E.now_ms()))
    if body.get("failed") or not _settings().get("visual_quick_screenshot", True):
        with _ARRIVED:
            if _STATE["pending"] <= opened:
                _STATE["pending"] = 0
            _ARRIVED.notify_all()
        return {"ok": True, "attached": False, "reason": body.get("reason") or "disabled"}
    app, title = str(body.get("app") or "")[:120], str(body.get("title") or "")[:300]
    if _blocked(app) or re.search(r"incognito|private browsing", title, re.I):
        with _ARRIVED:
            _STATE["shot"], _STATE["pending"] = None, 0
            _ARRIVED.notify_all()
        return {"ok": True, "attached": False, "reason": "blocked"}
    b64, media = _decode_image(body.get("image_b64"))
    frame = body.get("frame") or {}
    shot = {
        "id": f"s{opened}", "b64": b64, "media_type": media, "app": app, "title": title,
        "width": int(_num(body.get("width"))), "height": int(_num(body.get("height"))),
        "frame": {k: _num(frame.get(k)) for k in ("x", "y", "w", "h")},
        "opened_at": opened, "at": time.time(),
    }
    with _ARRIVED:
        cur = _STATE["shot"]
        if cur and cur["opened_at"] > opened:   # a newer capture already landed
            return {"ok": True, "attached": False, "reason": "stale"}
        _STATE["shot"] = shot
        if _STATE["pending"] <= opened:
            _STATE["pending"] = 0
        _ARRIVED.notify_all()
    return {"ok": True, "attached": True, "id": shot["id"]}


def _clear_screenshot(_body=None) -> dict:
    with _ARRIVED:
        _STATE["shot"], _STATE["pending"] = None, 0
        _ARRIVED.notify_all()
    return {"ok": True}


def current_shot(wait: float = PENDING_WAIT_S) -> dict | None:
    """The latest fresh screenshot, waiting briefly if a capture is still in flight."""
    deadline = time.time() + wait
    with _ARRIVED:
        while _STATE["pending"] and time.time() < deadline:
            _ARRIVED.wait(max(0.0, deadline - time.time()))
        shot = _STATE["shot"]
    if shot and time.time() - shot["at"] <= SHOT_MAX_AGE_S:
        return shot
    return None


def _status(_q) -> dict:
    with _LOCK:
        shot, pending = _STATE["shot"], _STATE["pending"]
    fresh = shot and time.time() - shot["at"] <= SHOT_MAX_AGE_S
    return {"screenshot": {"id": shot["id"], "app": shot["app"], "title": shot["title"],
                           "width": shot["width"], "height": shot["height"],
                           "age_s": round(time.time() - shot["at"], 1)} if fresh else None,
            "pending": bool(pending)}


# ------------------------------------------------------------- wrapping ask / history

TOUR_RE = re.compile(
    r"\b(explain|walk me through|tour of|go over)\b.*\b(screen|window|page|this ui|this app)\b"
    r"|what('s| is| am i looking at)\b.*\b(on )?(my|this) (screen|window)"
    r"|\b(mark up|annotate|draw on|label (the|this|my))\b", re.I)
LOCATE_RE = re.compile(
    r"\b(where('s| is| are| do i| can i)|point (to|at|out)|show me (where|the)|circle|highlight|"
    r"which (button|menu|icon|tab|setting|field)|how do i (find|get to|open|turn|enable|disable))\b", re.I)

_orig_ask = E.ask
_orig_history = E.thread_history


def _intent(message: str) -> str | None:
    if TOUR_RE.search(message or ""):
        return "tour"
    if LOCATE_RE.search(message or ""):
        return "locate"
    return None


def ask(thread_id, message, emit, scope="main"):
    shot = None
    if scope == "quick" and _settings().get("visual_quick_screenshot", True):
        shot = current_shot()
    E.TOOL_CTX.visual = {"shot": shot, "intent": _intent(message) if shot else None,
                         "overlay_drawn": False}
    if shot:  # a new question clears whatever the last answer pointed at
        emit("overlay", {"action": "clear", "live": True})
    try:
        return _orig_ask(thread_id, message, emit, scope)
    finally:
        E.TOOL_CTX.visual = None


def thread_history(thread_id, scope):
    history = _orig_history(thread_id, scope)
    ctx = getattr(E.TOOL_CTX, "visual", None) or {}
    shot = ctx.get("shot")
    if not shot:
        return history
    note = (f"[Screenshot of the foreground window when Quick Ask opened: {shot['app']}"
            f"{' — ' + shot['title'] if shot['title'] else ''}. "
            f"{shot['width']}x{shot['height']} px. My question follows.]")
    turn = {"role": "user", "content": [ai.image_block(shot["b64"], shot["media_type"]),
                                        {"type": "text", "text": note}]}
    # The image turn goes right before the new question (Claude/OpenAI both accept back-to-back user turns).
    return history + [turn]


def _context_hook() -> str | None:
    ctx = getattr(E.TOOL_CTX, "visual", None) or {}
    shot = ctx.get("shot")
    lines = []
    if getattr(E.TOOL_CTX, "scope", "main") == "quick":
        if shot:
            lines += [
                "[Screen access this turn] screenshot attached: yes.",
                f"The image just before the question is the foreground window ({shot['app']}"
                f"{' — ' + shot['title'] if shot['title'] else ''}), {shot['width']}x{shot['height']} px. "
                "Answer from the image when it shows what's needed; call read_foreground_text only if "
                "the text in the image is unreadable or cut off.",
                "Coordinates for draw_screen_overlay: normalized 0-1 relative to this screenshot. (0,0) is "
                "the top-left corner of the captured window (title bar included), (1,1) its bottom-right. "
                "Don't add screen or window offsets. x grows right, y grows down. Aim at the centre of the "
                "control you mean.",
                "Overlay policy: default to text only; a screenshot is not a reason to draw. To help the user "
                "find one control, draw 1-2 callouts. For an explicit tour or 'mark up my screen' request, "
                "draw 2-4 items first, then answer in short text. Labels are ≤6 words and say something useful "
                "(not just the control's name). Never annotate obvious UI or the Quick Ask panel.",
            ]
            if not _settings().get("visual_overlays", True):
                lines.append("On-screen drawing is turned off by the user: describe locations in words.")
            elif ctx.get("intent") == "tour":
                lines.append("This question asks for a screen tour: call draw_screen_overlay (2-4 items) "
                             "before writing the answer.")
            elif ctx.get("intent") == "locate":
                lines.append("This question asks where something is: if it's visible, point at it with one "
                             "draw_screen_overlay call (1-2 items), then answer in a sentence.")
        else:
            lines.append("[Screen access this turn] screenshot attached: no. On-screen drawing is unavailable; "
                         "use read_foreground_text for questions about the screen.")
    if _settings().get("visual_answers", True):
        lines.append("[Visuals] Default to text. Use create_chart / create_flowchart / create_illustration only "
                     "when a picture is clearly better: numbers to compare or a trend, steps, a decision, a "
                     "timeline, a comparison or a spatial idea. One visual per answer is usually enough. Chart "
                     "values must come from context or tools, never invented. Never paste SVG or Mermaid as text.")
    return "\n".join(lines) or None


# ------------------------------------------------------------------ visual tools

def _s(v, n: int, field: str, required: bool = False) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ValueError(f"{field} is required")
        return None
    if not isinstance(v, (str, int, float)):
        raise ValueError(f"{field} must be a string")
    return str(v).strip()[:n]


def _dicts(v, field: str) -> list[dict]:
    if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
        raise ValueError(f"{field} must be an array of objects")
    return v


def _visuals_on():
    if not _settings().get("visual_answers", True):
        raise ValueError("visual answers are turned off in Settings; answer in text")


def _done(payload: dict) -> str:
    E.emit_event("visual", payload)
    return json.dumps({"ok": True, "rendered": payload["title"]})


CHART_TYPES = ("bar", "line", "ring", "stats")


def tool_create_chart(args: dict) -> str:
    _visuals_on()
    kind = args.get("chart_type")
    if kind not in CHART_TYPES:
        raise ValueError(f"chart_type must be one of {', '.join(CHART_TYPES)}")
    title = _s(args.get("title"), 90, "title", True)
    out = {"type": "chart", "chart_type": kind, "title": title,
           "subtitle": _s(args.get("subtitle"), 160, "subtitle"), "unit": _s(args.get("unit"), 16, "unit")}
    if kind == "stats":
        stats = _dicts(args.get("stats") or [], "stats")
        if not 1 <= len(stats) <= 6:
            raise ValueError("stats needs 1-6 items of {label, value, delta?}")
        out["stats"] = [{"label": _s(s.get("label"), 40, "stats.label", True),
                         "value": _s(s.get("value"), 24, "stats.value", True),
                         "delta": _s(s.get("delta"), 24, "stats.delta")} for s in stats]
        return _done(out)
    labels = args.get("labels") or []
    if not isinstance(labels, list) or not labels:
        raise ValueError("labels is required for bar, line and ring charts")
    limit = {"bar": 24, "line": 60, "ring": 8}[kind]
    if len(labels) > limit:
        raise ValueError(f"{kind} charts take at most {limit} labels; group the rest")
    labels = [_s(x, 40, "label", True) for x in labels]
    series = args.get("series") or []
    if not isinstance(series, list) or not series:
        raise ValueError("series is required: [{name, values}]")
    if len(series) > (1 if kind == "ring" else 4):
        raise ValueError("ring charts take one series; bar/line at most 4")
    clean = []
    for i, s in enumerate(series):
        if not isinstance(s, dict) or not isinstance(s.get("values"), list):
            raise ValueError("each series needs a values array")
        vals = s["values"]
        if len(vals) != len(labels):
            raise ValueError(f"series {i + 1} has {len(vals)} values for {len(labels)} labels")
        nums = []
        for v in vals:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError("values must be finite numbers")
            nums.append(v)
        clean.append({"name": _s(s.get("name"), 40, "series.name") or f"Series {i + 1}", "values": nums})
    if kind == "ring":
        if any(v < 0 for v in clean[0]["values"]) or sum(clean[0]["values"]) <= 0:
            raise ValueError("ring values must be non-negative with a positive total")
    out.update(labels=labels, series=clean)
    return _done(out)


FLOW_PRESETS = ("process", "decision", "sequence", "timeline", "comparison", "mermaid")
NODE_KINDS = ("start", "step", "decision", "end")


def tool_create_flowchart(args: dict) -> str:
    _visuals_on()
    preset = args.get("preset")
    if preset not in FLOW_PRESETS:
        raise ValueError(f"preset must be one of {', '.join(FLOW_PRESETS)}")
    title = _s(args.get("title"), 90, "title", True)
    out = {"type": "flowchart", "preset": preset, "title": title,
           "subtitle": _s(args.get("subtitle"), 160, "subtitle")}
    steps = _dicts(args.get("steps") or [], "steps")
    if preset == "mermaid":
        src = args.get("mermaid")
        if not isinstance(src, str) or not src.strip():
            raise ValueError("mermaid source is required for the mermaid preset")
        src = re.sub(r"^```\w*\s*|\s*```$", "", src.strip())
        if len(src) > 8000:
            raise ValueError("mermaid source is too long")
        out["mermaid"] = src
    elif preset in ("process", "timeline"):
        if not 2 <= len(steps) <= 12:
            raise ValueError(f"{preset} needs 2-12 steps")
        out["steps"] = [{"label": _s(s.get("label"), 60, "steps.label", True),
                         "detail": _s(s.get("detail"), 140, "steps.detail")} for s in steps]
    elif preset == "sequence":
        if not 1 <= len(steps) <= 16:
            raise ValueError("sequence needs 1-16 steps of {from, to, label}")
        out["steps"] = [{"from": _s(s.get("from"), 30, "steps.from", True),
                         "to": _s(s.get("to"), 30, "steps.to", True),
                         "label": _s(s.get("label"), 60, "steps.label", True)} for s in steps]
        if len({x for s in out["steps"] for x in (s["from"], s["to"])}) > 6:
            raise ValueError("sequence diagrams take at most 6 participants")
    elif preset == "decision":
        nodes, edges = _dicts(args.get("nodes") or [], "nodes"), _dicts(args.get("edges") or [], "edges")
        if not 2 <= len(nodes) <= 16:
            raise ValueError("decision needs 2-16 nodes of {id, label, kind}")
        ids = set()
        out["nodes"] = []
        for n in nodes:
            nid = _s(n.get("id"), 24, "nodes.id", True)
            if nid in ids:
                raise ValueError(f"duplicate node id '{nid}'")
            ids.add(nid)
            kind = n.get("kind") if n.get("kind") in NODE_KINDS else "step"
            out["nodes"].append({"id": nid, "label": _s(n.get("label"), 60, "nodes.label", True), "kind": kind})
        if not edges or len(edges) > 30:
            raise ValueError("decision needs 1-30 edges of {from, to, label?}")
        out["edges"] = []
        for e in edges:
            a, b = _s(e.get("from"), 24, "edges.from", True), _s(e.get("to"), 24, "edges.to", True)
            if a not in ids or b not in ids:
                raise ValueError(f"edge {a} -> {b} refers to an unknown node id")
            out["edges"].append({"from": a, "to": b, "label": _s(e.get("label"), 24, "edges.label")})
    elif preset == "comparison":
        cols = _dicts(args.get("columns") or [], "columns")
        if not 2 <= len(cols) <= 4:
            raise ValueError("comparison needs 2-4 columns of {title, points[]}")
        out["columns"] = []
        for c in cols:
            pts = c.get("points") or []
            if not isinstance(pts, list) or not 1 <= len(pts) <= 8:
                raise ValueError("each column needs 1-8 points")
            out["columns"].append({"title": _s(c.get("title"), 40, "columns.title", True),
                                   "points": [_s(p, 100, "point", True) for p in pts]})
    return _done(out)


SHAPES = ("rect", "circle", "ellipse", "line", "path", "polygon", "polyline", "text", "arrow")
NUM_FIELDS = ("x", "y", "width", "height", "cx", "cy", "r", "rx", "ry", "x1", "y1", "x2", "y2",
              "stroke_width", "font_size", "opacity", "rotate")


def _color(v):
    if v is None:
        return None
    v = str(v).strip()
    if v.lower() in NAMED_COLORS or v in PALETTE_TOKENS or COLOR_RE.match(v):
        return v
    raise ValueError(f"unsupported colour '{v[:20]}' (use #hex, a basic name, or accent/muted/text/series1-8)")


def _element(el: dict, depth: int = 0) -> dict:
    if not isinstance(el, dict):
        raise ValueError("elements must be objects")
    shape = el.get("type")
    if shape not in SHAPES:
        raise ValueError(f"element type must be one of {', '.join(SHAPES)}")
    out = {"type": shape}
    for k in NUM_FIELDS:
        if el.get(k) is not None:
            v = _num(el[k], None)
            if v is None:
                raise ValueError(f"{k} must be a number")
            out[k] = max(-10000.0, min(10000.0, v))
    if "opacity" in out:
        out["opacity"] = max(0.0, min(1.0, out["opacity"]))
    for k in ("fill", "stroke"):
        c = _color(el.get(k))
        if c:
            out[k] = c
    if el.get("dash"):
        out["dash"] = bool(el["dash"])
    if shape == "path":
        d = str(el.get("d") or "")
        if not PATH_RE.match(d):
            raise ValueError("path d may only contain path commands and numbers")
        out["d"] = d
    if shape in ("polygon", "polyline"):
        pts = str(el.get("points") or "")
        if not POINTS_RE.match(pts):
            raise ValueError("points must be numbers like '10,10 50,20'")
        out["points"] = pts
    if shape == "text":
        out["text"] = _s(el.get("text"), 200, "text", True)
        if el.get("anchor") in ("start", "middle", "end"):
            out["anchor"] = el["anchor"]
        if el.get("weight") in ("normal", "bold"):
            out["weight"] = el["weight"]
        if el.get("font") in ("sans", "mono"):
            out["font"] = el["font"]
    return out


def tool_create_illustration(args: dict) -> str:
    _visuals_on()
    title = _s(args.get("title"), 90, "title", True)
    vb = args.get("view_box") or "0 0 400 300"
    parts = str(vb).replace(",", " ").split()
    if len(parts) != 4:
        raise ValueError("view_box must be 'minX minY width height'")
    nums = [_num(p, None) for p in parts]
    if None in nums or nums[2] <= 0 or nums[3] <= 0 or nums[2] > 4000 or nums[3] > 4000:
        raise ValueError("view_box width/height must be 1-4000")
    els = args.get("elements") or []
    if not isinstance(els, list) or not 1 <= len(els) <= 200:
        raise ValueError("elements needs 1-200 shapes")
    return _done({"type": "illustration", "title": title,
                  "subtitle": _s(args.get("subtitle"), 160, "subtitle"),
                  "caption": _s(args.get("caption"), 240, "caption"),
                  "view_box": " ".join(f"{n:g}" for n in nums),
                  "elements": [_element(e) for e in els]})


# ------------------------------------------------------------------ screen overlays

OVERLAY_KINDS = ("callout", "badge", "highlight")


def tool_draw_screen_overlay(args: dict) -> str:
    ctx = getattr(E.TOOL_CTX, "visual", None) or {}
    shot = ctx.get("shot")
    if not shot:
        raise ValueError("no screenshot is attached this turn, so nothing can be drawn; describe the location in words")
    if not _settings().get("visual_overlays", True):
        raise ValueError("on-screen drawing is turned off in Settings; describe the location in words")
    items = args.get("items") or []
    if not isinstance(items, list) or not 1 <= len(items) <= 6:
        raise ValueError("items needs 1-6 entries")
    clean = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or it.get("kind") not in OVERLAY_KINDS:
            raise ValueError(f"item {i + 1}: kind must be callout, badge or highlight")
        x, y = _num(it.get("x"), -1), _num(it.get("y"), -1)
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError(f"item {i + 1}: x and y must be normalized 0-1 within the screenshot")
        c = {"kind": it["kind"], "x": round(x, 4), "y": round(y, 4),
             "label": _s(it.get("label"), 60, "label") or ("" if it["kind"] != "callout" else None)}
        if it["kind"] == "callout" and not c["label"]:
            raise ValueError(f"item {i + 1}: a callout needs a label")
        if it["kind"] == "badge" and not c["label"]:
            c["label"] = str(i + 1)
        if it["kind"] == "highlight":
            w, h = _num(it.get("width"), 0), _num(it.get("height"), 0)
            if w <= 0 or h <= 0:
                raise ValueError(f"item {i + 1}: a highlight needs width and height (normalized)")
            c["width"], c["height"] = round(min(w, 1 - x), 4), round(min(h, 1 - y), 4)
        if it.get("side") in ("left", "right", "top", "bottom"):
            c["side"] = it["side"]
        clean.append(c)
    mode = args.get("ui_mode") if args.get("ui_mode") in ("light", "dark") else "light"
    dur = max(5, min(120, int(_num(args.get("duration_s"), 40))))
    E.emit_event("overlay", {"action": "show", "live": True, "shot_id": shot["id"], "app": shot["app"],
                             "frame": shot["frame"], "ui_mode": mode, "duration_s": dur, "items": clean})
    ctx["overlay_drawn"] = True
    return json.dumps({"ok": True, "drawn": len(clean), "note": "The user now sees these on screen; "
                       "refer to them briefly instead of re-describing positions."})


def tool_hide_screen_overlay(args: dict) -> str:
    E.emit_event("overlay", {"action": "clear", "live": True})
    return json.dumps({"ok": True})


# ------------------------------------------------------------------------ Glance

GLANCE_SYSTEM = """You are Glint's Glance: the user pressed a hotkey to understand the window \
in front of them. Explain what is on screen in a small card.
- At most 70 words. Lead with what this is and the one thing that matters most (a state, an error, \
a decision, a number). Then, if obvious, one concrete next step.
- Plain text. You may use up to 4 short "- " bullets and **bold** for one key term. No headings, \
no preamble, no "This screenshot shows"."""


def _glance(body: dict) -> dict:
    if not _settings().get("visual_glance", True):
        raise ValueError("Glance is turned off in Settings")
    app, title = str(body.get("app") or "")[:120], str(body.get("title") or "")[:300]
    if _blocked(app):
        return {"text": f"Glint doesn't look at {app} (it's on your blocked list).", "app": app}
    b64, media = _decode_image(body.get("image_b64"))
    question = _s(body.get("question"), 400, "question")
    prompt = [ai.image_block(b64, media),
              {"type": "text", "text": f"Window: {app}{' — ' + title if title else ''}\n"
                                       f"{question or 'What am I looking at?'}"}]
    text = E.provider().complete_text(GLANCE_SYSTEM, prompt, max_tokens=700)
    return {"text": text.strip() or "Nothing to explain here.", "app": app, "title": title}


# ---------------------------------------------------------------------- register

def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


STR = {"type": "string"}
NSTR = {"type": ["string", "null"]}
NUM = {"type": "number"}

CHART_SCHEMA = _schema({
    "chart_type": {"type": "string", "enum": list(CHART_TYPES)},
    "title": STR, "subtitle": NSTR, "unit": {**NSTR, "description": "Unit suffix for values, e.g. 'h', '%', '$'."},
    "labels": {"type": ["array", "null"], "items": STR, "description": "Categories / x-axis labels (bar, line, ring)."},
    "series": {"type": ["array", "null"], "description": "bar/line: 1-4 series; ring: exactly one.",
               "items": {"type": "object", "properties": {"name": STR, "values": {"type": "array", "items": NUM}},
                         "required": ["name", "values"]}},
    "stats": {"type": ["array", "null"], "description": "stats: 1-6 headline numbers.",
              "items": {"type": "object", "properties": {"label": STR, "value": STR, "delta": NSTR},
                        "required": ["label", "value"]}},
}, ["chart_type", "title"])

FLOW_SCHEMA = _schema({
    "preset": {"type": "string", "enum": list(FLOW_PRESETS)},
    "title": STR, "subtitle": NSTR,
    "steps": {"type": ["array", "null"], "description":
              "process/timeline: [{label, detail?}] in order (timeline label = when). "
              "sequence: [{from, to, label}] messages between participants.",
              "items": {"type": "object", "properties": {"label": STR, "detail": NSTR, "from": NSTR, "to": NSTR},
                        "required": ["label"]}},
    "nodes": {"type": ["array", "null"], "description": "decision: [{id, label, kind}] kind start|step|decision|end.",
              "items": {"type": "object", "properties": {"id": STR, "label": STR,
                                                          "kind": {"type": "string", "enum": list(NODE_KINDS)}},
                        "required": ["id", "label"]}},
    "edges": {"type": ["array", "null"], "description": "decision: [{from, to, label?}] e.g. label 'yes'/'no'.",
              "items": {"type": "object", "properties": {"from": STR, "to": STR, "label": NSTR},
                        "required": ["from", "to"]}},
    "columns": {"type": ["array", "null"], "description": "comparison: 2-4 [{title, points[]}].",
                "items": {"type": "object", "properties": {"title": STR, "points": {"type": "array", "items": STR}},
                          "required": ["title", "points"]}},
    "mermaid": {**NSTR, "description": "mermaid preset only: raw Mermaid source, no code fences."},
}, ["preset", "title"])

ELEMENT_ITEM = {"type": "object", "properties": {
    "type": {"type": "string", "enum": list(SHAPES)},
    **{k: NUM for k in NUM_FIELDS},
    "d": STR, "points": STR, "text": STR, "fill": STR, "stroke": STR, "dash": {"type": "boolean"},
    "anchor": {"type": "string", "enum": ["start", "middle", "end"]},
    "weight": {"type": "string", "enum": ["normal", "bold"]},
    "font": {"type": "string", "enum": ["sans", "mono"]},
}, "required": ["type"]}

ILLUSTRATION_SCHEMA = _schema({
    "title": STR, "subtitle": NSTR, "caption": NSTR,
    "view_box": {**NSTR, "description": "SVG viewBox, default '0 0 400 300'."},
    "elements": {"type": "array", "items": ELEMENT_ITEM},
}, ["title", "elements"])

OVERLAY_SCHEMA = _schema({
    "items": {"type": "array", "description": "1-6 marks, usually 1-2.", "items": {
        "type": "object", "properties": {
            "kind": {"type": "string", "enum": list(OVERLAY_KINDS),
                     "description": "callout = label chip + leader line to (x,y); badge = numbered step "
                                    "marker at (x,y); highlight = ring around the box x,y,width,height."},
            "x": {**NUM, "description": "0-1 from the screenshot's left edge (target point, or box left)."},
            "y": {**NUM, "description": "0-1 from the screenshot's top edge (target point, or box top)."},
            "width": {"type": ["number", "null"], "description": "highlight only, normalized."},
            "height": {"type": ["number", "null"], "description": "highlight only, normalized."},
            "label": {**NSTR, "description": "≤6 words. Required for callouts; badges default to their number."},
            "side": {"type": ["string", "null"], "enum": ["left", "right", "top", "bottom", None],
                     "description": "Where the callout chip sits relative to the target (default: auto)."},
        }, "required": ["kind", "x", "y"]}},
    "ui_mode": {"type": ["string", "null"], "enum": ["light", "dark", None],
                "description": "light when the area behind is pale (most apps), dark for dark UIs."},
    "duration_s": {"type": ["integer", "null"], "description": "Seconds to keep it up (default 40)."},
}, ["items"])


def register():
    E.ask = ask
    E.thread_history = thread_history
    E.CONTEXT_HOOKS.append(_context_hook)
    E.register_tool(ai.Tool(
        "create_chart",
        "Show a small chart inline in the answer. bar: compare categories; line: change over time; ring: "
        "parts of a whole (≤8 slices); stats: 1-6 headline numbers. Use only numbers from context or tool "
        "results, never invented ones. Skip it when one or two numbers fit in a sentence.",
        CHART_SCHEMA, tool_create_chart))
    E.register_tool(ai.Tool(
        "create_flowchart",
        "Show a diagram inline. Presets: process (ordered steps), timeline (dated events), decision (nodes + "
        "labelled edges, e.g. yes/no), sequence (messages between ≤6 participants), comparison (2-4 columns of "
        "points); or mermaid (raw flowchart source) when no preset fits.",
        FLOW_SCHEMA, tool_create_flowchart))
    E.register_tool(ai.Tool(
        "create_illustration",
        "Draw a simple explanatory figure inline from SVG shapes (rect, circle, ellipse, line, arrow, path, "
        "polygon, polyline, text) on a view_box canvas (default 400x300). Colours: #hex, basic names, or theme "
        "tokens accent/muted/text/line/surface/series1-8. Use for spatial or conceptual ideas, not data.",
        ILLUSTRATION_SCHEMA, tool_create_illustration))
    # Overlay tools only make sense in Quick Ask, where a screenshot of the user's window is attached.
    E.QUICK_TOOLS.append(ai.Tool(
        "draw_screen_overlay",
        "Draw callouts, numbered badges or highlight rings over the user's real screen, positioned in "
        "normalized 0-1 coordinates of the attached screenshot. Only works when a screenshot is attached this "
        "turn. Use 1-2 items to show where a control is; 2-4 for an explicit tour. Replaces earlier marks.",
        OVERLAY_SCHEMA, tool_draw_screen_overlay))
    E.QUICK_TOOLS.append(ai.Tool(
        "hide_screen_overlay", "Remove everything drawn on the user's screen.",
        _schema({}, []), tool_hide_screen_overlay))
    E.POST_ROUTES["/api/visual/pending"] = _pending
    E.POST_ROUTES["/api/visual/screenshot"] = _store_screenshot
    E.POST_ROUTES["/api/visual/screenshot/clear"] = _clear_screenshot
    E.POST_ROUTES["/api/visual/glance"] = _glance
    E.GET_ROUTES["/api/visual/status"] = _status
