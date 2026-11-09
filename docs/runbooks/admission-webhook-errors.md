# KubeAPIAdmissionWebhookErrors

**What it means:** the API server is rejecting requests because it can't call an admission webhook. With
`failurePolicy: Fail`, every request that webhook covers fails, such as creating pods.

**Confirm it:**

```bash
kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations
kubectl -n <namespace> run check --image=registry.k8s.io/pause:3.10 --dry-run=server
```

The dry run's error names the webhook and why it couldn't be called. The alert's `name` label names it
too.

**Likely causes:** the webhook's Service or pods are gone or unready, its certificate expired, or a
NetworkPolicy blocks the API server from reaching it.

**Fix:** bring the webhook back. In an emergency, delete or edit its configuration, which turns its
checks off. Exempt system namespaces with a `namespaceSelector`, so the cluster can repair itself.

**How it was tested:** a `promtool` unit test, BlindSpot's webhook fault under Profile C, and the quiet
test.
