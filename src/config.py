from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class ConfigError(ValueError):
    """Raised when an environment configuration is missing or invalid."""


@dataclass(frozen=True)
class EnvironmentConfig:
    name: str
    data: dict[str, Any]
    source_file: Path

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        if not isinstance(value, dict):
            raise ConfigError(f"Configuration section '{name}' must be a mapping")
        return value

    def require(self, dotted_key: str) -> Any:
        value: Any = self.data
        for key in dotted_key.split("."):
            if not isinstance(value, dict) or key not in value:
                raise ConfigError(f"Missing required configuration: {dotted_key}")
            value = value[key]
        return value

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    @property
    def image_repository(self) -> str:
        registry = str(self.require("registry.address")).rstrip("/")
        repository = str(self.require("registry.repository")).strip("/")
        return f"{registry}/{repository}" if registry else repository

    def image_ref(self, version: str) -> str:
        normalized = validate_version(version)
        return f"{self.image_repository}:{normalized}-{self.name}"


def validate_version(version: str) -> str:
    value = version.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ConfigError(
            "Version must start with a letter or number and contain only letters, "
            "numbers, '.', '_' or '-'"
        )
    return value


def load_environment(name: str, config_dir: Path | None = None) -> EnvironmentConfig:
    if not ENV_NAME_PATTERN.fullmatch(name):
        raise ConfigError(f"Invalid environment name: {name!r}")

    directory = config_dir or PROJECT_ROOT / "config"
    source_file = directory / f"env_{name}.yaml"
    if not source_file.is_file():
        raise ConfigError(f"Environment configuration not found: {source_file}")

    with source_file.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Top-level YAML value must be a mapping: {source_file}")

    configured_name = data.get("environment")
    if configured_name != name:
        raise ConfigError(
            f"Configuration environment is {configured_name!r}, expected {name!r}"
        )

    config = EnvironmentConfig(name=name, data=data, source_file=source_file)
    for key in (
        "project_name",
        "registry.address",
        "registry.repository",
        "compose.file",
        "compose.project_name",
        "compose.health_url",
        "k8s.namespace",
        "k8s.deployment_name",
    ):
        config.require(key)
    return config

