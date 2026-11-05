# Trip Agent on AgentCore Runtime + Amazon Bedrock

This standalone repository hosts a handwritten Python travel agent on
**Bedrock AgentCore Runtime**, with model calls through **Amazon Bedrock Converse**.
The reusable agent loop and travel tools are included in `backend/`.

```text
Browser on your laptop
  -> localhost:8001 (AWS-authenticated local bridge)
  -> AgentCore InvokeAgentRuntime (session ID)
  -> session-isolated execution environment
       -> backend/loop.py
            -> BedrockProvider.reason() -> Amazon Bedrock Nova 2 Lite
            -> backend/tools/* -> Geoapify, Open-Meteo, Tavily, AviationStack
```

This creates a **custom AgentCore Runtime**, visible in Bedrock AgentCore's
Runtimes console. It does not create a managed Bedrock Agents resource or a
Harness. The reasoning and tool loop is still your code, without Strands or
LangChain. The AgentCore SDK supplies `/invocations`, `/ping`, and SSE serving.

## Files

- `entrypoint.py`: `BedrockAgentCoreApp` entrypoint, validation, and streamed events.
- `bedrock_provider.py`: Converse message/tool conversion matching `reason(history, tools)`.
- `bootstrap.py`: retrieves only the three travel keys before importing backend configuration.
- `Dockerfile`: packages this wrapper with the shared backend; build from the repository root.
- `deploy.py`: plans, builds ARM64, pushes a separate ECR image, configures a scoped runtime role, and creates/updates the runtime.
- `invoke.py` / `client.py`: signed invocation, streamed events, and explicit session stop.
- `serve.py` / `ui.html`: local browser demo using the same runtime.
- `settings.example.json`: sample deployment settings with placeholder AWS identifiers.
- `settings.json`: your local, ignored account and deployment settings.
- `.state/`: local, ignored deployment metadata and generated IAM policy files.

## Setup

From the **repository root**, using Python 3.11 or 3.12:

```bash
python3.11 -m venv agentcore/.venv
agentcore/.venv/bin/python -m pip install -r agentcore/requirements.txt
aws login --region us-east-2
cp agentcore/settings.example.json agentcore/settings.json
```

Edit the ignored `agentcore/settings.json`: set `account_id` to your AWS account
and `secret_arn` to your own Secrets Manager secret ARN. The secret must contain
`GEOAPIFY_KEY`, `TAVILY_API_KEY`, and `AVIATIONSTACK_KEY`. Authenticate using your
normal AWS CLI profile or login; keep all key values outside Git.

Docker Desktop must be running for a container build. AgentCore's image is **linux/arm64**.

## Plan, review, and deploy

```bash
# Read-only AWS checks; writes reviewable IAM JSON locally.
agentcore/.venv/bin/python agentcore/deploy.py

# Build and create/update this folder's resources.
agentcore/.venv/bin/python agentcore/deploy.py --apply
```

Defaults in `settings.json`:

- Region: `us-east-2`; runtime name: `trip_agent_bedrock_demo`.
- ECR repository: `trip-agent-agentcore` (immutable image tags).
- IAM role: `trip-agent-agentcore-runtime-role`.
- Model: `us.amazon.nova-2-lite-v1:0`, a US cross-region inference profile.
- Tool credentials: your Secrets Manager secret (example name `trip-agent/demo`).
- Public outbound networking with **IAM-authenticated invocation**.
- Runtime platform V2; idle timeout 5 minutes; compute lifetime 30 minutes.

The deployer verifies the AWS account and model profile, generates policies in
`.state/iam/`, and refuses to overwrite colliding resources without its ownership
tag. Model IAM permissions cover the selected profile and its destination model
ARNs. The runtime may run in Ohio while the US inference profile routes model
calls to another supported US region. No OpenAI key is needed by this version.

The role can read your configured secret as an AWS resource; the wrapper copies only
`GEOAPIFY_KEY`, `TAVILY_API_KEY`, and `AVIATIONSTACK_KEY` into its environment.
No secret values are written into the image, deployment state, or runtime config.
Secrets are fetched at the first invocation in a new execution environment.
After updating the secret, use a fresh session or stop and restart session compute.

## Invoke and see a real agent

```bash
agentcore/.venv/bin/python agentcore/invoke.py \
  --goal 'Resolve Albany, New York with geocode_place, then give a short travel suggestion.' \
  --stop-after
```

The runtime emits hosting/model metadata followed by the existing `start`,
`reason`, `tool_result`, and `final` events. `--stop-after` releases that demo
session's compute when the invocation ends.

In AWS, open **Bedrock AgentCore -> Runtimes**, select
`trip_agent_bedrock_demo`, and inspect its status, endpoint, and CloudWatch logs.
The ARN is printed on deployment and saved in `.state/deployment.json`.

## Browser demo

```bash
agentcore/.venv/bin/python agentcore/serve.py
# Open http://127.0.0.1:8001
```

The bridge binds to loopback and uses your AWS login on the server side. AWS
credentials stay out of browser JavaScript. The UI shows arriving events
immediately; it does not replay a buffered offline sample.

Open `http://127.0.0.1:8001/code-flow` for the interactive code walkthrough, or
open `agentcore/code-flow.html` directly as a standalone file. It explains each
request step, the shared agent loop, sessions, deployment, logs, and traces
without making AWS calls.

Reuse a session ID to see the same process marker while its environment remains
alive; click **New session** for a separate environment. The marker is generated
by the application after initialization, not read as an AWS microVM identifier.
This makes it suitable for runtime V2 snapshot restoration as well.

**Session isolation is not conversation memory.** Every call still starts the
original loop with a fresh transcript. This wrapper adds no AgentCore Memory,
Gateway, durable history, or email notifications. Those can be added separately.

## Local runtime contract test

```bash
agentcore/.venv/bin/python agentcore/entrypoint.py
# The SDK serves /ping and /invocations on localhost:8080.
```

For local tool tests with no `TRIP_SECRET_ARN`, backend configuration can use the
existing development `.env`. Set `TRIP_SECRET_ARN` to test AWS secret retrieval.
The container disables `.env` loading and has no `.env` file.

## Where logs appear

- `python agentcore/entrypoint.py`: the agent runs locally; its logs appear in that terminal.
- `python agentcore/serve.py`: bridge/access logs appear in this terminal; agent execution is remote and its logs appear in CloudWatch.
- `python agentcore/invoke.py`: streamed response events appear in your terminal; execution logs are still in CloudWatch.
- Testing the deployed runtime from the AWS console: execution logs go to the same CloudWatch group.

In **CloudWatch -> Logs -> Log groups**, select **us-east-2** and open:

```text
/aws/bedrock-agentcore/runtimes/<runtime_id>-DEFAULT
```

The runtime ID is saved in `.state/deployment.json`. Open a recent
`runtime-logs-<session-id>` stream, expand the time range to the last hour, and
refresh. `storedBytes` may lag; it is not a reliable indicator of whether recent
events exist. Use log streams and their events to check.

Our JSON records include `invocation_started`, `runtime_initialized`,
`model_step`, `tool_completed`, `final_response`, and `invocation_finished`.
Filter by `session_id` to follow a request. Logs contain timing, token counts,
tool names, and success flags, rather than prompt bodies or API credentials.

## Traces

The container now starts with `opentelemetry-instrument` and AWS OpenTelemetry
0.22.0. Custom spans describe `invoke_agent`, each Bedrock `chat`, and each
`execute_tool`. Spans include session IDs, timing, model name, token counts,
and tool success. They exclude prompt bodies, model output, tool payloads,
tool request URLs, and raw exception messages. HTTP/botocore auto-instrumentation is disabled
to avoid collecting travel API keys from query strings.

`deploy.py` grants the runtime role X-Ray export and scoped log-resource-policy
permissions and sets `UNIFIED_TRACES_DESTINATION_ENABLED=true`. Updates also set
explicit OTLP trace headers and an X-Ray resource policy scoped to this runtime
log group, so routing works even when a Region retains the shared default.
AgentCore injects the export endpoints and resource metadata. The account/Region must have
CloudWatch Transaction Search enabled before verifying traces. The deployment preserves existing regional indexing rules.

In CloudWatch (us-east-2), open **GenAI Observability -> Agents**, select
`trip_agent_bedrock_demo`, choose the last hour, and inspect sessions/traces.
The local UI shows the latest trace ID for each Plan, and JSON application logs
include `trace_id`. You can also inspect the `spans` stream in the runtime log
group shown above. `otel-rt-logs` contains OTEL log records, while
`runtime-logs-<session-id>` contains process output.

After deployment, refresh the UI and select **New session**: an existing session
can continue using an older runtime version. Only new invocations produce these
new spans. Allow a few minutes for the CloudWatch dashboard to refresh.

A plain local `python agentcore/entrypoint.py` still writes terminal logs but
does not configure an AWS trace exporter. Using the local bridge or CLI against
the deployed runtime produces AWS traces. The Bedrock model playground invokes
only a model and does not execute this AgentCore agent.

AWS reference: [AgentCore observability setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html).

To verify model/tool traces with one real request and stop its session afterward:

```bash
agentcore/.venv/bin/python agentcore/verify_traces.py
```

The command waits for CloudWatch delivery and saves the verified trace ID in
`.state/tracing-verification.json`.

## Verification

```bash
agentcore/.venv/bin/python -m pip install -r agentcore/requirements-dev.txt
agentcore/.venv/bin/python -m unittest discover -s agentcore/tests -v
```

Offline tests exercise Converse tool-use/result IDs, SDK streaming and health
endpoints, secret redaction, input validation, and SSE parsing across chunks.
Live deployment verification must additionally check `READY`, invoke the actual
Bedrock model, and see a successful real tool result and a final event.

With the browser bridge running, this command performs those live checks, then
compares two requests in one session against a request in a new session:

```bash
agentcore/.venv/bin/python agentcore/verify_live.py
```

It makes real, billable model/tool calls, saves a non-secret verification summary
in `.state/verification.json`, and stops both probe sessions afterward.

## Costs and cleanup

AgentCore runtime sessions, Bedrock inference, ECR storage, and CloudWatch logs
have their own charges. Any other AWS deployments have independent costs.
Use `--stop-after` for CLI experiments or **Stop session compute** in the UI;
otherwise the configured idle timeout releases idle session compute.

For a complete teardown, stop your demo sessions and delete only this runtime,
its dedicated ECR repository, its runtime log group, and its dedicated IAM role
after checking dependencies. Keep the Secrets Manager secret if other applications still use it. Reusing this folder's deployment after teardown recreates its resources.

## AWS references

- [AgentCore HTTP contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html)
- [AgentCore execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html)
- [Session isolation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html)
- [Runtime platform versions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-how-it-works.html)
- [AWS AgentCore Python SDK](https://github.com/aws/bedrock-agentcore-sdk-python)
