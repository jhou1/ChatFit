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

## Prerequisites

- An EKS cluster selected by your current `kubectl` context, plus Helm 3,
  Docker, the AWS CLI, and permission to push to ECR and create namespace-scoped
  resources.
- The [Amazon EBS CSI driver](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html)
  installed in the cluster and a usable default or named StorageClass. The
  default `persistence.storageClass` is empty, which uses the cluster default.
- EKS worker nodes (or their configured image-pull identity) permitted to pull
  the ECR image. The credentials used for `docker push` are not automatically
  available to worker nodes.

Check the storage prerequisites before installing. Select a StorageClass name
such as `gp3` in an override file when the cluster has no suitable default.

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver
kubectl get storageclass
```

The first EBS volume is empty. This deployment does **not** migrate existing
Compose data: `~/.iron`, `runtime-data`, `chroma.db`, and cookbook Markdown
files are not copied to EKS.

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
kubectl config use-context "kind-${KIND_CLUSTER}"

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
business records, checkpoints, durable memory, Chroma data, and cookbook
files. Use the default PVC mode for data that must survive Pod replacement.

## Build and push an ECR image

Run these commands from the repository root. They use placeholder account and
region values; replace them with your own values and never put credentials in a
values file or in Git.

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

## Create the namespace and required Secret

Export the three values in your shell or retrieve them from your approved
secret manager. The generic Secret name below must match `existingSecret`.

```bash
kubectl create namespace chatfit
kubectl -n chatfit create secret generic chatfit-secrets \
  --from-literal=GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
  --from-literal=TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
  --from-literal=CHATFIT_API_TOKEN="${CHATFIT_API_TOKEN}"
```

Do not paste secret values into shell history, documentation, or Helm values.
For an existing namespace or Secret, use your team's approved update workflow
rather than expecting `kubectl create` to replace it.

## Configure and install

Create an optional local override file when you need to select an EBS
StorageClass, increase the PVC size, or adjust resource requests and limits:

```yaml
# chatfit-values.yaml -- keep this file free of credential values
image:
  repository: "123456789012.dkr.ecr.us-east-1.amazonaws.com/chatfit"
  tag: "replace-with-image-tag"
existingSecret: chatfit-secrets
persistence:
  storageClass: gp3
  size: 20Gi
api:
  resources:
    requests: {cpu: 250m, memory: 512Mi}
    limits: {cpu: "1", memory: 1Gi}
bot:
  resources:
    requests: {cpu: 100m, memory: 256Mi}
    limits: {cpu: 500m, memory: 512Mi}
```

The minimal installation below supplies the required values directly. To use
the override file, add `-f chatfit-values.yaml`; command-line `--set-string`
values take precedence over it.

Before installing, confirm that `kubectl` selects the intended, reachable EKS
cluster, then ask that cluster's API server to validate the rendered objects:

```bash
kubectl config current-context
helm template chatfit deploy/helm/chatfit \
  --namespace chatfit \
  --set-string image.repository="${IMAGE_URI}" \
  --set-string image.tag="${IMAGE_TAG}" \
  --set-string existingSecret=chatfit-secrets | \
  kubectl apply --dry-run=server --namespace chatfit -f -
```

This target-context validation requires network access and credentials for the
selected EKS cluster. Local or offline checks such as `helm lint`, `helm
template`, or client-side Kubernetes dry-runs can catch rendering problems but
cannot validate admission against the target cluster.

```bash
helm upgrade --install chatfit deploy/helm/chatfit \
  --namespace chatfit \
  --set-string image.repository="${IMAGE_URI}" \
  --set-string image.tag="${IMAGE_TAG}" \
  --set-string existingSecret=chatfit-secrets \
  --wait --timeout 10m
helm test chatfit --namespace chatfit
```

With release name `chatfit`, the rendered resources are `chatfit-chatfit-api`,
`chatfit-chatfit-bot`, `chatfit-chatfit-data`, and
`chatfit-chatfit-config`. A different release name or `fullnameOverride`
changes these values. For a very long override, the API Service reserves room
for its `-api` suffix within the 63-character DNS-label limit, and every
in-cluster Service consumer uses that rendered name.

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

After port-forwarding, open `http://localhost:8000/docs`. For a normal image or
configuration update, repeat the install command with the desired new values;
Helm performs an upgrade. The API's `Recreate` strategy causes brief API
downtime during the rollout so two API Pods do not write SQLite concurrently.
When you change an environment-backed value in the existing Secret, restart
both Deployments so both processes receive it:

```bash
kubectl rollout restart deployment/chatfit-chatfit-api \
  deployment/chatfit-chatfit-bot --namespace chatfit
kubectl rollout status deployment/chatfit-chatfit-api --namespace chatfit
kubectl rollout status deployment/chatfit-chatfit-bot --namespace chatfit
```

```bash
IMAGE_TAG="$(git rev-parse --short HEAD)"
helm upgrade chatfit deploy/helm/chatfit \
  --namespace chatfit \
  --set-string image.repository="${IMAGE_URI}" \
  --set-string image.tag="${IMAGE_TAG}" \
  --set-string existingSecret=chatfit-secrets \
  --wait --timeout 10m
helm test chatfit --namespace chatfit
```

## Uninstall and retained storage

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
