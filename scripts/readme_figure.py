"""Draw the README's results figure from the committed results.

Run from the repository root:  python scripts/readme_figure.py
It scores results/ the same way the results page does, then writes two SVGs, one for each GitHub
theme, into docs/assets/. Nothing in the figure is typed by hand: every outcome and time comes from
the scored rows, so a rerun that changes a result changes the figure.
"""

import json
import tempfile
from html import escape
from pathlib import Path

from blindspot import report

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets"

# Short names that fit a tile, and the three groups the README uses.
SHORT = {
    "out-of-memory": "Out of memory",
    "bad-image": "Bad image in a rollout",
    "unready": "All replicas unready",
    "crashloop": "App keeps crashing",
    "cpu-throttle": "CPU throttled",
    "dns-nxdomain": "DNS returns NXDOMAIN",
    "dns-slow": "DNS very slow",
    "packet-loss": "40% packet loss",
    "mtu-blackhole": "MTU black hole",
    "netpol-deny": "NetworkPolicy block",
    "webhook-down": "Admission webhook",
    "alertmanager-down": "Alertmanager down",
    "prometheus-down": "Prometheus down",
    "disk-full": "Node disk full",
    "inodes-used-up": "Node out of inodes",
    "memory-pressure": "Node memory pressure",
    "kubelet-stopped": "Kubelet stopped",
    "containerd-stopped": "containerd, then a crash",
}
GROUPS = [
    ("Common app failures", "kind", ["out-of-memory", "bad-image", "unready"]),
    (
        "Harder infrastructure failures",
        "kind",
        [
            "crashloop",
            "cpu-throttle",
            "dns-nxdomain",
            "dns-slow",
            "packet-loss",
            "mtu-blackhole",
            "netpol-deny",
            "webhook-down",
            "alertmanager-down",
            "prometheus-down",
        ],
    ),
    (
        "Node failures on real VMs",
        "3 KVM VMs",
        ["disk-full", "inodes-used-up", "memory-pressure", "kubelet-stopped", "containerd-stopped"],
    ),
]

THEMES = {
    "light": {
        "bg": "#ffffff",
        "ink": "#14202e",
        "muted": "#5a6577",
        "rule": "#d5dbe4",
        "accent": "#2147b0",
        "caught": ("#1d7a4c", "#dcefe4"),
        "fired": ("#b45f08", "#f8e7d3"),
        "pending only": ("#8f6c00", "#f4ecd0"),
        "missed": ("#b1281f", "#ffffff"),
    },
    "dark": {
        "bg": "#0d1117",
        "ink": "#e6edf3",
        "muted": "#9aa5b5",
        "rule": "#30363d",
        "accent": "#8ea8ff",
        "caught": ("#52c58d", "#14301f"),
        "fired": ("#f0a650", "#3a2710"),
        "pending only": ("#dcc25c", "#332c10"),
        "missed": ("#ff7d72", "#0d1117"),
    },
}
FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
TILE_W, TILE_H, GAP, LEFT, PER_ROW = 164, 58, 8, 24, 5


def scored_rows() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        data = report.build(ROOT / "results", ROOT / "faults", Path(tmp))
    rows = {}
    for row in data["current"]["rows"]:
        if row["class"] in ("control", "quiet"):
            continue
        rows[(row["fault"], row["profile"])] = row
    return {"rows": rows, "caught": data["current"]["caught"], "of": data["current"]["of"]}


def label(row: dict) -> tuple[str, str]:
    """The outcome key for colours, and the words shown on the tile."""
    outcome = row["outcome"]
    if outcome == "caught":
        return "caught", f"Caught, ~{row['median_caught_min']} min"
    if outcome == "fired, not delivered":
        muted = row["fault"] == "cpu-throttle"
        return "fired", "Fired, muted" if muted else "Fired, reached nobody"
    if outcome == "pending only":
        return "pending only", "Waited, never fired"
    return "missed", "Missed"


def draw(theme: str, scored: dict) -> str:
    t = THEMES[theme]
    rows = scored["rows"]
    width = LEFT * 2 + PER_ROW * TILE_W + (PER_ROW - 1) * GAP
    parts, y = [], 40
    title = (
        f"Default alerts caught {scored['caught']} of {scored['of']} real Kubernetes failures"
        " (5 trials each, a fresh cluster every trial)"
    )
    parts.append(f'<text x="{LEFT}" y="{y}" font-size="17" font-weight="700" fill="{t["ink"]}">{escape(title)}</text>')
    y += 28
    legend = [("caught", "Caught: an alert was delivered"), ("fired", "Fired, reached nobody")]
    legend += [("pending only", "Started waiting, never fired"), ("missed", "Missed: nothing happened")]
    x = LEFT
    for key, words in legend:
        stroke, fill = t[key]
        dash = ' stroke-dasharray="4 3"' if key == "pending only" else ""
        parts.append(
            f'<rect x="{x}" y="{y - 11}" width="14" height="14" rx="3" fill="{fill}" stroke="{stroke}"'
            f' stroke-width="1.5"{dash}/>'
        )
        parts.append(f'<text x="{x + 20}" y="{y}" font-size="12.5" fill="{t["muted"]}">{escape(words)}</text>')
        x += 20 + len(words) * 6.6 + 22
    y += 22
    parts.append(
        f'<text x="{LEFT}" y="{y}" font-size="12.5" fill="{t["accent"]}" font-weight="600">'
        "FIXED: caught in at least 4 of 5 trials once BlindSpot’s 5 proposed rules were added</text>"
    )
    y += 18
    for name, lab, faults in GROUPS:
        caught = sum(rows[(f, "a")]["outcome"] == "caught" for f in faults)
        y += 22
        parts.append(
            f'<text x="{LEFT}" y="{y}" font-size="14" font-weight="700" fill="{t["ink"]}">{escape(name)}</text>'
        )
        parts.append(
            f'<text x="{width - LEFT}" y="{y}" font-size="13" text-anchor="end" fill="{t["muted"]}">'
            f"{escape(lab)} · {caught} of {len(faults)} caught</text>"
        )
        y += 10
        for i, fault in enumerate(faults):
            row = rows[(fault, "a")]
            key, words = label(row)
            stroke, fill = t[key]
            col, line = i % PER_ROW, i // PER_ROW
            tx = LEFT + col * (TILE_W + GAP)
            ty = y + line * (TILE_H + GAP)
            dash = ' stroke-dasharray="5 3"' if key == "pending only" else ""
            parts.append(
                f'<rect x="{tx}" y="{ty}" width="{TILE_W}" height="{TILE_H}" rx="6" fill="{fill}"'
                f' stroke="{stroke}" stroke-width="1.5"{dash}/>'
            )
            parts.append(
                f'<text x="{tx + 10}" y="{ty + 21}" font-size="13" font-weight="600" fill="{t["ink"]}">'
                f"{escape(SHORT[fault])}</text>"
            )
            parts.append(
                f'<text x="{tx + 10}" y="{ty + 42}" font-size="12" font-weight="600" fill="{stroke}">'
                f"{escape(words)}</text>"
            )
            fixed = rows.get((fault, "c"))
            if fixed and fixed["outcome"] == "caught":
                parts.append(
                    f'<text x="{tx + TILE_W - 10}" y="{ty + 42}" font-size="10.5" font-weight="700"'
                    f' text-anchor="end" fill="{t["accent"]}">FIXED</text>'
                )
        lines = (len(faults) + PER_ROW - 1) // PER_ROW
        y += lines * (TILE_H + GAP) + 6
    height = y + 12
    body = "\n  ".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}"'
        f' font-family="{FONT}" role="img" aria-label="{escape(title)}">\n'
        f'  <rect width="{width}" height="{height}" rx="10" fill="{t["bg"]}"/>\n  {body}\n</svg>\n'
    )


def main() -> None:
    scored = scored_rows()
    OUT.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        (OUT / f"results-{theme}.svg").write_text(draw(theme, scored), encoding="utf-8", newline="\n")
    summary = {k: scored[k] for k in ("caught", "of")}
    print(f"wrote docs/assets/results-light.svg and results-dark.svg: {json.dumps(summary)}")


if __name__ == "__main__":
    main()
