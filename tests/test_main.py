from __future__ import annotations

import pytest

from src.main import build_parser


def test_pipeline_cli_arguments() -> None:
    args = build_parser().parse_args(
        ["--operator", "tester", "pipeline", "--env", "dev", "--engine", "compose", "--version", "v1"]
    )

    assert args.command == "pipeline"
    assert args.operator == "tester"
    assert args.version == "v1"


def test_cli_rejects_unknown_environment() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["check", "--env", "prod", "--engine", "compose"])

