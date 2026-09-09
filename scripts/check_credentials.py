#!/usr/bin/env python3
"""Best-effort validation of the credentials in .env.

Read-only checks against each provider's API -- never mutates anything.
Exits 0 regardless of outcome; this is a diagnostic, not a gate.
"""

from __future__ import annotations

import os
import shutil
import sys

import httpx
from dotenv import load_dotenv

# override=True: .env is authoritative for this service -- see config.py for
# why (a stale parent-shell env var should not silently beat .env).
load_dotenv(override=True)


def check(label: str, ok: bool, detail: str) -> None:
    marker = "OK  " if ok else "FAIL"
    print(f"    [{marker}] {label}: {detail}")


def check_github() -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    target_repo = os.environ.get("TARGET_REPO", "")
    if not token:
        check("GitHub token", False, "GITHUB_TOKEN is not set")
        return
    try:
        resp = httpx.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            check("GitHub token", True, f"authenticated as {resp.json().get('login')}")
        else:
            check("GitHub token", False, f"HTTP {resp.status_code}: {resp.text[:200]}")
            return
    except httpx.HTTPError as exc:
        check("GitHub token", False, str(exc))
        return

    if target_repo and target_repo != "your-org/your-repo":
        try:
            resp = httpx.get(
                f"https://api.github.com/repos/{target_repo}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            check(
                "GitHub target repo access",
                resp.status_code == 200,
                f"{target_repo} -> HTTP {resp.status_code}",
            )
        except httpx.HTTPError as exc:
            check("GitHub target repo access", False, str(exc))
    else:
        check("GitHub target repo access", False, "TARGET_REPO not set to a real owner/repo yet")


def check_confluence() -> None:
    base_url = os.environ.get("CONFLUENCE_BASE_URL", "")
    email = os.environ.get("CONFLUENCE_USER_EMAIL", "")
    token = os.environ.get("CONFLUENCE_API_TOKEN", "")
    space_key = os.environ.get("CONFLUENCE_SPACE_KEY", "")
    if not (base_url and email and token):
        check("Confluence credentials", False, "CONFLUENCE_USER_EMAIL / CONFLUENCE_API_TOKEN not set")
        return
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/rest/api/space/{space_key}",
            auth=(email, token),
            timeout=10.0,
        )
        check(
            "Confluence credentials + space access",
            resp.status_code == 200,
            f"space '{space_key}' -> HTTP {resp.status_code}",
        )
    except httpx.HTTPError as exc:
        check("Confluence credentials", False, str(exc))


def check_email() -> None:
    provider = os.environ.get("EMAIL_PROVIDER", "sendgrid").strip().lower()
    from_address = os.environ.get("EMAIL_FROM_ADDRESS", "")
    to_addresses = os.environ.get("EMAIL_TO_ADDRESSES", "")

    if provider == "postmark":
        api_key = os.environ.get("POSTMARK_API_KEY", "")
        if not api_key:
            check("Postmark server token", False, "POSTMARK_API_KEY is not set")
        else:
            try:
                resp = httpx.get(
                    "https://api.postmarkapp.com/server",
                    headers={"X-Postmark-Server-Token": api_key, "Accept": "application/json"},
                    timeout=10.0,
                )
                check("Postmark server token", resp.status_code == 200, f"HTTP {resp.status_code}")
            except httpx.HTTPError as exc:
                check("Postmark server token", False, str(exc))
    else:
        api_key = os.environ.get("SENDGRID_API_KEY", "")
        if not api_key:
            check("SendGrid API key", False, "SENDGRID_API_KEY is not set")
        else:
            try:
                resp = httpx.get(
                    "https://api.sendgrid.com/v3/scopes",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10.0,
                )
                check("SendGrid API key", resp.status_code == 200, f"HTTP {resp.status_code}")
            except httpx.HTTPError as exc:
                check("SendGrid API key", False, str(exc))

    # Not an API call -- just flagging the two placeholders from .env.example
    # that must be replaced with real values before mail will actually
    # deliver (Postmark/SendGrid both reject sending from an unverified
    # sender signature/domain, regardless of whether the API key is valid).
    check(
        "EMAIL_FROM_ADDRESS is a real, provider-verified sender",
        bool(from_address) and from_address != "confluence-pr-agent@example.com",
        from_address or "not set",
    )
    check(
        "EMAIL_TO_ADDRESSES is a real recipient list",
        bool(to_addresses) and to_addresses != "team@example.com",
        to_addresses or "not set",
    )


_ENGINE_REQUIREMENTS = {
    "claude_code": {
        "binary": "claude",
        "install": "npm install -g @anthropic-ai/claude-code",
        "credential_env": "ANTHROPIC_API_KEY",
    },
    "cursor": {
        "binary": "agent",
        "install": "curl https://cursor.com/install -fsS | bash",
        "credential_env": "CURSOR_API_KEY",
    },
    "copilot": {
        "binary": "copilot",
        "install": "npm install -g @github/copilot",
        "credential_env": "GITHUB_TOKEN",  # already checked by check_github()
    },
    "codex": {
        "binary": "codex",
        "install": "npm install -g @openai/codex",
        "credential_env": "OPENAI_API_KEY",
    },
    "gemini": {
        "binary": "gemini",
        "install": "npm install -g @google/gemini-cli",
        "credential_env": "GEMINI_API_KEY",
    },
    "antigravity": {
        "binary": "agy",
        "install": "see https://antigravity.google/docs/cli/overview",
        "credential_env": None,  # OAuth-only -- run `agy login` interactively once
    },
    "kiro": {
        "binary": "kiro-cli",
        "install": "curl -fsSL https://cli.kiro.dev/install | bash",
        "credential_env": "KIRO_API_KEY",
    },
}


def check_change_engine() -> None:
    engine = os.environ.get("CHANGE_AGENT_ENGINE", "claude_code").strip().lower()
    requirements = _ENGINE_REQUIREMENTS.get(engine)
    if requirements is None:
        check("Change engine", False, f"CHANGE_AGENT_ENGINE={engine!r} is not a known engine")
        return

    binary = requirements["binary"]
    path = shutil.which(binary)
    check(
        f"Change engine CLI ({engine}: '{binary}')",
        bool(path),
        path or f"not found -- install: {requirements['install']}",
    )

    credential_env = requirements["credential_env"]
    if credential_env is None:
        check(f"{engine} auth", True, "OAuth-only -- run `agy login` interactively once, no API key needed")
    elif credential_env != "GITHUB_TOKEN":  # GITHUB_TOKEN already validated above
        key = os.environ.get(credential_env, "")
        check(f"{credential_env} present", bool(key), "set" if key else "not set")


def main() -> int:
    print("Credential checks:")
    check_github()
    check_confluence()
    check_email()
    check_change_engine()
    return 0


if __name__ == "__main__":
    sys.exit(main())
