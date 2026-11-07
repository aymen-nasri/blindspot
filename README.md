# BlindSpot

Measures which real Kubernetes failures default monitoring catches.

**Status: feasibility test (v0.0).** This repo holds a throwaway harness that breaks a kind cluster in
10 ways and records what kube-prometheus-stack's default rules do. It decides whether the full tool
is worth building. Nothing here is a finding yet.

- How the test works and how it's scored: [docs/method-v0.md](docs/method-v0.md)
- The faults, and which alerts count for each, written before any run: [harness/faults.py](harness/faults.py)

Never run this against a cluster that matters. It breaks things on purpose.

## AI use

Built with an AI coding assistant. The method and results are reviewed by a person, who can explain them.
