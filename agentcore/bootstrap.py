"""Load only travel-tool credentials before importing the shared backend."""
import importlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import uuid

import boto3
from botocore.config import Config

TOOL_KEYS = ("GEOAPIFY_KEY", "TAVILY_API_KEY", "AVIATIONSTACK_KEY")
_lock = threading.Lock()
_runtime = None


def prepare():
    """Initialize once inside each session's process, on its first invocation."""
    global _runtime
    with _lock:
        if _runtime is not None:
            return _runtime
        region = os.getenv("TRIP_AWS_REGION", "us-east-2")
        secret_arn = os.getenv("TRIP_SECRET_ARN")
        if secret_arn:
            client = boto3.client("secretsmanager", region_name=region,
                                  config=Config(connect_timeout=5, read_timeout=15,
                                                retries={"mode": "standard", "max_attempts": 2}))
            secret = json.loads(client.get_secret_value(SecretId=secret_arn)["SecretString"])
            for name in TOOL_KEYS:
                value = secret.get(name)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"Required secret field is missing: {name}")
                os.environ[name] = value
        # config.py captures environment variables when it is first imported.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        loop = importlib.import_module("loop")
        provider_module = importlib.import_module("bedrock_provider")
        _runtime = (loop, provider_module.BedrockProvider(), str(uuid.uuid4()))
        return _runtime


def redact(value):
    """Shared HTTP errors can include API keys in URLs; scrub before streaming."""
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    for name in TOOL_KEYS:
        secret = os.getenv(name, "")
        if len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)((?:apiKey|access_key|api_key)=)[^&\s\"']+",
                  r"\1[REDACTED]", value)
