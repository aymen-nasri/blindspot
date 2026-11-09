"""Pure functions that turn many trial results into one row per fault, and a headline. No cluster needed.

The rules here are the ones docs/method-v0.2.md sets out before the v0.2 run.
"""

from collections import Counter
from statistics import median

# A fault with fewer valid trials than this is shown as "not enough data", and left out of the headline.
MIN_VALID = 3

# Rules are evaluated every 30 s, targets scraped every 30 s, and Alertmanager waits 30 s before a first
# notification. So a time is only good to about a minute, and is never shown more precisely than that.
TIME_RESOLUTION_S = 60


def minutes(seconds: float | None) -> int | None:
    return None if seconds is None else round(seconds / TIME_RESOLUTION_S)


def summarise_fault(trials: list[dict], *, min_valid: int = MIN_VALID, expected: int | None = None) -> dict:
    """One row for one fault, from all of its trials."""
    trials = sorted(trials, key=lambda r: r.get("trial", 1))
    first = trials[0]
    valid = [t for t in trials if t.get("status") == "valid"]
    counts = Counter(t["outcome"] for t in valid)
    row = {
        "fault": first["fault"],
        "lab": first.get("lab", "kind"),
        "run": first.get("_run"),
        "number": first.get("number"),
        "name": first.get("name"),
        "class": first.get("class"),
        "trials": len(trials),
        "valid": len(valid),
        "missing": max(0, expected - len(trials)) if expected else 0,
        "invalid": dict(sorted(Counter(t.get("status") for t in trials if t.get("status") != "valid").items())),
        "outcomes": dict(counts.most_common()),
        "first_alerts": dict(Counter(t["first_relevant_alert"] for t in valid if t.get("first_relevant_alert"))),
        "files": [t["_file"] for t in trials if "_file" in t],
    }

    if row["class"] == "quiet":
        noisy = [t for t in valid if (t.get("noise") or {}).get("delivered")]
        names = Counter(name for t in valid for name in (t.get("noise") or {}).get("delivered", []))
        if len(valid) < min_valid:
            row["outcome"] = "not enough data"
        else:
            row["outcome"] = "quiet" if not noisy else f"noisy in {len(noisy)} of {len(valid)}"
        row.update(noise_alerts=dict(names.most_common()), spread=spread(row) if valid else "")
        return row

    if row["class"] == "control":
        passed = sum(bool(t.get("control_passed")) for t in trials)
        row.update(outcome=f"passed {passed} of {len(trials)}", control_passed=passed == len(trials) and passed > 0)
        return row

    if len(valid) < min_valid:
        row.update(outcome="not enough data", stable=None)
    else:
        top, n = counts.most_common(1)[0]
        # A strict majority, or the fault is "mixed". Ties never round up to "caught".
        row.update(outcome=top if 2 * n > len(valid) else "mixed", stable=n == len(valid))

    caught_s = [
        t["seconds_after_harm"] for t in valid if t["outcome"] == "caught" and t.get("seconds_after_harm") is not None
    ]
    row["median_caught_s"] = median(caught_s) if caught_s else None
    row["median_caught_min"] = minutes(row["median_caught_s"])
    row["spread"] = spread(row)
    return row


def spread(row: dict) -> str:
    """The outcomes of every trial, in words: "4 caught; 1 missed; 1 invalid: no harm"."""
    parts = [f"{n} {outcome}" for outcome, n in row["outcomes"].items()]
    parts += [f"{n} {status}" for status, n in row["invalid"].items()]
    if row["missing"]:
        parts.append(f"{row['missing']} missing")
    return "; ".join(p for p in parts if p)


def summarise(
    results: list[dict],
    *,
    min_valid: int = MIN_VALID,
    expected_trials: int | None = None,
    expected_faults: list[str] | None = None,
) -> dict:
    """Rows for every fault, and the headline: caught K of N faults, after a median of T minutes."""
    # One row per fault and lab: a control runs on both labs, and each lab's trials are judged apart.
    by_fault: dict[tuple[str, str], list[dict]] = {}
    for result in results:
        by_fault.setdefault((result["fault"], result.get("lab", "kind")), []).append(result)

    rows = [summarise_fault(t, min_valid=min_valid, expected=expected_trials) for t in by_fault.values()]
    rows.sort(key=lambda r: (r["class"] == "control", r["number"] or 0, r["lab"]))
    absent = sorted(set(expected_faults or []) - {fault for fault, _ in by_fault})

    real = [r for r in rows if r["class"] not in ("control", "quiet") and r["outcome"] != "not enough data"]
    caught = [r for r in real if r["outcome"] == "caught"]
    controls = [r for r in rows if r["class"] == "control"]
    medians = [r["median_caught_s"] for r in caught if r["median_caught_s"] is not None]
    return {
        "rows": rows,
        "caught": len(caught),
        "of": len(real),
        "median_caught_min": minutes(median(medians)) if medians else None,
        "not_enough_data": [r["fault"] for r in rows if r["outcome"] == "not enough data"],
        "unstable": [r["fault"] for r in real if r["stable"] is False],
        "absent": absent,
        "controls": len(controls),
        "controls_passed": sum(bool(r["control_passed"]) for r in controls),
        "trusted": bool(controls) and all(r["control_passed"] for r in controls),
        "untrusted_labs": untrusted_labs(rows),
    }


def untrusted_labs(rows: list[dict]) -> list[str]:
    """Labs with fault rows whose controls didn't all pass there, or didn't run there at all."""
    labs = {r["lab"] for r in rows if r["class"] not in ("control", "quiet")}
    return sorted(
        lab
        for lab in labs
        if not any(r["class"] == "control" and r["lab"] == lab for r in rows)
        or not all(r["control_passed"] for r in rows if r["class"] == "control" and r["lab"] == lab)
    )


def headline(summary: dict) -> str:
    text = f"Caught {summary['caught']} of {summary['of']} faults"
    if summary["median_caught_min"] is not None:
        text += f", after a median of about {summary['median_caught_min']} minutes"
    return text + "."


def markdown(summary: dict) -> str:
    lines = [
        "| # | Fault | Trials | Outcome | Every trial | Median time to delivery | First relevant alert |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in summary["rows"]:
        time_cell = f"about {r['median_caught_min']} min" if r.get("median_caught_min") is not None else ""
        alerts = ", ".join(f"`{name}`" for name in r["first_alerts"])
        planned = r["trials"] + r["missing"]
        # Two controls are meant to end invalid, so for controls the count is of trials that ran.
        count = f"{r['trials']} of {planned} ran" if r["class"] == "control" else f"{r['valid']} valid of {planned}"
        lines.append(
            f"| {r['number'] or ''} | {r['name']} | {count} | {r['outcome']} | "
            f"{r.get('spread', '')} | {time_cell} | {alerts} |"
        )
    lines += ["", headline(summary)]
    if summary["controls"]:
        lines.append(f"Controls passed: {summary['controls_passed']} of {summary['controls']}.")
    for key, label in (
        ("unstable", "Not every trial agreed"),
        ("not_enough_data", "Not enough data"),
        ("absent", "No results at all"),
    ):
        if summary[key]:
            lines.append(f"{label}: {', '.join(summary[key])}.")
    return "\n".join(lines)
