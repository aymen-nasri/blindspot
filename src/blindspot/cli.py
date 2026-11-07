"""The command line: `blindspot faults list`, `doctor`, `lab up|down`, `run`, `score`."""

import argparse
import json
import shutil
import sys
from pathlib import Path

from blindspot import catalog, guard, lab
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
    for tool in ("docker", "kind", "kubectl", "helm"):
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
    result = run_trial(fault, Path(args.lab), Path(args.out) / f"{fault.id}.json", keep_lab=args.keep_lab)
    if fault.expect and not result.get("control_passed"):
        print(f"CONTROL FAILED: {fault.id} expected {fault.expect!r}", file=sys.stderr)
        return 3
    return 0


def score_cmd(args) -> int:
    rows = sorted(
        (json.loads(p.read_text()) for p in Path(args.results).rglob("*.json")),
        key=lambda r: (r.get("class") == "control", r.get("number", 0)),
    )
    print("| # | Fault | Status | Outcome | First relevant alert | After harm (s) | Band | Recovered (s) |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(
            f"| {r.get('number')} | {r.get('name')} | {r.get('status')} | {r.get('outcome', '')} | "
            f"{r.get('first_relevant_alert') or ''} | {r.get('seconds_after_harm') or ''} | "
            f"{r.get('time_band') or ''} | {r.get('recovered_after_undo_s') or ''} |"
        )
    real = [r for r in rows if r.get("class") != "control" and r.get("status") == "valid"]
    caught = [r for r in real if r.get("outcome") == "caught"]
    controls = [r for r in rows if r.get("class") == "control"]
    print(f"\nCaught {len(caught)} of {len(real)} valid faults.")
    if controls:
        passed = sum(bool(r.get("control_passed")) for r in controls)
        print(f"Controls passed: {passed} of {len(controls)}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blindspot", description=__doc__)
    parser.add_argument("--catalog", default="faults", help="directory of fault files")
    parser.add_argument("--lab", default="lab", help="directory with kind.yaml, kps-values.yaml and workload.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    faults = sub.add_parser("faults", help="list the fault catalogue")
    faults.add_argument("action", choices=["list"])
    faults.set_defaults(func=faults_list)

    sub.add_parser("doctor", help="check tools, fault files and the current cluster").set_defaults(func=doctor)

    lab_parser = sub.add_parser("lab", help="create or delete the kind lab")
    lab_parser.add_argument("action", choices=["up", "down"])
    lab_parser.add_argument("--receiver-port", type=int, default=8099)
    lab_parser.set_defaults(func=lab_cmd)

    run_parser = sub.add_parser("run", help="run one trial of one fault on a fresh lab")
    run_parser.add_argument("--fault", required=True)
    run_parser.add_argument("--out", default="results")
    run_parser.add_argument("--keep-lab", action="store_true", help="leave the cluster running afterwards")
    run_parser.set_defaults(func=run_cmd)

    score_parser = sub.add_parser("score", help="summarise result files")
    score_parser.add_argument("results")
    score_parser.set_defaults(func=score_cmd)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
