from __future__ import annotations

import hmac
import logging
import os
import re
import subprocess
import sys
import threading
import time

from flask import Flask, Response, jsonify, request

from .auth import authenticate, issue_local_token
from .config import PROJECT_ROOT, ConfigError, load_environment, validate_version
from .control_plane import ControlPlaneStore, Lease
from .logger import setup_logging
from .metrics import (
    ACTIVE_DEPLOYMENTS,
    DEPLOYMENT_DURATION,
    DEPLOYMENT_TOTAL,
    WEBHOOK_TOTAL,
    metrics_payload,
)


def create_app(
    logger: logging.Logger | None = None, store: ControlPlaneStore | None = None
) -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    log = logger or setup_logging()
    if os.getenv("PLATFORM_REQUIRE_DURABLE", "0") == "1":
        if not os.getenv("PLATFORM_DATABASE_URL", "").strip():
            raise RuntimeError("PLATFORM_DATABASE_URL is required in durable mode")
        if not os.getenv("PLATFORM_REDIS_URL", "").strip():
            raise RuntimeError("PLATFORM_REDIS_URL is required in durable mode")
    control_plane = store or ControlPlaneStore()

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.get("/health/details")
    def health_details():
        if not _detail_access_granted():
            return jsonify(error="invalid health detail token"), 401
        environment = os.getenv("HOOK_ENV", "dev")
        return jsonify(status="ok", deployment=control_plane.latest(environment))

    @app.get("/tasks/<task_id>")
    def task_details(task_id: str):
        if not _detail_access_granted():
            return jsonify(error="invalid health detail token"), 401
        task = control_plane.get(task_id)
        if task is None:
            return jsonify(error="task not found"), 404
        return jsonify(task=task)

    @app.post("/auth/token")
    def local_token():
        if os.getenv("OIDC_JWKS_URL", "").strip():
            return jsonify(error="use the configured OIDC provider"), 404
        body = request.get_json(silent=True) or {}
        token = issue_local_token(str(body.get("username", "")), str(body.get("password", "")))
        if token is None:
            return jsonify(error="invalid credentials or local token issuance is disabled"), 401
        return jsonify(access_token=token, token_type="Bearer", expires_in=28800)

    @app.post("/releases/<release_id>/approve")
    def approve_release(release_id: str):
        principal = authenticate()
        if principal is None:
            return jsonify(error="authentication required"), 401
        if not principal.roles & {"approver", "admin"}:
            return jsonify(error="approval role required"), 403
        body = request.get_json(silent=True) or {}
        try:
            result = control_plane.approve_release(
                release_id, principal.subject, body.get("reason")
            )
        except KeyError:
            return jsonify(error="release not found"), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 409
        control_plane.record_audit(
            {
                "action": "release_approved",
                "environment": result["environment"],
                "engine": result["engine"],
                "result": "approved",
                "operator": principal.subject,
                "version": result["version"],
                "detail": body.get("reason"),
            }
        )
        _enqueue_approved_task(result["task_id"], control_plane)
        return jsonify(result)

    @app.post("/releases/<release_id>/reject")
    def reject_release(release_id: str):
        principal = authenticate()
        if principal is None:
            return jsonify(error="authentication required"), 401
        if not principal.roles & {"approver", "admin"}:
            return jsonify(error="approval role required"), 403
        body = request.get_json(silent=True) or {}
        reason = str(body.get("reason", "")).strip()
        if not reason:
            return jsonify(error="rejection reason is required"), 400
        try:
            result = control_plane.reject_release(release_id, principal.subject, reason)
        except KeyError:
            return jsonify(error="release not found"), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 409
        control_plane.record_audit(
            {
                "action": "release_rejected",
                "environment": result["environment"],
                "engine": result["engine"],
                "result": "rejected",
                "operator": principal.subject,
                "version": result["version"],
                "detail": reason,
            }
        )
        return jsonify(result)

    @app.get("/metrics")
    def metrics():
        payload, content_type = metrics_payload()
        return Response(payload, content_type=content_type)

    @app.post("/webhook/gitee")
    def gitee_webhook():
        expected_token = os.getenv("GITEE_WEBHOOK_TOKEN", "")
        supplied_token = request.headers.get("X-Gitee-Token", "")
        if not expected_token:
            WEBHOOK_TOTAL.labels("rejected", os.getenv("HOOK_ENV", "unknown")).inc()
            log.error("GITEE_WEBHOOK_TOKEN is not configured")
            return jsonify(error="webhook token is not configured"), 503
        if not hmac.compare_digest(supplied_token, expected_token):
            WEBHOOK_TOTAL.labels("rejected", os.getenv("HOOK_ENV", "unknown")).inc()
            return jsonify(error="invalid token"), 401

        event = request.headers.get("X-Gitee-Event", "")
        if event and "push" not in event.lower():
            return jsonify(status="ignored", reason=f"unsupported event: {event}"), 202

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(error="request body must be a JSON object"), 400

        environment = os.getenv("HOOK_ENV", "dev")
        engine = os.getenv("HOOK_ENGINE", "compose")
        if engine not in {"compose", "k8s"}:
            return jsonify(error="HOOK_ENGINE must be compose or k8s"), 503
        try:
            config = load_environment(environment)
        except ConfigError as exc:
            return jsonify(error=str(exc)), 503

        source = config.section("source")
        configured_branch = str(source.get("branch", "main"))
        ref = str(payload.get("ref", ""))
        if ref and ref != f"refs/heads/{configured_branch}":
            return jsonify(status="ignored", reason=f"branch {ref} is not configured"), 202
        commit = str(payload.get("after") or payload.get("checkout_sha") or "")
        if not commit or set(commit) == {"0"}:
            return jsonify(status="ignored", reason="no deployable commit"), 202
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit):
            return jsonify(error="commit id must be hexadecimal"), 400
        try:
            version = validate_version(commit[:12])
        except ConfigError as exc:
            return jsonify(error=f"invalid commit id: {exc}"), 400

        repository = str(source.get("repository", "")).strip()
        if not repository:
            log.error("Webhook rejected because source.repository is empty for %s", environment)
            return jsonify(error="webhook deployments require source.repository"), 503

        delivery = request.headers.get("X-Gitee-Delivery", "").strip()
        idempotency_key = delivery or f"{environment}:{engine}:{commit.lower()}"
        requires_approval = bool(
            config.section("release").get("approval_required", False)
            or os.getenv("HOOK_APPROVAL_REQUIRED", "0") == "1"
        )
        existing = control_plane.get_by_key(idempotency_key)
        if existing:
            WEBHOOK_TOTAL.labels("duplicate", environment).inc()
            return jsonify(status="already accepted", task=existing), 202
        deploy_lock = Lease(f"deploy:{environment}")
        if not deploy_lock.acquire():
            WEBHOOK_TOTAL.labels("conflict", environment).inc()
            latest = control_plane.latest(environment)
            return jsonify(error="a deployment is already running", deployment=latest), 409
        try:
            task, created = control_plane.create_task(
                key=idempotency_key,
                environment=environment,
                engine=engine,
                version=version,
                commit=commit,
                requested_by="gitee-webhook",
                requires_approval=requires_approval,
            )
        except Exception:
            deploy_lock.release()
            existing = control_plane.get_by_key(idempotency_key)
            if existing:
                return jsonify(status="already accepted", task=existing), 202
            raise
        if not created:
            deploy_lock.release()
            return jsonify(status="already accepted", task=task), 202
        if task["status"] == "pending_approval":
            deploy_lock.release()
            WEBHOOK_TOTAL.labels("pending_approval", environment).inc()
            return jsonify(
                status="pending_approval", task_id=task["id"], release_id=task["release_id"]
            ), 202
        redis_url = os.getenv("PLATFORM_REDIS_URL", "").strip()
        if redis_url:
            try:
                from redis import Redis

                Redis.from_url(redis_url).rpush("auto-deploy:queue", task["id"])
            except Exception as exc:
                control_plane.update_task(task["id"], status="failed", error=str(exc))
                deploy_lock.release()
                log.exception("Unable to enqueue deployment task")
                return jsonify(error="deployment queue is unavailable"), 503
            deploy_lock.release()
            WEBHOOK_TOTAL.labels("queued", environment).inc()
            return jsonify(status="queued", task_id=task["id"]), 202

        worker = threading.Thread(
            target=_run_pipeline,
            args=(
                deploy_lock,
                control_plane,
                task["id"],
                environment,
                engine,
                version,
                commit,
                log,
            ),
            name=f"deploy-{environment}-{version}",
            daemon=True,
        )
        worker.start()
        WEBHOOK_TOTAL.labels("accepted", environment).inc()
        return (
            jsonify(
                status="accepted",
                task_id=task["id"],
                environment=environment,
                engine=engine,
                version=version,
            ),
            202,
        )

    return app


def _enqueue_approved_task(task_id: str | None, store: ControlPlaneStore) -> None:
    if not task_id:
        return
    redis_url = os.getenv("PLATFORM_REDIS_URL", "").strip()
    if redis_url:
        from redis import Redis

        Redis.from_url(redis_url).rpush("auto-deploy:queue", task_id)


def _detail_access_granted() -> bool:
    expected = os.getenv("PLATFORM_HEALTH_TOKEN", "").strip()
    supplied = request.headers.get("X-Health-Token", "")
    if expected and hmac.compare_digest(supplied, expected):
        return True
    principal = authenticate()
    return principal is not None and bool(principal.roles & {"viewer", "operator", "admin"})


def _run_pipeline(
    lock: Lease,
    control_plane: ControlPlaneStore,
    task_id: str,
    environment: str,
    engine: str,
    version: str,
    commit: str,
    logger: logging.Logger,
) -> None:
    started = time.monotonic()
    ACTIVE_DEPLOYMENTS.labels(environment).inc()
    control_plane.update_task(task_id, status="running")
    command = [
        sys.executable,
        "-m",
        "src.main",
        "--operator",
        "gitee-webhook",
        "pipeline",
        "--env",
        environment,
        "--engine",
        engine,
        "--version",
        version,
    ]
    try:
        process_environment = os.environ.copy()
        process_environment["SOURCE_COMMIT"] = commit
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=process_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=int(os.getenv("HOOK_PIPELINE_TIMEOUT", "1800")),
        )
        output = completed.stdout[-4000:]
        control_plane.update_task(
            task_id,
            status="succeeded" if completed.returncode == 0 else "failed",
            output=output,
            error=(
                None
                if completed.returncode == 0
                else f"pipeline exited with {completed.returncode}"
            ),
        )
        DEPLOYMENT_TOTAL.labels(
            "succeeded" if completed.returncode == 0 else "failed", environment, engine
        ).inc()
        if completed.returncode:
            logger.error("Webhook deployment failed:\n%s", output)
        else:
            logger.info("Webhook deployment completed for %s", version)
    except subprocess.TimeoutExpired:
        control_plane.update_task(task_id, status="failed", error="pipeline timed out")
        DEPLOYMENT_TOTAL.labels("failed", environment, engine).inc()
        logger.error("Webhook deployment timed out for %s", version)
    except Exception as exc:
        control_plane.update_task(task_id, status="failed", error=str(exc))
        DEPLOYMENT_TOTAL.labels("failed", environment, engine).inc()
        logger.exception("Unable to run webhook deployment")
    finally:
        DEPLOYMENT_DURATION.labels(environment, engine).observe(time.monotonic() - started)
        ACTIVE_DEPLOYMENTS.labels(environment).dec()
        lock.release()


def main() -> None:
    logger = setup_logging(verbose=os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG")
    create_app(logger).run(
        host=os.getenv("HOOK_HOST", "0.0.0.0"),
        port=int(os.getenv("HOOK_PORT", "9000")),
        debug=False,
    )


if __name__ == "__main__":
    main()
