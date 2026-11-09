"""v0.2: turning five trials per fault into one row, and the headline. Written before the v0.2 run."""

import pytest

from blindspot import summary


def trial(fault="dns-nxdomain", outcome="missed", status="valid", n=1, seconds=None, alert=None, **extra):
    return {
        "fault": fault,
        "trial": n,
        "number": 8,
        "name": fault,
        "class": "network",
        "status": status,
        "outcome": outcome if status == "valid" else None,
        "seconds_after_harm": seconds,
        "first_relevant_alert": alert,
        **extra,
    }


def row(*trials, **kwargs):
    return summary.summarise_fault(list(trials), **kwargs)


def test_five_agreeing_trials_are_stable():
    r = row(*(trial(n=i) for i in range(1, 6)))
    assert (r["outcome"], r["stable"], r["valid"], r["spread"]) == ("missed", True, 5, "5 missed")


def test_a_strict_majority_wins_and_is_marked_unstable():
    r = row(*(trial(n=i, outcome="caught", seconds=900) for i in range(3)), trial(n=4), trial(n=5))
    assert (r["outcome"], r["stable"]) == ("caught", False)
    assert r["spread"] == "3 caught; 2 missed"


def test_a_tie_is_mixed_and_never_rounds_up_to_caught():
    trials = [trial(n=1, outcome="caught", seconds=60), trial(n=2, outcome="caught", seconds=60)]
    trials += [trial(n=3), trial(n=4)]
    assert row(*trials)["outcome"] == "mixed"


def test_no_majority_among_three_outcomes_is_mixed():
    trials = [trial(n=1, outcome="caught", seconds=60), trial(n=2, outcome="caught", seconds=60)]
    trials += [trial(n=3), trial(n=4), trial(n=5, outcome="pending only")]
    assert row(*trials)["outcome"] == "mixed"


def test_fewer_than_three_valid_trials_is_not_enough_data():
    trials = [trial(n=1), trial(n=2), trial(n=3, status="invalid: no harm"), trial(n=4, status="invalid: setup")]
    r = row(*trials)
    assert r["outcome"] == "not enough data"
    assert r["invalid"] == {"invalid: no harm": 1, "invalid: setup": 1}
    assert r["spread"] == "2 missed; 1 invalid: no harm; 1 invalid: setup"


def test_missing_trials_are_counted_against_the_plan():
    r = row(trial(n=1), trial(n=2), trial(n=3), expected=5)
    assert (r["missing"], r["outcome"]) == (2, "missed")
    assert r["spread"].endswith("2 missing")


def test_median_time_uses_caught_trials_only_and_whole_minutes():
    trials = [trial(n=i, outcome="caught", seconds=s) for i, s in enumerate((900, 950, 1000), 1)]
    trials.append(trial(n=4, outcome="missed"))
    r = row(*trials)
    assert (r["median_caught_s"], r["median_caught_min"]) == (950, 16)


def test_a_catch_at_zero_seconds_still_counts_for_time():
    assert row(trial(outcome="caught", seconds=0), min_valid=1)["median_caught_s"] == 0


@pytest.mark.parametrize(("seconds", "expected"), [(None, None), (29, 0), (31, 1), (944, 16), (89, 1), (91, 2)])
def test_minutes_round_to_the_nearest_minute(seconds, expected):
    assert summary.minutes(seconds) == expected


def test_controls_pass_only_when_every_trial_passes():
    good = [trial(fault="positive-crashloop", n=i, control_passed=True, **{"class": "control"}) for i in (1, 2)]
    assert row(*good)["control_passed"] is True
    bad = [*good, trial(fault="positive-crashloop", n=3, control_passed=False, **{"class": "control"})]
    r = row(*bad)
    assert (r["control_passed"], r["outcome"]) == (False, "passed 2 of 3")


def test_the_headline_counts_faults_not_trials():
    results = [trial(fault="crashloop", n=i, outcome="caught", seconds=940 + i) for i in range(1, 6)]
    results += [trial(fault="dns-nxdomain", n=i) for i in range(1, 6)]
    results += [trial(fault="mtu-blackhole", n=i, status="invalid: no harm") for i in range(1, 6)]
    results += [trial(fault="negative-quiet", n=1, control_passed=True, **{"class": "control"})]
    s = summary.summarise(results, expected_faults=["crashloop", "dns-nxdomain", "mtu-blackhole", "webhook-down"])
    assert (s["caught"], s["of"], s["median_caught_min"]) == (1, 2, 16)
    assert s["not_enough_data"] == ["mtu-blackhole"]
    assert s["absent"] == ["webhook-down"]
    assert s["trusted"] is True
    assert summary.headline(s) == "Caught 1 of 2 faults, after a median of about 16 minutes."


def test_results_without_controls_are_never_marked_trusted():
    assert summary.summarise([trial()], min_valid=1)["trusted"] is False


def test_the_markdown_table_has_one_row_per_fault():
    results = [trial(fault=f, n=i) for f in ("a", "b") for i in range(1, 4)]
    text = summary.markdown(summary.summarise(results))
    assert text.count("| 8 |") == 2
    assert "3 valid of 3" in text
    assert "Caught 0 of 2 faults." in text


# --- What each trial now records -----------------------------------------------------------------


def test_runner_details_always_include_the_cpu_count():
    from blindspot.runner import runner_details

    details = runner_details()
    assert details["cpus"] >= 1
    assert "python" in details


def test_timings_are_unknown_rather_than_fatal_without_a_lab(monkeypatch):
    from blindspot import runner

    def down(*_):
        raise OSError("no lab")

    monkeypatch.setattr(runner, "prometheus_get", down)
    monkeypatch.setattr(runner.lab, "alertmanager_route", down)
    assert runner.timings() == {"prometheus": "unknown", "alertmanager": "unknown"}
    assert runner.prometheus_started() is None


def test_timings_read_the_live_configuration(monkeypatch):
    import base64

    from blindspot import lab, runner

    config = "global:\n  scrape_interval: 30s\n  evaluation_interval: 30s\n"
    monkeypatch.setattr(runner, "prometheus_get", lambda path: {"data": {"yaml": config}})
    secret = base64.b64encode(b"route:\n  group_wait: 30s\n  group_interval: 5m\n  repeat_interval: 12h\n").decode()
    secrets = {"items": [{"data": {"other": "eA=="}}, {"data": {"alertmanager.yaml": secret}}]}
    monkeypatch.setattr(lab, "kubectl_json", lambda *args: secrets)
    assert runner.timings() == {
        "scrape_interval": "30s",
        "evaluation_interval": "30s",
        "group_wait": "30s",
        "group_interval": "5m",
        "repeat_interval": "12h",
    }


def test_run_writes_one_file_per_trial_number(monkeypatch, tmp_path):
    from blindspot import cli

    seen = []

    def fake_trial(fault, lab_dir, out, *, trial, keep_lab):
        seen.append((out.name, trial))
        return {"control_passed": True}

    monkeypatch.setattr(cli, "run_trial", fake_trial)
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    args = ["--catalog", str(root / "faults"), "run", "--fault", "crashloop", "--trial", "3", "--trials", "2"]
    assert cli.main([*args, "--out", str(tmp_path)]) == 0
    assert seen == [("crashloop-3.json", 3), ("crashloop-4.json", 4)]


def test_the_page_reads_old_one_trial_runs_by_their_own_plan(tmp_path):
    """v0.0 retried one fault, so it has two files for it. It's still a one-trial run, read as one."""
    from pathlib import Path

    from blindspot import report

    root = Path(__file__).resolve().parents[1]
    data = report.build(root / "results", root / "faults", tmp_path, repo_url="https://example.invalid/r")
    v0 = next(run for run in data["runs"] if run["name"] == "v0.0")
    assert v0["trials_per_fault"] == 1
    assert summary.headline(v0["summary"]).startswith("Caught 1 of 10 faults")
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "https://example.invalid/r/blob/main/results/" in page
    assert "<script>alert" not in page
    assert "Open question" in page, "the CPU fault's note in results/notes.yaml must reach the page"


def test_the_probe_timeline_shows_harm_fading():
    from blindspot.scoring import probe_timeline

    t0 = 1000.0
    probes = [{"t": t0 + i, "kind": "web", "ok": i >= 120, "ms": 900 if i < 120 else 20} for i in range(-60, 180)]
    timeline = probe_timeline(probes, t0, t0 + 180)
    assert [row["minute"] for row in timeline] == [-1, 0, 1, 2, 3]
    assert timeline[0]["web"] == {"n": 60, "failed": 60, "p95_ms": None}
    assert timeline[3]["web"] == {"n": 60, "failed": 0, "p95_ms": 20}
    assert "web" not in timeline[4]


# --- Reaching VM lab nodes -----------------------------------------------------------------------


def test_node_commands_go_over_ssh_with_one_quoted_remote_line(monkeypatch):
    from blindspot import shell

    calls = []
    monkeypatch.setattr(shell, "run", lambda args, **kw: calls.append(args))
    nodes = shell.SshNodes(hosts={"blindspot-worker": "192.168.122.11"}, key="/k")
    shell.use_ssh(nodes)
    try:
        shell.on_node("blindspot-worker", "fallocate -l 1G '/var/lib/blind spot'")
        assert shell.node_ip("blindspot-worker") == "192.168.122.11"
        with pytest.raises(ValueError, match="isn't a node"):
            shell.on_node("prod-node-1", "true")
    finally:
        shell.use_ssh(None)
    args = calls[0]
    assert args[:3] == ["ssh", "-i", "/k"]
    assert args[-2] == "blindspot@192.168.122.11"
    assert args[-1] == "sudo sh -c 'fallocate -l 1G '\"'\"'/var/lib/blind spot'\"'\"''"


def test_without_ssh_node_commands_use_the_kind_container(monkeypatch):
    from blindspot import shell

    calls = []
    monkeypatch.setattr(shell, "run", lambda args, **kw: calls.append(args))
    shell.on_node("blindspot-worker", "true")
    assert calls == [["docker", "exec", "blindspot-worker", "sh", "-c", "true"]]


def test_a_kind_fault_is_refused_on_the_vm_lab(capsys):
    from pathlib import Path

    from blindspot import cli, lab, shell

    root = Path(__file__).resolve().parents[1]
    code = cli.main(["--catalog", str(root / "faults"), "--lab-type", "vm", "run", "--fault", "dns-nxdomain"])
    assert code == 2
    assert "runs on kind, not on the vm lab" in capsys.readouterr().err
    assert lab.TYPE == "kind" and shell._ssh is None, "the CLI must leave the process pointed back at kind"


# --- Node faults (VM lab) ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("used", "avail", "percent", "expected"),
    [(20, 80, 100, 80), (20, 80, 80, 60), (90, 10, 80, 0), (0, 100, 50, 50), (50, 50, 100, 50)],
)
def test_fill_size_reaches_the_percent_df_reports(used, avail, percent, expected):
    from blindspot.injectors import fill_size

    assert fill_size(used, avail, percent) == expected


class Done:
    def __init__(self, out=""):
        self.stdout = out


def node_calls(monkeypatch, outputs=()):
    from blindspot import injectors

    calls, replies = [], list(outputs)

    def fake(node, command, check=True):
        calls.append((node, command))
        return Done(replies.pop(0) if replies else "")

    monkeypatch.setattr(injectors, "on_node", fake)
    return calls


def test_disk_fill_is_sized_from_df_and_undone_by_one_file(monkeypatch):
    from blindspot import injectors

    calls = node_calls(monkeypatch, ["1000 9000\n"])
    state = injectors.node_fill_disk({"node": "blindspot-worker", "percent": 80})
    assert state == {"node": "blindspot-worker", "bytes": 7000}
    assert calls[1] == ("blindspot-worker", "fallocate -l 7000 /var/lib/blindspot-fill")
    injectors.undo_node_fill_disk({}, state)
    assert calls[2] == ("blindspot-worker", "rm -f /var/lib/blindspot-fill")


def test_only_kubelet_and_containerd_can_be_stopped(monkeypatch):
    from blindspot import injectors

    calls = node_calls(monkeypatch)
    with pytest.raises(ValueError, match="only stops kubelet, containerd"):
        injectors.node_stop_service({"node": "blindspot-worker", "service": "sshd"})
    assert calls == []


def test_clock_skew_is_undone_by_the_opposite_move(monkeypatch):
    from blindspot import injectors

    calls = node_calls(monkeypatch)
    state = injectors.node_clock_skew({"node": "blindspot-worker", "minutes": 10})
    injectors.undo_node_clock_skew({}, state)
    assert calls[0][1] == "timedatectl set-ntp false && date -s '+10 minutes'"
    assert calls[1][1] == "date -s '-10 minutes' && timedatectl set-ntp true"


@pytest.mark.parametrize(
    ("injector", "params"),
    [
        ("node_clock_skew", {"node": "n", "minutes": 0}),
        ("node_clock_skew", {"node": "n", "minutes": 500}),
        ("node_stress_memory", {"node": "n", "percent": 5}),
        ("node_fill_disk", {"node": "n", "percent": 0}),
        ("node_fill_disk", {}),
    ],
)
def test_node_faults_refuse_bad_settings_before_touching_a_node(monkeypatch, injector, params):
    from blindspot import injectors

    calls = node_calls(monkeypatch)
    with pytest.raises(ValueError):
        getattr(injectors, injector)(params)
    assert calls == []


def test_the_inode_script_is_valid_python_and_reports_its_count(monkeypatch):
    import ast
    import shlex

    from blindspot import injectors

    calls = node_calls(monkeypatch, ["1310720\n"])
    assert injectors.node_fill_inodes({"node": "n"}) == {"node": "n", "files": 1310720}
    ast.parse(shlex.split(calls[0][1])[2])


def test_a_fault_can_allow_harm_longer_to_show_and_old_faults_keep_five_minutes():
    import yaml

    from blindspot.catalog import Fault

    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    doc = yaml.safe_load((root / "faults/workloads/crashloop.yaml").read_text())
    assert Fault.model_validate(doc).harm.within_minutes is None
    doc["harm"]["within_minutes"] = 10
    assert Fault.model_validate(doc).harm.within_minutes == 10
    doc["harm"]["within_minutes"] = 60
    with pytest.raises(ValueError):
        Fault.model_validate(doc)


def test_placement_keeps_only_monitoring_and_app_pods(monkeypatch):
    from blindspot import runner

    def pod(ns, name, node):
        return {"metadata": {"namespace": ns, "name": name}, "spec": {"nodeName": node}}

    items = [pod("monitoring", "prometheus-kps-0", "w1"), pod("shop", "web-1", "w1"), pod("kube-system", "dns", "cp")]
    monkeypatch.setattr(runner, "kubectl_json", lambda *a: {"items": items})
    assert runner.placement() == {"monitoring/prometheus-kps-0": "w1", "shop/web-1": "w1"}


# --- Which run each row comes from (v0.3) --------------------------------------------------------


def run_of(name, fault, trials, valid=None, lab="kind", cls="harm"):
    valid = trials if valid is None else valid
    return [
        {
            "fault": fault,
            "lab": lab,
            "class": cls,
            "_run": name,
            "trial": i,
            "status": "valid" if i <= valid else "invalid: setup",
            "outcome": "missed",
            "number": 8,
            "name": fault,
        }
        for i in range(1, trials + 1)
    ]


def test_a_newer_run_with_enough_data_replaces_an_older_one():
    from blindspot.report import current

    chosen = current([("v0.1", run_of("v0.1", "dns", 1)), ("v0.2", run_of("v0.2", "dns", 5))])
    assert {r["_run"] for r in chosen} == {"v0.2"}


def test_a_newer_run_without_enough_data_keeps_the_older_one():
    from blindspot.report import current

    chosen = current([("v0.2", run_of("v0.2", "dns", 5)), ("smoke", run_of("smoke", "dns", 1))])
    assert {r["_run"] for r in chosen} == {"v0.2"}


def test_a_fault_never_measured_enough_keeps_its_latest_run():
    from blindspot.report import current

    chosen = current([("a", run_of("a", "disk", 1)), ("b", run_of("b", "disk", 5, valid=2))])
    assert {r["_run"] for r in chosen} == {"b"}


def test_controls_are_kept_apart_per_lab_and_vm_rows_need_vm_controls():
    from blindspot.report import current

    kind = run_of("v0.2", "positive", 5, cls="control") + run_of("v0.2", "dns", 5)
    vm = run_of("v0.3", "disk", 5, lab="vm")
    for r in kind:
        r["control_passed"] = True
    s = summary.summarise(current([("v0.2", kind), ("v0.3", vm)]))
    assert [(r["fault"], r["lab"], r["run"]) for r in s["rows"]] == [
        ("dns", "kind", "v0.2"),
        ("disk", "vm", "v0.3"),
        ("positive", "kind", "v0.2"),
    ]
    assert s["untrusted_labs"] == ["vm"]
