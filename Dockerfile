FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY src /app/src
COPY examples /app/examples

RUN mkdir -p /data && useradd --create-home --uid 10001 swarm && chown -R swarm:swarm /data /app
USER swarm

EXPOSE 8787
VOLUME ["/data"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=2)); assert d['status']=='HEALTHY'"

CMD ["python", "/app/src/swarm_api.py", "/app/examples/swarm-service.json"]
