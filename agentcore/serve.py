"""Local-only browser bridge: browser -> signed AgentCore API -> Bedrock agent."""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from client import deployment, invoke_events, stop_session
from runtime_logging import configure_logger

app = FastAPI(title="Trip Agent · AgentCore + Bedrock")
logger = configure_logger("trip_agent.bridge")


class RunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=10000)
    session_id: str = Field(min_length=33, max_length=256)


class SessionRequest(BaseModel):
    session_id: str = Field(min_length=33, max_length=256)


def same_origin(request):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Use the local demo page to invoke this bridge.")


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).resolve().parent / "ui.html").read_text()


@app.get("/config")
def config():
    try:
        state = deployment()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    return {key: state[key] for key in ("runtime_arn", "region", "model_id")}


@app.get("/code-flow", response_class=HTMLResponse)
def code_flow():
    return (Path(__file__).resolve().parent / "code-flow.html").read_text()


@app.post("/run")
def run(body: RunRequest, request: Request):
    same_origin(request)

    def events():
        fields = {"session_id": body.session_id}
        logger.info("bridge_invocation_started", extra={"fields": fields})
        try:
            for event in invoke_events(body.goal, body.session_id):
                logger.info("runtime_event_received", extra={"fields": {
                    **fields, "event_type": event.get("type"), "tool": event.get("name"),
                    "ok": event.get("ok")}})
                yield "data: " + json.dumps(event) + "\n\n"
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code", type(error).__name__)
            logger.error("bridge_invocation_failed", extra={"fields": {**fields, "error_code": code}})
            yield "data: " + json.dumps({"type": "error", "where": "bridge",
                    "message": f"Invocation failed ({code}). Check AWS login and runtime status."}) + "\n\n"
        finally:
            logger.info("bridge_invocation_finished", extra={"fields": fields})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/session/stop")
def stop(body: SessionRequest, request: Request):
    same_origin(request)
    try:
        stop_session(body.session_id)
    except Exception as error:
        code = getattr(error, "response", {}).get("Error", {}).get("Code", type(error).__name__)
        raise HTTPException(status_code=502, detail=f"Stop failed ({code}).") from None
    return {"status": "stopped", "session_id": body.session_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
