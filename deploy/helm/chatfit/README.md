# ChatFit with Helm

This chart supports an existing EKS cluster with the default
`persistence.type: pvc`, or a local Kind cluster with explicit
`persistence.type: emptyDir`. On EKS, it runs one internal FastAPI Deployment
and one Telegram Bot Deployment. The API uses a single `ReadWriteOnce`
EBS-backed PVC, so it intentionally has one replica and uses a `Recreate`
rollout to avoid concurrent SQLite writers. The chart does not create an ECR
repository, Kubernetes Namespace, Secret, StorageClass, EBS CSI driver,
Ingress, or public load balancer.

The Bot also uses a `Recreate` rollout so an upgrade never overlaps two
Telegram long-polling processes.

## Choose a storage mode

| Mode | Helm value | Intended use | Data lifetime |
| --- | --- | --- | --- |
| Persistent PVC (recommended) | `persistence.type=pvc` | EKS and other long-running clusters with a usable StorageClass | Survives Pod replacement; the claim is retained on uninstall by default |
| Experimental `emptyDir` | `persistence.type=emptyDir` | Local Kind smoke tests without a registry or StorageClass | Deleted when the API Pod is deleted or replaced |

Both modes require Helm 3, Docker, a reachable Kubernetes cluster selected by
the current `kubectl` context, and permission to create namespace-scoped
resources. Run all commands from the repository root.

## Create the namespace and required Secret

The chart deliberately consumes an existing Secret rather than managing
credentials. Export all three non-empty values before creating it:

```bash
export GOOGLE_API_KEY="replace-with-google-api-key"
export TELEGRAM_BOT_TOKEN="replace-with-telegram-bot-token"
export CHATFIT_API_TOKEN="$(openssl rand -hex 32)"

kubectl create namespace chatfit

(
  set -eu
  : "${GOOGLE_API_KEY:?GOOGLE_API_KEY must be non-empty}"
  : "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN must be non-empty}"
  : "${CHATFIT_API_TOKEN:?CHATFIT_API_TOKEN must be non-empty}"
  kubectl -n chatfit create secret generic chatfit-secrets \
    --from-literal=GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
    --from-literal=TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
    --from-literal=CHATFIT_API_TOKEN="${CHATFIT_API_TOKEN}"
)
```

The subshell stops before Secret creation if any required value is empty. An
empty `CHATFIT_API_TOKEN` makes both workloads crash and causes `helm install
--wait` to time out. Do not put credential values in a Helm values file or in
Git. If the namespace or Secret already exists, use your approved update
workflow rather than expecting `kubectl create` to replace it.

## Mode 1: persistent PVC storage (recommended)

On EKS, install the
[Amazon EBS CSI driver](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html)
and make a default or named StorageClass available. EKS worker nodes must also
be allowed to pull the ECR image. Confirm those prerequisites first:

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver
kubectl get storageclass
```

Build and push the image. Replace the account and region placeholders with
your own values:

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
```

Install the release with explicit PVC storage. An empty `storageClass` uses the
cluster default; add `--set-string persistence.storageClass=gp3` when selecting
a named class:

```bash
helm install chatfit deploy/helm/chatfit \
  --namespace chatfit \
  --set-string image.repository="${IMAGE_URI}" \
  --set-string image.tag="${IMAGE_TAG}" \
  --set-string existingSecret=chatfit-secrets \
  --set-string persistence.type=pvc \
  --set-string persistence.size=20Gi \
  --wait --timeout 10m
helm test chatfit --namespace chatfit
```

For a Helm-only client-side check before installing, add the same values to:

```bash
helm lint deploy/helm/chatfit \
  --set-string image.repository="${IMAGE_URI}" \
  --set-string image.tag="${IMAGE_TAG}" \
  --set-string existingSecret=chatfit-secrets \
  --set-string persistence.type=pvc
```

The first PVC is empty: this process does not migrate Compose data from
`~/.iron`, `runtime-data`, `chroma.db`, or cookbook Markdown files. The PVC has
`helm.sh/resource-policy: keep` by default, so uninstall retains the claim.

## Mode 2: experimental `emptyDir` on local Kind

This mode is only for local smoke tests. It needs no registry or StorageClass:
build the image on the host and load it directly into every Kind node.

```bash
KIND_CLUSTER="kind"
IMAGE_REPOSITORY="chatfit"
IMAGE_TAG="$(git rev-parse --short HEAD)"

docker build -t "${IMAGE_REPOSITORY}:${IMAGE_TAG}" .
kind load docker-image \
  --name "${KIND_CLUSTER}" \
  "${IMAGE_REPOSITORY}:${IMAGE_TAG}"
kubectl config use-context "kind-${KIND_CLUSTER}"
```

Install the locally loaded image with pulling disabled:

```bash
helm install chatfit deploy/helm/chatfit \
  --namespace chatfit \
  --set-string image.repository="${IMAGE_REPOSITORY}" \
  --set-string image.tag="${IMAGE_TAG}" \
  --set-string image.pullPolicy=Never \
  --set-string existingSecret=chatfit-secrets \
  --set-string persistence.type=emptyDir \
  --wait --timeout 10m
helm test chatfit --namespace chatfit
```

`image.pullPolicy=Never` prevents Kubernetes from contacting a registry. If the
cluster name is not `kind`, obtain it with `kind get clusters` and update
`KIND_CLUSTER`.

**Experimental data warning:** `emptyDir` belongs to the API Pod. Deleting or
replacing that Pod—including during an image upgrade, node reschedule, or
uninstall—permanently removes ChatFit's business records, checkpoints, durable
memory, Chroma data, and cookbook files. Use PVC mode for data that must
survive Pod replacement.

With release name `chatfit`, the rendered resources include
`chatfit-chatfit-api`, `chatfit-chatfit-bot`, and
`chatfit-chatfit-config`; PVC mode also creates `chatfit-chatfit-data`. A
different release name or `fullnameOverride` changes these values.

## Observe and operate

Check the release and workload state, then use logs or a local port-forward to
inspect the internal-only API:

```bash
helm status chatfit --namespace chatfit
kubectl get pods,pvc --namespace chatfit -l app.kubernetes.io/instance=chatfit
kubectl logs deployment/chatfit-chatfit-api --namespace chatfit
kubectl logs deployment/chatfit-chatfit-bot --namespace chatfit
kubectl port-forward service/chatfit-chatfit-api 8000:8000 --namespace chatfit
```

After port-forwarding, open `http://localhost:8000/docs`. When you change an
environment-backed value in the existing Secret, restart both Deployments so
both processes receive it:

```bash
kubectl rollout restart deployment/chatfit-chatfit-api \
  deployment/chatfit-chatfit-bot --namespace chatfit
kubectl rollout status deployment/chatfit-chatfit-api --namespace chatfit
kubectl rollout status deployment/chatfit-chatfit-bot --namespace chatfit
```

## Upgrade an existing release

Use `helm upgrade`, not `helm install`, after the release exists. The API's
`Recreate` strategy causes brief downtime so two API Pods never write SQLite
concurrently.

For persistent PVC mode, build and push the new ECR tag, then run:

```bash
IMAGE_TAG="$(git rev-parse --short HEAD)"
helm upgrade chatfit deploy/helm/chatfit \
  --namespace chatfit \
  --set-string image.repository="${IMAGE_URI}" \
  --set-string image.tag="${IMAGE_TAG}" \
  --set-string existingSecret=chatfit-secrets \
  --set-string persistence.type=pvc \
  --wait --timeout 10m
helm test chatfit --namespace chatfit
```

For experimental `emptyDir`, build and load a new local tag before upgrading:

```bash
IMAGE_TAG="$(git rev-parse --short HEAD)-$(date +%s)"
docker build -t "${IMAGE_REPOSITORY}:${IMAGE_TAG}" .
kind load docker-image \
  --name "${KIND_CLUSTER}" \
  "${IMAGE_REPOSITORY}:${IMAGE_TAG}"

helm upgrade chatfit deploy/helm/chatfit \
  --namespace chatfit \
  --set-string image.repository="${IMAGE_REPOSITORY}" \
  --set-string image.tag="${IMAGE_TAG}" \
  --set-string image.pullPolicy=Never \
  --set-string existingSecret=chatfit-secrets \
  --set-string persistence.type=emptyDir \
  --wait --timeout 10m
helm test chatfit --namespace chatfit
```

This `emptyDir` upgrade replaces the API Pod and permanently deletes its data.

## Uninstall and storage lifecycle

The PVC is annotated `helm.sh/resource-policy: keep` by default
(`persistence.keep: true`), so uninstalling the release preserves the EBS
claim and its application data. Remove it deliberately only when the data is
no longer needed:

```bash
helm uninstall chatfit --namespace chatfit

# Irreversibly requests deletion of the retained claim and its backing storage
# according to the StorageClass reclaim policy.
kubectl delete pvc chatfit-chatfit-data --namespace chatfit
```

Deleting the PVC is a separate, deliberate cleanup step. Check the
StorageClass reclaim policy before doing it because the associated EBS volume
may also be deleted.

In experimental `emptyDir` mode, `helm uninstall` deletes the API Pod and all
data stored in its `emptyDir`; there is no retained PVC to recover.

## Troubleshooting

### `ImagePullBackOff`

Confirm the image tag exists and inspect the Pod events:

```bash
kubectl describe pod -n chatfit -l app.kubernetes.io/component=api
aws ecr describe-images --repository-name "${ECR_REPOSITORY}" \
  --image-ids imageTag="${IMAGE_TAG}" --region "${AWS_REGION}"
```

Correct an incorrect `image.repository` or `image.tag`, and ensure the EKS node
image-pull identity has ECR read permissions. For a non-ECR private registry,
configure `imagePullSecrets` in the override file.

### `CreateContainerConfigError`

This normally means `chatfit-secrets` is absent or is missing one of
`GOOGLE_API_KEY`, `TELEGRAM_BOT_TOKEN`, or `CHATFIT_API_TOKEN`. Inspect events
and the Secret key names (not their values), then create or repair the Secret
and rerun `helm upgrade`:

```bash
kubectl describe pod -n chatfit -l app.kubernetes.io/component=api
kubectl get secret chatfit-secrets --namespace chatfit -o go-template='{{range $k, $v := .data}}{{println $k}}{{end}}'
```

### `CrashLoopBackOff` and `helm install --wait` timeout

If the image is present but both workloads restart, inspect their previous
logs. A Secret key can exist while its value is still empty:

```bash
kubectl logs deployment/chatfit-chatfit-api --namespace chatfit --previous
kubectl logs deployment/chatfit-chatfit-bot --namespace chatfit --previous
kubectl get secret chatfit-secrets --namespace chatfit \
  -o go-template='CHATFIT_API_TOKEN={{len (index .data "CHATFIT_API_TOKEN")}}{{"\n"}}'
```

`CHATFIT_API_TOKEN=0` means the stored value is empty. Repair the existing
Secret using your approved secret-management workflow, then restart both
Deployments. Increasing the Helm timeout does not make an invalid credential
configuration healthy.

### Pending PVC

Inspect the claim and its events. A Pending claim generally indicates that the
EBS CSI driver is unavailable, the selected StorageClass does not exist or has
no provisioner capacity, or the requested topology cannot be scheduled.

```bash
kubectl get pvc chatfit-chatfit-data --namespace chatfit
kubectl describe pvc chatfit-chatfit-data --namespace chatfit
kubectl get storageclass
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver
```

Install or repair the EBS CSI driver, choose a usable StorageClass in
`persistence.storageClass`, and retry the Helm upgrade. Do not manually bind a
claim to an unrelated volume unless that is an intentional migration plan.
