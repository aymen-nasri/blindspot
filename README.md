# BlindSpot

Breaks a test Kubernetes cluster in real ways, and measures which failures the monitoring actually
tells someone about.

## First result

In a feasibility test, kube-prometheus-stack 92.22.0 with its default rules **delivered an alert for 1
of 10 real failures**. Each fault ran once, on a fresh kind cluster, and checks outside the cluster
confirmed it really happened before it was scored.

| # | Failure | What the monitoring did |
|---|---|---|
| 1 | App keeps crashing | **Caught.** `KubePodCrashLooping` delivered 925 s after the app went down |
| 5 | CPU throttled | Fired `CPUThrottlingHigh` after 938 s, but it has `info` severity, and `InfoInhibitor` muted it. Nobody was told |
| 8 | Upstream DNS returns NXDOMAIN | Nothing fired |
| 9 | DNS very slow | Nothing fired |
| 10 | 40% packet loss into a backend | `KubePodNotReady` started waiting after 303 s, and never fired within 30 minutes |
| 11 | MTU black hole | Nothing fired |
| 14 | NetworkPolicy blocks the app | Nothing fired |
| 25 | Admission webhook down | `KubeAPIErrorBudgetBurn` started waiting after 118 s, and never fired |
| 32 | Alertmanager down, then the app crashes | Alerts fired, including `PrometheusNotConnectedToAlertmanagers`, and none could be delivered |
| 33 | Prometheus down | Nothing fired. The always-on `Watchdog` alert stopped arriving 177 s later, which only an outside dead man's switch would notice |

Raw results, one file per trial: [results/v0.0](results/v0.0). How it was measured: [docs/method-v0.md](docs/method-v0.md).

**What this does and doesn't show.** One trial per fault, on kind, with a small app. It's a signal that
default rules miss many infrastructure failures, not a finding about any particular production cluster.
Many default rules wait 15 minutes before firing by design, and the scoring counts those as caught: the
crash above was caught after about 15 minutes, as designed. The chart sends alerts to a receiver called
`null` by default, so a real team would also have to configure delivery. This test did that.

## How a trial works

1. Build a fresh kind cluster, install the monitoring being judged and a small reference app.
2. Wait for a `Watchdog` delivery, which proves alerts can reach the receiver.
3. Watch a 5-minute baseline, and record anything already firing.
4. Inject one fault, then confirm with probes outside the cluster that it really hurt the app.
5. Watch for the fault's window, at least 30 minutes, timing alerts as pending, firing and delivered.
6. Undo the fault, check the app recovers, and delete the cluster.

Everything that measures runs outside the cluster: the receiver, the probes and the clock. Which alerts
count for each fault is written in its file before it runs.

## Usage

Needs Docker, kind, kubectl and Helm. One trial takes about 45 minutes.

```bash
python3 -m pip install .
blindspot doctor
blindspot faults list
blindspot run --fault dns-nxdomain --out results
blindspot score results
```

`blindspot run` creates its own kind cluster and deletes it afterwards. Add `--keep-lab` to look around.

## Faults and controls

Each fault is a YAML file in [faults/](faults): what to break, how to confirm the harm, which alerts
count, and why the fault matters. Adding a fault means adding a file.

Four controls check the tool itself. If one fails, no result from that version can be trusted:

| Control | Must produce |
|---|---|
| A crash the defaults should catch | caught |
| No fault at all, for 20 minutes | nothing caught |
| A fault that does nothing | thrown out for causing no harm |
| No alert receiver | thrown out, never scored as missed |

## Safety

BlindSpot breaks things on purpose. It refuses any cluster whose context doesn't start with `kind-` or
`blindspot-`, that lacks a marker holding the cluster's own ID, or that has more nodes than a lab
should. These checks stop mistakes, not someone determined to get around them, so **never point it at a
cluster that matters.**

## Status

- **v0.0, done:** the go/no-go test above. Its throwaway harness is kept in [harness/](harness) so its results can be reproduced.
- **v0.1, in progress:** the tool in [src/blindspot](src/blindspot), with the same faults and scoring as files, a safety guard, undo and controls.
- **Next:** repeated trials, faults that need real VMs, a quiet test for false alarms, proposed fixes, and a results page.

## AI use

Built with the help of an AI coding assistant.
