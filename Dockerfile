FROM ghcr.io/astral-sh/uv:0.5.24-python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1

COPY pyproject.toml README.md ./
COPY src ./src

RUN uv sync --no-dev

CMD ["uv", "run", "poketracker"]
