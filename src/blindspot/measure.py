"""Everything that measures runs outside the cluster: the alert receiver, the Prometheus watcher and the probes."""

import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

from blindspot.scoring import Observations, identity
from blindspot.shell import run

PROMETHEUS_PORT = 19090


class Recorder:
    """Holds one trial's observations, shared between threads."""

    def __init__(self) -> None:
        self.obs = Observations()
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def snapshot(self) -> Observations:
        with self.lock:
            return self.obs.snapshot()

    # --- receiver -------------------------------------------------------------------------------

    def start_receiver(self, port: int) -> ThreadingHTTPServer:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                now = time.time()
                try:
                    alerts = json.loads(body).get("alerts", [])
                except ValueError:
                    alerts = []
                with recorder.lock:
                    for alert in alerts:
                        recorder.obs.events.append(
                            {"t": now, "status": alert.get("status"), "labels": alert.get("labels", {})}
                        )
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def has_delivered(self, alertname: str) -> bool:
        with self.lock:
            return any(e["labels"].get("alertname") == alertname for e in self.obs.events)

    # --- Prometheus -----------------------------------------------------------------------------

    def watch_prometheus(self, service: str = "svc/kps-prometheus", namespace: str = "monitoring") -> None:
        threading.Thread(target=self._watch, args=(service, namespace), daemon=True).start()

    def _watch(self, service: str, namespace: str) -> None:
        forward = None
        while not self.stop.is_set():
            if forward is None or forward.poll() is not None:
                forward = subprocess.Popen(
                    ["kubectl", "-n", namespace, "port-forward", service, f"{PROMETHEUS_PORT}:9090"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.stop.wait(3)
            try:
                alerts = prometheus_get("/api/v1/alerts")["data"]["alerts"]
                now = time.time()
                with self.lock:
                    for alert in alerts:
                        seen = self.obs.alerts.setdefault(identity(alert["labels"]), {"labels": alert["labels"]})
                        seen.setdefault(f"{alert['state']}_t", now)
                        seen["last_seen"] = now
            except Exception:  # noqa: BLE001, S110 - retried in 15 seconds; Prometheus may be down on purpose
                pass
            self.stop.wait(15)
        if forward:
            forward.terminate()

    # --- probes ---------------------------------------------------------------------------------

    def _record(self, kind: str, ok: bool, started: float) -> None:
        with self.lock:
            self.obs.probes.append({"t": started, "kind": kind, "ok": ok, "ms": round((time.time() - started) * 1000)})

    def start_http_probe(self, kind: str, url: str, every: float = 0.5) -> None:
        def loop():
            while not self.stop.is_set():
                started = time.time()
                try:
                    with urlopen(url, timeout=3) as response:
                        response.read()
                        ok = response.status == 200
                except Exception:  # noqa: BLE001 - any failure is a failed probe
                    ok = False
                self._record(kind, ok, started)
                self.stop.wait(max(0.0, every - (time.time() - started)))

        threading.Thread(target=loop, daemon=True).start()

    def start_api_probe(self, every: float = 10) -> None:
        """A server-side dry run of creating a pod: it passes through admission, and creates nothing."""

        def loop():
            while not self.stop.is_set():
                started = time.time()
                done = run(
                    [
                        "kubectl",
                        "-n",
                        "shop",
                        "run",
                        "apiprobe",
                        "--image=registry.k8s.io/pause:3.10",
                        "--restart=Never",
                        "--dry-run=server",
                        "-o",
                        "name",
                    ],
                    check=False,
                    timeout=30,
                    quiet=True,
                )
                self._record("api", done.returncode == 0, started)
                self.stop.wait(every)

        threading.Thread(target=loop, daemon=True).start()


def prometheus_get(path: str) -> dict:
    with urlopen(f"http://127.0.0.1:{PROMETHEUS_PORT}{path}", timeout=5) as response:
        return json.load(response)
