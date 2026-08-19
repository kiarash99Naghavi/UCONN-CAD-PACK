"""Rebuild candidate geometry from `handoff/candidates.jsonl` — no API, no GPU.

The archived candidate solids are large: 601 MB of .step and 1.8 GB of .stl for the
167 scored attempts, far too much to track in git. They do not need to be tracked.
Each candidate's CadQuery source is embedded in `candidates.jsonl` (2 MB total), and
re-executing that source against the task's input solid reproduces the geometry
EXACTLY — verified: `geo.compare(archived, rebuilt)` returns `identical: True` with a
volume delta of 0.0.

So the scripts are a lossless, ~300x compressed form of the geometry, and this script
is the decompressor. Everything downstream — voxel masks, renders, the MBR study —
can be regenerated on any machine with the benchmark dataset and no model access.

    # rebuild every scored candidate (167 solids)
    python tools/rebuild_candidates.py --out /tmp/candidates

    # just one task, and render the views too
    python tools/rebuild_candidates.py --request ZK22J6VYRKQ2RTFD_1758875163.609787 \
                                       --render --out /tmp/candidates

Output layout mirrors the group structure the selector work uses:

    <out>/<request_id>/sub<N>_try<M>.step
    <out>/<request_id>/sub<N>_try<M>.stl          (with --stl)
    <out>/<request_id>/sub<N>_try<M>_<view>.png   (with --render)
"""

import argparse
import json
import os
import os.path as osp
import sys

sys.path.insert(0, osp.dirname(osp.dirname(osp.abspath(__file__))))

HANDOFF = osp.join(osp.dirname(osp.dirname(osp.abspath(__file__))), "handoff")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="rebuilt_candidates")
    ap.add_argument("--request", help="only this request_id")
    ap.add_argument("--sub", type=int, help="only this sub-goal index")
    ap.add_argument("--stl", action="store_true", help="also export STL")
    ap.add_argument("--render", action="store_true", help="also render the 7 views")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from src.tools import runner, render as our_render
    from src import evaluate as ev

    rows = [json.loads(l) for l in open(osp.join(HANDOFF, "candidates.jsonl"))]
    if args.request:
        rows = [r for r in rows if r["request_id"] == args.request]
    if args.sub is not None:
        rows = [r for r in rows if r["sub"] == args.sub]
    rows = [r for r in rows if r.get("script")]
    if args.limit:
        rows = rows[:args.limit]

    db = ev._db()
    # the input solid is per TASK, not per candidate, so resolve it once each
    inputs = {}
    ok_n = fail_n = 0
    try:
        for r in rows:
            rid = r["request_id"]
            if rid not in inputs:
                _gt, start = ev._gt_and_start(rid, db)
                # `_gt_and_start` returns mesh-relative paths; the executor needs the
                # STEP the pipeline itself loaded via args["input_file"]
                inputs[rid] = osp.join(db.root_dir, start.replace(".stl", ".step"))
            src = inputs[rid]
            if not osp.exists(src):
                print(f"  SKIP {rid} sub{r['sub']} try{r['attempt']}: input STEP missing")
                fail_n += 1
                continue

            dest = osp.join(args.out, rid)
            os.makedirs(dest, exist_ok=True)
            stem = f"sub{r['sub']}_try{r['attempt']}"
            work = osp.join(dest, f".work_{stem}")

            ok, info, _log = runner.run_script(r["script"], src, work)
            if not ok:
                print(f"  FAIL {rid} sub{r['sub']} try{r['attempt']}: "
                      f"{str(info.get('error'))[:70]}")
                fail_n += 1
                continue

            step_out = osp.join(dest, stem + ".step")
            os.replace(info["step"], step_out)
            if args.stl:
                our_render.export_stl(step_out, osp.join(dest, stem + ".stl"))
            if args.render:
                our_render.render_views(step_out, dest, stem=stem)
            ok_n += 1
            gt = (r.get("gt_scores") or {}).get("diff_f1")
            print(f"  ok   {rid[:26]} {stem:14} faces={info.get('faces')} "
                  f"gt_diff_f1={gt}")
    finally:
        db.close_connection()

    print(f"\nrebuilt {ok_n} candidate solids into {args.out}/  ({fail_n} failed)")
    if fail_n:
        print("A failure here means the script no longer reproduces — worth "
              "investigating, since execution was deterministic when archived.")


if __name__ == "__main__":
    raise SystemExit(main())
