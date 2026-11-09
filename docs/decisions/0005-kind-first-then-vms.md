# 5. kind first, real VMs for node faults

**Status:** accepted. kind built in v0.0, the VM lab built in v0.3.

## Context

kind runs Kubernetes nodes as containers, which is fast and runs on a free CI runner. But the nodes
share the runner's kernel and disk. Filling a "node" disk fills the runner, clock changes affect the
host, and some kernel settings, such as `nf_conntrack_max`, are read-only inside a node.

## Decision

Faults that only need workloads, the network or the control plane run on kind. Faults that need a real
machine (disk, inodes, memory pressure, clock, conntrack) wait for a VM lab built with QEMU, cloud-init,
Ansible and kubeadm. If that lab can't run on free runners, those faults are labelled as not tested,
never quietly faked on kind.

## Consequences

- The first ten faults run in about 40 minutes each, for free.
- Network faults on kind still happen on a real kernel, in real network namespaces, with real `tc` and
  `iptables`. They just aren't on separate machines.
- Every fault file says where it can run (`runs_on`), so the results never mix the two labs silently.

## Outcome

The VM lab runs on free 4-CPU GitHub runners: three KVM virtual machines, Kubernetes 1.37.0 through
kubeadm, ready in about 4.5 minutes ([vmlab/](../../vmlab)). All four controls passed there, 5 of 5 each.
Five node faults ran on it: disk full, inodes, memory pressure, the kubelet stopped, and containerd
stopped then a crash ([results/v0.3](../../results/v0.3), [method](../method-v0.3.md)). Clock skew has
an injector but no fault file yet, and conntrack exhaustion isn't built.
