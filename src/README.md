# Our implementation — ADK-style multi-agent CAD editing harness

A stateful router drives three specialised agents over a CadQuery tool layer.

## Setup

```bash
cp src/.env.example src/.env
# put your OPENAI_API_KEY in it
```

`.env` is gitignored. Nothing calls an API until you explicitly run something.

## Run

From the benchmark repo root, with the project root also on `PYTHONPATH`:

```bash
cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit

# one task, then score it
PYTHONPATH=$(pwd):$(pwd)/../.. uv run python -m src.pipeline \
    --request ZK22J6VYRKQ2RTFD_1758875163.609787 --score

# score everything produced so far
PYTHONPATH=$(pwd):$(pwd)/../.. uv run python -m src.evaluate

# or click "Run our agent pipeline" in dashboard tab 3
PYTHONPATH=$(pwd):$(pwd)/../.. uv run python ../../tools/dashboard.py
```

## Architecture

```
                        StatefulRouter  (adk/router.py)
                                |
        +-----------------------+------------------------+
        |                       |                        |
   Strategist              Executor                    QA
 (agents/strategist)    (agents/executor)         (agents/qa)
 instruction -> ordered  one sub-goal -> a        6 ortho views before/after
 sub-goals               CadQuery function        + measured geometry delta
                                |                        |
                                v                        v
                         tools/runner.py           accept / reject
                         (subprocess, validated)    + specific guidance
                                |
                                v
                    tools/geometry.py   tools/render.py
                    B-rep index         7 views, STL export
                    tools/focus.py      tools/skillref.py
                    instruction-        the verified recipes for
                    conditioned slice   this kind of edit
                    of that index
```

State machine: `PLAN → EXECUTE → RUN → RENDER → QA → (retry | next sub-goal) → FINALIZE`.

## What each piece is for

**`tools/geometry.py`** — turns an opaque 266-face solid into an addressable
index: circular-edge families grouped by radius (the holes), cylindrical faces,
planar faces with normals. Filters blend artifacts and part-scale curves, ranks
by repetition, and renders to compact text (~700 tokens instead of ~2k of JSON).
`compare()` gives the objective before/after delta. Volume/area are guarded:
3 of the 48 benchmark inputs are degenerate compounds that crash OCC's mass
properties outright, and those runs used to die in `_prepare` and score 0.

**`tools/focus.py`** — decides which slice of that index a given edit actually
needs, from the instruction text alone (zero LLM calls). See
[Geometry focus](#geometry-focus--why-the-executor-sees-a-smaller-index).

**`tools/skillref.py`** — the same trick applied to knowledge instead of
geometry. `Skills/reference/recipes_edit.md` holds ten recipes for editing
imported B-reps, each one executed against real benchmark STEP files; the whole
file is ~10k tokens, which the executor would pay on every attempt. This module
scores the sections against the sub-goal using `focus`'s cue vocabulary (plus
operation words the index has no concept of — mirror, enlarge, defeature) and
sends the best one or two under `RECIPES_MAX_TOKENS`, typically ~2.4k tokens,
a 75-85% saving. §1 (multi-body load/return) always ships: imports here carry
up to 56 bodies and silently dropping the untouched ones scores near zero.
`USE_SKILL_RECIPES=0` reverts to the bare contract, byte for byte, which is how
the ablation is run.

**`agents/strategist.py`** — splits a compound instruction into ordered
sub-goals. Explicitly told not to invent work, because every unnecessary change
costs Diff F1 — with the concrete case in the prompt, since the general
instruction was not enough on its own: asked for a 0.5 mm flange, it also
planned four mounting holes through the flange corners that nobody requested.
QA now cross-checks the result against the *customer instruction*, not only the
sub-goal, so invented geometry is caught even when the plan is what invented
it. Also emits per-sub-goal `focus` terms that sharpen the index slice each
executor call sees.

**`agents/executor.py`** — writes the function for exactly *one* sub-goal. Never
sees the other sub-goals, which is what keeps context from ballooning. On the
final proposal, or once its selection code has matched nothing twice, it
switches to `LAST_RESORT` mode and is told to write the bluntest thing that
could work — including the plain string selectors the contract otherwise warns
against. "Unreliable" beats "selected nothing three times": the failure mode
this fixes is 180 lines of hand-rolled region tests and adjacency maps exiting
through `return shape`, on a task the single-shot baseline wins with a
four-line selector chain.

**`agents/qa.py`** — independent acceptance gate. Sees six orthographic views
before and after plus the measured delta, and must reject no-ops and rebuilds.

**`adk/llm.py`** — retries malformed JSON with the parse error fed back instead
of silently degrading to `{}`.

**`adk/router.py`** — budgets attempts per sub-goal, distinguishes a crash from
a QA rejection, and promotes the last geometry that *passed QA* rather than
whatever ran last. When a sub-goal runs out of attempts having passed nothing,
the fallback is *ranked*, not "whatever ran last": an edit QA saw and disliked
outranks one a gate threw out, and a no-op or a frame-drifted part is never
promoted at all. Both score at or below the untouched input while reporting
`status=done` — measured on the scroll-wheel fillet task, where three
consecutive no-ops were correctly rejected and the third was shipped anyway.

## Geometry focus — why the executor sees a smaller index

The full geometry index describes the whole part; most edits touch one feature
family. "Add a 1.5 mm rib" does not need the twelve hole families, and "change
the profile" does not need any of them. `tools/focus.py` conditions the index
on the instruction so each executor call — up to
`MAX_SUBTASKS x MAX_ATTEMPTS_PER_SUBTASK` of them per run — reads only the
sections its sub-goal can act on.

Two layers, both deterministic and free:

1. **`extract_cues(text)`** parses instruction + sub-goal into
   - *topics* — feature vocabulary grounded in the 48 real instructions
     (holes, chamfers, rounds, slots, ribs, planes, vertical, global, …),
   - *sizes* with units — handles the dataset's comma decimals (`R=0,2mm`,
     `0,5cm`) and shorthand (`d=3`), and classifies what each number measures
     (diameter / radius / chamfer / fillet / thickness / length),
   - *directions* (top, vertical, front, …).
2. **`focused_text(insp, ...)`** renders only the index sections those topics
   map to. A family whose measured diameter/radius matches a quoted size is
   expanded in place — full `edge_idx` list and centers — so "chamfer the
   d=2.6 holes" hands the executor the exact 8 edges. Dropped sections are
   named in a closing note.

Three invariants keep it safe:

- **Fallback, not filter.** No topic matched → the full index is returned
  unchanged. Focusing can only drop sections positively identified as
  irrelevant; it can never do worse than the unfocused baseline.
- **New-geometry sizes match nothing.** "Add a 0.1 mm fillet" sizes geometry
  that does not exist yet; fillet/chamfer numbers are excluded from family
  matching so they cannot drag in the r=0.1 blend-artifact families.
- **Blend junk stays hidden.** Cylindrical families below 2% of the bbox
  diagonal are fillet blends, not features anyone refers to, and a size match
  never resurrects them (mirrors the circular-edge filtering in geometry.py).

Wiring: the strategist plans from the **full** index (one call, needs the whole
part) and returns `focus` terms per sub-goal; the executor's slice is built
from instruction + sub-goal + those terms (`agents/executor.py`). The router
also **re-indexes after every accepted sub-goal**, so later executors select by
radii measured on the current geometry, not the original input.

Measured on all scoreable benchmark tasks (instruction-level, before
sub-goal terms narrow it further): **21% average index reduction, up to 62%**
on narrow edits, worst case +137 chars on a genuinely broad edit; 43/48
instructions hit at least one topic, the rest fall back to the full index.

Inspect any case by hand:

```bash
cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
PYTHONPATH=$(pwd):$(pwd)/../.. uv run python -m src.tools.focus \
    data/edit_192_external/breps/<id>.step "Add 0.2 mm chamfer to the hole edges"
```

prints the extracted cues, the focused index, and full-vs-focused sizes.

To extend: add stems to `_TOPIC_WORDS` (a trailing `*` allows suffixes) and map
the topic to index sections in `_TOPIC_SECTIONS`. Anything unmapped simply
falls back to the full index, so a missing word is a lost optimisation, never a
lost run.

## Output layout

Matches `example_data/` exactly, so it ingests without modification:

```
results/runs/<user_id>/outputs/<edit_id>/brep_end/<ts>/
    settings.json  tmp.step  tmp.stl  tmp_<view>.jpg  session_state.json
results/scores/<user_id>.json
```

## How this differs from the baseline

| | Baseline (`base_vlm.py:364`) | Ours |
|---|---|---|
| Agents | 1, self-judging | 4 roles, independent QA |
| Task handling | whole instruction at once | decomposed, sequential |
| Visual feedback | 1 image | 6 orthographic views |
| Geometric feedback | none | face/edge/volume delta every attempt |
| Selection | guessed selector strings | measured B-rep index, focused per sub-goal |
| Malformed JSON | silently `{}`, iteration wasted | retried with the error |
| Result kept | whatever ran last | last one that passed QA, else a real edit QA rejected — never a no-op |
| Nothing worked | exports the input (its script ends `except: return shape`) | exports the input, and says so in `geometry_source` |
| Validation | none | isValid + non-zero volume + renderable |

## Two metrics are saturated, and it changes what "better" means

Handing back the **unedited input** as your answer scores, on four benchmark
tasks measured directly:

| request | chamfer sim | volume F1 | diff F1 |
|---|---|---|---|
| `SUJ2G2UMJQR7PMBX_1759203600` | 0.987 | 0.996 | 0.000 |
| `4S7JQK6ZQMAD25GL_1758863141` | 0.990 | 0.998 | 0.000 |
| `ZK22J6VYRKQ2RTFD_1758875163` | 0.989 | 0.998 | 0.000 |
| `F332D3FXML85WLR2_1770205912` | 0.915 | 0.389 | 0.000 |

The ground-truth edit is a small delta off the same part, so chamfer similarity
and volume F1 mostly measure *did you return roughly this part*, not *did you
edit it*. Only Diff F1 reads a no-op as zero.

This is most of the published baseline's headline number. Across all 48 tasks
`gpt-5.2_cadquery-script` has `diff_f1 < 0.02` on **29**, and **24** of those
still score chamfer > 0.95 — it is scoring high by failing safely. The humans
manage it on 6 of 48.

Two consequences the harness is built around:

1. **Never emit nothing.** A missing `tmp.stl` scores 0.0 on all three metrics,
   which is *worse than giving up*. `finalize` therefore always writes
   geometry, falling back to the untouched input, and records
   `geometry_source: "input-unedited"` so the fallback is never mistaken for a
   real edit. `evaluate.py` counts them as `n_unedited_fallback`.
2. **Never dress a no-op up as an edit.** The corollary: promoting a rejected
   no-op scores the same as the fallback while reporting `status=done`. The
   router refuses to promote geometry the gates threw out, so an honest
   `failed` is the outcome and the number is the same either way.

Quote Diff F1 first in the writeup. The other two are the price of entry.

## Known constraint worth designing around

OCC frequently refuses operations Fusion performed happily. On the r=1.3 and
r=1.4 hole families of the sample bracket, `chamfer()` fails with
`BRep_API: command not done` at every size tried, while the r=0.4 family
succeeds. A harness that cannot recover from a kernel refusal will lose those
tasks outright — this is why the executor is told to expect failure and select
differently rather than retrying the same selector.

## Tuning

Everything is in `.env`: per-role models (a cheaper QA model is a real cost
lever), `REASONING_EFFORT`, `MAX_SUBTASKS`, `MAX_ATTEMPTS_PER_SUBTASK`,
`SCRIPT_TIMEOUT_S`, `RENDER_SIZE`.

For the writeup, ablate one at a time: `MAX_SUBTASKS=1` disables decomposition,
pointing QA at a single view undoes the 6-view gate. Judges weight novelty and
cost efficiency at 40%; showing which component earned which delta is worth more
than the raw score.
