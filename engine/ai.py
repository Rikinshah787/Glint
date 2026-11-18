"""Glint AI layer: Claude (anthropic SDK) and OpenAI (openai SDK) behind one interface.

Each provider exposes:
  run(system, history, tools, emit) -> str   streaming tool-using agent loop
  complete_json(system, prompt, schema) -> dict   one structured-output call
  list_models() -> list[str]

User message content may be a string or a list of text/image blocks (see image_block);
each provider converts it to its own format.

`emit(kind, payload)` receives "text" deltas, "tool" calls, "tool_result" summaries
and "notice" messages so the UI can stream them.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Callable
import itertools
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

Emit = Callable[[str, dict], None]

WRAP_UP = ("You have used many tool rounds. Stop calling tools unless one is strictly necessary "
           "and answer with what you have now.")


class ProviderError(Exception):
    """User-facing configuration or API problem (missing key, bad model, refusal)."""


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[[dict], str]


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
          "array": list, "object": dict}


def validate_args(tool: Tool, args) -> str | None:
    """Minimal JSON-schema check; streamed tool inputs are not server-validated."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    props = tool.schema.get("properties", {})
    for key in tool.schema.get("required", []):
        if key not in args:
            return f"missing required argument '{key}'"
    for key, val in args.items():
        spec = props.get(key)
        if spec is None:
            return f"unknown argument '{key}'"
        if val is None:
            continue
        expected = spec.get("type")
        if isinstance(expected, list):
            expected = next((t for t in expected if t != "null"), None)
        py = _TYPES.get(expected)
        if py and not isinstance(val, py) or (expected == "integer" and isinstance(val, bool)):
            return f"argument '{key}' must be {expected}"
    return None


def fill_optional(tool: Tool, args):
    """Strict schemas list every parameter as required with null allowed; treat omitted ones as null."""
    if not isinstance(args, dict):
        return args
    props = tool.schema.get("properties", {})
    out = dict(args)
    for key in tool.schema.get("required", []):
        t = props.get(key, {}).get("type")
        if key not in out and isinstance(t, list) and "null" in t:
            out[key] = None
    return out


def call_tool(tool: Tool | None, args, emit: Emit) -> tuple[str, bool]:
    if tool is None:
        return "Error: unknown tool", True
    args = fill_optional(tool, args)
    err = validate_args(tool, args)
    if err:
        return f"Error: {err}", True
    emit("tool", {"name": tool.name, "input": args})
    try:
        out = tool.fn(args)
    except Exception as e:  # tool failures go back to the model, not up the stack
        return f"Error: {e}", True
    emit("tool_result", {"name": tool.name, "chars": len(out)})
    return out, False


# ------------------------------------------------------------- image content
# A user message's content may be a string or a list of blocks: {"type": "text", "text": ...} and
# images in Claude's shape (see image_block). Each provider converts the list to its own format.

def image_block(data_b64: str, media_type: str = "image/jpeg") -> dict:
    """Provider-neutral image content block (Claude's shape; converted for OpenAI)."""
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data_b64}}


def _image_parts(b: dict) -> tuple[str, str] | None:
    """(media_type, base64) from an image block in Claude, short or OpenAI data-URL form."""
    if b.get("type") == "image":
        src = b.get("source") or b
        return src.get("media_type", "image/jpeg"), src.get("data", "")
    if b.get("type") == "image_url":
        url = (b.get("image_url") or {}).get("url", "")
        if url.startswith("data:") and ";base64," in url:
            head, data = url[5:].split(";base64,", 1)
            return head or "image/jpeg", data
    return None


def claude_content(content):
    if not isinstance(content, list):
        return content
    out = []
    for b in content:
        if isinstance(b, str):
            out.append({"type": "text", "text": b})
        elif isinstance(b, dict) and (img := _image_parts(b)):
            out.append(image_block(img[1], img[0]))
        else:
            out.append(b)
    return out


def openai_content(content):
    if not isinstance(content, list):
        return content
    out = []
    for b in content:
        if isinstance(b, str):
            out.append({"type": "text", "text": b})
        elif isinstance(b, dict) and (img := _image_parts(b)):
            out.append({"type": "image_url", "image_url": {"url": f"data:{img[0]};base64,{img[1]}"}})
        else:
            out.append(b)
    return out


# ----------------------------------------------------------------------- Claude

class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str, model: str):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def _wrap(self, e: Exception) -> ProviderError:
        a = self._anthropic
        if isinstance(e, a.AuthenticationError):
            return ProviderError("Claude rejected the API key. Update it in Settings.")
        if isinstance(e, a.NotFoundError):
            return ProviderError(f"Claude model '{self.model}' was not found. Pick another in Settings.")
        if isinstance(e, a.RateLimitError):
            return ProviderError("Claude rate limit reached. Try again in a minute.")
        if isinstance(e, a.APIStatusError):
            return ProviderError(f"Claude API error {e.status_code}: {e.message}")
        if isinstance(e, a.APIConnectionError):
            return ProviderError("Couldn't reach the Claude API. Check your connection.")
        return ProviderError(str(e))

    def run(self, system: str, history: list[dict], tools: list[Tool], emit: Emit,
            max_steps: int = 14, wrap_at: int = 12, web_search: bool = False) -> str:
        by_name = {t.name: t for t in tools}
        tool_defs = [{"name": t.name, "description": t.description, "input_schema": t.schema,
                      "eager_input_streaming": True} for t in tools]
        if web_search:
            tool_defs.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 4})
        messages = [{"role": m["role"], "content": claude_content(m["content"])} for m in history]
        texts: list[str] = []
        json_retries = 0
        step = 0
        while step < max_steps:
            # Last round: keep the tool definitions (history holds tool blocks) but forbid calls.
            final_round = step == max_steps - 1
            try:
                with self.client.beta.messages.stream(
                    model=self.model,
                    max_tokens=16000,
                    system=system,
                    messages=messages,
                    tools=tool_defs,
                    tool_choice={"type": "none"} if final_round else {"type": "auto"},
                    output_config={"effort": "medium"},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                ) as stream:
                    for event in stream:
                        if event.type == "text":
                            emit("text", {"delta": event.text})
                    msg = stream.get_final_message()
                json_retries = 0
            except ValueError:
                # A streamed tool input that could not be parsed at all; re-issue the turn.
                json_retries += 1
                if json_retries > 2:
                    raise ProviderError("Claude returned malformed tool input repeatedly.")
                continue
            except self._anthropic.APIError as e:
                raise self._wrap(e) from e

            step += 1
            messages.append({"role": "assistant", "content": msg.content})
            texts.append("".join(b.text for b in msg.content if b.type == "text"))
            for b in msg.content:
                if b.type == "server_tool_use":
                    emit("tool", {"name": b.name, "input": b.input or {}})

            if msg.stop_reason == "refusal":
                emit("notice", {"message": "The model declined this request."})
                break
            if msg.stop_reason == "pause_turn":
                continue
            uses = [b for b in msg.content if b.type == "tool_use"]
            if msg.stop_reason != "tool_use" or not uses:
                break  # end_turn, or max_tokens (never run a possibly-truncated tool call)

            results = []
            for u in uses:
                out, is_err = call_tool(by_name.get(u.name), u.input, emit)
                block = {"type": "tool_result", "tool_use_id": u.id, "content": out}
                if is_err:
                    block["is_error"] = True
                results.append(block)
            if step >= wrap_at:
                results.append({"type": "text", "text": WRAP_UP})
            messages.append({"role": "user", "content": results})  # all results in one message
        return "\n\n".join(t for t in texts if t.strip()).strip()

    def complete_text(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": claude_content(prompt)}], output_config={"effort": "low"})
        except self._anthropic.APIError as e:
            raise self._wrap(e) from e
        if resp.stop_reason == "refusal":
            raise ProviderError("The model declined this request.")
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def complete_json(self, system: str, prompt: str, schema: dict) -> dict:
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            )
        except self._anthropic.APIError as e:
            raise self._wrap(e) from e
        if resp.stop_reason == "refusal":
            raise ProviderError("The model declined to summarize this session.")
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        return json.loads(text)

    def list_models(self) -> list[str]:
        try:
            return [m.id for m in self.client.models.list()]
        except self._anthropic.APIError as e:
            raise self._wrap(e) from e


# ----------------------------------------------------------------------- OpenAI

class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str):
        import openai
        self._openai = openai
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model

    def _wrap(self, e: Exception) -> ProviderError:
        o = self._openai
        if isinstance(e, o.AuthenticationError):
            return ProviderError("OpenAI rejected the API key. Update it in Settings.")
        if isinstance(e, o.NotFoundError):
            return ProviderError(f"OpenAI model '{self.model}' was not found. Pick another in Settings.")
        if isinstance(e, o.RateLimitError):
            return ProviderError("OpenAI rate limit reached. Try again in a minute.")
        if isinstance(e, o.APIConnectionError):
            return ProviderError("Couldn't reach the OpenAI API. Check your connection.")
        if isinstance(e, o.APIStatusError):
            return ProviderError(f"OpenAI API error {e.status_code}: {e.message}")
        return ProviderError(str(e))

    def run(self, system: str, history: list[dict], tools: list[Tool], emit: Emit,
            max_steps: int = 14, wrap_at: int = 12, web_search: bool = False) -> str:
        by_name = {t.name: t for t in tools}
        tool_defs = [{"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.schema}} for t in tools]
        messages = [{"role": "system", "content": system}] + [
            {"role": m["role"], "content": openai_content(m["content"])} for m in history]
        texts: list[str] = []
        for step in range(max_steps):
            try:
                stream = self.client.chat.completions.create(
                    model=self.model, messages=messages, tools=tool_defs, stream=True,
                    tool_choice="none" if step == max_steps - 1 else "auto")
                text, calls, finish = "", {}, None
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    finish = choice.finish_reason or finish
                    delta = choice.delta
                    if delta.content:
                        text += delta.content
                        emit("text", {"delta": delta.content})
                    for tc in delta.tool_calls or []:
                        c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            c["id"] = tc.id
                        if tc.function and tc.function.name:
                            c["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            c["args"] += tc.function.arguments
            except self._openai.APIError as e:
                raise self._wrap(e) from e

            texts.append(text)
            if not calls or finish == "length":
                break
            ordered = [calls[i] for i in sorted(calls)]
            messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["args"] or "{}"}} for c in ordered]})
            for c in ordered:
                try:
                    args = json.loads(c["args"] or "{}")
                except json.JSONDecodeError:
                    out = "Error: arguments were not valid JSON"
                else:
                    out, _ = call_tool(by_name.get(c["name"]), args, emit)
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})
            if step + 1 >= wrap_at:
                messages.append({"role": "user", "content": WRAP_UP})
        return "\n\n".join(t for t in texts if t.strip()).strip()

    def complete_text(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "system", "content": system},
                                            {"role": "user", "content": openai_content(prompt)}])
        except self._openai.APIError as e:
            raise self._wrap(e) from e
        return (resp.choices[0].message.content or "").strip()

    def complete_json(self, system: str, prompt: str, schema: dict) -> dict:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "result", "schema": schema, "strict": True}},
            )
        except self._openai.APIError as e:
            raise self._wrap(e) from e
        return json.loads(resp.choices[0].message.content or "{}")

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list())
        except self._openai.APIError as e:
            raise self._wrap(e) from e


# ------------------------------------------------------------- Apple on-device

APPLE_BIN = Path(__file__).resolve().parent.parent / "bin" / "glint-llm"
APPLE_PROMPT_CHARS = 9000   # the on-device model has a ~4k-token window shared with its answer
TIME_WORDS = re.compile(r"\b(today|this (morning|afternoon|evening)|tonight|yesterday|earlier|just now|"
                        r"last (hour|few hours)|recent(ly)?|so far)\b", re.I)
CHART_WORDS = re.compile(r"\b(chart|graph|plot|visuali[sz]e|breakdown)\b", re.I)
SCREEN_WORDS = re.compile(r"\b(this|screen|window|page|here|on my screen|open)\b", re.I)


class _AppleServer:
    """One long-running glint-llm process shared by every caller (keeps the model warm)."""
    lock = threading.Lock()
    proc = None
    available: bool | None = None
    reason = ""
    pending: dict = {}
    ids = itertools.count(1)

    @classmethod
    def ensure(cls) -> bool:
        with cls.lock:
            if cls.proc and cls.proc.poll() is None:
                return bool(cls.available)
            if not APPLE_BIN.exists():
                cls.available, cls.reason = False, "glint-llm helper not built"
                return False
            cls.proc = subprocess.Popen([str(APPLE_BIN)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1)
            first = cls.proc.stdout.readline()
            try:
                hello = json.loads(first)
            except ValueError:
                hello = {"ready": False, "availability": "helper failed to start"}
            cls.available, cls.reason = bool(hello.get("ready")), hello.get("availability", "")
            threading.Thread(target=cls._reader, args=(cls.proc,), daemon=True).start()
            return cls.available

    @classmethod
    def _reader(cls, proc) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            q = cls.pending.get(msg.get("id"))
            if q:
                q.put(msg)
        for q in list(cls.pending.values()):  # helper died: fail everything still waiting
            q.put({"error": "on-device model stopped", "code": "other"})

    @classmethod
    def request(cls, payload: dict, on_delta=None, timeout: float = 60) -> str:
        if not cls.ensure():
            raise ProviderError("Apple Intelligence isn't available on this Mac. Turn it on in System Settings "
                                "→ Apple Intelligence & Siri, or add a Claude/OpenAI key in Glint Settings.")
        rid = str(next(cls.ids))
        q: queue.Queue = queue.Queue()
        cls.pending[rid] = q
        try:
            with cls.lock:
                cls.proc.stdin.write(json.dumps({**payload, "id": rid}) + "\n")
                cls.proc.stdin.flush()
            deadline = time.time() + timeout
            while True:
                msg = q.get(timeout=max(0.1, deadline - time.time()))
                if "delta" in msg and on_delta:
                    on_delta(msg["delta"])
                elif "text" in msg:
                    return msg["text"]
                elif "error" in msg:
                    code = msg.get("code")
                    if code == "context":
                        raise ProviderError("That was too long for the on-device model.")
                    if code == "guardrail":
                        raise ProviderError("The on-device model declined this request.")
                    raise ProviderError(f"On-device model error: {msg['error'][:200]}")
        except queue.Empty:
            raise ProviderError("The on-device model took too long.") from None
        finally:
            cls.pending.pop(rid, None)


def apple_available() -> bool:
    try:
        return _AppleServer.ensure()
    except OSError:
        return False


def _fit(instructions: str, prompt: str) -> tuple[str, str]:
    instructions = instructions[:2500]
    room = APPLE_PROMPT_CHARS - len(instructions)
    if len(prompt) > room:  # keep the start (headers) and the end (the actual ask)
        head = min(1500, room // 4)
        prompt = prompt[:head] + "\n…\n" + prompt[-(room - head - 5):]
    return instructions, prompt


def _text_of(content) -> str:
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def _conform(obj, schema: dict):
    """Coerce a parsed JSON value to the schema's shape (fill missing required fields, fix types)."""
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    if t == "object":
        obj = obj if isinstance(obj, dict) else {}
        props = schema.get("properties", {})
        out = {k: _conform(obj.get(k), v) for k, v in props.items()
               if k in obj or k in schema.get("required", [])}
        if schema.get("additionalProperties", True) is not False:
            out.update({k: v for k, v in obj.items() if k not in props})
        return out
    if t == "array":
        items = schema.get("items", {})
        return [_conform(x, items) for x in obj] if isinstance(obj, list) else []
    if t == "integer":
        try:
            return int(float(obj))
        except (TypeError, ValueError):
            return 0
    if t == "number":
        try:
            return float(obj)
        except (TypeError, ValueError):
            return 0.0
    if t == "boolean":
        return bool(obj)
    if t == "string":
        if obj is None:
            return ""
        return obj if isinstance(obj, str) else json.dumps(obj) if isinstance(obj, (dict, list)) else str(obj)
    return obj


def _salvage_json(text: str):
    """Parse the first JSON object in `text`; if the model was cut off mid-reply, close it at the last
    complete field (the small on-device context can end a reply early)."""
    start = text.find("{")
    if start < 0:
        return None
    s, stack, in_str, esc, cut = text[start:], [], False, False, None
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
            if not stack:
                try:
                    return json.loads(s[:i + 1])
                except ValueError:
                    break
        elif ch == ",":
            cut = (i, list(stack))
    if cut:
        try:
            return json.loads(s[:cut[0]] + "".join(reversed(cut[1])))
        except ValueError:
            return None
    return None


class AppleProvider:
    """Apple's on-device model: private, free and fast; small context (~4k tokens), no tool calling.
    Ask runs retrieval-first: memory is searched, then the model answers from what was found."""
    name = "apple"
    model = "apple-on-device"

    def complete_text(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        ins, pr = _fit(system, _text_of(prompt))
        return _AppleServer.request({"instructions": ins, "prompt": pr, "max_tokens": min(max_tokens, 1500),
                                     "temperature": 0.3}).strip()

    def complete_json(self, system: str, prompt: str, schema: dict) -> dict:
        shape = json.dumps(schema, separators=(",", ":"))[:2200]
        ins = (f"{system}\n\nReply with ONLY one JSON object (no prose, no code fences) matching this JSON schema. "
               f"Keep every string short (titles under 8 words, sentences under 30 words):\n{shape}")
        ins, pr = _fit(ins, _text_of(prompt))
        last = ""
        for attempt in range(2):
            text = _AppleServer.request({"instructions": ins, "prompt": pr if not attempt else
                                         pr + "\n\nYour previous reply was not valid JSON. Reply with JSON only.",
                                         "max_tokens": 2000, "temperature": 0.2})
            last = text
            obj = _salvage_json(text)
            if isinstance(obj, dict) and obj:
                return _conform(obj, schema)
        raise ProviderError(f"The on-device model didn't return valid JSON ({last[:80]!r}).")

    def run(self, system: str, history: list[dict], tools: list[Tool], emit: Emit,
            max_steps: int = 14, wrap_at: int = 12, web_search: bool = False) -> str:
        by_name = {t.name: t for t in tools}
        question = _text_of(history[-1]["content"]) if history else ""
        found: list[str] = []
        if "search_memory" in by_name:
            out, _ = call_tool(by_name["search_memory"], {"query": question[:300], "limit": 8}, emit)
            found.append("Matching captures:\n" + out[:3600])
        if TIME_WORDS.search(question) and "get_today_activity" in by_name:
            out, _ = call_tool(by_name["get_today_activity"], {}, emit)
            found.append("Today's activity:\n" + out[:1600])
        if "read_foreground_text" in by_name and SCREEN_WORDS.search(question):
            out, _ = call_tool(by_name["read_foreground_text"], {}, emit)
            found.append("Current window:\n" + out[:1800])
        if CHART_WORDS.search(question) and "create_chart" in by_name and found:
            ordered = sorted(found, key=lambda x: not x.startswith("Today's activity"))  # numbers first
            self._chart(question, "\n\n".join(ordered), by_name["create_chart"], emit)
        opening = re.search(r"<opening_context>(.*?)</opening_context>", system, re.S)
        ins = ("You are Glint, a private assistant on the user's Mac. Answer the question using the memory "
               "excerpts (text captured from the user's screen, with times and apps). Be specific: what, when, "
               "which app. Write natural sentences in your own words; never paste raw excerpt lines or timestamps "
               "in brackets. If the excerpts don't contain the answer, say so plainly. Keep it short.\n\n"
               + (opening.group(1).strip()[:1500] if opening else ""))
        convo = "\n".join(f"{m['role']}: {_text_of(m['content'])[:300]}" for m in history[-5:-1])
        prompt = "\n\n".join(found) + (f"\n\nEarlier in this chat:\n{convo}" if convo else "") + \
            f"\n\nQuestion: {question}"
        ins, prompt = _fit(ins, prompt)
        return _AppleServer.request({"instructions": ins, "prompt": prompt, "max_tokens": 700, "temperature": 0.3,
                                     "stream": True}, on_delta=lambda d: emit("text", {"delta": d})).strip()

    def _chart(self, question: str, context: str, tool: Tool, emit: Emit) -> None:
        """No native tool calling on-device: extract chart data from what was found, then draw it."""
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["chart_type", "title", "labels", "values", "unit"],
                  "properties": {"chart_type": {"type": "string", "enum": ["bar", "line", "ring"]},
                                 "title": {"type": "string"}, "unit": {"type": "string"},
                                 "labels": {"type": "array", "items": {"type": "string"}},
                                 "values": {"type": "array", "items": {"type": "number"}}}}
        try:
            spec = self.complete_json("Build a chart for the request using only numbers present in the data. "
                                      "Up to 8 items. Use a bar chart unless asked otherwise.",
                                      f"Request: {question}\n\nData:\n{context[:4000]}", schema)
        except ProviderError as e:
            print(f"[ai] chart skipped: {e}", file=sys.stderr, flush=True)
            return
        n = min(len(spec["labels"]), len(spec["values"]), 8)
        if not n:
            print("[ai] chart skipped: no data points", file=sys.stderr, flush=True)
        if n:
            out, err = call_tool(tool, {"chart_type": spec["chart_type"] or "bar", "title": spec["title"] or "Chart",
                                        "labels": spec["labels"][:n], "unit": spec["unit"] or None,
                                        "series": [{"name": spec["unit"] or "Value", "values": spec["values"][:n]}]},
                                 emit)
            if err:
                print(f"[ai] chart tool rejected: {out[:200]}", file=sys.stderr, flush=True)

    def list_models(self) -> list[str]:
        return [self.model]


# ------------------------------------------------------------- fast short calls

FAST_CLAUDE_MODEL = "claude-haiku-5-5"   # latency-sensitive helpers (autocomplete)


def fast_text(provider, system: str, prompt: str, max_tokens: int = 80, timeout: float = 1.5) -> str:
    """One short, single-shot completion tuned for latency: minimal reasoning, no retries and a hard
    timeout. Works with any provider; cloud providers get their fastest request shape."""
    if isinstance(provider, ClaudeProvider):
        client = provider.client.with_options(timeout=timeout, max_retries=0)
        kw: dict = {"output_config": {"effort": "low"}}
        if "haiku" in provider.model:
            kw["thinking"] = {"type": "disabled"}  # allowed on Haiku at effort high or below
        try:
            resp = client.messages.create(model=provider.model, max_tokens=max_tokens, system=system,
                                          messages=[{"role": "user", "content": prompt}], **kw)
        except provider._anthropic.APIError as e:
            raise provider._wrap(e) from e
        if resp.stop_reason == "refusal":
            raise ProviderError("The model declined this request.")
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    if isinstance(provider, OpenAIProvider):
        client = provider.client.with_options(timeout=timeout, max_retries=0)
        kw = ({"reasoning_effort": "minimal", "max_completion_tokens": max(400, max_tokens)}
              if re.match(r"^(gpt-5|o\d)", provider.model) else {"max_tokens": max_tokens})
        try:
            resp = client.chat.completions.create(
                model=provider.model, messages=[{"role": "system", "content": system},
                                                {"role": "user", "content": prompt}], **kw)
        except provider._openai.APIError as e:
            raise provider._wrap(e) from e
        return (resp.choices[0].message.content or "").strip()
    ins, pr = _fit(system, prompt)
    return _AppleServer.request({"instructions": ins, "prompt": pr, "max_tokens": max_tokens,
                                 "temperature": 0.2}, timeout=timeout).strip()


def make_provider(provider: str, keys: dict, models: dict):
    """keys/models: {'claude': ..., 'openai': ...}. Raises ProviderError if not configured.
    provider: auto (cloud key if set, else on-device) | apple | claude | openai."""
    if provider == "auto":
        if keys.get("claude"):
            return ClaudeProvider(keys["claude"], models["claude"])
        if keys.get("openai"):
            return OpenAIProvider(keys["openai"], models["openai"])
        if apple_available():
            return AppleProvider()
        raise ProviderError("No AI model is set up. Turn on Apple Intelligence (System Settings) for the free "
                            "on-device model, or add a Claude/OpenAI key in Glint Settings.")
    if provider == "apple":
        if not apple_available():
            raise ProviderError(f"Apple's on-device model isn't available ({_AppleServer.reason or 'unknown'}). "
                                "Turn on Apple Intelligence in System Settings, or pick Claude/OpenAI.")
        return AppleProvider()
    if provider == "openai":
        if not keys.get("openai"):
            raise ProviderError("Add your OpenAI API key in Settings to use Ask.")
        return OpenAIProvider(keys["openai"], models["openai"])
    if not keys.get("claude"):
        raise ProviderError("Add your Claude API key in Settings to use Ask.")
    return ClaudeProvider(keys["claude"], models["claude"])
