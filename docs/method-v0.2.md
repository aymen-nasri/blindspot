# Method for v0.2

Written before the v0.2 run. One trial works exactly as in [method-v0.md](method-v0.md). This file adds
how five trials of a fault become one result. If a rule here changes after results exist, the change
gets its own commit saying why.

## What changes from v0.1

- **Five trials per fault**, and five per control. Each trial gets its own job and its own fresh cluster.
- **Bigger runners.** The repo is now public, so trials run on GitHub's public-repo `ubuntu-24.04`
  runners, with 4 CPUs and 16 GB of memory. v0.0 and v0.1 ran on private-repo runners with 2 CPUs and
  7 GB. Each result now records the CPUs, memory, kernel and runner image, so the difference is visible.
- **More recorded per trial:** the live scrape and rule evaluation intervals, Alertmanager's waits, and
  whether Prometheus restarted during the window.

Nothing else changes: the same chart (92.22.0), the same fault files, the same lists of which alerts
count, and the same windows.

## From five trials to one row

| Rule | Why |
|---|---|
| Only trials with status `valid` are scored. Invalid ones are listed with their reason | A broken trial must never count as a monitoring failure |
| A fault with fewer than 3 valid trials is shown as **not enough data**, and left out of the headline | Two trials can't show whether an outcome repeats |
| A fault's outcome is the one in **more than half** of its valid trials. Otherwise it's **mixed** | A tie never rounds up to caught |
| If every valid trial agrees, the fault is stable. If not, the table shows every trial's outcome | Flaky detection is a finding too |
| Time is the median, over the trials that caught it, from harm to delivery | Trials that missed have no time to average |
| Times are shown in whole minutes | Rules are evaluated every 30 s, targets scraped every 30 s, Alertmanager waits 30 s before a first notification, and BlindSpot reads Prometheus's alert list every 15 s. A time is only good to about a minute |
| A job that produces no result file counts as a **missing** trial | So a crashed job can't quietly drop a bad result |

## The headline

"Caught K of N faults, after a median of about T minutes." N counts the faults with enough data. K
counts those whose outcome is caught. T is the median of those faults' median times.

## Controls

Every control trial must pass. If any fails, the run's results are marked as not trusted, and the
README and results page say so rather than showing the score as a finding.

## How the result is used

The v0.2 table replaces the one-trial table as the headline. Where a fault's outcome differs from its
v0.0 or v0.1 trial, the README says so. The earlier results stay in `results/` as they were.

## Limits

- Still kind only. Five trials show whether an outcome repeats, not how rare failures behave.
- The runner change from 2 to 4 CPUs could change timings or outcomes. If outcomes differ from v0.1,
  this is the first thing to check, using the runner details in each result.
