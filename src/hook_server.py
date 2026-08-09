from __future__ import annotations

import hmac
import logging
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, request

from .config import PROJECT_ROOT, ConfigError, load_environment, validate_version
from .logger import setup_logging


def create_app(logger: logging.Logger | None = None) -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    log = logger or setup_logging()
    deploy_lock = threading.Lock()
    state: dict[str, Any] = {
        "running": False,
        "last_started_at": None,
        "last_finished_at": None,
        "last_result": None,
        "last_version": None,
    }

    @app.get("/health")
    def health():
        return jsonify(status="ok", deployment=state)

    @app.post("/webhook/gitee")
    def gitee_webhook():
        expected_token = os.getenv("GITEE_WEBHOOK_TOKEN", "")
        supplied_token = request.headers.get("X-Gitee-Token", "")
        if not expected_token:
            log.error("GITEE_WEBHOOK_TOKEN is not configured")
            return jsonify(error="webhook token is not configured"), 503
        if not hmac.compare_digest(supplied_token, expected_token):
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

        configured_branch = str(config.section("source").get("branch", "main"))
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

        if not deploy_lock.acquire(blocking=False):
            return jsonify(error="a deployment is already running", deployment=state), 409

        state.update(
            running=True,
            last_started_at=datetime.now(timezone.utc).isoformat(),
            last_result=None,
            last_version=version,
        )
        worker = threading.Thread(
            target=_run_pipeline,
            args=(deploy_lock, state, environment, engine, version, commit, log),
            name=f"deploy-{environment}-{version}",
            daemon=True,
        )
        worker.start()
        return jsonify(status="accepted", environment=environment, engine=engine, version=version), 202

    return app


def _run_pipeline(
    lock: threading.Lock,
    state: dict[str, Any],
    environment: str,
    engine: str,
    version: str,
    commit: str,
    logger: logging.Logger,
) -> None:
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
        )
        output = completed.stdout[-4000:]
        state["last_result"] = {
            "success": completed.returncode == 0,
            "exit_code": completed.returncode,
            "output": output,
        }
        if completed.returncode:
            logger.error("Webhook deployment failed:\n%s", output)
        else:
            logger.info("Webhook deployment completed for %s", version)
    except Exception as exc:
        state["last_result"] = {"success": False, "error": str(exc)}
        logger.exception("Unable to run webhook deployment")
    finally:
        state["running"] = False
        state["last_finished_at"] = datetime.now(timezone.utc).isoformat()
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
