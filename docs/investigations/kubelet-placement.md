# When Alertmanager's node breaks, nothing is delivered

## The question

In v0.3, the kubelet-stopped fault was caught in 4 of 5 trials. In trial 4, `TargetDown` and six
other alerts fired in Prometheus, but nothing reached the receiver, not even Watchdog after 534 s.
Then, in v0.3.1, "containerd stopped, then the app crashes" fired alerts in all 5 trials and delivered
none. What do the trials that delivered nothing have in common?

## What the trials show

Each trial records which node every monitoring pod ran on. Both faults break `blindspot-worker`, the
web app's node. All numbers come from the raw results in [results/v0.3](../../results/v0.3) (run
42626043262) and [results/v0.3.1](../../results/v0.3.1) (run 42652063472).

| Fault and trial | Prometheus on the faulted node | Alertmanager on the faulted node | Outcome | Last Watchdog after T0 |
|---|---|---|---|---|
| Kubelet stopped, 1 | yes | no | caught, 596 s after harm | 1,737 s |
| Kubelet stopped, 2 | yes | no | caught, 601 s after harm | 1,737 s |
| Kubelet stopped, 3 | no | no | caught, 600 s after harm | 1,735 s |
| Kubelet stopped, 4 | yes | **yes** | fired, not delivered | **534 s** |
| Kubelet stopped, 5 | yes | no | caught, 599 s after harm | 1,739 s |
| containerd, then a crash, 1 | no | **yes** | fired, not delivered | **536 s** |
| containerd, then a crash, 2 | no | **yes** | fired, not delivered | **534 s** |
| containerd, then a crash, 3 | no | **yes** | fired, not delivered | **536 s** |
| containerd, then a crash, 4 | no | **yes** | fired, not delivered | **535 s** |
| containerd, then a crash, 5 | no | **yes** | fired, not delivered | **538 s** |

In all 6 trials with Alertmanager on the faulted node, nothing was delivered after about 9 minutes. In
all 4 with Alertmanager elsewhere, the alert was delivered. Prometheus on the faulted node didn't
matter on its own: in kubelet trials 1, 2 and 5 it kept evaluating rules and sending alerts, because
stopping the kubelet doesn't stop containers that are already running.

In the containerd trials, `PrometheusNotConnectedToAlertmanagers` fired too: Prometheus itself
noticed it had no Alertmanager left, and had nowhere to send that alert either.

The containerd run is lopsided: Alertmanager landed on the faulted node in all five trials, by where
the scheduler happened to put it. So it shows what happens in that placement, and says nothing about
containerd failures elsewhere. In the first v0.3 run, Alertmanager landed on the faulted node in about
half the trials.

## The likely chain

This is the explanation that fits, not something each step of which was recorded:

1. The node can no longer run pods properly: with the kubelet stopped, it stops reporting; with
   containerd stopped, the kubelet reports the runtime down. Either way the node is marked not ready.
2. After 5 minutes, the default toleration for a not-ready or unreachable node runs out, and
   Kubernetes evicts the node's pods. Nothing on the node can stop them, so they stay in Terminating.
3. Prometheus finds Alertmanager through Kubernetes service discovery, so an Alertmanager pod that's
   terminating drops out of its list. Alerts stop reaching Alertmanager.
4. Alertmanager stops notifying about alerts it no longer hears about a few minutes later, which fits
   the last Watchdog at 534 to 538 s in all six.
5. Alertmanager is a StatefulSet with one replica here, so no replacement starts while the old pod is
   still terminating. The alerts that fire later have nowhere to go.

`PrometheusNotConnectedToAlertmanagers` firing supports step 3. Recording Prometheus's own list of
Alertmanagers during each trial would confirm it directly. That's not recorded yet.

## What it means

- With one Alertmanager, a kubelet or container runtime failure on its node silences every alert for
  the rest of the outage.
- The Watchdog alert is the safety net: it stopped arriving about 9 minutes in, every time. An outside
  dead man's switch, which pages when Watchdog goes quiet, would have caught all six. BlindSpot's
  receiver records Watchdog but doesn't page on its absence.
- The outcomes are still counted as "fired, not delivered", by the rules written before the runs.

## Proposed fixes, not yet tested

- Run Alertmanager with 2 or more replicas, spread across nodes with pod anti-affinity or topology
  spread constraints, so one node can't take out delivery.
- Use an outside dead man's switch on the Watchdog alert.

A later version can test these the same way: the same faults and trials, with Alertmanager spread out,
and the receiver counting deliveries.
