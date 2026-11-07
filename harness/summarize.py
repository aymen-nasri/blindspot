"""Turn the trial results into one table and the go/no-go line."""

import json
import sys
from pathlib import Path

rows = sorted(
    (json.loads(p.read_text()) for p in Path(sys.argv[1]).rglob("result.json")),
    key=lambda r: r.get("number", 0),
)
lines = [
    "| # | Fault | Status | Harm after (s) | Outcome | First relevant alert | After harm (s) "
    "| Delivered after the fault |",
    "|---|---|---|---|---|---|---|---|",
]
for r in rows:
    lines.append(
        f"| {r.get('number')} | {r.get('name')} | {r.get('status')} | {r.get('harm_after_t0_s', '')} | "
        f"{r.get('outcome', '')} | {r.get('first_relevant_alert') or ''} | {r.get('seconds_after_harm') or ''} | "
        f"{', '.join(r.get('delivered_after_t0', [])) or 'nothing'} |"
    )
valid = [r for r in rows if r.get("status") == "valid"]
caught = [r for r in valid if r.get("outcome") == "caught"]
lines += [
    "",
    f"Valid trials: {len(valid)} of {len(rows)}. Caught: {len(caught)} of {len(valid)}.",
    "Decision rule, written before the run: stop if 9 or more of 10 are caught.",
]
print("\n".join(lines))
