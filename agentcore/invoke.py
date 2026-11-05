"""Invoke the deployed Bedrock agent and optionally stop its session afterward."""
import argparse
import json

from client import invoke_events, new_session_id, stop_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal", default="Resolve Albany, New York, USA with geocode_place, then provide a short weather-aware travel suggestion.")
    parser.add_argument("--session-id", default=None, help="Reuse a UUID to invoke the same runtime session")
    parser.add_argument("--stop-after", action="store_true", help="Release the demo session's compute after invocation")
    args = parser.parse_args()
    session_id = args.session_id or new_session_id()
    print("Session:", session_id, flush=True)
    completed = False
    try:
        for event in invoke_events(args.goal, session_id):
            print(json.dumps(event, ensure_ascii=False), flush=True)
            if event.get("type") == "error":
                raise RuntimeError("Agent returned an error; see the event above.")
            if event.get("type") == "final":
                completed = True
        if not completed:
            raise RuntimeError("Stream ended without a final response.")
    finally:
        if args.stop_after:
            stop_session(session_id)
            print("Session compute stopped.", flush=True)


if __name__ == "__main__":
    main()
