# Method for v0.4

Written before any of these faults ran. Trials, scoring and windows work as in
[method-v0.2.md](method-v0.2.md) and [method-v0.3.md](method-v0.3.md). v0.4 grows the catalogue
towards the spec's full list. This file covers the first part: common app failures.

## Why these faults

The first ten faults were chosen because their results were hard to predict, so they say little about
the failures the default rules were built for. These three are those failures: a bad release in three
common forms. Adding them makes the overall picture fairer, and tests that the tool reports a catch
when there is one to report.

| # | Fault | How it's injected | Slowest rule naming the cause | Window |
|---|---|---|---|---|
| 2 | Out of memory | Memory limit of 8 MiB on the web app | 15 min (`KubePodCrashLooping`) | 30 min |
| 3 | Bad image in a rollout | An image tag that doesn't exist | 1 h (`KubeContainerWaiting`) | 75 min |
| 4 | All replicas unready | A readiness probe on a path that returns 404 | 15 min (`KubePodNotReady`) | 30 min |

All three run on kind, and any alert about the app's namespace counts. Harm must show within 5
minutes, as for every v0.2 fault.

## Predictions, written before the run

- All three should be caught, mostly by 15-minute app rules, so after about 16 minutes, like the crash.
- Out of memory has no alert of its own in the defaults. It should be caught as a crash loop.
- The bad image may be caught by `KubePodNotReady` or `KubeDeploymentReplicasMismatch` at about 15
  minutes, long before `KubeContainerWaiting`'s hour.

If any of these isn't caught, that's a finding, and the result stands as measured.

## The quiet test

Written before it ran. Catching more is worthless if it also cries wolf, so the quiet test measures
false alarms: an hour of normal cluster work with no fault, where every alert that reaches the receiver
counts as noise.

**The normal work**, on a fixed schedule from T0, in [`injectors.py`](../src/blindspot/injectors.py):

| Minute | Operation |
|---|---|
| 0 | Create a namespace with a CronJob that runs a short Python job every 5 minutes |
| 2, 12, 22, 32, 42, 52 | Rolling restart of the backend, waiting for it to finish |
| 5 and 35 | Scale the backend from 2 to 4 pods |
| 8 and 38 | Scale it back to 2 |
| 15 | Cordon the backend's node, so nothing new is scheduled there |
| 18 | Uncordon it |
| 25 | Create a two-replica batch Deployment |
| 45 | Delete it |

None of these should hurt users: the backend uses a rolling update with a readiness probe, and the web
app isn't touched.

**Rules:**

- Every alert delivered after T0 is noise, except `Watchdog`, `InfoInhibitor` and alerts already active
  at T0. Alerts that fire but aren't delivered are recorded too, separately.
- If the probes show harm in any minute, more than 5% failed web requests, the trial is invalid: the
  normal work itself hurt users, so it can't measure noise fairly.
- Five trials. The row reads "quiet" if no valid trial delivered noise, otherwise how many were noisy
  and which alerts did it. It never counts toward the catch headline.
- It runs on kind. The window is 60 minutes, so 15-minute rules have time to fire four times over.
