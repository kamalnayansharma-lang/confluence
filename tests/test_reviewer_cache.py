import json
import subprocess
from pathlib import Path

from confluence_pr_agent.repo.reviewer_cache import get_best_reviewer


def _git(repo_dir: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo_dir), *args], check=True, capture_output=True)


def test_get_best_reviewer_uses_most_frequent_contributor_per_area(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _git(repo_dir, "init")
    _git(repo_dir, "config", "user.name", "Alice")
    _git(repo_dir, "config", "user.email", "alice@example.com")
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "a.py").write_text("one\n")
    _git(repo_dir, "add", ".")
    _git(repo_dir, "commit", "-m", "first")
    (repo_dir / "src" / "a.py").write_text("one\ntwo\n")
    _git(repo_dir, "commit", "-am", "second")

    _git(repo_dir, "config", "user.name", "Bob")
    _git(repo_dir, "config", "user.email", "bob@example.com")
    (repo_dir / "docs").mkdir()
    (repo_dir / "docs" / "guide.md").write_text("guide\n")
    _git(repo_dir, "add", ".")
    _git(repo_dir, "commit", "-m", "docs")

    assert get_best_reviewer(str(repo_dir), ["src/new.py", "src/other.py"]) == "alice@example.com"
    assert json.loads((repo_dir / "repo_contributors_cache.json").read_text())["areas"]["src"] == "alice@example.com"


def test_get_best_reviewer_refreshes_stale_cache(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "repo_contributors_cache.json").write_text(
        json.dumps({"areas": {"src": "old@example.com"}})
    )
    cache = repo_dir / "repo_contributors_cache.json"
    cache.touch()
    old = cache.stat().st_mtime - (8 * 24 * 60 * 60)
    import os
    os.utime(cache, (old, old))

    assert get_best_reviewer(str(repo_dir), ["src/a.py"]) is None