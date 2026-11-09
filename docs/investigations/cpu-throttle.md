# Did the CPU fault hurt users for the whole window?

## The question

In v0.1 and all five v0.2 trials, the CPU fault's probes confirmed harm about 40 seconds in, but no
longer met the harm threshold by the end of the 30-minute window. Applying the CPU limit replaces the
web pods, and the web Deployment uses `Recreate`, so part of the early harm might have been that restart
rather than throttling.

## What was run

Five more trials of the same fault file, unchanged, in run 42612045002 from commit 65d0971. That
commit records each probe minute by minute (`probe_timeline` in each result). The raw results are in
[cpu-throttle/](cpu-throttle). They're a diagnostic, kept apart from `results/`, so they don't count
toward any headline.

## What it showed

| Trial | Failures in minute 0 | Failures after minute 1 | p95 latency before the fault | p95 latency, minutes 1 to 29 |
|---|---|---|---|---|
| 1 | 18% | none | 14 ms | 958 to 1,200 ms |
| 2 | 15% | none | 8 ms | 285 to 600 ms |
| 3 | 19% | none | 9 ms | 400 to 683 ms |
| 4 | 16% | none | 9 ms | 468 to 671 ms |
| 5 | 12% | none | 10 ms | 132 to 267 ms |

Trial 1 also had 10% failures in minute 1, the tail of the same restart. `CPUThrottlingHigh` fired in
every trial, after 923 to 958 s, and nothing was delivered, as in v0.2.

## What it means

- The harm that made these trials valid was the pods restarting in the first minute, not throttling.
- Throttling made the app 15 to 100 times slower for the whole window. Users would notice that, but it
  never reached the 1,500 ms threshold the fault file set before any run.
- So fault 5's row reads "throttling went unreported", not "users were hurt and nobody was told".

Nothing in v0.2's scoring changes: the rules stand as they were written. A future version of this fault
should confirm harm after the restart has passed, or judge latency against the baseline instead of a
fixed threshold, and that change will be written down before it runs.
