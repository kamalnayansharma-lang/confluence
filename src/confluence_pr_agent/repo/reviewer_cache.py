"""Local contributor-history cache used for automatic PR reviewer selection."""

from __future__ import annotations

import json
import logging
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "repo_contributors_cache.json"
_CACHE_MAX_AGE = timedelta(days=7)
_AUTHOR_MARKER = "__CONTRIBUTOR__"


def _cache_path(repo_dir: str) -> Path:
    return Path(repo_dir) / _CACHE_FILENAME


def _top_level_path(path: str) -> str:
    return path.removeprefix("./").split("/", 1)[0]


def _refresh_cache_if_needed(repo_dir: str) -> dict[str, str]:
    cache_path = _cache_path(repo_dir)
    if cache_path.exists():
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(cache_path.stat().st_mtime, timezone.utc)
        if age <= _CACHE_MAX_AGE:
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                return dict(data.get("areas", {}))
            except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
                logger.warning("Could not read contributor cache %s: %s", cache_path, exc)

    try:
        result = subprocess.run(
            [
                "git", "-C", repo_dir, "log", "--all",
                f"--format={_AUTHOR_MARKER}%aE", "--name-only", "--diff-filter=ACMR", "--",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.warning("Could not refresh contributor cache for %s: %s", repo_dir, exc)
        return {}

    contributors: dict[str, Counter[str]] = defaultdict(Counter)
    contributor = ""
    for line in result.stdout.splitlines():
        if line.startswith(_AUTHOR_MARKER):
            contributor = line[len(_AUTHOR_MARKER):].strip()
        elif line.strip() and contributor:
            contributors[_top_level_path(line.strip())][contributor] += 1

    areas = {area: counts.most_common(1)[0][0] for area, counts in contributors.items() if counts}
    try:
        cache_path.write_text(
            json.dumps(
                {"generated_at": datetime.now(timezone.utc).isoformat(), "areas": areas},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Could not write contributor cache %s: %s", cache_path, exc)
    return areas


def get_best_reviewer(repo_dir: str, changed_files: list[str]) -> str | None:
    """Return the contributor with the strongest history in changed areas."""
    if not changed_files:
        return None
    try:
        areas = _refresh_cache_if_needed(repo_dir)
    except OSError as exc:
        logger.warning("Could not inspect contributor cache for %s: %s", repo_dir, exc)
        return None
    reviewers = Counter(areas.get(_top_level_path(path), "") for path in changed_files)
    reviewers.pop("", None)
    return reviewers.most_common(1)[0][0] if reviewers else None