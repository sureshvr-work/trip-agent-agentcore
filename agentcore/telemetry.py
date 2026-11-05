"""GenAI spans for the custom loop; never record prompts, tool payloads or URLs."""
from contextlib import contextmanager
from functools import wraps
import os

from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode

tracer = trace.get_tracer("trip_agent.agentcore")


def trace_ids():
    context = trace.get_current_span().get_span_context()
    return {"trace_id": format(context.trace_id, "032x")} if context.is_valid else {}


@contextmanager
def safe_span(name, attributes, kind=SpanKind.INTERNAL):
    with tracer.start_as_current_span(name, kind=kind, attributes=attributes,
                                     record_exception=False, set_status_on_exception=False) as span:
        try:
            yield span
        except Exception as error:
            span.set_attribute("error.type", type(error).__name__)
            span.set_status(StatusCode.ERROR)
            raise


def trace_invocation(function):
    @wraps(function)
    def wrapped(payload, context):
        agent = os.getenv("TRIP_AGENT_NAME", "trip_agent_bedrock_demo")
        attributes = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": agent,
                      "gen_ai.provider.name": "aws.bedrock"}
        session = getattr(context, "session_id", None)
        if session:
            attributes["session.id"] = session
            attributes["gen_ai.conversation.id"] = session
        span = tracer.start_span("invoke_agent " + agent, kind=SpanKind.INTERNAL, attributes=attributes)
        events = function(payload, context)
        try:
            while True:
                # Starlette can resume a sync stream in a different thread/context.
                # Attach only while advancing it, and detach BEFORE each yield.
                with trace.use_span(span, end_on_exit=False, record_exception=False,
                                    set_status_on_exception=False):
                    try:
                        event = next(events)
                    except StopIteration:
                        break
                    if event.get("type") == "error":
                        span.set_status(StatusCode.ERROR)
                    if event.get("type") == "runtime":
                        event.update(trace_ids())
                yield event
        except Exception as error:
            span.set_attribute("error.type", type(error).__name__)
            span.set_status(StatusCode.ERROR)
            raise
        finally:
            events.close()
            span.end()
            provider = trace.get_tracer_provider()
            if hasattr(provider, "force_flush"):
                provider.force_flush(timeout_millis=5000)
    return wrapped


def trace_events(events):
    """The next loop advance after a tool decision executes exactly that tool."""
    pending_tool = None
    iterator = iter(events)
    while True:
        try:
            if pending_tool:
                with safe_span("execute_tool " + pending_tool, {
                    "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": pending_tool,
                    "gen_ai.tool.type": "function"}) as span:
                    event = next(iterator)
                    if event.get("type") == "tool_result":
                        span.set_attribute("trip_agent.tool.ok", bool(event.get("ok")))
                        if not event.get("ok"):
                            span.set_status(StatusCode.ERROR)
            else:
                event = next(iterator)
        except StopIteration:
            return
        pending_tool = (event.get("tool") or {}).get("name") if event.get("type") == "reason" else None
        yield event
