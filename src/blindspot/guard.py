"""Refuse to break a cluster that hasn't opted in.

Someone will eventually run this with the wrong kubeconfig. These checks stop mistakes, not a person
who is determined to bypass them, so never run BlindSpot against a cluster that matters.
"""

import json

from blindspot.shell import kubectl, kubectl_json

MARKER = "blindspot-allowed"
ALLOWED_PREFIXES = ("kind-", "blindspot-")
MAX_NODES = 5


class Refused(RuntimeError):
    pass


def cluster_uid() -> str:
    """The kube-system namespace's UID, which is unique to a cluster."""
    return kubectl_json("get", "namespace", "kube-system")["metadata"]["uid"]


def mark() -> None:
    """Opt the current cluster in. Only `blindspot lab up` calls this, on a cluster it just made."""
    manifest = kubectl(
        "-n",
        "kube-system",
        "create",
        "configmap",
        MARKER,
        f"--from-literal=clusterUID={cluster_uid()}",
        "--dry-run=client",
        "-o",
        "yaml",
        quiet=True,
    ).stdout
    kubectl("apply", "-f", "-", stdin=manifest)


def reasons_to_refuse(context: str, marker_uid: str | None, actual_uid: str, node_count: int) -> list[str]:
    """Pure decision, so it can be tested without a cluster."""
    reasons = []
    if not context.startswith(ALLOWED_PREFIXES):
        reasons.append(f"context {context!r} doesn't start with {' or '.join(ALLOWED_PREFIXES)}")
    if marker_uid is None:
        reasons.append(f"no {MARKER} ConfigMap in kube-system")
    elif marker_uid != actual_uid:
        reasons.append(f"the {MARKER} marker was made for a different cluster")
    if node_count > MAX_NODES:
        reasons.append(f"{node_count} nodes is more than a lab should have")
    return reasons


def check() -> list[str]:
    context = kubectl("config", "current-context", quiet=True).stdout.strip()
    marker = kubectl("-n", "kube-system", "get", "configmap", MARKER, "-o", "json", check=False, quiet=True)
    marker_uid = json.loads(marker.stdout).get("data", {}).get("clusterUID") if marker.returncode == 0 else None
    nodes = len(kubectl_json("get", "nodes")["items"])
    return reasons_to_refuse(context, marker_uid, cluster_uid(), nodes)


def enforce() -> None:
    reasons = check()
    if reasons:
        raise Refused("refusing to inject faults: " + "; ".join(reasons))
