"""Guard test for deploy/ kustomize manifests (Phase 1c Task 5).

Keeps the k3s manifests honest against the cluster's binding constraints
(see docs/plans/2026-07-16-phase1c-remote-deploy-design.md §5):

- SQLite/CSV single-writer -> Deployment must be replicas=1, strategy=Recreate,
  and the PVC must be ReadWriteOnce.
- Kyverno guardrails: no `:latest` image tag anywhere; every container has
  both resources.requests and resources.limits.
- Task 4's non-root container (uid/gid 10001) needs a matching pod
  securityContext (runAsUser/runAsGroup/fsGroup all 10001) or a fresh
  Longhorn PVC crash-loops on startup.
- PVC must use the longhorn-r2 StorageClass.
- Namespace must carry the ghcr-pull=enabled label (Kyverno GHCR-pull-secret
  trigger).
- Service must front the container's actual port (8080).

Prefers parsing the *rendered* `kubectl kustomize deploy/` output (proves the
kustomization itself is valid, not just that the individual yaml files
happen to contain the right keys) but falls back to parsing the raw yaml
files directly if kubectl/kustomize isn't available in the environment
running the test -- never skipped either way.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"


def _render_via_kubectl() -> list[dict] | None:
    """Return parsed docs from `kubectl kustomize deploy/`, or None if kubectl
    isn't available. Raises (does not swallow) if kubectl IS available but the
    kustomization fails to render -- that's a real bug, not an environment gap."""
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        return None
    result = subprocess.run(
        [kubectl, "kustomize", str(DEPLOY_DIR)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"`kubectl kustomize deploy/` failed (exit {result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _render_via_raw_files() -> list[dict]:
    """Fallback: parse every yaml file in deploy/ directly (excluding the
    kustomization/argocd-application meta-manifests, which aren't plain k8s
    resources applied via this kustomization)."""
    docs = []
    for path in sorted(DEPLOY_DIR.glob("*.yaml")):
        if path.name in ("kustomization.yaml", "argocd-application.yaml"):
            continue
        docs.extend(doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc)
    return docs


@pytest.fixture(scope="module")
def manifests() -> list[dict]:
    docs = _render_via_kubectl()
    if docs is None:
        docs = _render_via_raw_files()
    assert docs, "no manifests were parsed from deploy/"
    return docs


def _by_kind(manifests: list[dict], kind: str) -> dict:
    matches = [m for m in manifests if m.get("kind") == kind]
    assert len(matches) == 1, f"expected exactly one {kind}, found {len(matches)}"
    return matches[0]


def test_deploy_dir_exists():
    assert DEPLOY_DIR.is_dir()


def test_kustomization_exists():
    assert (DEPLOY_DIR / "kustomization.yaml").is_file()


def test_argocd_application_exists():
    assert (DEPLOY_DIR / "argocd-application.yaml").is_file()


def test_deployment_replicas_and_strategy(manifests):
    deployment = _by_kind(manifests, "Deployment")
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"


def test_no_image_uses_latest_tag(manifests):
    for doc in manifests:
        containers = (
            doc.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        for container in containers:
            image = container.get("image", "")
            assert image, f"container {container.get('name')} has no image set"
            assert not image.endswith(":latest"), (
                f"container {container.get('name')} uses :latest ({image}) -- "
                "Kyverno blocks this cluster-wide"
            )


def test_every_container_has_requests_and_limits(manifests):
    for doc in manifests:
        containers = (
            doc.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        for container in containers:
            resources = container.get("resources", {})
            assert "requests" in resources and resources["requests"], (
                f"container {container.get('name')} missing resources.requests"
            )
            assert "limits" in resources and resources["limits"], (
                f"container {container.get('name')} missing resources.limits"
            )


def test_pod_security_context_matches_container_uid(manifests):
    deployment = _by_kind(manifests, "Deployment")
    sc = deployment["spec"]["template"]["spec"]["securityContext"]
    assert sc["runAsUser"] == 10001
    assert sc["runAsGroup"] == 10001
    assert sc["fsGroup"] == 10001


def test_pvc_storage_class_and_access_mode(manifests):
    pvc = _by_kind(manifests, "PersistentVolumeClaim")
    assert pvc["spec"]["storageClassName"] == "longhorn-r2"
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]


def test_pvc_has_longhorn_backup_group_label(manifests):
    pvc = _by_kind(manifests, "PersistentVolumeClaim")
    labels = pvc["metadata"].get("labels", {})
    assert labels.get("recurring-job-group.longhorn.io/app-data") == "enabled"


def test_namespace_has_ghcr_pull_label(manifests):
    namespace = _by_kind(manifests, "Namespace")
    labels = namespace["metadata"].get("labels", {})
    assert labels.get("ghcr-pull") == "enabled"


def test_service_targets_container_port(manifests):
    service = _by_kind(manifests, "Service")
    ports = service["spec"]["ports"]
    assert any(p.get("targetPort") == 8080 for p in ports)


def test_deployment_uses_envfrom_auth_secret(manifests):
    deployment = _by_kind(manifests, "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    secret_refs = [
        ref["secretRef"]["name"]
        for ref in container.get("envFrom", [])
        if "secretRef" in ref
    ]
    assert "dubis-server-auth" in secret_refs


def test_ingress_uses_tailscale_class(manifests):
    ingress = _by_kind(manifests, "Ingress")
    assert ingress["spec"]["ingressClassName"] == "tailscale"


def test_argocd_application_shape():
    doc = yaml.safe_load((DEPLOY_DIR / "argocd-application.yaml").read_text(encoding="utf-8"))
    assert doc["kind"] == "Application"
    assert doc["apiVersion"] == "argoproj.io/v1alpha1"
    assert doc["spec"]["source"]["path"] == "deploy"
    assert doc["spec"]["destination"]["namespace"] == "dubis"
    assert doc["spec"]["syncPolicy"]["automated"]["prune"] is False
