from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        self.directory.mkdir(parents=True, exist_ok=True)
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
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with (self.directory / "audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event


@dataclass
class ReleaseStateStore:
    environment: str
    engine: str
    directory: Path = PROJECT_ROOT / "releases"

    @property
    def path(self) -> Path:
        return self.directory / f"{self.environment}-{self.engine}-state.json"

    def load(self) -> dict[str, Any]:
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

