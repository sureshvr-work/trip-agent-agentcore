"""Plan or deploy only this folder's AgentCore resources; ECS is independent."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

import boto3
from botocore.exceptions import ClientError

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE = HERE / ".state" / "deployment.json"
OWNER = "trip-agent-agentcore"


def settings():
    path = HERE / "settings.json"
    if not path.is_file():
        raise SystemExit("Copy agentcore/settings.example.json to agentcore/settings.json "
                         "and set your AWS account ID and Secrets Manager ARN first.")
    config = json.loads(path.read_text())
    if config.get("account_id") == "123456789012" or "EXAMPLE" in config.get("secret_arn", ""):
        raise SystemExit("Replace the example AWS account ID and secret ARN in "
                         "agentcore/settings.json before deploying.")
    return config


def policies(config, profile):
    account, region = config["account_id"], config["region"]
    runtime, repository = config["runtime_name"], config["repository"]
    log_prefix = f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/{runtime}-*"
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole", "Condition": {
            "StringEquals": {"aws:SourceAccount": account},
            "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{runtime}-*"}}}]}
    permissions = {"Version": "2012-10-17", "Statement": [
        {"Sid": "PullAgentImage", "Effect": "Allow",
         "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
         "Resource": f"arn:aws:ecr:{region}:{account}:repository/{repository}"},
        {"Sid": "RegistryAuthentication", "Effect": "Allow",
         "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Sid": "InvokeSelectedBedrockModel", "Effect": "Allow", "Action": "bedrock:InvokeModel",
         "Resource": [profile["inferenceProfileArn"], *[m["modelArn"] for m in profile["models"]]]},
        {"Sid": "ReadTravelToolKeys", "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
         "Resource": config["secret_arn"]},
        {"Sid": "CreateRuntimeLogs", "Effect": "Allow",
         "Action": ["logs:CreateLogGroup", "logs:DescribeLogStreams"], "Resource": log_prefix},
        {"Sid": "WriteRuntimeLogs", "Effect": "Allow",
         "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": log_prefix + ":log-stream:*"},
        {"Sid": "DescribeLogs", "Effect": "Allow", "Action": "logs:DescribeLogGroups", "Resource": "*"},
        {"Sid": "UnifiedTraceDelivery", "Effect": "Allow",
          "Action": "logs:PutResourcePolicy", "Resource": log_prefix},
        {"Sid": "ExportTraces", "Effect": "Allow",
         "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules", "xray:GetSamplingTargets"], "Resource": "*"},
        {"Sid": "AgentMetrics", "Effect": "Allow", "Action": "cloudwatch:PutMetricData",
         "Resource": "*", "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}}
    ]}
    return trust, permissions


def plan(session, config):
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != config["account_id"]:
        raise RuntimeError("AWS account does not match settings.json; no resources changed.")
    profile = session.client("bedrock").get_inference_profile(inferenceProfileIdentifier=config["model_id"])
    if profile["status"] != "ACTIVE":
        raise RuntimeError("Selected Bedrock inference profile is not active.")
    trust, permissions = policies(config, profile)
    folder = HERE / ".state" / "iam"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "trust-policy.json").write_text(json.dumps(trust, indent=2) + "\n")
    (folder / "permissions.json").write_text(json.dumps(permissions, indent=2) + "\n")
    print(json.dumps({"account": identity["Account"], "region": config["region"],
                      "runtime": config["runtime_name"], "repository": config["repository"],
                      "execution_role": config["role_name"], "model": config["model_id"],
                      "secret_reference": config["secret_arn"],
                      "network": "PUBLIC outbound; invocation requires AWS IAM",
                      "policy_files": str(folder)}, indent=2), flush=True)
    return trust, permissions


def ensure_repository(client, config):
    try:
        repository = client.describe_repositories(repositoryNames=[config["repository"]])["repositories"][0]
        tags = client.list_tags_for_resource(resourceArn=repository["repositoryArn"])["tags"]
        if not any(t["Key"] == "project" and t["Value"] == OWNER for t in tags):
            raise RuntimeError("Existing ECR name is not owned by this deployment; choose another repository.")
    except client.exceptions.RepositoryNotFoundException:
        repository = client.create_repository(
            repositoryName=config["repository"], imageTagMutability="IMMUTABLE",
            imageScanningConfiguration={"scanOnPush": False},
            tags=[{"Key": "project", "Value": OWNER}])["repository"]
    return repository["repositoryUri"]


def ensure_role(client, config, trust, permissions):
    name = config["role_name"]
    created = False
    try:
        role = client.get_role(RoleName=name)["Role"]
        tags = client.list_role_tags(RoleName=name)["Tags"]
        if not any(t["Key"] == "project" and t["Value"] == OWNER for t in tags):
            raise RuntimeError("Existing IAM role is not owned by this deployment; choose another role name.")
        client.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
    except client.exceptions.NoSuchEntityException:
        role = client.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                                  Description="Trip Agent Bedrock AgentCore runtime only",
                                  Tags=[{"Key": "project", "Value": OWNER}])["Role"]
        created = True
    client.put_role_policy(RoleName=name, PolicyName="TripAgentRuntimeAccess",
                           PolicyDocument=json.dumps(permissions))
    if created:
        print("Waiting for the new execution role to propagate...", flush=True)
        time.sleep(15)
    return role["Arn"]


def build_image(client, repository):
    tag = "agentcore-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    image_uri = repository + ":" + tag
    auth = client.get_authorization_token()["authorizationData"][0]
    username, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    # The registry password is supplied only through stdin, never argv or files here.
    subprocess.run(["docker", "login", "--username", username, "--password-stdin",
                    auth["proxyEndpoint"]], input=password, text=True, check=True)
    subprocess.run(["docker", "buildx", "build", "--platform", "linux/arm64",
                    "--file", "agentcore/Dockerfile", "--tag", image_uri,
                    "--push", "."], cwd=ROOT, check=True)
    return image_uri


def ensure_trace_delivery(client, config, runtime_id):
    """Allow X-Ray to write only this runtime's unified trace destination."""
    group = f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT"
    arn = f"arn:aws:logs:{config['region']}:{config['account_id']}:log-group:{group}"
    existing = client.describe_resource_policies(resourceArn=arn, policyScope="RESOURCE")["resourcePolicies"]
    current = existing[0] if existing else None
    policy = json.loads(current["policyDocument"]) if current else {"Version": "2012-10-17", "Statement": []}
    statement = {"Sid": "TripAgentXRayTraceDelivery", "Effect": "Allow",
                 "Principal": {"Service": "xray.amazonaws.com"},
                 "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                 "Resource": arn + ":*", "Condition": {
                     "StringEquals": {"aws:SourceAccount": config["account_id"]},
                     "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{config['region']}:{config['account_id']}:*"}}}
    policy["Statement"] = [s for s in policy["Statement"] if s.get("Sid") != statement["Sid"]] + [statement]
    request = {"policyDocument": json.dumps(policy), "resourceArn": arn}
    if current and current.get("revisionId"):
        request["expectedRevisionId"] = current["revisionId"]
    client.put_resource_policy(**request)
    return f"x-aws-log-group={group},x-aws-log-stream=spans"


def deploy(session, config, trust, permissions, skip_build):
    ecr = session.client("ecr")
    repository = ensure_repository(ecr, config)
    if skip_build:
        if not STATE.exists():
            raise RuntimeError("--skip-build requires an existing deployment.json state file.")
        image_uri = json.loads(STATE.read_text())["image_uri"]
    else:
        image_uri = build_image(ecr, repository)
    role_arn = ensure_role(session.client("iam"), config, trust, permissions)
    client = session.client("bedrock-agentcore-control")
    matches = []
    for page in client.get_paginator("list_agent_runtimes").paginate():
        matches.extend(r for r in page.get("agentRuntimes", []) if r["agentRuntimeName"] == config["runtime_name"])
    request = {
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": image_uri}},
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": config["idle_timeout_seconds"],
                                   "maxLifetime": config["max_session_seconds"]},
        "environmentVariables": {"TRIP_AWS_REGION": config["region"],
                                 "TRIP_MODEL_ID": config["model_id"],
                                 "TRIP_SECRET_ARN": config["secret_arn"],
                                 "TRIP_AGENT_NAME": config["runtime_name"],
                                 "AGENT_OBSERVABILITY_ENABLED": "true",
                                 "UNIFIED_TRACES_DESTINATION_ENABLED": "true",
                                 "OTEL_TRACES_SAMPLER": "always_on",
                                 "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
                                 "AWS_GENAI_INSTRUMENTATION": "disabled",
                                 # Explicit spans avoid exporting secret-bearing tool URLs.
                                 "OTEL_PYTHON_DISABLED_INSTRUMENTATIONS": "requests,urllib,urllib3,botocore,httpx,system_metrics",
                                 "MAX_TURNS": "10", "HTTP_TIMEOUT": "12", "HTTP_RETRIES": "2"},
        "description": "Managed by trip-agent-realtime/agentcore; custom Python loop with Amazon Bedrock",
        "platformVersion": "V2"
    }
    if matches:
        existing = matches[0]
        tags = client.list_tags_for_resource(resourceArn=existing["agentRuntimeArn"])["tags"]
        if tags.get("project") != OWNER:
            raise RuntimeError("Existing AgentCore runtime is not owned by this deployment.")
        runtime_id = existing["agentRuntimeId"]
        # Explicit routing also works in Regions retaining the shared default.
        request["environmentVariables"]["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] = ensure_trace_delivery(
            session.client("logs"), config, runtime_id)
        result = client.update_agent_runtime(agentRuntimeId=runtime_id, **request)
    else:
        result = client.create_agent_runtime(agentRuntimeName=config["runtime_name"],
                                             tags={"project": OWNER}, **request)
        runtime_id = result["agentRuntimeId"]
    state = {"runtime_id": runtime_id, "runtime_arn": result["agentRuntimeArn"],
             "region": config["region"], "model_id": config["model_id"],
             "image_uri": image_uri, "role_arn": role_arn,
             "saved_at": datetime.now(timezone.utc).isoformat()}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + "\n")
    print("Deployment state saved:", STATE, flush=True)
    print("Runtime ARN:", state["runtime_arn"], flush=True)
    # Bounded wait; each iteration reports progress so long provisioning is visible.
    for _ in range(90):
        runtime = client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = runtime["status"]
        print("Runtime status:", status, flush=True)
        if status == "READY":
            print("Ready. Invoke with: python agentcore/invoke.py --goal 'Plan a trip to Albany, New York.'")
            return
        if status.endswith("FAILED"):
            raise RuntimeError("Runtime deployment failed: " + runtime.get("failureReason", status))
        time.sleep(10)
    raise RuntimeError("Runtime is still provisioning; deployment state is saved. Check the AgentCore console.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create/update this AgentCore deployment")
    parser.add_argument("--skip-build", action="store_true", help="Reuse the image from saved deployment state")
    args = parser.parse_args()
    config = settings()
    session = boto3.Session(region_name=config["region"])
    try:
        trust, permissions = plan(session, config)
        if args.apply:
            deploy(session, config, trust, permissions, args.skip_build)
        else:
            print("Plan only. Run with --apply to build and deploy.")
    except (ClientError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))


if __name__ == "__main__":
    main()
