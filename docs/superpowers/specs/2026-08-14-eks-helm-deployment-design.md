# ChatFit EKS Helm Deployment Design

## Objective

Add a Helm chart under `deploy/helm/chatfit/` that deploys ChatFit to an
existing Amazon EKS cluster. The chart will deploy a newly built ChatFit image
from a caller-supplied registry location and provision fresh persistent storage;
it will not build or publish the image and will not migrate local Compose data.

## Deployment assumptions

- The EKS cluster, AWS EBS CSI driver, and a usable default or named
  `StorageClass` already exist.
- `image.repository` and `image.tag` are required install-time values because no
  ECR repository or published ChatFit image exists yet.
- The deployment starts with a new EBS-backed PVC. Existing `~/.iron`,
  `runtime-data`, `chroma.db`, and cookbook Markdown files are not migrated.
- API and Bot each run exactly one replica. The SQLite databases and Chroma
  index do not support concurrent API writers.
- The API is internal-only. The Telegram Bot reaches it through a Kubernetes
  `ClusterIP` Service; the chart creates neither an Ingress nor a public load
  balancer.

## Alternatives considered

### Recommended: two Deployments and one PVC

Run API and Bot as separate Deployments. The API Deployment uses the `Recreate`
strategy and mounts one `ReadWriteOnce` PVC; the Bot has no persistent volume.
This keeps the two processes independently restartable while ensuring that an
upgrade never intentionally runs two API writers at once.

### API StatefulSet plus Bot Deployment

A StatefulSet more strongly advertises that the API is stateful, but adds
volume-claim-template lifecycle and upgrade complexity without providing a
benefit for a fixed single replica. This design is not selected.

### API and Bot as containers in one Pod

A shared Pod reduces the number of Kubernetes resources but couples process
health, rollout, resource sizing, and restarts. This design is not selected.

## Chart layout and resources

The chart will live at `deploy/helm/chatfit/` and contain:

- `Chart.yaml` and `values.yaml` for chart metadata and supported settings;
- `_helpers.tpl` for stable names, labels, selector labels, and the ServiceAccount
  name;
- an API Deployment with `replicas: 1` and `strategy.type: Recreate`;
- a Bot Deployment with `replicas: 1`;
- one API `ClusterIP` Service on port 8000;
- one `ReadWriteOnce` PVC for all API durable data;
- one ServiceAccount shared by the workloads unless an existing account is
  selected;
- one ConfigMap for non-sensitive application settings;
- a Helm test Pod that requests the internal API `/docs` endpoint; and
- `NOTES.txt` plus a chart README for operational instructions.

The chart does not create a Namespace, StorageClass, Secret, Ingress, IAM role,
ECR repository, or EBS CSI installation. Those objects have cluster- or
account-level lifecycles and must remain under the operator's control.

## Image and configuration contract

The following Helm values are required and checked with Helm's `required`
function so an incomplete release fails during rendering:

- `image.repository`
- `image.tag`
- `existingSecret`

The image pull policy and `imagePullSecrets` are configurable. The same ChatFit
image runs the API (`uvicorn api:app --host 0.0.0.0 --port 8000`), Bot
(`python bot.py`), persistent-directory initializer, and Helm test Pod.

The existing Kubernetes Secret must contain these keys:

- `GOOGLE_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `CHATFIT_API_TOKEN`

The chart references each required key explicitly. Optional Secret keys cover
Langfuse credentials, observability hashing, and proxy credentials. Optional
non-secret settings cover timezone, proactive review behavior, media limits,
provider choices, and Langfuse content capture. They are exposed through a
ConfigMap or explicit environment values rather than copied into a generated
Secret. `CHATFIT_API_TOKEN` is sourced from the same Secret key in both
Deployments.

The Bot receives internal URLs derived from the rendered Service name:

- `http://<service>:8000/chat`
- `http://<service>:8000/clear`
- `http://<service>:8000/resume`
- `http://<service>:8000/proactive-review`

## Persistent storage

The chart provisions one dynamically backed PVC with configurable size,
access modes, and `storageClass`. An empty `storageClass` delegates selection to
the cluster default; an operator can name an existing EBS `gp3` StorageClass.
The default access mode is `ReadWriteOnce`.

An init container mounts the volume root and creates these subdirectories before
the API container starts:

- `iron`
- `runtime-data`
- `chroma.db`
- `cookbook`

The API mounts them at the paths expected by the current application:

| PVC subdirectory | Container path | Purpose |
| --- | --- | --- |
| `iron` | `/root/.iron` | Business SQLite database |
| `runtime-data` | `/app/data` | Checkpoint and durable user-memory SQLite files |
| `chroma.db` | `/app/chroma.db` | Chroma recipe index |
| `cookbook` | `/root/Documents/LifeOS/下厨房` | Optional recipe Markdown files |

`CHECKPOINTER_DB_PATH` is `/app/data/checkpointer.db` and
`USER_MEMORY_DB_PATH` is `/app/data/user-memory.db`, keeping the three SQLite
files distinct as required by the application architecture. Creating the empty
`chroma.db` mount directory also lets the application open a fresh empty Chroma
store when no cookbook data exists.

The PVC is annotated with `helm.sh/resource-policy: keep` by default so
uninstalling a release does not delete user data. A value can disable retention
when ephemeral cleanup is explicitly desired. Documentation will explain that
retained PVCs require deliberate manual cleanup.

## Runtime health and security

The API uses TCP startup, readiness, and liveness probes on port 8000 because
the application currently has no dedicated unauthenticated health endpoint.
The Bot uses an exec liveness probe that confirms its Python process is present.
Probe thresholds and resource requests/limits are configurable.

Both Deployments disable privilege escalation, drop all Linux capabilities,
and use the `RuntimeDefault` seccomp profile. The chart will not force
`runAsNonRoot`: the application expands a hard-coded `~/.iron/iron.db` path and
the current container layout expects that home to be `/root`. Changing this is
an application refactor outside the Helm-only scope.

The API is not externally exposed. Operators inspect it with `kubectl
port-forward` when needed. Workloads receive no Kubernetes API permissions, so
the ServiceAccount has no Role or RoleBinding.

## Failure behavior and operational boundaries

- Helm rendering fails when the image repository, image tag, or Secret name is
  absent.
- Pods remain unschedulable with visible Kubernetes events when the EBS CSI
  driver or requested StorageClass is unavailable.
- Pods fail clearly with `CreateContainerConfigError` when a required Secret or
  required Secret key is absent.
- The init container must succeed before the API starts, preventing subPath
  mount failures on a new volume.
- `Recreate` trades brief API downtime for exclusive SQLite ownership during an
  upgrade. The Bot's API retry behavior handles short connection outages.
- Horizontal scaling, high-availability databases, external object storage,
  data migration, public API exposure, and AWS infrastructure provisioning are
  explicitly out of scope.

## Verification

Implementation follows test-first development for the chart contract. A new
pytest module will render the chart and assert observable Kubernetes behavior,
including:

- required values fail with useful messages;
- rendered resource kinds and selectors are complete and consistent;
- the API has one replica, `Recreate`, the expected commands, mounts, probes,
  and internal environment paths;
- the Bot has one replica and all internal API URLs;
- the PVC is `ReadWriteOnce` and retained by default;
- sensitive values are Secret references rather than ConfigMap data; and
- security contexts and Helm test annotations are present.

Final verification consists of:

1. `helm lint` with representative required values;
2. `helm template` with representative required values;
3. `kubectl apply --dry-run=client --validate=false` over rendered YAML;
4. the repository's `make verify` test suite; and
5. the repository's required `make quality` static-analysis suite.

Per repository policy, an independent verification subagent will read
`docs/quality.md`, inspect documentation freshness, and run the required gates.
Any reported error, failure, or warning will be fixed and re-verified before
the work is described as complete.

## Documentation deliverables

The chart README and root documentation will provide:

- EKS/EBS CSI and Helm prerequisites;
- ECR login, repository creation, image build, tag, and push examples without
  assuming a fixed AWS account or region;
- a `kubectl create secret generic` example for required credentials;
- a minimal values file containing image, Secret, persistence, and resource
  settings;
- `helm upgrade --install`, `helm test`, status, logs, port-forward, upgrade,
  and uninstall commands;
- PVC retention and manual cleanup warnings; and
- troubleshooting for image pulls, missing Secrets, and pending EBS claims.

`README.md` and `docs/index.html` will link to the Helm deployment path so the
new deployment method is discoverable and documentation quality remains aligned
with the codebase.
