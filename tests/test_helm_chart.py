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
