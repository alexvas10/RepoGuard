FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS uv

# Install Node.js 22 for the GitLab MCP sidecar
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-install-project

# GitLab MCP sidecar — pre-install so there's no npx download at runtime
RUN npm install -g @yoda.digital/gitlab-mcp-server

COPY . .

RUN chmod +x scripts/start.sh

ENV PORT=8080

EXPOSE 8080

CMD ["scripts/start.sh"]
