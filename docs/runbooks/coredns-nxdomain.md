# CoreDNSHighNXDOMAINRate

**What it means:** more than a quarter of CoreDNS's answers have said "this name doesn't exist", for 5
minutes. Either an upstream resolver is failing names that should exist, or something is asking for
names that don't.

**Confirm it:**

```bash
kubectl run -it --rm dnscheck --image=registry.k8s.io/e2e-test-images/agnhost:2.53 -- \
  dig +short example.com.
kubectl -n kube-system logs deploy/coredns | grep NXDOMAIN | tail
```

In Prometheus, `sum by (zone, rcode) (rate(coredns_dns_responses_total[5m]))` shows which zone answers
NXDOMAIN.

**Likely causes:** a broken or hijacked upstream resolver, a bad `forward` or `template` block in the
Corefile (`kubectl -n kube-system get configmap coredns -o yaml`), or an app asking for a mistyped
name.

**Noise warning:** names without a trailing dot are tried with each search domain first, and every
miss is an NXDOMAIN. In a busy cluster, measure the normal share before trusting a 25% threshold.

**How it was tested:** a `promtool` unit test, BlindSpot's upstream NXDOMAIN fault under Profile C, and
the quiet test.
