"""Each step type knows how to break something and how to put it back.

`inject` returns whatever `undo` needs, so undo never has to guess what changed.
"""

import json
import shlex
from collections.abc import Callable
from typing import Any

import yaml

from blindspot.catalog import Step
from blindspot.shell import container_pids, kubectl, kubectl_json, node_ip, on_node, pods


def _require(params: dict, *keys: str) -> None:
    missing = [k for k in keys if k not in params]
    if missing:
        raise ValueError(f"missing parameters: {', '.join(missing)}")


# --- deployment-patch: a strategic merge patch, undone with a rollout undo ------------------------


def deployment_patch(p: dict) -> dict:
    _require(p, "namespace", "name", "patch")
    kubectl("-n", p["namespace"], "patch", "deployment", p["name"], "--type", "strategic", "-p", json.dumps(p["patch"]))
    return {}


def undo_deployment_patch(p: dict, _: dict) -> None:
    kubectl("-n", p["namespace"], "rollout", "undo", f"deployment/{p['name']}")


# --- corefile-block: add a server block to CoreDNS, undone by restoring the saved Corefile ---------


def _replace_corefile(text: str) -> None:
    configmap = kubectl_json("-n", "kube-system", "get", "configmap", "coredns")
    configmap["data"]["Corefile"] = text
    for key in ("resourceVersion", "uid", "creationTimestamp", "managedFields"):
        configmap["metadata"].pop(key, None)
    kubectl("replace", "-f", "-", stdin=json.dumps(configmap))


def corefile_block(p: dict) -> dict:
    _require(p, "block")
    original = kubectl_json("-n", "kube-system", "get", "configmap", "coredns")["data"]["Corefile"]
    _replace_corefile(original.rstrip() + "\n" + p["block"])
    return {"original": original}


def undo_corefile_block(_: dict, state: dict) -> None:
    _replace_corefile(state["original"])


# --- pod-netem: packet loss inside an app's pods, with iptables if netem is missing ---------------


def pod_netem(p: dict) -> dict:
    _require(p, "namespace", "app", "loss_percent")
    node = pods(p["namespace"], p["app"])[0][0]
    applied = []
    for pid in container_pids(node, p["app"]):
        netem = f"nsenter -t {pid} -n tc qdisc add dev eth0 root netem loss {p['loss_percent']}%"
        if on_node(node, netem, check=False).returncode == 0:
            applied.append({"pid": pid, "method": "netem"})
        else:
            rule = f"INPUT -m statistic --mode random --probability {p['loss_percent'] / 100} -j DROP"
            on_node(node, f"nsenter -t {pid} -n iptables -I {rule}")
            applied.append({"pid": pid, "method": "iptables", "rule": rule})
    return {"node": node, "applied": applied}


def undo_pod_netem(_: dict, state: dict) -> None:
    for item in state["applied"]:
        if item["method"] == "netem":
            on_node(state["node"], f"nsenter -t {item['pid']} -n tc qdisc del dev eth0 root", check=False)
        else:
            on_node(state["node"], f"nsenter -t {item['pid']} -n iptables -D {item['rule']}", check=False)


# --- route-mtu: an MTU black hole on the hop carrying replies back to an app ----------------------


ICMP_DROP = "OUTPUT -p icmp --icmp-type fragmentation-needed -j DROP"


def route_mtu(p: dict) -> dict:
    """On the node of `from_app`, lower the MTU of the routes to `to_app`'s pods and drop the ICMP that would
    tell the sender. Small packets pass, and large ones vanish."""
    _require(p, "namespace", "from_app", "to_app", "mtu")
    sender_node = pods(p["namespace"], p["from_app"])[0][0]
    targets = pods(p["namespace"], p["to_app"])
    routes = []
    for target_node, ip in targets:
        route = f"{ip}/32 via {node_ip(target_node)} dev eth0 mtu {p['mtu']}"
        on_node(sender_node, f"ip route add {route}")
        routes.append(f"{ip}/32")
    on_node(sender_node, f"iptables -I {ICMP_DROP}")
    return {"node": sender_node, "routes": routes}


def undo_route_mtu(_: dict, state: dict) -> None:
    for route in state["routes"]:
        on_node(state["node"], f"ip route del {route}", check=False)
    on_node(state["node"], f"iptables -D {ICMP_DROP}", check=False)


# --- apply: create Kubernetes objects, undone by deleting them ------------------------------------


def _manifest(p: dict) -> str:
    _require(p, "manifest")
    manifest = p["manifest"]
    return (
        manifest
        if isinstance(manifest, str)
        else yaml.safe_dump_all(manifest if isinstance(manifest, list) else [manifest])
    )


def apply(p: dict) -> dict:
    kubectl("apply", "-f", "-", stdin=_manifest(p))
    return {}


def undo_apply(p: dict, _: dict) -> None:
    kubectl("delete", "--ignore-not-found", "-f", "-", stdin=_manifest(p))


# --- scale-cr: scale a Prometheus Operator resource, which owns its StatefulSet ------------------


def scale_cr(p: dict) -> dict:
    _require(p, "namespace", "kind", "name", "replicas")
    if p["kind"] not in ("alertmanager", "prometheus"):
        raise ValueError("scale-cr only handles alertmanager and prometheus")
    previous = kubectl_json("-n", p["namespace"], "get", p["kind"], p["name"])["spec"].get("replicas", 1)
    patch = json.dumps({"spec": {"replicas": p["replicas"]}})
    kubectl("-n", p["namespace"], "patch", p["kind"], p["name"], "--type", "merge", "-p", patch)
    return {"previous": previous}


def undo_scale_cr(p: dict, state: dict) -> None:
    patch = json.dumps({"spec": {"replicas": state["previous"]}})
    kubectl("-n", p["namespace"], "patch", p["kind"], p["name"], "--type", "merge", "-p", patch)


# --- node faults: for the VM lab, where a node has its own kernel and disk -------------------------
# Every command is fixed here. A fault file only chooses the node and a size, never the command.

FILL_FILE = "/var/lib/blindspot-fill"
FILL_MARGIN = 32 * 1024 * 1024
INODE_DIR = "/var/lib/blindspot-inodes"
STRESS_UNIT = "blindspot-stress"
STOPPABLE = ("kubelet", "containerd")
INODE_SCRIPT = f"""import os
d = {INODE_DIR!r}
os.makedirs(d, exist_ok=True)
n = 0
try:
    while True:
        open(f"{{d}}/{{n}}", "w").close()
        n += 1
except OSError:
    pass
print(n)
"""


def _node(p: dict) -> str:
    _require(p, "node")
    return p["node"]


def fill_size(used: int, avail: int, percent: int) -> int:
    """Bytes to add so the filesystem reads `percent` full, as df counts it: used / (used + avail)."""
    if not 1 <= percent <= 100:
        raise ValueError("percent must be 1 to 100")
    return max(0, min(avail, (used + avail) * percent // 100 - used))


def node_fill_disk(p: dict) -> dict:
    """Fill the filesystem holding the kubelet's data. fallocate reserves space without writing data."""
    node = _node(p)
    percent = int(p.get("percent", 100))
    fill_size(0, 1, percent)  # refuse a bad percent before touching the node
    df = on_node(node, "df --output=used,avail -B1 /var/lib/kubelet | tail -1").stdout.split()
    size = fill_size(int(df[0]), int(df[1]), percent)
    if percent == 100:
        # fallocate can't take every free byte: the filesystem needs a little for its own bookkeeping, so
        # asking for exactly what df reports fails. Reserve all but 32 MiB, then write zeros until it's full.
        on_node(node, f"fallocate -l {max(0, size - FILL_MARGIN)} {FILL_FILE}")
        on_node(node, f"dd if=/dev/zero of={FILL_FILE}-rest bs=1M status=none", check=False)
    elif size:
        on_node(node, f"fallocate -l {size} {FILL_FILE}")
    return {"node": node, "bytes": size}


def undo_node_fill_disk(_: dict, state: dict) -> None:
    on_node(state["node"], f"rm -f {FILL_FILE} {FILL_FILE}-rest")


def node_fill_inodes(p: dict) -> dict:
    """Create empty files until no inodes are left. Python, because a shell loop is far too slow."""
    node = _node(p)
    created = on_node(node, f"python3 -c {shlex.quote(INODE_SCRIPT)}").stdout.strip()
    return {"node": node, "files": int(created or 0)}


def undo_node_fill_inodes(_: dict, state: dict) -> None:
    on_node(state["node"], f"rm -rf {INODE_DIR}")


def node_stress_memory(p: dict) -> dict:
    """Hold most of the node's free memory, like a leaking system daemon, until undone."""
    node = _node(p)
    percent = int(p.get("percent", 90))
    if not 10 <= percent <= 100:
        raise ValueError("percent must be 10 to 100")
    on_node(node, f"systemd-run --unit={STRESS_UNIT} stress-ng --vm 1 --vm-bytes {percent}% --vm-keep --timeout 3h")
    return {"node": node}


def undo_node_stress_memory(_: dict, state: dict) -> None:
    on_node(state["node"], f"systemctl stop {STRESS_UNIT}; systemctl reset-failed {STRESS_UNIT} || true")


def node_stop_service(p: dict) -> dict:
    node = _node(p)
    _require(p, "service")
    if p["service"] not in STOPPABLE:
        raise ValueError(f"node-stop-service only stops {', '.join(STOPPABLE)}")
    on_node(node, f"systemctl stop {p['service']}")
    return {"node": node, "service": p["service"]}


def undo_node_stop_service(_: dict, state: dict) -> None:
    on_node(state["node"], f"systemctl start {state['service']}")


def node_clock_skew(p: dict) -> dict:
    """Stop time sync on one node and move its clock. Undone by moving it back, then syncing again."""
    node = _node(p)
    minutes = int(p.get("minutes", 10))
    if not 1 <= abs(minutes) <= 120:
        raise ValueError("minutes must be 1 to 120, ahead or behind")
    on_node(node, f"timedatectl set-ntp false && date -s '{minutes:+d} minutes'")
    return {"node": node, "minutes": minutes}


def undo_node_clock_skew(_: dict, state: dict) -> None:
    on_node(state["node"], f"date -s '{-state['minutes']:+d} minutes' && timedatectl set-ntp true")


INJECTORS: dict[str, tuple[Callable[[dict], dict], Callable[[dict, dict], None]]] = {
    "deployment-patch": (deployment_patch, undo_deployment_patch),
    "corefile-block": (corefile_block, undo_corefile_block),
    "pod-netem": (pod_netem, undo_pod_netem),
    "route-mtu": (route_mtu, undo_route_mtu),
    "apply": (apply, undo_apply),
    "scale-cr": (scale_cr, undo_scale_cr),
    "noop": (lambda _: {}, lambda _p, _s: None),
    "node-fill-disk": (node_fill_disk, undo_node_fill_disk),
    "node-fill-inodes": (node_fill_inodes, undo_node_fill_inodes),
    "node-stress-memory": (node_stress_memory, undo_node_stress_memory),
    "node-stop-service": (node_stop_service, undo_node_stop_service),
    "node-clock-skew": (node_clock_skew, undo_node_clock_skew),
}


def inject_all(steps: list[Step]) -> list[tuple[Step, dict[str, Any]]]:
    """Apply steps in order. If one fails, undo the ones already applied, then re-raise."""
    done: list[tuple[Step, dict[str, Any]]] = []
    try:
        for step in steps:
            done.append((step, INJECTORS[step.type][0](step.params)))
    except Exception:
        undo_all(done)
        raise
    return done


def undo_all(done: list[tuple[Step, dict[str, Any]]]) -> list[str]:
    """Undo in reverse order. Keeps going after a failure, and returns the errors."""
    errors = []
    for step, state in reversed(done):
        try:
            INJECTORS[step.type][1](step.params, state)
        except Exception as error:  # noqa: BLE001 - every undo must be attempted
            errors.append(f"{step.type}: {error}")
    return errors
