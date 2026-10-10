# 多階段：runtime 必須是最後一個 stage（hl deploy 預設 build 它）
# 在 m1 跑測試：docker build --target test .
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM base AS test
# osmium wheel 在 bookworm-slim 缺 libexpat.so.1（只有測試用到 osmium，runtime 不需要）
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-install-project
COPY . .
RUN uv sync --frozen
RUN uv run --no-sync pytest -q

FROM base AS runtime
COPY . .
RUN uv sync --frozen --no-dev
CMD exec uv run --no-sync uvicorn engine.app:app --host 0.0.0.0 --port "$PORT"
