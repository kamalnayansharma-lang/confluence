from confluence_pr_agent.config import Settings
from confluence_pr_agent.repo.test_command_detection import detect_lint_command


def test_python_stack_suggests_ruff_and_bandit():
    assert detect_lint_command(["pyproject.toml"]) == "ruff check && bandit -q -r ."


def test_single_repo_lint_setting_flows_into_resolved_target():
    settings = Settings(
        target_repo="acme/widgets",
        target_repo_base_branch="main",
        target_repo_test_command="pytest",
        target_repo_lint_command="ruff check && bandit -q -r .",
    )

    target = settings.resolved_repo_targets[0]

    assert target.lint_command == "ruff check && bandit -q -r ."