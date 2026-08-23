from __future__ import annotations

from src.control_plane import ControlPlaneStore, Lease
from src.worker import recover_queued_tasks


class FakeRedis:
    def __init__(self) -> None:
        self.items = []

    def rpush(self, _queue: str, task_id: str) -> None:
        self.items.append(task_id)


def test_control_plane_task_is_idempotent(tmp_path) -> None:
    store = ControlPlaneStore(f"sqlite:///{tmp_path / 'control.db'}")

    first, created = store.create_task(
        key="gitee:delivery-1",
        environment="dev",
        engine="compose",
        version="abc123",
        commit="a" * 40,
    )
    second, duplicate = store.create_task(
        key="gitee:delivery-1",
        environment="dev",
        engine="compose",
        version="abc123",
        commit="a" * 40,
    )

    assert created is True
    assert duplicate is False
    assert second["id"] == first["id"]
    store.update_task(first["id"], status="running")
    assert store.update_task(first["id"], status="succeeded", output="ok")["status"] == "succeeded"
    assert store.latest("dev")["output"] == "ok"


def test_approval_transitions_release_and_task(tmp_path) -> None:
    store = ControlPlaneStore(f"sqlite:///{tmp_path / 'approval.db'}")
    task, _ = store.create_task(
        key="approval-1",
        environment="prod",
        engine="k8s",
        version="abc123",
        commit="a" * 40,
        requested_by="developer",
        requires_approval=True,
    )
    assert task["status"] == "pending_approval"
    result = store.approve_release(task["release_id"], "approver")
    assert result["status"] == "queued"
    assert store.get(task["id"])["status"] == "queued"


def test_rejected_release_is_terminal(tmp_path) -> None:
    store = ControlPlaneStore(f"sqlite:///{tmp_path / 'reject.db'}")
    task, _ = store.create_task(
        key="approval-reject",
        environment="prod",
        engine="k8s",
        version="abc123",
        commit="a" * 40,
        requires_approval=True,
    )
    result = store.reject_release(task["release_id"], "approver", "change window closed")
    assert result["status"] == "rejected"
    assert store.get(task["id"])["status"] == "rejected"


def test_file_lease_reclaims_expired_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    first = Lease("test", ttl_seconds=1)
    assert first.acquire() is True
    second = Lease("test", ttl_seconds=1)
    assert second.acquire() is False
    assert first.path is not None
    first.path.write_text('{"token":"stale", "created_at": 0}', encoding="utf-8")
    assert second.acquire() is True
    second.release()


def test_stale_running_task_is_requeued(tmp_path) -> None:
    store = ControlPlaneStore(f"sqlite:///{tmp_path / 'control.db'}")
    task, _ = store.create_task(
        key="stale-task",
        environment="dev",
        engine="compose",
        version="abc123",
        commit="a" * 40,
    )
    store.update_task(task["id"], status="running")
    assert store.requeue_stale(max_age_seconds=0) == 1
    assert store.get(task["id"])["status"] == "queued"
    assert store.queued_task_ids() == [task["id"]]


def test_worker_recovery_restores_queue_after_crash(tmp_path) -> None:
    store = ControlPlaneStore(f"sqlite:///{tmp_path / 'control.db'}")
    task, _ = store.create_task(
        key="crashed-worker",
        environment="dev",
        engine="compose",
        version="abc123",
        commit="a" * 40,
    )
    store.update_task(task["id"], status="running")
    redis = FakeRedis()

    assert recover_queued_tasks(redis, store, stale_seconds=0) == 1
    assert redis.items == [task["id"]]
