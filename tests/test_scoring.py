"""The scoring decides every result, so it is tested without a cluster."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import trial
from faults import FAULTS

T0 = 1000.0
HARM = 1050.0


@pytest.fixture(autouse=True)
def clean_state():
    for store in (trial.EVENTS, trial.PROBES):
        store.clear()
    trial.OBSERVED.clear()
    yield


def event(t, alertname, status="firing", **labels):
    trial.EVENTS.append({"t": t, "status": status, "labels": {"alertname": alertname, **labels}})


def seen(alertname, *, pending=None, firing=None, last=None, **labels):
    data = {"alertname": alertname, **labels}
    entry = {"labels": data, "last_seen": last if last is not None else max(filter(None, (pending, firing)))}
    if pending is not None:
        entry["pending_t"] = pending
    if firing is not None:
        entry["firing_t"] = firing
    trial.OBSERVED[trial.identity(data)] = entry


# --- Relevance -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fault", "labels", "expected"),
    [
        ("crashloop", {"alertname": "KubePodCrashLooping", "namespace": "shop"}, True),
        ("crashloop", {"alertname": "KubePodCrashLooping", "namespace": "monitoring"}, False),
        ("crashloop", {"alertname": "TargetDown", "namespace": "shop"}, False),
        ("cpu-throttle", {"alertname": "CPUThrottlingHigh", "namespace": "shop"}, True),
        ("dns-nxdomain", {"alertname": "TargetDown", "job": "coredns"}, True),
        ("dns-nxdomain", {"alertname": "KubePodNotReady", "namespace": "shop"}, False),
        ("mtu-blackhole", {"alertname": "NodeNetworkReceiveErrs", "instance": "x"}, True),
        ("webhook-down", {"alertname": "KubeAPIErrorBudgetBurn"}, True),
        ("alertmanager-down", {"alertname": "TargetDown", "job": "kps-alertmanager"}, True),
        ("prometheus-down", {"alertname": "PrometheusNotIngestingSamples"}, True),
    ],
)
def test_relevance_follows_the_written_lists(fault, labels, expected):
    assert trial.is_relevant(fault, labels) is expected


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_watchdog_never_counts(fault):
    assert not trial.is_relevant(fault, {"alertname": "Watchdog", "namespace": "shop", "job": "coredns"})


def test_every_fault_has_what_a_trial_needs():
    for fault, spec in FAULTS.items():
        assert spec["relevant"], fault
        assert spec["harm"].get("probe") or spec["harm"].get("gone"), fault
    assert len(FAULTS) == 10


# --- Outcomes ------------------------------------------------------------------------------------


def test_a_delivered_relevant_alert_is_caught_and_timed_from_harm():
    event(HARM + 900, "KubePodCrashLooping", namespace="shop", pod="web-1")
    result = trial.score("crashloop", T0, HARM)
    assert result["outcome"] == "caught"
    assert result["first_relevant_alert"] == "KubePodCrashLooping"
    assert result["seconds_after_harm"] == 900


def test_firing_without_delivery_is_its_own_outcome():
    seen("KubePodCrashLooping", pending=T0 + 60, firing=T0 + 960, namespace="shop")
    assert trial.score("crashloop", T0, HARM)["outcome"] == "fired, not delivered"


def test_a_rule_that_never_finished_waiting_is_pending_only():
    seen("KubePodNotReady", pending=T0 + 60, namespace="shop")
    assert trial.score("crashloop", T0, HARM)["outcome"] == "pending only"


def test_nothing_relevant_is_missed():
    event(HARM + 100, "NodeSystemSaturation", instance="x")
    result = trial.score("crashloop", T0, HARM)
    assert result["outcome"] == "missed"
    assert result["delivered_after_t0"] == ["NodeSystemSaturation"]


def test_an_alert_already_active_at_the_fault_earns_no_credit():
    seen("KubePodNotReady", pending=T0 - 200, firing=T0 - 100, last=T0 + 500, namespace="shop", pod="web-1")
    event(T0 - 90, "KubePodNotReady", namespace="shop", pod="web-1")
    event(T0 + 600, "KubePodNotReady", namespace="shop", pod="web-1")
    result = trial.score("crashloop", T0, HARM)
    assert result["outcome"] == "missed"
    assert result["baseline_alerts"] == ["KubePodNotReady"]


def test_a_startup_blip_that_resolved_is_not_baseline():
    seen("KubePodNotReady", pending=T0 - 600, last=T0 - 500, namespace="shop", pod="web-1")
    event(T0 - 590, "KubePodNotReady", namespace="shop", pod="web-1")
    event(T0 - 400, "KubePodNotReady", status="resolved", namespace="shop", pod="web-1")
    event(T0 + 600, "KubePodNotReady", namespace="shop", pod="web-1")
    result = trial.score("crashloop", T0, HARM)
    assert result["outcome"] == "caught"
    assert result["seen_before_t0"] == ["KubePodNotReady"]


def test_labels_added_on_the_way_to_alertmanager_do_not_split_identities():
    seen("KubePodNotReady", pending=T0 - 200, last=T0 + 10, namespace="shop")
    event(T0 + 600, "KubePodNotReady", namespace="shop", prometheus="monitoring/kps-prometheus")
    assert trial.score("crashloop", T0, HARM)["outcome"] == "missed"


def test_watchdog_gaps_are_reported():
    event(T0 + 60, "Watchdog")
    event(T0 + 120, "Watchdog")
    result = trial.score("alertmanager-down", T0, HARM)
    assert result["watchdog_last_seen_after_t0_s"] == 120
    assert result["watchdog_deliveries_after_t0"] == 2


# --- Harm ----------------------------------------------------------------------------------------


def probes(kind, results, end=HARM, ms=50):
    for i, ok in enumerate(results):
        trial.PROBES.append({"t": end - 59 + i, "kind": kind, "ok": ok, "ms": ms})


def test_too_few_probes_never_count_as_harm():
    probes("web", [False] * 4)
    assert not trial.harmed({"probe": "web"}, HARM + 1)


def test_failures_above_five_percent_are_harm():
    probes("web", [True] * 18 + [False] * 2)
    assert trial.harmed({"probe": "web"}, HARM + 1)


def test_slow_successes_are_harm_only_when_a_limit_is_set():
    probes("web", [True] * 20, ms=2000)
    assert not trial.harmed({"probe": "web"}, HARM + 1)
    assert trial.harmed({"probe": "web", "p95_ms": 1500}, HARM + 1)


def test_harm_only_looks_at_the_last_minute():
    probes("web", [False] * 20, end=HARM - 120)
    probes("web", [True] * 20)
    assert not trial.harmed({"probe": "web"}, HARM + 1)


# --- Summary -------------------------------------------------------------------------------------


def test_summary_counts_only_valid_trials(tmp_path):
    for name, status, outcome in (
        ("a", "valid", "caught"),
        ("b", "valid", "missed"),
        ("c", "invalid: no harm", "missed"),
    ):
        (tmp_path / name).mkdir()
        (tmp_path / name / "result.json").write_text(
            json.dumps({"number": 1, "name": name, "status": status, "outcome": outcome})
        )
    script = Path(__file__).resolve().parents[1] / "harness" / "summarize.py"
    out = subprocess.run(
        [sys.executable, str(script), str(tmp_path)], capture_output=True, text=True, check=True
    ).stdout
    assert "Valid trials: 2 of 3. Caught: 1 of 2." in out
