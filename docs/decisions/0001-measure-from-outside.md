# 1. Measure from outside the cluster

**Status:** accepted, built in v0.0, updated in v0.3.

## Context

BlindSpot judges whether the monitoring noticed a failure. If the evidence that a failure happened came
from that same monitoring, a fault the monitoring can't see would also leave no evidence that it
happened. It would be scored as "no harm" instead of "missed", which hides exactly the gaps BlindSpot
exists to find. Fault 33 makes this concrete: with Prometheus down, Prometheus can't report anything,
including its own absence.

## Decision

Everything that measures runs on the runner, outside the cluster:

- **The probes** that prove harm call the app through its NodePort and the API server with `kubectl`.
- **The receiver** that times deliveries is a small HTTP server on the runner, reached through the lab
  network's gateway: the Docker network on kind, the libvirt network on the VM lab.
- **The clock** is the runner's. Prometheus's alert list is read through the API server's pod proxy and
  timestamped when it's read.

## Consequences

- A fault is only scored after outside probes confirm harm, so "missed" means the app was really hurt.
- The probes see what a user would see, not what a metric says.
- Prometheus's own timestamps aren't used, so pending and firing times are only as precise as the
  15-second polling. That's finer than the 30-second rule evaluation, so it costs nothing in practice.

## Changed in v0.3

Until v0.3 the alert list was read over `kubectl port-forward`. That tunnel runs through the kubelet of
Prometheus's node, so when the kubelet-stopped smoke test stopped that kubelet, the watcher went blind.
Since v0.3 the list is read through the API server's pod proxy, which connects straight to the pod, and
each result counts the watcher's good and failed reads after T0 ([method](../method-v0.3.md)).
