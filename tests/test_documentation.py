from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_host_curl_imports_nonempty_api_token_from_dotenv() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "set -a\nsource .env\nset +a" in readme
    assert 'test -n "$CHATFIT_API_TOKEN"' in readme
    assert '-H "Authorization: Bearer $CHATFIT_API_TOKEN"' in readme
    assert (
        "编辑 `.env` 不会让直接在宿主机启动的 `api.py` 自动加载这些变量"
        in readme.replace("\n", "")
    )


def test_readme_local_uvicorn_uses_host_paths_and_required_token() -> None:
    readme = README.read_text(encoding="utf-8")

    expected_local_setup = """mkdir -p runtime-data
export CHATFIT_API_TOKEN='replace-with-an-independent-random-secret'
export CHECKPOINTER_DB_PATH=./runtime-data/checkpointer.db
export USER_MEMORY_DB_PATH=./runtime-data/user-memory.db
uv run uvicorn api:app --reload"""
    assert expected_local_setup in readme


def test_readme_distinguishes_compose_env_file_from_host_processes() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "Compose 会通过 `env_file` 把 `.env` 注入 API 与 Bot 容器" in readme
    assert "`/app/data/user-memory.db` 是容器内路径" in readme
