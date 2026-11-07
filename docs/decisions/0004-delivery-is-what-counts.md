# 4. Delivery is what counts

**Status:** accepted, built in v0.0.

## Context

An alert can reach three stages: pending in Prometheus, firing in Prometheus, and delivered by
Alertmanager to a receiver. Only the last one tells a person anything. The v0.0 results show why the
difference matters: the CPU fault's alert fired and was then muted by `InfoInhibitor`, and with
Alertmanager down, alerts fired with nowhere to go.

## Decision

A fault is caught only when a relevant alert is delivered to the receiver. Firing without delivery and
pending without firing are separate outcomes, never counted as caught. Time runs from the moment the
outside probes confirm harm to the first relevant delivery.

## Consequences

- The result covers the whole path a team depends on: rules, routing, inhibition and delivery.
- The chart's default receiver is `null`, so the lab has to add a real one. That change is disclosed.
- Timing from harm, not from injection, means slow-acting faults aren't penalised for their own delay.
