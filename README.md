# Trip Agent on Amazon Bedrock AgentCore

A standalone travel-planning agent with a handwritten tool loop, Amazon Bedrock
Converse, AgentCore Runtime hosting, a local streaming browser UI, and
CloudWatch logging and tracing.

- `agentcore/`: runtime, deployment tooling, browser bridge, and offline tests.
- `backend/`: shared agent loop, decision interfaces, and travel tools.

## Quick start

Use Python 3.11 or 3.12, AWS CLI credentials for your own account, and Docker
Desktop when building the ARM64 runtime container.

```bash
python3.11 -m venv agentcore/.venv
agentcore/.venv/bin/python -m pip install -r agentcore/requirements-dev.txt
cp agentcore/settings.example.json agentcore/settings.json
```

Edit `agentcore/settings.json` with your own AWS account ID and Secrets Manager
secret ARN. Store `GEOAPIFY_KEY`, `TAVILY_API_KEY`, and `AVIATIONSTACK_KEY` in that
secret. For local-only development, copy `.env.example` to `.env` and supply your
own travel API keys there.

```bash
# Offline tests: no AWS deployment or model calls.
agentcore/.venv/bin/python -m unittest discover -s agentcore/tests -v

# Review the deployment plan with your AWS credentials.
agentcore/.venv/bin/python agentcore/deploy.py

# Create/update AWS resources and push the ARM64 image; incurs AWS costs.
agentcore/.venv/bin/python agentcore/deploy.py --apply

# Start the local bridge; open http://127.0.0.1:8001.
agentcore/.venv/bin/python agentcore/serve.py
```

See [the complete guide](agentcore/README.md) for invocation, sessions,
observability, verification, and cleanup, or open
[the code walkthrough](agentcore/code-flow.html) in a browser.

## Repository hygiene

This repository starts from a clean source snapshot. Local environment files,
AWS credentials, account-specific settings, deployment state, logs, virtual
environments, and source-repository Git history are excluded. The committed
settings template uses example AWS identifiers and contains no credential values.
Keep real settings in ignored `agentcore/settings.json`, use Secrets Manager for
production travel keys, and review staged files before future commits.
