"""Signed AgentCore invocation shared by the CLI and local browser bridge."""
import codecs
import json
from pathlib import Path
import uuid

import boto3
from botocore.config import Config

STATE = Path(__file__).resolve().parent / ".state" / "deployment.json"


def deployment():
    if not STATE.exists():
        raise RuntimeError("Deploy first: python agentcore/deploy.py --apply")
    return json.loads(STATE.read_text())


def new_session_id():
    return str(uuid.uuid4())


def runtime_client(region):
    return boto3.client("bedrock-agentcore", region_name=region,
                        config=Config(connect_timeout=10, read_timeout=180,
                                      retries={"mode": "standard", "max_attempts": 4}))


def invoke_events(goal, session_id):
    if not 33 <= len(session_id) <= 256:
        raise ValueError("Session IDs must have 33–256 characters; a UUID works.")
    state = deployment()
    response = runtime_client(state["region"]).invoke_agent_runtime(
        agentRuntimeArn=state["runtime_arn"], runtimeSessionId=session_id,
        qualifier="DEFAULT", contentType="application/json", accept="text/event-stream",
        payload=json.dumps({"goal": goal}).encode())
    stream = response["response"]
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""

    def frames():
        nonlocal buffer
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            lines = [line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:")]
            if lines:
                yield json.loads("\n".join(lines))

    try:
        if "text/event-stream" not in response.get("contentType", ""):
            raise RuntimeError("Runtime returned an unexpected content type.")
        # Small reads keep early SSE events visible instead of waiting for 1 KiB.
        for chunk in stream.iter_chunks(chunk_size=1):
            buffer = (buffer + decoder.decode(chunk)).replace("\r\n", "\n")
            yield from frames()
        buffer += decoder.decode(b"", final=True)
        if buffer.strip():
            buffer += "\n\n"
        yield from frames()
    finally:
        stream.close()


def stop_session(session_id):
    state = deployment()
    return runtime_client(state["region"]).stop_runtime_session(
        agentRuntimeArn=state["runtime_arn"], runtimeSessionId=session_id, qualifier="DEFAULT")
