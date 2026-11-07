# 2. The score is a count

**Status:** accepted, built in v0.1, extended to repeated trials in v0.2.

## Context

A weighted score, such as points for speed or severity, would need weights. Any weights would be a
judgement call, and the headline would then depend on that judgement. Readers would have to trust the
weights before they could trust the number.

## Decision

The headline is a plain count: "caught K of N faults". Time is reported beside it, as a median in whole
minutes, and never folded into the score. Each fault has one of four outcomes (caught, fired but not
delivered, pending only, missed), and the table shows all of them.

With repeated trials, a fault's outcome is the one in more than half of its valid trials. A tie is
"mixed", and never rounds up to caught.

## Consequences

- Anyone can check the headline by counting rows in the table.
- A fault caught after 15 minutes counts the same as one caught after 1 minute. That's deliberate:
  many default rules wait 15 minutes by design, and the time column shows it.
- "Fired, not delivered" is kept apart from "missed", because the fix is different: routing, not rules.
