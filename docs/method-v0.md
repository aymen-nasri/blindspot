# Method for the v0.0 test

Written before any trial ran. If a rule changes after results exist, the change gets its own commit
saying why, and earlier results keep the rule they were scored with.

## The question

Does the monitoring that many clusters install by default miss enough real failures to be worth
measuring properly? If it catches nearly everything, BlindSpot has no headline and stops here.

**Decision rule:** stop if 9 or more of the 10 faults are caught.

## Setup

- kind 0.33.0, Kubernetes 1.37, one control plane and two workers, on a GitHub-hosted runner.
- kube-prometheus-stack 92.22.0 with its default rules.
- A small web app on one worker and its backend on the other.
- One trial per fault, each on a fresh cluster. One trial proves little, which is fine for a go/no-go test.

## Changes from the chart's defaults

| Change | Why |
|---|---|
| A webhook receiver for Alertmanager | The chart sends everything to a receiver called `null`, so nothing could be timed |
| Watchdog sent to that receiver, repeated every minute | It also goes to `null` by default. Repeating it makes it a dead man's switch |
| Control-plane and kube-proxy metrics exposed | They listen on localhost by default, so Prometheus can't scrape them |
| Grafana turned off | It plays no part in alerting, and the runner is small |

## One trial

1. Build the cluster, install the chart and the app.
2. Wait for a Watchdog delivery, which proves the delivery path works. Without one, the trial is invalid.
3. Wait 5 minutes as a baseline, and record any alerts already pending or firing.
4. Inject the fault. This is T0.
5. Confirm harm within 5 minutes, using probes that run outside the cluster. Without harm, the trial is invalid.
6. Watch until 30 minutes after T0.

## Outcomes

| Outcome | Meaning |
|---|---|
| caught | A relevant alert was delivered to the receiver within the window |
| fired, not delivered | A relevant alert fired in Prometheus but never reached the receiver |
| pending only | A relevant rule's condition became true, but its wait hadn't ended |
| missed | Nothing relevant happened |

Alerts already pending or firing at T0 never earn credit. Which alerts are relevant to each fault is
in `harness/faults.py`, written before the run.

Time is recorded, from harm to delivery, but this test doesn't score it. The spec's original
headline, "caught within 15 minutes", would count every default rule with a 15-minute wait as slow by
design, which isn't fair to the monitoring being judged.

## Limits

- One trial each, on kind, with a small app. Results here are a signal, not a finding.
- kind nodes share the runner's kernel, so network faults happen on a real kernel but not on real machines.
- Whether each fault's injection works on a hosted runner is itself part of what this test finds out.
