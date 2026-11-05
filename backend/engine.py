"""
engine.py — the REASON step for real. Two vendors, one contract.

Both adapters do the same job: take our neutral transcript + tool specs, ask the
model what to do, and return a Decision (a tool call WITH the vendor's own id, or
a final answer). The id threading is the fiddly part that's wrong in most demos —
done properly here so multi-turn tool use actually works.

Needs OPENAI_API_KEY or ANTHROPIC_API_KEY depending on which engine you pick.
"""
import json
from dataclasses import dataclass
from typing import Optional

from config import cfg


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Decision:
    thought: str = ""
    tool_call: Optional[ToolCall] = None
    final: Optional[str] = None
    usage: Optional[dict] = None        # {prompt, completion} tokens, if reported


# ── OpenAI ───────────────────────────────────────────────────────────────────
class OpenAIProvider:
    name = "openai"

    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=cfg.OPENAI_API_KEY)
        self.model = cfg.OPENAI_MODEL

    @staticmethod
    def _messages(history):
        out = []
        for h in history:
            if h["role"] in ("system", "user"):
                out.append({"role": h["role"], "content": h["content"]})
            elif h["role"] == "assistant":
                m = {"role": "assistant", "content": h.get("thought") or ""}
                if h.get("tool"):
                    t = h["tool"]
                    m["tool_calls"] = [{"id": t["id"], "type": "function",
                                        "function": {"name": t["name"],
                                                     "arguments": json.dumps(t["args"])}}]
                out.append(m)
            elif h["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": h["id"], "content": h["content"]})
        return out

    def reason(self, history, tools) -> Decision:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=self._messages(history),
            tools=[{"type": "function", "function": t} for t in tools])
        msg = resp.choices[0].message
        usage = {"prompt": resp.usage.prompt_tokens,
                 "completion": resp.usage.completion_tokens} if resp.usage else None
        if msg.tool_calls:
            c = msg.tool_calls[0]
            return Decision(thought=msg.content or "",
                            tool_call=ToolCall(c.id, c.function.name,
                                               json.loads(c.function.arguments or "{}")),
                            usage=usage)
        return Decision(thought=msg.content or "", final=msg.content or "", usage=usage)


# ── Anthropic ────────────────────────────────────────────────────────────────
class AnthropicProvider:
    name = "anthropic"

    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
        self.model = cfg.ANTHROPIC_MODEL

    @staticmethod
    def _split(history):
        system = next((h["content"] for h in history if h["role"] == "system"), "")
        msgs = []
        for h in history:
            if h["role"] == "user":
                msgs.append({"role": "user", "content": h["content"]})
            elif h["role"] == "assistant":
                content = []
                if h.get("thought"):
                    content.append({"type": "text", "text": h["thought"]})
                if h.get("tool"):
                    t = h["tool"]
                    content.append({"type": "tool_use", "id": t["id"],
                                    "name": t["name"], "input": t["args"]})
                msgs.append({"role": "assistant", "content": content or "…"})
            elif h["role"] == "tool":
                msgs.append({"role": "user",
                             "content": [{"type": "tool_result",
                                          "tool_use_id": h["id"], "content": h["content"]}]})
        return system, msgs

    def reason(self, history, tools) -> Decision:
        system, msgs = self._split(history)
        resp = self.client.messages.create(
            model=self.model, max_tokens=1500, system=system, messages=msgs,
            tools=[{"name": t["name"], "description": t["description"],
                    "input_schema": t["parameters"]} for t in tools])
        thought = "".join(b.text for b in resp.content if b.type == "text")
        usage = {"prompt": resp.usage.input_tokens, "completion": resp.usage.output_tokens}
        for b in resp.content:
            if b.type == "tool_use":
                return Decision(thought=thought,
                                tool_call=ToolCall(b.id, b.name, dict(b.input)),
                                usage=usage)
        return Decision(thought=thought, final=thought, usage=usage)


def get_provider(kind: str):
    kind = (kind or "openai").lower()
    if kind == "openai":
        return OpenAIProvider()
    if kind == "anthropic":
        return AnthropicProvider()
    raise ValueError(f"unknown engine: {kind} (use 'openai' or 'anthropic')")
