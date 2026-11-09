# CPUThrottlingHigh, warning

**What it means:** over half of a container's CPU periods were throttled for 15 minutes: it keeps
hitting its CPU limit and being paused. Latency rises, but nothing errors. The chart's own
`CPUThrottlingHigh`, at 25% and `info` severity, is usually muted by `InfoInhibitor`; this warning tier
isn't.

**Confirm it:**

```bash
kubectl -n <namespace> top pod <pod> --containers
kubectl -n <namespace> get pod <pod> -o jsonpath='{.spec.containers[*].resources}'
```

Compare usage with the limit. A container pinned at its limit is being throttled.

**Likely causes:** a CPU limit set too low for the real load, often by a release that copied limits from
another service.

**Fix:** raise or remove the CPU limit, keeping a sensible request. Many teams set requests and leave CPU
limits off.

**How it was tested:** a `promtool` unit test, BlindSpot's CPU fault under Profile C, and the quiet test.
