"""
loop.py — the real, dynamic agent loop.

No script. The LLM reads the goal, decides which tool to call (and with what
args), reacts to the result, and keeps going until it has enough to write the
plan. Each beat is yielded as an event so the UI can stream it live — including
the latency of every real call, and tool errors handled as recoverable results.

    sense  = the growing transcript the model reads each turn
    reason = provider.reason(...)  -> a tool call, or the final plan
    act    = run the tool, time it, fold the result back in
"""
import json
import time

from config import cfg
from tools import registry
from http_client import HttpError

SYSTEM = (
    "You are a meticulous travel-planning assistant. Use the tools to research a "
    "REAL plan: resolve the destination to coordinates first, check the weather, "
    "find real hotels/attractions, look up flights only if the user gave an origin, "
    "and search the web for current tips. Call ONE tool at a time.\n"
    "Rules:\n"
    "- If find_places returns no results, do NOT give up: retry once with a larger "
    "radius_m (e.g. 15000) or a different category before moving on.\n"
    "- Ground the plan ONLY in what the tools actually returned. If a search stayed "
    "empty even after retrying, say so honestly (e.g. 'no hotels found via search') "
    "rather than inventing specific names.\n"
    "- Respect the weather you sensed: if rain is likely, favour covered/indoor "
    "options and say why.\n"
    "When you have enough, stop calling tools and write a concise, day-by-day plan "
    "grounded in what the tools returned. If a tool errors, adapt and try another way."
)


def run(goal: str, provider):
    """Generator of event dicts (start / reason / tool_result / final / error)."""
    history = [{"role": "system", "content": SYSTEM},
               {"role": "user", "content": goal}]
    yield {"type": "start", "goal": goal, "engine": provider.name}

    for turn in range(1, cfg.MAX_TURNS + 1):
        # ── REASON ──
        try:
            t0 = time.time()
            decision = provider.reason(history, registry.specs())
            think_ms = int((time.time() - t0) * 1000)
        except Exception as e:                       # bad key, model error, etc.
            yield {"type": "error", "where": "reason", "message": f"{type(e).__name__}: {e}"}
            return

        history.append({"role": "assistant", "thought": decision.thought,
                        "tool": ({"id": decision.tool_call.id,
                                  "name": decision.tool_call.name,
                                  "args": decision.tool_call.args}
                                 if decision.tool_call else None)})
        yield {"type": "reason", "turn": turn, "thought": decision.thought,
               "tool": ({"name": decision.tool_call.name, "args": decision.tool_call.args}
                        if decision.tool_call else None),
               "latency_ms": think_ms, "usage": decision.usage}

        # No tool? The model is done — emit the final plan.
        if not decision.tool_call:
            yield {"type": "final", "turn": turn, "text": decision.final or decision.thought}
            return

        # ── ACT ── (run the real tool; time it; errors become recoverable results)
        tc = decision.tool_call
        t1 = time.time()
        try:
            result = registry.call(tc.name, tc.args)
            ok = not (isinstance(result, dict) and "error" in result)
        except HttpError as e:
            result, ok = {"error": str(e)}, False
        except Exception as e:
            result, ok = {"error": f"{type(e).__name__}: {e}"}, False
        act_ms = int((time.time() - t1) * 1000)

        history.append({"role": "tool", "id": tc.id, "name": tc.name,
                        "content": json.dumps(result)[:4000]})   # cap context growth
        yield {"type": "tool_result", "turn": turn, "name": tc.name,
               "ok": ok, "latency_ms": act_ms, "result": result}

    yield {"type": "final", "turn": cfg.MAX_TURNS,
           "text": "Reached the turn limit — here's the partial plan from what was gathered."}
