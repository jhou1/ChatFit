import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"
README = Path(__file__).resolve().parents[1] / "README.md"
HELM_README = (
    Path(__file__).resolve().parents[1] / "deploy" / "helm" / "chatfit" / "README.md"
)
INTERACTIVE_SHELLS = tuple(
    shell for name in ("bash", "zsh") if (shell := shutil.which(name)) is not None
)


def _bash_block_containing(readme: str, command: str) -> str:
    for block in readme.split("```bash\n")[1:]:
        contents, _, _ = block.partition("\n```")
        if command in contents:
            return contents
    raise AssertionError(f"No bash block contains {command!r}")


def _install_command_stubs(bin_dir: Path) -> None:
    bin_dir.mkdir()
    curl_stub = bin_dir / "curl"
    curl_stub.write_text(
        '#!/bin/sh\nprintf \'curl %s\\n\' "$*" >> "$README_COMMAND_LOG"\n',
        encoding="utf-8",
    )
    curl_stub.chmod(0o755)
    uv_stub = bin_dir / "uv"
    uv_stub.write_text(
        "#!/bin/sh\n"
        "printf 'uv %s | CHECKPOINTER_DB_PATH=%s | USER_MEMORY_DB_PATH=%s\\n' "
        '"$*" "${CHECKPOINTER_DB_PATH-}" "${USER_MEMORY_DB_PATH-}" '
        '>> "$README_COMMAND_LOG"\n',
        encoding="utf-8",
    )
    uv_stub.chmod(0o755)


def _run_interactively(
    shell: str,
    block: str,
    *,
    working_dir: Path,
    dotenv: str,
) -> tuple[subprocess.CompletedProcess[str], str]:
    (working_dir / ".env").write_text(dotenv, encoding="utf-8")
    bin_dir = working_dir / "bin"
    _install_command_stubs(bin_dir)
    command_log = working_dir / "commands.log"
    env = os.environ.copy()
    env.pop("CHATFIT_API_TOKEN", None)
    env.pop("GOOGLE_API_KEY", None)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "PS1": "",
            "PS2": "",
            "README_COMMAND_LOG": str(command_log),
        }
    )
    shell_args = [shell]
    if Path(shell).name == "bash":
        shell_args.extend(("--noprofile", "--norc"))
    else:
        shell_args.append("-f")
    shell_args.append("-i")
    result = subprocess.run(
        shell_args,
        cwd=working_dir,
        env=env,
        input=f"{block}\nexit\n",
        text=True,
        capture_output=True,
        check=False,
    )
    log = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
    return result, log


def _emitted_lines(result: subprocess.CompletedProcess[str]) -> set[str]:
    return {
        line.strip()
        for line in (result.stdout + result.stderr).splitlines()
        if line.strip()
    }


def _run_helm_readme_install_block(
    block: str, tmp_path: Path
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    assert block.strip().startswith("helm install ")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    helm_stub = bin_dir / "helm"
    helm_stub.write_text(
        '#!/bin/sh\nprintf \'helm %s\\n\' "$*" >> "$README_COMMAND_LOG"\n',
        encoding="utf-8",
    )
    helm_stub.chmod(0o755)
    command_log = tmp_path / "commands.log"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "README_COMMAND_LOG": str(command_log),
            "IMAGE_REPOSITORY": "chatfit",
            "IMAGE_URI": "123456789012.dkr.ecr.us-east-1.amazonaws.com/chatfit",
            "IMAGE_TAG": "documentation-test",
        }
    )
    result = subprocess.run(
        ("bash", "-eu"),
        cwd=tmp_path,
        env=env,
        input=block,
        text=True,
        capture_output=True,
        check=False,
    )
    commands = (
        command_log.read_text(encoding="utf-8").splitlines()
        if command_log.exists()
        else []
    )
    return result, commands


def test_helm_readme_persistent_mode_installs_with_pvc(tmp_path) -> None:
    readme = HELM_README.read_text(encoding="utf-8")
    block = _bash_block_containing(readme, "persistence.type=pvc")

    result, commands = _run_helm_readme_install_block(block, tmp_path)

    assert result.returncode == 0, result.stderr
    install = next(
        command for command in commands if command.startswith("helm install ")
    )
    assert (
        "image.repository=123456789012.dkr.ecr.us-east-1.amazonaws.com/chatfit"
        in install
    )
    assert "image.tag=documentation-test" in install
    assert "existingSecret=chatfit-secrets" in install
    assert "persistence.type=pvc" in install
    assert "image.pullPolicy=Never" not in install


def test_helm_readme_experimental_emptydir_installs_loaded_image(tmp_path) -> None:
    readme = HELM_README.read_text(encoding="utf-8")
    block = _bash_block_containing(readme, "persistence.type=emptyDir")

    result, commands = _run_helm_readme_install_block(block, tmp_path)

    assert result.returncode == 0, result.stderr
    install = next(
        command for command in commands if command.startswith("helm install ")
    )
    assert "image.repository=chatfit" in install
    assert "image.tag=documentation-test" in install
    assert "image.pullPolicy=Never" in install
    assert "existingSecret=chatfit-secrets" in install
    assert "persistence.type=emptyDir" in install


@pytest.mark.parametrize("shell", INTERACTIVE_SHELLS, ids=lambda path: Path(path).name)
@pytest.mark.parametrize(
    "dotenv",
    (pytest.param("", id="omitted"), pytest.param("CHATFIT_API_TOKEN=\n", id="empty")),
)
def test_readme_host_curl_stops_before_request_when_api_token_is_empty(
    shell, tmp_path, dotenv
) -> None:
    readme = README.read_text(encoding="utf-8")
    curl_block = _bash_block_containing(readme, "curl -X POST")

    result, command_log = _run_interactively(
        shell,
        curl_block,
        working_dir=tmp_path,
        dotenv=dotenv,
    )

    assert "CHATFIT_API_TOKEN is required; set it in .env." in _emitted_lines(result)
    assert "curl " not in command_log
    assert (
        "编辑 `.env` 不会让直接在宿主机启动的 `api.py` 自动加载这些变量"
        in readme.replace("\n", "")
    )


@pytest.mark.parametrize("shell", INTERACTIVE_SHELLS, ids=lambda path: Path(path).name)
def test_readme_host_curl_passes_configured_bearer_token(shell, tmp_path) -> None:
    readme = README.read_text(encoding="utf-8")
    curl_block = _bash_block_containing(readme, "curl -X POST")

    result, command_log = _run_interactively(
        shell,
        curl_block,
        working_dir=tmp_path,
        dotenv="CHATFIT_API_TOKEN=readme-secret\n",
    )

    assert "CHATFIT_API_TOKEN is required; set it in .env." not in _emitted_lines(
        result
    )
    assert "curl -X POST http://localhost:8000/chat" in command_log
    assert "Authorization: Bearer readme-secret" in command_log


@pytest.mark.parametrize("shell", INTERACTIVE_SHELLS, ids=lambda path: Path(path).name)
@pytest.mark.parametrize(
    "dotenv, missing_name",
    (
        pytest.param(
            "CHATFIT_API_TOKEN=api-secret\n", "GOOGLE_API_KEY", id="google-omitted"
        ),
        pytest.param(
            "GOOGLE_API_KEY=\nCHATFIT_API_TOKEN=api-secret\n",
            "GOOGLE_API_KEY",
            id="google-empty",
        ),
        pytest.param(
            "GOOGLE_API_KEY=google-secret\n", "CHATFIT_API_TOKEN", id="api-omitted"
        ),
        pytest.param(
            "GOOGLE_API_KEY=google-secret\nCHATFIT_API_TOKEN=\n",
            "CHATFIT_API_TOKEN",
            id="api-empty",
        ),
    ),
)
def test_readme_local_uvicorn_skips_startup_when_a_secret_is_empty(
    shell, tmp_path, dotenv, missing_name
) -> None:
    readme = README.read_text(encoding="utf-8")
    uvicorn_block = _bash_block_containing(readme, "uv run uvicorn api:app --reload")

    result, command_log = _run_interactively(
        shell,
        uvicorn_block,
        working_dir=tmp_path,
        dotenv=dotenv,
    )

    error_message = "GOOGLE_API_KEY and CHATFIT_API_TOKEN must both be set in .env."
    assert error_message in _emitted_lines(result)
    assert missing_name in error_message
    assert "uv run uvicorn api:app --reload" not in command_log


@pytest.mark.parametrize("shell", INTERACTIVE_SHELLS, ids=lambda path: Path(path).name)
def test_readme_local_uvicorn_uses_host_paths_when_secrets_are_set(
    shell, tmp_path
) -> None:
    readme = README.read_text(encoding="utf-8")
    uvicorn_block = _bash_block_containing(readme, "uv run uvicorn api:app --reload")

    result, command_log = _run_interactively(
        shell,
        uvicorn_block,
        working_dir=tmp_path,
        dotenv="GOOGLE_API_KEY=google-secret\nCHATFIT_API_TOKEN=api-secret\n",
    )

    assert (
        "GOOGLE_API_KEY and CHATFIT_API_TOKEN must both be set in .env."
        not in _emitted_lines(result)
    )
    assert "uv run uvicorn api:app --reload" in command_log
    assert "CHECKPOINTER_DB_PATH=./runtime-data/checkpointer.db" in command_log
    assert "USER_MEMORY_DB_PATH=./runtime-data/user-memory.db" in command_log


def test_readme_distinguishes_compose_env_file_from_host_processes() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "Compose 会通过 `env_file` 把 `.env` 注入 API 与 Bot 容器" in readme
    assert "`/app/data/user-memory.db` 是容器内路径" in readme


def test_only_bot_service_restarts_after_exhausted_bootstrap_retries() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    assert compose["services"]["bot"]["restart"] == "unless-stopped"
    assert "restart" not in compose["services"]["api"]
