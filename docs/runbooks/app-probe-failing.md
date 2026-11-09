# BlindSpotAppProbeFailing

**What it means:** blackbox-exporter, inside the cluster, is failing to fetch the app's pages: more than
10% of requests failed over 5 minutes. It's a symptom alert, so it fires whatever the cause.

**Confirm it:**

```bash
kubectl -n monitoring port-forward svc/blackbox-exporter 9115
curl 'localhost:9115/probe?module=http_2xx&target=http://web.shop.svc.cluster.local.:8080/work&debug=true'
```

The debug output shows where the request failed: DNS, connecting, or waiting for the response.

**Likely causes, and where to look:**

| If the debug output shows | Look at |
|---|---|
| Connect timeouts | NetworkPolicies on the target: `kubectl -n shop get networkpolicy` |
| Small pages work, the large one times out | An MTU black hole: `ping -M do -s 1400 <pod IP>` from the node, and `tcpdump` for missing ICMP |
| Intermittent failures | Packet loss: `tc qdisc show` in the pod's network namespace, and node network errors |
| DNS errors | CoreDNS: see [coredns-nxdomain.md](coredns-nxdomain.md) and [coredns-slow.md](coredns-slow.md) |

**Fix:** remove the cause, then watch `probe_success` return to 1.

**How it was tested:** a `promtool` unit test, and BlindSpot's packet-loss, MTU and NetworkPolicy faults
under Profile C ([docs/method-v0.5.md](../method-v0.5.md)).
