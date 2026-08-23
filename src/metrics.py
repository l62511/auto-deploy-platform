from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

WEBHOOK_TOTAL = Counter("auto_deploy_webhook_total", "Webhook requests", ["result", "environment"])
DEPLOYMENT_TOTAL = Counter(
    "auto_deploy_deployment_total", "Deployment executions", ["status", "environment", "engine"]
)
DEPLOYMENT_DURATION = Histogram(
    "auto_deploy_deployment_duration_seconds", "Deployment duration", ["environment", "engine"]
)
ACTIVE_DEPLOYMENTS = Gauge(
    "auto_deploy_active_deployments", "Currently running deployments", ["environment"]
)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
