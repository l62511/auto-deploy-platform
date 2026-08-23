from __future__ import annotations

import pytest

from src.config import ConfigError, load_environment, validate_version


def test_load_dev_environment_and_build_image_reference() -> None:
    config = load_environment("dev")

    assert config.name == "dev"
    assert config.section("compose")["project_name"] == "auto-deploy-dev"
    assert config.image_ref("v1.2.3") == "localhost:5000/demo-app:v1.2.3-dev"
    assert config.resolve_path("app_dockerfile").is_dir()


@pytest.mark.parametrize("version", ["", "bad tag", "../../image", "v1:latest"])
def test_rejects_unsafe_versions(version: str) -> None:
    with pytest.raises(ConfigError):
        validate_version(version)


def test_rejects_unknown_environment() -> None:
    with pytest.raises(ConfigError):
        load_environment("production")
