"""Amazon Bedrock Converse adapter for the existing reason(history, tools) seam."""
import json
import os

import boto3
from botocore.config import Config

from engine import Decision, ToolCall
from bootstrap import redact
from opentelemetry.trace import SpanKind
from telemetry import safe_span


class BedrockProvider:
    name = "bedrock"

    def __init__(self, client=None, model_id=None):
        self.model_id = model_id or os.getenv("TRIP_MODEL_ID", "us.amazon.nova-2-lite-v1:0")
        self.client = client or boto3.client(
            "bedrock-runtime", region_name=os.getenv("TRIP_AWS_REGION", "us-east-2"),
            config=Config(connect_timeout=5, read_timeout=120,
                          retries={"mode": "standard", "max_attempts": 2}))

    @staticmethod
    def messages(history):
        """Translate the neutral transcript, preserving tool-use/result IDs."""
        system, messages = [], []
        for entry in history:
            role = entry["role"]
            if role == "system":
                system.append({"text": entry["content"]})
                continue
            if role == "user":
                message = {"role": "user", "content": [{"text": entry["content"]}]}
            elif role == "assistant":
                content = []
                if entry.get("thought"):
                    content.append({"text": entry["thought"]})
                if entry.get("tool"):
                    tool = entry["tool"]
                    content.append({"toolUse": {"toolUseId": tool["id"],
                                               "name": tool["name"], "input": tool["args"]}})
                message = {"role": "assistant", "content": content or [{"text": "Continue."}]}
            elif role == "tool":
                safe_content = redact(entry["content"])
                try:
                    result = json.loads(safe_content)
                    content = [{"json": result}]
                except json.JSONDecodeError:
                    result = None
                    content = [{"text": safe_content}]
                message = {"role": "user", "content": [{"toolResult": {
                    "toolUseId": entry["id"], "content": content,
                    "status": "error" if isinstance(result, dict) and "error" in result else "success"}}]}
            else:
                raise ValueError(f"Unsupported history role: {role}")
            # Converse accepts adjacent blocks of the same role as one message.
            if messages and messages[-1]["role"] == message["role"]:
                messages[-1]["content"].extend(message["content"])
            else:
                messages.append(message)
        return system, messages

    def reason(self, history, tools):
        system, messages = self.messages(history)
        with safe_span("chat " + self.model_id, {
            "gen_ai.operation.name": "chat", "gen_ai.provider.name": "aws.bedrock",
            "gen_ai.system": "aws.bedrock", "gen_ai.request.model": self.model_id,
            "server.address": "bedrock-runtime." + os.getenv("TRIP_AWS_REGION", "us-east-2") + ".amazonaws.com"
        }, kind=SpanKind.CLIENT) as span:
            response = self.client.converse(
                modelId=self.model_id, system=system, messages=messages,
                inferenceConfig={"maxTokens": 2048, "temperature": 0.2},
                toolConfig={"tools": [{"toolSpec": {"name": tool["name"],
                             "description": tool["description"],
                             "inputSchema": {"json": tool["parameters"]}}} for tool in tools]})
            tokens = response.get("usage", {})
            span.set_attribute("gen_ai.usage.input_tokens", tokens.get("inputTokens", 0))
            span.set_attribute("gen_ai.usage.output_tokens", tokens.get("outputTokens", 0))
        content = response["output"]["message"]["content"]
        text = "\n".join(block["text"] for block in content if "text" in block)
        tokens = response.get("usage", {})
        usage = {"prompt": tokens.get("inputTokens", 0), "completion": tokens.get("outputTokens", 0)}
        for block in content:
            if "toolUse" in block:
                tool = block["toolUse"]
                return Decision(thought=text, tool_call=ToolCall(
                    tool["toolUseId"], tool["name"], tool["input"]), usage=usage)
        return Decision(thought=text, final=text, usage=usage)
