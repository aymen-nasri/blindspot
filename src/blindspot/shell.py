"""Running commands. Arguments are passed as lists, never through a shell, except inside a lab node."""

import json
import subprocess
import time


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


class CommandFailed(RuntimeError):
    pass


def run(args: list[str], *, check: bool = True, timeout: int = 900, stdin: str | None = None, quiet: bool = False):
    if not quiet:
        log("$ " + " ".join(args))
    done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=stdin, check=False)
    if not quiet:
        for stream in (done.stdout, done.stderr):
            if stream.strip():
                print(stream.strip()[-2500:], flush=True)
    if check and done.returncode != 0:
        raise CommandFailed(f"exit {done.returncode}: {' '.join(args)}: {done.stderr.strip()[-500:]}")
    return done


def kubectl(*args: str, stdin: str | None = None, check: bool = True, quiet: bool = False):
    return run(["kubectl", *args], stdin=stdin, check=check, quiet=quiet)


def kubectl_json(*args: str) -> dict:
    return json.loads(kubectl(*args, "-o", "json", quiet=True).stdout)


def on_node(node: str, command: str, check: bool = True):
    """Run a command inside a kind node, which is a container on the runner."""
    return run(["docker", "exec", node, "sh", "-c", command], check=check)


def node_ip(node: str) -> str:
    data = json.loads(run(["docker", "inspect", node], quiet=True).stdout)[0]
    return next(n["IPAddress"] for n in data["NetworkSettings"]["Networks"].values() if n.get("IPAddress"))


def pods(namespace: str, app: str) -> list[tuple[str, str]]:
    """(node, pod IP) for each pod of an app."""
    data = kubectl_json("-n", namespace, "get", "pods", "-l", f"app={app}")
    return [(p["spec"]["nodeName"], p["status"]["podIP"]) for p in data["items"]]


def container_pids(node: str, name: str) -> list[int]:
    ids = on_node(node, f"crictl ps --name {name} -q").stdout.split()
    return [json.loads(on_node(node, f"crictl inspect {cid}").stdout)["info"]["pid"] for cid in ids]
