"""Invoke a real model/tool request, verify its CloudWatch spans, then stop it."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import boto3

from client import deployment, invoke_events, new_session_id, stop_session


def main():
    state = deployment()
    session_id = new_session_id()
    started = int(time.time() * 1000)
    try:
        events = list(invoke_events(
            "Tracing diagnostic: call geocode_place exactly once for Albany, New York, USA. "
            "Then reply with its coordinates and stop. Do not call other tools.", session_id))
        if any(e.get("type") == "error" for e in events):
            raise RuntimeError("Agent returned an error; inspect its runtime logs.")
        assert any(e.get("type") == "final" for e in events), "Final response is missing."
        assert any(e.get("type") == "tool_result" and e.get("ok") for e in events), "Tool did not succeed."
        metadata = next(e for e in events if e.get("type") == "runtime")
        trace_id = metadata.get("trace_id")
        assert trace_id, "Runtime returned no trace ID; confirm the latest version is READY."
        print(json.dumps({"session_id": session_id, "trace_id": trace_id}), flush=True)
    finally:
        stop_session(session_id)
        print("Stopped diagnostic session.", flush=True)

    logs = boto3.client("logs", region_name=state["region"])
    group = "/aws/bedrock-agentcore/runtimes/" + state["runtime_id"] + "-DEFAULT"
    required = {"invoke_agent", "chat", "execute_tool"}
    for attempt in range(24):
        records = []
        try:
            for page in logs.get_paginator("filter_log_events").paginate(
                logGroupName=group, logStreamNames=["spans"], startTime=started - 60000):
                for event in page.get("events", []):
                    if trace_id in event["message"]:
                        records.append(json.loads(event["message"]))
        except logs.exceptions.ResourceNotFoundException:
            pass
        # CloudWatch stores OTLP spans as JSON with GenAI attributes.
        serialized = json.dumps(records)
        if records and all('"' + operation + '"' in serialized for operation in required):
            result = {"verified_at": datetime.now(timezone.utc).isoformat(),
                      "session_id": session_id, "trace_id": trace_id,
                      "xray_trace_id": "1-" + trace_id[:8] + "-" + trace_id[8:],
                      "log_group": group, "log_stream": "spans", "span_count": len(records),
                      "operations": sorted(required)}
            folder = Path(__file__).resolve().parent / ".state"
            (folder / "tracing-verification.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2), flush=True)
            return
        print(f"Waiting for CloudWatch spans ({attempt + 1}/24)...", flush=True)
        time.sleep(10)
    raise RuntimeError("Expected spans were not delivered within four minutes; inspect runtime exporter logs.")


if __name__ == "__main__":
    main()
