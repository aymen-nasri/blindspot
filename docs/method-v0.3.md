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
wait among the alerts that count for that fault, plus 15 minutes, so no rule is scored as missed while
it's still waiting by design.

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

## Controls

The four controls run on the VM lab too, five times each. Every control trial on a lab must pass, or
that lab's results aren't trusted.

## What else each trial records

- Which node each monitoring and app pod ran on. A node fault on Prometheus's own node also blinds the
  monitoring, which is a real risk, but a reader needs to know when it happened.
- How every probe did, minute by minute, and whether harm was still there at the end.

## The headline

Each fault's row comes from its most recent run with enough data: the kind faults from v0.2, and the
node faults from v0.3. The headline counts every fault in those rows, and the page says which run and
which lab each row came from.

## Predictions, written before the run

Recorded so the results can't quietly reshape them:

- `KubeNodePressure` and `KubeNodeEviction` have `info` severity, so `InfoInhibitor` will probably mute
  them, as it muted `CPUThrottlingHigh`.
- The disk-full fault should be caught by `NodeFilesystemAlmostOutOfSpace` after about 30 minutes, if
  the app's pods being evicted doesn't trigger a faster app alert first.
- The inode fault may be caught only after an hour, or by an app alert about evicted pods.
- Stopping the kubelet should be caught by `KubeNodeNotReady` after about 15 minutes.

## Limits

- The VMs run nested inside a CI runner, so they're slower and noisier than real servers.
- Five node faults on one small cluster. It shows what default rules do on real kernels, not on every
  kind of hardware.
