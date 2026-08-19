"""Export the verbatim executor prompts, so the executor can be re-run ALONE.

The point: a new model can be dropped into the executor slot and evaluated without
running the strategist, QA or the router at all — and therefore without spending a
cent on the API. 77% of this project's API cost is the executor; the other 23% is
strategist + QA, and replaying a saved prompt skips both.

Each row is one executor call exactly as it was issued: the system prompt, the full
user prompt, and enough information to rebuild the images and pick the input solid.

    request_id, sub, attempt, repair   identity
    system, prompt                     the verbatim text that was sent
    n_images, colour_coded             what accompanied it
    source_step                        the solid the executor was editing
    input_step                         the task's original solid
    reference_script, reference_gt     what the original model produced, and its score

`source_step` is the state that sub-goal started from — the task input for sub 0, and
the previous sub-goal's kept geometry after that. It is the file to render the 7 views
from AND the file to execute a new candidate against, so a replay is faithful.

Measured across all 311 dumps: colour-coded views were used 0% of the time, so the
images are always the plain 7 views of `source_step` — fully regenerable with
`tools/render.py::render_views`, no archived JPEGs needed.

Usage (from the benchmark repo, which owns the dataset config):
    cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
    PYTHONPATH="$PWD:$(pwd)/../.." .venv/bin/python ../../tools/export_prompts.py
"""

import glob
import json
import os.path as osp
import re
import sys

sys.path.insert(0, osp.dirname(osp.dirname(osp.abspath(__file__))))

ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
OUT = osp.join(ROOT, "handoff", "prompts.jsonl")

SYS_RE = re.compile(r"=+ SYSTEM =+\n(.*?)\n=+ USER PROMPT =+", re.S)
USR_RE = re.compile(r"=+ USER PROMPT =+\n(.*?)(?:\n=+ REPLY|\Z)", re.S)
IMG_RE = re.compile(r"^images\s*:\s*(\d+)", re.M)
NAME_RE = re.compile(r"^sub(-?\d+)_try(\d+)(?:_repair(\d+))?")


def rel(p):
    try:
        return osp.relpath(p, ROOT)
    except (ValueError, TypeError):
        return p


def main():
    import tools.dashboard as dash
    from src import evaluate as ev

    db = ev._db()

    # dest dir -> request id, so a dump can be traced back to its task
    dest2rid = {}
    for p in glob.glob(osp.join(ROOT, "src/results/dashboard_runs/*.json")):
        rec = json.load(open(p))
        if rec.get("dest"):
            dest2rid[osp.abspath(rec["dest"])] = osp.basename(p)[:-5]

    # (rid, sub, attempt) -> the candidate record, for reference script + score
    cand = {}
    # (rid, sub) -> the geometry that sub-goal was kept at, to chain source states
    kept = {}
    for p in glob.glob(osp.join(ROOT, "src/results/dashboard_runs/*.json")):
        rid = osp.basename(p)[:-5]
        for s in (json.load(open(p)).get("steps") or []):
            cand[(rid, s.get("sub"), s.get("attempt"))] = s
            if s.get("verdict") in ("accepted", "partial") and s.get("step"):
                kept[(rid, s.get("sub"))] = s["step"]

    rows, skipped = [], 0
    for f in sorted(glob.glob(osp.join(
            ROOT, "src/results/runs/ours_adk-router/outputs/"
                  "*/brep_end/*/steps/*executor_io.txt"))):
        rid = dest2rid.get(osp.abspath(osp.dirname(osp.dirname(f))))
        m = NAME_RE.match(osp.basename(f))
        if not rid or not m:
            skipped += 1
            continue
        sub, attempt = int(m.group(1)), int(m.group(2))
        repair = int(m.group(3)) if m.group(3) else 0

        text = open(f, errors="ignore").read()
        sm, um = SYS_RE.search(text), USR_RE.search(text)
        if not sm or not um:
            skipped += 1
            continue

        # the solid this sub-goal started from
        try:
            _gt, start_rel = ev._gt_and_start(rid, db)
            input_step = osp.join(db.root_dir, start_rel.replace(".stl", ".step"))
        except Exception:
            input_step = None
        source = input_step if sub <= 0 else kept.get((rid, sub - 1), input_step)

        c = cand.get((rid, sub, attempt)) or {}
        im = IMG_RE.search(text)
        rows.append({
            "request_id": rid, "sub": sub, "attempt": attempt, "repair": repair,
            "system": sm.group(1).strip(),
            "prompt": um.group(1).strip(),
            "n_images": int(im.group(1)) if im else None,
            "colour_coded": "COLOR LEGEND" in text,
            "source_step": rel(source),
            "input_step": rel(input_step),
            "reference_script": c.get("script"),
            "reference_verdict": c.get("verdict"),
            "reference_gt": (c.get("gt_scores") or {}).get("diff_f1"),
            "dump_file": rel(f),
        })

    db.close_connection()
    with open(OUT, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")

    groups = {(r["request_id"], r["sub"]) for r in rows}
    print(f"prompts.jsonl : {len(rows)} executor calls, "
          f"{len(groups)} (task, sub-goal) groups, "
          f"{len({r['request_id'] for r in rows})} tasks")
    print(f"  skipped     : {skipped}")
    print(f"  with a resolvable source solid: "
          f"{sum(1 for r in rows if r['source_step'])}")
    print(f"  colour-coded views            : "
          f"{sum(1 for r in rows if r['colour_coded'])}")
    print(f"  size        : {osp.getsize(OUT) / 1e6:.1f} MB")


if __name__ == "__main__":
    raise SystemExit(main())
