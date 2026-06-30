FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY pyproject.toml .

RUN uv pip install --system -r pyproject.toml

COPY . .
