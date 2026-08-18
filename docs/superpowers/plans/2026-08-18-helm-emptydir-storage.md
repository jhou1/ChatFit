# Helm EmptyDir Storage Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit `emptyDir` storage mode so ChatFit can be deployed to a local Kind cluster without a StorageClass while preserving PVC-backed EKS behavior as the default.

**Architecture:** `persistence.type` is the single storage selector and accepts only `pvc` or `emptyDir`. The existing API Deployment, init container, subPath mounts, single replica, and `Recreate` strategy remain unchanged; templates only switch the `data` volume source and omit the PVC resource in `emptyDir` mode.

**Tech Stack:** Helm 3 templates, Kubernetes `PersistentVolumeClaim` and `emptyDir`, Python 3, pytest, PyYAML, Kind, Docker

**Spec:** `docs/superpowers/specs/2026-08-18-helm-emptydir-storage-design.md`

## Global Constraints

- `persistence.type` supports exactly `pvc` and `emptyDir`.
- `persistence.type: pvc` remains the chart default and preserves the existing retained `ReadWriteOnce` PVC behavior.
- `persistence.type: emptyDir` renders no PVC and uses exactly `emptyDir: {}` for the API Pod's `data` volume.
- Unsupported persistence types fail Helm rendering with a message naming both supported values.
- The API remains one replica with a `Recreate` strategy in both modes.
- The `prepare-data` init container and the `iron`, `runtime-data`, `chroma.db`, and `cookbook` subPath mounts remain unchanged.
- Kind documentation must state that all application data disappears when the API Pod is deleted or replaced.
- No local registry, StorageClass, migration, backup, or restore workflow is introduced.

---

### Task 1: Add the tested storage-mode template contract

**Files:**
- Modify: `tests/test_helm_chart.py:64-241`
- Modify: `deploy/helm/chatfit/values.yaml:12-18`
- Modify: `deploy/helm/chatfit/templates/_helpers.tpl:58-63`
- Modify: `deploy/helm/chatfit/templates/pvc.yaml:1-20`
- Modify: `deploy/helm/chatfit/templates/api-deployment.yaml:133-136`

**Interfaces:**
- Consumes: existing `_render(*extra_args)` and `_resource(resources, kind, component)` Helm-test helpers.
- Produces: `.Values.persistence.type` with the exact string values `pvc` and `emptyDir`; invalid input exits Helm rendering with `persistence.type must be one of: pvc, emptyDir`.
- Produces: the existing `data` volume as either `persistentVolumeClaim: {claimName: <fullname>-data}` or `emptyDir: {}`.

- [ ] **Step 1: Write the failing validation and `emptyDir` render tests**

Add these tests after `test_required_install_values_fail_rendering` and after the existing persistent-subdirectory test respectively:

```python
def test_unsupported_persistence_type_fails_rendering() -> None:
    result = _helm(
        "template",
        RELEASE,
        str(CHART),
        *REQUIRED_SET_ARGS,
        "--set",
        "persistence.type=hostPath",
    )

    assert result.returncode != 0
    assert "persistence.type must be one of: pvc, emptyDir" in result.stderr


def test_empty_dir_storage_preserves_mount_contract_without_a_claim() -> None:
    resources = _render("--set", "persistence.type=emptyDir")
    deployment = _resource(resources, "Deployment", "api")
    pod_spec = deployment["spec"]["template"]["spec"]
    initializer = pod_spec["initContainers"][0]
    api = _container(deployment, "api")

    assert not any(item["kind"] == "PersistentVolumeClaim" for item in resources)
    assert pod_spec["volumes"] == [{"name": "data", "emptyDir": {}}]
    assert initializer["volumeMounts"] == [{"name": "data", "mountPath": "/storage"}]
    assert (
        "mkdir -p /storage/iron /storage/runtime-data /storage/chroma.db /storage/cookbook"
        in initializer["args"][0]
    )
    assert {
        item["subPath"]: item["mountPath"]
        for item in api["volumeMounts"]
        if "subPath" in item
    } == {
        "iron": "/root/.iron",
        "runtime-data": "/app/data",
        "chroma.db": "/app/chroma.db",
        "cookbook": "/root/Documents/LifeOS/下厨房",
    }
```

- [ ] **Step 2: Run the focused tests and verify both fail for the intended reasons**

Run:

```bash
uv run pytest \
  tests/test_helm_chart.py::test_unsupported_persistence_type_fails_rendering \
  tests/test_helm_chart.py::test_empty_dir_storage_preserves_mount_contract_without_a_claim \
  -v
```

Expected: two failures. The validation test sees a zero Helm exit code, and the storage test finds a rendered PVC instead of `emptyDir`.

- [ ] **Step 3: Add the public default and exact-value validation**

Add the selector above the existing PVC settings in `values.yaml`:

```yaml
persistence:
  type: pvc
  storageClass: ""
```

Extend `chatfit.validateValues` in `_helpers.tpl` before the final empty string:

```gotemplate
{{- $persistenceType := default "pvc" .Values.persistence.type -}}
{{- if not (or (eq $persistenceType "pvc") (eq $persistenceType "emptyDir")) -}}
{{- fail "persistence.type must be one of: pvc, emptyDir" -}}
{{- end -}}
```

- [ ] **Step 4: Conditionally render the PVC and API volume source**

Wrap the PVC resource, after the validation include, with:

```gotemplate
{{- include "chatfit.validateValues" . -}}
{{- if eq (default "pvc" .Values.persistence.type) "pvc" }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "chatfit.fullname" . }}-data
  labels:
    {{- include "chatfit.labels" . | nindent 4 }}
  {{- if .Values.persistence.keep }}
  annotations:
    helm.sh/resource-policy: keep
  {{- end }}
spec:
  accessModes:
    {{- toYaml .Values.persistence.accessModes | nindent 4 }}
  resources:
    requests:
      storage: {{ .Values.persistence.size }}
  {{- with .Values.persistence.storageClass }}
  storageClassName: {{ . }}
  {{- end }}
{{- end }}
```

Replace the existing API `data` volume source with:

```gotemplate
      volumes:
        - name: data
          {{- if eq (default "pvc" .Values.persistence.type) "pvc" }}
          persistentVolumeClaim:
            claimName: {{ include "chatfit.fullname" . }}-data
          {{- else }}
          emptyDir: {}
          {{- end }}
```

- [ ] **Step 5: Run focused and complete Helm tests**

Run:

```bash
uv run pytest \
  tests/test_helm_chart.py::test_unsupported_persistence_type_fails_rendering \
  tests/test_helm_chart.py::test_empty_dir_storage_preserves_mount_contract_without_a_claim \
  -v
uv run pytest tests/test_helm_chart.py -v
```

Expected: both focused tests pass, followed by the complete Helm test file passing with no warnings.

- [ ] **Step 6: Exercise Helm directly in both modes**

Run:

```bash
helm lint deploy/helm/chatfit \
  --set-string image.repository=chatfit \
  --set-string image.tag=local \
  --set-string existingSecret=chatfit-secrets
helm lint deploy/helm/chatfit \
  --set-string image.repository=chatfit \
  --set-string image.tag=local \
  --set-string existingSecret=chatfit-secrets \
  --set-string persistence.type=emptyDir
helm template chatfit deploy/helm/chatfit \
  --namespace chatfit \
  --set-string image.repository=chatfit \
  --set-string image.tag=local \
  --set-string existingSecret=chatfit-secrets \
  --set-string persistence.type=emptyDir
```

Expected: both lint commands report `0 chart(s) failed`; the template command renders the API `data` volume as `emptyDir: {}` and contains no `kind: PersistentVolumeClaim`.

- [ ] **Step 7: Commit the storage-mode contract**

```bash
git add \
  tests/test_helm_chart.py \
  deploy/helm/chatfit/values.yaml \
  deploy/helm/chatfit/templates/_helpers.tpl \
  deploy/helm/chatfit/templates/pvc.yaml \
  deploy/helm/chatfit/templates/api-deployment.yaml
git commit -m "feat: support Helm emptyDir storage"
```

---

### Task 2: Document a local Kind deployment without a registry or StorageClass

**Files:**
- Modify: `deploy/helm/chatfit/README.md:1-3`
- Modify: `deploy/helm/chatfit/README.md:28-29`
- Verify only: `README.md:307-310`
- Verify only: `docs/index.html:420`

**Interfaces:**
- Consumes: `persistence.type=emptyDir`, `image.pullPolicy=Never`, the existing `existingSecret` value, and an image named `chatfit:<git-short-sha>` loaded into a selected Kind cluster.
- Produces: a copy-pasteable local workflow using Docker, `kind load docker-image`, `kubectl`, and `helm upgrade --install`.

- [ ] **Step 1: Prove the Kind workflow is absent from the current guide**

Run:

```bash
rg -n 'kind load docker-image|persistence.type=emptyDir|image.pullPolicy=Never' \
  deploy/helm/chatfit/README.md
```

Expected: exit code 1 with no matches.

- [ ] **Step 2: Broaden the guide introduction without changing the EKS default**

Change the title to `# ChatFit with Helm`. State immediately below it that the chart supports an existing EKS cluster with default `persistence.type: pvc`, and a local Kind cluster with explicit `persistence.type: emptyDir`. Retain the existing EKS explanation about a single API replica, `Recreate`, ECR, EBS, and resources the chart does not create.

- [ ] **Step 3: Add the complete local Kind workflow before the ECR build section**

Add this section after the storage-prerequisite discussion:

````markdown
## Local Kind deployment with ephemeral storage

For a local smoke test without a registry or StorageClass, build the image on
the host, load it into every node of the selected Kind cluster, and explicitly
select `emptyDir`. Run these commands from the repository root:

```bash
KIND_CLUSTER="kind"
IMAGE_REPOSITORY="chatfit"
IMAGE_TAG="$(git rev-parse --short HEAD)"

docker build -t "${IMAGE_REPOSITORY}:${IMAGE_TAG}" .
kind load docker-image \
  --name "${KIND_CLUSTER}" \
  "${IMAGE_REPOSITORY}:${IMAGE_TAG}"

kubectl create namespace chatfit
kubectl -n chatfit create secret generic chatfit-secrets \
  --from-literal=GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
  --from-literal=TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
  --from-literal=CHATFIT_API_TOKEN="${CHATFIT_API_TOKEN}"

helm upgrade --install chatfit deploy/helm/chatfit \
  --namespace chatfit \
  --set-string image.repository="${IMAGE_REPOSITORY}" \
  --set-string image.tag="${IMAGE_TAG}" \
  --set-string image.pullPolicy=Never \
  --set-string existingSecret=chatfit-secrets \
  --set-string persistence.type=emptyDir \
  --wait --timeout 10m
helm test chatfit --namespace chatfit
```

`image.pullPolicy=Never` makes Kubernetes use the image already loaded into
the Kind nodes. If the cluster name is not `kind`, obtain it with
`kind get clusters` and change `KIND_CLUSTER`.

**Data warning:** `emptyDir` is tied to the API Pod. Deleting or replacing that
Pod, including during an upgrade or uninstall, permanently removes ChatFit's
business records, checkpoints, durable memory, Chroma data, and cookbook files.
Use the default PVC mode for data that must survive Pod replacement.
````

- [ ] **Step 4: Verify the guide and entry-point links**

Run:

```bash
rg -n 'kind load docker-image|persistence.type=emptyDir|image.pullPolicy=Never|Data warning' \
  deploy/helm/chatfit/README.md
rg -n 'deploy/helm/chatfit|EKS / Helm' README.md
rg -n 'deploy/helm/chatfit|Deploy on EKS with Helm' docs/index.html
git diff --check
```

Expected: the chart guide reports all four local-workflow markers; the root README and site index still link to `deploy/helm/chatfit`; `git diff --check` produces no output.

- [ ] **Step 5: Run repository verification and quality gates**

Run:

```bash
make verify
make quality
```

Expected: all tests pass; MyPy, Ruff, Black, and Bandit finish with no error, failure, or warning.

- [ ] **Step 6: Commit the Kind documentation**

```bash
git add deploy/helm/chatfit/README.md
git commit -m "docs: add local Kind Helm deployment"
```

---

### Task 3: Run the required independent verification

**Files:**
- Verify: `docs/quality.md`
- Verify: every file changed since the feature branch base

**Interfaces:**
- Consumes: the commits from Tasks 1 and 2 and the repository quality contract in `docs/quality.md`.
- Produces: an independent verification report containing command exit codes, test counts, warnings, documentation-freshness findings, and final worktree status.

- [ ] **Step 1: Dispatch a fresh verification subagent**

Give the subagent this exact assignment:

```text
Independently verify the Helm emptyDir change in the existing feature worktree.
Read docs/quality.md and the approved spec and plan. Do not edit files. Run the
focused emptyDir tests, the entire tests/test_helm_chart.py file, Helm lint for
both default pvc and persistence.type=emptyDir, Helm template for both modes,
make verify, make quality, git diff --check against the branch base, and git
status --short. Confirm default PVC behavior, no PVC plus emptyDir: {} in local
mode, invalid-type failure, unchanged init/subPath mounts, and that README.md,
docs/index.html, and deploy/helm/chatfit/README.md are current. Report every
error, failure, warning, skipped check, and exact exit status.
```

- [ ] **Step 2: Evaluate the independent report**

Expected: every command exits 0, all tests pass, there are no warnings, the documentation is current, and the worktree contains no uncommitted changes. If the report contains any error, failure, or warning, return to the owning task, add or adjust a failing regression test when behavior changes, fix the issue, commit it, and dispatch a fresh independent verifier again.

- [ ] **Step 3: Record the final branch state for handoff**

Run:

```bash
git log --oneline --decorate -5
git status --short
```

Expected: the design, implementation plan, storage-mode implementation, and Kind guide commits appear in history; status produces no output.
