"""One trial: build a fresh lab, break one thing, watch, put it back, and write down what happened."""

import json
import os
import platform
import time
from pathlib import Path

import yaml

from blindspot import __version__, guard, lab
from blindspot.catalog import Fault, Harm
from blindspot.injectors import inject_all, undo_all
from blindspot.measure import Recorder, prometheus_get
from blindspot.scoring import harmed_by_probes, meets_expectation, score
from blindspot.shell import log, node_ip

BASELINE_S = 300
HARM_DEADLINE_S = 300
DELIVERY_DEADLINE_S = 600
RECOVERY_DEADLINE_S = 300


def harmed(harm: Harm, recorder: Recorder, at: float) -> bool:
    if harm.gone and not lab.component_gone(harm.gone):
        return False
    if harm.probe is None:
        return True
    return harmed_by_probes(recorder.snapshot().probes, harm.probe, at, harm.p95_ms)


def recovered(harm: Harm, recorder: Recorder, at: float) -> bool:
    """Every harm condition has cleared. Not simply `not harmed`, which clears when any one condition does."""
    if harm.gone and lab.component_gone(harm.gone):
        return False
    return harm.probe is None or not harmed_by_probes(recorder.snapshot().probes, harm.probe, at, harm.p95_ms)


def runner_details() -> dict:
    """What the trial ran on, so a change of runner hardware shows up in the results."""
    details: dict = {"python": platform.python_version(), "kernel": platform.release(), "cpus": os.cpu_count()}
    try:
        meminfo = Path("/proc/meminfo").read_text()
        kb = int(next(line.split()[1] for line in meminfo.splitlines() if line.startswith("MemTotal:")))
        details["memory_gb"] = round(kb / 1024 / 1024, 1)
    except (OSError, StopIteration, ValueError):
        pass
    for name in ("ImageOS", "ImageVersion", "RUNNER_ENVIRONMENT"):
        if os.environ.get(name):
            details[name] = os.environ[name]
    return details


def timings() -> dict:
    """The live intervals that limit how precise any time can be."""
    found: dict = {}
    try:
        config = yaml.safe_load(prometheus_get("/api/v1/status/config")["data"]["yaml"])
        found.update({k: config["global"].get(k) for k in ("scrape_interval", "evaluation_interval")})
    except Exception:  # noqa: BLE001 - recorded as unknown
        found["prometheus"] = "unknown"
    try:
        found.update(lab.alertmanager_route())
    except Exception:  # noqa: BLE001 - recorded as unknown
        found["alertmanager"] = "unknown"
    return found


def prometheus_started() -> str | None:
    try:
        return prometheus_get("/api/v1/status/runtimeinfo")["data"]["startTime"]
    except Exception:  # noqa: BLE001 - Prometheus may be down on purpose
        return None


def wait_for(condition, deadline: float, every: float = 5) -> float | None:
    """Poll until `condition()` is true, and return when it became true, or None at the deadline."""
    while time.time() < deadline:
        if condition():
            return time.time()
        time.sleep(every)
    return None


def run_trial(
    fault: Fault, lab_dir: Path, out: Path, *, trial: int = 1, receiver_port: int = 8099, keep_lab: bool = False
) -> dict:
    result: dict = {
        "fault": fault.id,
        "trial": trial,
        "number": fault.number,
        "name": fault.name,
        "class": fault.fault_class,
        "chart": lab.CHART_VERSION,
        "blindspot": __version__,
        "runner": runner_details(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if os.environ.get("GITHUB_RUN_ID"):
        result.update(run=os.environ["GITHUB_RUN_ID"], commit=os.environ.get("GITHUB_SHA"))
    recorder = Recorder()
    server = recorder.start_receiver(receiver_port) if fault.delivery == "on" else None
    try:
        _trial(fault, lab_dir, receiver_port, recorder, result)
    finally:
        recorder.stop.set()
        if server:
            server.shutdown()
        if not keep_lab:
            lab.down()
        if fault.expect:
            result["control_passed"] = meets_expectation(fault, result)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n")
        log(f"result: {json.dumps(result)}")
    return result


def _trial(fault: Fault, lab_dir: Path, receiver_port: int, recorder: Recorder, result: dict) -> None:
    try:
        result["receiver_url"] = lab.up(lab_dir, receiver_port)
        guard.enforce()
    except Exception as error:  # noqa: BLE001 - recorded as the trial's status
        result.update(status="invalid: setup", error=str(error))
        return

    target = f"http://{node_ip(f'{lab.CLUSTER}-control-plane')}:30080"
    recorder.watch_prometheus()
    recorder.start_http_probe("web", f"{target}/work")
    recorder.start_http_probe("big", f"{target}/work?big=1")
    recorder.start_api_probe()

    if wait_for(lambda: recorder.has_delivered("Watchdog"), time.time() + DELIVERY_DEADLINE_S) is None:
        result.update(status="invalid: receiver", error="no Watchdog delivery, so delivery can't be timed")
        return
    log("Watchdog arrived, so the delivery path works")

    time.sleep(BASELINE_S)
    probe = fault.harm.probe if fault.harm and fault.harm.probe else "web"
    if harmed_by_probes(recorder.snapshot().probes, probe, time.time()):
        result.update(status="invalid: baseline", error="the app was already unhealthy before the fault")
        return
    try:
        down = prometheus_get("/api/v1/query?query=up%3D%3D0")["data"]["result"]
        result["targets_down_at_t0"] = sorted({r["metric"].get("job", "?") for r in down})
    except Exception:  # noqa: BLE001 - informational only
        result["targets_down_at_t0"] = "unknown"

    result["timings"] = timings()
    started = prometheus_started()

    t0 = time.time()
    try:
        done = inject_all(fault.inject)
    except Exception as error:  # noqa: BLE001 - recorded as the trial's status
        result.update(status="invalid: inject", error=str(error))
        return
    log(f"injected {fault.id}")
    result["inject_state"] = [{"type": step.type, "state": state} for step, state in done]

    try:
        harm_t = None
        if fault.harm:
            harm_t = wait_for(lambda: harmed(fault.harm, recorder, time.time()), t0 + HARM_DEADLINE_S)
            if harm_t is None:
                result.update(status="invalid: no harm", **score(fault, recorder.snapshot(), t0, None))
                return
            result["harm_after_t0_s"] = round(harm_t - t0)
            log(f"harm confirmed after {result['harm_after_t0_s']}s")
        wait_for(lambda: False, t0 + fault.window_minutes * 60, every=15)
        now_started = prometheus_started()
        # Prometheus keeps waiting timers across a restart unless its data was lost, so a restart is noted, not fatal.
        result["prometheus_restarted"] = None if not (started and now_started) else started != now_started
        result.update(
            status="valid", window_s=fault.window_minutes * 60, **score(fault, recorder.snapshot(), t0, harm_t)
        )
    finally:
        undone_at = time.time()
        result["undo_errors"] = undo_all(done)
        if fault.harm:
            back = wait_for(lambda: recovered(fault.harm, recorder, time.time()), undone_at + RECOVERY_DEADLINE_S)
            result["recovered_after_undo_s"] = round(back - undone_at) if back else None
