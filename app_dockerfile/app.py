from __future__ import annotations

import os
from datetime import datetime, timezone

import mysql.connector
import redis
from flask import Flask, jsonify


app = Flask(__name__)
STARTED_AT = datetime.now(timezone.utc).isoformat()


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def check_redis() -> None:
    client = redis.Redis(
        host=env("REDIS_HOST", "redis"),
        port=int(env("REDIS_PORT", "6379")),
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    if not client.ping():
        raise RuntimeError("Redis PING returned false")


def check_mysql() -> None:
    connection = mysql.connector.connect(
        host=env("MYSQL_HOST", "mysql"),
        port=int(env("MYSQL_PORT", "3306")),
        database=env("MYSQL_DATABASE", "demo"),
        user=env("MYSQL_USER", "demo"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        connection_timeout=2,
    )
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
    finally:
        connection.close()


@app.get("/")
def index():
    return jsonify(
        service="demo-app",
        environment=env("APP_ENV", "unknown"),
        version=env("APP_VERSION", "unknown"),
        started_at=STARTED_AT,
    )


@app.get("/health")
def health():
    checks: dict[str, str] = {"application": "ok"}
    if env("DEPENDENCY_CHECK_ENABLED", "true").lower() == "true":
        for name, checker in (("redis", check_redis), ("mysql", check_mysql)):
            try:
                checker()
                checks[name] = "ok"
            except Exception as exc:
                checks[name] = f"error: {exc}"
    healthy = all(value == "ok" for value in checks.values())
    return jsonify(status="ok" if healthy else "error", checks=checks), 200 if healthy else 503


@app.get("/ready")
def ready():
    return jsonify(status="ready")

