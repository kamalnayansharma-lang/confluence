"""Best-effort test-command detection from a repo's root file listing.

Used by the /ui/config repeating repo-config editor to pre-fill a sensible
default (see ui/routes.py's detect_test_command_route) instead of leaving
the field blank or wrong, which only ever surfaces as a real pipeline run
failing at the test-gate step. Detect-and-prefill in the UI, not re-detect
on every pipeline run -- see the multi-repo plan's reasoning: a value the
user can see and override stays predictable, silently re-guessing it fresh
every run does not.

Order matters -- first match wins, for a repo that happens to carry more
than one marker (e.g. a Python backend with a small bundled npm tool).
"""

from __future__ import annotations

_MARKERS: list[tuple[str, str]] = [
    ("pom.xml", "mvn test"),
    ("build.gradle", "./gradlew test"),
    ("build.gradle.kts", "./gradlew test"),
    ("go.mod", "go test ./..."),
    ("Cargo.toml", "cargo test"),
    ("package.json", "npm test"),
    ("pyproject.toml", "pytest"),
    ("requirements.txt", "pytest"),
    ("setup.py", "pytest"),
    ("Gemfile", "bundle exec rspec"),
]

_LINT_MARKERS: list[tuple[str, str]] = [
    ("pom.xml", "mvn verify"),
    ("build.gradle", "./gradlew check"),
    ("build.gradle.kts", "./gradlew check"),
    ("go.mod", "golangci-lint run"),
    ("Cargo.toml", "cargo clippy -- -D warnings"),
    ("package.json", "npm run lint"),
    ("pyproject.toml", "ruff check && bandit -q -r ."),
    ("requirements.txt", "ruff check && bandit -q -r ."),
    ("setup.py", "ruff check && bandit -q -r ."),
    ("Gemfile", "bundle exec rubocop"),
]


def detect_test_command(root_files: list[str]) -> str | None:
    names = set(root_files)
    for marker, command in _MARKERS:
        if marker in names:
            return command
    return None


def detect_lint_command(root_files: list[str]) -> str | None:
    names = set(root_files)
    for marker, command in _LINT_MARKERS:
        if marker in names:
            return command
    return None


# Same marker-file idiom as _MARKERS above, for prefilling RepoTarget.
# tech_stack (config.py / ui/config.html) instead of test_command. Order
# matters the same way -- first match wins.
_STACK_MARKERS: list[tuple[str, str]] = [
    ("pom.xml", "Java + Maven"),
    ("build.gradle", "Java/Kotlin + Gradle"),
    ("build.gradle.kts", "Kotlin + Gradle"),
    ("go.mod", "Go"),
    ("Cargo.toml", "Rust"),
    ("package.json", "Node.js"),
    ("pyproject.toml", "Python"),
    ("requirements.txt", "Python"),
    ("setup.py", "Python"),
    ("Gemfile", "Ruby"),
]


def detect_tech_stack(root_files: list[str]) -> str | None:
    names = set(root_files)
    for marker, stack in _STACK_MARKERS:
        if marker in names:
            return stack
    return None
