# Method for v0.3

Written before any node fault ran. One trial works as in [method-v0.md](method-v0.md), and five trials
become one row as in [method-v0.2.md](method-v0.2.md). This file adds the VM lab and the node faults.

## Why a second lab

kind's nodes are containers that share the runner's kernel and disk. Filling a kind node's disk fills
the runner, and stopping its kubelet isn't what happens on a real machine. So node faults run on real
VMs, or not at all ([decision 5](decisions/0005-kind-first-then-vms.md)).

## The VM lab

[vmlab/](../vmlab) starts three Ubuntu 24.04 guests under KVM on the CI runner, each with its own kernel
and disk, and Ansible builds them into a kubeadm cluster: one control plane and two workers, named like
the kind lab's nodes. It runs Kubernetes 1.37.0, the same version as the kind lab, with the same
control-plane metrics changes, the same chart and values, and the same app. The CNI is Flannel, because
kind's own CNI only exists inside kind.

| Node | vCPUs | Memory | Disk |
|---|---|---|---|
| Each of the 3 | 2 | 4 GiB | 20 GiB |

## The node faults

All five break `blindspot-worker`, the node the web app runs on. Each window is the longest default
wait among the rules that name the fault's cause, plus 15 minutes, and never under 30 minutes, so no
such rule is scored as missed while it's still waiting by design. App alerts count too, but they don't
set the window: most wait 15 minutes, which every window covers, and one, `KubeContainerWaiting`,
waits an hour, which would make every window 75 minutes.

| # | Fault | Longest relevant wait | Window | Harm must show within |
|---|---|---|---|---|
| 17 | Node disk full | 30 min (`NodeFilesystemAlmostOutOfSpace`) | 45 min | 10 min |
| 18 | Node out of inodes | 1 h (`NodeFilesystemAlmostOutOfFiles`) | 75 min | 10 min |
| 20 | Node memory pressure | 15 min (`NodeMemoryHighUtilization`) | 30 min | 10 min |
| 22 | Kubelet stopped | 15 min (`KubeNodeNotReady`) | 30 min | 5 min |
| 23 | containerd stopped | 15 min (`KubeNodeNotReady`) | 30 min | 10 min |

Harm may take longer than 5 minutes to show for four of them, because it comes through the kubelet
evicting pods or the node going NotReady, so they allow 10. Every v0.2 fault keeps 5.

Which alerts count is in each file under [faults/nodes](../faults/nodes): any alert about the app's
namespace, plus alerts that name the cause, such as `NodeFilesystem.*` for a full disk.

## Changes after the smoke test, before the full run

One trial of each node fault ran first, as a smoke test (run 42615901291). Its results aren't scored.
It found four problems, fixed before the full run:

| Problem | What the smoke test showed | Change |
|---|---|---|
| Disk full couldn't be injected | `fallocate` of exactly the free space `df` reports fails with ENOSPC | Reserve all but 32 MiB, then write zeros until full |
| Memory pressure did no harm in 10 minutes | The kernel's OOM killer can kill `stress-ng` itself, so the pressure goes away | `stress-ng` runs with `OOMScoreAdjust=-1000`, like a protected system daemon |
| containerd stopped did no harm in 10 minutes | The node went NotReady in 83 s, but running containers kept serving: they don't need containerd, and the kubelet's readiness probes kept passing | The fault now also starts a rollout during the outage, the realistic way it hurts: old pods leave the Service and new ones can't start |
| The watcher could go blind | Prometheus was read through `kubectl port-forward`, which tunnels through the kubelet of Prometheus's node. With that kubelet stopped, alert states couldn't be read | Prometheus is now read through the API server's pod proxy, which connects straight to the pod. Each result also counts the watcher's good and failed reads after T0 |

The kubelet-stopped smoke trial was valid but scored missed, with no deliveries after T0 except
Watchdog. Because of the watcher problem, its pending and firing states weren't trustworthy, so it's
rerun with the rest. No rule for judging outcomes changed.

## Changes after the full run, before the rerun

The full run (42626043262) found two faults that did no harm in any of their five trials, so those
trials are invalid and the faults have no result. These changes were made after seeing that, and before
any rerun, and they're written here first. Both faults rerun five times into `results/v0.3.1`. No rule
for judging outcomes changed, and the three faults that did harm aren't rerun.

| Fault | What the full run showed | Change |
|---|---|---|
| Memory pressure | No harm, and no memory alert even started its wait, so memory was never more than 90% used when Prometheus looked. The likely cause: `stress-ng` makes its own workers the OOM killer's first choice and restarts them when killed, which undoes `OOMScoreAdjust=-1000` | `stress-ng` runs with `--no-oom-adjust`. The trial now records free memory and the kernel's OOM kill count before injecting and 30 seconds after, so a trial without harm shows whether the pressure was really there |
| containerd stopped, then a rollout | No harm. `Recreate` deletes the old pods first, but with containerd down the kubelet can't stop them, so they keep running. Their endpoints are marked terminating but still serving, and kube-proxy keeps sending traffic to serving endpoints when no ready ones are left. The new pods are never created, because `Recreate` waits for the old ones to go | The fault becomes "containerd stopped, then the app crashes": with containerd stopped, the web app's container processes are killed through their cgroups. Nothing can restart them until containerd is back |
| Undo of the rollout | `kubectl rollout undo` failed in all five trials with "no rollout history found", because the stuck rollout never made a second revision. Each trial's lab is thrown away, so no later trial was affected | Every `deployment-patch` step now saves the pod template before patching and puts it back on undo, for every fault that uses it |

What the full run did show is kept as a finding: stopping containerd alone, even with a rollout started
during the outage, didn't hurt an app whose pods were already running.

A trial that ends with no harm now also records its minute-by-minute probe timeline.

## Controls

The four controls run on the VM lab too, five times each. Every control trial on a lab must pass, or
that lab's results aren't trusted.

## What else each trial records

- Which node each monitoring and app pod ran on. A node fault on Prometheus's own node also blinds the
  monitoring, which is a real risk, but a reader needs to know when it happened.
- How every probe did, minute by minute, and whether harm was still there at the end.

## The headline

Each fault's row comes from its most recent run with enough data: the kind faults from v0.2, and the
node faults from v0.3, or from v0.3.1 for the two faults rerun after the changes above. The headline counts every fault in those rows, and the page says which run and
which lab each row came from.

## Predictions, written before the run

Recorded so the results can't quietly reshape them:

- `KubeNodePressure` and `KubeNodeEviction` have `info` severity, so `InfoInhibitor` will probably mute
  them, as it muted `CPUThrottlingHigh`.
- The disk-full fault should be caught by `NodeFilesystemAlmostOutOfSpace` after about 30 minutes, if
  the app's pods being evicted doesn't trigger a faster app alert first.
- The inode fault may be caught only after an hour, or by an app alert about evicted pods.
- Stopping the kubelet should be caught by `KubeNodeNotReady` after about 15 minutes.

## How the predictions did, added after the run

The full run is 42626043262, from commit 8f5de4f, as every result file records. (The commit that added
the results names cac3182 by mistake.)

- `KubeNodePressure` was **not** muted: it was delivered in the disk and inode trials. The prediction
  was wrong.
- Disk full was caught by an app alert first, as the prediction allowed: `KubeDeploymentReplicasMismatch`
  after about 16 minutes, before any `NodeFilesystem` rule.
- The inode fault was caught after about 16 minutes too, not an hour, by the same app alert.
- Stopping the kubelet was caught after about 10 minutes by `TargetDown`, sooner than the 15 minutes
  predicted for `KubeNodeNotReady`. One trial in five delivered nothing, because Alertmanager was on
  the stopped node ([investigation](investigations/kubelet-placement.md)).

## Limits

- The VMs run nested inside a CI runner, so they're slower and noisier than real servers.
- Five node faults on one small cluster. It shows what default rules do on real kernels, not on every
  kind of hardware.
