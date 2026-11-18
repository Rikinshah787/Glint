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
from dataclasses import dataclass
from typing import Callable

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


def call_tool(tool: Tool | None, args, emit: Emit) -> tuple[str, bool]:
    if tool is None:
        return "Error: unknown tool", True
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


def make_provider(provider: str, keys: dict, models: dict):
    """keys/models: {'claude': ..., 'openai': ...}. Raises ProviderError if not configured."""
    if provider == "openai":
        if not keys.get("openai"):
            raise ProviderError("Add your OpenAI API key in Settings to use Ask.")
        return OpenAIProvider(keys["openai"], models["openai"])
    if not keys.get("claude"):
        raise ProviderError("Add your Claude API key in Settings to use Ask.")
    return ClaudeProvider(keys["claude"], models["claude"])
