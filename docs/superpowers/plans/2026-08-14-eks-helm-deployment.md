# ChatFit EKS Helm Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-conscious Helm chart that deploys the ChatFit API and Telegram Bot to an existing EKS cluster with fresh EBS-backed persistent storage.

**Architecture:** One chart renders separate single-replica API and Bot Deployments. The API uses `Recreate`, an internal ClusterIP Service, and one retained ReadWriteOnce PVC whose prepared subdirectories map to the application's existing SQLite, Chroma, and cookbook paths; the Bot calls the API only through that Service.

**Tech Stack:** Helm 3/4 templates, Kubernetes `apps/v1` and core `v1` resources, Amazon EBS CSI dynamic provisioning, Python 3.13, pytest, PyYAML, kubectl client-side dry run.

## Global Constraints

- Work only in the isolated `codex/helm-eks-deployment` worktree.
- The EKS cluster, AWS EBS CSI driver, and a usable default or named `StorageClass` already exist.
- `image.repository`, `image.tag`, and `existingSecret` are required render-time values.
- The chart must not build or publish an image and must not migrate local Compose data.
- API and Bot each run exactly one replica; API updates use `Recreate`.
- API exposure is `ClusterIP` only; create no Ingress or public load balancer.
- Provision one fresh `ReadWriteOnce` PVC and retain it across uninstall by default.
- Create no Namespace, StorageClass, Secret, Ingress, IAM role, ECR repository, EBS CSI installation, Role, or RoleBinding.
- Preserve distinct business, checkpoint, and durable-memory SQLite file paths.
- Required Secret keys are `GOOGLE_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `CHATFIT_API_TOKEN`.
- Containers disable privilege escalation, drop all capabilities, and use `RuntimeDefault` seccomp; do not force `runAsNonRoot` while `/root/.iron` remains hard-coded.
- Update `README.md`, `docs/index.html`, and the chart-local README.
- Before completion, an independent subagent must follow `docs/quality.md`; every reported error, failure, or warning must be fixed and re-verified.

## File map

- `tests/test_helm_chart.py`: executable chart contract and render assertions.
- `deploy/helm/chatfit/Chart.yaml`: Helm chart identity and supported Kubernetes floor.
- `deploy/helm/chatfit/values.yaml`: complete public configuration surface with no credentials.
- `deploy/helm/chatfit/templates/_helpers.tpl`: names, labels, selectors, image, and ServiceAccount helpers.
- `deploy/helm/chatfit/templates/serviceaccount.yaml`: unprivileged workload identity with no RBAC bindings.
- `deploy/helm/chatfit/templates/configmap.yaml`: non-sensitive environment settings.
- `deploy/helm/chatfit/templates/pvc.yaml`: fresh dynamically provisioned durable volume and retention policy.
- `deploy/helm/chatfit/templates/api-deployment.yaml`: exclusive API writer, directory initialization, Secret references, mounts, probes, and security.
- `deploy/helm/chatfit/templates/service.yaml`: internal API discovery.
- `deploy/helm/chatfit/templates/bot-deployment.yaml`: Telegram Bot and internal endpoint wiring.
- `deploy/helm/chatfit/templates/tests/test-connection.yaml`: post-install API reachability check using the ChatFit image.
- `deploy/helm/chatfit/templates/NOTES.txt`: concise post-install commands.
- `deploy/helm/chatfit/README.md`: EKS/ECR/Secret/install/operate/troubleshoot guide.
- `README.md`: make Helm deployment discoverable from the project entry point.
- `docs/index.html`: make EKS deployment discoverable from the published site.

---

### Task 1: Establish the chart render contract and foundation resources

**Files:**
- Create: `tests/test_helm_chart.py`
- Create: `deploy/helm/chatfit/Chart.yaml`
- Create: `deploy/helm/chatfit/values.yaml`
- Create: `deploy/helm/chatfit/templates/_helpers.tpl`
- Create: `deploy/helm/chatfit/templates/serviceaccount.yaml`
- Create: `deploy/helm/chatfit/templates/configmap.yaml`
- Create: `deploy/helm/chatfit/templates/pvc.yaml`

**Interfaces:**
- Consumes: Helm executable available on `PATH`; `yaml.safe_load_all`; the required values `image.repository`, `image.tag`, and `existingSecret`.
- Produces: `_render(*extra_args) -> list[dict]`, `_resource(resources, kind, component=None) -> dict`, the stable fullname `<release>-chatfit`, API Service name `<fullname>-api`, PVC name `<fullname>-data`, ConfigMap name `<fullname>-config`, and shared ServiceAccount name `<fullname>`.

- [ ] **Step 1: Write failing tests for required values and foundation resources**

Create `tests/test_helm_chart.py` with these helpers and tests:

```python
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
            if item["metadata"].get("labels", {}).get(
                "app.kubernetes.io/component"
            )
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
    assert not ({"GOOGLE_API_KEY", "TELEGRAM_BOT_TOKEN", "CHATFIT_API_TOKEN"} & config["data"].keys())
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
```

- [ ] **Step 2: Run the focused tests and verify the RED state**

Run:

```bash
uv run pytest tests/test_helm_chart.py -v
```

Expected: FAIL because `deploy/helm/chatfit/Chart.yaml` does not exist, so Helm reports that the chart path is missing.

- [ ] **Step 3: Add chart metadata, values, helpers, ConfigMap, ServiceAccount, and PVC**

Create `Chart.yaml` with `apiVersion: v2`, `name: chatfit`, `type: application`,
`version: 0.1.0`, `appVersion: "0.1.0"`, and `kubeVersion: ">=1.28.0-0"`.

Define this values schema in `values.yaml` without inserting credentials:

```yaml
nameOverride: ""
fullnameOverride: ""
image:
  repository: ""
  tag: ""
  pullPolicy: IfNotPresent
imagePullSecrets: []
existingSecret: ""
serviceAccount:
  create: true
  annotations: {}
  name: ""
persistence:
  storageClass: ""
  accessModes:
    - ReadWriteOnce
  size: 10Gi
  keep: true
config:
  timezone: Asia/Shanghai
  proactiveReviewsEnabled: false
  telegramChatId: ""
  voiceMaxDurationSeconds: 180
  mediaMaxNormalizedBytes: 12582912
  imageMaxPixels: 20000000
  mediaParseConcurrency: 4
  mediaProviderTimeoutSeconds: 45.0
  mediaProviderMaxRetries: 2
  speechProvider: gemini
  imageProvider: gemini
  geminiMediaModel: gemini-3.5-flash-lite
  langfuseHost: https://cloud.langfuse.com
  langfuseCaptureContent: false
secretKeys:
  googleApiKey: GOOGLE_API_KEY
  telegramBotToken: TELEGRAM_BOT_TOKEN
  chatfitApiToken: CHATFIT_API_TOKEN
  langfuseSecretKey: LANGFUSE_SECRET_KEY
  langfusePublicKey: LANGFUSE_PUBLIC_KEY
  observabilityHashKey: OBSERVABILITY_HASH_KEY
  telegramProxy: TELEGRAM_PROXY
  llmProxy: LLM_PROXY
api:
  resources:
    requests: {cpu: 250m, memory: 512Mi}
    limits: {cpu: "1", memory: 1Gi}
  startupProbe: {failureThreshold: 30, periodSeconds: 10}
  readinessProbe: {initialDelaySeconds: 5, periodSeconds: 10}
  livenessProbe: {initialDelaySeconds: 15, periodSeconds: 20}
bot:
  resources:
    requests: {cpu: 100m, memory: 256Mi}
    limits: {cpu: 500m, memory: 512Mi}
  livenessProbe: {initialDelaySeconds: 30, periodSeconds: 30}
nodeSelector: {}
tolerations: []
affinity: {}
```

In `_helpers.tpl`, implement `chatfit.name`, `chatfit.fullname`,
`chatfit.chart`, `chatfit.labels`, `chatfit.selectorLabels`,
`chatfit.serviceAccountName`, `chatfit.image`, and `chatfit.validateValues`.
`chatfit.image` must enforce:

```gotemplate
{{- $repository := required "image.repository is required" .Values.image.repository -}}
{{- $tag := required "image.tag is required" .Values.image.tag -}}
{{- printf "%s:%s" $repository $tag -}}
```

`chatfit.validateValues` must include `chatfit.image` and invoke `required
"existingSecret is required" .Values.existingSecret`, returning an empty
string after validation. Make each rendered resource invoke
`{{- include "chatfit.validateValues" . -}}` near the top, even when that
resource does not otherwise use the image or Secret, so every render validates
the complete install contract.

Render `configmap.yaml` with the exact environment variable names from
`.env.example`, including `TZ`, media settings, provider settings,
`PROACTIVE_REVIEWS_ENABLED`, `TELEGRAM_CHAT_ID`, `LANGFUSE_HOST`, and
`LANGFUSE_CAPTURE_CONTENT`. Render `serviceaccount.yaml` only when
`serviceAccount.create` is true. Render `pvc.yaml` with the `keep` annotation
only when `persistence.keep` is true and omit `storageClassName` when the value
is empty.

- [ ] **Step 4: Run focused tests and lint the foundation**

Run:

```bash
uv run pytest tests/test_helm_chart.py -v
helm lint deploy/helm/chatfit \
  --set image.repository=example/chatfit \
  --set image.tag=test-sha \
  --set existingSecret=chatfit-secrets
```

Expected: all Task 1 tests PASS and Helm reports `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 5: Commit the foundation contract**

```bash
git add tests/test_helm_chart.py deploy/helm/chatfit
git commit -m "feat: add Helm chart foundation"
```

---

### Task 2: Render the stateful API workload and internal Service

**Files:**
- Modify: `tests/test_helm_chart.py`
- Create: `deploy/helm/chatfit/templates/api-deployment.yaml`
- Create: `deploy/helm/chatfit/templates/service.yaml`

**Interfaces:**
- Consumes: `chatfit.fullname`, `chatfit.labels`, `chatfit.selectorLabels`, `chatfit.serviceAccountName`, `chatfit.image`, `<fullname>-config`, `<fullname>-data`, and `.Values.existingSecret` from Task 1.
- Produces: API component label `api`, named container port `http`, Service `<fullname>-api:8000`, and PVC subPaths `iron`, `runtime-data`, `chroma.db`, and `cookbook`.

- [ ] **Step 1: Add failing API and Service behavior tests**

Append these helpers and tests to `tests/test_helm_chart.py`:

```python
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
    mounts = {item["subPath"]: item["mountPath"] for item in api["volumeMounts"] if "subPath" in item}

    assert initializer["command"] == ["sh", "-ec"]
    assert "mkdir -p /storage/iron /storage/runtime-data /storage/chroma.db /storage/cookbook" in initializer["args"][0]
    assert mounts == {
        "iron": "/root/.iron",
        "runtime-data": "/app/data",
        "chroma.db": "/app/chroma.db",
        "cookbook": "/root/Documents/LifeOS/下厨房",
    }
    assert pod_spec["volumes"] == [
        {"name": "data", "persistentVolumeClaim": {"claimName": "contract-chatfit-data"}}
    ]


def test_api_uses_distinct_databases_explicit_secret_refs_and_security_defaults() -> None:
    deployment = _resource(_render(), "Deployment", "api")
    pod_spec = deployment["spec"]["template"]["spec"]
    api = _container(deployment, "api")
    env = _env(api)

    assert env["CHECKPOINTER_DB_PATH"]["value"] == "/app/data/checkpointer.db"
    assert env["USER_MEMORY_DB_PATH"]["value"] == "/app/data/user-memory.db"
    for name in ("GOOGLE_API_KEY", "CHATFIT_API_TOKEN"):
        assert env[name]["valueFrom"]["secretKeyRef"]["name"] == "chatfit-secrets"
        assert env[name]["valueFrom"]["secretKeyRef"].get("optional") is not True
    for name in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY", "OBSERVABILITY_HASH_KEY", "LLM_PROXY"):
        assert env[name]["valueFrom"]["secretKeyRef"]["optional"] is True
    assert pod_spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert api["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }
    assert api["startupProbe"]["tcpSocket"]["port"] == "http"
    assert api["readinessProbe"]["tcpSocket"]["port"] == "http"
    assert api["livenessProbe"]["tcpSocket"]["port"] == "http"
```

- [ ] **Step 2: Run the API tests and verify the RED state**

Run:

```bash
uv run pytest tests/test_helm_chart.py -v
```

Expected: foundation tests PASS; new tests FAIL because no API Deployment or Service is rendered.

- [ ] **Step 3: Implement the API Deployment and Service**

Create `api-deployment.yaml` with component label `api`, fixed `replicas: 1`,
`strategy.type: Recreate`, and a pod template checksum annotation:

```gotemplate
checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
```

Use the shared image for both containers. The init container mounts the PVC at
`/storage` and runs exactly:

```yaml
command: ["sh", "-ec"]
args:
  - mkdir -p /storage/iron /storage/runtime-data /storage/chroma.db /storage/cookbook
```

The API container command, mounts, required and optional Secret refs, ConfigMap
envFrom, TCP probes, resources, container security context, pod seccomp profile,
image pull secrets, node selector, tolerations, and affinity must match the test
contract. Reference the PVC as `<fullname>-data`.

Create `service.yaml` with component label `api`, `type: ClusterIP`, selector
labels identical to the API pod selector, and port 8000 targeting the named
`http` port.

- [ ] **Step 4: Verify the API render and focused tests**

Run:

```bash
uv run pytest tests/test_helm_chart.py -v
helm template contract deploy/helm/chatfit \
  --namespace chatfit \
  --set image.repository=example/chatfit \
  --set image.tag=test-sha \
  --set existingSecret=chatfit-secrets > /tmp/chatfit-api-render.yaml
kubectl apply --dry-run=client --validate=false -f /tmp/chatfit-api-render.yaml
```

Expected: tests PASS; Helm exits 0; kubectl prints `created (dry run)` for every rendered foundation/API resource.

- [ ] **Step 5: Commit the API workload**

```bash
git add tests/test_helm_chart.py deploy/helm/chatfit/templates/api-deployment.yaml deploy/helm/chatfit/templates/service.yaml
git commit -m "feat: deploy stateful ChatFit API with Helm"
```

---

### Task 3: Add the Telegram Bot and installed-release smoke test

**Files:**
- Modify: `tests/test_helm_chart.py`
- Create: `deploy/helm/chatfit/templates/bot-deployment.yaml`
- Create: `deploy/helm/chatfit/templates/tests/test-connection.yaml`
- Create: `deploy/helm/chatfit/templates/NOTES.txt`

**Interfaces:**
- Consumes: API Service `<fullname>-api:8000`, shared image and ConfigMap, required Secret, ServiceAccount, security helpers, and scheduling values.
- Produces: Bot component label `bot`, exact `API_URL`/`API_CLEAR_URL`/`API_RESUME_URL`/`API_PROACTIVE_REVIEW_URL`, and a Helm test Pod annotated `helm.sh/hook: test`.

- [ ] **Step 1: Add failing Bot and Helm test assertions**

Append to `tests/test_helm_chart.py`:

```python
def test_bot_calls_only_the_internal_api_service() -> None:
    deployment = _resource(_render(), "Deployment", "bot")
    bot = _container(deployment, "bot")
    env = _env(bot)
    base = "http://contract-chatfit-api:8000"

    assert deployment["spec"]["replicas"] == 1
    assert bot["command"] == ["python"]
    assert bot["args"] == ["bot.py"]
    assert env["API_URL"]["value"] == f"{base}/chat"
    assert env["API_CLEAR_URL"]["value"] == f"{base}/clear"
    assert env["API_RESUME_URL"]["value"] == f"{base}/resume"
    assert env["API_PROACTIVE_REVIEW_URL"]["value"] == f"{base}/proactive-review"
    for name in ("GOOGLE_API_KEY", "TELEGRAM_BOT_TOKEN", "CHATFIT_API_TOKEN"):
        assert env[name]["valueFrom"]["secretKeyRef"]["name"] == "chatfit-secrets"
    assert env["TELEGRAM_PROXY"]["valueFrom"]["secretKeyRef"]["optional"] is True
    assert bot["livenessProbe"]["exec"]["command"][0:2] == ["python", "-c"]
    assert bot["securityContext"]["capabilities"]["drop"] == ["ALL"]


def test_chart_has_an_internal_api_helm_test() -> None:
    resources = _render()
    test_pod = _resource(resources, "Pod", "test")
    container = test_pod["spec"]["containers"][0]

    assert test_pod["metadata"]["annotations"]["helm.sh/hook"] == "test"
    assert test_pod["metadata"]["annotations"]["helm.sh/hook-delete-policy"] == "before-hook-creation,hook-succeeded"
    assert test_pod["spec"]["restartPolicy"] == "Never"
    assert container["image"].endswith("/chatfit:test-sha")
    assert container["command"] == ["python", "-c"]
    assert "http://contract-chatfit-api:8000/docs" in container["args"][0]


def test_complete_chart_renders_expected_resource_inventory() -> None:
    resources = _render()
    kinds = [resource["kind"] for resource in resources]

    assert kinds.count("Deployment") == 2
    assert kinds.count("Service") == 1
    assert kinds.count("PersistentVolumeClaim") == 1
    assert kinds.count("ServiceAccount") == 1
    assert kinds.count("ConfigMap") == 1
    assert kinds.count("Pod") == 1
    assert not ({"Ingress", "Secret", "StorageClass", "Role", "RoleBinding"} & set(kinds))
```

- [ ] **Step 2: Run the new tests and verify the RED state**

Run:

```bash
uv run pytest tests/test_helm_chart.py -v
```

Expected: previous tests PASS; Bot and test-Pod tests FAIL because their resources do not exist.

- [ ] **Step 3: Implement Bot, test hook, and release notes**

Create `bot-deployment.yaml` with one replica, the shared ConfigMap, required
Secret refs, optional `TELEGRAM_PROXY`, resource/scheduling settings, pod
seccomp, and container security. Set internal URLs using:

```gotemplate
{{- $apiBase := printf "http://%s-api:8000" (include "chatfit.fullname" .) -}}
```

The liveness probe must execute Python and exit successfully only when
`/proc/1/cmdline` contains `bot.py`:

```yaml
exec:
  command:
    - python
    - -c
    - "from pathlib import Path; import sys; sys.exit(0 if b'bot.py' in Path('/proc/1/cmdline').read_bytes() else 1)"
```

Create `templates/tests/test-connection.yaml` using the ChatFit image and an
inline `urllib.request.urlopen` call to
`http://<fullname>-api:8000/docs` with a 10-second timeout. Give it component
label `test`, restart policy `Never`, the shared ServiceAccount, pod/container
security defaults, and hook annotations `test` plus
`before-hook-creation,hook-succeeded`.

Create `NOTES.txt` that prints release-scoped commands for `helm test`,
`kubectl get pods,pvc`, API/Bot logs, and API port-forward. Do not print Secret
contents.

- [ ] **Step 4: Verify the complete chart**

Run:

```bash
uv run pytest tests/test_helm_chart.py -v
helm lint deploy/helm/chatfit \
  --set image.repository=example/chatfit \
  --set image.tag=test-sha \
  --set existingSecret=chatfit-secrets
helm template contract deploy/helm/chatfit \
  --namespace chatfit \
  --set image.repository=example/chatfit \
  --set image.tag=test-sha \
  --set existingSecret=chatfit-secrets > /tmp/chatfit-complete-render.yaml
kubectl apply --dry-run=client --validate=false -f /tmp/chatfit-complete-render.yaml
```

Expected: all chart tests PASS, lint succeeds with zero failures, and every manifest is accepted by client-side dry run.

- [ ] **Step 5: Commit Bot and smoke-test resources**

```bash
git add tests/test_helm_chart.py deploy/helm/chatfit/templates
git commit -m "feat: deploy ChatFit bot and Helm smoke test"
```

---

### Task 4: Document ECR-to-EKS operations and run project-wide gates

**Files:**
- Create: `deploy/helm/chatfit/README.md`
- Modify: `README.md`
- Modify: `docs/index.html`

**Interfaces:**
- Consumes: the final chart values and resource names from Tasks 1–3.
- Produces: copyable ECR build/push, Secret creation, `helm upgrade --install`, `helm test`, observability, upgrade, uninstall, retained-PVC cleanup, and troubleshooting workflows.

- [ ] **Step 1: Write the operational documentation**

Create `deploy/helm/chatfit/README.md` with these concrete variable-based steps:

```bash
AWS_ACCOUNT_ID="123456789012"
AWS_REGION="us-east-1"
ECR_REPOSITORY="chatfit"
IMAGE_TAG="$(git rev-parse --short HEAD)"
IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}"

aws ecr describe-repositories --repository-names "${ECR_REPOSITORY}" --region "${AWS_REGION}" || \
  aws ecr create-repository --repository-name "${ECR_REPOSITORY}" --region "${AWS_REGION}"
aws ecr get-login-password --region "${AWS_REGION}" | \
  docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
docker build -t "${IMAGE_URI}:${IMAGE_TAG}" .
docker push "${IMAGE_URI}:${IMAGE_TAG}"

kubectl create namespace chatfit
kubectl -n chatfit create secret generic chatfit-secrets \
  --from-literal=GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
  --from-literal=TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
  --from-literal=CHATFIT_API_TOKEN="${CHATFIT_API_TOKEN}"

helm upgrade --install chatfit deploy/helm/chatfit \
  --namespace chatfit \
  --set-string image.repository="${IMAGE_URI}" \
  --set-string image.tag="${IMAGE_TAG}" \
  --set-string existingSecret=chatfit-secrets \
  --wait --timeout 10m
helm test chatfit --namespace chatfit
```

Also document an override values file, EBS CSI and StorageClass preflight,
`kubectl get pods,pvc`, logs, port-forward, upgrade, uninstall, the default PVC
retention annotation, deliberate `kubectl delete pvc chatfit-data`, and
troubleshooting for `ImagePullBackOff`, `CreateContainerConfigError`, and
Pending PVCs. State that the initial EBS volume is empty and cookbook/Chroma
data is not migrated.

Add a concise `## EKS / Helm 部署` section to `README.md` linking to the chart
README. Add a visible deployment link containing `deploy/helm/chatfit` to
`docs/index.html` without restructuring the landing page.

- [ ] **Step 2: Review documentation against the approved design**

Read `deploy/helm/chatfit/README.md`, `README.md`, and `docs/index.html` with
the Documentation deliverables section of
`docs/superpowers/specs/2026-08-14-eks-helm-deployment-design.md` beside them.
Confirm every required workflow is present, every example uses the rendered
resource names, no credential value is committed, and both project entry points
link to the chart guide. Human-facing prose is reviewed directly rather than
protected by brittle exact-substring tests.

- [ ] **Step 3: Run existing documentation and chart tests**

Run:

```bash
uv run pytest tests/test_documentation.py tests/test_helm_chart.py -v
```

Expected: all focused tests PASS without warnings.

- [ ] **Step 4: Commit documentation**

```bash
git add deploy/helm/chatfit/README.md README.md docs/index.html
git commit -m "docs: explain EKS Helm deployment"
```

- [ ] **Step 5: Run local final verification before independent review**

Run:

```bash
uv run black tests/test_helm_chart.py tests/test_documentation.py
helm lint deploy/helm/chatfit \
  --set image.repository=example/chatfit \
  --set image.tag=test-sha \
  --set existingSecret=chatfit-secrets
uv run pytest
make quality
git diff --check HEAD~3..HEAD
git status --short
```

Expected: Helm lint reports zero failed charts; pytest reports all selected tests passing; Ruff, Black, MyPy, and Bandit emit no errors or warnings; diff check is empty; worktree is clean.

- [ ] **Step 6: Dispatch the required independent verification subagent**

Give the subagent this exact brief:

```text
In /Users/hjw/Projects/ChatFit/.worktrees/helm-eks-deployment, independently verify the EKS Helm feature. Read AGENTS.md, docs/architecture.md, docs/quality.md, and docs/superpowers/specs/2026-08-14-eks-helm-deployment-design.md. Inspect the branch diff and documentation freshness. Run helm lint with image.repository=example/chatfit, image.tag=test-sha, existingSecret=chatfit-secrets; run helm template piped to kubectl apply --dry-run=client --validate=false; run make verify; run make quality. Treat every error, failure, or warning as a failed verification. Report exact commands, exit codes, warnings, and actionable findings. Do not modify files.
```

Expected: subagent reports every command successful, no warnings, and no stale documentation. If it reports anything else, fix the finding, rerun focused tests, and dispatch verification again until the report is clean.

---

## Plan self-review

- Spec coverage: Tasks 1–3 cover all chart resources, Secret/config/image contracts, storage topology, health, security, failure behavior, and smoke testing; Task 4 covers all three documentation targets and every required verification gate.
- Placeholder scan: command examples use assigned shell variables; there are no deferred implementation markers or unspecified error-handling steps.
- Interface consistency: all tasks use release `contract`, fullname `contract-chatfit`, Service `contract-chatfit-api`, PVC `contract-chatfit-data`, ConfigMap `contract-chatfit-config`, required Secret `chatfit-secrets`, and the same three required image/Secret values.
