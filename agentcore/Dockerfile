FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1
WORKDIR /app
COPY agentcore/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend/ /app/backend/
COPY agentcore/entrypoint.py agentcore/bootstrap.py agentcore/bedrock_provider.py agentcore/runtime_logging.py agentcore/telemetry.py /app/agentcore/
WORKDIR /app/agentcore
EXPOSE 8080
CMD ["opentelemetry-instrument", "python", "entrypoint.py"]
