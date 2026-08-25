# Builds an image with only the change-engine CLI you've actually selected,
# via --build-arg CHANGE_AGENT_ENGINE=claude_code|cursor|copilot|codex|gemini|antigravity|kiro.
# See docs/change-engines.md for what each engine needs. Note: antigravity is
# OAuth-only -- the binary installs fine here, but headless use still needs
# credentials from a prior `agy login`, which this build cannot do for you
# (see docs/change-engines.md).
ARG CHANGE_AGENT_ENGINE=claude_code

FROM python:3.12-slim

ARG CHANGE_AGENT_ENGINE

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22+ -- required by the claude_code and copilot engines (both are
# npm packages); harmless extra weight if cursor was selected instead.
# Installed from the official binary tarball rather than nodesource's setup
# script or the distro package: nodesource's script silently fell back to
# Debian trixie's own (npm-less) nodejs package on this base image, which
# fails at npm-install time with a confusing "npm: not found".
ARG NODE_VERSION=22.14.0
RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
      amd64) node_arch=x64 ;; \
      arm64) node_arch=arm64 ;; \
      *) echo "unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1; \
    rm /tmp/node.tar.xz; \
    node --version && npm --version

RUN set -eux; \
    case "$CHANGE_AGENT_ENGINE" in \
      claude_code) npm install -g @anthropic-ai/claude-code ;; \
      cursor)      curl https://cursor.com/install -fsS | bash ;; \
      copilot)     npm install -g @github/copilot ;; \
      codex)       npm install -g @openai/codex ;; \
      gemini)      npm install -g @google/gemini-cli ;; \
      antigravity) curl -fsSL https://antigravity.google/cli/install.sh | bash ;; \
      kiro)        curl -fsSL https://cli.kiro.dev/install | bash ;; \
      *) echo "Unknown CHANGE_AGENT_ENGINE: $CHANGE_AGENT_ENGINE" >&2; exit 1 ;; \
    esac
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "confluence_pr_agent.webhook.app:app", "--host", "0.0.0.0", "--port", "8000"]
