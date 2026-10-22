FROM node:22-bookworm-slim AS cli

# Official CLI packages, pinned so image rebuilds keep the same transports.
RUN npm install --global --omit=dev --no-audit --no-fund \
        @openai/codex@0.163.0 @anthropic-ai/claude-code@2.1.308 \
    && codex --version \
    && claude --version \
    && npm cache clean --force

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git libatomic1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=cli /usr/local/bin/node /usr/local/bin/node
COPY --from=cli /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && ln -s ../lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude \
    && codex --version \
    && claude --version

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REVIEWER_LAB_BIND=0.0.0.0 \
    REVIEWER_LAB_PORT=8765 \
    REVIEWER_LAB_DEMO=0 \
    REVIEWER_LAB_CONTAINER=1 \
    REVIEWER_LAB_PUBLIC_ORIGINS=http://127.0.0.1:8765,http://localhost:8765 \
    HOME=/home/lab \
    CODEX_HOME=/home/lab/.codex \
    DISABLE_UPDATES=1 \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

WORKDIR /app
RUN useradd --create-home --uid 10001 lab
COPY --chown=lab:lab . .
RUN mkdir -p /app/runs /home/lab/.codex \
    && chown -R lab:lab /app/runs /home/lab
USER lab
EXPOSE 8765
CMD ["python", "run.py", "ui"]
