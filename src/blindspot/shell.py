"""Running commands. Arguments are passed as lists, never through a shell, except inside a lab node."""

import json
import shlex
import subprocess
import time
from dataclasses import dataclass


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


class CommandFailed(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    check: bool = True,
    timeout: int = 900,
    stdin: str | None = None,
    quiet: bool = False,
    cwd: str | None = None,
):
    if not quiet:
        log("$ " + " ".join(args))
    done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=stdin, check=False, cwd=cwd)
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


@dataclass(frozen=True)
class SshNodes:
    """How to reach VM lab nodes: their addresses, and the key and user to log in with."""

    hosts: dict[str, str]
    key: str
    user: str = "blindspot"

    def command(self, node: str, command: str) -> list[str]:
        if node not in self.hosts:
            raise ValueError(f"{node!r} isn't a node of this lab: {sorted(self.hosts)}")
        options = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]
        # ssh joins its arguments into one remote shell line, so the command is quoted once, here.
        return [
            "ssh",
            "-i",
            self.key,
            *options,
            f"{self.user}@{self.hosts[node]}",
            "sudo sh -c " + shlex.quote(command),
        ]


# None means the kind lab, where nodes are containers on the runner.
_ssh: SshNodes | None = None


def use_ssh(nodes: SshNodes | None) -> None:
    """Point node commands at VMs over SSH, or back at kind with None."""
    global _ssh
    _ssh = nodes


def on_node(node: str, command: str, check: bool = True):
    """Run a command as root on a lab node: inside a kind node's container, or over SSH on a VM."""
    if _ssh:
        return run(_ssh.command(node, command), check=check)
    return run(["docker", "exec", node, "sh", "-c", command], check=check)


def node_ip(node: str) -> str:
    if _ssh:
        return _ssh.hosts[node]
    data = json.loads(run(["docker", "inspect", node], quiet=True).stdout)[0]
    return next(n["IPAddress"] for n in data["NetworkSettings"]["Networks"].values() if n.get("IPAddress"))


def pods(namespace: str, app: str) -> list[tuple[str, str]]:
    """(node, pod IP) for each pod of an app."""
    data = kubectl_json("-n", namespace, "get", "pods", "-l", f"app={app}")
    return [(p["spec"]["nodeName"], p["status"]["podIP"]) for p in data["items"]]


def container_pids(node: str, name: str) -> list[int]:
    ids = on_node(node, f"crictl ps --name {name} -q").stdout.split()
    return [json.loads(on_node(node, f"crictl inspect {cid}").stdout)["info"]["pid"] for cid in ids]
