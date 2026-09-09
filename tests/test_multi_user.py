"""End-to-end proof that two different authenticated identities stay fully
isolated -- config.py's own isolation (Settings never reading os.environ)
is exercised implicitly by every other test module via the shared
DEFAULT_USER="testuser" fixture pattern; this file is specifically about
what happens when two *different* X-Auth-User identities hit the real HTTP
layer, which nothing else covers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from confluence_pr_agent.config import get_process_config, get_settings
from confluence_pr_agent.webhook.app import app


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # Blanked by default (real top-level .env has a real secret) -- tests
    # specifically about the secret check set it themselves.
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", "")
    get_process_config.cache_clear()
    get_settings.cache_clear()
    yield
    get_process_config.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)


def _as(username: str) -> dict[str, str]:
    return {"X-Auth-User": username}


def test_config_saved_by_one_user_is_invisible_to_another(client):
    client.post(
        "/ui/config", data={"TARGET_REPOS_JSON": '[{"target_repo": "alice/repo-a"}]'}, headers=_as("alice")
    )
    client.post(
        "/ui/config", data={"TARGET_REPOS_JSON": '[{"target_repo": "bob/repo-b"}]'}, headers=_as("bob")
    )

    alice_page = client.get("/ui/config", headers=_as("alice")).text
    bob_page = client.get("/ui/config", headers=_as("bob")).text

    assert "alice/repo-a" in alice_page
    assert "bob/repo-b" not in alice_page
    assert "bob/repo-b" in bob_page
    assert "alice/repo-a" not in bob_page


def test_run_history_is_scoped_per_user(client):
    from confluence_pr_agent.models import RunRecord
    from confluence_pr_agent.storage.run_store import RunStore

    alice_settings = get_settings("alice")
    RunStore(alice_settings.runs_store_path).upsert_run(
        RunRecord(
            run_id="alice-run-1", started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:05+00:00",
            duration_seconds=5.0, page_id="1", page_title="Alice Page", confluence_url="", engine="gemini",
            target_repo="alice/repo-a", status="opened_pr",
        )
    )

    alice_runs_page = client.get("/ui/runs", headers=_as("alice")).text
    bob_runs_page = client.get("/ui/runs", headers=_as("bob")).text

    assert "Alice Page" in alice_runs_page
    assert "Alice Page" not in bob_runs_page


def test_new_username_is_auto_provisioned_with_a_blank_slate(client):
    """No explicit "create user" step -- the first authenticated request
    from a never-seen-before username just works, starting from Settings'
    hardcoded defaults (see config.py::get_settings)."""
    resp = client.get("/ui/config", headers=_as("brand-new-person"))
    assert resp.status_code == 200

    process = get_process_config()
    assert process.user_dir_path("brand-new-person").exists()


def test_default_user_bootstraps_from_top_level_env(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "CONFLUENCE_BASE_URL=https://configured.example/wiki\n"
        "CONFLUENCE_USER_EMAIL=configured@example.com\n"
    )
    monkeypatch.setenv("DEFAULT_USER", "demo-user")
    get_process_config.cache_clear()
    get_settings.cache_clear()

    response = client.get("/ui/config", headers=_as("demo-user"))

    assert response.status_code == 200
    assert "https://configured.example/wiki" in response.text
    assert "configured@example.com" in response.text
    assert get_settings("demo-user").confluence_base_url == "https://configured.example/wiki"
    assert get_settings("other-user").confluence_base_url == "https://example.atlassian.net/wiki"


def test_current_username_rejects_a_request_without_the_shared_secret_when_configured(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", "the-real-secret")
    get_process_config.cache_clear()

    # X-Auth-User alone, no X-Internal-Secret -- must be rejected once a
    # real secret is configured (this is what makes trusting X-Auth-User
    # in the first place safe -- see ui/auth.py).
    resp = client.get("/ui/config", headers=_as("alice"))
    assert resp.status_code == 401

    resp = client.get(
        "/ui/config", headers={**_as("alice"), "X-Internal-Secret": "wrong-secret"}
    )
    assert resp.status_code == 401

    resp = client.get(
        "/ui/config", headers={**_as("alice"), "X-Internal-Secret": "the-real-secret"}
    )
    assert resp.status_code == 200

    get_process_config.cache_clear()


def test_current_username_skips_the_secret_check_when_unconfigured(client, monkeypatch):
    """Local `uvicorn --reload` dev convenience: with no Caddy in front to
    ever set X-Internal-Secret, the check would otherwise make every /ui/*
    route permanently unreachable in that setup."""
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", "")
    get_process_config.cache_clear()

    resp = client.get("/ui/config", headers=_as("alice"))
    assert resp.status_code == 200


def test_no_identity_at_all_is_rejected(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", "")
    monkeypatch.setenv("DEFAULT_USER", "")
    get_process_config.cache_clear()

    resp = client.get("/ui/config")
    assert resp.status_code == 401
