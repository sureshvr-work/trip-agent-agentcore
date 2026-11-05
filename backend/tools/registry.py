"""
registry.py — the tool contract + a name->function registry.

A tool = a function + a JSON-schema spec. The @tool decorator registers both.
`call()` validates the LLM's arguments against the schema BEFORE running the
function — because a model will, sooner or later, hand you malformed args, and
catching that here turns a crash into a clean error the model can recover from.
"""
from jsonschema import validate, ValidationError

TOOLS: dict[str, dict] = {}     # name -> {"fn":..., "spec":...}


def tool(name: str, description: str, schema: dict):
    def decorate(fn):
        TOOLS[name] = {"fn": fn,
                       "spec": {"name": name, "description": description,
                                "parameters": schema}}
        return fn
    return decorate


def specs() -> list[dict]:
    """Every tool's spec — handed to the LLM so it knows its options."""
    return [t["spec"] for t in TOOLS.values()]


def call(name: str, args: dict):
    """Validate args, then run the tool. Returns the tool's data, or {'error':…}."""
    if name not in TOOLS:
        return {"error": f"unknown tool: {name}"}
    try:
        validate(args, TOOLS[name]["spec"]["parameters"])
    except ValidationError as e:
        return {"error": f"invalid arguments for {name}: {e.message}"}
    return TOOLS[name]["fn"](**args)
