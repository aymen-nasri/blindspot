# Why one kubelet trial fired but delivered nothing

## The question

In v0.3, the kubelet-stopped fault was caught in 4 of 5 trials. In trial 4, `TargetDown` and six
other alerts fired in Prometheus, but nothing reached the receiver, not even Watchdog after 534 s.
What was different about trial 4?

## What the trials show

Each trial records which node every monitoring pod ran on. The fault always stops the kubelet on
`blindspot-worker`, the web app's node. All numbers come from the raw results in
[results/v0.3](../../results/v0.3) (run 42626043262).

| Trial | Prometheus on the faulted node | Alertmanager on the faulted node | Outcome | Last Watchdog after T0 |
|---|---|---|---|---|
| 1 | yes | no | caught, 596 s after harm | 1,737 s |
| 2 | yes | no | caught, 601 s after harm | 1,737 s |
| 3 | no | no | caught, 600 s after harm | 1,735 s |
| 4 | yes | **yes** | fired, not delivered | **534 s** |
| 5 | yes | no | caught, 599 s after harm | 1,739 s |

Only trial 4 had Alertmanager on the faulted node, and only trial 4 lost delivery. Prometheus on the
faulted node didn't matter on its own: in trials 1, 2 and 5 it kept evaluating rules and sending
alerts, because stopping the kubelet doesn't stop containers that are already running.

Watchdog kept arriving for about 9 minutes in trial 4, so delivery didn't stop when the kubelet did.

## The likely chain

This is the explanation that fits, not something each step of which was recorded:

1. The kubelet stops, so the node stops reporting. Within a minute the node is marked unreachable
   and its pods are marked not ready. That's the harm, 56 to 61 s in across the five trials.
2. After 5 minutes, the default toleration for an unreachable node runs out and Kubernetes evicts the
   node's pods. With no kubelet to stop them, they stay in Terminating, still running.
3. Prometheus finds Alertmanager through Kubernetes service discovery, so an Alertmanager pod that's
   not ready and terminating drops out of its list. Alerts stop reaching Alertmanager.
4. Alertmanager stops notifying about alerts it no longer hears about a few minutes later, which fits
   the last Watchdog at 534 s.
5. Alertmanager is a StatefulSet with one replica here, so no replacement starts while the old pod is
   still terminating. The alerts that fire at about 10 minutes have nowhere to go.

Recording Prometheus's own list of Alertmanagers during each trial would confirm steps 3 and 4. That's
not recorded yet.

## What it means

- With one Alertmanager, a kubelet failure on its node silences every alert for the whole outage. That
  happened in 1 of 5 trials here, purely by where the scheduler put the pod.
- The Watchdog alert is the safety net: it stopped arriving at 534 s. An outside dead man's switch,
  which pages when Watchdog goes quiet, would have caught this trial too. BlindSpot's receiver records
  Watchdog but doesn't page on its absence.
- The outcome is still counted as "fired, not delivered", by the rules written before the run. The
  fault's majority outcome is caught, in 4 of 5.

## Proposed fixes, not yet tested

- Run Alertmanager with 2 or more replicas, spread across nodes with pod anti-affinity or topology
  spread constraints, so one node can't take out delivery.
- Use an outside dead man's switch on the Watchdog alert.

A later version can test these the same way: the same fault and trials, with Alertmanager spread out,
and the receiver counting deliveries.
