# 6. Proposed rules come from reviewed templates

**Status:** accepted, planned for v0.5, not built yet.

## Context

Finding a gap is half the value. The other half is a rule that closes it. Generating rules with an AI
model would be quick, but nobody could explain why a generated rule has the threshold it has, and a
rule that can't be explained can't be trusted on call.

## Decision

Each proposed rule comes from a template written and reviewed by hand. A rule must pass four gates
before it's offered:

1. **Valid:** `promtool check rules` and `pint` pass.
2. **Unit tested:** a `promtool test rules` file shows it fires, and doesn't fire, on sample data.
3. **Catches the fault:** the real fault, with the rule added, is caught in at least 4 of 5 trials.
4. **Stays quiet:** the quiet test raises nothing from it.

## Consequences

- Every rule can be explained line by line, and comes with a runbook.
- Writing templates is slower than generating rules, so fewer rules ship.
- The before-and-after comparison stays fair, because the rule is tested against the same faults, with
  the same method.
