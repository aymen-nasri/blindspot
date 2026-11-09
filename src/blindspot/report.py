"""Builds the static results page from the result files kept in Git. No cluster needed.

Every number on the page comes from summary.summarise over the files in results/, so anyone can check
the page against the raw trials it links to.
"""

import json
import re
from collections import Counter
from html import escape
from pathlib import Path

import yaml

from blindspot import catalog, summary

BADGES = {
    "caught": "caught",
    "fired, not delivered": "fired",
    "pending only": "pending",
    "missed": "missed",
    "mixed": "mixed",
    "not enough data": "nodata",
}
CATEGORY_NAMES = {
    "workloads": "Workloads",
    "network": "Network",
    "control-plane": "Control plane",
    "monitoring": "Monitoring itself",
    "nodes": "Nodes",
    "storage": "Storage",
}


def load_results(directory: Path, catalog_dir: Path | None = None) -> list[dict]:
    """Every result file under a directory. Old results without a class get it from the catalogue."""
    known = catalog.load(catalog_dir) if catalog_dir and catalog_dir.is_dir() else {}
    results = []
    for path in sorted(directory.rglob("*.json")):
        result = json.loads(path.read_text())
        if "fault" not in result:
            continue
        if not result.get("class") and result["fault"] in known:
            result["class"] = known[result["fault"]].fault_class
        result["_file"] = path.relative_to(directory).as_posix()
        results.append(result)
    return results


def version_key(path: Path) -> tuple:
    numbers = re.findall(r"\d+", path.name)
    return (tuple(int(n) for n in numbers), path.name)


def describe_run(name: str, results: list[dict]) -> dict:
    """One run directory: its summary, plus what it ran on."""
    per_fault = Counter(r["fault"] for r in results)
    # The planned number of trials is the usual count per fault. A retried trial doesn't change it.
    trials = Counter(per_fault.values()).most_common(1)[0][0]
    # Older runs had one trial per fault by design, so they're read with the rule they were run under.
    found = summary.summarise(results, min_valid=min(summary.MIN_VALID, trials))
    dates = sorted(r["started"][:10] for r in results if r.get("started"))
    return {
        "name": name,
        "summary": found,
        "trials_per_fault": trials,
        "first_day": dates[0] if dates else None,
        "last_day": dates[-1] if dates else None,
        "cpus": sorted({r["runner"]["cpus"] for r in results if r.get("runner", {}).get("cpus")}),
        "charts": sorted({r["chart"] for r in results if r.get("chart")}),
    }


def build(results_dir: Path, catalog_dir: Path, out: Path, *, repo_url: str = "", commit: str = "") -> dict:
    faults = catalog.load(catalog_dir)
    categories = {p.stem: p.parent.name for p in catalog_dir.rglob("*.yaml")}
    runs = []
    for run_dir in sorted((p for p in results_dir.iterdir() if p.is_dir()), key=version_key):
        results = load_results(run_dir, catalog_dir)
        if results:
            runs.append(describe_run(run_dir.name, results))
    if not runs:
        raise ValueError(f"no result files under {results_dir}")

    notes_file = results_dir / "notes.yaml"
    notes = yaml.safe_load(notes_file.read_text(encoding="utf-8")) if notes_file.is_file() else {}
    out.mkdir(parents=True, exist_ok=True)
    page = render(runs, faults, categories, repo_url=repo_url.rstrip("/"), commit=commit, notes=notes or {})
    (out / "index.html").write_text(page, encoding="utf-8")
    data = {"commit": commit or None, "runs": runs}
    (out / "results.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


# --- HTML ----------------------------------------------------------------------------------------


def link(repo_url: str, path: str, text: str, title: str = "") -> str:
    href = f"{repo_url}/blob/main/{path}" if repo_url else path
    title_attr = f' title="{escape(title)}"' if title else ""
    return f'<a href="{escape(href)}"{title_attr}>{escape(text)}</a>'


def run_days(run: dict) -> str:
    if not run["first_day"]:
        return "not recorded"
    return run["first_day"] if run["first_day"] == run["last_day"] else f"{run['first_day']} to {run['last_day']}"


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def badge(outcome: str) -> str:
    kind = BADGES.get(outcome, "nodata")
    return f'<span class="badge {kind}">{escape(outcome)}</span>'


def fault_rows(run: dict, faults: dict, categories: dict, repo_url: str, notes: dict) -> str:
    rows = []
    for row in run["summary"]["rows"]:
        if row["class"] == "control":
            continue
        fault = faults.get(row["fault"])
        category = categories.get(row["fault"], "other")
        time_cell = f"about {row['median_caught_min']} min" if row.get("median_caught_min") is not None else "–"
        alerts = ", ".join(f"<code>{escape(a)}</code>" for a in row["first_alerts"]) or "–"
        trials = " ".join(
            link(repo_url, f"results/{run['name']}/{f}", str(i), f"Trial {i}: raw result")
            for i, f in enumerate(row["files"], 1)
        )
        why = f'<div class="why">{escape(fault.why)}</div>' if fault else ""
        if row["fault"] in notes:
            why += f'<div class="note"><strong>Note:</strong> {escape(notes[row["fault"]])}</div>'
        rows.append(
            f'<tr data-category="{escape(category)}">'
            f'<td class="num">{row["number"]}</td>'
            f"<td><strong>{escape(row['name'])}</strong>{why}</td>"
            f"<td>{escape(CATEGORY_NAMES.get(category, category))}</td>"
            f"<td>{badge(row['outcome'])}</td>"
            f"<td>{escape(row['spread'])}</td>"
            f"<td>{time_cell}</td>"
            f"<td>{alerts}</td>"
            f'<td class="trials">{trials}</td>'
            "</tr>"
        )
    return "\n".join(rows)


def control_rows(run: dict, faults: dict) -> str:
    rows = []
    for row in run["summary"]["rows"]:
        if row["class"] != "control":
            continue
        fault = faults.get(row["fault"])
        expect = escape(fault.expect) if fault and fault.expect else "–"
        result = "passed" if row["control_passed"] else "failed"
        passed = row["outcome"].removeprefix("passed ")
        rows.append(
            f"<tr><td><strong>{escape(row['name'])}</strong></td><td>{expect}</td>"
            f'<td><span class="badge {"caught" if row["control_passed"] else "missed"}">{result}</span>: '
            f"{escape(passed)} trials</td></tr>"
        )
    return "\n".join(rows)


def history_rows(runs: list[dict]) -> str:
    rows = []
    for run in reversed(runs):
        s = run["summary"]
        days = run_days(run)
        cpus = ", ".join(f"{c} CPUs" for c in run["cpus"]) or "not recorded"
        controls = f"{s['controls_passed']} of {s['controls']}" if s["controls"] else "none run"
        rows.append(
            f"<tr><td>{escape(run['name'])}</td><td>{escape(days)}</td>"
            f"<td>{run['trials_per_fault']}</td><td>{escape(summary.headline(s))}</td>"
            f"<td>{controls}</td><td>{escape(', '.join(run['charts']))}</td><td>{escape(cpus)}</td></tr>"
        )
    return "\n".join(rows)


def render(
    runs: list[dict], faults: dict, categories: dict, *, repo_url: str, commit: str, notes: dict | None = None
) -> str:
    latest = runs[-1]
    s = latest["summary"]
    shown = sorted({categories.get(r["fault"], "other") for r in s["rows"] if r["class"] != "control"})
    filters = "".join(
        f'<button type="button" data-filter="{escape(c)}">{escape(CATEGORY_NAMES.get(c, c))}</button>' for c in shown
    )
    trust = (
        ""
        if s["trusted"]
        else '<p class="warn"><strong>Not trusted:</strong> a control failed or none ran in this run, so these '
        "numbers aren't a finding.</p>"
    )
    unstable = (
        f"<p>Not every trial agreed for: {escape(', '.join(s['unstable']))}. The table shows each one.</p>"
        if s["unstable"]
        else "<p>Every fault with enough data gave the same outcome in all of its valid trials.</p>"
    )
    negative = next((r for r in s["rows"] if r["fault"] == "negative-quiet"), None)
    noise = (
        f"The negative control ran {plural(negative['trials'], 'time')}, 20 minutes each with no fault, and "
        f"{'no alert about the app earned credit in any of them' if negative['control_passed'] else 'it FAILED'}."
        if negative
        else "The negative control didn't run in this run."
    )
    method = link(repo_url, "docs/method-v0.2.md", "how five trials become one row")
    method_v0 = link(repo_url, "docs/method-v0.md", "how one trial works")
    source = link(repo_url, f"results/{latest['name']}", f"results/{latest['name']}")
    built_from = f"commit <code>{escape(commit[:7])}</code>" if commit else "the result files"
    repo = f'<a href="{escape(repo_url)}">Source and raw results</a>' if repo_url else ""
    median = (
        f", after a median of about <strong>{s['median_caught_min']} minutes</strong>"
        if s["median_caught_min"] is not None
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BlindSpot results</title>
<meta name="description"
  content="What default Kubernetes monitoring tells someone about, measured by breaking a test cluster in real ways.">
<style>
:root {{
  --bg: #fbfaf7; --fg: #1c1c1a; --muted: #5d5c57; --line: #e2dfd8; --card: #ffffff; --accent: #2457c5;
  --caught: #1d7348; --fired: #9a5600; --pending: #76601a; --missed: #b0281f; --mixed: #5a4a8c; --nodata: #66655f;
  --warn-bg: #fdf0ee;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #161615; --fg: #ecebe6; --muted: #a8a69e; --line: #34332f; --card: #1e1e1c; --accent: #8fb0ff;
    --caught: #5fcf95; --fired: #f0a64a; --pending: #d9c46a; --missed: #ff8a80; --mixed: #b9a8f0; --nodata: #a8a69e;
    --warn-bg: #3a201d;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 40px 16px 64px; }}
h1 {{ font-size: 15px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: 0 0 12px; }}
.headline {{ font-size: clamp(26px, 4.4vw, 42px); line-height: 1.15; margin: 0 0 14px;
  font-weight: 650; max-width: 22em; }}
.sub {{ color: var(--muted); max-width: 46em; margin: 0 0 8px; }}
h2 {{ font-size: 20px; margin: 44px 0 10px; }}
p {{ max-width: 46em; }}
a {{ color: var(--accent); }}
code {{ font: 13px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace; }}
.warn {{ background: var(--warn-bg); border-left: 4px solid var(--missed); padding: 10px 14px; }}
.table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; background: var(--card); }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ text-align: left; vertical-align: top; padding: 10px 12px; border-bottom: 1px solid var(--line); }}
th {{ font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600; }}
tr:last-child td {{ border-bottom: 0; }}
td.num {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
#faults td:nth-child(2) {{ min-width: 16em; }}
.why {{ color: var(--muted); font-size: 13px; margin-top: 4px; max-width: 30em; }}
.note {{ font-size: 13px; margin-top: 6px; max-width: 30em; padding: 6px 8px; border-left: 3px solid var(--fired);
  background: var(--warn-bg); }}
.trials a {{ display: inline-block; min-width: 1.6em; text-align: center; }}
.badge {{ display: inline-block; white-space: nowrap; font-weight: 600; font-size: 13px; }}
.badge::before {{ content: ""; display: inline-block; width: .6em; height: .6em; border-radius: 50%;
  margin-right: .4em; background: currentColor; }}
.caught {{ color: var(--caught); }} .fired {{ color: var(--fired); }} .pending {{ color: var(--pending); }}
.missed {{ color: var(--missed); }} .mixed {{ color: var(--mixed); }} .nodata {{ color: var(--nodata); }}
.filters {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }}
.filters button {{ font: inherit; font-size: 14px; padding: 5px 12px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--line); background: var(--card); color: var(--fg); }}
.filters button[aria-pressed="true"] {{ border-color: var(--fg); font-weight: 600; }}
ul {{ max-width: 46em; padding-left: 20px; }}
footer {{ margin-top: 48px; color: var(--muted); font-size: 14px; }}
</style>
</head>
<body>
<main>
<h1>BlindSpot</h1>
<p class="headline">Default monitoring caught <strong>{s["caught"]} of {s["of"]}</strong>
real Kubernetes failures{median}.</p>
<p class="sub">kube-prometheus-stack {escape(", ".join(latest["charts"]))} with its default rules, on a fresh kind
cluster for every trial. {plural(latest["trials_per_fault"], "trial")} per fault, run
{escape(run_days(latest))}. Each fault was confirmed with checks outside the cluster before it was
scored. Caught means a relevant alert reached a receiver. Time runs from the harm starting to that delivery.</p>
{trust}

<h2>Every fault</h2>
<div class="filters" role="group" aria-label="Filter by area">
<button type="button" data-filter="all" aria-pressed="true">All</button>{filters}
</div>
<div class="table-wrap">
<table id="faults">
<thead><tr><th>#</th><th>Failure</th><th>Area</th><th>Outcome</th><th>Every trial</th><th>Median time</th>
<th>First relevant alert</th><th>Raw trials</th></tr></thead>
<tbody>
{fault_rows(latest, faults, categories, repo_url, notes or {})}
</tbody>
</table>
</div>
{unstable}
<p><strong>Outcomes:</strong> <span class="badge caught">caught</span> a relevant alert was delivered.
<span class="badge fired">fired, not delivered</span> it fired in Prometheus and reached nobody.
<span class="badge pending">pending only</span> a rule's condition became true, but its wait never ended.
<span class="badge missed">missed</span> nothing relevant happened.
<span class="badge mixed">mixed</span> no outcome won more than half the valid trials.</p>

<h2>Controls</h2>
<p>These check the tool itself. If one fails, no number above can be trusted.</p>
<div class="table-wrap">
<table>
<thead><tr><th>Control</th><th>Must produce</th><th>Result</th></tr></thead>
<tbody>
{control_rows(latest, faults)}
</tbody>
</table>
</div>

<h2>False alarms</h2>
<p>{escape(noise)} A longer quiet test, which counts every alert raised during normal work, isn't built yet.</p>

<h2>History</h2>
<div class="table-wrap">
<table>
<thead><tr><th>Run</th><th>Date</th><th>Trials per fault</th><th>Result</th><th>Controls passed</th>
<th>Chart</th><th>Runner</th></tr></thead>
<tbody>
{history_rows(runs)}
</tbody>
</table>
</div>
<p>v0.0 and v0.1 ran on private-repo runners with 2 CPUs. Later runs record their runner in each result.</p>

<h2>Method and limits</h2>
<ul>
<li>Read {method_v0}, and {method}. Which alerts count for each fault is written in its file before it runs.</li>
<li>Everything that measures runs outside the cluster: the alert receiver, the probes and the clock.</li>
<li>Changes from the chart's defaults are disclosed: a webhook receiver (the default sends everything to
<code>null</code>), Watchdog sent to it every minute, control-plane metrics exposed, and Grafana off.</li>
<li>Many default rules wait 15 minutes by design. Those count as caught, and the time shows the wait.</li>
<li>Only kind so far. Faults that need real machines, such as full disks or clock drift, aren't measured yet.</li>
<li>A small reference app, not a production cluster. This shows what default rules can miss, not what any
particular team's monitoring misses.</li>
</ul>

<footer>Built from {built_from}. Raw results: {source}. {repo}</footer>
</main>
<script>
document.querySelectorAll(".filters button").forEach(function (button) {{
  button.addEventListener("click", function () {{
    var want = button.dataset.filter;
    document.querySelectorAll(".filters button").forEach(function (b) {{
      b.setAttribute("aria-pressed", String(b === button));
    }});
    document.querySelectorAll("#faults tbody tr").forEach(function (row) {{
      row.hidden = want !== "all" && row.dataset.category !== want;
    }});
  }});
}});
</script>
</body>
</html>
"""
