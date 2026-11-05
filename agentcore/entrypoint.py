"""Thin AgentCore SDK wrapper; the agent loop still lives in backend/loop.py."""
import os
import time
import uuid

from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.runtime import BedrockAgentCoreContext

from bootstrap import prepare, redact
from runtime_logging import configure_logger
from telemetry import trace_events, trace_ids, trace_invocation

app = BedrockAgentCoreApp()
logger = configure_logger("trip_agent.agentcore")


@app.entrypoint
@trace_invocation
def invoke(payload, context):
    if not isinstance(payload, dict):
        yield {"type": "error", "where": "input", "message": "Expected a JSON object."}
        return
    goal = payload.get("goal", payload.get("prompt", ""))
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 10000:
        yield {"type": "error", "where": "input", "message": "Provide a goal string of 1–10000 characters."}
        return
    session_id = getattr(context, "session_id", None)
    fields = {"session_id": session_id,
              "request_id": BedrockAgentCoreContext.get_request_id() or str(uuid.uuid4()),
              **trace_ids()}
    started = time.perf_counter()
    outcome = "incomplete"
    logger.info("invocation_started", extra={"fields": {**fields, "goal_chars": len(goal)}})
    try:
        loop, provider, instance_id = prepare()
        fields.update(instance_id=instance_id, model_id=provider.model_id)
        logger.info("runtime_initialized", extra={"fields": fields})
        yield {"type": "runtime", "hosting": "agentcore", "engine": "bedrock",
               "model_id": provider.model_id, "session_id": session_id,
               "instance_id": instance_id, "region": os.getenv("TRIP_AWS_REGION", "us-east-2")}
        for event in trace_events(loop.run(goal.strip(), provider)):
            kind = event.get("type")
            if kind == "reason":
                logger.info("model_step", extra={"fields": {
                    **fields, "turn": event.get("turn"), "latency_ms": event.get("latency_ms"),
                    "tool": (event.get("tool") or {}).get("name"), "usage": event.get("usage")}})
            elif kind == "tool_result":
                logger.info("tool_completed", extra={"fields": {
                    **fields, "turn": event.get("turn"), "tool": event.get("name"),
                    "ok": event.get("ok"), "latency_ms": event.get("latency_ms")}})
            elif kind == "error":
                outcome = "failed"
                logger.error("agent_error", extra={"fields": {**fields, "where": event.get("where")}})
            elif kind == "final":
                if outcome != "failed":
                    outcome = "success"
                logger.info("final_response", extra={"fields": {
                    **fields, "turn": event.get("turn"), "output_chars": len(event.get("text", ""))}})
            yield redact(event)
    except Exception as error:
        # Do not hand raw SDK exceptions or secret-bearing URLs to SDK logging.
        code = getattr(error, "response", {}).get("Error", {}).get("Code", type(error).__name__)
        outcome = "failed"
        logger.error("invocation_failed", extra={"fields": {**fields, "error_code": code}})
        yield {"type": "error", "where": "agentcore", "message": f"Agent startup or execution failed ({code}). Check runtime logs and permissions."}
    finally:
        logger.info("invocation_finished", extra={"fields": {
            **fields, "outcome": outcome, "elapsed_ms": round((time.perf_counter() - started) * 1000)}})


if __name__ == "__main__":
    app.run()
