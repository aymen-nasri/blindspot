"""The command line: `blindspot faults list`, `doctor`, `lab up|down`, `run`, `score`, `report`."""

import argparse
import json
import shutil
import sys
from pathlib import Path

from blindspot import catalog, guard, lab, report, summary
from blindspot.runner import run_trial


def faults_list(args) -> int:
    faults = catalog.load(Path(args.catalog))
    print(f"{'#':>3}  {'id':<20} {'class':<14} {'runs on':<9} {'window':<7} name")
    for fault in sorted(faults.values(), key=lambda f: (f.fault_class == "control", f.number)):
        runs_on = ",".join(fault.runs_on)
        window = f"{fault.window_minutes:>3} min"
        print(f"{fault.number:>3}  {fault.id:<20} {fault.fault_class:<14} {runs_on:<9} {window}  {fault.name}")
    return 0


def doctor(args) -> int:
    ok = True
    tools = (
        ("docker", "kind", "kubectl", "helm")
        if lab.TYPE == "kind"
        else ("virsh", "ansible-playbook", "kubectl", "helm")
    )
    for tool in tools:
        found = shutil.which(tool)
        print(f"{'ok ' if found else 'MISSING'} {tool}")
        ok &= bool(found)
    try:
        catalog.load(Path(args.catalog))
        print("ok  fault files are valid")
    except Exception as error:  # noqa: BLE001 - reported to the user
        print(f"BAD fault files: {error}")
        ok = False
    try:
        reasons = guard.check()
        print("ok  current cluster has opted in" if not reasons else "--  current cluster: " + "; ".join(reasons))
    except Exception:  # noqa: BLE001 - no cluster is fine for doctor
        print("--  no current cluster")
    return 0 if ok else 1


def lab_cmd(args) -> int:
    if args.action == "up":
        print(lab.up(Path(args.lab), args.receiver_port))
    else:
        lab.down()
    return 0


def run_cmd(args) -> int:
    faults = catalog.load(Path(args.catalog))
    if args.fault not in faults:
        print(f"unknown fault {args.fault!r}. Try: blindspot faults list", file=sys.stderr)
        return 2
    fault = faults[args.fault]
    if lab.TYPE not in fault.runs_on:
        # Never fake a fault on a lab that can't make it honestly: kind can't fill a real node's disk.
        print(f"{fault.id} runs on {', '.join(fault.runs_on)}, not on the {lab.TYPE} lab", file=sys.stderr)
        return 2
    failed = 0
    # Each trial gets its own fresh cluster. In CI, each job runs one trial and passes its number with --trial.
    for trial in range(args.trial, args.trial + args.trials):
        out = Path(args.out) / f"{fault.id}-{trial}.json"
        result = run_trial(fault, Path(args.lab), out, trial=trial, keep_lab=args.keep_lab)
        if fault.expect and not result.get("control_passed"):
            print(f"CONTROL FAILED: {fault.id} trial {trial} expected {fault.expect!r}", file=sys.stderr)
            failed += 1
    return 3 if failed else 0


def score_cmd(args) -> int:
    results = report.load_results(Path(args.results), Path(args.catalog))
    results.sort(key=lambda r: (r.get("class") == "control", r.get("number") or 0, r.get("trial", 1)))
    print("### Every trial\n")
    print("| # | Fault | Trial | Status | Outcome | First relevant alert | After harm (s) | Recovered (s) |")
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        after, recovered = r.get("seconds_after_harm"), r.get("recovered_after_undo_s")
        print(
            f"| {r.get('number')} | {r.get('name')} | {r.get('trial', 1)} | {r.get('status')} | "
            f"{r.get('outcome') or ''} | {r.get('first_relevant_alert') or ''} | "
            f"{'' if after is None else after} | {'' if recovered is None else recovered} |"
        )
    expected_faults = json.loads(args.expect_faults) if args.expect_faults else None
    result = summary.summarise(
        results, min_valid=args.min_valid, expected_trials=args.expect_trials, expected_faults=expected_faults
    )
    print("\n### Each fault\n")
    print(summary.markdown(result))
    return 0


def report_cmd(args) -> int:
    data = report.build(
        Path(args.results), Path(args.catalog), Path(args.out), repo_url=args.repo_url, commit=args.commit
    )
    print(f"wrote {args.out}/index.html from {len(data['runs'])} runs. " + summary.headline(data["current"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blindspot", description=__doc__)
    parser.add_argument("--catalog", default="faults", help="directory of fault files")
    parser.add_argument("--lab", default="lab", help="directory with kind.yaml, kps-values.yaml and workload.yaml")
    parser.add_argument("--lab-type", choices=lab.LAB_TYPES, default="kind", help="kind, or real VMs from vmlab/")
    parser.add_argument("--vmlab", default="vmlab", help="directory with the VM lab's scripts")
    parser.add_argument(
        "--profile", choices=lab.PROFILES, default="a", help="a: chart defaults; c: defaults plus BlindSpot's fixes"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    faults = sub.add_parser("faults", help="list the fault catalogue")
    faults.add_argument("action", choices=["list"])
    faults.set_defaults(func=faults_list)

    sub.add_parser("doctor", help="check tools, fault files and the current cluster").set_defaults(func=doctor)

    lab_parser = sub.add_parser("lab", help="create or delete the kind lab")
    lab_parser.add_argument("action", choices=["up", "down"])
    lab_parser.add_argument("--receiver-port", type=int, default=8099)
    lab_parser.set_defaults(func=lab_cmd)

    run_parser = sub.add_parser("run", help="run trials of one fault, each on a fresh lab")
    run_parser.add_argument("--fault", required=True)
    run_parser.add_argument("--out", default="results")
    run_parser.add_argument("--trials", type=int, default=1, help="how many trials to run, one after another")
    run_parser.add_argument("--trial", type=int, default=1, help="the number of the first trial")
    run_parser.add_argument("--keep-lab", action="store_true", help="leave the cluster running afterwards")
    run_parser.set_defaults(func=run_cmd)

    score_parser = sub.add_parser("score", help="summarise result files, one row per fault")
    score_parser.add_argument("results")
    score_parser.add_argument("--min-valid", type=int, default=summary.MIN_VALID, help="valid trials a fault needs")
    score_parser.add_argument("--expect-trials", type=int, help="trials planned per fault, to count missing ones")
    score_parser.add_argument("--expect-faults", help="JSON list of the fault ids planned, to spot absent ones")
    score_parser.set_defaults(func=score_cmd)

    report_parser = sub.add_parser("report", help="build the static results page from every run under a directory")
    report_parser.add_argument("results", nargs="?", default="results")
    report_parser.add_argument("--out", default="site")
    report_parser.add_argument("--repo-url", default="", help="where raw results are linked, such as the GitHub repo")
    report_parser.add_argument("--commit", default="", help="the commit the page is built from")
    report_parser.set_defaults(func=report_cmd)

    args = parser.parse_args(argv)
    lab.use(args.lab_type, Path(args.vmlab), args.profile)
    try:
        return args.func(args)
    finally:
        lab.use("kind")


if __name__ == "__main__":
    sys.exit(main())
