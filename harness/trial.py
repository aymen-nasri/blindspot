"""One trial of the v0.0 test: break one thing and record what the monitoring did.

This is a throwaway harness for the go/no-go test, not the tool. Everything that measures runs on the
GitHub runner, outside the cluster: the alert receiver, the harm probes and the clock.
"""

import argparse
import json
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from faults import FAULTS, NEVER_RELEVANT

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = "blindspot"
CHART_VERSION = "92.22.0"
RECEIVER_PORT = 8099
BASELINE_S = 300
WINDOW_S = 1800
HARM_DEADLINE_S = 300
DROP_LABELS = {"prometheus", "prometheus_replica"}  # added on the way to Alertmanager

LOCK = threading.Lock()
EVENTS = []  # every alert the receiver got
OBSERVED = {}  # every alert Prometheus reported, with when it was first seen in each state
PROBES = []  # every probe result


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def sh(cmd, check=True, timeout=900, stdin=None, quiet=False):
    if not quiet:
        log(f"$ {cmd}")
    done = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, input=stdin)
    if not quiet:
        for stream in (done.stdout, done.stderr):
            if stream.strip():
                print(stream.strip()[-2500:], flush=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"exit {done.returncode}: {cmd}")
    return done


def identity(labels):
    return json.dumps({k: v for k, v in labels.items() if k not in DROP_LABELS}, sort_keys=True)


# --- Measuring, all outside the cluster ----------------------------------------------------------


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        now = time.time()
        try:
            alerts = json.loads(body).get("alerts", [])
        except ValueError:
            alerts = []
        with LOCK:
            for alert in alerts:
                EVENTS.append({"t": now, "status": alert.get("status"), "labels": alert.get("labels", {})})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def prometheus_get(path):
    with urlopen(f"http://127.0.0.1:19090{path}", timeout=5) as response:
        return json.load(response)


def watch_prometheus(stop):
    forward = None
    while not stop.is_set():
        if forward is None or forward.poll() is not None:
            forward = subprocess.Popen(
                "kubectl -n monitoring port-forward svc/kps-prometheus 19090:9090",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            stop.wait(3)
        try:
            alerts = prometheus_get("/api/v1/alerts")["data"]["alerts"]
            now = time.time()
            with LOCK:
                for alert in alerts:
                    seen = OBSERVED.setdefault(identity(alert["labels"]), {"labels": alert["labels"]})
                    seen.setdefault(f"{alert['state']}_t", now)
                    seen.setdefault("activeAt", alert.get("activeAt"))
                    seen["last_seen"] = now
        except Exception:  # noqa: S110 - a missed poll is retried in 15 seconds; Prometheus may be down on purpose
            pass
        stop.wait(15)
    if forward:
        forward.terminate()


def record_probe(kind, ok, started):
    with LOCK:
        PROBES.append({"t": started, "kind": kind, "ok": ok, "ms": round((time.time() - started) * 1000)})


def http_probe(stop, kind, url):
    while not stop.is_set():
        started = time.time()
        try:
            with urlopen(url, timeout=3) as response:
                response.read()
                ok = response.status == 200
        except Exception:
            ok = False
        record_probe(kind, ok, started)
        stop.wait(max(0.0, 0.5 - (time.time() - started)))


def api_probe(stop):
    while not stop.is_set():
        started = time.time()
        done = sh(
            "kubectl -n shop run apiprobe --image=registry.k8s.io/pause:3.10 --restart=Never --dry-run=server -o name",
            check=False,
            timeout=30,
            quiet=True,
        )
        record_probe("api", done.returncode == 0, started)
        stop.wait(10)


def harmed(spec, at):
    """Is the fault's harm visible in the 60 seconds before `at`?"""
    if spec.get("gone"):
        name = spec["gone"]
        pods = sh(f"kubectl -n monitoring get pods -l app.kubernetes.io/name={name} -o name", check=False, quiet=True)
        if pods.stdout.strip():
            return False
        if "probe" not in spec:
            return True
    with LOCK:
        recent = [p for p in PROBES if p["kind"] == spec["probe"] and at - 60 <= p["t"] < at]
    if len(recent) < 5:
        return False
    if sum(p["ok"] for p in recent) / len(recent) < 0.95:
        return True
    latencies = sorted(p["ms"] for p in recent if p["ok"])
    limit = spec.get("p95_ms")
    return bool(limit and latencies and latencies[int(0.95 * (len(latencies) - 1))] > limit)


# --- The cluster ----------------------------------------------------------------------------------


def node_ip(node):
    data = json.loads(sh(f"docker inspect {node}", quiet=True).stdout)[0]
    return next(n["IPAddress"] for n in data["NetworkSettings"]["Networks"].values() if n.get("IPAddress"))


def setup():
    sh(f"kind create cluster --name {CLUSTER} --config {ROOT / 'lab/kind.yaml'} --wait 3m", timeout=600)
    gateway = sh(f"docker exec {CLUSTER}-control-plane ip -4 route show default").stdout.split()[2]
    receiver_url = f"http://{gateway}:{RECEIVER_PORT}/alerts"
    values = (ROOT / "lab/kps-values.yaml").read_text().replace("RECEIVER_URL", receiver_url)
    Path("/tmp/kps-values.yaml").write_text(values)
    sh(f"kubectl apply -f {ROOT / 'lab/workload.yaml'}")
    sh("helm repo add prometheus-community https://prometheus-community.github.io/helm-charts")
    sh(
        f"helm install kps prometheus-community/kube-prometheus-stack --version {CHART_VERSION} "
        "-n monitoring --create-namespace -f /tmp/kps-values.yaml --wait --timeout 12m",
        timeout=900,
    )
    for deploy in ("web", "api"):
        sh(f"kubectl -n shop rollout status deploy/{deploy} --timeout=5m", timeout=330)
    return receiver_url


def pods(app):
    data = json.loads(sh(f"kubectl -n shop get pods -l app={app} -o json", quiet=True).stdout)
    return [(p["spec"]["nodeName"], p["status"]["podIP"]) for p in data["items"]]


def on_node(node, command, check=True):
    return sh(f"docker exec {node} sh -c {json.dumps(command)}", check=check)


def container_pids(node, name):
    ids = on_node(node, f"crictl ps --name {name} -q").stdout.split()
    return [json.loads(on_node(node, f"crictl inspect {cid}").stdout)["info"]["pid"] for cid in ids]


def edit_corefile(block):
    configmap = json.loads(sh("kubectl -n kube-system get configmap coredns -o json", quiet=True).stdout)
    configmap["data"]["Corefile"] = configmap["data"]["Corefile"].rstrip() + "\n" + block
    for key in ("resourceVersion", "uid", "creationTimestamp", "managedFields"):
        configmap["metadata"].pop(key, None)
    sh("kubectl replace -f -", stdin=json.dumps(configmap))


# --- The faults -----------------------------------------------------------------------------------


def inject(fault):
    notes = {}
    if fault in ("crashloop", "alertmanager-down"):
        if fault == "alertmanager-down":
            sh('kubectl -n monitoring patch alertmanager kps-alertmanager --type merge -p \'{"spec":{"replicas":0}}\'')
        patch = [{"op": "replace", "path": "/spec/template/spec/containers/0/command", "value": ["sh", "-c", "exit 1"]}]
        sh(f"kubectl -n shop patch deploy web --type json -p '{json.dumps(patch)}'")
    elif fault == "cpu-throttle":
        # The request must drop too, because Kubernetes refuses a limit below the request.
        sh("kubectl -n shop set resources deploy/web -c web --limits=cpu=20m --requests=cpu=10m")
    elif fault == "dns-nxdomain":
        edit_corefile(
            "example.com:53 {\n    errors\n    template IN ANY example.com {\n"
            "        match .*\n        rcode NXDOMAIN\n    }\n}\n"
        )
    elif fault == "dns-slow":
        edit_corefile("example.com:53 {\n    errors\n    erratic {\n        delay 1 5s\n    }\n}\n")
    elif fault == "packet-loss":
        node = pods("api")[0][0]
        for pid in container_pids(node, "api"):
            tc = on_node(node, f"nsenter -t {pid} -n tc qdisc add dev eth0 root netem loss 40%", check=False)
            if tc.returncode == 0:
                notes["method"] = "tc netem, 40% loss on the backend pods"
            else:
                on_node(
                    node, f"nsenter -t {pid} -n iptables -I INPUT -m statistic --mode random --probability 0.4 -j DROP"
                )
                notes["method"] = (
                    "iptables statistic, 40% of packets into the backend pods dropped (tc netem unavailable)"
                )
    elif fault == "mtu-blackhole":
        api_node = pods("api")[0][0]
        web = pods("web")
        gateway = node_ip(web[0][0])
        for _, ip in web:
            on_node(api_node, f"ip route add {ip}/32 via {gateway} dev eth0 mtu 1000")
        on_node(api_node, "iptables -I OUTPUT -p icmp --icmp-type fragmentation-needed -j DROP")
        notes["method"] = f"route MTU 1000 on {api_node} toward the web pods, with ICMP 'fragmentation needed' dropped"
    elif fault == "netpol-deny":
        policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "blindspot-deny", "namespace": "shop"},
            "spec": {"podSelector": {"matchLabels": {"app": "api"}}, "policyTypes": ["Ingress"]},
        }
        sh("kubectl apply -f -", stdin=json.dumps(policy))
    elif fault == "webhook-down":
        webhook = {
            "apiVersion": "admissionregistration.k8s.io/v1",
            "kind": "ValidatingWebhookConfiguration",
            "metadata": {"name": "blindspot-broken"},
            "webhooks": [
                {
                    "name": "broken.blindspot.test",
                    "admissionReviewVersions": ["v1"],
                    "sideEffects": "None",
                    "failurePolicy": "Fail",
                    "timeoutSeconds": 5,
                    "clientConfig": {
                        "service": {"name": "missing", "namespace": "default", "path": "/validate", "port": 443}
                    },
                    "rules": [
                        {
                            "operations": ["CREATE", "UPDATE"],
                            "apiGroups": [""],
                            "apiVersions": ["v1"],
                            "resources": ["pods"],
                        }
                    ],
                    "namespaceSelector": {
                        "matchExpressions": [
                            {"key": "kubernetes.io/metadata.name", "operator": "NotIn", "values": ["kube-system"]}
                        ]
                    },
                }
            ],
        }
        sh("kubectl apply -f -", stdin=json.dumps(webhook))
    elif fault == "prometheus-down":
        sh('kubectl -n monitoring patch prometheus kps-prometheus --type merge -p \'{"spec":{"replicas":0}}\'')
    else:
        raise ValueError(fault)
    return notes


# --- Scoring --------------------------------------------------------------------------------------


def is_relevant(fault, labels):
    name = labels.get("alertname", "")
    if name in NEVER_RELEVANT:
        return False
    return any(
        re.fullmatch(name_re, name) and all(re.fullmatch(v, labels.get(k, "")) for k, v in label_res.items())
        for name_re, label_res in FAULTS[fault]["relevant"]
    )


def score(fault, t0, harm_t):
    with LOCK:
        events = list(EVENTS)
        observed = {k: dict(v) for k, v in OBSERVED.items()}
    # Baseline means still active when the fault began, not merely seen while the cluster started up.
    baseline = {
        k
        for k, v in observed.items()
        if min(v.get("pending_t", 1e18), v.get("firing_t", 1e18)) < t0 and v.get("last_seen", 0) >= t0 - 45
    }
    last_status = {}
    for e in sorted(events, key=lambda e: e["t"]):
        if e["t"] < t0:
            last_status[identity(e["labels"])] = e["status"]
    baseline |= {k for k, status in last_status.items() if status == "firing"}
    startup = {
        json.loads(k).get("alertname")
        for k, v in observed.items()
        if min(v.get("pending_t", 1e18), v.get("firing_t", 1e18)) < t0
    }

    def credit(labels):
        return is_relevant(fault, labels) and identity(labels) not in baseline

    delivered = [e for e in events if e["t"] >= t0 and e["status"] == "firing" and credit(e["labels"])]
    fired = [v for v in observed.values() if v.get("firing_t", 0) >= t0 and credit(v["labels"])]
    pending = [v for v in observed.values() if v.get("pending_t", 0) >= t0 and credit(v["labels"])]

    def since(t):
        return round(t - (harm_t or t0)) if t else None

    if delivered:
        first = min(delivered, key=lambda e: e["t"])
        outcome, alert, seconds = "caught", first["labels"].get("alertname"), since(first["t"])
    elif fired:
        first = min(fired, key=lambda v: v["firing_t"])
        outcome, alert, seconds = "fired, not delivered", first["labels"].get("alertname"), since(first["firing_t"])
    elif pending:
        first = min(pending, key=lambda v: v["pending_t"])
        outcome, alert, seconds = "pending only", first["labels"].get("alertname"), since(first["pending_t"])
    else:
        outcome, alert, seconds = "missed", None, None

    watchdog = [e["t"] for e in events if e["labels"].get("alertname") == "Watchdog"]
    after = [t for t in watchdog if t >= t0]
    return {
        "outcome": outcome,
        "first_relevant_alert": alert,
        "seconds_after_harm": seconds,
        "delivered_after_t0": sorted(
            {e["labels"].get("alertname") for e in events if e["t"] >= t0 and e["status"] == "firing"}
        ),
        "firing_after_t0": sorted(
            {v["labels"].get("alertname") for v in observed.values() if v.get("firing_t", 0) >= t0}
        ),
        "baseline_alerts": sorted({json.loads(k).get("alertname") for k in baseline}),
        "seen_before_t0": sorted(startup),
        "watchdog_last_seen_after_t0_s": round(max(after) - t0) if after else None,
        "watchdog_deliveries_after_t0": len(after),
    }


# --- One trial ------------------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fault", required=True, choices=sorted(FAULTS))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    fault, spec = args.fault, FAULTS[args.fault]
    result = {"fault": fault, "number": spec["number"], "name": spec["name"], "chart": CHART_VERSION}

    server = ThreadingHTTPServer(("0.0.0.0", RECEIVER_PORT), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop = threading.Event()

    def finish(status, **extra):
        result.update(status=status, **extra)
        Path(args.out).write_text(json.dumps(result, indent=2))
        log(f"result: {json.dumps(result)}")
        stop.set()

    try:
        result["receiver_url"] = setup()
    except Exception as error:
        return finish("invalid: setup", error=str(error))

    target = f"http://{node_ip(f'{CLUSTER}-control-plane')}:30080"
    for target_fn, fn_args in (
        (watch_prometheus, (stop,)),
        (http_probe, (stop, "web", f"{target}/work")),
        (http_probe, (stop, "big", f"{target}/work?big=1")),
        (api_probe, (stop,)),
    ):
        threading.Thread(target=target_fn, args=fn_args, daemon=True).start()

    deadline = time.time() + 600
    while time.time() < deadline and not any(e["labels"].get("alertname") == "Watchdog" for e in EVENTS):
        time.sleep(5)
    if not any(e["labels"].get("alertname") == "Watchdog" for e in EVENTS):
        sh("kubectl -n monitoring logs -l app.kubernetes.io/name=alertmanager --tail=50", check=False)
        return finish("invalid: receiver", error="no Watchdog delivery within 10 minutes")
    log("Watchdog arrived, so the delivery path works")

    time.sleep(BASELINE_S)
    if harmed({"probe": spec["harm"].get("probe", "web")}, time.time()):
        return finish("invalid: baseline", error="the app was already unhealthy before the fault")
    try:
        result["targets_down_at_t0"] = sorted(
            {r["metric"].get("job", "?") for r in prometheus_get("/api/v1/query?query=up%3D%3D0")["data"]["result"]}
        )
    except Exception:
        result["targets_down_at_t0"] = "unknown"

    t0 = time.time()
    try:
        result["inject"] = inject(fault)
    except Exception as error:
        return finish("invalid: inject", error=str(error))
    log(f"injected {fault}")

    harm_t = None
    while time.time() < t0 + HARM_DEADLINE_S:
        time.sleep(5)
        if harmed(spec["harm"], time.time()):
            harm_t = time.time()
            break
    if harm_t is None:
        return finish("invalid: no harm", harm_deadline_s=HARM_DEADLINE_S, **score(fault, t0, None))
    log(f"harm confirmed after {round(harm_t - t0)}s")

    while time.time() < t0 + WINDOW_S:
        time.sleep(15)
    finish("valid", harm_after_t0_s=round(harm_t - t0), window_s=WINDOW_S, **score(fault, t0, harm_t))


if __name__ == "__main__":
    main()
