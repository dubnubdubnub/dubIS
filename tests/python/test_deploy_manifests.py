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

Two sources, and what each one actually proves:

- `manifests` -- every yaml document in deploy/, read from the files. The
  source for every kind assertion, cluster-scoped included, so this file
  answers identically on every machine.
- `rendered` -- `kubectl kustomize deploy/` where kubectl exists, else None.
  Proves the kustomization is valid and applies *exactly* the namespaced set.
  Never a source for a kind assertion.

Why the split, and what the old "prefer the render, fall back to the files"
fixture got wrong: `deploy/kustomization.yaml` deliberately omits
`namespace.yaml` and `argocd-appproject.yaml`, because every AppProject on
this cluster runs with `clusterResourceWhitelist: []` and no Argo app may
manage cluster-scoped objects -- the Namespace is applied out-of-band per
docs/deploy-runbook.md step 1, which is also what owns the `ghcr-pull=enabled`
Kyverno label. So `kubectl kustomize deploy/` is *correct* to emit no
Namespace, and a fixture built from that render could only ever assert zero
of them. Reading the cluster-scoped objects from a render that legitimately
lacks them made the whole file depend on which binaries happened to be on
PATH: green on CI (no kubectl), red on any developer Mac that has it, and the
Kyverno label untested on the one machine where a human would notice.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"

# Not a k8s resource at all -- the kustomize config itself.
KUSTOMIZE_CONFIG = "kustomization.yaml"
# Argo's own object, applied by hand and asserted by its own test; the
# kustomization under test does not render it.
ARGO_META = "argocd-application.yaml"
# Cluster-scoped objects, applied out-of-band per docs/deploy-runbook.md.
# They are deliberately absent from the kustomization's `resources` (no
# AppProject here whitelists cluster resources), so a kustomize render can
# never contain them: the files are their only source.
OUT_OF_BAND_FILES = ("namespace.yaml", "argocd-appproject.yaml")
# The cluster-scoped kinds that could plausibly turn up in this directory --
# not an exhaustive k8s catalogue. A file carrying one of these cannot live in
# the kustomization: no AppProject here whitelists cluster resources.
CLUSTER_SCOPED_KINDS = {
    "Namespace",
    "ClusterRole",
    "ClusterRoleBinding",
    "CustomResourceDefinition",
    "PersistentVolume",
    "StorageClass",
}
# Everything in deploy/ that the kustomization is *not* meant to render: the
# cluster-scoped files above plus Argo's own Application. Together they are the
# reason a render is not a complete picture of deploy/.
NOT_KUSTOMIZED = OUT_OF_BAND_FILES + (ARGO_META,)


def _render_via_kubectl() -> list[dict] | None:
    """Return parsed docs from `kubectl kustomize deploy/`, or None if kubectl
    isn't available. Raises (does not swallow) if kubectl IS available but the
    kustomization fails to render -- that's a real bug, not an environment gap.

    The render is the *namespaced* set only: it is correct to omit the
    out-of-band cluster-scoped objects, so never assert one of those from here.
    """
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


def _load_files(exclude: tuple[str, ...]) -> dict[str, list[dict]]:
    """Parse every yaml file in deploy/ (except `exclude`) keyed by filename.

    Keyed by file so a test can say which file a document came from -- the
    kustomization's `resources` names files, and comparing kinds across two
    sources without provenance is how you get an assertion that passes because
    it silently compared different things.
    """
    by_file: dict[str, list[dict]] = {}
    for path in sorted(DEPLOY_DIR.glob("*.yaml")):
        if path.name in exclude:
            continue
        docs = [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]
        if docs:
            by_file[path.name] = docs
    return by_file


@pytest.fixture(scope="module")
def raw_by_file() -> dict[str, list[dict]]:
    """Every deploy/ yaml document, grouped by the file it lives in."""
    return _load_files(exclude=(KUSTOMIZE_CONFIG,))


@pytest.fixture(scope="module")
def manifests(raw_by_file) -> list[dict]:
    """Every manifest in deploy/, always from the files themselves.

    The full set a human or Argo applies: the kustomized namespaced objects,
    the out-of-band cluster-scoped ones and Argo's own Application. Never a
    kustomize render: the render omits the out-of-band objects by design, so a
    fixture that preferred it would carry a different set of kinds depending on
    whether kubectl happens to be installed. The render is still checked -- by
    name, against these files -- in
    `test_kustomization_applies_exactly_the_namespaced_resources`.
    """
    docs = [doc for file_docs in raw_by_file.values() for doc in file_docs]
    assert docs, "no manifests were parsed from deploy/"
    return docs


@pytest.fixture(scope="module")
def kustomization() -> dict:
    return yaml.safe_load((DEPLOY_DIR / KUSTOMIZE_CONFIG).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rendered() -> list[dict] | None:
    """The `kubectl kustomize deploy/` output, or None where kubectl is absent.

    Tests using this assert the same proposition on both paths -- the ones
    that can only be proven by rendering assert it via the kustomization's own
    `resources` list when there is no kubectl -- so the verdict never depends
    on the machine. Nothing here is ever skipped.
    """
    return _render_via_kubectl()


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
    """The label Kyverno watches to clone the GHCR pull Secret into the
    namespace. Read from deploy/namespace.yaml -- the file that owns it -- via
    the `manifests` fixture, not from a kustomize render that is correct to
    omit it (see the module docstring)."""
    namespace = _by_kind(manifests, "Namespace")
    labels = namespace["metadata"].get("labels", {})
    assert labels.get("ghcr-pull") == "enabled"


def test_cluster_scoped_objects_live_only_in_the_out_of_band_files(
    raw_by_file, kustomization
):
    """The out-of-band rule, read off the files, not the render.

    - Nothing cluster-scoped in any file the kustomization does *not* declare
      out-of-band -- whether that is a kustomized file (Argo cannot sync a
      cluster object under `clusterResourceWhitelist: []`) or a new file nobody
      declared. A new cluster-scoped manifest is a decision: name it in
      OUT_OF_BAND_FILES and add its apply to docs/deploy-runbook.md. Leaving it
      undeclared is how the next machine-dependent assertion gets written -- no
      render will ever carry it, so any assertion drawn from a render passes
      only where kubectl is missing.
    - OUT_OF_BAND_FILES cannot rot: every name must still exist, parse, and stay
      out of `resources`.

    (AppProject is *not* cluster-scoped -- it lives in ns `argocd`. It is
    out-of-band for a different reason, that Argo does not self-manage
    projects, which is why this test keys on kind for the first half and on the
    declared list for the second.)

    This is also the premise of `test_namespace_has_ghcr_pull_label` above:
    while namespace.yaml stays out of `resources`, nothing Argo syncs can add,
    move or relabel the Namespace, so runbook step 1 is its only owner. If it
    ever goes back in, that ownership story is wrong and this file has to be
    re-read rather than patched.
    """
    smuggled = [
        (name, doc.get("kind"))
        for name, docs in raw_by_file.items()
        if name not in OUT_OF_BAND_FILES
        for doc in docs
        if doc.get("kind") in CLUSTER_SCOPED_KINDS
    ]
    assert not smuggled, (
        f"cluster-scoped objects outside the declared out-of-band files: {smuggled} "
        f"-- declared: {sorted(OUT_OF_BAND_FILES)}; no AppProject on this cluster "
        "whitelists cluster resources, so Argo cannot sync them"
    )
    resources = kustomization["resources"]
    for name in OUT_OF_BAND_FILES:
        assert name in raw_by_file, (
            f"{name} is declared out-of-band but is not in deploy/ -- "
            "update OUT_OF_BAND_FILES and the runbook together"
        )
        assert name not in resources, (
            f"{name} is declared out-of-band but kustomization.yaml renders it"
        )


def test_manifests_fixture_is_the_files_not_the_render(manifests, rendered):
    """Pin the machine-independence directly.

    On a box WITH kubectl this proves the fixture is not the render: it carries
    the out-of-band objects the render legitimately does not. On a box WITHOUT
    kubectl `rendered` is None and the same fixture assertions above still ran
    -- which is the whole point: the Namespace assertions hold on both paths
    because there is only one path to the Namespace.
    """
    kinds = {doc.get("kind") for doc in manifests}
    assert {"Namespace", "AppProject"} <= kinds, (
        "the out-of-band cluster-scoped objects are missing from the fixture -- "
        "it is being built from a kustomize render again"
    )
    if rendered is not None:
        rendered_kinds = {doc.get("kind") for doc in rendered}
        assert not rendered_kinds & CLUSTER_SCOPED_KINDS, (
            "the render now emits a cluster-scoped object: an AppProject with "
            "clusterResourceWhitelist: [] cannot sync it -- see runbook step 1"
        )
        assert rendered_kinds < kinds


def test_kustomization_applies_exactly_the_namespaced_resources(
    kustomization, raw_by_file, rendered
):
    """What the render was originally wanted for -- proving the kustomization
    itself is valid, not just that the yaml files happen to hold the right
    keys -- stated as a proposition both paths can answer.

    Coverage is asserted on BOTH paths: every namespaced file in deploy/ is in
    `resources`, and every entry in `resources` is a file that parses. A
    differential against the render alone would not do it -- dropping a file
    from `resources` shrinks both sides at once and stays green, which is
    exactly how a manifest stops ever being applied while the test reports
    success.

    With kubectl, additionally: the render's (kind, name) set equals the set
    held by the listed files, and every rendered doc lands in ns `dubis`.
    Same claim, weaker instrument, never a skip.
    """
    resources = kustomization["resources"]
    for name in resources:
        assert (DEPLOY_DIR / name).is_file(), f"resources names {name}, not on disk"
        assert name in raw_by_file, f"resources names {name}, which parses to nothing"

    namespaced_files = {n for n in raw_by_file if n not in NOT_KUSTOMIZED}
    assert set(resources) == namespaced_files, (
        "the kustomization does not cover the namespaced manifests in deploy/:\n"
        f"  applied by Argo:  {sorted(resources)}\n"
        f"  on disk, ns-scoped: {sorted(namespaced_files)}\n"
        "a namespaced file left out is never applied; one added that should be "
        "out-of-band would fail to sync"
    )

    if rendered is None:
        return

    expected = {
        (doc["kind"], doc["metadata"]["name"])
        for name in resources
        for doc in raw_by_file[name]
    }
    got = [(doc["kind"], doc["metadata"]["name"]) for doc in rendered]
    assert sorted(got) == sorted(expected), (
        f"`kubectl kustomize deploy/` does not apply exactly the namespaced set:\n"
        f"  render only: {sorted(set(got) - set(expected))}\n"
        f"  files only:  {sorted(set(expected) - set(got))}"
    )
    for doc in rendered:
        assert doc["metadata"].get("namespace") == "dubis", (
            f"{doc['kind']}/{doc['metadata']['name']} rendered outside ns dubis"
        )


def test_pinned_image_tag_is_not_the_placeholder(kustomization, rendered):
    """deploy/deployment.yaml keeps the literal `sha-PLACEHOLDER` tag on
    purpose: the kustomization's `images:` transformer is what substitutes the
    sha CI builds. So the tag the cluster would actually pull lives in exactly
    one file, and the runbook currently asks a human to eyeball it ("must NOT
    read sha-PLACEHOLDER"). Asserted on both paths, from the kustomization,
    and cross-checked against the render where one exists."""
    images = kustomization["images"]
    assert images, "kustomization.yaml pins no image"
    for entry in images:
        tag = str(entry.get("newTag", ""))
        assert tag and "PLACEHOLDER" not in tag.upper(), (
            f"{entry['name']} is pinned to {tag!r} -- nothing would pull; "
            "build-image.yml rewrites newTag to the built sha on merge to main"
        )
    if rendered is not None:
        for doc in rendered:
            containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            for container in containers:
                image = container.get("image", "")
                assert "PLACEHOLDER" not in image.upper(), f"rendered {image} would not pull"


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


def test_rename_to_fremont_kept_the_stateful_names(manifests):
    """The deployment is named fremont, but the PVC and the auth Secret keep
    their pre-rename `dubis-server-*` names on purpose: a renamed PVC is a new,
    empty volume (the inventory is orphaned), and a renamed Secret is a fresh
    one whose tokens no client holds. See docs/deploy-runbook.md, "Cutover"."""
    deployment = _by_kind(manifests, "Deployment")
    assert deployment["metadata"]["name"] == "fremont"
    pod = deployment["spec"]["template"]["spec"]
    assert pod["containers"][0]["image"].startswith("ghcr.io/dubnubdubnub/fremont:")
    claims = [v["persistentVolumeClaim"]["claimName"] for v in pod["volumes"]
              if "persistentVolumeClaim" in v]
    assert claims == ["dubis-server-data"]
    pvc = _by_kind(manifests, "PersistentVolumeClaim")
    assert pvc["metadata"]["name"] == "dubis-server-data"
    assert _by_kind(manifests, "Service")["metadata"]["name"] == "fremont"
    ingress = _by_kind(manifests, "Ingress")
    assert ingress["metadata"]["annotations"]["tailscale.com/hostname"] == "fremont"
    assert ingress["spec"]["tls"][0]["hosts"] == ["fremont"]
    app = yaml.safe_load((DEPLOY_DIR / "argocd-application.yaml").read_text(encoding="utf-8"))
    assert app["metadata"]["name"] == "fremont"
