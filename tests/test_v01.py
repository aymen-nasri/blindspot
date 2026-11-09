"""v0.1: the fault files, the scoring, the guard and the injectors' bookkeeping, with no cluster."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from blindspot import catalog, injectors, scoring
from blindspot.catalog import Fault, Step
from blindspot.cli import main
from blindspot.guard import reasons_to_refuse
from blindspot.scoring import Observations

ROOT = Path(__file__).resolve().parents[1]
FAULTS = catalog.load(ROOT / "faults")
T0, HARM = 1000.0, 1050.0


# --- Fault files ---------------------------------------------------------------------------------


def test_the_catalogue_has_the_ten_faults_and_four_controls():
    real = [f for f in FAULTS.values() if f.fault_class not in ("control", "quiet") and "kind" in f.runs_on]
    controls = [f for f in FAULTS.values() if f.fault_class == "control"]
    # The ten from v0.0, plus the three common app failures added in v0.4.
    assert sorted(f.number for f in real) == [1, 2, 3, 4, 5, 8, 9, 10, 11, 14, 25, 32, 33]
    assert sorted(f.expect for f in controls) == ["caught", "invalid: no harm", "invalid: receiver", "not caught"]


def test_every_step_type_is_one_the_tool_knows():
    for fault in FAULTS.values():
        for step in fault.inject:
            assert step.type in injectors.INJECTORS


def test_the_catalogue_matches_the_v0_test_it_came_from():
    """v0.1 must judge each fault exactly as the v0.0 test did, so its results stay comparable."""
    import faults as v0

    for fault_id, old in v0.FAULTS.items():
        new = FAULTS[fault_id]
        assert new.number == old["number"]
        assert [(c.alertname, c.labels) for c in new.relevant] == [(name, labels) for name, labels in old["relevant"]]
        assert new.harm.model_dump(exclude_none=True) == old["harm"]


def fault_doc(**changes) -> dict:
    doc = yaml.safe_load((ROOT / "faults/workloads/crashloop.yaml").read_text())
    doc.update(changes)
    return doc


@pytest.mark.parametrize(
    "changes",
    [
        {"unknown_setting": 1},
        {"class": "harm", "expect": "caught"},
        {"harm": None},
        {"inject": []},
        {"relevant": []},
        {"harm": {}},
        {"harm": {"p95_ms": 100}},
        {"inject": [{"type": "rm -rf"}]},
        {"relevant": [{"alertname": "("}]},
        {"window_minutes": 1},
        {"id": "Has Spaces"},
    ],
)
def test_bad_fault_files_are_refused(changes):
    with pytest.raises(ValidationError):
        Fault.model_validate(fault_doc(**changes))


def test_a_control_must_say_what_it_expects():
    with pytest.raises(ValidationError):
        Fault.model_validate(fault_doc(**{"class": "control"}))


def test_the_file_name_must_match_the_id(tmp_path):
    (tmp_path / "other-name.yaml").write_text(yaml.safe_dump(fault_doc()))
    with pytest.raises(ValueError, match="must match the file name"):
        catalog.load(tmp_path)


# --- Scoring -------------------------------------------------------------------------------------


def obs(events=(), alerts=(), probes=()) -> Observations:
    o = Observations()
    for t, status, labels in events:
        o.events.append({"t": t, "status": status, "labels": labels})
    for labels, times in alerts:
        o.alerts[scoring.identity(labels)] = {"labels": labels, **times}
    o.probes.extend(probes)
    return o


def test_caught_is_timed_from_harm_and_banded():
    o = obs(events=[(HARM + 963, "firing", {"alertname": "KubePodCrashLooping", "namespace": "shop"})])
    result = scoring.score(FAULTS["crashloop"], o, T0, HARM)
    assert (result["outcome"], result["seconds_after_harm"], result["time_band"]) == ("caught", 963, "within 30 min")


@pytest.mark.parametrize(
    ("seconds", "band"), [(300, "within 5 min"), (301, "within 15 min"), (1800, "within 30 min"), (1801, "over 30 min")]
)
def test_bands_have_exact_edges(seconds, band):
    assert scoring.band(seconds) == band


def test_firing_without_delivery_and_pending_only():
    fired = obs(
        alerts=[({"alertname": "KubePodNotReady", "namespace": "shop"}, {"firing_t": T0 + 900, "last_seen": T0 + 900})]
    )
    assert scoring.score(FAULTS["crashloop"], fired, T0, HARM)["outcome"] == "fired, not delivered"
    pending = obs(
        alerts=[({"alertname": "KubePodNotReady", "namespace": "shop"}, {"pending_t": T0 + 60, "last_seen": T0 + 60})]
    )
    assert scoring.score(FAULTS["crashloop"], pending, T0, HARM)["outcome"] == "pending only"


def test_an_alert_active_at_the_fault_earns_nothing():
    labels = {"alertname": "KubePodNotReady", "namespace": "shop"}
    o = obs(
        events=[(T0 - 50, "firing", labels), (T0 + 600, "firing", labels)],
        alerts=[(labels, {"firing_t": T0 - 50, "last_seen": T0 + 600})],
    )
    assert scoring.score(FAULTS["crashloop"], o, T0, HARM)["outcome"] == "missed"


def test_a_resolved_startup_blip_is_not_baseline():
    labels = {"alertname": "KubePodNotReady", "namespace": "shop"}
    o = obs(
        events=[(T0 - 600, "firing", labels), (T0 - 400, "resolved", labels), (T0 + 600, "firing", labels)],
        alerts=[(labels, {"pending_t": T0 - 700, "last_seen": T0 - 400})],
    )
    assert scoring.score(FAULTS["crashloop"], o, T0, HARM)["outcome"] == "caught"


@pytest.mark.parametrize(
    ("fault_id", "result", "passes"),
    [
        ("positive-crashloop", {"status": "valid", "outcome": "caught"}, True),
        ("positive-crashloop", {"status": "valid", "outcome": "fired, not delivered"}, False),
        ("positive-crashloop", {"status": "invalid: no harm"}, False),
        ("negative-quiet", {"status": "valid", "outcome": "missed"}, True),
        ("negative-quiet", {"status": "valid", "outcome": "caught"}, False),
        ("negative-quiet", {"status": "invalid: setup"}, False),
        ("harm-noop", {"status": "invalid: no harm"}, True),
        ("harm-noop", {"status": "valid", "outcome": "missed"}, False),
        ("delivery-off", {"status": "invalid: receiver"}, True),
        ("delivery-off", {"status": "valid", "outcome": "missed"}, False),
    ],
)
def test_controls_pass_only_on_the_expected_result(fault_id, result, passes):
    assert scoring.meets_expectation(FAULTS[fault_id], result) is passes


def test_harm_needs_five_probes_and_looks_only_at_the_last_minute():
    old_failures = [{"t": HARM - 200 + i, "kind": "web", "ok": False, "ms": 1} for i in range(20)]
    fresh = [{"t": HARM - 50 + i, "kind": "web", "ok": True, "ms": 1} for i in range(20)]
    assert not scoring.harmed_by_probes(old_failures + fresh, "web", HARM)
    assert not scoring.harmed_by_probes([{"t": HARM - 1, "kind": "web", "ok": False, "ms": 1}] * 4, "web", HARM)


# --- Guard ---------------------------------------------------------------------------------------


def test_the_guard_accepts_only_a_marked_lab():
    assert reasons_to_refuse("kind-blindspot", "uid-1", "uid-1", 3) == []


@pytest.mark.parametrize(
    ("context", "marker", "nodes", "reason"),
    [
        ("arn:aws:eks:prod", "uid-1", 3, "context"),
        ("kind-blindspot", None, 3, "no blindspot-allowed"),
        ("kind-blindspot", "uid-copied", 3, "different cluster"),
        ("kind-blindspot", "uid-1", 40, "more than a lab"),
    ],
)
def test_the_guard_refuses_anything_else(context, marker, nodes, reason):
    reasons = reasons_to_refuse(context, marker, "uid-1", nodes)
    assert any(reason in r for r in reasons)


# --- Injector bookkeeping ------------------------------------------------------------------------


def test_a_failed_step_undoes_the_steps_before_it(monkeypatch):
    calls = []
    monkeypatch.setitem(
        injectors.INJECTORS,
        "noop",
        (lambda p: calls.append(("inject", p["n"])) or {"n": p["n"]}, lambda p, s: calls.append(("undo", s["n"]))),
    )

    def boom(_):
        raise RuntimeError("no")

    monkeypatch.setitem(injectors.INJECTORS, "apply", (boom, lambda p, s: None))
    steps = [Step(type="noop", params={"n": 1}), Step(type="noop", params={"n": 2}), Step(type="apply")]
    with pytest.raises(RuntimeError):
        injectors.inject_all(steps)
    assert calls == [("inject", 1), ("inject", 2), ("undo", 2), ("undo", 1)]


def test_undo_keeps_going_after_a_failure(monkeypatch):
    def bad_undo(p, s):
        raise RuntimeError("stuck")

    undone = []
    monkeypatch.setitem(injectors.INJECTORS, "apply", (lambda p: {}, bad_undo))
    monkeypatch.setitem(injectors.INJECTORS, "noop", (lambda p: {}, lambda p, s: undone.append(True)))
    errors = injectors.undo_all([(Step(type="noop"), {}), (Step(type="apply"), {})])
    assert undone == [True]
    assert errors == ["apply: stuck"]


def test_missing_parameters_are_refused_before_anything_runs():
    with pytest.raises(ValueError, match="missing parameters: patch"):
        injectors.deployment_patch({"namespace": "shop", "name": "web"})


# --- CLI -----------------------------------------------------------------------------------------


def test_faults_list_runs(capsys):
    assert main(["--catalog", str(ROOT / "faults"), "faults", "list"]) == 0
    assert "mtu-blackhole" in capsys.readouterr().out


def test_score_summarises_results(tmp_path, capsys):
    (tmp_path / "a.json").write_text(
        '{"fault": "a", "number": 1, "name": "a", "class": "harm", "status": "valid", "outcome": "caught",'
        ' "seconds_after_harm": 900}'
    )
    (tmp_path / "b.json").write_text(
        '{"fault": "b", "number": 8, "name": "b", "class": "harm", "status": "valid", "outcome": "missed"}'
    )
    (tmp_path / "c.json").write_text(
        '{"fault": "c", "number": 0, "name": "c", "class": "control", "control_passed": true}'
    )
    assert main(["score", str(tmp_path), "--min-valid", "1"]) == 0
    out = capsys.readouterr().out
    assert "Caught 1 of 2 faults, after a median of about 15 minutes." in out
    assert "Controls passed: 1 of 1." in out


def test_score_reads_the_committed_v0_results(capsys):
    """Old result files still load, with their class taken from the catalogue."""
    assert main(["--catalog", str(ROOT / "faults"), "score", str(ROOT / "results/v0.0"), "--min-valid", "1"]) == 0
    assert "Caught 1 of 10 faults" in capsys.readouterr().out


# --- Recovery ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gone", "probe_failing", "expected"),
    [(True, True, False), (False, True, False), (True, False, False), (False, False, True)],
)
def test_recovery_needs_every_harm_condition_cleared(monkeypatch, gone, probe_failing, expected):
    from blindspot import runner
    from blindspot.catalog import Harm
    from blindspot.measure import Recorder

    monkeypatch.setattr(runner.lab, "component_gone", lambda name: gone)
    recorder = Recorder()
    recorder.obs.probes.extend({"t": HARM - 30 + i, "kind": "web", "ok": not probe_failing, "ms": 5} for i in range(10))
    harm = Harm(probe="web", gone="alertmanager")
    assert runner.recovered(harm, recorder, HARM) is expected


def test_node_faults_run_only_on_vms_and_target_the_web_apps_node():
    nodes = [f for f in FAULTS.values() if "vm" in f.runs_on and f.fault_class != "control"]
    assert sorted(f.number for f in nodes) == [17, 18, 20, 22, 23]
    for fault in nodes:
        assert fault.runs_on == ["vm"]
        assert {step.params["node"] for step in fault.inject if step.type.startswith("node-")} == {"blindspot-worker"}
        assert fault.is_relevant({"alertname": "KubePodNotReady", "namespace": "shop"})
        assert not fault.is_relevant({"alertname": "KubePodNotReady", "namespace": "monitoring"})


def test_every_control_runs_on_both_labs():
    """A lab's results can't be trusted until its controls pass there."""
    for fault in FAULTS.values():
        if fault.fault_class == "control":
            assert sorted(fault.runs_on) == ["kind", "vm"], fault.id
