"""Export the pipeline's actual OUTPUT — plans, sub-goals, candidates, verdicts.

`handoff/scores/` records how well the pipeline did. This exports WHAT IT DID: the
strategist's decomposition, every sub-goal's text and tags, every candidate script
it wrote, the gate or QA verdict each received, and the ground-truth score each
earned. That is the material a new selection/ranking method actually operates on,
and none of it is reconstructable from the score files.

Two JSONL files, both small enough to track in git:

  runs.jsonl        one row per benchmark task — instruction, plan, sub-goals, scores
  candidates.jsonl  one row per attempt — sub-goal text, tags, script, verdict, score

Deliberately excluded: `input_tagged` (6.5 MB of base64 renders, regenerable) and
`lines` (the event log, already in each run's run_log.txt).

Usage (from the benchmark repo, which owns the dataset config):
    cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
    PYTHONPATH="$PWD:$(pwd)/../.." .venv/bin/python ../../tools/export_handoff.py
"""

import json
import os
import os.path as osp
import sys

sys.path.insert(0, osp.dirname(osp.dirname(osp.abspath(__file__))))

OUT = osp.join(osp.dirname(osp.dirname(osp.abspath(__file__))), "handoff")


def rel(p):
    """Paths relative to the repo root — absolute ones are meaningless elsewhere."""
    if not p:
        return None
    root = osp.dirname(osp.dirname(osp.abspath(__file__)))
    try:
        return osp.relpath(p, root)
    except ValueError:
        return p


def main():
    import tools.dashboard as dash
    from src import config as c

    ref = json.load(open(osp.join(c.DATASET, "results", "all_results.json")))

    def baseline(rid, who, metric="diff_f1"):
        for block in ref.values():
            m = (block.get(who) or {}).get(metric) or {}
            if rid in m:
                return m[rid]
        return None

    os.makedirs(OUT, exist_ok=True)
    runs, cands = [], []

    for rid, req in dash.REQUESTS.items():
        p = dash._run_record_path(rid)
        if not osp.exists(p):
            continue
        rec = json.load(open(p))
        scores = rec.get("scores") or {}
        # `plan_summary` is the strategist's one-line UNDERSTANDING of the
        # instruction (a string), and `subtasks` is the decomposition it produced.
        # The per-sub-goal tags are not stored on the plan; they are attached to
        # each attempt, so they are collected from the step records below.
        understanding = rec.get("plan_summary")
        if not isinstance(understanding, str):
            understanding = None
        subtasks = rec.get("subtasks") or []
        tags_by_sub = {}
        for s in (rec.get("steps") or []):
            if s.get("tags") and s.get("sub") is not None:
                tags_by_sub.setdefault(s["sub"], s["tags"])

        runs.append({
            "request_id": rid,
            "difficulty": req.get("difficulty"),
            "instruction": (req.get("text") or req.get("prompt") or "").strip(),
            "status": rec.get("status"),
            "scores": {k: scores.get(k) for k in
                       ("chamfer_similarity_norm", "volume_f1", "diff_f1")},
            "baselines": {"other_human_diff_f1": baseline(rid, "other human"),
                          "gpt52_diff_f1": baseline(rid, "gpt-5.2_cadquery-script")},
            # The strategist's decomposition: how it read the instruction, and the
            # sub-goals it emitted with their operation tags. NOTE the goal text
            # here is the ORIGINAL; a refinement rewrites the goal in place, so the
            # text an executor actually saw is on the candidate record instead.
            "plan": {
                "understanding": understanding,
                "n_subgoals": len(subtasks),
                "subgoals": [{
                    "idx": i,
                    "goal": t.get("goal"),
                    "status": t.get("status"),
                    "attempts": t.get("attempts"),
                    "tags": tags_by_sub.get(i),
                } for i, t in enumerate(subtasks)],
            },
            "tokens": rec.get("tokens"),
            "output_dir": rel(rec.get("dest")),
            "saved_at": rec.get("saved_at"),
        })

        for s in (rec.get("steps") or []):
            cands.append({
                "request_id": rid,
                "difficulty": req.get("difficulty"),
                "sub": s.get("sub"),
                "attempt": s.get("attempt"),
                # the sub-goal text AS THE EXECUTOR SAW IT (refinements rewrite it)
                "goal": s.get("goal"),
                "tags": s.get("tags"),
                # what happened to this candidate and why
                "verdict": s.get("verdict"),
                "gate": s.get("gate"),
                "issues": s.get("issues"),
                "ok": s.get("ok"),
                # measured geometry
                "faces": s.get("faces"),
                "volume": s.get("volume"),
                # ground truth for this individual candidate — the label a selector
                # is trying to predict without seeing it
                "gt_scores": s.get("gt_scores"),
                # the artifact itself
                "script": s.get("script"),
                "step_file": rel(s.get("step")),
                "prompt_file": rel(s.get("prompt_file")),
                "prompt_tokens": s.get("prompt_tokens"),
            })

    _dump(osp.join(OUT, "runs.jsonl"), runs)
    _dump(osp.join(OUT, "candidates.jsonl"), cands)

    scored = [x for x in cands if (x.get("gt_scores") or {}).get("diff_f1") is not None]
    print(f"runs.jsonl       : {len(runs)} runs")
    print(f"candidates.jsonl : {len(cands)} attempts "
          f"({len(scored)} with a ground-truth score, "
          f"{sum(1 for x in cands if x.get('script'))} with source)")
    for f in ("runs.jsonl", "candidates.jsonl"):
        print(f"  {f}: {osp.getsize(osp.join(OUT, f)) / 1e6:.2f} MB")


def _dump(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
