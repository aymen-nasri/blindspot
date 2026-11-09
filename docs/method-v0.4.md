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
