# How this harness edits a CAD part

## The task, and why diff F1 is the metric that matters

The benchmark is the IDETC 2026 Autodesk *neuralCAD-Edit* set, split `edit_192_external`: 48 requests, 16 easy, 16 medium, 16 hard. Each gives one part as a STEP file and one sentence from a customer, for example "Add 0.2 mm chamfer to the hole edges to improve fitting". There is no feature tree and no sketch, just a solved B-rep with a few hundred faces.

Three numbers come back, all computed against the edit a human expert made from the same sentence. Chamfer similarity and volume F1 compare the finished parts, so they are dominated by the material nobody touched: we average 0.918 and 0.849 on those while still getting many edits wrong.

Diff F1 discriminates. All three parts are voxelised on one shared grid (voxel size is the smaller of the start and ground-truth bounding box diagonals, divided by 128). The voxels each edit changed are taken as an XOR against the start part, and F1 compares the human's change set with ours (`src/utils/evals_diff.py`). So a no-op scores 0, and so does a clean rebuild. There is no alignment step: a correct part sitting 5 mm off scores 0 too.

## Why one model writing one script is not enough

The published baselines are not single calls. The benchmark's own harness lets the model write a function, run it, look at a render of the result, and iterate up to ten times, deciding for itself when it is done. The `gpt-5.2` baseline in the charts above is that harness, driving the same model we drive at the same reasoning effort. The gap between it and us is not model quality.

Three things stall it. Selection: the customer says "that vertical slot", the STEP file has no names, so a model writing selectors blind picks by position. On a part with two congruent slots, one blind and one already open, the wrong one was cut: 748 voxels removed 120 mm from the human's 2028, diff F1 0.000, where a single-shot script scored 1.000. Self-grading: a render cannot show that the part shifted (the camera auto-frames) or that a fillet landed on the opposite rim. The API surface: one wrong call form (`makeCompound` handed raw `.wrapped` objects) crashed 8 attempts across 5 sessions, and each crash burns an iteration of ten.

## Three agents, and what each one decides

**Strategist.** Reads the instruction and a measured index of the part: hole families by radius, cylindrical faces with their sweep angle, planar faces, bores paired into single features, and every opening labelled `[BLIND]` or `[THROUGH]`. It also gets the dataset's colour renders when the sentence carries an appearance, view, deictic or dimension word. It returns 1 to 5 ordered sub-goals, each with a goal sentence, tags, and an *envelope*: which bounding box faces the sub-goal may move.

**Executor.** Sees one sub-goal and never the others. It gets the geometry index, the last approved script, seven views of the state it is editing, and feedback from its last failure. It writes one CadQuery function, run in a subprocess under a 180 s timeout.

**QA.** A separate call with no stake in the edit passing. It sees the sub-goal, the rest of the plan labelled done or not-run-yet, seven views before and after, and the measured diff. Three verdicts, not two: accepted, partial (kept and refined in place), rejected (discarded). Showing it the rest of the plan fixed a real loss: a correct flange was marked partial for missing "the four D=0.5 mm mounting holes", the next sub-goal's entire content. The refinement cut them, so the holes sub-goal no-opped and was rejected. Two budgets on one misread sentence.

## The gates that reject before any model looks

Every attempt is measured against the geometry it started from before QA is called. Each gate rejects for free and hands back text, not just a retry.

- **lint.** Asks the installed CadQuery and OCP whether each attribute the code calls exists, offering the nearest real name back. Repaired in place; the attempt is not spent.
- **no-op.** Output identical to the input, which scores 0. A no-op traced to a misspelled API name the script's own `try/except` swallowed is charged as a typo, so it does not condemn the approach behind it.
- **phantom material.** Summed volume rose but occupied volume did not: a duplicate body buried inside material, invisible in every render.
- **direction.** A sub-goal tagged `cut-hole-slot` that added material, or `add-body` that removed it. It caught a fillet that shipped -52.02 mm^3 where the human's was +2.87 mm^3, the opposite rim of the same slot, accepted by QA at 0.96 confidence for a score of 0.066. Fillet and chamfer tags are exempt: their sign belongs to the edge, not the operation.
- **frame drift.** The part was translated, rescaled or re-centred. Views auto-frame, so QA cannot see it, and every metric scores it near zero.
- **envelope.** A bounding box face moved that the sub-goal never declared it could move.

## What we measured, and what changed the number

Not all 48 tasks are scored yet, so every average on this page is taken over the subset we have run and every baseline is averaged over exactly that same subset. The live numbers are at the top of this tab and broken out on tab 5; they move as runs land, which is why they are computed rather than written down here. The pattern has held throughout: we lead every published model on the mean, we trail the second human, and the gap to the human widens on the hard band.

The scores come from two places. A task the dashboard has a saved run for uses that run, so the number on the chart and the part on the screen are the same run. A task scored in an earlier sweep and never re-run through the dashboard uses `src/results/scores/ours_adk-router.json`. Nothing is averaged twice.

Gains trace to single rules. Attaching the colour renders on a colour or view word took the lever task from 0.0 to 0.797. The phantom-material gate plus a rotation recipe took the rotor to 0.934. A rule that a feature "starting on" a face is coplanar with it took the flange to 0.9998. The newest gates came out of failures in these runs, so tasks scored before them do not carry their benefit yet.

| Knob | Value | Why this value |
|---|---|---|
| `MAX_ATTEMPTS_PER_SUBTASK` | 3 | Counts only attempts that produced judgeable geometry, not crashes. |
| `MAX_BARREN_RETRIES` | 0 | A sub-goal producing no-ops rarely recovers by attempt 5 or 6. |
| `LLM_TIMEOUT_S` | 420 | One executor call ran 1015 s under the SDK's 600 s default, 57% of a killed run. |
| `USE_SKILL_RECIPES` | 0 | The recipe block had grown past 4.1k tokens, more than the geometry index. Off pending the A/B. |
| `QA_VIEWS` | iso + 6 orthographic | Off the near edge-on views QA read a 3-blade rotor as 4 and rejected it three times. |
| `EXECUTOR_VIEWS` | all 7, of the last approved state | Three views hid an arrangement a fourth would have shown. |
