from __future__ import annotations

from confluence_pr_agent.storage.page_store import PageStore, StoredPage


def test_get_returns_none_when_absent(tmp_path):
    store = PageStore(tmp_path / "store.json")
    assert store.get("123") is None


def test_put_then_get_roundtrips(tmp_path):
    store = PageStore(tmp_path / "store.json")
    page = StoredPage(
        page_id="123", title="Spec", version=1, body_html="<p>hi</p>", body_checksum="abc123", url="https://x"
    )
    store.put(page)

    assert store.get("123") == page


def test_persists_across_instances(tmp_path):
    path = tmp_path / "store.json"
    store1 = PageStore(path)
    store1.put(
        StoredPage(
            page_id="123", title="Spec", version=1, body_html="<p>hi</p>", body_checksum="abc123", url="https://x"
        )
    )

    store2 = PageStore(path)
    stored = store2.get("123")
    assert stored is not None
    assert stored["version"] == 1


def test_list_all_returns_all_pages(tmp_path):
    store = PageStore(tmp_path / "store.json")
    assert store.list_all() == []

    p1 = StoredPage(
        page_id="1", title="Spec 1", version=1, body_html="<p>1</p>", body_checksum="abc", url="https://x/1"
    )
    p2 = StoredPage(
        page_id="2", title="Spec 2", version=1, body_html="<p>2</p>", body_checksum="def", url="https://x/2"
    )
    store.put(p1)
    store.put(p2)

    pages = store.list_all()
    assert len(pages) == 2
    assert {p["page_id"] for p in pages} == {"1", "2"}


def test_update_repo_pr_field(tmp_path):
    store = PageStore(tmp_path / "store.json")
    p1 = StoredPage(
        page_id="1",
        title="Spec 1",
        version=1,
        body_html="<p>1</p>",
        body_checksum="abc",
        url="https://x/1",
        repo_prs={"owner/repo": {"open_pr_number": 42, "open_pr_branch": "feature"}},
    )
    store.put(p1)

    store.update_repo_pr_field("1", "owner/repo", "last_seen_comment_id", 999)
    store.update_repo_pr_field("1", "owner/repo", "feedback_attempts", 2)

    updated = store.get("1")
    assert updated["repo_prs"]["owner/repo"]["open_pr_number"] == 42
    assert updated["repo_prs"]["owner/repo"]["last_seen_comment_id"] == 999
    assert updated["repo_prs"]["owner/repo"]["feedback_attempts"] == 2
