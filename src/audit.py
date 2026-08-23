from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AuditLog:
    directory: Path = PROJECT_ROOT / "releases"

    def record(
        self,
        *,
        action: str,
        environment: str,
        engine: str,
        result: str,
        operator: str,
        version: str | None = None,
        image: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "timestamp": utc_now(),
            "action": action,
            "environment": environment,
            "engine": engine,
            "operator": operator,
            "version": version,
            "image": image,
            "result": result,
            "detail": detail,
        }
        database_url = os.getenv("PLATFORM_DATABASE_URL", "").strip()
        if database_url and os.getenv("PLATFORM_FILE_AUDIT", "0") != "1":
            from .control_plane import ControlPlaneStore

            ControlPlaneStore(database_url).record_audit(event)
            return event
        self.directory.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with (self.directory / "audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event


@dataclass
class ReleaseArtifactStore:
    """Append-only provenance records for images that were built by this platform."""

    directory: Path = PROJECT_ROOT / "releases"

    def record(
        self,
        *,
        environment: str,
        version: str,
        image_tag: str,
        image_reference: str,
        source_commit: str | None,
        source_repository: str | None,
    ) -> dict[str, Any]:
        self.directory.mkdir(parents=True, exist_ok=True)
        artifact = {
            "timestamp": utc_now(),
            "environment": environment,
            "version": version,
            "image_tag": image_tag,
            "image_reference": image_reference,
            "source_commit": source_commit,
            "source_repository": source_repository,
        }
        encoded = json.dumps(artifact, ensure_ascii=False, separators=(",", ":"))
        with (self.directory / "artifacts.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return artifact


@dataclass
class ReleaseStateStore:
    environment: str
    engine: str
    directory: Path = PROJECT_ROOT / "releases"

    @property
    def database(self):
        database_url = os.getenv("PLATFORM_DATABASE_URL", "").strip()
        if not database_url or os.getenv("PLATFORM_FILE_STATE", "0") == "1":
            return None
        from .control_plane import ControlPlaneStore

        return ControlPlaneStore(database_url)

    @property
    def path(self) -> Path:
        return self.directory / f"{self.environment}-{self.engine}-state.json"

    def load(self) -> dict[str, Any]:
        if self.database:
            return self.database.load_release_state(self.environment, self.engine)
        if not self.path.exists():
            return {"current_image": None, "history": []}
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"Invalid release state: {self.path}")
        data.setdefault("current_image", None)
        data.setdefault("history", [])
        return data

    def mark_success(self, image: str, *, reason: str = "deploy") -> dict[str, Any]:
        if self.database:
            return self.database.mark_release_success(self.environment, self.engine, image, reason)
        state = self.load()
        previous = state.get("current_image")
        history = list(state.get("history", []))
        history.append(
            {
                "image": image,
                "previous_image": previous,
                "reason": reason,
                "timestamp": utc_now(),
            }
        )
        state.update(
            {
                "current_image": image,
                "previous_image": previous,
                "updated_at": utc_now(),
                "history": history[-50:],
            }
        )
        self._atomic_write(state)
        return state

    def rollback_target(self) -> str | None:
        state = self.load()
        current = state.get("current_image")
        for item in reversed(state.get("history", [])[:-1]):
            candidate = item.get("image")
            if candidate and candidate != current:
                return str(candidate)
        previous = state.get("previous_image")
        return str(previous) if previous and previous != current else None

    def _atomic_write(self, state: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.directory, text=True
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
