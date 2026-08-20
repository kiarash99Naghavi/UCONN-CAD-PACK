#!/usr/bin/env python3
"""Build the task index and the gallery thumbnails the site reads.

Two outputs, both under `site/`:

`data/tasks.json`   one row per request id — instruction, difficulty, the three
                    scores, the baseline numbers we are measured against, the
                    strategist's plan, and token/cost totals. The gallery sorts
                    and filters on this; the task pages render it.

`assets/thumbs/`    a trimmed, transparent thumbnail of each of the three parts
                    per task. The gallery needs 48 cards on one screen and 48
                    live WebGL contexts is not a thing a browser will give you,
                    so cards are images and only the task page mounts viewers.

Thumbnails are trimmed to the ink and re-squared before scaling, the same way
the pitch deck does it. The renderer frames every part to its own bounding box,
so untrimmed thumbnails mix parts that fill the frame with parts adrift in a sea
of white, and a row of them reads as though the parts were different sizes.

`ours` renders come from the winning run record for each task, which is the same
geometry `outputs/<rid>/ours.stl` was exported from. `input` and `gt` come from
the dataset's own renders. Run it with the benchmark interpreter:

    NEURALCAD_REPO=/path/to/IDETC26-Hackathon-Autodesk-neuralCAD-Edit \
        $NEURALCAD_REPO/.venv/bin/python tools/make_site_data.py
"""

import json
import os
import os.path as osp
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, osp.dirname(osp.abspath(__file__)))
from make_web_meshes import benchmark_repo, load_db     # noqa: E402

REPO = osp.dirname(osp.dirname(osp.abspath(__file__)))
RUNS = osp.join(REPO, "src", "results", "runs", "ours_adk-router", "outputs")
THUMBS = osp.join(REPO, "site", "assets", "thumbs")
TASKS = osp.join(REPO, "site", "data", "tasks.json")

THUMB = 420       # px on the long side of the emitted square thumbnail
MARGIN = 0.045    # fraction of the square left as background on every side


def background_alpha(im):
    """Alpha mask that is transparent on the render's background only.

    A brightness threshold would eat the part's own white highlights, so the
    background is found by flooding in from the four corners instead: only white
    connected to the edge of the frame counts, and a white face in the middle of
    the solid stays opaque. Built at source resolution, so the downsampling
    afterwards is what anti-aliases the cut-out edge.
    """
    # 0 is the marker the flood paints with, so the image is lifted off it
    # first: some renders are genuinely black in places and those pixels would
    # otherwise read back as background and be cut away.
    flat = im.convert("L").point(lambda v: max(v, 1))
    for corner in ((0, 0), (im.width - 1, 0),
                   (0, im.height - 1), (im.width - 1, im.height - 1)):
        ImageDraw.floodfill(flat, corner, 0, thresh=10)
    return flat.point(lambda v: 0 if v == 0 else 255)


def thumbnail(src, dst):
    """Trim one render to its ink, re-square it, write a transparent WebP."""
    if not src or not osp.exists(src):
        return False
    im = Image.open(src).convert("RGB")
    # near-white counts as background: the renders carry a faint gradient and a
    # hard 255 threshold leaves a full-bleed bounding box on some of them
    box = im.point(lambda v: 255 if v < 246 else 0).convert("L").getbbox()
    if box is None:
        return False
    l, t, r, b = box
    side = int(max(r - l, b - t) * (1 + 2 * MARGIN))
    cx, cy = (l + r) // 2, (t + b) // 2
    x0, y0 = cx - side // 2, cy - side // 2

    im.putalpha(background_alpha(im))
    # the square can hang off the edge of the render, so the visible part is
    # pasted into a transparent tile at its own offset rather than cropped
    crop = im.crop((max(0, x0), max(0, y0),
                    min(im.width, x0 + side), min(im.height, y0 + side)))
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(crop, (max(0, -x0), max(0, -y0)))
    out = out.resize((THUMB, THUMB), Image.LANCZOS)

    os.makedirs(osp.dirname(dst), exist_ok=True)
    out.save(dst, "WEBP", quality=82, method=6)
    return True


def run_dirs_by_request():
    """request id -> the run record folder that produced the shipped geometry.

    A task can have several run folders from re-runs; the one that shipped is
    the one whose end time is latest, which is also the one `outputs/` was
    exported from.
    """
    found = {}
    if not osp.isdir(RUNS):
        return found
    for run in os.listdir(RUNS):
        end = osp.join(RUNS, run, "brep_end")
        if not osp.isdir(end):
            continue
        for stamp in os.listdir(end):
            d = osp.join(end, stamp)
            settings = osp.join(d, "settings.json")
            if not osp.exists(settings):
                continue
            with open(settings) as f:
                s = json.load(f)
            rid = s.get("edit_request_id")
            if not rid:
                continue
            when = s.get("end_time") or s.get("start_time") or 0
            if rid not in found or when > found[rid][0]:
                found[rid] = (when, d)
    return {rid: d for rid, (_, d) in found.items()}


def main():
    db = load_db()
    root = db.root_dir
    requests = {r["_id"]: r for r in db.requests.find({})}

    gt_brep = {}
    for e in db.edits.find({}):
        req = requests.get(e.get("request"))
        if req is not None and e["user"] == req["user"]:
            gt_brep[e["request"]] = e.get("brep_end")

    with open(osp.join(REPO, "outputs", "manifest.json")) as f:
        manifest = json.load(f)

    # the plan, token and baseline columns only exist in the run export; the
    # site degrades to scores-only for any task that has no row here
    runs = {}
    runs_path = osp.join(REPO, "handoff", "runs.jsonl")
    if osp.exists(runs_path):
        with open(runs_path) as f:
            for line in f:
                row = json.loads(line)
                runs[row["request_id"]] = row

    shipped = run_dirs_by_request()
    mesh_index = {}
    mesh_path = osp.join(REPO, "site", "data", "meshes.json")
    if osp.exists(mesh_path):
        with open(mesh_path) as f:
            mesh_index = json.load(f)

    tasks, missing = [], []
    for rid in sorted(manifest):
        req = requests.get(rid, {})
        entry = manifest[rid]
        run = runs.get(rid, {})

        srcs = {"ours": osp.join(shipped.get(rid, ""), "tmp_toprightiso.jpg")}
        for role, brep in (("input", req.get("brep_start")),
                           ("gt", gt_brep.get(rid))):
            b = db.breps.find_one({"_id": brep}) if brep else None
            srcs[role] = osp.join(root, "breps", f"{brep}_toprightiso.jpg") \
                if b else None

        thumbs = {}
        for role, src in srcs.items():
            dst = osp.join(THUMBS, rid, role + ".webp")
            if thumbnail(src, dst):
                thumbs[role] = f"assets/thumbs/{rid}/{role}.webp"
            else:
                missing.append(f"{rid} {role} thumbnail")

        plan = run.get("plan") or {}
        tasks.append({
            "id": rid,
            "instruction": entry.get("instruction")
                or req.get("text", "").strip(),
            "difficulty": entry.get("difficulty") or req.get("difficulty"),
            "part": req.get("filename_cleaned") or "",
            "scores": entry.get("scores", {}),
            "baselines": run.get("baselines", {}),
            "cost_usd": entry.get("cost_estimate_usd"),
            "tokens": run.get("tokens", {}),
            "plan": {
                "understanding": plan.get("understanding", ""),
                "subgoals": [
                    {"goal": s.get("goal", ""), "tags": s.get("tags", [])}
                    for s in plan.get("subgoals", [])
                ],
            },
            "thumbs": thumbs,
            "meshes": {
                role: f"assets/meshes/{rid}/{role}.glb"
                for role in ("input", "ours", "gt")
                if role in mesh_index.get(rid, {})
            },
            "downloads": {
                name: f"https://github.com/kiarash99Naghavi/UCONN-CAD-PACK"
                      f"/raw/main/outputs/{rid}/{name}"
                for name in entry.get("files", [])
            },
        })

    os.makedirs(osp.dirname(TASKS), exist_ok=True)
    with open(TASKS, "w") as f:
        json.dump(tasks, f, indent=1)

    size = os.path.getsize(TASKS)
    thumb_bytes = sum(
        os.path.getsize(osp.join(dirpath, n))
        for dirpath, _, names in os.walk(THUMBS) for n in names)
    print(f"{len(tasks)} tasks -> site/data/tasks.json ({size/1024:.0f} kB)")
    print(f"thumbnails -> site/assets/thumbs ({thumb_bytes/1e6:.1f} MB)")
    if missing:
        print(f"{len(missing)} missing:")
        for line in missing:
            print("  " + line)


if __name__ == "__main__":
    main()
