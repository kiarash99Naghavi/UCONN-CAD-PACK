# Agent handoff — neuralCAD-Edit pipeline

You are picking up a CAD-editing agent pipeline that has been measured end-to-end on
all 48 benchmark tasks. This document is the state of play, what is known, what was
tried and rejected, and what to do next.

**You do not need to run anything to start.** The pipeline's actual output is in this
folder — every plan, every sub-goal, every candidate script, every verdict, and the
ground-truth score each candidate earned:

| file | rows | use it for |
|---|---|---|
| `RESULTS.md` | — | the numbers, and which fixes have causal evidence |
| `DATA.md` | — | **schema for the two files below — read this first** |
| `runs.jsonl` | 48 | per task: instruction, strategist's understanding, sub-goals + tags, scores, baselines, tokens |
| `candidates.jsonl` | 191 | per attempt: sub-goal text, tags, verdict, which gate killed it, QA's issues, **full CadQuery source**, ground-truth score |
| `scores/benchmark_48.csv` | 48 | ours vs `other human` vs `gpt-5.2` per task |
| `mbr/` | — | the offline selector study and its per-group detail |
| `REPLAY.md` | — | **run the executor alone, zero API calls** — the fastest path to evaluating a new model |
| `prompts.jsonl` | 189 | verbatim executor calls: system + prompt + resolved source solid + reference score |
| `renders/` | 48x7 | the final geometry per task, as the dashboard shows it |

`candidates.jsonl` is the one that matters for most of the open work: 167 of the 191
attempts carry a ground-truth `diff_f1`, which is exactly the label a selector,
reranker or router is trying to predict without seeing it. Any `(request_id, sub)`
group with ≥2 rows is a complete ranking problem you can work on offline, with no
model calls and no GPU.

---

## 1. Orientation

**Repo layout.** The git root is `/Users/kiarash/Downloads/2026ASMEHackathon`. The
benchmark (`sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit`) is a **submodule**;
the dataset is CC BY-NC 4.0 and is not vendored. Our pipeline lives in
`ourimplementation/`, tools in `tools/`.

**Run one task headlessly** (no dashboard, records land where the dashboard reads them):

```bash
REPO=sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
cd $REPO && PYTHONPATH="$PWD:$(pwd)/../.." .venv/bin/python \
    ../../tools/run_headless.py <request_id>
# --list shows done vs todo; --todo N runs the next N unrun tasks
```

`tools/run_headless.py` calls the dashboard's own `_run_pipeline`, so a headless run
is indistinguishable from clicking Run. It streams progress to stdout. Multiple ids
run sequentially in one process — use that to bound concurrency.

**Dashboard:** `./run_dashboard.sh` (port 8050). It is not running by default.

**Architecture.** `pipeline.run_request` → `adk/router.py::StatefulRouter._advance`
drives sub-goals. Per sub-goal: `agents/strategist.py` plans → `agents/executor.py`
writes a CadQuery function → `tools/lint.py` static check → `tools/runner.py` executes
in a subprocess → deterministic gates (no-op, phantom material, envelope, direction,
frame drift) → `agents/qa.py` judges from renders + measured deltas. Up to 3 design
attempts per sub-goal, 2 replans.

**One LLM path for everything:** `adk/llm.py::LLM.json` → `_create` →
`chat.completions.create(model, messages, response_format=json_object,
reasoning_effort)`. All four roles share it. Swapping providers is a `base_url` change.

---

## 2. The measured failure taxonomy

Eight distinct classes were identified from ~34 runs with per-attempt ground-truth
scoring. Fixed ones are marked.

1. **Wrong plan accepted** — QA validates execution against the *sub-goal*, so a
   faithful build of a wrong plan is unfalsifiable. `plan_flaw` exists for this and
   has fired **0 times in 346 judged steps**. **UNFIXED — highest value.**
2. **Hallucinated API names** — FIXED. `tools/lint.py` now introspects the installed
   CadQuery/OCP rather than matching regexes. 29 hits, **0 false positives** across
   1,323 historical scripts.
3. **Silent selector truncation** — helpers that `sort(...)[0]` when the selector
   matched 2+. Shipped a frame severed by a 5 mm gap that QA could not see at 512 px.
   **UNFIXED.**
4. **Stale entity indices** — the router re-indexes after a sub-goal is accepted but
   pending sub-goals still carry `face #N` from the original index. **UNFIXED.**
5. **QA dimension checks on a change-footprint bbox** — FIXED. `new_face_region` now
   uses each face's geometric bbox, not its vertices (a full-revolution cylinder has
   only 2 seam vertices, so a Ø44.45 cap measured as a *point*).
6. **Frame confusion** — FIXED. Strategist now receives the view→world-axis table
   (this renderer is `front=+Z`, `top=+Y`, *not* the CAD convention) and is told the
   dataset renders may be rotated relative to it.
7. **Invisible target bodies** — FIXED. The solids list was capped at 10; on a 20-body
   part the actual target was body s19 and invisible. Affected 12 of 48 tasks.
8. **Body pairing by centroid** — FIXED. `per_solid_delta` paired bodies by nearest
   centroid; on a rotor every centroid is on the axis, so a micron shift permuted the
   table and manufactured false "non-target body was modified" rejections.

**Cross-cutting:** the pipeline **ships worse than it builds** on 7 of 48 runs
(0.592 `diff_f1`). See §4.

---

## 3. Things tried and REJECTED — do not redo these

- **Direction-gate escape** (allow a refinement whose incremental delta is
  "wrong direction" if the cumulative delta is right). Sound in isolation, but it
  removed an accidental brake on the unconditional-keep bug and **cost −0.274** on
  `3YH2WFSRM22W7DKT_1769177116`. Reverted; reasoning is in `adk/router.py`.
- **QA envelope guidance** (tell QA to check whether the *customer* required the
  envelope preserved before rejecting on it). Tested on a task that reproduces the
  failure; QA kept judging against the sub-goal — *"not preserved as required by the
  sub-goal"* — because the surrounding prompt instructs it to, emphatically. The
  paragraph lost to a stronger instruction. Reverted.
- **Ranking checkpoints by QA confidence.** Documented regression in `_rank`'s
  docstring: the lug bracket shipped without chamfers it had already built.
- **MBR with duplicates uncollapsed / at n=2.** See §4 — both are degenerate.
- **`LLM_TIMEOUT_S = 420`.** Backfired: executor calls legitimately need 360–675 s on
  hard tasks, so the timeout fired on healthy work and retries re-paid the latency.
  Now 900 s with `max_retries=1`.

---

## 4. The open problem: selection

The pipeline generates up to 3 candidates per sub-goal and keeps one. It often keeps
the wrong one. Current measurement: **0.592 `diff_f1` across 7 of 48 runs**.

**Instrumentation already exists.** `tools/dashboard.py::_attempt_gt_scores` scores
*every* attempt that left geometry, and writes `gt_scores` onto the step record. 167
of 191 step records carry it. So "was the shipped result the best one built?" is
answerable offline for any run.

**MBR study — done, see `mbr/`.** `tools/mbr_offline.py` re-selects from existing
candidates by Minimum Bayes Risk over a geometric kernel:

> `ĉ = argmax_c Σ_{c'≠c} u(c, c')`, with `u = IoU(xor(start,c), xor(start,c'))`

Two design points that matter:
- the kernel is on the **edit**, not the part — on a 99%-unchanged solid a part-level
  IoU is ~1.0 for every candidate and carries no signal;
- the kernel is **verified bit-exact** against the benchmark's `diff_f1` (deviation 0.0).

Result: **safe but nearly inert.** 0 losses, 86% argmax accuracy, but it abstains on
12 of 14 contested groups because the pipeline rarely produces ≥3 *distinct*
candidates (attempts are refinement chains, not iid draws — duplicates are common
enough that the router has a "reproduced already-rejected geometry" escalation).

**Hard safety constraint:** on 2 groups every candidate scores 0 while shipping the
*unedited input* scores 1.0 (zero-voxel ground-truth diff). A selector that must pick
a generated candidate destroys those. **"Ship nothing" must remain a candidate.**

---

## 5. Recommended next work, in order

### 5a. Replay harness — highest value, cheapest
**Already built. See `REPLAY.md` and `handoff/prompts.jsonl` (189 calls, source solids
pre-resolved).** What remains is wiring a model to it and merging the output.


Add candidates to existing groups **without re-running the pipeline**. Everything
needed is on disk:

- **311 verbatim executor prompts** (`SYSTEM` + `USER PROMPT`) in
  `results/runs/**/steps/*executor_io.txt`, covering **71 groups across 47 runs**
- source `.step` per sub-goal (run input for `sub0`; previous sub-goal's accepted step
  otherwise)
- `tools/render.py::render_views` regenerates the 7 views deterministically
- `tools/runner.py::run_script` executes a candidate; `evaluate.score_output` scores it

Build `tools/replay_candidates.py --model <name>`: replay a saved prompt → parse
`my_cad_function` → execute → export STL → score → append to the group. Then re-run
`tools/mbr_offline.py` over the enlarged pool.

**Do it with gpt-5.2 first** (a few dollars, no serving work). That tests whether MBR
improves with more candidates *at all*. If MBR still cannot beat `shipped` with 4–5
same-model candidates, a second model will not rescue it.

**Three confounds to control or the numbers are meaningless:**
1. **Images** — re-render from the sub-goal's source `.step` so every model sees
   byte-identical inputs. Reusing whatever is in `_work/` risks feeding one model the
   *natural* views where the other saw *colour-coded* ones.
2. **Source state** — groups whose predecessor sub-goal was not kept cannot be
   replayed faithfully. Skip them; do not guess.
3. **"Ship nothing"** must stay in the candidate set (see §4).

### 5b. Two-expert ensemble (Qwen3-VL MoE on 4× A6000)

Only worth doing if 5a shows MBR benefits from more candidates. Design notes:

**The blocker, stated plainly.** The MODEL is per role (`MODEL_EXECUTOR`, `MODEL_QA`,
`MODEL_STRATEGIST`) but the ENDPOINT is not:

```python
self.client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL, ...)
```

One global `base_url` shared by all four roles. So **you cannot today point the
executor at a local Qwen while QA and the strategist stay on the API.** Switching
`MODEL_EXECUTOR` alone changes which model name is requested from the *same* endpoint.
Three ways out, cheapest first:

1. **A proxy (LiteLLM or similar) — zero pipeline changes.** Run it in front and route
   by model name: `qwen*` → local vLLM, `gpt-5.2*` → OpenAI. Point `OPENAI_BASE_URL` at
   the proxy once; the per-role model names then do the routing for free. Do this first
   unless you need per-expert cost accounting.
2. **The `Expert` refactor (~30 lines).** `LLM.__init__(..., expert=None)` defaulting to
   today's globals so all five call sites stay byte-identical, plus a client cache keyed
   on `(base_url, api_key, timeout, retries)`. Descriptor carries `model, base_url,
   api_key, reasoning_effort, json_mode, image_detail, cost_in/out`. Needed eventually
   for honest per-expert cost and for the per-expert shims below.
3. **All roles on Qwen** — works today with env vars alone, if you only want to compare
   whole-pipeline configurations rather than mix them.

Shims required in every case:

- **`require_key()` raises on an empty `OPENAI_API_KEY`** — give the local path any
  non-empty dummy value.
- **`reasoning_effort` must not be sent to vLLM** — it will 400, and the current
  fallback only catches errors whose text echoes the parameter name.
- `image_part` sets `detail:"low"`, an OpenAI extension some servers reject; make it
  omittable per expert.
- vLLM needs `--limit-mm-per-prompt image=8` (default cap is below the 7 images the
  executor sends) and `--enable-prefix-caching` (the ~3k-token contract prefix is
  identical on every executor call).
- **Hardware:** 4× RTX A6000 = 192 GB, **Ampere SM86, no native FP8** → BF16 for a
  ~30B-class MoE, or AWQ/GPTQ **INT4 + Marlin** for a 235B-class (~120–130 GB at TP=4).
  **Keep the vision tower in BF16** — quantising the ViT is where VLM quality collapses.
- Verify checkpoint names/sizes against current releases; do not assume.
- Prompts are 3.4k–10.8k text tokens **plus 7 images** (~2–2.5k) → `--max-model-len 32768`.

**Insist on a shadow run**: the second expert generates and is scored offline but can
never ship. It buys per-task evidence at zero risk, and prevents confounding "the
local model is worse" with "the selector picked wrong."

### 5c. Smaller, well-evidenced fixes still open

- Stale entity tags after re-index (§2.4) — re-resolve pending sub-goal `face #N` tags,
  or gate on the executor's own `RESOLVED:` line.
- `SELECTED: n of expected` count check (§2.3).
- Scope filter drops in-scope QA findings — the *logging* was fixed, the *logic* was not.
- Prompt-prefix ordering for cache: measured cache rate is **12%** on a ~2,939-token
  static block that is identical across every executor call.

---

## 6. Ground rules learned the hard way

- **Do not tune on a single task.** One task swung 0.0 → 0.53 → 0.0002 → 0.0176 across
  identical-input runs. Use paired per-task deltas and a sign test; n=48 is small and
  several tasks are near-binary.
- **Check the reference before chasing a score.** 5 of 48 tasks are degenerate (a second
  human also scores 0.0). `data/edit_192_external/results/all_results.json` has
  `other human` and `gpt-5.2_cadquery-script` per task — consult it before optimising.
- **Re-derive claims after re-running.** An earlier "1.9 `diff_f1` regret" figure became
  stale once reruns overwrote the records; the current value is 0.592.
- **Prefer abstention to a wrong opinion.** Several fixes here take the form "say nothing
  when uncertain" (`[BLIND]` labels on multi-loop faces, the sign gate on contradictory
  tags, MBR below 3 distinct candidates). This was consistently better than guessing.
- **Verify a metric reimplementation against the original** before trusting anything
  built on it. The MBR kernel matched `diff_f1` to 0.0; had it not, every number would
  have been fiction.

---

## 7. Files worth reading first

**Data in this folder** (no setup needed): `DATA.md` for the schema, then
`candidates.jsonl` — 191 attempts with sub-goal text, verdict, gate, QA issues, full
CadQuery source and ground-truth score. `runs.jsonl` for plans and per-task context.

**Code:**

| file | why |
|---|---|
| `adk/router.py` | the loop; `_advance`, `_rank`, `_revert_to_best`, `_checkpoint`, the gates |
| `agents/qa.py` | the acceptance gate and its `plan_flaw` path (never used) |
| `tools/geometry.py` | the geometry index the planner reads; `to_prompt`, `per_solid_delta` |
| `tools/lint.py` | the introspection API check + its regression methodology |
| `tools/mbr_offline.py` | the selector study; reusable pattern for offline analysis |
| `src/utils/evals_diff.py` | the scoring metric — reuse its voxelisation, never reimplement |
