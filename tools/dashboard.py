#!/usr/bin/env python3
"""UCONN CAD PACK, the dashboard for our ASME 2026 Student Hackathon
submission: text-conditioned CAD editing with a three-agent loop.

Tab 1  Method overview        the deck: the pipeline, the shapes, the numbers
Tab 2  Task & input           the instruction, the input part, the ground truth
Tab 3  Ground truth vs model  any benchmark method against the human edit
Tab 4  Our implementation     run the agent pipeline and watch it work
Tab 5  Results                every method on one task, or the whole benchmark
Tab 6  Animation flow         a saved run replayed over the pipeline
Tab 7  Competition examples   the five tasks the organisers named, on one slide

Run from the repo root:
    ./run_dashboard.sh

Then open http://127.0.0.1:8050
"""

import base64
import datetime
import glob
import json
import os
import os.path as osp
import re
import statistics
import threading
import traceback
from functools import lru_cache

import numpy as np
import open3d as o3d
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update

from src.utils.db import DatabaseManager
from src.utils.process_config import load_config

# charts and design tokens. Running this file directly puts tools/ on sys.path,
# so the bare name resolves; run_dashboard.sh puts the project root there too,
# which is the form run_headless.py imports through.
try:
    from tools import dashviz as viz
except ImportError:  # pragma: no cover
    import dashviz as viz

# our agent pipeline — optional, so the browser still works without a key
try:
    from src import config as our_config
    from src import evaluate as our_eval
    from src import pipeline as our_pipeline
    OURS_AVAILABLE = True
    OURS_IMPORT_ERROR = ""
except Exception as _e:  # pragma: no cover
    OURS_AVAILABLE = False
    OURS_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"

CONFIG = "src/config/edit_192_external.json"
METRICS = ["chamfer_similarity_norm", "volume_f1", "diff_f1"]
METRIC_LABELS = {
    "chamfer_similarity_norm": "Chamfer similarity",
    "volume_f1": "Volume F1",
    "diff_f1": "Diff F1",
}
VIEWS = ["toprightiso", "front", "back", "left", "right", "top", "bottom"]
# Decimation cap for the 3D viewers. 60k triangles serialized to several MB
# of Plotly JSON per viewer; two viewers per tab plus the polled re-renders
# overflowed the server socket (errno 55). 20k is visually equivalent at
# dashboard size.
MAX_TRIS = 20000

BG = "#eef1f7"
PANEL = "#ffffff"
FG = viz.INK
MUTED = viz.INK_2
FAINT = viz.INK_3
ACCENT = viz.S1
BORDER = "#e1e6f0"
TILE = viz.SURFACE_SUNK

# score / status colours, reserved: never reused as a series colour
GOOD, WARN, BAD = viz.GOOD, viz.WARN, viz.BAD
MESH_INPUT, MESH_GT, MESH_PRED = "#a8b2c1", viz.GT_COLOR, viz.OURS_COLOR

DIFF_COLOR = viz.DIFF_COLOR

# Human-readable names for the benchmark's own user keys. The dataset stores a
# model's edits under "<model>_cadquery-script"; nobody reads that on a slide.
METHOD_LABEL = {
    "gpt-5.2_cadquery-script": "GPT-5.2",
    "claude-sonnet-4.5_cadquery-script": "Claude Sonnet 4.5",
    "gemini-3-pro_cadquery-script": "Gemini 3 Pro",
    "gpt-5.6-sol_openrouter_cadquery-script": "GPT-5.6-sol",
    "other human": "Second human",
    "gt human": "Ground truth",
}
OURS = "Ours (agent loop)"
# Excluded from every comparison: it is the ground truth scored against itself,
# so it is 1.0 by construction and only flatters the chart.
SELF_SCORED = "gt human"

# The page sets a proportional sans-serif on the outermost div, so anything
# showing code or logs has to name its own family — never leave it to be
# inherited. One shared look for all of them, see code_block().
MONO = ('ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, '
        '"Liberation Mono", monospace')
CODE_STYLE = {
    "fontFamily": MONO, "fontSize": "12.5px", "lineHeight": "1.5",
    "tabSize": "4",
    "color": FG, "background": BG, "border": f"1px solid {BORDER}",
    "borderRadius": "5px", "padding": "12px 14px", "margin": "6px 0 0 0",
    # keep every space and newline the model wrote; scroll long lines
    # sideways instead of clipping them
    "whiteSpace": "pre", "overflowX": "auto",
    "maxHeight": "70vh", "overflowY": "auto",
}


REPO_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
LOGO_PATH = osp.join(REPO_ROOT, "Logos", "Hackathon-Logo.png")
UCONN_LOGO_PATH = osp.join(REPO_ROOT, "Logos",
                           "starter-side-colors_MAM BLUE GRAY.png")


def logo_src(path=LOGO_PATH):
    """Base64 data-URI for a logo (Dash cannot serve local files)."""
    if not osp.exists(path):
        return None
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------
db = DatabaseManager(load_config(CONFIG))
ROOT = db.root_dir

REQUESTS = {r["_id"]: r for r in db.requests.find({})}
EDITS = list(db.edits.find({}))

try:
    with open(osp.join(ROOT, "results", "all_results.json")) as f:
        RESULTS = json.load(f)
except FileNotFoundError:
    RESULTS = {}


def role_of(edit):
    """Classify an edit: ground truth, other human, or a model prediction."""
    req = REQUESTS.get(edit.get("request"))
    if req is None:
        return "unknown"
    if edit["user"].endswith("cadquery-script"):
        return "model"
    return "gt" if edit["user"] == req["user"] else "other human"


# request_id -> {label: brep_id}
EDITS_BY_REQUEST = {}
for e in EDITS:
    rid = e.get("request")
    if rid not in REQUESTS:
        continue
    role = role_of(e)
    label = "GROUND TRUTH" if role == "gt" else (
        "other human" if role == "other human" else e["user"])
    EDITS_BY_REQUEST.setdefault(rid, {})[label] = e.get("brep_end")


def score_for(request_id, user, metric):
    """Look up one score. Returns (value, is_failure)."""
    for diff in RESULTS:
        block = RESULTS[diff].get(user, {}).get(metric, {})
        if request_id in block:
            v = block[request_id]
            if v is None or v != v:  # None or NaN -> counted as 0.0
                return 0.0, True
            return float(v), False
    return None, False


def geom_path(brep_id, ext):
    if not brep_id:
        return None
    p = osp.join(ROOT, "breps", f"{brep_id}.{ext}")
    return p if osp.exists(p) else None


def published_methods():
    """Every baseline this dataset actually shipped, in a stable order.

    all_results.json is keyed difficulty -> user -> metric -> request, and the
    three difficulty blocks do not necessarily carry the same users, so the
    union is taken. Ordering is by how many tasks the method has a diff F1 for,
    descending.

    A user that appears in the score file but has no edits in the database is
    dropped. One does: `gpt-5.6-sol_openrouter_cadquery-script` carries a row
    of zeros and not a single B-rep, so it drew an empty panel on every task
    and an empty bar on every chart while still counting as a competitor in
    the win/loss tally. A baseline we cannot show is not a baseline.
    """
    have_geometry = set()
    for labels in EDITS_BY_REQUEST.values():
        have_geometry.update(labels)

    counts = {}
    for diff in RESULTS:
        for user, block in RESULTS[diff].items():
            if user == SELF_SCORED or user not in have_geometry:
                continue
            counts[user] = counts.get(user, 0) + len(block.get("diff_f1", {}))
    return sorted(counts, key=lambda u: (-counts[u], u))


METHODS = published_methods()


def method_label(user):
    return METHOD_LABEL.get(user, user.replace("_cadquery-script", ""))


# --------------------------------------------------------------------------
# our own runs, read back off disk
# --------------------------------------------------------------------------
def _runs_dir():
    return osp.join(our_config.RESULTS, "dashboard_runs") if OURS_AVAILABLE \
        else None


@lru_cache(maxsize=1)
def _our_runs_cached(signature):
    """Every saved run record, keyed by request id. `signature` is the set of
    (filename, mtime) pairs, so the cache invalidates the moment a run finishes
    and rewrites its record, and costs one directory listing otherwise."""
    d = _runs_dir()
    out = {}
    if not d:
        return out
    for path in glob.glob(osp.join(d, "*.json")):
        rid = osp.splitext(osp.basename(path))[0]
        if rid not in REQUESTS:
            continue
        try:
            with open(path) as f:
                rec = json.load(f)
        except Exception:
            continue
        # only records that carry real numbers are worth aggregating
        if isinstance(rec.get("scores"), dict):
            out[rid] = rec
    return out


def our_runs():
    d = _runs_dir()
    if not d:
        return {}
    try:
        sig = tuple(sorted((osp.basename(p), int(osp.getmtime(p)))
                           for p in glob.glob(osp.join(d, "*.json"))))
    except OSError:
        sig = ()
    return _our_runs_cached(sig)


@lru_cache(maxsize=4)
def _sweep_scores_cached(mtime):
    """`results/scores/<user>.json`, written by evaluate.py when it scores a
    whole sweep of `results/runs/`. It covers tasks that were run before the
    dashboard started keeping its own per-task records, so it fills gaps the
    run records leave."""
    del mtime
    out = {}
    if not OURS_AVAILABLE:
        return out
    path = osp.join(our_config.RESULTS, "scores", "ours_adk-router.json")
    try:
        with open(path) as f:
            blob = json.load(f)
    except Exception:
        return out
    for rid, entry in (blob.get("per_request") or {}).items():
        if rid in REQUESTS and isinstance(entry, dict):
            out[rid] = entry
    return out


def sweep_scores():
    path = osp.join(our_config.RESULTS, "scores", "ours_adk-router.json") \
        if OURS_AVAILABLE else None
    try:
        mt = int(osp.getmtime(path))
    except (OSError, TypeError):
        mt = 0
    return _sweep_scores_cached(mt)


def _run_ts(path_or_none):
    """The run's own timestamp, taken out of `ours_adk-router_<ts>` in its
    output path. Both score sources carry that path, so it is the one clock
    they can be compared on."""
    if not path_or_none:
        return None
    for part in str(path_or_none).replace("\\", "/").split("/"):
        if "_" in part:
            tail = part.rsplit("_", 1)[-1]
            try:
                return float(tail)
            except ValueError:
                continue
    return None


def _record_ts(rec):
    ts = _run_ts(rec.get("dest"))
    if ts is not None:
        return ts
    try:
        return datetime.datetime.fromisoformat(rec["saved_at"]).timestamp()
    except Exception:
        return 0.0


def _sweep_ts(entry):
    v = entry.get("scored_at")
    if isinstance(v, (int, float)):
        return float(v)
    return _run_ts(entry.get("edit_id")) or 0.0


def latest_source(rid):
    """The single most recent run of a task, whichever file recorded it.

    Only the last run is ever shown. Two files can hold a score for the same
    task: the dashboard's own per-task record, and the sweep file that scores
    everything under results/runs/. They disagree when one of them scored an
    older run, so the run timestamps are compared and the newer one wins
    outright, scores and geometry together. Nothing is blended.

    Returns (kind, payload) where kind is "record" or "sweep".
    """
    rec = our_runs().get(rid)
    entry = sweep_scores().get(rid)
    if rec and entry:
        return ("record", rec) if _record_ts(rec) >= _sweep_ts(entry) \
            else ("sweep", entry)
    if rec:
        return "record", rec
    if entry:
        return "sweep", entry
    return None, None


def our_score(rid, metric):
    """Our value for one metric on one task, from its latest run only."""
    kind, payload = latest_source(rid)
    if not kind:
        return None
    scores = (payload.get("scores") or {}) if kind == "record" else payload
    v = scores.get(metric)
    return float(v) if isinstance(v, (int, float)) else None


def our_task_ids():
    """Every task we have any score of our own for."""
    return [r for r in REQUESTS if r in our_runs() or r in sweep_scores()]


def our_cost(rid):
    kind, payload = latest_source(rid)
    if not kind:
        return None
    v = ((payload.get("tokens") or {}).get("cost_estimate")
         if kind == "record" else payload.get("cost_estimate"))
    return float(v) if isinstance(v, (int, float)) else None


# --------------------------------------------------------------------------
# rendering helpers
# --------------------------------------------------------------------------
DIFF_KEEP = "#c3cad6"       # surface both parts agree on
DIFF_OURS = viz.OURS_COLOR  # surface only our result has
DIFF_GT = viz.S2            # surface only the ground truth has
# A surface counts as moved when it sits further than this fraction of the
# combined bounding-box diagonal from the other part. Mesh vertices never land
# in the same places on two independently built solids, so an exact test marks
# every triangle as changed; 0.4% of the diagonal is below the smallest feature
# any of these instructions asks for and above the tessellation noise.
DIFF_TOL_FRAC = 0.004


@lru_cache(maxsize=96)
def load_mesh(stl_path, max_tris=MAX_TRIS):
    """(vertices, triangles) from an STL, decimated if very dense."""
    mesh = o3d.io.read_triangle_mesh(stl_path)
    if len(mesh.triangles) > max_tris:
        mesh = mesh.simplify_quadric_decimation(max_tris)
    mesh.compute_vertex_normals()
    return np.asarray(mesh.vertices), np.asarray(mesh.triangles)


def mesh_figure(stl_path, color, title, height=430, max_tris=MAX_TRIS):
    """Interactive 3D view of one STL.

    `max_tris` is lowered by callers that put many viewers on one page: the
    results tab draws seven at once, and seven 20k-triangle meshes serialize to
    more Plotly JSON than the dev server will push in one response.
    """
    fig = go.Figure()
    if stl_path:
        v, t = load_mesh(stl_path, max_tris)
        fig.add_trace(go.Mesh3d(
            x=v[:, 0], y=v[:, 1], z=v[:, 2],
            i=t[:, 0], j=t[:, 1], k=t[:, 2],
            color=color, opacity=1.0, flatshading=True,
            lighting=dict(ambient=0.62, diffuse=0.8, specular=0.2, roughness=0.65),
            lightposition=dict(x=200, y=200, z=300),
            hoverinfo="skip",
        ))
    else:
        fig.add_annotation(text="no geometry, this edit failed (scores 0.0)",
                           showarrow=False,
                           font=dict(color=BAD, size=13, family=viz.FONT))
    fig.update_layout(
        title=dict(text=title, font=dict(color=FG, size=12.5, family=viz.FONT),
                   x=0, xanchor="left"),
        scene=dict(
            aspectmode="data",
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            zaxis=dict(visible=False),
            bgcolor=PANEL,
            camera=dict(eye=dict(x=1.45, y=1.45, z=1.05)),
        ),
        paper_bgcolor=PANEL, margin=dict(l=0, r=0, t=30, b=0), height=height,
        showlegend=False, font=dict(family=viz.FONT),
    )
    return fig


# --------------------------------------------------------------------------
# surface deviation — the two solids in one view, with what moved picked out
# --------------------------------------------------------------------------
@lru_cache(maxsize=8)
def surface_deviation(pred_stl, gt_stl, mtimes, max_tris=MAX_TRIS):
    """Split both solids into the surface they share and the surface they do
    not, by distance from each vertex to the other solid's surface.

    A boolean difference of the two solids would be the textbook answer, and it
    is the wrong tool here: these are tessellated STLs, not clean B-reps, and a
    boolean on two independently meshed parts fails or leaves slivers often
    enough to be useless in a viewer. Nearest-surface distance is what CAD
    inspection actually uses for this, it never fails, and it answers the
    question being asked, which is where the two parts stop agreeing.

    Returns (pred verts, pred tris, pred changed mask, gt verts, gt tris,
    gt changed mask, tolerance, bbox diagonal), or None.
    """
    del mtimes                                  # cache key only
    if not pred_stl or not gt_stl:
        return None
    try:
        vp, tp = load_mesh(pred_stl, max_tris)
        vg, tg = load_mesh(gt_stl, max_tris)
    except Exception as e:
        print(f"[dashboard] deviation: could not load a mesh: {e}")
        return None
    if len(tp) == 0 or len(tg) == 0:
        return None

    both = np.vstack([vp, vg])
    diag = float(np.linalg.norm(both.max(axis=0) - both.min(axis=0)))
    tol = max(diag * DIFF_TOL_FRAC, 1e-9)

    def to_surface(query, ref_v, ref_t):
        scene = o3d.t.geometry.RaycastingScene()
        scene.add_triangles(
            o3d.core.Tensor(np.ascontiguousarray(ref_v, dtype=np.float32)),
            o3d.core.Tensor(np.ascontiguousarray(ref_t, dtype=np.uint32)))
        return scene.compute_distance(
            o3d.core.Tensor(np.ascontiguousarray(query,
                                                 dtype=np.float32))).numpy()

    try:
        dp = to_surface(vp, vg, tg)
        dg = to_surface(vg, vp, tp)
    except Exception as e:
        print(f"[dashboard] deviation: distance query failed: {e}")
        return None

    # a triangle is flagged when any of its corners has moved, so a face that
    # is only partly new is still shown as new rather than silently dropped
    pred_changed = dp[tp].max(axis=1) > tol
    gt_changed = dg[tg].max(axis=1) > tol
    return vp, tp, pred_changed, vg, tg, gt_changed, tol, diag


def _mesh_trace(v, t, mask, color, name, opacity=1.0, show=True):
    if mask is not None:
        t = t[mask]
    if len(t) == 0:
        return None
    return go.Mesh3d(
        x=v[:, 0], y=v[:, 1], z=v[:, 2],
        i=t[:, 0], j=t[:, 1], k=t[:, 2],
        color=color, opacity=opacity, flatshading=True,
        lighting=dict(ambient=0.62, diffuse=0.8, specular=0.2, roughness=0.65),
        lightposition=dict(x=200, y=200, z=300),
        hoverinfo="skip", name=name, showlegend=show,
    )


def deviation_figure(pred_stl, gt_stl, height=420):
    """Both solids in one scene: shared surface in gray, what only we have in
    blue, what only the human has in amber."""
    if not pred_stl or not gt_stl:
        fig = go.Figure()
        fig.add_annotation(
            text=("need both parts to compare: "
                  + ("our result is missing" if not pred_stl
                     else "no ground truth .stl on disk")),
            showarrow=False, font=dict(color=BAD, size=13, family=viz.FONT))
        fig.update_layout(paper_bgcolor=PANEL, height=height,
                          margin=dict(l=0, r=0, t=30, b=0),
                          scene=dict(xaxis=dict(visible=False),
                                     yaxis=dict(visible=False),
                                     zaxis=dict(visible=False)))
        return fig, None

    key = tuple(int(osp.getmtime(p)) if osp.exists(p) else 0
                for p in (pred_stl, gt_stl))
    out = surface_deviation(pred_stl, gt_stl, key)
    if out is None:
        return mesh_figure(pred_stl, MESH_PRED,
                           "OUR RESULT (the two parts could not be "
                           "compared)", height), None
    vp, tp, pred_changed, vg, tg, gt_changed, tol, diag = out

    traces = [
        # the agreed surface is drawn once, from our result. Drawing it from
        # both would put two coincident skins in the same place and the
        # depth buffer would tear them into speckle.
        _mesh_trace(vp, tp, ~pred_changed, DIFF_KEEP, "unchanged surface"),
        _mesh_trace(vg, tg, gt_changed, DIFF_GT,
                    "only in the ground truth", opacity=0.92),
        _mesh_trace(vp, tp, pred_changed, DIFF_OURS,
                    "only in our result", opacity=0.92),
    ]
    fig = go.Figure([t for t in traces if t is not None])
    fig.update_layout(
        title=dict(text="OUR RESULT AND THE GROUND TRUTH, OVERLAID",
                   font=dict(color=FG, size=12.5, family=viz.FONT),
                   x=0, xanchor="left"),
        scene=dict(aspectmode="data",
                   xaxis=dict(visible=False), yaxis=dict(visible=False),
                   zaxis=dict(visible=False), bgcolor=PANEL,
                   camera=dict(eye=dict(x=1.45, y=1.45, z=1.05))),
        paper_bgcolor=PANEL, margin=dict(l=0, r=0, t=30, b=0), height=height,
        font=dict(family=viz.FONT), showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=0, xanchor="left",
                    x=0, font=dict(size=11, color=MUTED),
                    bgcolor="rgba(255,255,255,0.75)"),
    )
    stats = {
        "tol": tol, "diag": diag,
        "ours_pct": 100.0 * float(pred_changed.mean()),
        "gt_pct": 100.0 * float(gt_changed.mean()),
    }
    return fig, stats


@lru_cache(maxsize=64)
def our_views(brep_id):
    """Render a dataset brep with OUR camera set, cached on disk.

    The stored `<brep>_<view>.jpg` files that ship with the dataset are NOT a
    consistent camera set: measured by silhouette overlap, one part's stored
    "front" matches our front, another's matches our top, and a third's "top"
    is our bottom mirrored. Comparing a prediction against a ground truth
    through those labels means comparing two different directions and calling
    them the same view. Rendering both sides here with
    `src.tools.render` — the same projections the QA agent and
    the step gallery already use — makes front mean front on both sides.
    """
    step = geom_path(brep_id, "step")
    if not step or not OURS_AVAILABLE:
        return {}
    out_dir = osp.join(our_config.RESULTS, "render_cache", str(brep_id))
    cached = {v: osp.join(out_dir, f"gt_{v}.png") for v in VIEWS}
    if all(osp.exists(p) for p in cached.values()):
        return cached
    try:
        from src.tools import render as our_render
        return our_render.render_views(step, out_dir, stem="gt")
    except Exception:
        return {}


def img_tag(brep_id, view, width="13%"):
    """Base64-inline a render (Dash cannot serve arbitrary local files)."""
    if not brep_id:
        return None
    ours = our_views(brep_id).get(view)
    p, note = (ours, None) if ours and osp.exists(ours) else (
        osp.join(ROOT, "breps", f"{brep_id}_{view}.jpg"),
        "dataset jpg — camera may differ")
    if not osp.exists(p):
        return None
    b64 = _b64_file(p, osp.getmtime(p))
    mime = "png" if p.lower().endswith(".png") else "jpeg"
    label = [html.Div(view, style={"color": MUTED, "fontSize": "11px",
                                   "textAlign": "center", "paddingTop": "3px"})]
    if note:
        label.append(html.Div(note, style={"color": BAD, "fontSize": "9px",
                                           "textAlign": "center"}))
    return html.Div([
        html.Img(src=f"data:image/{mime};base64,{b64}",
                 style={"width": "100%", "borderRadius": "5px",
                        "background": "#fff", "display": "block",
                        "border": f"1px solid {BORDER}"}),
        *label,
    ], style={"width": width, "padding": "4px"})


def file_label(brep_id):
    """Filename block under a 3D view: the brep id and which files exist on disk."""
    if not brep_id:
        return html.Div("no brep record", style={"color": BAD, "fontSize": "11px",
                                                 "fontFamily": "monospace"})
    have = [e for e in ("step", "stl") if geom_path(brep_id, e)]
    if not have:
        return html.Div([
            html.Div(f"{brep_id}", style={"color": MUTED, "fontSize": "11px",
                                          "fontFamily": "monospace",
                                          "wordBreak": "break-all"}),
            html.Div("no .step / .stl on disk — failed edit",
                     style={"color": BAD, "fontSize": "11px"}),
        ])
    return html.Div([
        html.Div(f"{brep_id}.{e}", style={
            "color": ACCENT, "fontSize": "11px", "fontFamily": "monospace",
            "wordBreak": "break-all", "lineHeight": "1.55"}) for e in have
    ] + [html.Div("breps/ — .step is the CAD file, .stl is what gets scored",
                  style={"color": MUTED, "fontSize": "10px", "paddingTop": "2px"})])


def card(children, **style):
    base = {"background": PANEL, "borderRadius": "11px", "padding": "17px 18px",
            "marginBottom": "14px", "border": f"1px solid {BORDER}",
            "boxShadow": "0 1px 2px rgba(16,24,40,0.05)"}
    base.update(style)
    return html.Div(children, style=base)


def code_block(text, **style):
    """A <pre> that reads like an editor pane: monospace, roomy, scrollable."""
    return html.Pre(text, style={**CODE_STYLE, **style})


TAB_STYLE = {"border": "none", "borderBottom": f"2px solid {BORDER}",
             "background": "transparent", "color": MUTED, "fontSize": "13px",
             "fontWeight": "600", "padding": "11px 6px", "letterSpacing": "0.2px"}
TAB_SEL_STYLE = {**TAB_STYLE, "color": ACCENT,
                 "borderBottom": f"2px solid {ACCENT}", "background": "transparent"}


def eyebrow(text, **style):
    """The small all-caps label that titles a block."""
    return html.Div(text, style={"color": FAINT, "fontSize": "11px",
                                 "fontWeight": "700", "letterSpacing": "1.4px",
                                 "marginBottom": "9px", **style})


def note(text, **style):
    return html.Div(text, style={"color": MUTED, "fontSize": "12px",
                                 "lineHeight": "1.55", **style})


def kpi(label, value, sub=None, color=None, width=None):
    """One headline number. No plot, so no hover layer: the number is the mark."""
    return html.Div([
        html.Div(label, style={"color": FAINT, "fontSize": "10.5px",
                               "fontWeight": "700", "letterSpacing": "1.2px"}),
        html.Div(value, style={"color": color or FG, "fontSize": "30px",
                               "fontWeight": "700", "lineHeight": "1.25",
                               "letterSpacing": "-0.7px", "marginTop": "3px",
                               "fontVariantNumeric": "tabular-nums"}),
        html.Div(sub or "", style={"color": MUTED, "fontSize": "11.5px",
                                   "marginTop": "2px", "lineHeight": "1.4"}),
    ], style={"flex": f"1 1 {width or '150px'}", "padding": "13px 15px",
              "background": TILE, "border": f"1px solid {BORDER}",
              "borderRadius": "9px"})


def pill(text, color, filled=True, **style):
    return html.Span(text, style={
        "background": color if filled else "transparent",
        "color": "#ffffff" if filled else color,
        "border": "none" if filled else f"1px solid {color}",
        "padding": "3px 10px", "borderRadius": "11px", "fontSize": "10.5px",
        "fontWeight": "700", "letterSpacing": "0.8px",
        "whiteSpace": "nowrap", **style})


def graph(fig, **style):
    """A Plotly figure sized by its container rather than by its own width.

    The height has to be stated on the div as well as in the figure: a graph
    in a CSS grid cell measures itself against a parent that has not been laid
    out yet, and without a definite height it renders at its default 450 px and
    spills out of the card.
    """
    h = fig.layout.height
    return dcc.Graph(figure=fig, config={"displayModeBar": False,
                                         "responsive": True},
                     style={"width": "100%", "minWidth": "0",
                            **({"height": f"{int(h)}px"} if h else {}),
                            **style})


def grid(children, min_width="440px", gap="14px"):
    """Responsive card grid. Charts reflow instead of overflowing sideways.

    `minmax(0, 1fr)` rather than `minmax(min_width, 1fr)` on the second half of
    the track: a grid item's default `min-width: auto` is its content's
    intrinsic width, so one wide child (a Plotly canvas, a long table) widens
    its whole column and pushes the row off screen.
    """
    return html.Div(children, style={
        "display": "grid", "gap": gap, "alignItems": "start",
        "gridTemplateColumns":
            f"repeat(auto-fit, minmax(min({min_width}, 100%), 1fr))"})


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------
_DIFF_RANK = {"easy": 0, "medium": 1, "hard": 2}


def task_options():
    """The dropdown, rebuilt on every tab switch.

    The ● / ○ marker says whether we have a run for that task, and runs land
    while the dashboard is open: a headless sweep in another terminal writes
    records into results/dashboard_runs, and the scores on tab 5 pick them up
    on their own. Built once at import, the markers would keep claiming a task
    had never been run hours after it had.
    """
    scored = set(our_task_ids())
    out = []
    for rid, r in sorted(REQUESTS.items(),
                         key=lambda kv: (
                             _DIFF_RANK.get(kv[1].get("difficulty"), 9),
                             kv[1].get("text", ""))):
        d = r.get("difficulty", "?")
        ran = "●" if rid in scored else "○"
        out.append({"label": f"{ran}  {d:<6}   {r.get('text','')[:88]}",
                    "value": rid})
    return out


options = task_options()

# tabs build their bodies dynamically, so callbacks referencing those
# components cannot be validated against the initial layout
app = Dash(__name__, suppress_callback_exceptions=True)
app.title = "UCONN CAD PACK · ASME 2026 Student Hackathon"

_LOGO = logo_src()
_UCONN_LOGO = logo_src(UCONN_LOGO_PATH)

TABS = [
    ("method", "1 · Method overview"),
    ("input", "2 · Task & input"),
    ("compare", "3 · Ground truth vs model"),
    ("ours", "4 · Our implementation"),
    ("results", "5 · Results"),
    ("flow", "6 · Animation flow"),
    ("competition", "7 · Competition examples"),
]
# Tab 1 is the same for every task and tab 5 has its own scope switch, so the
# task picker is hidden there rather than sitting inert at the top of a slide.
TASK_PICKER_TABS = {"input", "compare", "ours", "results", "flow"}

app.layout = html.Div([
    html.Div([
        *([html.Div([
            html.Img(src=_LOGO, alt="ASME 2026 Student Hackathon logo",
                     style={"height": "76px", "display": "block"}),
        ], style={"marginRight": "20px"})] if _LOGO else []),
        html.Div([
            html.Div([
                html.Span("ASME 2026 STUDENT HACKATHON"),
                # the benchmark is named on the front page because every score
                # anywhere in here is its score, computed by its own metric code
                html.Span("neuralCAD-Edit Benchmark", style={
                    "color": MUTED, "marginLeft": "10px", "fontWeight": "700"}),
            ], style={"color": ACCENT, "fontSize": "11.5px",
                      "fontWeight": "700", "letterSpacing": "1.6px",
                      "marginBottom": "5px"}),
            # the project's name leads, and what it does follows it in the
            # same line: a title nobody can decode is not a title
            html.Div([
                html.H2("UCONN CAD PACK",
                        style={"margin": "0", "color": FG, "fontSize": "25px",
                               "letterSpacing": "-0.4px",
                               "whiteSpace": "nowrap"}),
                html.Span("Text-conditioned CAD editing with a three-agent "
                          "loop",
                          style={"color": MUTED, "fontSize": "14.5px"}),
            ], style={"display": "flex", "alignItems": "baseline",
                      "gap": "11px", "flexWrap": "wrap",
                      "margin": "0 0 5px 0"}),
            html.Div("University of Connecticut · School of Mechanical, "
                     "Aerospace and Manufacturing Engineering",
                     style={"color": MUTED, "fontSize": "13px"}),
        ]),
        *([html.Img(src=_UCONN_LOGO,
                    alt="UConn College of Engineering",
                    style={"height": "60px", "display": "block",
                           "marginLeft": "auto"})] if _UCONN_LOGO else []),
    ], style={"marginBottom": "18px", "display": "flex",
              "alignItems": "center", "gap": "4px"}),

    html.Div(id="task-picker", children=card([
        html.Div([
            html.Label("TASK", style={"color": FAINT, "fontSize": "11px",
                                      "fontWeight": "700",
                                      "letterSpacing": "1.4px"}),
            html.Span("●  we have a saved run    ○  not run yet",
                      style={"color": FAINT, "fontSize": "11px",
                             "marginLeft": "auto"}),
        ], style={"display": "flex", "alignItems": "baseline"}),
        dcc.Dropdown(id="request", options=options, value=options[0]["value"],
                     clearable=False, optionHeight=34,
                     style={"marginTop": "7px"}),
    ], marginBottom="14px")),

    dcc.Tabs(id="tabs", value="method",
             children=[dcc.Tab(label=lab, value=val,
                               style=TAB_STYLE, selected_style=TAB_SEL_STYLE)
                       for val, lab in TABS],
             style={"height": "44px"},
             colors={"border": BORDER, "primary": ACCENT, "background": TILE}),

    html.Div(id="content", style={"marginTop": "16px"}),
    # 4 s, was 1.5 s: each tick re-sends the whole tab-4 body, and stacking
    # those responses faster than the browser drained them exhausted the
    # socket buffer (macOS errno 55) on long runs.
    dcc.Interval(id="poll", interval=4000, disabled=True),
    dcc.Store(id="node-pick"),
], style={"background": BG, "minHeight": "100vh", "padding": "22px 30px 60px",
          "fontFamily": viz.FONT})


@app.callback(Output("task-picker", "style"), Output("request", "options"),
              Input("tabs", "value"))
def toggle_task_picker(tab):
    return ({} if tab in TASK_PICKER_TABS else {"display": "none"}),\
        task_options()


# --------------------------------------------------------------------------
# callbacks
# --------------------------------------------------------------------------
@app.callback(Output("content", "children"),
              Input("tabs", "value"), Input("request", "value"))
def render_tab(tab, rid):
    req = REQUESTS[rid]
    if tab == "method":
        return method_tab()
    if tab == "input":
        return input_tab(req)
    if tab == "ours":
        return ours_tab(req)
    if tab == "results":
        return results_tab(rid)
    if tab == "competition":
        return competition_tab()
    if tab == "flow":
        return animation_tab(rid)
    return compare_tab(req)


def instruction_card(req, size="19px"):
    diff = req.get("difficulty", "?")
    return card([
        html.Div([
            pill(diff.upper(), DIFF_COLOR.get(diff, MUTED)),
            html.Span(req["_id"], style={"color": FAINT, "fontSize": "11.5px",
                                         "marginLeft": "12px",
                                         "fontFamily": MONO}),
        ], style={"marginBottom": "11px"}),
        eyebrow("INSTRUCTION · THE ONLY TEXT THE MODEL IS GIVEN"),
        html.Div(f"“{req.get('text','')}”",
                 style={"color": FG, "fontSize": size, "lineHeight": "1.5",
                        "fontWeight": "500"}),
    ])


def viewer_panel(children, **style):
    return html.Div(children, style={
        "background": PANEL, "borderRadius": "11px",
        "border": f"1px solid {BORDER}", "padding": "13px",
        "boxShadow": "0 1px 2px rgba(16,24,40,0.05)",
        "minWidth": "0", "overflow": "hidden", **style})


def input_tab(req):
    """The task itself: instruction, the part going in, the human's answer."""
    rid = req["_id"]
    start = req.get("brep_start")
    gt_id = EDITS_BY_REQUEST.get(rid, {}).get("GROUND TRUTH")

    return html.Div([
        instruction_card(req),
        grid([
            viewer_panel([
                graph(mesh_figure(geom_path(start, "stl"), MESH_INPUT,
                                  "INPUT — before the edit (drag to rotate)")),
                file_label(start),
            ]),
            viewer_panel([
                graph(mesh_figure(geom_path(gt_id, "stl"), MESH_GT,
                                  "GROUND TRUTH — the human expert's edit")),
                file_label(gt_id),
            ]),
        ], min_width="420px"),
        html.Div(style={"height": "14px"}),
        card([
            eyebrow("INPUT RENDERS"),
            html.Div([t for t in (img_tag(start, v) for v in VIEWS) if t],
                     style={"display": "flex", "flexWrap": "wrap",
                            "marginBottom": "16px"}),
            eyebrow("GROUND TRUTH RENDERS"),
            html.Div([t for t in (img_tag(gt_id, v) for v in VIEWS) if t]
                     or [note("no ground truth renders for this task",
                              color=BAD)],
                     style={"display": "flex", "flexWrap": "wrap"}),
        ]),
    ])


def compare_tab(req):
    rid = req["_id"]
    avail = EDITS_BY_REQUEST.get(rid, {})
    models = [k for k in avail if k != "GROUND TRUTH"]
    return html.Div([
        card([
            eyebrow("COMPARE THE HUMAN'S EDIT AGAINST", marginBottom="7px"),
            dcc.RadioItems(id="model",
                           options=[{"label": " " + method_label(m),
                                     "value": m} for m in models],
                           value=models[0] if models else None,
                           inline=True,
                           style={"color": FG, "fontSize": "13.5px",
                                  "fontWeight": "600"},
                           inputStyle={"marginRight": "6px",
                                       "marginLeft": "18px"},
                           labelStyle={"cursor": "pointer"}),
        ]),
        dcc.Loading(html.Div(id="compare-body"), type="dot", color=ACCENT),
    ])


@app.callback(Output("compare-body", "children"),
              Input("model", "value"), Input("request", "value"))
def render_compare(model, rid):
    if not model:
        return card(html.Div("no edits for this request", style={"color": MUTED}))

    req = REQUESTS[rid]
    avail = EDITS_BY_REQUEST.get(rid, {})
    gt_id, pred_id = avail.get("GROUND TRUTH"), avail.get(model)
    gt_stl, pred_stl = geom_path(gt_id, "stl"), geom_path(pred_id, "stl")

    # score lookup uses the benchmark's own user label
    user_key = model
    label = method_label(model)

    tiles = []
    for m in ("diff_f1", "chamfer_similarity_norm", "volume_f1"):
        v, failed = score_for(rid, user_key, m)
        tiles.append(kpi(
            METRIC_LABELS[m].upper(),
            f"{v:.3f}" if v is not None else "n/a",
            "failed edit, scores 0.0" if failed else
            ("the metric that measures editing" if m == "diff_f1" else ""),
            color=viz.score_color(v)))

    return html.Div([
        card([
            html.Div(f"“{req.get('text','')}”",
                     style={"color": FG, "fontSize": "16px",
                            "marginBottom": "14px", "fontStyle": "italic"}),
            html.Div(tiles, style={"display": "flex", "gap": "11px",
                                   "flexWrap": "wrap"}),
            note("Diff F1 compares this method's change to the human's change, "
                 "so anything correctly left alone cancels out. That is why a "
                 "method can hold a chamfer similarity near 1.0 and still "
                 "score zero: it barely touched the part.", marginTop="12px"),
        ]),
        grid([
            viewer_panel([
                graph(mesh_figure(gt_stl, MESH_GT,
                                  "GROUND TRUTH — the human expert's edit")),
                file_label(gt_id),
            ]),
            viewer_panel([
                graph(mesh_figure(pred_stl, MESH_PRED, f"{label.upper()}")),
                file_label(pred_id),
            ]),
        ], min_width="420px"),
        html.Div(style={"height": "14px"}),
        card([
            eyebrow("GROUND TRUTH RENDERS"),
            html.Div([t for t in (img_tag(gt_id, v) for v in VIEWS) if t],
                     style={"display": "flex", "flexWrap": "wrap",
                            "marginBottom": "16px"}),
            eyebrow(f"{label.upper()} RENDERS"),
            html.Div([t for t in (img_tag(pred_id, v) for v in VIEWS) if t]
                     or [note("none, this edit produced no geometry",
                              color=BAD)],
                     style={"display": "flex", "flexWrap": "wrap"}),
        ]),
    ])


# --------------------------------------------------------------------------
# tab 3 — our agent implementation (manual trigger)
# --------------------------------------------------------------------------
RUNS = {}          # request_id -> {"status","lines","scores","dest","error","stop"}
RUNS_LOCK = threading.Lock()


class StopRequested(Exception):
    """Raised inside the worker thread when the user clicks Stop."""


def _run_record_path(rid):
    return osp.join(our_config.RESULTS, "dashboard_runs", f"{rid}.json")


def _save_run_record(rid, rec):
    """Persist THE LATEST run of a task so the dashboard shows it across
    restarts and tab switches. One file per request, overwritten on every
    rerun — 'latest wins'. Carries everything ours_body renders: status,
    dest, plan, per-attempt steps with their GT scores, final scores, tokens,
    the log tail, and the color-coded input (base64, self-contained)."""
    try:
        snap = {k: v for k, v in rec.items() if k != "stop"}
        snap["lines"] = (snap.get("lines") or [])[-200:]
        snap["saved_at"] = datetime.datetime.now().astimezone().isoformat(
            timespec="seconds")
        os.makedirs(osp.dirname(_run_record_path(rid)), exist_ok=True)
        with open(_run_record_path(rid), "w") as f:
            json.dump(snap, f, default=str)
    except Exception as e:
        print(f"[dashboard] could not persist run record for {rid}: {e}")


def _load_saved_run(rid):
    """The persisted latest run for a task, or None."""
    try:
        with open(_run_record_path(rid)) as f:
            return json.load(f)
    except Exception:
        return None


def _attempt_gt_scores(rid, steps):
    """All three benchmark scores vs ground truth for EVERY attempt that left
    geometry behind — rejected and partial attempts included (their .step
    files are persisted by finalize). Mutates each step dict in place, adding
    `gt_scores`. STLs and score dirs are cached next to the attempt's .step,
    so repeated dashboard sessions reuse them. Attempts with no geometry (a
    crash) are skipped — there is nothing to score."""
    from src.tools import render as our_render
    for st in steps:
        sp = st.get("step")
        if not sp or not osp.exists(sp) or st.get("gt_scores"):
            continue
        d = osp.splitext(sp)[0] + "_gt"
        stl = osp.join(d, "tmp.stl")
        try:
            os.makedirs(d, exist_ok=True)
            if not osp.exists(stl):
                our_render.export_stl(sp, stl)
            if osp.exists(stl):
                st["gt_scores"] = our_eval.score_output(rid, d)
        except Exception as e:
            st["gt_scores"] = {"error": str(e)}


def _run_pipeline(rid, user_id):
    """Background worker. Never raises into the server thread."""
    # bind to this run's record by identity, so a stale thread that was
    # stopped keeps writing to (and reading its stop flag from) its own
    # record even after a fresh run replaces RUNS[rid]
    with RUNS_LOCK:
        rec = RUNS[rid]

    def on_event(state, msg):
        # keep a live handle on the session so the UI can show each attempt's
        # renders while the run is still going
        with RUNS_LOCK:
            # cooperative cancellation: the pipeline emits an event at every
            # step, so raising here aborts the run at the next step boundary
            if rec.get("stop"):
                raise StopRequested
            rec["lines"].append(msg)
            rec["steps"] = [dict(s) for s in state.steps]
            rec["plan_summary"] = state.plan_summary
            # The feature-colour-coded input the strategist plans from lives
            # only in the run's work dir, which pipeline.py deletes at the
            # end — inline it the moment it exists so the UI keeps it.
            if "input_tagged" not in rec:
                tagged = state.__dict__.get("input_views_tagged") or {}
                enc = {}
                for v, p in tagged.items():
                    if not osp.exists(p):
                        continue
                    mime = ("jpeg" if p.lower().endswith((".jpg", ".jpeg"))
                            else "png")
                    with open(p, "rb") as f:
                        enc[v] = (f"data:image/{mime};base64,"
                                  + base64.b64encode(f.read()).decode())
                if enc:
                    rec["input_tagged"] = enc

    try:
        state, dest, settings = our_pipeline.run_request(
            rid, user_id=user_id, on_event=on_event)
        steps_scored = [dict(s) for s in state.steps]
        with RUNS_LOCK:
            rec.update(status=state.status, dest=dest, steps=steps_scored,
                       tokens=settings.get("token_counts", {}),
                       subtasks=[{"goal": t.goal, "status": t.status,
                                  "attempts": t.attempts}
                                 for t in state.subtasks])
            rec["lines"].append("scoring against ground truth…")
        scores = our_eval.score_output(rid, dest)
        with RUNS_LOCK:
            rec["scores"] = scores
            rec["lines"].append("scoring every attempt against ground truth…")
        # Post-run analysis only — these scores are never fed back into the
        # pipeline's decisions, so the benchmark stays honest.
        _attempt_gt_scores(rid, steps_scored)
        with RUNS_LOCK:
            rec["lines"].append("done")
            rec["status"] = "finished"
            snap = dict(rec)
        _save_run_record(rid, snap)
    except StopRequested:
        with RUNS_LOCK:
            rec["status"] = "stopped"
            rec["lines"].append("stopped by user")
            snap = dict(rec)
        _save_run_record(rid, snap)
    except Exception as e:
        with RUNS_LOCK:
            rec["status"] = "error"
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["lines"].append(traceback.format_exc()[-1500:])
            snap = dict(rec)
        _save_run_record(rid, snap)


def ours_tab(req):
    rid = req["_id"]
    if not OURS_AVAILABLE:
        return card([
            html.Div("Our pipeline could not be imported.",
                     style={"color": BAD, "fontWeight": "600"}),
            html.Div(OURS_IMPORT_ERROR, style={"color": MUTED, "fontSize": "12px",
                                               "fontFamily": "monospace"}),
            html.Div("Run the dashboard with the project root on PYTHONPATH:",
                     style={"color": MUTED, "fontSize": "12px", "marginTop": "8px"}),
            html.Div("PYTHONPATH=$(pwd):$(pwd)/../.. uv run python ../../tools/dashboard.py",
                     style={"color": ACCENT, "fontFamily": "monospace", "fontSize": "12px"}),
        ])

    has_key = bool(our_config.API_KEY)
    with RUNS_LOCK:
        run = dict(RUNS.get(rid, {}))
    # No run in memory: fall back to the persisted LATEST run of this task,
    # so finished results survive dashboard restarts and tab switches. A
    # rerun simply overwrites the record when it finishes.
    if not run:
        saved = _load_saved_run(rid)
        if saved:
            with RUNS_LOCK:
                RUNS.setdefault(rid, saved)
            run = saved

    return html.Div([
        card([
            html.Div(f"“{req.get('text','')}”",
                     style={"color": FG, "fontSize": "16px", "fontStyle": "italic",
                            "marginBottom": "12px"}),
            html.Div([
                html.Button("▶  Run our agent pipeline", id="run-btn", n_clicks=0,
                            disabled=not has_key,
                            style={"background": ACCENT if has_key else MUTED,
                                   "color": "#fff", "border": "none",
                                   "padding": "11px 20px", "borderRadius": "7px",
                                   "fontSize": "14px", "fontWeight": "600",
                                   "cursor": "pointer" if has_key else "not-allowed"}),
                html.Button("■  Stop", id="stop-btn", n_clicks=0,
                            style={"background": BAD, "color": "#fff",
                                   "border": "none", "padding": "11px 20px",
                                   "borderRadius": "7px", "fontSize": "14px",
                                   "fontWeight": "600", "cursor": "pointer",
                                   "marginLeft": "10px"}),
                html.Span(id="run-hint",
                          children=("" if has_key else
                                    "  no OPENAI_API_KEY — add it to src/.env"),
                          style={"color": BAD if not has_key else MUTED,
                                 "fontSize": "12px", "marginLeft": "12px"}),
            ]),
            html.Div("Runs strategist → executor → QA loop, writes results/, then "
                     "scores against ground truth. Nothing runs until you click.",
                     style={"color": MUTED, "fontSize": "12px", "marginTop": "10px"}),
        ]),
        html.Div(id="ours-body", children=ours_body(rid, run)),
    ])


VERDICT_STYLE = {
    "accepted": (GOOD, "QA ACCEPTED"),
    "partial":  (WARN, "QA PARTIAL — kept and refined"),
    "rejected": (BAD,  "QA REJECTED"),
    "crashed":  (BAD,  "SCRIPT CRASHED"),
    "pending":  (MUTED, "awaiting QA…"),
}


@lru_cache(maxsize=2048)
def _b64_file(path, mtime):
    """Base64 of a file, cached by (path, mtime) — the poll re-renders the
    whole tab every few seconds, and re-reading + re-encoding a hundred step
    renders per tick was pure waste. `mtime` busts the cache when a file is
    rewritten in place."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def img_from_path(path, label, width="12.5%"):
    """Inline a render from an absolute path (Dash cannot serve local files)."""
    if not path or not osp.exists(path):
        return None
    b64 = _b64_file(path, osp.getmtime(path))
    mime = "jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "png"
    return html.Div([
        html.Img(src=f"data:image/{mime};base64,{b64}",
                 style={"width": "100%", "borderRadius": "4px",
                        "background": "#fff", "display": "block",
                        "border": f"1px solid {BORDER}"}),
        html.Div(label, style={"color": MUTED, "fontSize": "10px",
                               "textAlign": "center", "paddingTop": "2px"}),
    ], style={"width": width, "padding": "3px"})


def our_output_stl(dest):
    """The STL our finished run produced.

    `run_request` returns the INNERMOST output directory (the one holding
    tmp.stl directly); the glob is kept as a fallback for records that stored
    the outer <edit_id> directory. Globbing unconditionally appended
    `brep_end` a second time, found nothing, and the finished run's "OUR
    RESULT" viewer rendered empty.
    """
    if not dest:
        return None
    direct = osp.join(dest, "tmp.stl")
    if osp.exists(direct):
        return direct
    hits = glob.glob(osp.join(dest, "brep_end", "*", "*.stl"))
    return hits[0] if hits else None


def redacted_views(views):
    """Colour-coded change renders, redacted behind a click-to-reveal fold."""
    order = ["toprightiso"] + [v for v in VIEWS if v != "toprightiso"]
    thumbs = [t for t in (img_from_path(views.get(v), v)
                          for v in order if v in views) if t]
    if not thumbs:
        return None
    return html.Details([
        html.Summary("color-coded change renders (redacted — click to reveal)",
                     style={"color": ACCENT, "fontSize": "11px",
                            "cursor": "pointer", "userSelect": "none"}),
        html.Div("red = this attempt's new faces · other colors = earlier "
                 "accepted steps · gray = inherited from the input",
                 style={"color": MUTED, "fontSize": "10px",
                        "margin": "4px 0 2px 0"}),
        html.Div(thumbs, style={"display": "flex", "flexWrap": "wrap"}),
    ], style={"marginTop": "6px"})


def _listy(v):
    """Tolerate a field that is missing, a bare scalar, or a proper list."""
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple, set)) else [v]


def steps_gallery(steps, finished=False):
    """One row per executed attempt, with the six views the QA agent saw."""
    rows = []
    for st in steps:
        colour, label = VERDICT_STYLE.get(st.get("verdict", "pending"),
                                          (MUTED, st.get("verdict", "?")))
        header = [
            html.Span(f"sub-goal {st['sub']} · attempt {st['attempt']}",
                      style={"color": FG, "fontWeight": "600",
                             "fontSize": "13px"}),
            html.Span(f"   {label}", style={"color": colour, "fontSize": "12px",
                                            "fontWeight": "600"}),
        ]
        # Ground-truth scores for THIS attempt's geometry — all three metrics,
        # on every attempt that produced geometry, rejected and partial
        # included. Computed after the run finishes (_attempt_gt_scores);
        # display-only, never fed back into the pipeline.
        g = st.get("gt_scores") or {}
        if g.get("error"):
            header.append(html.Span("   GT scores unavailable",
                                    style={"color": MUTED, "fontSize": "11px"}))
        elif g:
            header.append(html.Span("   vs GT:",
                                    style={"color": MUTED, "fontSize": "11px"}))
            for m, short in (("chamfer_similarity_norm", "cham"),
                             ("volume_f1", "vol"), ("diff_f1", "diff")):
                v = g.get(m)
                if not isinstance(v, (int, float)):
                    continue
                c = GOOD if v >= 0.6 else WARN if v >= 0.25 else BAD
                header.append(html.Span(
                    f" {short} {v:.3f}",
                    style={"color": c, "fontSize": "11px", "fontWeight": "600"}))
        if st.get("ok"):
            header.append(html.Span(
                f"   {st.get('faces','?')} faces · vol {st.get('volume','?')} "
                f"({st.get('volume_change_pct','?')}%)"
                + (f" · new {st['new_surface_types']}"
                   if st.get("new_surface_types") else ""),
                style={"color": MUTED, "fontSize": "11px"}))

        # MBR consensus, when this sub-goal had tied checkpoints and the selector
        # ran. It is how much THIS candidate's edit agrees with its siblings —
        # no ground truth involved — so it is the number the pipeline actually
        # selects on. Deliberately printed next to the GT scores so the two can
        # be compared by eye: high consensus with a low GT score is the selector
        # being fooled by agreement, which is the failure worth spotting.
        cons = st.get("mbr_consensus")
        # `mbr_backfilled` marks a consensus computed AFTER the run, from archived
        # geometry. It is a counterfactual — what the selector would have chosen —
        # and the wording says so, because labelling it "selected" would present a
        # simulation as history.
        would = st.get("mbr_backfilled")
        if isinstance(cons, (int, float)):
            header.append(html.Span(
                f"   MBR {cons:.3f}",
                style={"color": ACCENT, "fontSize": "11px", "fontWeight": "600"}))
            if st.get("mbr_picked"):
                header.append(html.Span(
                    " ← would pick" if would else " ← selected",
                    style={"color": ACCENT, "fontSize": "11px",
                           "fontWeight": "600"}))
        elif st.get("mbr_reason"):
            # Abstention is a real outcome and worth showing: it is why the
            # selector changed nothing on this sub-goal.
            header.append(html.Span(
                f"   MBR abstained · {st['mbr_reason']}",
                style={"color": MUTED, "fontSize": "11px"}))

        body = [html.Div(header, style={"marginBottom": "6px"})]

        # Which playbooks the executor prompt actually carried. Deliberately
        # unfolded: the whole point is to scan down the cards and compare the
        # sections that were present against what QA then did with the result.
        tags = [str(t) for t in _listy(st.get("tags")) if str(t).strip()]
        recipes = [str(r) for r in _listy(st.get("recipes")) if str(r).strip()]
        if tags or recipes:
            meta = []
            if tags:
                meta.append(html.Span("tags: ", style={"color": MUTED}))
                meta.append(html.Span(", ".join(tags), style={"color": ACCENT}))
            if recipes:
                if meta:
                    meta.append(html.Span(" · ", style={"color": MUTED}))
                meta.append(html.Span("recipes ", style={"color": MUTED}))
                meta.append(html.Span(" ".join(f"§{r}" for r in recipes),
                                      style={"color": ACCENT}))
            body.append(html.Div(meta, style={
                "fontSize": "11px", "marginBottom": "6px",
                "lineHeight": "1.45"}))

        if st.get("goal"):
            body.append(html.Div(f"goal: {st['goal']}", style={
                "color": MUTED, "fontSize": "11px", "marginBottom": "6px",
                "lineHeight": "1.45"}))

        # The CadQuery the model actually wrote, folded away by default — it is
        # the only place the "why" of a verdict is fully visible.
        src = st.get("script")
        if not src and st.get("script_file") and osp.exists(st["script_file"]):
            with open(st["script_file"]) as f:
                src = f.read()
        if src:
            saved = [osp.basename(st[k]) for k in ("step", "script_file")
                     if st.get(k)]
            body.append(html.Details([
                html.Summary(
                    f"CadQuery source ({len(src.splitlines())} lines)"
                    + (f" · saved as {', '.join(saved)}" if saved else ""),
                    style={"color": ACCENT, "fontSize": "11px",
                           "cursor": "pointer", "userSelect": "none"}),
                code_block(src),
            ], style={"marginBottom": "6px"}))

        # Exactly what the executor was handed and exactly what it replied.
        # Folded away, because it is 5-20k characters — but it is the only
        # place that explains an attempt the script alone cannot account for.
        if st.get("approach"):
            body.append(html.Div(f"approach: {st['approach']}", style={
                "color": ACCENT, "fontSize": "11px", "marginBottom": "6px",
                "lineHeight": "1.45"}))
        io_file = st.get("prompt_file")
        if io_file and osp.exists(io_file):
            try:
                with open(io_file) as f:
                    io_text = f.read()
            except OSError as e:
                io_text = f"(could not read {io_file}: {e})"
            body.append(html.Details([
                html.Summary(
                    f"executor input / output (~{st.get('prompt_tokens','?')} "
                    f"prompt tokens) · {osp.basename(io_file)}",
                    style={"color": WARN, "fontSize": "11px",
                           "cursor": "pointer", "userSelect": "none"}),
                # prose and code mixed, so this one has to wrap
                code_block(io_text, whiteSpace="pre-wrap",
                           overflowWrap="anywhere"),
            ], style={"marginBottom": "6px"}))

        if st.get("error"):
            body.append(html.Div(st["error"], style={
                "color": BAD, "fontSize": "11px", "fontFamily": "monospace",
                "marginBottom": "4px"}))

        views = st.get("views") or {}
        # While the run is LIVE, only the two most recent attempts carry their
        # images; older rows keep all their text. The poll re-sends this whole
        # tab every few seconds, and inlining every attempt's renders on every
        # tick backed the socket up until macOS killed the write with
        # "[Errno 55] No buffer space available". On finish everything is shown.
        recent = st is steps[-1] or (len(steps) > 1 and st is steps[-2])
        if not finished and not recent:
            if views:
                body.append(html.Div(
                    "(views collapsed while the run is live — shown on finish)",
                    style={"color": MUTED, "fontSize": "11px"}))
            views = {}
        order = ["toprightiso"] + [v for v in VIEWS if v != "toprightiso"]
        thumbs = [t for t in (img_from_path(views.get(v), v)
                              for v in order if v in views) if t]
        if thumbs:
            body.append(html.Div(thumbs, style={"display": "flex",
                                                "flexWrap": "wrap"}))
        elif st.get("ok") and (finished or recent):
            body.append(html.Div("renders not available",
                                 style={"color": MUTED, "fontSize": "11px"}))

        # colour-coded change renders only appear once the run is over, and
        # even then redacted behind a fold so the gallery stays scannable
        if finished and st.get("views_changed"):
            fold = redacted_views(st["views_changed"])
            if fold is not None:
                body.append(fold)

        if st.get("observation"):
            body.append(html.Div(f"QA saw: {st['observation']}", style={
                "color": MUTED, "fontSize": "11px", "marginTop": "6px",
                "fontStyle": "italic"}))
        for i in st.get("issues", []):
            body.append(html.Div(f"• {i}", style={"color": colour,
                                                  "fontSize": "11px"}))

        rows.append(html.Div(body, style={
            "borderLeft": f"3px solid {colour}", "paddingLeft": "12px",
            "marginBottom": "16px"}))

    return card([
        html.Div("STEP BY STEP — every attempt, with the views QA judged",
                 style={"color": MUTED, "fontSize": "11px",
                        "letterSpacing": "1px", "marginBottom": "12px"}),
        *rows,
    ])


def _baseline_line(rid):
    """This request's Diff F1 for every published edit, tab 3's own numbers."""
    bits = []
    for user in METHODS:
        v, failed = score_for(rid, user, "diff_f1")
        if v is not None:
            bits.append(f"{method_label(user)} {v:.3f}"
                        + (" (failed)" if failed else ""))
    if not bits:
        return "No benchmark scores for this task."
    return "Diff F1 on this task, same numbers as tabs 3 and 5: " \
        + " · ".join(bits)


def _finished_pair(rid):
    """(our .stl, ground-truth .stl, ground-truth brep id) for a finished run."""
    kind, payload = latest_source(rid)
    if kind == "record":
        dest = payload.get("dest")
    elif kind == "sweep":
        dest = sweep_dest(payload)
    else:
        dest = None
    gt_id = EDITS_BY_REQUEST.get(rid, {}).get("GROUND TRUTH")
    return our_output_stl(dest), geom_path(gt_id, "stl"), gt_id


def stl_body(rid, view):
    """The two solids, either beside each other or overlaid as a diff."""
    ours_stl, gt_stl, gt_id = _finished_pair(rid)

    if view == "diff":
        fig, stats = deviation_figure(ours_stl, gt_stl)
        if stats:
            caption = note(
                f"Gray is surface the two parts agree on. Blue is surface only "
                f"our result has, amber is surface only the human's edit has. "
                f"A face counts as moved when it sits more than "
                f"{stats['tol']:.3g} mm from the other part, which is "
                f"{DIFF_TOL_FRAC * 100:.1f}% of the "
                f"{stats['diag']:.3g} mm bounding-box diagonal. On this task "
                f"{stats['ours_pct']:.1f}% of our surface and "
                f"{stats['gt_pct']:.1f}% of the human's is unmatched.",
                marginTop="8px")
        else:
            caption = note("Nothing to compare on this task.", marginTop="8px")
        return html.Div([
            viewer_panel([graph(fig)]),
            caption,
            note("This is a surface comparison, not a boolean: two solids that "
                 "differ only inside a sealed cavity would look identical "
                 "here. Diff F1 is the number that judges the edit.",
                 marginTop="4px", color=FAINT),
        ])

    return grid([
        viewer_panel([
            graph(mesh_figure(ours_stl, MESH_PRED, "OUR RESULT — final output")),
        ]),
        viewer_panel([
            graph(mesh_figure(gt_stl, MESH_GT, "GROUND TRUTH (human expert)")),
            file_label(gt_id),
        ]),
    ], min_width="420px")


@app.callback(Output("stl-body", "children"),
              Input("stl-view", "value"), State("request", "value"),
              prevent_initial_call=True)
def render_stl_view(view, rid):
    return stl_body(rid, view)


def ours_body(rid, run):
    if not run:
        return card(html.Div("No run yet for this task — press Run above to "
                             "produce one; it will be saved here.",
                             style={"color": MUTED, "fontSize": "13px"}))

    blocks = []
    status = run.get("status", "?")
    colour = {"finished": GOOD, "error": BAD, "stopped": MUTED}.get(status, WARN)
    blocks.append(card([
        html.Span("STATUS  ", style={"color": MUTED, "fontSize": "11px",
                                     "letterSpacing": "1px"}),
        html.Span(status.upper(), style={"color": colour, "fontWeight": "700"}),
        html.Span(f"   latest run: {run['saved_at']}" if run.get("saved_at")
                  else "", style={"color": MUTED, "fontSize": "11px"}),
        html.Span(f"   {run.get('dest','')}", style={"color": MUTED, "fontSize": "11px",
                                                     "fontFamily": "monospace"}),
    ]))

    # the colour-coded input comes BEFORE the plan: it is what the strategist
    # looked at to write the sub-goals in the first place
    tagged = run.get("input_tagged") or {}
    if tagged:
        order = ["toprightiso"] + [v for v in VIEWS if v != "toprightiso"]
        thumbs = [html.Div([
            html.Img(src=tagged[v],
                     style={"width": "100%", "borderRadius": "4px",
                            "background": "#fff", "display": "block",
                            "border": f"1px solid {BORDER}"}),
            html.Div(v, style={"color": MUTED, "fontSize": "10px",
                               "textAlign": "center", "paddingTop": "2px"}),
        ], style={"width": "12.5%", "padding": "3px"})
            for v in order if v in tagged]
        blocks.append(card([
            html.Div("INPUT — FEATURE-COLOR-CODED (what the strategist plans "
                     "from)", style={"color": MUTED, "fontSize": "11px",
                                     "letterSpacing": "1px",
                                     "marginBottom": "6px"}),
            html.Div("vivid color = one feature family; muted shades = other "
                     "faces, tinted only so touching faces stay separable",
                     style={"color": MUTED, "fontSize": "10px",
                            "marginBottom": "6px"}),
            html.Div(thumbs, style={"display": "flex", "flexWrap": "wrap"}),
        ]))

    if run.get("subtasks"):
        blocks.append(card([
            html.Div("PLAN — the instruction split into sub-goals",
                     style={"color": MUTED, "fontSize": "11px",
                            "letterSpacing": "1px", "marginBottom": "8px"}),
            html.Ol([
                html.Li([
                    html.Span(t["goal"], style={"color": FG}),
                    html.Span(f"  [{t['status']}, {t['attempts']} attempt(s)]",
                              style={"color": GOOD if t["status"] == "done" else BAD,
                                     "fontSize": "12px"}),
                ], style={"marginBottom": "5px"}) for t in run["subtasks"]
            ], style={"paddingLeft": "20px", "margin": 0}),
        ]))

    sc = run.get("scores")
    if sc:
        tiles = []
        for m in ("diff_f1", "chamfer_similarity_norm", "volume_f1"):
            v = sc.get(m)
            tiles.append(kpi(
                METRIC_LABELS[m].upper(),
                f"{v:.3f}" if isinstance(v, (int, float)) else "n/a",
                "the metric that measures editing" if m == "diff_f1" else "",
                color=viz.score_color(v)))
        tok = run.get("tokens", {})
        blocks.append(card([
            eyebrow("OUR SCORES ON THIS RUN"),
            html.Div(tiles, style={"display": "flex", "gap": "11px",
                                   "flexWrap": "wrap"}),
            html.Div(f"{tok.get('total_tokens','?')} tokens · "
                     f"{tok.get('llm_calls','?')} LLM calls · "
                     f"est. ${tok.get('cost_estimate','?')}",
                     style={"color": MUTED, "fontSize": "12px", "marginTop": "10px"}),
            # THIS task's baselines, from the same precomputed all_results.json
            # tab 3 reads — a hardcoded benchmark-wide average sat here before
            # and never matched tab 3's per-task numbers.
            html.Div(_baseline_line(rid),
                     style={"color": MUTED, "fontSize": "12px"}),
        ]))

    # once the run is done, put our result next to the human's ground truth
    if status == "finished":
        gt_id = EDITS_BY_REQUEST.get(rid, {}).get("GROUND TRUTH")
        blocks.append(card([
            html.Div([
                eyebrow("THE TWO SOLIDS", marginBottom="0"),
                dcc.RadioItems(
                    id="stl-view",
                    options=[{"label": " Side by side", "value": "pair"},
                             {"label": " Diff (overlay and highlight what "
                                       "changed)", "value": "diff"}],
                    value="pair", inline=True,
                    style={"color": FG, "fontSize": "13.5px",
                           "fontWeight": "600", "marginLeft": "auto"},
                    inputStyle={"marginRight": "6px", "marginLeft": "18px"},
                    labelStyle={"cursor": "pointer"}),
            ], style={"display": "flex", "alignItems": "center",
                      "flexWrap": "wrap", "gap": "10px",
                      "marginBottom": "10px"}),
            dcc.Loading(html.Div(id="stl-body", children=stl_body(rid, "pair")),
                        type="dot", color=ACCENT),
        ]))
        gt_thumbs = [t for t in (img_tag(gt_id, v) for v in VIEWS) if t]
        if gt_thumbs:
            blocks.append(card([
                html.Div("GROUND TRUTH RENDERS",
                         style={"color": MUTED, "fontSize": "11px",
                                "letterSpacing": "1px", "marginBottom": "6px"}),
                html.Div(gt_thumbs, style={"display": "flex",
                                           "flexWrap": "wrap"}),
            ]))

    # Did this run ship the best geometry it built? Written by
    # tools/selection_audit.py from the per-attempt ground-truth scores. Placed
    # directly above the attempt gallery because the answer is only actionable
    # next to the attempts it is talking about.
    aud = run.get("selection_audit")
    if isinstance(aud, dict) and aud.get("cause") not in (None, "shipped-the-best"):
        why = {
            "rejected-was-better":
                "a REJECTED attempt scored higher than what shipped — the judge "
                "discarded the best thing on the table",
            "degraded-by-later-subgoal":
                "an earlier sub-goal's ACCEPTED state scored higher than the final "
                "result — a later sub-goal made the part worse",
        }.get(aud["cause"], aud["cause"])
        blocks.append(card([
            html.Div("SELECTION AUDIT",
                     style={"color": MUTED, "fontSize": "11px",
                            "letterSpacing": "1px", "marginBottom": "6px"}),
            html.Div([
                html.Span(f"shipped {aud['shipped']:.4f}",
                          style={"fontWeight": "600"}),
                html.Span("  →  ", style={"color": MUTED}),
                html.Span(f"best built {aud['best']:.4f}",
                          style={"color": GOOD, "fontWeight": "600"}),
                html.Span(f"   (sub {aud.get('best_sub')} · attempt "
                          f"{aud.get('best_attempt')} · "
                          f"{aud.get('best_verdict')})",
                          style={"color": MUTED, "fontSize": "12px"}),
                html.Span(f"   +{aud['gap']:.4f} left on the table",
                          style={"color": BAD, "fontWeight": "600",
                                 "marginLeft": "8px"}),
            ], style={"fontSize": "13px", "marginBottom": "4px"}),
            html.Div(why, style={"color": MUTED, "fontSize": "12px"}),
        ], border=f"1px solid {BAD}"))
    elif isinstance(aud, dict) and aud.get("cause") == "shipped-the-best":
        blocks.append(card([
            html.Div("SELECTION AUDIT", style={"color": MUTED, "fontSize": "11px",
                                               "letterSpacing": "1px"}),
            html.Div(f"shipped {aud['shipped']:.4f} — the best geometry this run "
                     f"built. Nothing better was discarded.",
                     style={"color": GOOD, "fontSize": "13px"}),
        ]))

    if run.get("steps"):
        blocks.append(steps_gallery(run["steps"], finished=status == "finished"))

    lines = run.get("lines", [])
    blocks.append(card([
        html.Div("LIVE LOG", style={"color": MUTED, "fontSize": "11px",
                                    "letterSpacing": "1px", "marginBottom": "6px"}),
        code_block("\n".join(lines[-200:]) or "…",
                   whiteSpace="pre-wrap", overflowWrap="anywhere",
                   background=TILE, borderRadius="6px",
                   maxHeight="340px", margin="0"),
    ]))
    if run.get("error"):
        blocks.append(card(html.Div(run["error"], style={"color": BAD,
                                                         "fontFamily": "monospace",
                                                         "fontSize": "12px"})))
    return blocks


@app.callback(Output("poll", "disabled"), Output("ours-body", "children",
                                                 allow_duplicate=True),
              Input("run-btn", "n_clicks"), State("request", "value"),
              prevent_initial_call=True)
def start_run(n, rid):
    if not n:
        return no_update, no_update
    with RUNS_LOCK:
        if RUNS.get(rid, {}).get("status") == "running":
            return False, no_update
        RUNS[rid] = {"status": "running", "lines": ["starting…"], "scores": None}
    user_id = osp.basename(getattr(our_pipeline, "USER_ID", "ours_adk-router"))
    threading.Thread(target=_run_pipeline, args=(rid, user_id), daemon=True).start()
    with RUNS_LOCK:
        return False, ours_body(rid, dict(RUNS[rid]))


@app.callback(Output("run-hint", "children"),
              Input("stop-btn", "n_clicks"), State("request", "value"),
              prevent_initial_call=True)
def stop_run(n, rid):
    if not n:
        return no_update
    with RUNS_LOCK:
        run = RUNS.get(rid)
        if not run or run.get("status") != "running":
            return "  nothing running for this task"
        run["stop"] = True
        run["lines"].append("stop requested — aborting at the next step…")
    return "  stopping — takes effect at the next pipeline step"


@app.callback(Output("ours-body", "children"), Output("poll", "disabled",
                                                      allow_duplicate=True),
              Input("poll", "n_intervals"), State("request", "value"),
              prevent_initial_call=True)
def poll_run(_n, rid):
    with RUNS_LOCK:
        run = dict(RUNS.get(rid, {}))
    if not run:
        return no_update, True
    stop = run.get("status") in ("finished", "error", "stopped")
    return ours_body(rid, run), stop


# --------------------------------------------------------------------------
# tab 1 — method overview (the presentation tab)
# --------------------------------------------------------------------------
DOCS = osp.join(REPO_ROOT, "docs")
NODES_JSON = osp.join(DOCS, "method_nodes.json")

NODE_KIND = {
    "source": (viz.INK_3, "input"),
    "tool":   (viz.S3, "deterministic tool"),
    "agent":  (viz.S1, "LLM agent"),
    "gate":   (viz.S2, "gate / check"),
    "sink":   (viz.S7, "output"),
}

# Used when docs/method_nodes.json has not been generated yet, so the tab is
# never blank in front of an audience.
FALLBACK_NODES = {
    "nodes": [
        {"id": "db", "label": "Benchmark task", "kind": "source", "row": 1,
         "summary": "One STEP part and one sentence of instruction.",
         "inputs": ["request id"],
         "outputs": ["input .step", "instruction text"],
         "detail": "The dataset is a mongita database of parts, edit requests "
                   "and the edits humans made from them.",
         "file": "src/pipeline.py"},
        {"id": "inspect", "label": "Geometry index", "kind": "tool", "row": 2,
         "summary": "Measures the B-rep so the model reads numbers, not pixels.",
         "inputs": ["input .step"],
         "outputs": ["face families", "hole axes", "bounding box"],
         "detail": "An opaque solid of a few hundred faces becomes a short "
                   "text index of the features an instruction can refer to.",
         "file": "src/tools/geometry.py"},
        {"id": "strategist", "label": "Strategist", "kind": "agent", "row": 3,
         "summary": "Splits one sentence into ordered sub-goals.",
         "inputs": ["instruction", "geometry index", "renders"],
         "outputs": ["sub-goals", "tags", "envelope"],
         "detail": "Each sub-goal names the feature it acts on and the change "
                   "it expects, so the next agent has something checkable.",
         "file": "src/agents/strategist.py"},
        {"id": "executor", "label": "Executor", "kind": "agent", "row": 4,
         "summary": "Writes the CadQuery function for one sub-goal.",
         "inputs": ["sub-goal", "geometry index", "views", "feedback"],
         "outputs": ["python source"],
         "detail": "Up to three judged attempts per sub-goal. Feedback from a "
                   "rejected attempt arrives as text, never as its geometry.",
         "file": "src/agents/executor.py"},
        {"id": "gates", "label": "Gates", "kind": "gate", "row": 5,
         "summary": "Deterministic checks before any model judges the result.",
         "inputs": ["produced .step"],
         "outputs": ["measured diff", "gate verdicts"],
         "detail": "Crash, no-op, phantom geometry, frame drift and envelope "
                   "violations are caught by measurement, not by opinion.",
         "file": "src/tools/geometry.py"},
        {"id": "qa", "label": "QA", "kind": "agent", "row": 6,
         "summary": "Looks at seven rendered views and accepts or rejects.",
         "inputs": ["7 views", "measured diff", "sub-goal"],
         "outputs": ["accept / partial / reject", "issues"],
         "detail": "The iso view leads: on parts whose features sit in 3D the "
                   "orthographic views flatten the arrangement and QA "
                   "miscounted features it could see clearly in the iso.",
         "file": "src/agents/qa.py"},
        {"id": "score", "label": "Score", "kind": "sink", "row": 7,
         "summary": "Chamfer similarity, volume F1 and diff F1.",
         "inputs": ["our .stl", "ground truth .stl"],
         "outputs": ["three metrics"],
         "detail": "Run through the benchmark's own metric code, called the "
                   "same way its own evaluation calls it.",
         "file": "src/evaluate.py"},
    ],
    "edges": [
        {"from": "db", "to": "inspect", "label": "input .step", "kind": "forward"},
        {"from": "inspect", "to": "strategist", "label": "index", "kind": "forward"},
        {"from": "strategist", "to": "executor", "label": "sub-goal", "kind": "forward"},
        {"from": "executor", "to": "gates", "label": "produced .step", "kind": "forward"},
        {"from": "gates", "to": "qa", "label": "measured diff", "kind": "forward"},
        {"from": "qa", "to": "executor", "label": "rejection feedback", "kind": "feedback"},
        {"from": "qa", "to": "score", "label": "accepted geometry", "kind": "forward"},
    ],
}


@lru_cache(maxsize=8)
def _read_text(path, mtime):
    with open(path) as f:
        return f.read()


def method_graph():
    """The node/edge description the diagram is drawn from."""
    if osp.exists(NODES_JSON):
        try:
            return json.loads(_read_text(NODES_JSON, osp.getmtime(NODES_JSON)))
        except Exception as e:
            print(f"[dashboard] {NODES_JSON} is not readable: {e}")
    return FALLBACK_NODES




# --------------------------------------------------------------------------
# the schematic
#
# The picture is not drawn here. `tools/blockdiagram.py` already renders the
# signal-flow schematic that ARCHITECTURE.md ships, with named ports on every
# block edge, a label on every wire, and feedback routed cleanly around the
# back in amber. Reproducing that in Dash divs meant a second, worse drawing
# that would drift from the document. So the generated SVG is what is shown,
# and the only thing added on top is a transparent hotspot per block, in the
# SVG's own pixel coordinates, so a block can still be clicked for its full
# input and output contract.
# --------------------------------------------------------------------------
SCHEMATIC_TITLE = "One edit request, end to end"
SCHEMATIC_SUB = ("port names are the signals on the wires · rows run left to "
                 "right, top to bottom · amber wires retry the attempt")

# Schematic block id -> the id of the same thing in docs/method_nodes.json.
# The two files name a few blocks differently because the schematic draws
# `geometry` twice, once as the index and once as the diff.
SCHEMATIC_TO_NODE = {
    "dataset": "dataset", "geo_index": "inspect", "views_in": "render_in",
    "strategist": "strategist", "router": "router", "skillref": "skillref",
    "executor": "executor", "lint": "lint", "runner": "runner",
    "geo_diff": "compare", "views_out": "render_out", "qa": "qa",
    "finalize": "finalize", "evaluate": "evaluate",
}
# Blocks the schematic draws that the node file has no entry for. They are
# artifacts rather than steps, so they were never worth a node of their own.
SCHEMATIC_EXTRA = {
    "submission": {
        "summary": "The folder the benchmark ingests, in the layout it expects.",
        "inputs": ["the last accepted .step"],
        "outputs": ["tmp.step", "tmp.stl", "7 jpgs", "settings.json"],
        "detail": "Written by router.finalize. The .stl is the only file the "
                  "metrics read; the rest is there so a run can be inspected "
                  "afterwards.",
    },
    "scores": {
        "summary": "Chamfer similarity, volume F1 and diff F1.",
        "inputs": ["our .stl", "the human expert's .stl"],
        "outputs": ["three numbers per task"],
        "detail": "Computed by the benchmark's own metric code, called the "
                  "same way its own evaluation calls it, so the numbers sit "
                  "beside the published baselines without adjustment.",
    },
}


# A narrower build of the same schematic, for the animation tab, where the
# drawing shares the width with the step panel beside it. Only spacing changes:
# the columns sit closer together, the padding is trimmed and the boxes are
# capped narrower, which makes a few of them stack their ports instead of
# putting inputs and outputs side by side. Same blocks, same wires, same
# routing code, so the two drawings cannot disagree about the pipeline.
# PAD_B stays generous while the rest is trimmed. The generator puts the
# legend at a fixed offset above the bottom edge, and the narrower canvas wraps
# it onto two lines rather than one, so a bottom padding cut to match the sides
# ran the legend straight through the last row's file captions. 96 leaves 28 px
# of clearance; 56 left minus 12, which is what the collision looked like.
COMPACT_LAYOUT = dict(COL_GAP=58, MAX_BOX_W=190, MIN_BOX_W=118,
                      PAD_L=34, PAD_R=20, PAD_T=54, PAD_B=96,
                      LANE_EDGE=42, LANE_PITCH=11)


@lru_cache(maxsize=2)
def _schematic_cached(mtime, compact=False):
    """(svg data URI, block rectangles, pixel size) for the generated diagram.

    `_svg_layout` is private to the generator. It is used here rather than
    re-deriving the geometry because the hotspots have to land on the boxes
    exactly; if the generator's internals change, the import fails and the tab
    falls back to the plain block list.
    """
    del mtime
    try:
        from tools import blockdiagram as bd
    except Exception as e:                     # pragma: no cover
        print(f"[dashboard] no schematic: {type(e).__name__}: {e}")
        return None
    # The generator reads its spacing from module constants. Swapping them for
    # the compact build and putting them back in a finally is the whole change:
    # a second hand-maintained layout would be one more thing to keep in step
    # with the document, and this way the routing code is shared.
    saved = {k: getattr(bd, k) for k in COMPACT_LAYOUT} if compact else {}
    # The compact build also drops the terminal metrics block. On the animation
    # tab the replay ends by putting the three scores in the panel beside the
    # drawing, so a block on the diagram saying the same thing is a second copy
    # of the answer, and the space it frees goes to the blocks that do work.
    # Its wire goes with it, or the layout would refer to a block that is gone.
    blocks, signals = list(bd.SBLOCKS), list(bd.SIGNALS)
    if compact:
        gone = {"scores"}
        blocks = [b for b in blocks if b.id not in gone]
        signals = [w for w in signals
                   if w.src not in gone and w.dst not in gone]
    try:
        for k, v in (COMPACT_LAYOUT if compact else {}).items():
            setattr(bd, k, v)
        layout = bd._svg_layout(blocks, signals)
        svg = bd.build_svg(blocks, signals,
                           SCHEMATIC_TITLE, SCHEMATIC_SUB)
    except Exception as e:                     # pragma: no cover
        print(f"[dashboard] could not build the schematic: "
              f"{type(e).__name__}: {e}")
        return None
    finally:
        for k, v in saved.items():
            setattr(bd, k, v)
    m = re.search(r'height="([\d.]+)"', svg)
    height = float(m.group(1)) if m else layout["h"]
    uri = ("data:image/svg+xml;base64,"
           + base64.b64encode(svg.encode()).decode())
    return {
        "uri": uri,
        "place": {k: dict(v) for k, v in layout["place"].items()},
        "w": float(layout["w"]), "h": height,
        "blocks": [(b.id, b.name, b.kind, b.sub) for b in blocks],
        "kinds": {k: (v[0], v[1], v[2]) for k, v in bd.SKINDS.items()},
        "signals": [(w.src, w.dst, w.signal, w.kind) for w in signals],
        "mermaid": bd.block_diagram(bd.BLOCKS),
    }


def schematic(compact=False):
    try:
        mt = int(osp.getmtime(osp.join(REPO_ROOT, "tools", "blockdiagram.py")))
    except OSError:
        mt = 0
    return _schematic_cached(mt, compact)


def hotspot_style(kind_stroke, selected=False):
    """A transparent box over one drawn block. Invisible until pointed at."""
    return {
        "position": "absolute", "cursor": "pointer", "borderRadius": "4px",
        "border": f"2px solid {kind_stroke}" if selected else "2px solid "
                                                              "transparent",
        "background": "rgba(42,120,214,0.10)" if selected else "transparent",
        "boxShadow": ("0 0 0 3px rgba(42,120,214,0.16)" if selected else None),
        "transition": "background .12s ease, box-shadow .12s ease",
    }


def schematic_detail_node(sid):
    """Everything the panel shows for one drawn block, from both files."""
    s = schematic()
    if not s:
        return None
    meta = {b[0]: b for b in s["blocks"]}.get(sid)
    if not meta:
        return None
    _id, name, kind, sub = meta
    graph = {n["id"]: n for n in method_graph()["nodes"]}
    node = graph.get(SCHEMATIC_TO_NODE.get(sid, ""), {})
    extra = SCHEMATIC_EXTRA.get(sid, {})
    return {
        "id": sid, "label": name, "kind": kind, "file": node.get("file") or sub,
        "summary": node.get("summary") or extra.get("summary", ""),
        "inputs": node.get("inputs") or extra.get("inputs") or [],
        "outputs": node.get("outputs") or extra.get("outputs") or [],
        "detail": node.get("detail") or extra.get("detail", ""),
    }


def node_detail(node_id):
    s = schematic()
    n = schematic_detail_node(node_id) if s else None
    if n is None:
        return html.Div([
            eyebrow("PICK A BLOCK", marginBottom="5px"),
            note("Click any block in the schematic above to see exactly what "
                 "it is handed, what it produces, which wires run in and out "
                 "of it, and where it lives in the code. The colour key is "
                 "along the bottom of the drawing."),
        ])

    stroke = (s["kinds"].get(n["kind"]) or (viz.INK_3, viz.INK_3, "block"))[1]
    kind_name = (s["kinds"].get(n["kind"]) or ("", "", "block"))[2]
    names = {b[0]: b[1] for b in s["blocks"]}
    ins = [w for w in s["signals"] if w[1] == node_id]
    outs = [w for w in s["signals"] if w[0] == node_id]

    def wires(edges, other_at, heading):
        if not edges:
            return []
        rows = []
        for src, dst, signal, kind in edges:
            other = names.get(src if other_at == 0 else dst,
                              src if other_at == 0 else dst)
            back = kind == "back"
            rows.append(html.Div([
                html.Span("↺ " if back else "→ ",
                          style={"color": viz.S2 if back else FAINT}),
                html.Span(signal, style={"color": FG}),
                html.Span(f"  ({other})", style={"color": FAINT}),
            ], style={"fontSize": "11.5px", "lineHeight": "1.7"}))
        return [eyebrow(heading, marginTop="0", marginBottom="5px"), *rows]

    def bullets(items, heading):
        if not items:
            return []
        return [eyebrow(heading, marginTop="0", marginBottom="5px"),
                *[html.Div("· " + str(i), style={
                    "color": FG, "fontSize": "11.5px", "lineHeight": "1.7"})
                  for i in items]]

    columns = [
        html.Div(bullets(n["inputs"], "TAKES") + bullets(n["outputs"],
                                                         "PRODUCES"),
                 style={"flex": "1 1 220px", "minWidth": "0"}),
        html.Div(wires(ins, 0, "WIRED FROM") + [html.Div(style={"height": "12px"})]
                 + wires(outs, 1, "WIRED INTO"),
                 style={"flex": "1 1 300px", "minWidth": "0"}),
        html.Div(([eyebrow("HOW IT WORKS", marginTop="0", marginBottom="5px"),
                   note(n["detail"])] if n["detail"] else [])
                 + ([html.Div(n["file"], style={
                     "color": ACCENT, "fontFamily": MONO, "fontSize": "11px",
                     "marginTop": "12px", "wordBreak": "break-all"})]
                    if n["file"] else []),
                 style={"flex": "1 1 320px", "minWidth": "0"}),
    ]

    return html.Div([
        html.Div([
            pill(kind_name.upper(), stroke),
            html.H3(n["label"], style={"margin": "0 0 0 12px", "color": FG,
                                       "fontSize": "18px"}),
        ], style={"display": "flex", "alignItems": "center",
                  "flexWrap": "wrap", "gap": "4px"}),
        note(n["summary"], marginTop="6px", marginBottom="16px"),
        html.Div(columns, style={"display": "flex", "gap": "28px",
                                 "flexWrap": "wrap"}),
    ])


def mermaid_source():
    """The module graph as mermaid, straight out of the generator.

    Same text ARCHITECTURE.md section 4 carries, so a slide pasted from here
    and the document cannot disagree.
    """
    s = schematic()
    if s and s.get("mermaid"):
        return s["mermaid"].replace("```mermaid\n", "").replace("\n```", "")
    g = method_graph()
    lines = ["flowchart TD"]
    for n in g["nodes"]:
        lines.append(f'    {n["id"]}["{n.get("label", n["id"])}"]')
    for e in g.get("edges", []):
        arrow = "-.->" if e.get("kind") == "feedback" else "-->"
        lab = e.get("label", "")
        lines.append(f'    {e["from"]} {arrow}'
                     + (f'|{lab}|' if lab else "") + f' {e["to"]}')
    return "\n".join(lines)


def best_model_baseline(rids):
    """(label, mean diff F1) for the strongest published MODEL on `rids`.

    The second human is held out of this. A human redoing the same edit is the
    agreement ceiling for the metric, not a method we are competing with, and
    folding it in would hide which model is actually the one to beat.
    """
    best = (None, None)
    for user in METHODS:
        if user == "other human":
            continue
        vals = [score_for(r, user, "diff_f1")[0] for r in rids]
        vals = [v for v in vals if v is not None]
        if len(vals) < max(1, len(rids) // 2):
            continue
        m = statistics.fmean(vals)
        if best[1] is None or m > best[1]:
            best = (method_label(user), m)
    return best


def human_ceiling(rids):
    vals = [score_for(r, "other human", "diff_f1")[0] for r in rids]
    vals = [v for v in vals if v is not None]
    return statistics.fmean(vals) if vals else None


def headline_numbers():
    """The numbers a judge should leave the room with."""
    rids = our_task_ids()
    ours = [v for v in (our_score(r, "diff_f1") for r in rids) if v is not None]
    costs = [c for c in (our_cost(r) for r in rids) if c is not None]
    best_label, best_mean = best_model_baseline(rids)
    ceiling = human_ceiling(rids)

    tiles = [
        kpi("TASKS SCORED", f"{len(rids)}",
            f"of {len(REQUESTS)} in the benchmark"),
        kpi("OUR MEAN DIFF F1",
            f"{statistics.fmean(ours):.3f}" if ours else "n/a",
            "same tasks for every method", color=ACCENT),
    ]
    if best_mean is not None:
        tiles.append(kpi("BEST BENCHMARK MODEL", f"{best_mean:.3f}",
                         f"{best_label}, published score"))
    if ceiling is not None:
        tiles.append(kpi("SECOND HUMAN", f"{ceiling:.3f}",
                         "two experts agreeing, the ceiling"))
    if ours:
        tiles.append(kpi("SOLVED OUTRIGHT",
                         f"{sum(1 for v in ours if v >= 0.6)}",
                         "diff F1 at or above 0.60"))
    if costs:
        tiles.append(kpi("SPEND PER TASK", f"${statistics.fmean(costs):.2f}",
                         f"${sum(costs):.2f} in total"))
    return tiles


def schematic_figure():
    """The generated SVG with one transparent, clickable hotspot per block."""
    s = schematic()
    if not s:
        return note("The schematic could not be generated. Run "
                    "`python3 tools/blockdiagram.py` and reload.", color=BAD)

    kinds = s["kinds"]
    hotspots = []
    for bid, name, kind, _sub in s["blocks"]:
        p = s["place"].get(bid)
        if not p:
            continue
        stroke = (kinds.get(kind) or ("", viz.INK_3, ""))[1]
        node = schematic_detail_node(bid) or {}
        tip = (f"{name}\n\nin:  "
               + ", ".join(node.get("inputs") or ["-"])
               + "\nout: " + ", ".join(node.get("outputs") or ["-"])
               + "\n\nclick for the full contract")
        hotspots.append(html.Div(
            id={"type": "mnode", "id": bid}, n_clicks=0, title=tip,
            style={**hotspot_style(stroke),
                   "left": f"{p['x'] - 3:.1f}px", "top": f"{p['y'] - 3:.1f}px",
                   "width": f"{p['w'] + 6:.1f}px",
                   # the caption under each box carries its name, so the
                   # clickable area has to reach past the box itself
                   "height": f"{p['h'] + 32:.1f}px"}))

    return html.Div(
        html.Div([
            html.Img(src=s["uri"], alt="signal-flow schematic of the pipeline",
                     style={"display": "block", "width": f"{s['w']:.0f}px",
                            "height": f"{s['h']:.0f}px",
                            "pointerEvents": "none"}),
            *hotspots,
        ], style={"position": "relative", "width": f"{s['w']:.0f}px",
                  "height": f"{s['h']:.0f}px", "margin": "0 auto"}),
        style={"overflowX": "auto", "paddingBottom": "4px"})


def slide(n, title, sub, *body):
    """One card of the deck: a number, a short title, and a figure.

    The tab used to carry the write-up as well. It was accurate and nobody
    reads a thousand words off a projector, so the prose moved out and the
    figures stayed: this tab is now drawings, shapes and numbers, and
    docs/method_overview.md remains the written version for anyone who wants
    the argument in sentences.
    """
    return card([
        html.Div([
            html.Span(f"{n:02d}", style={
                "color": "#fff", "background": ACCENT, "borderRadius": "6px",
                "padding": "2px 8px", "fontSize": "12px", "fontWeight": "700",
                "letterSpacing": "0.5px", "fontVariantNumeric": "tabular-nums"}),
            html.Span(title, style={"color": FG, "fontSize": "19px",
                                    "fontWeight": "700",
                                    "letterSpacing": "-0.2px"}),
            html.Span(sub, style={"color": MUTED, "fontSize": "12.5px",
                                  "marginLeft": "auto", "textAlign": "right"}),
        ], style={"display": "flex", "alignItems": "baseline", "gap": "11px",
                  "flexWrap": "wrap", "marginBottom": "14px"}),
        *body,
    ], padding="18px 20px")


def chip_row(items, colour):
    """A row of labelled shapes: name in bold, one short line under it."""
    return html.Div([
        html.Div([
            html.Div(name, style={"color": FG, "fontWeight": "700",
                                  "fontSize": "12.5px",
                                  "fontFamily": MONO if mono else viz.FONT}),
            html.Div(what, style={"color": MUTED, "fontSize": "11.5px",
                                  "lineHeight": "1.45", "marginTop": "4px"}),
        ], style={"flex": "1 1 210px", "minWidth": "0", "padding": "11px 13px",
                  "background": PANEL, "borderRadius": "9px",
                  "borderLeft": f"3px solid {colour}",
                  "border": f"1px solid {BORDER}",
                  "borderLeftWidth": "3px", "borderLeftColor": colour})
        for name, what, mono in items
    ], style={"display": "flex", "gap": "10px", "flexWrap": "wrap"})


# The gates, in the order an attempt meets them. Kept to one clause each: the
# slide is meant to be read at a glance, and every one of these is a rejection
# that costs nothing because no model was asked.
FLOW_GATES = [
    ("regex check", "an API form that cannot work in this CadQuery build"),
    ("no-op", "output identical to the input, which scores zero"),
    ("phantom material", "summed volume rose but occupied volume did not"),
    ("direction", "a cut sub-goal that added material, or the reverse"),
    ("frame drift", "the part translated, rescaled or re-centred"),
    ("envelope", "a bounding box face moved that the sub-goal never claimed"),
]

FLOW_TOOLS = [
    ("Part inspector", "turns the opaque solid into a measured text index"),
    ("Camera rig", "seven fixed cameras, rendered in a child process"),
    ("Skill provider", "verified playbooks, inside a token budget"),
    ("Regex check", "a blocklist of API forms that cannot work in this build, "
                    "plus an introspection check against the installed one"),
    ("Sandbox runner", "runs the generated function in an isolated subprocess"),
    ("Benchmark scorer", "the benchmark's own metric code, called its own way"),
]

# name, config attribute, unit
FLOW_BUDGETS = [
    ("Sub-goals", "MAX_SUBTASKS", ""),
    ("Attempts per sub-goal", "MAX_ATTEMPTS_PER_SUBTASK", ""),
    ("Barren retries", "MAX_BARREN_RETRIES", ""),
    ("Replans", "MAX_REPLANS", ""),
    ("Script timeout", "SCRIPT_TIMEOUT_S", "s"),
    ("One model call", "LLM_TIMEOUT_S", "s"),
    ("Recipe budget", "RECIPES_MAX_TOKENS", "tok"),
]


def budget_tiles():
    tiles = []
    for label, attr, unit in FLOW_BUDGETS:
        v = getattr(our_config, attr, None) if OURS_AVAILABLE else None
        txt = "n/a" if v is None else (f"{v:g}{unit}" if unit else f"{v:g}")
        tiles.append(kpi(label.upper(), txt, attr.lower().replace("_", " "),
                         width="130px"))
    return html.Div(tiles, style={"display": "flex", "gap": "10px",
                                  "flexWrap": "wrap"})


def kinds_row():
    """The drawing's own vocabulary: what each shape on the schematic means."""
    s = schematic()
    if not s:
        return note("no schematic")
    out = []
    for fill, stroke, text in s["kinds"].values():
        out.append(html.Div([
            html.Div(style={"width": "38px", "height": "26px",
                            "borderRadius": "4px", "background": fill,
                            "border": f"1.3px solid {stroke}",
                            "flex": "0 0 auto"}),
            html.Span(text, style={"color": FG, "fontSize": "12px",
                                   "lineHeight": "1.4"}),
        ], style={"display": "flex", "alignItems": "center", "gap": "10px",
                  "flex": "1 1 240px", "minWidth": "0"}))
    out.append(html.Div([
        html.Div(style={"width": "38px", "height": "0",
                        "borderTop": f"2px solid {viz.S2}", "flex": "0 0 auto",
                        "marginTop": "13px"}),
        html.Span("feedback: the attempt goes back to be retried",
                  style={"color": FG, "fontSize": "12px"}),
    ], style={"display": "flex", "alignItems": "flex-start", "gap": "10px",
              "flex": "1 1 240px", "minWidth": "0"}))
    return html.Div(out, style={"display": "flex", "gap": "14px",
                                "flexWrap": "wrap"})


@lru_cache(maxsize=1)
def _agents_svg_cached(mtime):
    """The generator's second drawing, the one that opens the three agents up.

    It ships as docs/agents.svg beside ARCHITECTURE.md and was not shown here
    before, which was a waste: it is the only figure that says what goes into
    each prompt and what comes back out.
    """
    del mtime
    try:
        from tools import blockdiagram as bd
        svg = bd.build_svg(bd.ABLOCKS, bd.ASIGNALS, "Inside the three agents",
                           "what goes into each prompt, what is parsed back "
                           "out, and where the output comes round again")
        layout = bd._svg_layout(bd.ABLOCKS, bd.ASIGNALS)
    except Exception as e:                     # pragma: no cover
        print(f"[dashboard] no agents figure: {type(e).__name__}: {e}")
        return None
    m = re.search(r'height="([\d.]+)"', svg)
    return {"uri": "data:image/svg+xml;base64,"
                   + base64.b64encode(svg.encode()).decode(),
            "w": float(layout["w"]),
            "h": float(m.group(1)) if m else float(layout["h"])}


def agents_figure():
    try:
        mt = int(osp.getmtime(osp.join(REPO_ROOT, "tools", "blockdiagram.py")))
    except OSError:
        mt = 0
    a = _agents_svg_cached(mt)
    if not a:
        return note("The agents figure could not be generated.", color=BAD)
    return html.Div(
        html.Img(src=a["uri"], alt="what goes in and out of each agent",
                 style={"display": "block", "width": "100%",
                        "maxWidth": f"{a['w']:.0f}px", "height": "auto",
                        "margin": "0 auto"}),
        style={"overflowX": "auto"})


def method_tab():
    return html.Div([
        card([
            eyebrow("WHAT THIS IS"),
            html.Div("A CAD part goes in with one sentence describing an edit. "
                     "The system has to produce the edited part, and it is "
                     "scored against the edit a human expert made from the "
                     "same sentence.",
                     style={"color": FG, "fontSize": "17px",
                            "lineHeight": "1.55", "maxWidth": "70ch"}),
            html.Div(headline_numbers(),
                     style={"display": "flex", "gap": "11px",
                            "flexWrap": "wrap", "marginTop": "16px"}),
        ]),

        slide(1, "The pipeline, end to end",
              "click any block for its input and output contract",
              schematic_figure(),
              html.Div(id="node-detail", children=node_detail(None),
                       style={"background": TILE, "borderRadius": "9px",
                              "border": f"1px solid {BORDER}",
                              "padding": "16px 18px", "marginTop": "14px"})),

        slide(2, "What the shapes mean", "the drawing's own vocabulary",
              kinds_row()),

        slide(3, "Inside the three agents",
              "what goes into each prompt, what comes back out",
              agents_figure()),

        slide(4, "The deterministic layer",
              "no model is asked until every one of these has passed",
              eyebrow("TOOLS"),
              chip_row([(n, w, False) for n, w in FLOW_TOOLS], viz.S4),
              eyebrow("GATES", marginTop="16px"),
              chip_row([(n, w, False) for n, w in FLOW_GATES], viz.S2),
              note("A gate rejects for free and hands back text, not just a "
                   "retry. Every one of them came out of a run that failed in "
                   "exactly that way.", marginTop="12px")),

        slide(5, "What bounds the loop", "read live from config.py",
              budget_tiles()),

        slide(6, "Where we stand", "every method over the tasks we have run",
              where_we_stand(bare=True)),

        card([
            html.Details([
                html.Summary("mermaid source, the same graph as "
                             "ARCHITECTURE.md section 4",
                             style={"color": ACCENT, "fontSize": "11.5px",
                                    "cursor": "pointer", "userSelect": "none"}),
                code_block(mermaid_source()),
            ]),
        ]),
    ])


def where_we_stand(bare=False):
    """The result, on the tab that has to carry it on its own.

    Computed live rather than written into the write-up: the run set grows, and
    a number typed into a document is wrong the next time a task finishes.
    `bare` drops the card and its heading, for callers that supply their own.
    """
    rids = our_task_ids()
    if not rids:
        msg = note("No scored runs yet, so there is nothing to compare. "
                   "Run a task on tab 4.")
        return msg if bare else card(msg)
    labels = [OURS] + [method_label(u) for u in METHODS]
    series = _method_series(rids, "diff_f1")
    fig = viz.ranked_bar(labels, [_mean(series[l]) for l in labels],
                         colors=method_colors(), xtitle="mean diff F1",
                         height=250)

    bands = [b for b in viz.DIFF_ORDER
             if any(REQUESTS[r].get("difficulty") == b for r in rids)]
    band_fig = viz.grouped_by_difficulty(
        bands,
        [(l, [_mean([series[l][i] for i, r in enumerate(rids)
                     if REQUESTS[r].get("difficulty") == b]) or 0.0
              for b in bands], method_colors()[l])
         for l in labels],
        height=250)

    body = [
        note(f"{len(rids)} of {len(REQUESTS)} tasks scored. Every method is "
             "averaged over exactly the tasks we have run, never over its own "
             "full 48. Tab 5 has the task-by-task numbers.",
             marginBottom="8px"),
        grid([graph(fig), graph(band_fig)], min_width="420px"),
    ]
    if bare:
        return html.Div(body)
    return card([eyebrow(f"WHERE WE STAND · {len(rids)} OF "
                         f"{len(REQUESTS)} TASKS SCORED"), *body])




@app.callback(Output("node-detail", "children"),
              Output({"type": "mnode", "id": ALL}, "style"),
              Input({"type": "mnode", "id": ALL}, "n_clicks"),
              prevent_initial_call=True)
def pick_node(_clicks):
    s = schematic()
    picked = (ctx.triggered_id or {}).get("id")
    kinds = {b[0]: b[2] for b in s["blocks"]} if s else {}
    styles = []
    for out in ctx.outputs_list[1]:
        bid = out["id"]["id"]
        stroke = ((s or {}).get("kinds", {}).get(kinds.get(bid))
                  or ("", viz.INK_3, ""))[1]
        p = (s or {}).get("place", {}).get(bid, {})
        styles.append({
            **hotspot_style(stroke, bid == picked),
            "left": f"{p.get('x', 0) - 3:.1f}px",
            "top": f"{p.get('y', 0) - 3:.1f}px",
            "width": f"{p.get('w', 0) + 6:.1f}px",
            "height": f"{p.get('h', 0) + 32:.1f}px",
        })
    return node_detail(picked), styles


# --------------------------------------------------------------------------
# tab 6, animation flow: replay the latest saved run over the schematic
# --------------------------------------------------------------------------
ANIM_SPEEDS = [("0.5x", 2400), ("1x", 1200), ("2x", 600)]

# what each verdict makes the router do next, one line per frame
ROUTER_ACTION = {
    "accepted": ("keeps this geometry and moves on to the next sub-goal.",
                 GOOD),
    "partial": ("keeps the geometry and sends it back to be refined in place.",
                WARN),
    "rejected": ("discards the attempt and starts the sub-goal fresh.", BAD),
    "crashed": ("has nothing to keep, so the sub-goal starts fresh.", BAD),
}


def _fact(label, value):
    """One measured name and value line inside a frame body."""
    return html.Div([
        html.Span(f"{label}   ", style={"color": FAINT, "fontSize": "10.5px",
                                        "fontWeight": "700",
                                        "letterSpacing": "0.8px"}),
        html.Span(str(value), style={"color": FG, "fontSize": "12.5px"}),
    ], style={"lineHeight": "1.8"})


def _gt_chip_row(g):
    """One attempt's three vs-ground-truth scores as inline chips, or None."""
    bits = [html.Span("vs ground truth:  ",
                      style={"color": MUTED, "fontSize": "11.5px"})]
    for m, short in (("diff_f1", "diff F1"),
                     ("chamfer_similarity_norm", "chamfer"),
                     ("volume_f1", "vol F1")):
        v = g.get(m)
        if not isinstance(v, (int, float)):
            continue
        bits.append(html.Span(f"{short} {v:.3f}    ", style={
            "color": viz.score_color(v), "fontSize": "11.5px",
            "fontWeight": "700"}))
    return html.Div(bits, style={"marginTop": "8px"}) if len(bits) > 1 else None


def _frame(block, title, sub, body):
    return {"block": block, "title": title, "sub": sub, "body": body}


@lru_cache(maxsize=6)
def _flow_frames_cached(rid, sig):
    """The frame list is rebuilt only when the run record changes: the tick
    callback asks for it every second, and the evaluate frame's deviation
    overlay plus a hundred re-encoded thumbnails per tick is what stalls a
    dev server. `sig` is the record file's mtime, cache key only."""
    del sig
    rec = our_runs().get(rid)
    if not rec:
        return []
    req = REQUESTS.get(rid, {})
    start = req.get("brep_start")
    frames = []

    # 1 · the task and the part it starts from
    renders = [t for t in (img_tag(start, v) for v in VIEWS) if t]
    frames.append(_frame("dataset", "The task arrives", "", [
        html.Div(f"“{req.get('text', '')}”",
                 style={"color": FG, "fontSize": "16px", "fontStyle": "italic",
                        "marginBottom": "10px"}),
        html.Div(renders, style={"display": "flex", "flexWrap": "wrap"})
        if renders else note("no dataset renders for this part"),
    ]))

    # 2 · the geometry index
    frames.append(_frame("geo_index", "Geometry index measures the part", "", [
        note("The B-rep is opaque to a language model. Here it becomes a "
             "measured text index: every face with its type, size and "
             "position, so the agents can reason about the solid in words.",
             marginBottom="8px"),
        graph(mesh_figure(geom_path(start, "stl"), MESH_INPUT,
                          "INPUT: the solid being indexed", height=360)),
    ]))

    # 3 · the tagged input views (inlined base64 in the record itself)
    tagged = rec.get("input_tagged") or {}
    tiles = []
    for v in VIEWS:
        uri = tagged.get(v)
        if not uri:
            continue
        tiles.append(html.Div([
            html.Img(src=uri, style={"width": "100%", "borderRadius": "5px",
                                     "background": "#fff", "display": "block",
                                     "border": f"1px solid {BORDER}"}),
            html.Div(v, style={"color": MUTED, "fontSize": "11px",
                               "textAlign": "center", "paddingTop": "3px"}),
        ], style={"width": "13%", "padding": "4px"}))
    if tiles:
        frames.append(_frame("views_in", "Seven colour-coded views go in", "", [
            note("Every feature wears its own colour, so the strategist can "
                 "name a face instead of pointing at pixels.",
                 marginBottom="8px"),
            html.Div(tiles, style={"display": "flex", "flexWrap": "wrap"}),
        ]))

    # 4 · the plan
    plan_bits = []
    if isinstance(rec.get("plan_summary"), str) and rec["plan_summary"].strip():
        plan_bits.append(note(rec["plan_summary"], marginBottom="10px"))
    for i, t in enumerate(rec.get("subtasks") or []):
        stat = str(t.get("status", "?"))
        col = GOOD if stat == "done" else BAD if stat == "failed" else MUTED
        plan_bits.append(html.Div([
            pill(str(i + 1), col, filled=False, marginRight="9px"),
            html.Span(t.get("goal", ""),
                      style={"color": FG, "fontSize": "12.5px"}),
            html.Span(f"   {stat} · {t.get('attempts', 0)} attempt(s)",
                      style={"color": MUTED, "fontSize": "11px",
                             "whiteSpace": "nowrap"}),
        ], style={"marginBottom": "8px", "lineHeight": "1.55"}))
    frames.append(_frame("strategist", "Strategist writes the plan", "",
                         plan_bits or [note("no plan recorded on this run")]))

    # 5 · every executed attempt, in order
    for st in rec.get("steps") or []:
        subl = f"sub-goal {st.get('sub', '?')} · attempt {st.get('attempt', '?')}"

        tags = [str(t) for t in _listy(st.get("tags")) if str(t).strip()]
        recipes = [str(r) for r in _listy(st.get("recipes")) if str(r).strip()]
        if tags or recipes:
            body = []
            if tags:
                body.append(_fact("TAGS", ", ".join(tags)))
            if recipes:
                body.append(_fact("RECIPES", " ".join(f"§{r}" for r in recipes)))
            body.append(note("the playbook sections the executor's prompt "
                             "carried into this attempt", marginTop="6px"))
            frames.append(_frame("skillref",
                                 "Skill library hands over its playbooks",
                                 subl, body))

        body = []
        if st.get("goal"):
            body.append(_fact("GOAL", st["goal"]))
        if st.get("approach"):
            body.append(_fact("APPROACH", st["approach"]))
        src = st.get("script")
        if not src and st.get("script_file") and osp.exists(st["script_file"]):
            try:
                with open(st["script_file"]) as f:
                    src = f.read()
            except OSError:
                src = None
        if src:
            body.append(code_block(src, maxHeight="46vh"))
        if body:
            frames.append(_frame("executor", "Executor writes the CadQuery",
                                 subl, body))

        if st.get("error"):
            frames.append(_frame("runner", "Runner: the script crashed", subl, [
                code_block(str(st["error"]), color=BAD,
                           whiteSpace="pre-wrap", overflowWrap="anywhere"),
            ]))
        else:
            frames.append(_frame("runner", "Runner executes the script", subl, [
                note("The script ran and produced geometry." if st.get("ok")
                     else "The script ran but left no geometry behind."),
            ]))

        gd_labels = (("faces", "FACES"), ("volume", "VOLUME"),
                     ("volume_change_pct", "VOLUME CHANGE %"),
                     ("new_surface_types", "NEW SURFACE TYPES"),
                     ("gate", "GATE"))
        vals = []
        for k, lab in gd_labels:
            v = st.get(k)
            if v is None:
                continue
            if isinstance(v, (list, tuple)):
                v = ", ".join(str(x) for x in v)
            vals.append(_fact(lab, v))
        if vals:
            frames.append(_frame("geo_diff",
                                 "Geometry diff measures the result",
                                 subl, vals))

        views = st.get("views") or {}
        order = ["toprightiso"] + [v for v in VIEWS if v != "toprightiso"]
        thumbs = [t for t in (img_from_path(views.get(v), v)
                              for v in order if v in views) if t]
        if thumbs:
            body = [html.Div(thumbs, style={"display": "flex",
                                            "flexWrap": "wrap"})]
            if st.get("views_changed"):
                fold = redacted_views(st["views_changed"])
                if fold is not None:
                    body.append(fold)
            frames.append(_frame("views_out",
                                 "The new geometry is rendered", subl, body))

        colour, vlabel = VERDICT_STYLE.get(st.get("verdict", "pending"),
                                           (MUTED, st.get("verdict", "?")))
        body = [html.Div(pill(vlabel, colour), style={"marginBottom": "9px"})]
        if st.get("observation"):
            body.append(note(f"QA saw: {st['observation']}",
                             fontStyle="italic", marginBottom="6px"))
        for issue in st.get("issues") or []:
            body.append(html.Div(f"• {issue}", style={
                "color": colour, "fontSize": "12px", "lineHeight": "1.6"}))
        if isinstance(st.get("confidence"), (int, float)):
            body.append(_fact("CONFIDENCE", f"{st['confidence']:.2f}"))
        g = st.get("gt_scores") or {}
        chips = _gt_chip_row(g) if g and not g.get("error") else None
        if chips is not None:
            body.append(chips)
        frames.append(_frame("qa", "QA judges the attempt", subl, body))

        act = ROUTER_ACTION.get(st.get("verdict"))
        if act:
            line, col = act
            frames.append(_frame("router", "Router acts on the verdict", subl, [
                html.Div([
                    html.Span(str(st.get("verdict", "")).upper() + ":  ",
                              style={"color": col, "fontWeight": "700",
                                     "fontSize": "13px"}),
                    html.Span(f"the router {line}",
                              style={"color": FG, "fontSize": "13px"}),
                ], style={"lineHeight": "1.6"}),
            ]))

    # 6 · finalize
    dest = rec.get("dest")
    stl = our_output_stl(dest)
    body = []
    if dest:
        body.append(_fact("OUTPUT FOLDER", dest))
    body.append(graph(mesh_figure(stl, MESH_PRED, "OUR RESULT", height=360))
                if stl else note("this run left no geometry on disk",
                                 color=BAD))
    frames.append(_frame("finalize", "Finalize writes the output", "", body))

    # 7 · evaluate
    scores = rec.get("scores") or {}
    tiles = [kpi(METRIC_LABELS[m].upper(),
                 f"{scores[m]:.3f}"
                 if isinstance(scores.get(m), (int, float)) else "n/a",
                 "the metric that measures editing" if m == "diff_f1" else "",
                 color=viz.score_color(scores.get(m)))
             for m in ("diff_f1", "chamfer_similarity_norm", "volume_f1")]
    body = [html.Div(tiles, style={"display": "flex", "gap": "11px",
                                   "flexWrap": "wrap",
                                   "marginBottom": "10px"})]
    gt_id = EDITS_BY_REQUEST.get(rid, {}).get("GROUND TRUTH")
    gt_stl = geom_path(gt_id, "stl")
    if stl and gt_stl:
        fig, _stats = deviation_figure(stl, gt_stl, height=400)
        body.append(graph(fig))
    frames.append(_frame("evaluate", "Scored against the ground truth", "",
                         body))
    return frames


def flow_frames(rid):
    """The saved run of one task, as an ordered list of animation frames."""
    rec = our_runs().get(rid)
    if not rec:
        return []
    try:
        sig = int(osp.getmtime(_run_record_path(rid)))
    except (OSError, TypeError):
        sig = 0
    return _flow_frames_cached(rid, sig)


def _fnode_style(p, stroke, active=False, visited=False, box=(1.0, 1.0)):
    """The hotspot box over one schematic block, in one of three states.

    Positioned in percentages of the drawing rather than pixels, so the
    diagram can be sized from the viewport and the hotspots follow it. With
    pixel offsets the drawing had to be locked to one scale factor, and that
    is what kept it small.
    """
    w, h = box
    style = {
        "position": "absolute", "cursor": "pointer", "borderRadius": "4px",
        "border": "2px solid transparent", "background": "transparent",
        "transition": "background .15s ease, border-color .15s ease",
        "left": f"{100.0 * (p.get('x', 0) - 3) / w:.3f}%",
        "top": f"{100.0 * (p.get('y', 0) - 3) / h:.3f}%",
        "width": f"{100.0 * (p.get('w', 0) + 6) / w:.3f}%",
        # the caption under each box carries its name, so the clickable area
        # has to reach past the box itself, same as tab 1
        "height": f"{100.0 * (p.get('h', 0) + 32) / h:.3f}%",
    }
    if active:
        style.update({"border": f"2px solid {stroke}",
                      "background": "rgba(42,120,214,0.14)"})
    elif visited:
        style["background"] = "rgba(42,120,214,0.06)"
    return style


def _flow_hotspots(s, frames, frame):
    """(styles, classNames) for every block, given the current frame index."""
    active = frames[frame]["block"] if frames else None
    visited = {frames[i]["block"] for i in range(frame)} if frames else set()
    box = (s["w"], s["h"])
    styles, classes = [], []
    for bid, _name, kind, _sub in s["blocks"]:
        stroke = (s["kinds"].get(kind) or ("", viz.INK_3, ""))[1]
        p = s["place"].get(bid, {})
        styles.append(_fnode_style(p, stroke, active=bid == active,
                                   visited=bid in visited, box=box))
        classes.append("flow-active" if bid == active else "")
    return styles, classes


# The drawing is sized from the viewport rather than pinned to one scale
# factor: it takes whatever height is left under the controls, and its width
# follows from the aspect ratio without ever exceeding its column. Pinning it
# to a pixel width is what kept it small, because the pixel that fits a short
# laptop window is the pixel every window then got. The subtraction is the page
# chrome above it plus room for the agent strip below; the floor keeps it
# usable on a short window, the ceiling stops it exceeding its natural size.
FLOW_DIAGRAM_H = "clamp(430px, calc(100vh - 300px), 800px)"


def _flow_schematic(frames, frame):
    """The tab-1 schematic technique, with this tab's own hotspot ids."""
    s = schematic(compact=True)
    if not s:
        return note("The schematic could not be generated. Run "
                    "`python3 tools/blockdiagram.py` and reload.", color=BAD)
    styles, classes = _flow_hotspots(s, frames, frame)
    hotspots = [
        html.Div(id={"type": "fnode", "id": bid}, n_clicks=0,
                 title=f"{name}\n\nclick to jump the replay here and zoom in",
                 className=cls, style=sty)
        for (bid, name, _kind, _sub), sty, cls
        in zip(s["blocks"], styles, classes)
    ]
    return html.Div([
        html.Img(src=s["uri"], alt="signal-flow schematic of the pipeline",
                 style={"display": "block", "width": "100%", "height": "100%",
                        "pointerEvents": "none"}),
        *hotspots,
    ], style={"position": "relative",
              "aspectRatio": f"{s['w']:.0f} / {s['h']:.0f}",
              "height": FLOW_DIAGRAM_H, "maxWidth": "100%",
              "margin": "0 auto"})



# The three blocks that are a model call, in the order the loop reaches them.
# Everything else on the schematic is deterministic, and the point of the strip
# is to show how little of the pipeline is actually a model deciding something.
FLOW_AGENTS = [
    ("strategist", "Strategist", "splits one sentence into ordered sub-goals",
     "MODEL_STRATEGIST"),
    ("executor", "Executor", "writes the CadQuery for exactly one sub-goal",
     "MODEL_EXECUTOR"),
    ("qa", "QA", "reads the rendered views and accepts or rejects",
     "MODEL_QA"),
]


def _flow_agents(frames, frame):
    """The three agents, with the one currently working lit up.

    No counters. Whether the executor is on its second or its sixth attempt is
    already on the frame label and in the stage card beside it, and a number
    ticking here only competes with the thing this strip is for, which is
    seeing at a glance which of the three is thinking right now.
    """
    active = frames[frame]["block"] if frames else None
    s = schematic(compact=True)
    lit = (s["kinds"].get("agent") or ("", viz.S3, ""))[1] if s else viz.S3

    cards = []
    for bid, name, what, cfg in FLOW_AGENTS:
        on = bid == active
        model = getattr(our_config, cfg, "") if OURS_AVAILABLE else ""
        cards.append(html.Div([
            html.Span(style={
                "width": "8px", "height": "8px", "borderRadius": "4px",
                "background": lit if on else BORDER,
                "display": "inline-block", "flex": "0 0 auto",
                "transition": "background .2s ease"}),
            html.Span(name, style={
                "color": lit if on else MUTED,
                "fontWeight": "700" if on else "600", "fontSize": "12.5px",
                "whiteSpace": "nowrap"}),
            html.Span("working" if on else "", style={
                "color": lit, "fontSize": "9.5px", "fontWeight": "700",
                "letterSpacing": "0.6px", "marginLeft": "auto",
                "textTransform": "uppercase", "whiteSpace": "nowrap"}),
        ], className="flow-agent-on" if on else "",
            title=f"{name}: {what}" + (f"\nmodel: {model}" if model else ""),
            style={
            "flex": "1 1 0", "minWidth": "0", "padding": "8px 11px",
            "borderRadius": "8px", "background": "#eef7f1" if on else TILE,
            "border": f"1px solid {lit if on else BORDER}",
            "display": "flex", "alignItems": "center", "gap": "8px",
            "transition": "background .2s ease, border-color .2s ease"}))

    # The router sits over the three of them because that is the relationship:
    # it is the only block that decides what happens next, and it is NOT an
    # agent, it is a state machine with no model call. Red rather than green
    # keeps that difference visible at a glance.
    ron = active == "router"
    router = html.Div([
        html.Span(style={
            "width": "9px", "height": "9px", "borderRadius": "5px",
            "background": viz.S8 if ron else BORDER,
            "display": "inline-block", "flex": "0 0 auto",
            "transition": "background .2s ease"}),
        html.Span("Run controller", style={
            "color": viz.S8 if ron else FG, "fontWeight": "700",
            "fontSize": "13.5px", "whiteSpace": "nowrap"}),
        html.Span("keeps the budgets, decides which geometry is carried "
                  "forward. Deterministic, no model call.", style={
                      "color": MUTED, "fontSize": "11px",
                      "overflow": "hidden", "textOverflow": "ellipsis",
                      "whiteSpace": "nowrap"}),
        html.Span("deciding" if ron else "", style={
            "color": viz.S8, "fontSize": "9.5px", "fontWeight": "700",
            "letterSpacing": "0.6px", "marginLeft": "auto",
            "textTransform": "uppercase", "whiteSpace": "nowrap"}),
    ], className="flow-router-on" if ron else "", style={
        "display": "flex", "alignItems": "center", "gap": "9px",
        "padding": "10px 13px", "borderRadius": "9px",
        "background": "#fdf0f0" if ron else TILE,
        "border": f"1.5px solid {viz.S8 if ron else BORDER}",
        "transition": "background .2s ease, border-color .2s ease"})

    return html.Div([
        router,
        # one thin row under it: the strip has to sit below the drawing without
        # pushing it under the fold, which is the whole reason for two columns
        html.Div([
            eyebrow("AGENTS", marginBottom="0", whiteSpace="nowrap",
                    paddingTop="8px"),
            html.Div(cards, style={"display": "flex", "gap": "8px",
                                   "flex": "1 1 auto", "minWidth": "0"}),
        ], style={"display": "flex", "gap": "12px", "alignItems": "flex-start",
                  "marginTop": "8px"}),
    ])


def _flow_stage(frames, frame):
    """The card under the schematic that carries the current frame."""
    f = frames[frame]
    head = [
        pill(f["block"].upper(), ACCENT, filled=False),
        html.Span(f["sub"], style={"color": FAINT, "fontSize": "11.5px",
                                   "marginLeft": "10px"}),
    ]
    body = f["body"] if isinstance(f["body"], list) else [f["body"]]
    # the key remounts the card on every frame change, which is what lets the
    # CSS entry animation replay instead of running once and never again
    return html.Div(card([
        html.Div(head, style={"display": "flex", "alignItems": "center",
                              "flexWrap": "wrap", "marginBottom": "7px"}),
        html.H3(f["title"], style={"margin": "0 0 10px 0", "color": FG,
                                   "fontSize": "18px",
                                   "letterSpacing": "-0.2px"}),
        *body,
    ]), key=f"flow-frame-{frame}", className="flow-stage-card")


def _flow_zoom(frames, bid):
    """One block blown up: its full contract plus every frame it owns."""
    s = schematic()
    name = ({b[0]: b[1] for b in s["blocks"]}.get(bid, bid)) if s else bid
    matches = [f for f in frames if f["block"] == bid]
    stacked = []
    for f in matches:
        stacked.append(html.Div([
            eyebrow(f["title"].upper()
                    + (f"  ·  {f['sub']}" if f["sub"] else "")),
            *(f["body"] if isinstance(f["body"], list) else [f["body"]]),
        ], style={"borderTop": f"1px solid {BORDER}",
                  "padding": "14px 0 4px", "marginTop": "10px"}))
    if not stacked:
        stacked = [note("this block has no frames in the saved run",
                        marginTop="12px")]
    return html.Div(card([
        html.Div([
            html.H3(name, style={"margin": "0", "color": FG,
                                 "fontSize": "20px",
                                 "letterSpacing": "-0.3px"}),
            # a pattern id, not a plain string: the button only exists while
            # zoomed, and Dash refuses to fire a callback whose plain-id Input
            # is missing from the layout. A pattern Input with zero matches
            # is legal.
            html.Button("✕ close", id={"type": "fclose", "id": "zoom"},
                        n_clicks=0, style={
                "marginLeft": "auto", "background": "transparent",
                "color": MUTED, "border": f"1px solid {BORDER}",
                "borderRadius": "7px", "padding": "6px 14px",
                "fontSize": "12.5px", "fontWeight": "600",
                "cursor": "pointer"}),
        ], style={"display": "flex", "alignItems": "center",
                  "marginBottom": "12px"}),
        html.Div(node_detail(bid), style={
            "background": TILE, "borderRadius": "9px",
            "border": f"1px solid {BORDER}", "padding": "16px 18px",
            "marginBottom": "6px"}),
        *stacked,
    ]), key=f"flow-zoom-{bid}", className="flow-zoom-panel")


def animation_tab(rid):
    frames = flow_frames(rid)
    if not frames:
        return card([
            eyebrow("6 · ANIMATION FLOW"),
            html.Div("No saved run for this task yet.",
                     style={"color": FG, "fontSize": "16px",
                            "fontWeight": "600", "marginBottom": "6px"}),
            note("This tab replays a finished run frame by frame over the "
                 "pipeline schematic. Go to tab 4, Our implementation, and "
                 "press Run; once the run finishes it can be replayed here."),
        ])
    n = len(frames)
    controls = card([
        html.Div([
            html.Button("▶ Play", id="anim-play", n_clicks=0, style={
                "background": ACCENT, "color": "#fff", "border": "none",
                "padding": "9px 18px", "borderRadius": "7px",
                "fontSize": "13.5px", "fontWeight": "600",
                "cursor": "pointer", "minWidth": "96px"}),
            html.Button("⏮ Reset", id="anim-reset", n_clicks=0, style={
                "background": "transparent", "color": MUTED,
                "border": f"1px solid {BORDER}", "padding": "9px 16px",
                "borderRadius": "7px", "fontSize": "13.5px",
                "fontWeight": "600", "cursor": "pointer"}),
            dcc.RadioItems(
                id="anim-speed",
                options=[{"label": f" {lab}", "value": ms}
                         for lab, ms in ANIM_SPEEDS],
                value=1200, inline=True,
                style={"color": FG, "fontSize": "13px", "fontWeight": "600"},
                inputStyle={"marginRight": "5px", "marginLeft": "14px"},
                labelStyle={"cursor": "pointer"}),
            # the frame label to the right already says where the replay is,
            # so Dash 4's editable value box on the slider is pure clutter
            html.Div(dcc.Slider(id="anim-scrub", min=0, max=max(n - 1, 1),
                                step=1, value=0, allow_direct_input=False,
                                marks={0: "1", max(n - 1, 1): str(n)}),
                     style={"flex": "1 1 260px", "minWidth": "200px",
                            "padding": "0 6px"}),
            html.Div(f"frame 1 of {n}", id="anim-frame-label",
                     style={"color": MUTED, "fontSize": "12px",
                            "fontVariantNumeric": "tabular-nums",
                            "whiteSpace": "nowrap"}),
        ], style={"display": "flex", "alignItems": "center", "gap": "10px",
                  "flexWrap": "wrap"}),
    ])
    return html.Div([
        controls,
        # Side by side, and both columns start at the same top edge, so the
        # step that just played is on screen without scrolling for it. The
        # right column scrolls inside itself rather than growing the page,
        # because a 200 line CadQuery script would otherwise push the diagram
        # off the top every time the replay reached the executor.
        html.Div([
            card([
                eyebrow("THE RUN, REPLAYED OVER THE PIPELINE",
                        marginBottom="1px"),
                note("Pulsing is where the replay stands, tinted has already "
                     "played, click any block to open it.",
                     marginBottom="6px", fontSize="11.5px"),
                _flow_schematic(frames, 0),
                html.Div(id="anim-agents",
                         children=_flow_agents(frames, 0),
                         style={"marginTop": "12px"}),
            ], marginBottom="0", padding="12px 13px",
                flex="1 1 660px", minWidth="0"),
            # zoom, not a pile of larger font sizes: the frame bodies are built
            # by a dozen helpers with their own inline sizes, and scaling the
            # whole panel keeps their hierarchy intact instead of flattening
            # every band to one size. It reflows properly, unlike a transform.
            # Back to 1.0 now that the diagram takes the wider column: the
            # panel is narrower, so the same type reads larger relative to it.
            html.Div(id="anim-stage", children=_flow_stage(frames, 0),
                     style={"flex": "1 1 380px", "minWidth": "0",
                            "zoom": "1.0",
                            "maxHeight": "calc(100vh - 150px)",
                            "overflowY": "auto", "overflowX": "hidden"}),
        ], style={"display": "flex", "gap": "14px", "alignItems": "flex-start",
                  "flexWrap": "wrap"}),
        # store and interval live in the tab body on purpose: switching tabs
        # discards them, so coming back always starts a clean replay
        dcc.Store(id="anim-state",
                  data={"frame": 0, "playing": False, "zoom": None}),
        dcc.Interval(id="anim-tick", interval=1200, disabled=True),
    ])


@app.callback(Output("anim-tick", "interval"),
              Input("anim-speed", "value"))
def flow_speed(ms):
    return int(ms or 1200)


@app.callback(Output("anim-state", "data"),
              Output("anim-stage", "children"),
              Output("anim-agents", "children"),
              Output("anim-scrub", "value"),
              Output("anim-frame-label", "children"),
              Output("anim-play", "children"),
              Output("anim-tick", "disabled"),
              Output({"type": "fnode", "id": ALL}, "style"),
              Output({"type": "fnode", "id": ALL}, "className"),
              Input("anim-tick", "n_intervals"),
              Input("anim-play", "n_clicks"),
              Input("anim-reset", "n_clicks"),
              Input("anim-scrub", "value"),
              Input({"type": "fnode", "id": ALL}, "n_clicks"),
              Input({"type": "fclose", "id": ALL}, "n_clicks"),
              State("anim-state", "data"),
              State("request", "value"),
              prevent_initial_call=True)
def flow_step(_tick, _play, _reset, scrub, _clicks, _close, state, rid):
    """Every way the replay can move, in one callback.

    One callback rather than several because they would all have to write
    anim-state, and because the scrubber is both an input (the user drags it)
    and an output (the tick advances it), which Dash only allows inside a
    single callback.
    """
    frames = flow_frames(rid)
    n = len(frames)
    if n == 0:
        return (no_update,) * 7 + ([], [])

    state = state or {}
    frame = int(state.get("frame") or 0)
    playing = bool(state.get("playing"))
    zoom = state.get("zoom")
    trig = ctx.triggered_id
    trig_val = (ctx.triggered[0].get("value") if ctx.triggered else None)

    if trig == "anim-tick":
        # stop by disabling the interval at the last frame, never loop
        frame = min(frame + 1, n - 1)
        if frame >= n - 1:
            playing = False
    elif trig == "anim-play":
        if not playing and frame >= n - 1:
            frame = 0
        playing = not playing
    elif trig == "anim-reset":
        frame, playing, zoom = 0, False, None
    elif trig == "anim-scrub":
        frame, zoom = int(scrub or 0), None
    elif isinstance(trig, dict) and trig.get("type") == "fclose":
        # ignore the phantom fire when the close button first mounts
        if trig_val:
            zoom = None
    elif isinstance(trig, dict) and trig.get("type") == "fnode":
        if trig_val:
            bid = trig["id"]
            first = next((i for i, f in enumerate(frames)
                          if f["block"] == bid), None)
            if first is not None:
                frame = first
            zoom, playing = bid, False
    frame = max(0, min(frame, n - 1))

    stage = _flow_zoom(frames, zoom) if zoom else _flow_stage(frames, frame)
    # the compact build, the same one _flow_schematic drew: the hotspot styles
    # returned here carry absolute pixel positions, and taken from the wide
    # layout every box would land somewhere else on the drawing
    s = schematic(compact=True)
    styles, classes = _flow_hotspots(s, frames, frame) if s else ([], [])
    return ({"frame": frame, "playing": playing, "zoom": zoom},
            stage, _flow_agents(frames, frame), frame,
            f"frame {frame + 1} of {n}",
            "⏸ Pause" if playing else "▶ Play",
            not playing, styles, classes)


# --------------------------------------------------------------------------
# tab 5 — results: one task with every method, or the whole benchmark
# --------------------------------------------------------------------------
def method_colors():
    """One colour per method, fixed for the whole session.

    Colour follows the entity, never its rank: the ranked bar chart re-sorts
    itself every time a run lands, and a method that keeps its colour can be
    followed from that chart to the difficulty chart to the strip plot.
    """
    out = {OURS: viz.OURS_COLOR}
    # the second human is a reference line, not a competitor, so it wears the
    # neutral ink rather than a series hue. That also keeps two greens (aqua
    # and green) off the same chart.
    out[method_label("other human")] = viz.INK_2
    slot = 1
    for user in METHODS:
        if user == "other human":
            continue
        out[method_label(user)] = viz.SERIES[slot % len(viz.SERIES)]
        slot += 1
    return out


def results_tab(rid):
    return html.Div([
        card([
            html.Div([
                html.Div([
                    eyebrow("VIEW", marginBottom="7px"),
                    dcc.RadioItems(
                        id="results-scope",
                        options=[{"label": " Per task", "value": "task"},
                                 {"label": " General", "value": "general"}],
                        value="task", inline=True,
                        style={"color": FG, "fontSize": "14px",
                               "fontWeight": "600"},
                        inputStyle={"marginRight": "6px",
                                    "marginLeft": "18px"},
                        labelStyle={"cursor": "pointer"}),
                ]),
                html.Div(id="results-caption",
                         style={"color": MUTED, "fontSize": "12px",
                                "marginLeft": "auto", "maxWidth": "48ch",
                                "textAlign": "right", "lineHeight": "1.5"}),
            ], style={"display": "flex", "alignItems": "center",
                      "flexWrap": "wrap", "gap": "12px"}),
        ], marginBottom="14px"),
        dcc.Loading(html.Div(id="results-body"), type="dot", color=ACCENT),
    ])


@app.callback(Output("results-body", "children"),
              Output("results-caption", "children"),
              Input("results-scope", "value"), Input("request", "value"))
def render_results(scope, rid):
    if scope == "general":
        return general_results(), (
            "Every benchmark method against our pipeline, over the "
            f"{len(our_task_ids())} tasks we have scored so far.")
    return per_task_results(rid), (
        "Every method's answer to this one task, side by side, with the "
        "scores each one earned.")


# --------------------------------------------------------------------------
# per task
# --------------------------------------------------------------------------
def sweep_dest(entry):
    """The output folder of a run recorded only in the sweep file.

    evaluate.py stores the edit id, not the path; the layout is fixed, so the
    path is rebuilt from it. Without this a task whose newest run predates the
    dashboard's own records showed its score with no geometry beside it.
    """
    edit_id = entry.get("edit_id")
    if not edit_id or not OURS_AVAILABLE:
        return None
    stamp = str(edit_id).rsplit("_", 1)[-1]
    d = osp.join(our_config.RESULTS, "runs", "ours_adk-router", "outputs",
                 str(edit_id), "brep_end", stamp)
    return d if osp.isdir(d) else None


def our_view_path(dest, view):
    """One of the seven jpgs finalize writes next to our tmp.stl."""
    if not dest:
        return None
    p = osp.join(dest, f"tmp_{view}.jpg")
    return p if osp.exists(p) else None


def score_chips(scores, small=False):
    """The three benchmark metrics as a compact row. Diff F1 leads because it
    is the one that measures editing rather than similarity."""
    order = ["diff_f1", "chamfer_similarity_norm", "volume_f1"]
    short = {"diff_f1": "diff F1", "chamfer_similarity_norm": "chamfer",
             "volume_f1": "vol F1"}
    out = []
    for m in order:
        v = scores.get(m)
        txt = f"{v:.3f}" if isinstance(v, (int, float)) else "n/a"
        col = viz.score_color(v)
        lead = m == "diff_f1"
        out.append(html.Div([
            html.Div(short[m], style={"color": FAINT, "fontSize": "9.5px",
                                      "fontWeight": "700",
                                      "letterSpacing": "0.7px"}),
            html.Div(txt, style={"color": col,
                                 "fontSize": "20px" if lead else "14px",
                                 "fontWeight": "700", "lineHeight": "1.2",
                                 "fontVariantNumeric": "tabular-nums"}),
        ], style={"flex": "1", "padding": "6px 9px", "background": TILE,
                  "borderRadius": "7px",
                  "border": f"1px solid {ACCENT if lead and isinstance(v, (int, float)) and v >= 0.6 else BORDER}"}))
    return html.Div(out, style={"display": "flex", "gap": "6px",
                                "marginTop": "9px"})


def method_panel(title, subtitle, colour, stl, iso_img, views, scores=None,
                 emphasis=False, missing=None):
    """One column of the per-task comparison: what this method produced.

    `missing` is the reason there is no geometry to draw. Without it a method
    that the benchmark scored but never published a B-rep for looked identical
    to a method whose edit crashed, which are very different things.
    """
    body = [
        html.Div([
            pill(title, colour),
            html.Span(subtitle, style={"color": FAINT, "fontSize": "11px",
                                       "marginLeft": "8px"}),
        ], style={"marginBottom": "9px", "display": "flex",
                  "alignItems": "center", "flexWrap": "wrap", "gap": "4px"}),
    ]
    if iso_img is not None:
        body.append(iso_img)
    else:
        # keeps the 3D viewers on one line across the row when one method has
        # no iso render to show
        body.append(html.Div(style={"height": "150px", "background": TILE,
                                    "borderRadius": "7px"}))
    if missing and stl is None:
        body.append(html.Div(missing, style={
            "height": "230px", "display": "flex", "alignItems": "center",
            "justifyContent": "center", "textAlign": "center",
            "color": FAINT, "fontSize": "12px", "background": TILE,
            "borderRadius": "8px", "padding": "0 18px", "lineHeight": "1.5"}))
    else:
        body.append(graph(mesh_figure(stl, colour, "", height=230,
                                      max_tris=5000)))
    if scores is not None:
        body.append(score_chips(scores))
    if views:
        body.append(html.Details([
            html.Summary("all seven views",
                         style={"color": ACCENT, "fontSize": "10.5px",
                                "cursor": "pointer", "userSelect": "none",
                                "marginTop": "7px"}),
            html.Div(views, style={"display": "flex", "flexWrap": "wrap",
                                   "marginTop": "4px"}),
        ]))
    extra = {"border": f"1.5px solid {colour}",
             "boxShadow": "0 0 0 3px rgba(42,120,214,0.10)"} if emphasis else {}
    return viewer_panel(body, padding="12px", **extra)


def _iso(path_or_none, height="150px"):
    """The iso render, letterboxed to a fixed height.

    Left at its natural size the renders are square and eat 300 px of a card
    that also carries a 3D viewer and three numbers, so a row of seven methods
    scrolled for two screens. Fixed height also makes the cards in a row the
    same height, which is what lets the eye scan across them.
    """
    if not path_or_none or not osp.exists(path_or_none):
        return None
    b64 = _b64_file(path_or_none, osp.getmtime(path_or_none))
    mime = "jpeg" if path_or_none.lower().endswith((".jpg", ".jpeg")) else "png"
    return html.Img(src=f"data:image/{mime};base64,{b64}", style={
        "width": "100%", "height": height, "objectFit": "contain",
        "borderRadius": "7px", "background": "#fff",
        "display": "block", "border": f"1px solid {BORDER}"})


def _dataset_iso_path(brep_id):
    """Path to a dataset part's iso render, ours if we have re-rendered it."""
    if not brep_id:
        return None
    p = our_views(brep_id).get("toprightiso")
    if not p or not osp.exists(p):
        p = osp.join(ROOT, "breps", f"{brep_id}_toprightiso.jpg")
    return p


def _dataset_iso(brep_id):
    return _iso(_dataset_iso_path(brep_id))


def _dataset_views(brep_id, width="24%"):
    return [t for t in (img_tag(brep_id, v, width) for v in VIEWS if v !=
                        "toprightiso") if t]


def per_task_results(rid):
    req = REQUESTS[rid]
    avail = EDITS_BY_REQUEST.get(rid, {})
    start = req.get("brep_start")
    gt_id = avail.get("GROUND TRUTH")
    kind, payload = latest_source(rid)
    colours = method_colors()

    # The question the row has to answer is "what did each method do with the
    # same starting part", so the two things that are not answers, the part
    # going in and the human's answer key, are lifted out and set above it.
    given = [
        method_panel("INPUT", "before the edit", MESH_INPUT,
                     geom_path(start, "stl"), _dataset_iso(start),
                     _dataset_views(start)),
        method_panel("GROUND TRUTH", "human expert", MESH_GT,
                     geom_path(gt_id, "stl"), _dataset_iso(gt_id),
                     _dataset_views(gt_id)),
    ]
    answers, ours_panel = [], None

    entities, series_vals = [], {m: [] for m in METRICS}

    our_here = {m: our_score(rid, m) for m in METRICS}
    if kind:
        dest = (payload.get("dest") if kind == "record"
                else sweep_dest(payload))
        ours_panel = method_panel(
            "OURS", "three-agent loop", MESH_PRED, our_output_stl(dest),
            _iso(our_view_path(dest, "toprightiso")),
            [t for t in (img_from_path(our_view_path(dest, v), v, "24%")
                         for v in VIEWS if v != "toprightiso") if t],
            scores=our_here, emphasis=True,
            missing="this run left no geometry on disk. Press Run on tab 4 "
                    "to rebuild it.")
        entities.append(OURS)
        for m in METRICS:
            v = our_here[m]
            series_vals[m].append(float(v) if isinstance(v, (int, float))
                                  else 0.0)

    for user in METHODS:
        label = method_label(user)
        scores = {}
        present = False
        for m in METRICS:
            v, _failed = score_for(rid, user, m)
            scores[m] = v
            present = present or v is not None
        if not present:
            continue
        entities.append(label)
        for m in METRICS:
            v = scores[m]
            series_vals[m].append(float(v) if isinstance(v, (int, float)) else 0.0)

        brep = avail.get(user)
        answers.append(method_panel(
            label.upper(),
            # everything that is not ours came with the benchmark, so that is
            # what it is called here rather than "baseline"
            "second human" if user == "other human" else "benchmark",
            colours.get(label, viz.BASE_COLOR),
            geom_path(brep, "stl"), _dataset_iso(brep),
            _dataset_views(brep), scores=scores,
            missing=("scored by the benchmark, but no B-rep is published "
                     "for this method" if not brep else
                     "this edit produced no geometry, so it scores 0.0")))

    # ours sits in the middle of the answers rather than at one end: it is the
    # comparison the row exists to make, and an end position reads as an
    # afterthought
    if ours_panel is not None:
        half = len(answers) // 2
        answers = answers[:half] + [ours_panel] + answers[half:]

    chart = viz.grouped_metric_bar(
        entities,
        [(METRIC_LABELS[m], series_vals[m]) for m in
         ("diff_f1", "chamfer_similarity_norm", "volume_f1")],
        title="Every method on this task, all three metrics", height=360)

    return html.Div([
        instruction_card(req, size="17px"),
        card([
            eyebrow("SCORES ON THIS TASK"),
            graph(chart),
            note("Diff F1 compares your change to the human's change, so "
                 "anything correctly left alone cancels out. A no-op and a "
                 "rebuild from scratch both land near zero, which is why the "
                 "other two metrics can look healthy while diff F1 does not.",
                 marginTop="6px"),
        ]),
        card([
            eyebrow("THE TASK · WHAT GOES IN AND WHAT THE HUMAN DID"),
            html.Div([html.Div(p, style={"flex": "0 1 320px", "minWidth": "0"})
                      for p in given],
                     style={"display": "flex", "gap": "12px",
                            "justifyContent": "center", "flexWrap": "wrap"}),
            eyebrow("WHAT EACH METHOD PRODUCED", marginTop="20px"),
            grid(answers, min_width="230px", gap="12px"),
        ]),
    ])


# --------------------------------------------------------------------------
# general
# --------------------------------------------------------------------------
def _run_universe():
    """The tasks we have our own score for. Every baseline is averaged over
    exactly this list, never over its own full 48, so the comparison is like
    for like."""
    return our_task_ids()


def our_tokens(rid):
    kind, payload = latest_source(rid)
    if not kind:
        return None
    v = ((payload.get("tokens") or {}).get("total_tokens")
         if kind == "record" else payload.get("total_tokens"))
    return int(v) if isinstance(v, (int, float)) else None


def _method_series(rids, metric="diff_f1"):
    """{method label: [value per rid]} for every method plus ours, with None
    where a method has no score for that task."""
    out = {OURS: [our_score(r, metric) for r in rids]}
    for user in METHODS:
        out[method_label(user)] = [score_for(r, user, metric)[0] for r in rids]
    return out


def _mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return statistics.fmean(vals) if vals else None


def general_results():
    rids = _run_universe()
    if not rids:
        return card(note("No saved runs yet. Run a task from tab 4, or "
                         "`python tools/run_headless.py --todo 5`, and this "
                         "view fills in."))

    diffs = {r: REQUESTS[r].get("difficulty", "?") for r in rids}
    by_method = _method_series(rids, "diff_f1")
    labels = [OURS] + [method_label(u) for u in METHODS]
    colours = method_colors()
    at = {r: i for i, r in enumerate(rids)}

    # ---- headline -------------------------------------------------------
    ours_vals = [v for v in by_method[OURS] if v is not None]
    # the second human is the metric's agreement ceiling, not a competitor, so
    # the win count and the parity plot are against the models
    model_labels = [method_label(u) for u in METHODS if u != "other human"]
    per_task_best = []
    wins = ties = losses = 0
    for i, r in enumerate(rids):
        base = [by_method[l][i] for l in model_labels]
        base = [v for v in base if v is not None]
        ours = by_method[OURS][i]
        if not base or ours is None:
            continue
        b = max(base)
        per_task_best.append({"x": b, "y": ours, "label": _short(r),
                              "difficulty": diffs[r]})
        if ours > b + 1e-9:
            wins += 1
        elif ours < b - 1e-9:
            losses += 1
        else:
            ties += 1

    costs = [c for c in (our_cost(r) for r in rids) if c is not None]
    best_label, best_mean = best_model_baseline(rids)
    tiles = [
        kpi("TASKS SCORED", f"{len(rids)}",
            f"of {len(REQUESTS)} in the benchmark"),
        kpi("OUR MEAN DIFF F1",
            f"{statistics.fmean(ours_vals):.3f}" if ours_vals else "n/a",
            "across those tasks", color=ACCENT),
        kpi("BEST BENCHMARK MODEL",
            f"{best_mean:.3f}" if best_mean is not None else "n/a",
            f"{best_label}, same tasks" if best_label else ""),
        kpi("BEATS EVERY MODEL", f"{wins}",
            f"{losses} lost, {ties} tied, task by task",
            color=GOOD if wins > losses else FG),
        kpi("SOLVED OUTRIGHT", f"{sum(1 for v in ours_vals if v >= 0.6)}",
            "diff F1 at or above 0.60"),
        kpi("TOTAL SPEND", f"${sum(costs):.2f}" if costs else "n/a",
            f"${statistics.fmean(costs):.2f} per task" if costs else ""),
    ]

    # ---- mean per method ------------------------------------------------
    mean_vals = [_mean(by_method[l]) for l in labels]
    rank_fig = viz.ranked_bar(labels, mean_vals, colors=colours,
                              title="Mean diff F1 per method",
                              xtitle="mean diff F1", height=310)

    # ---- all three metrics per method -----------------------------------
    metric_series = []
    for m in ("diff_f1", "chamfer_similarity_norm", "volume_f1"):
        s = _method_series(rids, m)
        metric_series.append((METRIC_LABELS[m],
                              [_mean(s[l]) or 0.0 for l in labels]))
    metrics_fig = viz.grouped_metric_bar(
        labels, metric_series, title="All three metrics, averaged", height=340,
        ytitle="mean score")

    # ---- by difficulty ---------------------------------------------------
    bands = [b for b in viz.DIFF_ORDER if b in set(diffs.values())]
    band_series = []
    for l in labels:
        vals = []
        for b in bands:
            vals.append(_mean([by_method[l][j] for j, r in enumerate(rids)
                               if diffs[r] == b]) or 0.0)
        band_series.append((l, vals, colours[l]))
    band_fig = viz.grouped_by_difficulty(
        bands, band_series, title="Mean diff F1 by difficulty band", height=340)

    # ---- spread ----------------------------------------------------------
    strip_rows = []
    for l in labels:
        vals = [v for v in by_method[l] if isinstance(v, (int, float))]
        if vals:
            strip_rows.append((l, vals, colours[l]))
    strip_fig = viz.violin_group(
        strip_rows, highlight=OURS, height=430,
        title="Every task, every method, on one scale")

    # ---- parity ----------------------------------------------------------
    parity_fig = viz.parity_scatter(
        per_task_best, title="Us against the best model, task by task",
        height=400)

    # ---- cost ------------------------------------------------------------
    # one bubble per task: dollars across, score up, tokens as the area
    cost_points = [{"x": our_cost(r), "y": our_score(r, "diff_f1"),
                    "size": our_tokens(r) or 0,
                    "label": _short(r), "difficulty": diffs[r]}
                   for r in rids
                   if our_cost(r) is not None
                   and our_score(r, "diff_f1") is not None]
    cost_fig = viz.cost_density(
        cost_points,
        title="Where a task lands, by difficulty band", height=420)

    return html.Div([
        card([
            eyebrow("BENCHMARK SUMMARY"),
            html.Div(tiles, style={"display": "flex", "gap": "11px",
                                   "flexWrap": "wrap"}),
            note("One run per task, always the most recent one, so a task "
                 "counts once no matter how often it was rerun. Averages are "
                 "taken over the tasks we have run and every baseline is "
                 "averaged over exactly that same list, so the comparison is "
                 "like for like. The ground truth scored against itself is "
                 "excluded: it is 1.000 by construction.",
                 marginTop="14px"),
        ]),
        grid([
            card([graph(rank_fig)], marginBottom="0"),
            card([graph(parity_fig)], marginBottom="0"),
        ], min_width="430px"),
        html.Div(style={"height": "14px"}),
        grid([
            card([graph(metrics_fig)], marginBottom="0"),
            card([graph(band_fig)], marginBottom="0"),
        ], min_width="430px"),
        html.Div(style={"height": "14px"}),
        card([
            graph(strip_fig),
            note("One violin per method on the same scale, best on the left. "
                 "The body is where that method's tasks land, the dots are "
                 "the tasks themselves, and the box gives the quartiles and "
                 "median. Ours carries real mass at the top of the range "
                 "where every published model is pinned near zero, and the "
                 "second human is still above us.", marginTop="4px"),
        ]),
        card([
            eyebrow("COST"),
            graph(cost_fig),
            note("Each panel is one difficulty band. The shading is a binned "
                 "density over that band's tasks, so it says where a task of "
                 "that kind tends to land, and every task is drawn on top as "
                 "a point. Prices come from the per-run token counts and the "
                 "rates in src/config.py.", marginTop="6px"),
        ]),
        results_table(rids, by_method, labels, diffs, at),
    ])


def _short(rid):
    """A task id short enough for an axis tick, and still unique.

    A request id is `<part>_<timestamp>`, and one part carries up to 18 of the
    48 requests. Truncating to the part alone collapsed those onto a single
    category: Plotly then stacked every one of them on the same x position, so
    "attempts per task" read 45 attempts for a task that took 6.
    """
    head, _, tail = rid.partition("_")
    return f"{head[:6]}·{tail.split('.')[0][-5:]}"


def results_table(rids, by_method, labels, diffs, at):
    """The numbers behind every chart above, because a chart alone is not an
    accessible way to publish a result."""
    # bold on this table means "best benchmark model on that row", so the two
    # columns that are not published models have to be excluded from the
    # comparison and marked differently. The second human is the agreement
    # ceiling for the metric rather than a competitor, and our own run is not a
    # published baseline, so calling either one the best model would be a lie.
    model_labels = {method_label(u) for u in METHODS if u != "other human"}
    human_label = method_label("other human")
    ours_wash = "rgba(42, 120, 214, 0.07)"

    def col_style(label):
        """The wash and the italic that keep ours and the human off the claim
        that bold is making."""
        if label == OURS:
            return {"background": ours_wash}
        if label == human_label:
            return {"fontStyle": "italic"}
        return {}

    head = ["task", "difficulty", "instruction"] + labels + ["spend"]
    header = html.Tr([html.Th(h, style={
        "textAlign": "left" if h in ("task", "difficulty", "instruction")
        else "right",
        "padding": "8px 10px", "color": FAINT, "fontSize": "10.5px",
        "fontWeight": "700", "letterSpacing": "0.8px",
        "borderBottom": f"1px solid {BORDER}", "whiteSpace": "nowrap",
        "textTransform": "uppercase", **col_style(h)}) for h in head])

    rows = []
    order = sorted(rids, key=lambda r: -(our_score(r, "diff_f1") or -1))
    for r in order:
        cells = [
            html.Td(_short(r), style={"padding": "7px 10px",
                                      "fontFamily": MONO, "fontSize": "11px",
                                      "color": MUTED}),
            html.Td(pill(diffs[r], DIFF_COLOR.get(diffs[r], MUTED)),
                    style={"padding": "7px 10px"}),
            html.Td((REQUESTS[r].get("text") or "")[:64],
                    style={"padding": "7px 10px", "fontSize": "11.5px",
                           "color": FG, "maxWidth": "320px"}),
        ]
        row_vals = [by_method[l][at[r]] for l in labels]
        best = max([v for l, v in zip(labels, row_vals)
                    if l in model_labels and isinstance(v, (int, float))],
                   default=None)
        for l, v in zip(labels, row_vals):
            txt = f"{v:.3f}" if isinstance(v, (int, float)) else "·"
            is_best = l in model_labels and best is not None \
                and isinstance(v, (int, float)) and abs(v - best) < 1e-9
            cells.append(html.Td(txt, style={
                "padding": "7px 10px", "textAlign": "right",
                "fontVariantNumeric": "tabular-nums", "fontSize": "11.5px",
                "fontWeight": "700" if is_best else "500",
                "color": viz.score_color(v) if isinstance(v, (int, float))
                else FAINT, **col_style(l)}))
        c = our_cost(r)
        cells.append(html.Td(f"${c:.2f}" if c is not None else "·", style={
            "padding": "7px 10px", "textAlign": "right", "color": MUTED,
            "fontSize": "11.5px", "fontVariantNumeric": "tabular-nums"}))
        rows.append(html.Tr(cells, style={
            "borderBottom": f"1px solid {BORDER}"}))

    return card([
        eyebrow("EVERY NUMBER ON THIS PAGE"),
        note("Bold is the best benchmark model on that row. Our own column is "
             f"shaded and {human_label.lower()} is in italic because neither "
             "is a benchmark model: the second human is what two experts "
             "agree to, the ceiling for the metric, not a method in the race.",
             marginBottom="10px"),
        html.Div(html.Table([html.Thead(header), html.Tbody(rows)],
                            style={"borderCollapse": "collapse",
                                   "width": "100%"}),
                 style={"overflowX": "auto"}),
    ])



# --------------------------------------------------------------------------
# tab 7 — the five tasks the competition singled out, on one slide
# --------------------------------------------------------------------------
# Fixed by the organisers, not chosen by us, which is the point of showing them
# together: the set was picked before any of these numbers existed.
COMPETITION_IDS = [
    "SUJ2G2UMJQR7PMBX_1759209987.785593",
    "3YH2WFSRM22W7DKT_1769773335.525203",
    "B7A2N74ZJBF9MZHU_1770174133.012106",
    "F332D3FXML85WLR2_1769607142.566352",
    "ZK22J6VYRKQ2RTFD_1758874422.1403751",
]


def _comp_panel(title, colour, iso, score, stl=None, emphasis=False):
    """One method's answer to one task, small enough to sit five across.

    Only the ground truth and our own result carry an interactive mesh. Seven
    viewers on each of five tasks is thirty five meshes in one response, which
    is more Plotly JSON than the dev server will push before macOS kills the
    write; the rendered iso is the same geometry seen from the same camera.
    """
    body = [
        html.Div(pill(title, colour), style={"marginBottom": "6px"}),
        iso if iso is not None else html.Div(
            "no geometry", style={
                "height": "116px", "display": "flex", "alignItems": "center",
                "justifyContent": "center", "color": FAINT,
                "fontSize": "11px", "background": TILE,
                "borderRadius": "6px", "textAlign": "center"}),
    ]
    if stl is not None:
        body.append(graph(mesh_figure(stl, colour, "", height=170,
                                      max_tris=2500)))
    # the input and the ground truth carry no score of their own: the input is
    # what everyone started from and the ground truth is what they are scored
    # against, so a number under either would be a number about nothing
    if isinstance(score, (int, float)):
        body.append(html.Div([
            html.Span("diff F1 ", style={"color": FAINT, "fontSize": "10px",
                                         "fontWeight": "700",
                                         "letterSpacing": "0.6px"}),
            html.Span(f"{score:.3f}", style={
                "color": viz.score_color(score), "fontSize": "17px",
                "fontWeight": "700", "fontVariantNumeric": "tabular-nums"}),
        ], style={"marginTop": "7px"}))
    return viewer_panel(body, padding="10px", **(
        {"border": f"1.5px solid {colour}",
         "boxShadow": "0 0 0 3px rgba(42,120,214,0.10)"} if emphasis else {}))


def _comp_row(rid):
    """One competition task: the instruction, then every method side by side."""
    req = REQUESTS[rid]
    avail = EDITS_BY_REQUEST.get(rid, {})
    start = req.get("brep_start")
    gt_id = avail.get("GROUND TRUTH")
    kind, payload = latest_source(rid)
    dest = (payload.get("dest") if kind == "record"
            else sweep_dest(payload) if kind else None)
    colours = method_colors()
    ours = our_score(rid, "diff_f1")
    diff = req.get("difficulty", "?")

    panels = [
        _comp_panel("INPUT", MESH_INPUT, _iso(_dataset_iso_path(start), "116px"),
                    None),
        _comp_panel("GROUND TRUTH", MESH_GT,
                    _iso(_dataset_iso_path(gt_id), "116px"), None,
                    stl=geom_path(gt_id, "stl")),
        _comp_panel("OURS", MESH_PRED,
                    _iso(our_view_path(dest, "toprightiso"), "116px"), ours,
                    stl=our_output_stl(dest), emphasis=True),
    ]
    for user in METHODS:
        v, _failed = score_for(rid, user, "diff_f1")
        brep = avail.get(user)
        panels.append(_comp_panel(
            method_label(user).upper(),
            colours.get(method_label(user), viz.BASE_COLOR),
            _iso(_dataset_iso_path(brep), "116px"), v))

    return card([
        html.Div([
            pill(diff.upper(), DIFF_COLOR.get(diff, MUTED)),
            html.Span(f"“{req.get('text', '')}”", style={
                "color": FG, "fontSize": "15px", "fontWeight": "500",
                "marginLeft": "12px"}),
            html.Span(_short(rid), style={
                "color": FAINT, "fontSize": "11px", "fontFamily": MONO,
                "marginLeft": "auto", "whiteSpace": "nowrap"}),
        ], style={"display": "flex", "alignItems": "baseline", "gap": "6px",
                  "flexWrap": "wrap", "marginBottom": "11px"}),
        grid(panels, min_width="168px", gap="9px"),
    ])


def competition_tab():
    ids = [r for r in COMPETITION_IDS if r in REQUESTS]
    if not ids:
        return card(note("None of the competition request ids are in this "
                         "dataset build."))

    labels = [_short(r) for r in ids]
    names = [OURS] + [method_label(u) for u in METHODS]
    colours = method_colors()
    series = []
    for name in names:
        vals = []
        for r in ids:
            if name == OURS:
                v = our_score(r, "diff_f1")
            else:
                user = next(u for u in METHODS if method_label(u) == name)
                v, _f = score_for(r, user, "diff_f1")
            vals.append(float(v) if isinstance(v, (int, float)) else 0.0)
        series.append((name, vals, colours[name]))

    ours_vals = [v for v in (our_score(r, "diff_f1") for r in ids)
                 if v is not None]
    best_name, best_mean = best_model_baseline(ids)
    human = human_ceiling(ids)
    costs = [c for c in (our_cost(r) for r in ids) if c is not None]
    tiles = [
        kpi("EXAMPLES", f"{len(ids)}", "chosen by the organisers"),
        kpi("OUR MEAN DIFF F1",
            f"{statistics.fmean(ours_vals):.3f}" if ours_vals else "n/a",
            "over these five", color=ACCENT),
        kpi("BEST BENCHMARK MODEL",
            f"{best_mean:.3f}" if best_mean is not None else "n/a",
            f"{best_name}, same five" if best_name else ""),
        kpi("SECOND HUMAN", f"{human:.3f}" if human is not None else "n/a",
            "the agreement ceiling"),
        kpi("SPEND", f"${sum(costs):.2f}" if costs else "n/a",
            f"${statistics.fmean(costs):.2f} per task" if costs else ""),
    ]

    return html.Div([
        card([
            eyebrow("COMPETITION SELECTED TEST EXAMPLES"),
            note("The five request ids the organisers named. Every method's "
                 "answer to each one is below, with the part it started from "
                 "and the edit the human expert made.", marginBottom="14px"),
            html.Div(tiles, style={"display": "flex", "gap": "11px",
                                   "flexWrap": "wrap"}),
            html.Div(style={"height": "14px"}),
            graph(viz.grouped_by_difficulty(
                labels, series, height=330, ytitle="diff F1",
                title="Diff F1 on each of the five, every method")),
        ]),
        *[_comp_row(r) for r in ids],
    ])


if __name__ == "__main__":
    print(f"loaded {len(REQUESTS)} requests, {len(EDITS)} edits")
    print(f"our pipeline: {'available' if OURS_AVAILABLE else OURS_IMPORT_ERROR}")
    print("open http://127.0.0.1:8050")
    app.run(debug=False, port=8050)
