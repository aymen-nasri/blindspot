"""The disposable lab: a cluster, the monitoring stack being judged, and the reference workload.

Two kinds of cluster: kind, where nodes are containers on the runner, and the VM lab (vmlab/), where
they are KVM guests with their own kernels and disks. Both get the same monitoring and the same app.
"""

import base64
import os
from pathlib import Path

import yaml

from blindspot import guard, shell
from blindspot.shell import kubectl, kubectl_json, run

CLUSTER = "blindspot"
CHART = "prometheus-community/kube-prometheus-stack"
CHART_VERSION = "92.22.0"
NAMESPACE = "monitoring"
LAB_TYPES = ("kind", "vm")
VM_NODES = {
    "blindspot-control-plane": "192.168.122.10",
    "blindspot-worker": "192.168.122.11",
    "blindspot-worker2": "192.168.122.12",
}
VM_GATEWAY = "192.168.122.1"  # libvirt's default network, as seen from the VMs

# Which lab this process drives. Set once, by the CLI, before anything runs.
TYPE = "kind"
VMLAB = Path("vmlab")


def use(lab_type: str, vmlab_dir: Path = Path("vmlab")) -> None:
    global TYPE, VMLAB
    if lab_type not in LAB_TYPES:
        raise ValueError(f"lab type must be one of {LAB_TYPES}, not {lab_type!r}")
    TYPE, VMLAB = lab_type, vmlab_dir
    if lab_type == "vm":
        out = vmlab_dir / "out"
        os.environ["KUBECONFIG"] = str((out / "kubeconfig").resolve())
        shell.use_ssh(shell.SshNodes(hosts=dict(VM_NODES), key=str((out / "id_ed25519").resolve())))
    else:
        shell.use_ssh(None)


def receiver_url(port: int) -> str:
    """How pods reach the runner: the default gateway of a kind node, or libvirt's gateway for VMs."""
    if TYPE == "vm":
        return f"http://{VM_GATEWAY}:{port}/alerts"
    route = run(["docker", "exec", f"{CLUSTER}-control-plane", "ip", "-4", "route", "show", "default"], quiet=True)
    return f"http://{route.stdout.split()[2]}:{port}/alerts"


def create_cluster(lab_dir: Path) -> None:
    if TYPE == "kind":
        run(
            ["kind", "create", "cluster", "--name", CLUSTER, "--config", str(lab_dir / "kind.yaml"), "--wait", "3m"],
            timeout=600,
        )
        return
    run(["bash", str(VMLAB / "up.sh")], timeout=1200)
    run(["ansible-playbook", "-i", "../out/inventory.ini", "site.yml"], timeout=1800, cwd=str(VMLAB / "ansible"))
    run(["bash", str(VMLAB / "finish.sh")], timeout=900)


def up(lab_dir: Path, receiver_port: int, values_out: Path = Path("/tmp/kps-values.yaml")) -> str:
    create_cluster(lab_dir)
    guard.mark()
    url = receiver_url(receiver_port)
    values_out.write_text((lab_dir / "kps-values.yaml").read_text().replace("RECEIVER_URL", url))
    kubectl("apply", "-f", str(lab_dir / "workload.yaml"))
    run(
        ["helm", "repo", "add", "prometheus-community", "https://prometheus-community.github.io/helm-charts"],
        check=False,
    )
    run(
        [
            "helm",
            "install",
            "kps",
            CHART,
            "--version",
            CHART_VERSION,
            "-n",
            NAMESPACE,
            "--create-namespace",
            "-f",
            str(values_out),
            "--wait",
            "--timeout",
            "12m",
        ],
        timeout=900,
    )
    for deploy in ("web", "api"):
        kubectl("-n", "shop", "rollout", "status", f"deployment/{deploy}", "--timeout=5m")
    return url


def down() -> None:
    if TYPE == "kind":
        run(["kind", "delete", "cluster", "--name", CLUSTER], check=False)
        return
    for name in VM_NODES:
        run(["sudo", "virsh", "destroy", name], check=False, quiet=True)
        run(["sudo", "virsh", "undefine", name, "--remove-all-storage"], check=False)


def component_gone(name: str) -> bool:
    found = kubectl("-n", NAMESPACE, "get", "pods", "-l", f"app.kubernetes.io/name={name}", "-o", "name", quiet=True)
    return not found.stdout.strip()


def alertmanager_route() -> dict:
    """The top route's waits, from the configuration secret the chart writes."""
    for item in kubectl_json("-n", NAMESPACE, "get", "secrets")["items"]:
        data = item.get("data") or {}
        if "alertmanager.yaml" in data:
            route = yaml.safe_load(base64.b64decode(data["alertmanager.yaml"])).get("route", {})
            return {k: route.get(k) for k in ("group_wait", "group_interval", "repeat_interval")}
    return {"alertmanager": "no configuration secret found"}
