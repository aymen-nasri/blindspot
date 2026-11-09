"""Pure functions from what was observed to an outcome. Nothing here touches a cluster."""

import json
from dataclasses import dataclass, field

from blindspot.catalog import Fault

# Labels Prometheus adds on the way to Alertmanager, which would otherwise split one alert into two.
DROP_LABELS = frozenset({"prometheus", "prometheus_replica"})
FAR_FUTURE = 1e18


def identity(labels: dict[str, str]) -> str:
    return json.dumps({k: v for k, v in labels.items() if k not in DROP_LABELS}, sort_keys=True)


@dataclass
class Observations:
    """Everything measured outside the cluster during one trial."""

    events: list[dict] = field(default_factory=list)  # deliveries at the receiver
    alerts: dict[str, dict] = field(default_factory=dict)  # Prometheus's view, by identity
    probes: list[dict] = field(default_factory=list)  # harm probe results

    def snapshot(self) -> "Observations":
        return Observations(list(self.events), {k: dict(v) for k, v in self.alerts.items()}, list(self.probes))


def harmed_by_probes(probes: list[dict], kind: str, at: float, p95_ms: int | None = None) -> bool:
    """Did the probe fail more than 5% of the time, or run slower than `p95_ms`, in the minute before `at`?"""
    recent = [p for p in probes if p["kind"] == kind and at - 60 <= p["t"] < at]
    if len(recent) < 5:
        return False
    if sum(p["ok"] for p in recent) / len(recent) < 0.95:
        return True
    latencies = sorted(p["ms"] for p in recent if p["ok"])
    return bool(p95_ms and latencies and latencies[int(0.95 * (len(latencies) - 1))] > p95_ms)


def probe_timeline(probes: list[dict], t0: float, end: float) -> list[dict]:
    """How each probe did in each minute, from the minute before the fault to `end`.

    Minute 0 starts at T0. Harm is only confirmed once, near the start, so this shows whether it lasted.
    """
    timeline = []
    kinds = sorted({p["kind"] for p in probes})
    for minute in range(-1, int((end - t0) // 60) + 1):
        begin = t0 + 60 * minute
        row: dict = {"minute": minute}
        for kind in kinds:
            window = [p for p in probes if p["kind"] == kind and begin <= p["t"] < begin + 60]
            if window:
                latencies = sorted(p["ms"] for p in window if p["ok"])
                row[kind] = {
                    "n": len(window),
                    "failed": sum(not p["ok"] for p in window),
                    "p95_ms": latencies[int(0.95 * (len(latencies) - 1))] if latencies else None,
                }
        timeline.append(row)
    return timeline


def baseline(obs: Observations, t0: float) -> set[str]:
    """Alerts still pending or firing when the fault began. Startup blips that resolved don't count."""
    active = {
        key
        for key, seen in obs.alerts.items()
        if min(seen.get("pending_t", FAR_FUTURE), seen.get("firing_t", FAR_FUTURE)) < t0
        and seen.get("last_seen", 0) >= t0 - 45
    }
    last_status: dict[str, str] = {}
    for event in sorted(obs.events, key=lambda e: e["t"]):
        if event["t"] < t0:
            last_status[identity(event["labels"])] = event["status"]
    return active | {key for key, status in last_status.items() if status == "firing"}


def score(fault: Fault, obs: Observations, t0: float, harm_t: float | None) -> dict:
    ignore = baseline(obs, t0)

    def credit(labels: dict[str, str]) -> bool:
        return fault.is_relevant(labels) and identity(labels) not in ignore

    delivered = [e for e in obs.events if e["t"] >= t0 and e["status"] == "firing" and credit(e["labels"])]
    fired = [v for v in obs.alerts.values() if v.get("firing_t", 0) >= t0 and credit(v["labels"])]
    pending = [v for v in obs.alerts.values() if v.get("pending_t", 0) >= t0 and credit(v["labels"])]
    origin = harm_t or t0

    if delivered:
        first = min(delivered, key=lambda e: e["t"])
        outcome, alert, seconds = "caught", first["labels"].get("alertname"), round(first["t"] - origin)
    elif fired:
        first = min(fired, key=lambda v: v["firing_t"])
        outcome, alert, seconds = (
            "fired, not delivered",
            first["labels"].get("alertname"),
            round(first["firing_t"] - origin),
        )
    elif pending:
        first = min(pending, key=lambda v: v["pending_t"])
        outcome, alert, seconds = "pending only", first["labels"].get("alertname"), round(first["pending_t"] - origin)
    else:
        outcome, alert, seconds = "missed", None, None

    watchdog_after = [e["t"] for e in obs.events if e["labels"].get("alertname") == "Watchdog" and e["t"] >= t0]
    return {
        "outcome": outcome,
        "first_relevant_alert": alert,
        "seconds_after_harm": seconds,
        "time_band": band(seconds) if outcome == "caught" else None,
        "delivered_after_t0": sorted(
            {e["labels"].get("alertname") for e in obs.events if e["t"] >= t0 and e["status"] == "firing"}
        ),
        "firing_after_t0": sorted(
            {v["labels"].get("alertname") for v in obs.alerts.values() if v.get("firing_t", 0) >= t0}
        ),
        "baseline_alerts": sorted({json.loads(k).get("alertname") for k in ignore}),
        "seen_before_t0": sorted(
            {
                v["labels"].get("alertname")
                for v in obs.alerts.values()
                if min(v.get("pending_t", FAR_FUTURE), v.get("firing_t", FAR_FUTURE)) < t0
            }
        ),
        "watchdog_last_seen_after_t0_s": round(max(watchdog_after) - t0) if watchdog_after else None,
        "watchdog_deliveries_after_t0": len(watchdog_after),
    }


def band(seconds: int | None) -> str | None:
    """Reported beside the outcome, never used as a cut-off."""
    if seconds is None:
        return None
    for limit, label in ((300, "within 5 min"), (900, "within 15 min"), (1800, "within 30 min")):
        if seconds <= limit:
            return label
    return "over 30 min"


def meets_expectation(fault: Fault, result: dict) -> bool:
    """For controls: did the trial come out the way the control says it must?"""
    if fault.expect is None:
        return True
    if fault.expect.startswith("invalid"):
        return result.get("status") == fault.expect
    if result.get("status") != "valid":
        return False
    caught = result.get("outcome") == "caught"
    return caught if fault.expect == "caught" else not caught
