from __future__ import annotations

from src.control_plane import ControlPlaneStore
from src.hook_server import create_app


def test_webhook_rejects_invalid_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GITEE_WEBHOOK_TOKEN", "correct-token")
    client = create_app(
        store=ControlPlaneStore(f"sqlite:///{tmp_path / 'control.db'}")
    ).test_client()

    response = client.post(
        "/webhook/gitee",
        headers={"X-Gitee-Token": "wrong-token"},
        json={"after": "a" * 40, "ref": "refs/heads/main"},
    )

    assert response.status_code == 401


def test_webhook_ignores_unconfigured_branch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GITEE_WEBHOOK_TOKEN", "correct-token")
    monkeypatch.setenv("HOOK_ENV", "dev")
    client = create_app(
        store=ControlPlaneStore(f"sqlite:///{tmp_path / 'control.db'}")
    ).test_client()

    response = client.post(
        "/webhook/gitee",
        headers={"X-Gitee-Token": "correct-token", "X-Gitee-Event": "Push Hook"},
        json={"after": "a" * 40, "ref": "refs/heads/feature"},
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "ignored"


def test_webhook_rejects_non_hex_commit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GITEE_WEBHOOK_TOKEN", "correct-token")
    monkeypatch.setenv("HOOK_ENV", "dev")
    client = create_app(
        store=ControlPlaneStore(f"sqlite:///{tmp_path / 'control.db'}")
    ).test_client()

    response = client.post(
        "/webhook/gitee",
        headers={"X-Gitee-Token": "correct-token", "X-Gitee-Event": "Push Hook"},
        json={"after": "not-a-commit", "ref": "refs/heads/main"},
    )

    assert response.status_code == 400


def test_webhook_requires_remote_source_for_matching_branch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GITEE_WEBHOOK_TOKEN", "correct-token")
    monkeypatch.setenv("HOOK_ENV", "dev")
    client = create_app(
        store=ControlPlaneStore(f"sqlite:///{tmp_path / 'control.db'}")
    ).test_client()

    response = client.post(
        "/webhook/gitee",
        headers={"X-Gitee-Token": "correct-token", "X-Gitee-Event": "Push Hook"},
        json={"after": "a" * 40, "ref": "refs/heads/main"},
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "webhook deployments require source.repository"


def test_health_details_requires_separate_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PLATFORM_HEALTH_TOKEN", "details-token")
    client = create_app(
        store=ControlPlaneStore(f"sqlite:///{tmp_path / 'control.db'}")
    ).test_client()

    assert client.get("/health").get_json() == {"status": "ok"}
    assert client.get("/health/details").status_code == 401
    response = client.get("/health/details", headers={"X-Health-Token": "details-token"})
    assert response.status_code == 200
    assert (
        client.get("/tasks/missing", headers={"X-Health-Token": "details-token"}).status_code == 404
    )


def test_local_jwt_token_and_rbac(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("AUTH_LOCAL_ENABLED", "1")
    monkeypatch.setenv("AUTH_LOCAL_USERNAME", "operator")
    monkeypatch.setenv("AUTH_LOCAL_PASSWORD", "password")
    client = create_app(
        store=ControlPlaneStore(f"sqlite:///{tmp_path / 'control.db'}")
    ).test_client()

    token_response = client.post(
        "/auth/token", json={"username": "operator", "password": "password"}
    )
    assert token_response.status_code == 200
    token = token_response.get_json()["access_token"]
    assert (
        client.get("/health/details", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )
