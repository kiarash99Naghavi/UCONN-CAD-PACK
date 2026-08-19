"""Multi-view rendering — reuses the benchmark's own camera projections.

The baseline feeds the model exactly one picture (`tmp.png`) per iteration, so
it can never see the back or underside of what it just built. The benchmark
compares six orthographic views. We render all seven and hand the six
orthographic ones to the QA agent, so self-inspection matches what is scored.

All rendering happens in a child process — see render_proc.py for why.
"""

import json
import os
import os.path as osp
import subprocess
import sys

from .. import config

_PROC = osp.join(osp.dirname(osp.abspath(__file__)), "render_proc.py")
_RENDER_TIMEOUT = 300


def _run(step_path, out_dir, stem, views, size, stl=None, colors=None):
    """`views` is an explicit list; [] means render nothing (STL-only export).

    `colors` is the path to a colour-plan JSON (see `render_views_tagged`); the
    plan travels as a FILE, never on the command line — it is 2-3 kB of face
    indices on the benchmark inputs (measured: 2.0 kB on the 90-face rotor,
    3.0 kB on the 3362-face input), which would make every render command in
    the log unreadable and grow with the part.
    """
    os.makedirs(out_dir, exist_ok=True)
    cmd = [sys.executable, _PROC, "--step", str(step_path),
           "--out_dir", str(out_dir), "--stem", stem, "--size", str(size),
           "--views", ",".join(views)]
    if stl:
        cmd += ["--stl", str(stl)]
    if colors:
        cmd += ["--colors", str(colors)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_RENDER_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"  [render] timed out after {_RENDER_TIMEOUT}s")
        return {"views": {}, "stl": None, "errors": ["timeout"]}

    for line in p.stdout.splitlines():
        if line.startswith("RENDER: "):
            try:
                return json.loads(line[len("RENDER: "):])
            except json.JSONDecodeError:
                pass
    return {"views": {}, "stl": None,
            "errors": [f"no RENDER line; stderr={p.stderr[-400:]}"]}


def render_views(step_path, out_dir, stem="tmp", views=None, size=None):
    """Render `views` of a STEP file into out_dir as <stem>_<view>.png.

    Returns {view: path} for the ones that actually rendered.
    """
    views = views or config.ALL_VIEWS
    size = size or config.RENDER_SIZE
    res = _run(step_path, out_dir, stem, views, size)
    for e in res.get("errors", []):
        print(f"  [render] {e}")
    return res.get("views", {})


def render_views_tagged(step_path, out_dir, insp, stem="tag", views=None,
                        size=None):
    """Render `views` with each feature family in its own colour.

    Returns `(views_dict, plan)` — the same {view: path} map as `render_views`,
    plus the colour plan (see `geometry.color_plan`) so the caller can print the
    colour -> face_idx legend beside the pictures. A picture the model cannot
    key back to the geometry index is the failure this exists to fix, so the two
    always travel together.

    Degrades all the way down: any failure — an empty plan, an unwritable plan
    file, a child process that produced nothing — falls back to the plain grey
    `render_views` and returns `(views, [])`. A caller that gets an empty plan
    prints no legend and is otherwise exactly where it was before.
    """
    views = views or config.ALL_VIEWS
    size = size or config.RENDER_SIZE
    try:
        # Imported here, not at module scope: geometry pulls in cadquery, and
        # this module's whole point is that the parent process does not need it.
        from . import geometry

        plan = geometry.color_plan(insp)
        if not any(g.get("face_ids") for g in plan):
            raise ValueError("colour plan has no face groups")

        os.makedirs(out_dir, exist_ok=True)
        cpath = osp.join(out_dir, f"{stem}_colorplan.json")
        with open(cpath, "w") as fh:
            json.dump(plan, fh)

        res = _run(step_path, out_dir, stem, views, size, colors=cpath)
        for e in res.get("errors", []):
            print(f"  [render] {e}")
        got = res.get("views", {})
        if not got:
            raise RuntimeError("tagged render produced no views")
        return got, plan
    except Exception as e:
        print(f"  [render] tagged render unavailable ({e}) — plain render")
        return render_views(step_path, out_dir, stem=stem, views=views,
                            size=size), []


def render_views_colored(step_path, out_dir, plan, stem="chg", views=None,
                         size=None):
    """Render with an EXPLICIT colour plan (e.g. `geometry.change_color_plan`),
    rather than one derived from an inspection like `render_views_tagged`.

    Returns {view: path}, or {} on any failure — callers must fall back to the
    natural renders they already hold, so nothing here raises.
    """
    views = views or config.ALL_VIEWS
    size = size or config.RENDER_SIZE
    try:
        if not any(g.get("face_ids") for g in plan or []):
            return {}
        os.makedirs(out_dir, exist_ok=True)
        cpath = osp.join(out_dir, f"{stem}_colorplan.json")
        with open(cpath, "w") as fh:
            json.dump(plan, fh)
        res = _run(step_path, out_dir, stem, views, size, colors=cpath)
        for e in res.get("errors", []):
            print(f"  [render] {e}")
        return res.get("views", {})
    except Exception as e:
        print(f"  [render] colored render unavailable ({e})")
        return {}


def export_stl(step_path, stl_path):
    """STL-only export in a fresh child process. Returns the path, or None.

    The recovery half of `render_and_export`: when the combined call dies in
    the view-dumping half (an offscreen-window failure kills the whole child),
    the STL — the one artifact the scorer cannot live without — dies with it.
    A views-less child skips everything but importStep + export.
    """
    res = _run(step_path, osp.dirname(str(stl_path)) or ".", "stlonly", [],
               config.RENDER_SIZE, stl=stl_path)
    for e in res.get("errors", []):
        print(f"  [render] {e}")
    return res.get("stl")


def render_and_export(step_path, out_dir, stl_path, stem="tmp", views=None,
                      size=None):
    """Renders and exports the STL in a single child process."""
    views = views or config.ALL_VIEWS
    size = size or config.RENDER_SIZE
    res = _run(step_path, out_dir, stem, views, size, stl=stl_path)
    for e in res.get("errors", []):
        print(f"  [render] {e}")
    return res.get("views", {}), res.get("stl")


def export_stl(step_path, stl_path, tolerance=0.01):
    """STEP -> STL only. This is the file the benchmark actually scores.

    Passes an empty view list so no PNGs are produced; an earlier version left
    seven throwaway `unused_*.png` files in the submission directory.
    """
    res = _run(step_path, osp.dirname(str(stl_path)) or ".", "stl", [],
               config.RENDER_SIZE, stl=stl_path)
    for e in res.get("errors", []):
        print(f"  [stl] {e}")
    return res.get("stl")


def view_axis_table(views=None):
    """The view -> world-direction table, READ OFF THE RENDERER ITSELF.

    This project's projections are not the usual CAD convention: `front` looks
    along +Z and `top` along +Y, where a CAD reader expects front to look along
    -Y and top along +Z. Sub-goals are written entirely in world coordinates
    ("the outer +/-Y rail planes", "face #46, normal [0,1,0]"), so an agent has
    to map one onto the other and had nothing to map with.

    Measured twice. QA (task SUJ2G2UMJQR7PMBX_1759203664): attempts 2, 3 and 6
    cut slots through exactly the +/-Y faces the sub-goal named and QA rejected
    all three reasoning "the AFTER front/back views show no corresponding slot
    pattern" — those faces appear in `top`/`bottom`. STRATEGIST (tasks
    B7A2N74ZJBF9MZHU_1770174519 and ..._1770174308): both plans named the wrong
    world face on a coffee machine and lost the whole run; the executor hit the
    human's footprint to 0.4 mm and the volume to 0.02% on the wrong side.

    Generated from `VIEW_PROJECTIONS` rather than written out, so it cannot
    drift away from what was actually rendered.
    """
    try:
        from src.utils.cadquery_rendering import VIEW_PROJECTIONS
    except Exception:
        return "  (view/axis table unavailable)"

    names = {(0, 0, 1): "+Z", (0, 0, -1): "-Z", (1, 0, 0): "+X",
             (-1, 0, 0): "-X", (0, 1, 0): "+Y", (0, -1, 0): "-Y"}
    lines = []
    for v in (views if views is not None else config.ALL_VIEWS):
        proj = VIEW_PROJECTIONS.get(v)
        if not proj:
            continue
        axis = names.get(tuple(proj))
        lines.append(f"  {v:12} shows the {axis} face (camera on the {axis} "
                     f"side, looking back along it)" if axis else
                     f"  {v:12} is an isometric view from {tuple(proj)}")
    return "\n".join(lines) or "  (no orthographic views in this set)"
