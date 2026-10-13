"""Garden manifests follow the cluster's plain-manifest conventions and the App's settings."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
GARDEN = ROOT / "deploy" / "garden"
RETAIN = {"argocd.argoproj.io/sync-options": "Prune=false,Delete=false"}


def documents():
    found = {}
    for path in sorted(GARDEN.glob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text()):
            found[document["kind"]] = document
    return found


def test_garden_directory_is_plain_argo_manifests_without_committed_secrets():
    kinds = documents()
    assert sorted(kinds) == ["Deployment", "Namespace", "PersistentVolumeClaim", "Service"]
    assert not (GARDEN / "kustomization.yaml").exists()
    for kind, document in kinds.items():
        expected = "scrutare" if kind == "Namespace" else document["metadata"]["namespace"]
        assert expected == "scrutare"
    # Argo prune must never remove review history or the namespace holding the secrets.
    assert kinds["Namespace"]["metadata"]["annotations"] == RETAIN
    assert kinds["PersistentVolumeClaim"]["metadata"]["annotations"] == RETAIN
    assert kinds["PersistentVolumeClaim"]["spec"]["storageClassName"] == "ceph-rbd"
    assert kinds["Service"]["spec"]["ports"] == [
        {"name": "http", "port": 80, "targetPort": "http"}]


def test_deployment_is_one_restricted_worker_with_out_of_band_secret_references():
    deployment = documents()["Deployment"]["spec"]
    pod = deployment["template"]["spec"]
    container, = pod["containers"]
    assert (deployment["replicas"], deployment["strategy"]) == (1, {"type": "Recreate"})
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]}}
    assert re.fullmatch(r"ghcr\.io/vividynamics/scrutare:[A-Za-z0-9.-]+", container["image"])
    assert "envFrom" not in container
    # Probes use the health listener, which the Service never publishes.
    ports = {port["name"]: port["containerPort"] for port in container["ports"]}
    assert ports == {"http": 8080, "health": 8081}
    for probe in ("readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"] == {"path": "/healthz", "port": "health"}
    secrets = {entry["name"]: entry["valueFrom"]["secretKeyRef"]
               for entry in container["env"] if "valueFrom" in entry}
    # Garden convention: one Secret named for the app, keys named for the variables.
    assert set(secrets) == {"SCRUTARE_APP_WEBHOOK_SECRET", "SCRUTARE_APP_API_TOKEN",
                            "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
    for name, reference in secrets.items():
        assert (reference["name"], reference["key"]) == ("scrutare", name)
        assert reference.get("optional", False) is name.endswith("_API_KEY")
    key, = (volume for volume in pod["volumes"] if "secret" in volume)
    assert key["secret"]["secretName"] == "scrutare-github-app"
    literal = {entry["name"]: entry["value"] for entry in container["env"] if "value" in entry}
    assert literal["SCRUTARE_APP_PRIVATE_KEY"] == "/secrets/private-key.pem"
    assert literal["SCRUTARE_APP_ID"] == "5245551"
    # Every setting in the manifest is one the service reads and the guide documents.
    source = (ROOT / "src/scrutare/interfaces/app_server.py").read_text()
    guide = (ROOT / "docs/app.md").read_text()
    for name in [*secrets, *literal]:
        if name.startswith("SCRUTARE_APP_"):
            assert f'"{name}"' in source, name
        assert f"`{name}`" in guide, name


def test_release_image_smoke_covers_the_console_the_deployment_starts():
    container, = documents()["Deployment"]["spec"]["template"]["spec"]["containers"]
    console, = container["command"]
    smoke = (ROOT / "scripts/smoke-container.sh").read_text()
    assert f"--entrypoint {console} " in smoke
    assert "scrutare-app: startup failed" in smoke
