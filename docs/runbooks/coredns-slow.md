# CoreDNSSlowResponses

**What it means:** the slowest 1% of CoreDNS answers have taken over a second, for 5 minutes. Most
clients wait about 5 seconds per lookup attempt, so slow DNS turns into slow or failed requests
everywhere.

**Confirm it:**

```bash
kubectl run -it --rm dnscheck --image=registry.k8s.io/e2e-test-images/agnhost:2.53 -- \
  dig example.com. | grep 'Query time'
```

In Prometheus, `histogram_quantile(0.99, sum by (le, zone) (rate(coredns_dns_request_duration_seconds_bucket[5m])))`
shows which zone is slow.

**Likely causes:** a slow or unreachable upstream resolver, CoreDNS short of CPU (check its throttling
and restarts), or a misconfigured plugin in the Corefile.

**Fix:** fix or replace the upstream, give CoreDNS more replicas or CPU, and consider NodeLocal DNSCache
so pods don't all wait on the same resolver.

**How it was tested:** a `promtool` unit test, BlindSpot's slow-DNS fault under Profile C, and the quiet
test.
