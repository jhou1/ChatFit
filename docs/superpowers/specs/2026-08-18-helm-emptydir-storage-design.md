# Helm EmptyDir Storage Mode Design

## Objective

Allow the existing ChatFit Helm chart to run on a local Kind cluster that has
no StorageClass by explicitly selecting ephemeral `emptyDir` storage. Preserve
the current PVC-backed EKS deployment as the default.

## Public configuration

Add one value:

```yaml
persistence:
  type: pvc
```

Supported values are exactly:

- `pvc`: render the existing retained `ReadWriteOnce` PVC and mount it into the
  API Pod. This remains the default.
- `emptyDir`: render no PVC and mount `emptyDir: {}` as the API Pod's `data`
  volume.

Any other value fails Helm rendering with a message that names the supported
values. Existing `storageClass`, `accessModes`, `size`, and `keep` values apply
only when `type` is `pvc`; they are ignored in `emptyDir` mode.

## Workload behavior

The API Deployment retains its single replica and `Recreate` strategy in both
modes. The `prepare-data` init container remains unchanged and creates the
same four directories in the selected volume:

- `iron`
- `runtime-data`
- `chroma.db`
- `cookbook`

The API container keeps the same subPath mounts and distinct SQLite paths.
Only the source of the `data` volume changes:

```yaml
# pvc mode
persistentVolumeClaim:
  claimName: <release>-chatfit-data

# emptyDir mode
emptyDir: {}
```

Because `emptyDir` belongs to one Pod, all business data, checkpoints, durable
memory, Chroma data, and cookbook files disappear when that Pod is deleted or
replaced. The chart and local Kind instructions must state this prominently.

## Template changes

- `values.yaml` adds `persistence.type: pvc`.
- `_helpers.tpl` validates the value as part of `chatfit.validateValues` so all
  render paths fail consistently for an invalid type.
- `pvc.yaml` renders only in `pvc` mode.
- `api-deployment.yaml` conditionally renders either the PVC source or
  `emptyDir: {}` for the existing `data` volume.
- No other resource or workload topology changes.

## Verification

Tests must demonstrate:

1. The default render still contains one retained `ReadWriteOnce` PVC and a
   PVC-backed API volume.
2. `persistence.type=emptyDir` renders no PVC and produces exactly one
   `data` volume with `emptyDir: {}` while preserving the initializer and all
   four API subPath mounts.
3. An unsupported persistence type fails rendering with a useful error.
4. All existing Helm chart contracts remain green.

Final verification runs focused Helm tests, Helm lint/template in both modes,
the repository's `make verify`, and required `make quality`. An independent
subagent must follow `docs/quality.md` and report any error, failure, warning,
or stale documentation.

## Documentation

The chart README gains a local Kind section showing:

- a locally built and Kind-loaded image;
- `image.pullPolicy: Never`;
- `persistence.type: emptyDir`;
- installation with the existing Secret; and
- the destructive lifecycle warning for Pod replacement and uninstall.

The EKS instructions continue to use the default `pvc` mode. No local registry,
StorageClass, data migration, backup, or restore workflow is added.
