# Results — neuralCAD-Edit pipeline, full 48-task benchmark

All 48 tasks of `edit_192_external` scored. Baselines are the benchmark's own
`data/edit_192_external/results/all_results.json`.

## Headline

| | ours | gpt-5.2 single-shot | other human |
|---|---|---|---|
| mean `diff_f1` | **0.3896** | 0.1836 | 0.5953 |
| record vs baseline | **31 W / 6 T / 11 L** | — | — |
| ratio to baseline | **2.12×** | 1.00× | 3.24× |

By difficulty (n=16 each):

| | ours | gpt-5.2 | ratio |
|---|---|---|---|
| easy | 0.5402 | 0.2986 | 1.81× |
| medium | 0.3025 | 0.1547 | 1.95× |
| hard | **0.3262** | 0.0975 | **3.35×** |

Hard tasks score *above* medium and beat the baseline by the widest margin.

**5 of 48 tasks are degenerate** — a second human also scores 0.0, so no pipeline
change can move them. On one (`SUJ2G2UMJQR7PMBX_1759209917`) the ground-truth diff
is zero voxels, so "change nothing" scores **1.0** and any real edit scores **0.0**.

Mean cost **$0.855/edit** across 48 tasks (`scores/ours_adk-router.json`).

Per-task detail: **`scores/benchmark_48.csv`**.

## Caveat on comparability

These 48 scores were produced across four code states as fixes landed. They are a
snapshot of the pipeline's final capability, **not** a controlled A/B. The only
controlled comparisons are the same-task reruns below.

## Fixes with same-task causal evidence

Identical task, identical inputs, only the code changed:

| fix | task | before → after |
|---|---|---|
| blend adjacency in geometry index | `B7A2N74ZJBF9MZHU_1770172545` | 0.0000 → **0.9505** |
| strategist world-axis table | `B7A2N74ZJBF9MZHU_1770174519` | 0.0000 → **0.8345** |
| uncapped solids list | `3YH2WFSRM22W7DKT_1769782403` | 0.0000 → **0.1397** |

Eleven fixes total shipped; **two were reverted after measurement proved them
harmful** (a direction-gate escape that cost −0.274 on one task, and a QA envelope
guidance change that failed at its stated purpose). Both reverts are documented in
the code with the evidence.

## Offline selector study (MBR)

See **`mbr/mbr_report.md`**. Summary:

- Voxel kernel verified **bit-exact** against the benchmark's own `diff_f1`
  (max deviation 0.0 across 10 candidates) — the load-bearing validity check.
- MBR over existing candidates: **+0.0018 on contested groups, 86% argmax
  accuracy, 1 W / 13 T / 0 L — zero losses.**
- **But it abstains on 12 of 14 contested groups**, because MBR is degenerate with
  fewer than three *distinct* candidates. Where it acted: +0.0129, 100% accurate (n=2).
- Current run-level selection regret: **0.592 `diff_f1` across 7 of 48 runs**
  (mean +0.0123 if the best-built attempt had shipped).
- **Safety finding:** on 2 groups every candidate scores 0 while shipping the
  unedited input scores 1.0. Any deployed selector must keep "ship nothing" as an
  explicit candidate or it will destroy those results.

## What is NOT in this folder

`ourimplementation/results/` is gitignored — `runs/` alone is 5.8 GB of per-attempt
STEP/STL/renders. Only the small, durable artifacts are copied here.

**Known `.gitignore` bug:** line 28 excludes `ourimplementation/results` outright, so
git never descends into it and the `!ourimplementation/results/scores/` negations
below it silently do nothing. The scores were never actually tracked. Fix by
excluding the subdirectories instead of the parent:

```
ourimplementation/results/*
!ourimplementation/results/scores/
```
