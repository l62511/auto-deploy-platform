from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import PROJECT_ROOT


class Base(DeclarativeBase):
    pass


class DeploymentTask(Base):
    __tablename__ = "deployment_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    environment: Mapped[str] = mapped_column(String(64), index=True)
    engine: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(128))
    commit: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("releases.id"), nullable=True, index=True
    )


class Release(Base):
    __tablename__ = "releases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(64), index=True)
    engine: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(128))
    commit: Mapped[str] = mapped_column(String(64))
    image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    requested_by: Mapped[str] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), ForeignKey("releases.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    requested_by: Mapped[str] = mapped_column(String(255))
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    environment: Mapped[str] = mapped_column(String(64), index=True)
    engine: Mapped[str] = mapped_column(String(32))
    result: Mapped[str] = mapped_column(String(32))
    operator: Mapped[str] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReleaseState(Base):
    __tablename__ = "release_states"

    environment: Mapped[str] = mapped_column(String(64), primary_key=True)
    engine: Mapped[str] = mapped_column(String(32), primary_key=True)
    current_image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    previous_image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    history: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def utc_now() -> datetime:
    return datetime.now(UTC)


def task_dict(task: DeploymentTask | None) -> dict[str, Any] | None:
    if task is None:
        return None
    return {
        "id": task.id,
        "idempotency_key": task.idempotency_key,
        "environment": task.environment,
        "engine": task.engine,
        "version": task.version,
        "commit": task.commit,
        "status": task.status,
        "output": task.output,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "release_id": task.release_id,
    }


def release_dict(release: Release | None) -> dict[str, Any] | None:
    if release is None:
        return None
    return {
        "id": release.id,
        "environment": release.environment,
        "engine": release.engine,
        "version": release.version,
        "commit": release.commit,
        "image": release.image,
        "status": release.status,
        "requested_by": release.requested_by,
        "approved_by": release.approved_by,
        "created_at": release.created_at.isoformat() if release.created_at else None,
        "approved_at": release.approved_at.isoformat() if release.approved_at else None,
        "finished_at": release.finished_at.isoformat() if release.finished_at else None,
    }


class ControlPlaneStore:
    """Durable task state. PostgreSQL is recommended; SQLite keeps local development usable."""

    def __init__(self, database_url: str | None = None) -> None:
        url = database_url or os.getenv(
            "PLATFORM_DATABASE_URL",
            f"sqlite:///{(PROJECT_ROOT / '.runtime' / 'platform.db').as_posix()}",
        )
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, pool_pre_ping=True)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        if os.getenv("PLATFORM_AUTO_CREATE_SCHEMA", "1") == "1":
            Base.metadata.create_all(self.engine)

    def get_by_key(self, key: str) -> dict[str, Any] | None:
        with self.sessions() as session:
            task = session.scalar(
                select(DeploymentTask).where(DeploymentTask.idempotency_key == key)
            )
            return task_dict(task)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self.sessions() as session:
            return task_dict(session.get(DeploymentTask, task_id))

    def get_release(self, release_id: str) -> dict[str, Any] | None:
        with self.sessions() as session:
            return release_dict(session.get(Release, release_id))

    def create_task(
        self,
        *,
        key: str,
        environment: str,
        engine: str,
        version: str,
        commit: str,
        requested_by: str = "system",
        requires_approval: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        with self.sessions.begin() as session:
            existing = session.scalar(
                select(DeploymentTask).where(DeploymentTask.idempotency_key == key)
            )
            if existing:
                return task_dict(existing) or {}, False
            now = utc_now()
            release_id = secrets.token_hex(16)
            release_status = "pending_approval" if requires_approval else "queued"
            release = Release(
                id=release_id,
                environment=environment,
                engine=engine,
                version=version,
                commit=commit,
                status=release_status,
                requested_by=requested_by,
                created_at=now,
            )
            session.add(release)
            if requires_approval:
                session.add(
                    ApprovalRequest(
                        id=secrets.token_hex(16),
                        release_id=release_id,
                        status="pending",
                        requested_by=requested_by,
                        created_at=now,
                    )
                )
            task = DeploymentTask(
                id=secrets.token_hex(16),
                idempotency_key=key,
                environment=environment,
                engine=engine,
                version=version,
                commit=commit,
                status=release_status,
                created_at=now,
                release_id=release_id,
            )
            session.add(task)
            session.flush()
            return task_dict(task) or {}, True

    def update_task(
        self, task_id: str, *, status: str, output: str | None = None, error: str | None = None
    ) -> dict[str, Any] | None:
        allowed = {
            "pending_approval": {"queued", "rejected"},
            "queued": {"running", "cancelled"},
            "running": {"succeeded", "failed", "cancelled"},
            "succeeded": set(),
            "failed": set(),
            "rejected": set(),
            "cancelled": set(),
        }
        with self.sessions.begin() as session:
            task = session.get(DeploymentTask, task_id)
            if task is None:
                return None
            if status != task.status and status not in allowed.get(task.status, set()):
                raise ValueError(f"Invalid task transition: {task.status} -> {status}")
            now = utc_now()
            task.status = status
            if status == "running":
                task.started_at = now
            if status in {"succeeded", "failed", "cancelled"}:
                task.finished_at = now
            if output is not None:
                task.output = output[-4000:]
            task.error = error
            if task.release_id:
                release = session.get(Release, task.release_id)
                if release:
                    release.status = status
                    if status == "running":
                        release.approved_at = release.approved_at or now
                    if status in {"succeeded", "failed", "cancelled", "rejected"}:
                        release.finished_at = now
            session.flush()
            return task_dict(task)

    def approve_release(
        self, release_id: str, approver: str, reason: str | None = None
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            release = session.get(Release, release_id)
            if release is None:
                raise KeyError("release not found")
            if release.status != "pending_approval":
                raise ValueError(f"Release is not awaiting approval: {release.status}")
            approval = session.scalar(
                select(ApprovalRequest).where(ApprovalRequest.release_id == release_id)
            )
            task = session.scalar(
                select(DeploymentTask).where(DeploymentTask.release_id == release_id)
            )
            now = utc_now()
            release.status = "queued"
            release.approved_by = approver
            release.approved_at = now
            if approval:
                approval.status = "approved"
                approval.decided_by = approver
                approval.reason = reason
                approval.decided_at = now
            if task:
                task.status = "queued"
            return {
                "release_id": release_id,
                "task_id": task.id if task else None,
                "environment": release.environment,
                "engine": release.engine,
                "version": release.version,
                "status": "queued",
            }

    def reject_release(self, release_id: str, approver: str, reason: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            release = session.get(Release, release_id)
            if release is None:
                raise KeyError("release not found")
            if release.status != "pending_approval":
                raise ValueError(f"Release is not awaiting approval: {release.status}")
            approval = session.scalar(
                select(ApprovalRequest).where(ApprovalRequest.release_id == release_id)
            )
            task = session.scalar(
                select(DeploymentTask).where(DeploymentTask.release_id == release_id)
            )
            now = utc_now()
            release.status = "rejected"
            release.finished_at = now
            if approval:
                approval.status = "rejected"
                approval.decided_by = approver
                approval.reason = reason
                approval.decided_at = now
            if task:
                task.status = "rejected"
                task.finished_at = now
            return {
                "release_id": release_id,
                "task_id": task.id if task else None,
                "environment": release.environment,
                "engine": release.engine,
                "version": release.version,
                "status": "rejected",
            }

    def record_audit(self, event: dict[str, Any]) -> None:
        with self.sessions.begin() as session:
            session.add(
                AuditEvent(
                    id=secrets.token_hex(16),
                    action=str(event["action"]),
                    environment=str(event["environment"]),
                    engine=str(event["engine"]),
                    result=str(event["result"]),
                    operator=str(event["operator"]),
                    version=event.get("version"),
                    image=event.get("image"),
                    detail=event.get("detail"),
                    created_at=utc_now(),
                )
            )

    def list_audit(self, limit: int = 20) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("audit limit must be between 1 and 1000")
        with self.sessions() as session:
            events = session.scalars(
                select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
            ).all()
            return [
                {
                    "timestamp": event.created_at.isoformat(),
                    "action": event.action,
                    "environment": event.environment,
                    "engine": event.engine,
                    "operator": event.operator,
                    "version": event.version,
                    "image": event.image,
                    "result": event.result,
                    "detail": event.detail,
                }
                for event in events
            ]

    def load_release_state(self, environment: str, engine: str) -> dict[str, Any]:
        with self.sessions() as session:
            state = session.get(ReleaseState, (environment, engine))
            if state is None:
                return {"current_image": None, "history": []}
            return {
                "current_image": state.current_image,
                "previous_image": state.previous_image,
                "history": json.loads(state.history or "[]"),
                "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            }

    def mark_release_success(
        self, environment: str, engine: str, image: str, reason: str = "deploy"
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            state = session.get(ReleaseState, (environment, engine))
            previous = state.current_image if state else None
            history = json.loads(state.history or "[]") if state else []
            history.append(
                {
                    "image": image,
                    "previous_image": previous,
                    "reason": reason,
                    "timestamp": utc_now().isoformat(),
                }
            )
            if state is None:
                state = ReleaseState(environment=environment, engine=engine)
                session.add(state)
            state.current_image = image
            state.previous_image = previous
            state.history = json.dumps(history[-50:], ensure_ascii=False)
            state.updated_at = utc_now()
            return {
                "current_image": state.current_image,
                "previous_image": state.previous_image,
                "history": json.loads(state.history),
                "updated_at": state.updated_at.isoformat(),
            }

    def latest(self, environment: str) -> dict[str, Any] | None:
        with self.sessions() as session:
            task = session.scalar(
                select(DeploymentTask)
                .where(DeploymentTask.environment == environment)
                .order_by(DeploymentTask.created_at.desc())
                .limit(1)
            )
            return task_dict(task)

    def requeue_stale(self, max_age_seconds: int = 3600) -> int:
        cutoff = utc_now().timestamp() - max_age_seconds
        with self.sessions.begin() as session:
            tasks = session.scalars(
                select(DeploymentTask).where(DeploymentTask.status == "running")
            ).all()
            count = 0
            for task in tasks:
                if task.started_at and task.started_at.timestamp() < cutoff:
                    task.status = "queued"
                    task.started_at = None
                    count += 1
            return count

    def queued_task_ids(self) -> list[str]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(DeploymentTask.id).where(DeploymentTask.status == "queued")
                ).all()
            )


@dataclass
class Lease:
    name: str
    ttl_seconds: int = 3600
    redis_url: str | None = None
    token: str | None = None
    path: Path | None = None

    def __post_init__(self) -> None:
        self.redis_url = self.redis_url or os.getenv("PLATFORM_REDIS_URL", "").strip() or None
        self.token = secrets.token_urlsafe(24)
        self.path = PROJECT_ROOT / ".runtime" / f"lease-{self.name}.json"
        self._redis = None
        if self.redis_url:
            from redis import Redis

            self._redis = Redis.from_url(self.redis_url, decode_responses=True)

    def acquire(self) -> bool:
        if self._redis is not None:
            return bool(self._redis.set(self.key, self.token, nx=True, ex=self.ttl_seconds))
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if time.time() - float(data.get("created_at", 0)) <= self.ttl_seconds:
                    return False
            except (OSError, ValueError, TypeError):
                return False
            self.path.unlink(missing_ok=True)
            return self.acquire()
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"token": self.token, "created_at": time.time()}, handle)
        return True

    def release(self) -> None:
        if self._redis is not None:
            script = (
                "if redis.call('get', KEYS[1]) == ARGV[1] "
                "then return redis.call('del', KEYS[1]) else return 0 end"
            )
            self._redis.eval(script, 1, self.key, self.token)
            return
        assert self.path is not None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, ValueError):
            return

    @property
    def key(self) -> str:
        digest = hashlib.sha256(self.name.encode()).hexdigest()[:24]
        return f"auto-deploy:lease:{digest}"
