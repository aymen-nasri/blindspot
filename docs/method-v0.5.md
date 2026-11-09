# Method for v0.5: proposed fixes

Written before any Profile C trial ran. Trials and scoring work exactly as in
[method-v0.md](method-v0.md) and [method-v0.2.md](method-v0.2.md). v0.5 asks the second question: once a
gap is found, does a fix close it without adding noise?

## Profile C

Profile A is the chart's defaults, the headline. Profile C is the same chart and values, plus:

| Addition | Closes the gap in |
|---|---|
| blackbox-exporter fetching `/work` and `/work?big=1` from inside the cluster every 30 s, with `BlindSpotAppProbeFailing` alerting when over 10% fail for 5 minutes | Packet loss, the MTU black hole, the NetworkPolicy block |
| `CoreDNSHighNXDOMAINRate`: over 25% of answers NXDOMAIN for 5 minutes | Upstream NXDOMAIN |
| `CoreDNSSlowResponses`: 99th-percentile DNS time over 1 s for 5 minutes | Slow DNS |
| `CPUThrottlingHigh` at warning severity: over 50% of CPU periods throttled for 15 minutes | CPU throttling, whose info-level alert is muted |
| `KubeAPIAdmissionWebhookErrors`: requests rejected because a webhook couldn't be called, for 5 minutes | The broken admission webhook |

The rules are in [rules/blindspot.rules.yml](../rules/blindspot.rules.yml), and the lab loads that same
file. Every rule has `warning` severity, so `InfoInhibitor` can't mute it, and a runbook in
[docs/runbooks](runbooks).

No rule inside the cluster can fix Prometheus or Alertmanager being down: there's nothing left to
evaluate or deliver. That fix is an outside dead man's switch watching for `Watchdog`, so those two
faults aren't part of Profile C.

## The four gates

A proposed rule only counts as a fix if it passes all four:

1. **Valid:** `promtool check rules` and `pint lint` pass, in CI on every push.
2. **Unit tested:** `promtool test rules` shows it fires on its failure and stays silent before it, in CI.
3. **Catches its fault:** under Profile C, its fault is caught in at least 4 of 5 trials.
4. **Stays quiet:** under Profile C, the quiet test delivers nothing from it.

## Fairness

- Each fault is judged by the same relevance list as under Profile A, written before v0.0. A fix that
  helps a person but doesn't point at the cause doesn't count: the app probe failing during a DNS fault
  doesn't count for the DNS fault.
- Profile C rows never enter the headline, which stays about the defaults. The results page shows them
  side by side with Profile A.
- The positive and negative controls run under Profile C too.

## Change after the first run, before the DNS rerun

In the first Profile C run (42628449377), both DNS faults were missed in all five trials, and neither
CoreDNS rule even started its wait. The cause is in the faults, not the rules: each fault adds its own
CoreDNS server block for `example.com`, and that block had no `prometheus` plugin. CoreDNS only counts
requests in blocks that have it, so the broken lookups never appeared in CoreDNS's metrics, and no rule
reading them could fire. In a real outage, where the upstream server is slow or says the name doesn't
exist, those lookups go through the main block, which is counted.

Change: each DNS fault's block now includes `prometheus :9153`, the same address as the main block.
Both DNS faults rerun five times under Profile C into `results/v0.5.1`. The relevance rules don't
change, so the app probe alert, which did fire and was delivered in those trials, still doesn't count.

They don't rerun under Profile A: the defaults have no rule that reads CoreDNS metrics, so counting the
lookups can't change what they do. That's reasoning, not a measurement. The page shows which run each
row came from: Profile A's DNS rows are from v0.2, before this change.

## Predictions, written before the run

- DNS faults: caught after about 6 to 10 minutes, by the CoreDNS rules.
- Packet loss, MTU and NetworkPolicy: caught after about 3 to 8 minutes, by the app probe.
- CPU throttling: caught after about 16 minutes, the same 15-minute wait as the default rule.
- Webhook: caught after about 6 to 10 minutes, if the API server exports the webhook rejection metric
  in this version. If it doesn't, the rule can't fire, and that's reported as a failed fix.
- The quiet test: no noise from these rules. The NXDOMAIN rule is the most likely to make some.

## How the predictions did, added after the runs

Runs 42628449377 (from commit 9766782, into `results/v0.5`) and 42652854871 (the DNS rerun, from
4a76605, into `results/v0.5.1`).

| Rule | Gate 3: catches its fault | Gate 4: quiet test | Prediction |
|---|---|---|---|
| `BlindSpotAppProbeFailing` | Packet loss, MTU and NetworkPolicy each 5 of 5, about 3 min | Silent | Right: 3 to 8 min predicted |
| `CoreDNSHighNXDOMAINRate` | 4 of 5, about 8 min, after the rerun | Silent, with a caveat below | Right on time, after the fault was fixed |
| `CoreDNSSlowResponses` | 5 of 5, about 6 min, after the rerun | Silent | Right on time, after the fault was fixed |
| `CPUThrottlingHigh`, warning | 5 of 5, about 16 min | Silent | Right |
| `KubeAPIAdmissionWebhookErrors` | 5 of 5, about 6 min | Silent | Right: the API server does export the metric |

All five pass all four gates. The quiet test under Profile C delivered nothing and fired nothing in
five hours of normal work, and both controls passed 5 of 5.

The caveat: `CoreDNSHighNXDOMAINRate` started its wait during cluster setup, before any fault, in 7 of
the 60 Profile C trials, and was still active when the fault began in 1, which is the NXDOMAIN trial
that earned no credit. The quiet test starts after setup, so gate 4 doesn't see this. A fresh cluster
makes many lookups that end in NXDOMAIN while its search domains expand names. Tune the 25% threshold
or the 5-minute wait for your own cluster before relying on it.

The app probe alert fired and was delivered in every DNS trial too, after about 3 minutes, but it
doesn't name DNS, so by the relevance rules it doesn't count for the DNS faults.

