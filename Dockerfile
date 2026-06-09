FROM python:3.12-slim

# Install Node.js 22 for the GitLab MCP sidecar
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# GitLab MCP sidecar — pre-install so there's no npx download at runtime
RUN npm install -g @yoda.digital/gitlab-mcp-server

COPY . .

RUN chmod +x start.sh

ENV PORT=8080

EXPOSE 8080

CMD ["./start.sh"]
