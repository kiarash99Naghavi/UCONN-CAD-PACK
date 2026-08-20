# Data schema — `runs.jsonl` and `candidates.jsonl`

The pipeline's actual output, not just its scores. This is the material a selection,
reranking or routing method operates on. Regenerate with:

```bash
cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
PYTHONPATH="$PWD:$(pwd)/../.." .venv/bin/python ../../tools/export_handoff.py
```

## `runs.jsonl` — 48 rows, one per benchmark task (0.16 MB)

```jsonc
{
  "request_id": "ZK22J6VYRKQ2RTFD_1758874422.1403751",
  "difficulty": "hard",
  "instruction": "Add a connecting hole of 1.7 millimetre diameter and ...",
  "status": "finished",
  "scores":    {"chamfer_similarity_norm": .., "volume_f1": .., "diff_f1": 0.0732},
  "baselines": {"other_human_diff_f1": 0.531, "gpt52_diff_f1": 0.002},
  "plan": {
    "understanding": "Enlarge the aligned hole through the two central lugs ...",
    "n_subgoals": 2,
    "subgoals": [
      {"idx": 0, "goal": "On the single solid, enlarge the existing coaxial ...",
       "status": "done", "attempts": 1,
       "tags": ["resize-feature", "cut-hole-slot"]}
    ]
  },
  "subtasks": [...],            // raw router state, kept for fidelity
  "tokens": {...},              // per-model and per-role counts + cost estimate
  "output_dir": "ourimplementation/results/runs/.../brep_end/...",
  "saved_at": "..."
}
```

**`plan.subgoals[].goal` is the ORIGINAL text.** A refinement rewrites the goal in
place, so the text an executor actually received is on the candidate record.

## `candidates.jsonl` — 191 rows, one per attempt (2.07 MB)

Every attempt the pipeline made, including the ones it threw away. **167 carry a
ground-truth score; all 191 carry their source.**

```jsonc
{
  "request_id": "...", "difficulty": "hard",
  "sub": 0, "attempt": 1,
  "goal": "...",                 // the sub-goal AS THIS ATTEMPT SAW IT
  "tags": ["resize-feature", "cut-hole-slot"],

  "verdict": "accepted",         // accepted | partial | rejected | crashed
  "gate": null,                  // null=QA judged it; else no-op|direction|envelope|lint|...
  "issues": ["..."],             // QA's verbatim complaints
  "ok": true,                    // did the script execute

  "faces": 266, "volume": 66.4866,

  "gt_scores": {"chamfer_similarity_norm": .., "volume_f1": .., "diff_f1": 0.0732},

  "script": "def my_cad_function(args):\n    ...",   // full CadQuery source
  "step_file":   "ourimplementation/results/runs/.../sub0_try1_accepted.step",
  "prompt_file": "ourimplementation/results/runs/.../sub0_try1_accepted_executor_io.txt",
  "prompt_tokens": 5046
}
```

### Why this is the interesting file

`gt_scores.diff_f1` is **the label a selector is trying to predict without seeing it.**
For any `(request_id, sub)` group with ≥2 rows you have a complete ranking problem:
several candidate scripts, their executed geometry, the verdict the pipeline gave
each, and the true score of each.

- **34 groups have ≥2 scored candidates** (129 candidates).
- **14 of those are "contested"** — the candidates differ in true score by >0.01.
- The pipeline picked a worse candidate than it had built on **7 of 48 runs**
  (0.592 `diff_f1` total). That gap is the target.

`gate` matters: a candidate rejected by a *gate* never reached QA at all, so gate
rejections and QA rejections are different evidence. `gate="direction"`, `"no-op"`,
`"envelope"` and `"lint"` are deterministic and free; `gate=null` means an LLM judged it.

## Geometry — rebuild it, don't download it

The archived solids are large: **601 MB of `.step` and 1.8 GB of `.stl`** for the 167
scored candidates. None of it is tracked, and none of it needs to be.

Re-executing a candidate's `script` against its task's input solid reproduces the
archived geometry **exactly** — verified with `geo.compare(archived, rebuilt)`:
`identical: True`, volume delta `0.0`. Execution is deterministic, so the 2 MB of
source in `candidates.jsonl` is a lossless ~300x compression of the geometry.

```bash
# every scored candidate (167 solids), plus STL and the 7 views
python tools/rebuild_candidates.py --stl --render --out /tmp/candidates

# or just one task
python tools/rebuild_candidates.py --request <request_id> --out /tmp/candidates
```

No API key and no GPU — only the benchmark dataset (a git submodule) and the venv.
Output is `<out>/<request_id>/sub<N>_try<M>.step`.

## `renders/` — 48 tasks x 7 views (7.3 MB)

The final shipped geometry of each task, as the dashboard displays it:
`renders/<request_id>/{toprightiso,front,back,left,right,top,bottom}.jpg`.

These are the FINAL result per task, not per candidate. Per-candidate renders (1,484
files, 31 MB) are not shipped — regenerate them with `rebuild_candidates.py --render`
if you need them, e.g. for an LLM-judge selector that compares candidates visually.

### Referenced artifacts

`step_file` and `prompt_file` are repo-relative but point into
`ourimplementation/results/runs/`, which is **gitignored** (5.8 GB). They resolve on
the machine that produced them; treat them as provenance, not as portable data. The
`script` field is embedded precisely so the candidate is usable without them.

Cached STLs, where they exist, sit next to each `.step` at
`<step_without_extension>_gt/tmp.stl` — that is what `tools/mbr_offline.py` consumes.

## Worked example — `tools/mbr_offline.py`

The MBR study is the reference consumer of exactly this data: it groups candidates by
`(request_id, sub)`, loads each one's geometry, ranks them without looking at
`gt_scores`, then uses `gt_scores` only to grade the ranking. Read it before writing a
new selector; it also documents the two ways the naive version fails (degenerate at
n=2, and duplicate candidates dominating a consensus vote).
