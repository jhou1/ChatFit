{{/* Expand the name of the chart. */}}
{{- define "chatfit.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Create a stable fully qualified application name. */}}
{{- define "chatfit.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "chatfit.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/* Reserve room for the API Service suffix within a DNS label. */}}
{{- define "chatfit.apiServiceName" -}}
{{- $fullname := include "chatfit.fullname" . -}}
{{- printf "%s-api" ($fullname | trunc 59 | trimSuffix "-") -}}
{{- end }}

{{/* Chart name and version for labels. */}}
{{- define "chatfit.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Common resource labels. */}}
{{- define "chatfit.labels" -}}
helm.sh/chart: {{ include "chatfit.chart" . }}
{{ include "chatfit.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Labels used to select ChatFit pods. */}}
{{- define "chatfit.selectorLabels" -}}
app.kubernetes.io/name: {{ include "chatfit.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* ServiceAccount name, supporting a caller-selected existing account. */}}
{{- define "chatfit.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "chatfit.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Render the required image reference. */}}
{{- define "chatfit.image" -}}
{{- $repository := required "image.repository is required" .Values.image.repository -}}
{{- $tag := required "image.tag is required" .Values.image.tag -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end }}

{{/* Validate the complete install contract before rendering resources. */}}
{{- define "chatfit.validateValues" -}}
{{- $_ := include "chatfit.image" . -}}
{{- $_ := required "existingSecret is required" .Values.existingSecret -}}
{{- $persistenceType := default "pvc" .Values.persistence.type -}}
{{- if not (or (eq $persistenceType "pvc") (eq $persistenceType "emptyDir")) -}}
{{- fail "persistence.type must be one of: pvc, emptyDir" -}}
{{- end -}}
{{- "" -}}
{{- end }}
