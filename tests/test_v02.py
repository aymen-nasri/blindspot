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
