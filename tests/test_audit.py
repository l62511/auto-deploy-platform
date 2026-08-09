from __future__ import annotations

import json

from src.audit import AuditLog, ReleaseStateStore


def test_release_state_tracks_previous_success_and_rollback(tmp_path) -> None:
    store = ReleaseStateStore("dev", "compose", tmp_path)

    store.mark_success("registry/demo:v1")
    store.mark_success("registry/demo:v2")

    assert store.load()["current_image"] == "registry/demo:v2"
    assert store.rollback_target() == "registry/demo:v1"

    store.mark_success("registry/demo:v1", reason="rollback")
    assert store.rollback_target() == "registry/demo:v2"


def test_audit_writes_json_lines(tmp_path) -> None:
    audit = AuditLog(tmp_path)
    event = audit.record(
        action="deploy",
        environment="dev",
        engine="compose",
        result="success",
        operator="tester",
        version="v1",
        image="registry/demo:v1",
    )

    line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(line) == event

