import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "chatfit"
RELEASE = "contract"
REQUIRED_SET_ARGS = (
    "--set",
    "image.repository=123456789012.dkr.ecr.us-east-1.amazonaws.com/chatfit",
    "--set",
    "image.tag=test-sha",
    "--set",
    "existingSecret=chatfit-secrets",
)


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail("helm must be installed to validate the deployment chart")
    return subprocess.run(
        (helm, *args),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _render(*extra_args: str) -> list[dict[str, Any]]:
    result = _helm(
        "template",
        RELEASE,
        str(CHART),
        "--namespace",
        "chatfit",
        *REQUIRED_SET_ARGS,
        *extra_args,
    )
    assert result.returncode == 0, result.stderr
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def _resource(
    resources: list[dict[str, Any]], kind: str, component: str | None = None
) -> dict[str, Any]:
    matches = [item for item in resources if item["kind"] == kind]
    if component is not None:
        matches = [
            item
            for item in matches
            if item["metadata"].get("labels", {}).get("app.kubernetes.io/component")
            == component
        ]
    assert len(matches) == 1, (kind, component, matches)
    return matches[0]


@pytest.mark.parametrize(
    "set_args, expected",
    (
        (
            ("--set", "image.tag=test-sha", "--set", "existingSecret=secrets"),
            "image.repository is required",
        ),
        (
            (
                "--set",
                "image.repository=example/chatfit",
                "--set",
                "existingSecret=secrets",
            ),
            "image.tag is required",
        ),
        (
            (
                "--set",
                "image.repository=example/chatfit",
                "--set",
                "image.tag=test-sha",
            ),
            "existingSecret is required",
        ),
    ),
)
def test_required_install_values_fail_rendering(set_args, expected) -> None:
    result = _helm("template", RELEASE, str(CHART), *set_args)

    assert result.returncode != 0
    assert expected in result.stderr


def test_foundation_resources_use_safe_defaults() -> None:
    resources = _render()
    account = _resource(resources, "ServiceAccount")
    config = _resource(resources, "ConfigMap")
    claim = _resource(resources, "PersistentVolumeClaim")

    assert account["metadata"]["name"] == "contract-chatfit"
    assert config["metadata"]["name"] == "contract-chatfit-config"
    assert config["data"]["TZ"] == "Asia/Shanghai"
    assert config["data"]["MEDIA_EPHEMERAL_DIRECTORY"] == "/tmp/chatfit-media"
    assert config["data"]["PROACTIVE_REVIEWS_ENABLED"] == "false"
    assert config["data"]["MEDIA_MAX_NORMALIZED_BYTES"] == "12582912"
    assert config["data"]["IMAGE_MAX_PIXELS"] == "20000000"
    assert not (
        {"GOOGLE_API_KEY", "TELEGRAM_BOT_TOKEN", "CHATFIT_API_TOKEN"}
        & config["data"].keys()
    )
    assert claim["metadata"]["name"] == "contract-chatfit-data"
    assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert claim["spec"]["resources"]["requests"]["storage"] == "10Gi"
    assert "storageClassName" not in claim["spec"]


def test_persistence_and_service_account_overrides_render() -> None:
    resources = _render(
        "--set",
        "persistence.storageClass=gp3",
        "--set",
        "persistence.keep=false",
        "--set",
        "serviceAccount.create=false",
        "--set",
        "serviceAccount.name=chatfit-runtime",
    )
    claim = _resource(resources, "PersistentVolumeClaim")

    assert claim["spec"]["storageClassName"] == "gp3"
    assert "helm.sh/resource-policy" not in claim["metadata"].get("annotations", {})
    assert not any(item["kind"] == "ServiceAccount" for item in resources)


def _container(workload: dict[str, Any], name: str) -> dict[str, Any]:
    containers = workload["spec"]["template"]["spec"]["containers"]
    return next(item for item in containers if item["name"] == name)


def _env(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in container["env"]}


def test_api_is_an_exclusive_single_writer_with_internal_service() -> None:
    resources = _render()
    deployment = _resource(resources, "Deployment", "api")
    service = _resource(resources, "Service", "api")
    pod_spec = deployment["spec"]["template"]["spec"]
    api = _container(deployment, "api")

    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert deployment["spec"]["selector"]["matchLabels"] == service["spec"]["selector"]
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 8000, "protocol": "TCP", "targetPort": "http"}
    ]
    assert api["image"].endswith("/chatfit:test-sha")
    assert api["command"] == ["uvicorn"]
    assert api["args"] == ["api:app", "--host", "0.0.0.0", "--port", "8000"]
    assert api["ports"] == [{"containerPort": 8000, "name": "http", "protocol": "TCP"}]
    assert pod_spec["serviceAccountName"] == "contract-chatfit"


def test_api_prepares_and_mounts_all_persistent_subdirectories() -> None:
    deployment = _resource(_render(), "Deployment", "api")
    pod_spec = deployment["spec"]["template"]["spec"]
    initializer = pod_spec["initContainers"][0]
    api = _container(deployment, "api")
    mounts = {
        item["subPath"]: item["mountPath"]
        for item in api["volumeMounts"]
        if "subPath" in item
    }

    assert initializer["command"] == ["sh", "-ec"]
    assert (
        "mkdir -p /storage/iron /storage/runtime-data /storage/chroma.db /storage/cookbook"
        in initializer["args"][0]
    )
    assert mounts == {
        "iron": "/root/.iron",
        "runtime-data": "/app/data",
        "chroma.db": "/app/chroma.db",
        "cookbook": "/root/Documents/LifeOS/下厨房",
    }
    assert pod_spec["volumes"] == [
        {
            "name": "data",
            "persistentVolumeClaim": {"claimName": "contract-chatfit-data"},
        }
    ]


def test_api_uses_distinct_databases_explicit_secret_refs_and_security_defaults() -> (
    None
):
    deployment = _resource(_render(), "Deployment", "api")
    pod_spec = deployment["spec"]["template"]["spec"]
    api = _container(deployment, "api")
    env = _env(api)

    assert env["CHECKPOINTER_DB_PATH"]["value"] == "/app/data/checkpointer.db"
    assert env["USER_MEMORY_DB_PATH"]["value"] == "/app/data/user-memory.db"
    for name in ("GOOGLE_API_KEY", "CHATFIT_API_TOKEN"):
        assert env[name]["valueFrom"]["secretKeyRef"]["name"] == "chatfit-secrets"
        assert env[name]["valueFrom"]["secretKeyRef"].get("optional") is not True
    for name in (
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "OBSERVABILITY_HASH_KEY",
        "LLM_PROXY",
    ):
        assert env[name]["valueFrom"]["secretKeyRef"]["optional"] is True
    assert pod_spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert api["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }
    assert api["startupProbe"]["tcpSocket"]["port"] == "http"
    assert api["readinessProbe"]["tcpSocket"]["port"] == "http"
    assert api["livenessProbe"]["tcpSocket"]["port"] == "http"
