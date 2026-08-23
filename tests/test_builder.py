from __future__ import annotations

import logging

import pytest

from src.audit import AuditLog
from src.builder import ImageBuilder, ImageBuildError
from src.config import load_environment


def test_webhook_commit_cannot_build_unconfigured_local_source(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SOURCE_COMMIT", "a" * 40)
    builder = ImageBuilder(
        load_environment("dev"), logging.getLogger("test"), AuditLog(tmp_path), "tester"
    )

    with pytest.raises(ImageBuildError, match="requires source.repository"):
        builder.prepare_source()
