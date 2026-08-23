from __future__ import annotations

import os
import subprocess

import pytest

pytestmark = pytest.mark.integration


def test_worker_can_reach_docker_api_through_socket_proxy() -> None:
    if os.getenv("RUN_DOCKER_INTEGRATION") != "1":
        pytest.skip("set RUN_DOCKER_INTEGRATION=1 to run against Docker Desktop")
    command = [
        "docker",
        "compose",
        "--env-file",
        ".env",
        "-f",
        "config/compose/docker-compose-platform.yml",
        "exec",
        "-T",
        "worker",
        "docker",
        "info",
        "--format",
        "{{.ServerVersion}}",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
