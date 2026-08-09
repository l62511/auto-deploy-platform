from __future__ import annotations

from src.hook_server import create_app


def test_webhook_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setenv("GITEE_WEBHOOK_TOKEN", "correct-token")
    client = create_app().test_client()

    response = client.post(
        "/webhook/gitee",
        headers={"X-Gitee-Token": "wrong-token"},
        json={"after": "a" * 40, "ref": "refs/heads/main"},
    )

    assert response.status_code == 401


def test_webhook_ignores_unconfigured_branch(monkeypatch) -> None:
    monkeypatch.setenv("GITEE_WEBHOOK_TOKEN", "correct-token")
    monkeypatch.setenv("HOOK_ENV", "dev")
    client = create_app().test_client()

    response = client.post(
        "/webhook/gitee",
        headers={"X-Gitee-Token": "correct-token", "X-Gitee-Event": "Push Hook"},
        json={"after": "a" * 40, "ref": "refs/heads/feature"},
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "ignored"


def test_webhook_rejects_non_hex_commit(monkeypatch) -> None:
    monkeypatch.setenv("GITEE_WEBHOOK_TOKEN", "correct-token")
    monkeypatch.setenv("HOOK_ENV", "dev")
    client = create_app().test_client()

    response = client.post(
        "/webhook/gitee",
        headers={"X-Gitee-Token": "correct-token", "X-Gitee-Event": "Push Hook"},
        json={"after": "not-a-commit", "ref": "refs/heads/main"},
    )

    assert response.status_code == 400
