"""The disposable lab: a kind cluster, the monitoring stack being judged, and the reference workload."""

from pathlib import Path

from blindspot import guard
from blindspot.shell import kubectl, run

CLUSTER = "blindspot"
CHART = "prometheus-community/kube-prometheus-stack"
CHART_VERSION = "92.22.0"
NAMESPACE = "monitoring"


def receiver_url(port: int) -> str:
    """How pods reach the runner: the default gateway of a kind node."""
    route = run(["docker", "exec", f"{CLUSTER}-control-plane", "ip", "-4", "route", "show", "default"], quiet=True)
    return f"http://{route.stdout.split()[2]}:{port}/alerts"


def up(lab_dir: Path, receiver_port: int, values_out: Path = Path("/tmp/kps-values.yaml")) -> str:
    run(
        ["kind", "create", "cluster", "--name", CLUSTER, "--config", str(lab_dir / "kind.yaml"), "--wait", "3m"],
        timeout=600,
    )
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
    run(["kind", "delete", "cluster", "--name", CLUSTER], check=False)


def component_gone(name: str) -> bool:
    found = kubectl("-n", NAMESPACE, "get", "pods", "-l", f"app.kubernetes.io/name={name}", "-o", "name", quiet=True)
    return not found.stdout.strip()
