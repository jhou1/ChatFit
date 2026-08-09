from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def _bash_block_containing(readme: str, command: str) -> str:
    for block in readme.split("```bash\n")[1:]:
        contents, _, _ = block.partition("\n```")
        if command in contents:
            return contents
    raise AssertionError(f"No bash block contains {command!r}")


def _assert_commands_in_order(block: str, commands: tuple[str, ...]) -> None:
    for command in commands:
        assert command in block
    assert [block.index(command) for command in commands] == sorted(
        block.index(command) for command in commands
    )


def test_readme_host_curl_stops_before_request_when_api_token_is_empty() -> None:
    readme = README.read_text(encoding="utf-8")
    curl_block = _bash_block_containing(readme, "curl -X POST")

    ordered_commands = (
        "set -a",
        "source .env",
        "set +a",
        ': "${CHATFIT_API_TOKEN:?set CHATFIT_API_TOKEN in .env}"',
        "curl -X POST",
    )
    _assert_commands_in_order(curl_block, ordered_commands)
    assert '-H "Authorization: Bearer $CHATFIT_API_TOKEN"' in curl_block
    assert 'test -n "$CHATFIT_API_TOKEN"' not in curl_block
    assert (
        "编辑 `.env` 不会让直接在宿主机启动的 `api.py` 自动加载这些变量"
        in readme.replace("\n", "")
    )


def test_readme_local_uvicorn_loads_dotenv_and_fails_closed_on_required_secrets() -> (
    None
):
    readme = README.read_text(encoding="utf-8")
    uvicorn_block = _bash_block_containing(readme, "uv run uvicorn api:app --reload")

    ordered_commands = (
        "mkdir -p runtime-data",
        "set -a",
        "source .env",
        "set +a",
        ': "${GOOGLE_API_KEY:?set GOOGLE_API_KEY in .env}"',
        ': "${CHATFIT_API_TOKEN:?set CHATFIT_API_TOKEN in .env}"',
        "export CHECKPOINTER_DB_PATH=./runtime-data/checkpointer.db",
        "export USER_MEMORY_DB_PATH=./runtime-data/user-memory.db",
        "uv run uvicorn api:app --reload",
    )
    _assert_commands_in_order(uvicorn_block, ordered_commands)
    assert "/app/data" not in uvicorn_block
    assert "export CHATFIT_API_TOKEN=" not in uvicorn_block


def test_readme_distinguishes_compose_env_file_from_host_processes() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "Compose 会通过 `env_file` 把 `.env` 注入 API 与 Bot 容器" in readme
    assert "`/app/data/user-memory.db` 是容器内路径" in readme
