"""Exercise the local bridge, Bedrock/tool calls, and session reuse; stop probes."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import requests

from client import new_session_id, stop_session

BASE = "http://127.0.0.1:8001"


def collect(goal, session_id):
    started = time.monotonic()
    events = []
    with requests.post(BASE + "/run", json={"goal": goal, "session_id": session_id},
                       stream=True, timeout=(10, 180)) as response:
        response.raise_for_status()
        for line in response.iter_lines(chunk_size=1):
            if line.startswith(b"data:"):
                events.append(json.loads(line[5:]))
    if any(event.get("type") == "error" for event in events):
        raise RuntimeError("Live agent returned an error; inspect runtime logs.")
    if not any(event.get("type") == "final" for event in events):
        raise RuntimeError("No final event received.")
    metadata = next(event for event in events if event.get("type") == "runtime")
    summary = {"session_id": session_id, "instance_id": metadata["instance_id"],
               "model_id": metadata["model_id"], "duration_seconds": round(time.monotonic() - started, 2),
               "events": [event["type"] for event in events],
               "tools": [{"name": event["name"], "ok": event["ok"]}
                         for event in events if event.get("type") == "tool_result"]}
    print(json.dumps(summary), flush=True)
    return summary


def main():
    first, second = new_session_id(), new_session_id()
    try:
        a = collect("Deployment diagnostic: call geocode_place exactly once for Albany, New York, USA. Then reply with its coordinates and stop. Do not call other tools.", first)
        if not any(tool["name"] == "geocode_place" and tool["ok"] for tool in a["tools"]):
            raise RuntimeError("Real geocoding was not verified.")
        probe = "Session diagnostic only: reply with the short sentence 'Session probe complete.' Do not call any tools."
        a2 = collect(probe, first)
        b = collect(probe, second)
        if a["instance_id"] != a2["instance_id"]:
            raise RuntimeError("Same session did not preserve the execution-process marker.")
        if a["instance_id"] == b["instance_id"]:
            raise RuntimeError("Different sessions returned the same execution-process marker.")
        result = {"verified_at": datetime.now(timezone.utc).isoformat(),
                  "bridge": "passed", "bedrock_and_geocoding": "passed",
                  "same_session_process_reused": True, "different_session_process": True,
                  "invocations": [a, a2, b]}
        folder = Path(__file__).resolve().parent / ".state"
        folder.mkdir(exist_ok=True)
        (folder / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
        print("Live bridge, Bedrock, geocoding, and session checks passed.", flush=True)
    finally:
        for session_id in (first, second):
            stop_session(session_id)
            print("Stopped probe session:", session_id, flush=True)


if __name__ == "__main__":
    main()
