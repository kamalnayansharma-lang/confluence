from __future__ import annotations

from confluence_pr_agent.confluence.diff import _to_plain_text, build_full_spec_diff, compute_checksum, compute_diff
from confluence_pr_agent.models import PageSnapshot
from confluence_pr_agent.storage.page_store import PageStore, StoredPage


def _page(version: int, body: str) -> PageSnapshot:
    return PageSnapshot(
        page_id="123456",
        title="Checkout Flow Spec",
        version=version,
        body_html=body,
        url="https://example.atlassian.net/wiki/spaces/SD/pages/123456",
    )


def _checksum_for(body_html: str) -> str:
    return compute_checksum(_to_plain_text(body_html))


def _stored(version: int, body_html: str) -> StoredPage:
    return StoredPage(
        page_id="123456",
        title="Checkout Flow Spec",
        version=version,
        body_html=body_html,
        body_checksum=_checksum_for(body_html),
        url="https://example.com",
    )


def test_first_seen_page_has_no_previous_version(tmp_path):
    store = PageStore(tmp_path / "store.json")
    page = _page(1, "<p>Checkout must support credit card payments.</p>")

    diff = compute_diff(store, page)

    assert diff.is_first_seen is True
    assert diff.previous_version is None
    assert "credit card" in diff.diff_text
    assert diff.body_checksum == _checksum_for(page.body_html)


def test_unchanged_version_produces_empty_diff(tmp_path):
    """Redelivering the same webhook for a page that hasn't moved is a no-op."""
    store = PageStore(tmp_path / "store.json")
    store.put(_stored(1, "<p>Checkout must support credit card payments.</p>"))
    page = _page(1, "<p>Checkout must support credit card payments.</p>")

    diff = compute_diff(store, page)

    assert diff.is_first_seen is False
    assert diff.diff_text == ""
    assert diff.content_unchanged is False  # short-circuited on version match, not checksum


def test_full_spec_diff_never_empty_even_for_an_already_processed_page(tmp_path):
    """Regression test for the real bug found in Plan a Sprint: reusing
    compute_diff for planning gave the planner an EMPTY diff_text for any
    page the regular pipeline had already processed (see the sibling
    test_unchanged_version_produces_empty_diff above -- that's correct for
    the main pipeline's dedup guard, wrong for planning). build_full_spec_diff
    must always return the page's actual current content, regardless of
    PageStore state -- it doesn't even take a store argument.
    """
    page = _page(1, "<p>Checkout must support credit card payments.</p>")

    diff = build_full_spec_diff(page)

    assert diff.is_first_seen is True
    assert diff.previous_version is None
    assert "credit card" in diff.diff_text
    assert diff.diff_text != ""
    assert diff.body_checksum == _checksum_for(page.body_html)


def test_changed_version_produces_unified_diff(tmp_path):
    store = PageStore(tmp_path / "store.json")
    store.put(_stored(1, "<p>Checkout must support credit card payments.</p>"))
    page = _page(2, "<p>Checkout must support credit card and PayPal payments.</p>")

    diff = compute_diff(store, page)

    assert diff.is_first_seen is False
    assert diff.previous_version == 1
    assert "PayPal" in diff.diff_text
    assert diff.diff_text.startswith("---")


def test_version_bump_with_identical_content_is_treated_as_no_change(tmp_path):
    """A metadata-only edit (labels, restrictions, a no-op restore) bumps the
    version without changing the visible body -- the checksum guard should
    catch this and skip the pipeline, same as an exact version match.
    """
    store = PageStore(tmp_path / "store.json")
    body_html = "<p>Checkout must support credit card payments.</p>"
    store.put(_stored(1, body_html))
    page = _page(2, body_html)  # version moved, content did not

    diff = compute_diff(store, page)

    assert diff.is_first_seen is False
    assert diff.previous_version == 1
    assert diff.diff_text == ""
    assert diff.content_unchanged is True


def test_checksum_ignores_insignificant_html_differences(tmp_path):
    """Whitespace/formatting-only storage-format differences that normalize
    to the same plain text should also be treated as no change.
    """
    store = PageStore(tmp_path / "store.json")
    store.put(_stored(1, "<p>Checkout must support credit card payments.</p>"))
    page = _page(2, "<p>  Checkout must support credit card payments.  </p>")

    diff = compute_diff(store, page)

    assert diff.diff_text == ""
    assert diff.content_unchanged is True


def test_force_bypasses_exact_version_match(tmp_path):
    """The Retrigger button's use case: same version, human wants it
    reprocessed anyway (e.g. just added a routing label)."""
    store = PageStore(tmp_path / "store.json")
    body_html = "<p>Checkout must support credit card payments.</p>"
    store.put(_stored(1, body_html))
    page = _page(1, body_html)  # exact version match

    diff = compute_diff(store, page, force=True)

    assert diff.diff_text != ""
    assert "credit card" in diff.diff_text
    assert diff.forced is True
    assert diff.is_first_seen is False  # honest -- there IS prior history


def test_force_bypasses_checksum_match(tmp_path):
    """Same as above but via the version-bumped-identical-content path (the
    realistic case: labels changed, which bumps Confluence's version, body
    did not)."""
    store = PageStore(tmp_path / "store.json")
    body_html = "<p>Checkout must support credit card payments.</p>"
    store.put(_stored(1, body_html))
    page = _page(2, body_html)  # version moved, content did not

    diff = compute_diff(store, page, force=True)

    assert diff.diff_text != ""
    assert "credit card" in diff.diff_text
    assert diff.forced is True
    assert diff.content_unchanged is False  # forced path, not the plain no-op path


def test_force_has_no_effect_on_a_genuine_content_change(tmp_path):
    """force=True on a page that DID actually change should behave exactly
    like force=False -- forcing only matters when there'd otherwise be
    nothing to process."""
    store = PageStore(tmp_path / "store.json")
    store.put(_stored(1, "<p>Checkout must support credit card payments.</p>"))
    page = _page(2, "<p>Checkout must support credit card and PayPal payments.</p>")

    forced = compute_diff(store, page, force=True)
    unforced = compute_diff(store, page, force=False)

    assert forced.diff_text == unforced.diff_text
    assert forced.forced is False
    assert "PayPal" in forced.diff_text
