# 3. A fresh cluster for every trial

**Status:** accepted, built in v0.0.

## Context

Alerts carry state. A rule that started waiting during one trial, an alert still firing from the last
fault, or Alertmanager remembering it already notified someone would all change the next trial's
result. Undoing a fault doesn't reset any of that, and an undo can fail.

## Decision

Each trial creates its own kind cluster, installs the monitoring from a pinned chart version, and
deletes the cluster at the end. In CI, each trial also gets its own job and runner.

## Consequences

- No trial can affect another, so repeated trials are independent.
- A failed undo can't damage later trials. It's recorded as a finding instead.
- Each trial pays several minutes of setup. On free public runners that costs time, not money.
- Before each fault, the trial waits for a Watchdog delivery and a 5-minute baseline, so startup alerts
  are recorded and never earn credit.
