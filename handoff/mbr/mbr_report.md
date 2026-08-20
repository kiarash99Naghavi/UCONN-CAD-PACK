# Offline selector study — MBR vs what the pipeline shipped

Candidates, geometry and ground-truth scores all pre-existed; nothing was
regenerated. Ground truth is used only to grade the selectors, never to choose.

**Metric check**: recomputed `diff_f1` on 10 candidates, max deviation from the stored value **0.0** → PASS.

- groups with >=2 scored candidates: **33**
- with a reconstructable shipped pick: **26**
- contested (candidates differ by >0.01): **14**
- skipped: **1**

## Means over groups with a shipped pick

| selector | mean diff_f1 |
|---|---|
| random (floor) | 0.3353 |
| **shipped** (today) | **0.4033** |
| mbr-iou | 0.4043 |
| mbr-f1 | 0.4043 |
| oracle (ceiling) | 0.4115 |

## On contested groups only

| selector | mean | vs shipped | picks argmax | W/T/L | oracle gap recovered |
|---|---|---|---|---|---|
| mbr-iou | 0.5694 | +0.0018 | 86% | 1/13/0 | 12% |
| mbr-f1 | 0.5694 | +0.0018 | 86% | 1/13/0 | 12% |

## Where the selector actually acted

Fewer than three DISTINCT candidate answers makes the MBR objective
degenerate (at two, the consensus is symmetric and the pick is decided by
list order), so it abstains and keeps what shipped. These rows separate
'chose well' from 'declined to choose'.

| selector | abstained (contested) | acted on | vs shipped when acting | accuracy when acting |
|---|---|---|---|---|
| mbr-iou | 12/14 | 2 | +0.0129 | 100% |
| mbr-f1 | 12/14 | 2 | +0.0129 | 100% |

## Bias vs variance

- bias-limited (oracle < 0.1 — no selector can help): **2**
- variance-limited (oracle >= 0.1 and shipped well below it): **1**

## Groups where nothing was kept

Excluded from every table above (no shipped candidate to compare against),
but these are the runs that fell through to the one-shot fallback or shipped
the unedited input. A selector allowed to ship a REJECTED candidate would
operate here and essentially nowhere else.

- count: **7**
- mean best-candidate score available in them: **0.2170**

### Safety counter-example

- groups where every candidate scores 0 but shipping the UNEDITED input is correct: **2**

  On a task whose ground-truth diff is zero voxels, "changed nothing" scores
  1.0 and any real edit scores 0.0. A selector forced to pick a candidate
  destroys that result. Any deployed selector must keep "ship nothing" as a
  candidate, not just the geometries it generated.

