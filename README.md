# BlindSpot

Breaks a test Kubernetes cluster in real ways, and measures which failures the monitoring actually
tells someone about.

## Result

kube-prometheus-stack 92.22.0 with its default rules **delivered an alert for 1 of 10 real failures**,
after a median of about 16 minutes. Each fault ran 5 times, each time on a fresh kind cluster, and
checks outside the cluster confirmed the harm before a trial was scored. Every fault gave the same
outcome in all 5 of its trials, and all 20 control trials passed.

**Results page:** [aymen-nasri.github.io/blindspot](https://aymen-nasri.github.io/blindspot/), built
from the raw results in [results/v0.2](results/v0.2).

| # | Failure | What the monitoring did, in 5 of 5 trials |
|---|---|---|
| 1 | App keeps crashing | **Caught.** `KubePodCrashLooping` delivered 942 to 956 s after the app went down |
| 5 | CPU throttled | Fired `CPUThrottlingHigh` after 918 to 943 s, but it has `info` severity, and `InfoInhibitor` muted it. Nobody was told. [What this row means](#what-the-cpu-faults-row-means) |
| 8 | Upstream DNS returns NXDOMAIN | Nothing fired |
| 9 | DNS very slow | Nothing fired |
| 10 | 40% packet loss into a backend | `KubePodNotReady` started waiting, after 7 to 583 s, and never fired within 30 minutes |
| 11 | MTU black hole | Nothing fired |
| 14 | NetworkPolicy blocks the app | Nothing fired |
| 25 | Admission webhook down | `KubeAPIErrorBudgetBurn` started waiting after 83 to 118 s, and never fired |
| 32 | Alertmanager down, then the app crashes | Alerts fired, including `PrometheusNotConnectedToAlertmanagers` after 857 to 891 s, and none could be delivered |
| 33 | Prometheus down | Nothing fired. The always-on `Watchdog` alert stopped arriving about 3 minutes later, which only an outside dead man's switch would notice |

The negative control ran 5 times for 20 minutes with no fault, and no alert of any kind fired. So the
defaults are quiet, at least in this small lab, but that isn't a full test for false alarms yet.

Earlier runs, with one trial each, gave the same outcomes:
[results/v0.0](results/v0.0) and [results/v0.1](results/v0.1). How it was measured:
[docs/method-v0.md](docs/method-v0.md) for one trial, and [docs/method-v0.2.md](docs/method-v0.2.md)
for combining five, written before the v0.2 run.

**What this does and doesn't show.** Ten faults, on kind, with a small app. They weren't picked at
random: the crash was included as one the defaults should catch, and the other nine because their
results were the hardest to predict. So 1 of 10 isn't a catch rate for failures in general, and common
app failures such as a bad image or a failing readiness probe are what the default rules are built for.
It shows that default rules miss many infrastructure failures, not what any particular production
cluster misses. Many default
rules wait 15 minutes before firing by design, and the scoring counts those as caught: the crash above
was caught after about 16 minutes, as designed. The chart sends alerts to a receiver called `null` by
default, so a real team would also have to configure delivery. This lab did that.

### What the CPU fault's row means

Harm is confirmed once, near the start of each trial. For the CPU fault, it was confirmed about 40 s in,
but by the end of the window the probes no longer met the harm threshold. A rerun that recorded every
probe minute by minute ([docs/investigations/cpu-throttle.md](docs/investigations/cpu-throttle.md))
showed why: the harm that made those trials valid was the web pods restarting in the first minute, when
the new limit replaced them. After that, throttling made the app 15 to 100 times slower for the whole
window, but never past the 1.5 s threshold set before the run.

So fault 5's row reads "throttling went unreported", not "users were hurt and nobody was told".
`CPUThrottlingHigh` fired every time and was muted every time.

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
blindspot run --fault dns-nxdomain --trials 5 --out results/mine
blindspot score results/mine
blindspot report results --out site
```

Each trial creates its own kind cluster and deletes it afterwards. Add `--keep-lab` to look around.
`blindspot report` builds the results page from every run under `results/`.

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

- **v0.0, done:** the go/no-go test. Its throwaway harness is kept in [harness/](harness) so its results
  can be reproduced.
- **v0.1, done:** the tool in [src/blindspot](src/blindspot), with faults and scoring as files, a safety
  guard, undo and controls.
- **v0.2, done:** 5 trials per fault on GitHub Actions, results in Git, and the
  [results page](https://aymen-nasri.github.io/blindspot/).
- **v0.3, in progress:** a lab of real VMs (QEMU, cloud-init, Ansible and kubeadm, in [vmlab/](vmlab)),
  for faults that kind can't make honestly. On a free runner it builds a three-node Kubernetes 1.37
  cluster in about 4.5 minutes. Five node faults (full disk, no inodes, memory pressure, kubelet or
  containerd stopped) are written, with their rules set in [docs/method-v0.3.md](docs/method-v0.3.md)
  before any of them ran. They have no results yet.
- **Later:** more faults, a quiet test for false alarms, and proposed fixes with tested rules.

Why it's built this way: [docs/decisions](docs/decisions).

## AI use

Built with the help of an AI coding assistant.
