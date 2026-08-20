# Running the executor alone — zero API calls

The goal: drop a new model (Qwen3-VL, or anything else) into the **executor** slot,
generate candidates, score them, and merge them into the existing pool — **without
running the strategist, QA or the router, and without spending anything on the API.**

That works because the executor's inputs are fully archived. Each of the 189 calls in
`prompts.jsonl` is the verbatim text that was sent, plus enough to rebuild the images
and pick the right input solid.

**Cost context:** the executor is **77% of this project's API spend** ($26.56 of
$34.43 across 48 tasks); strategist + QA are the other 23%. Replaying prompts skips
both, so a full 189-call sweep against a local model costs **$0 in API**.

## The loop

```
prompts.jsonl row
   ├─ system + prompt      →  verbatim, send as-is
   ├─ source_step          →  render 7 views from it  →  the images
   │                          and execute the candidate against it
   └─ reference_script/gt  →  what gpt-5.2 produced here, and its true score
```

```python
import json
from ourimplementation.tools import render, runner
from ourimplementation.adk.llm import LLM, text_part, image_part
from ourimplementation import config, evaluate as ev

row = json.loads(open("handoff/prompts.jsonl").readline())

# 1. images — always the plain 7 views of source_step (colour-coded: 0 of 189 calls)
render.render_views(row["source_step"], "/tmp/views", stem="v")
parts = [text_part(row["prompt"])]
for v in config.EXECUTOR_VIEWS:                       # 7 views, fixed order
    parts += [text_part(f"[{v}]"), image_part(f"/tmp/views/v_{v}.png")]

# 2. the ONLY model call — point MODEL_EXECUTOR/OPENAI_BASE_URL at your server
out = LLM(config.MODEL_EXECUTOR, role="executor").json(row["system"], parts)
script = out["my_cad_function"]

# 3. execute — deterministic, no model involved
ok, info, log = runner.run_script(script, row["source_step"], "/tmp/work")

# 4. score against ground truth (needs the dataset submodule, not the API)
db = ev._db()
gt, start = ev._gt_and_start(row["request_id"], db)
# export STL next to info["step"], then ev.score_output(request_id, that_dir)
```

## Merging into the existing pool

A new candidate is the same shape as a row in `candidates.jsonl`. Append it with
`request_id`, `sub`, `attempt` (use a distinct range, e.g. 100+, so it cannot collide
with the archived attempts), plus `script`, `gt_scores`, `faces`, `volume`, and a
`model` field naming which model produced it.

Then re-run the selector study over the enlarged pool:

```bash
python tools/mbr_offline.py        # reads the same (request_id, sub) grouping
```

**This is the experiment that matters.** The MBR study found the selector is *safe but
nearly inert* — it abstained on 12 of 14 contested groups because MBR is degenerate
below three **distinct** candidates, and the pipeline's own attempts are refinement
chains that often duplicate each other. Adding an independent model to the same groups
is the direct fix: more distinct answers, which is exactly what consensus needs.

## Things that will bite you

- **`source_step` is not always the task input.** For `sub 0` it is; for later
  sub-goals it is the *previous sub-goal's kept geometry*. Executing against the wrong
  solid produces a candidate that is not comparable to the archived ones. The field is
  pre-resolved for all 189 rows — use it, don't recompute it.
- **Render the images from `source_step`, not from the final result.** The executor saw
  the state it was editing, not the outcome.
- **Groups whose predecessor sub-goal was never kept** cannot be replayed faithfully.
  `source_step` falls back to the task input in that case; skip those rows rather than
  pretend, or your candidate answers a different question than the archived ones.
- **`reasoning_effort` is sent unconditionally** and vLLM will 400 on it. Set
  `REASONING_EFFORT=""`. See `AGENT_HANDOFF.md` §5b for the full shim list.
- **Keep "ship nothing" as a candidate.** On 2 groups every generated candidate scores
  0 while shipping the unedited input scores 1.0 (zero-voxel ground-truth diff). A
  selector forced to pick a generated candidate destroys those.

## Sanity check before trusting a sweep

Replay a handful of prompts through **gpt-5.2** first and confirm the new candidates
land near `reference_gt`. If they do not, the replay harness is wrong — wrong source
solid, wrong images, or a mangled prompt — and any Qwen numbers built on it would be
measuring the harness, not the model.
