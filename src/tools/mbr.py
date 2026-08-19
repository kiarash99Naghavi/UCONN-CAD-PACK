"""Minimum Bayes Risk selection over candidate GEOMETRY.

When a sub-goal produces several candidates, the router has to keep one. Ranking
them by verdict-then-recency loses: measured across this project's runs, the
pipeline shipped a worse result than one it had already built on 7 of 48 tasks
(0.592 diff_f1). This module picks by CONSENSUS instead — the candidate that most
agrees with the others — which needs no ground truth and no model call.

    c* = argmax_c  sum_{c' != c}  u(c, c')

The idea is MBR-Exec (Shi et al., "Natural Language to Code Translation with
Execution"), which selects the program whose EXECUTION OUTPUT agrees most with its
peers. Here a script's execution output is a SOLID, so `u` is a geometric kernel
rather than discrete output matching. Two properties make that more than an analogy:

  * `diff_f1` — the metric this project is graded on — is itself a voxel-set
    comparison against an unseen reference. Using the same voxel kernel for `u`
    makes the selection objective consistent with the scoring function: MBR
    maximises a proxy for the score, with the candidate pool standing in for the
    reference it is not allowed to see.
  * the kernel is computed on the EDIT, xor(start, candidate), never on the whole
    part. On a 99%-unchanged solid a part-level IoU is ~1.0 for every candidate and
    carries no signal at all; the edit mask is nearly all signal, and it mirrors how
    `diff_f1` is constructed.

Validated offline before being wired in — see `tools/mbr_offline.py` and
`handoff/mbr/`. On this project's archived candidates it never picked a worse
candidate than the one shipped (1 win / 13 ties / 0 losses on contested groups,
86% argmax accuracy), but it abstains often; see `select()` for why.
"""

import os
import os.path as osp

import numpy as np

from . import render as our_render

VOXEL_DIVISOR = 128
# Above this utility two candidates are the same answer, not a second vote.
DUP_EPS = 0.99
# Below three DISTINCT answers the objective cannot discriminate — see select().
MIN_DISTINCT = 3


def _mesh(path):
    import open3d as o3d
    m = o3d.io.read_triangle_mesh(path)
    return m if len(m.vertices) > 0 else None


def _stl_for(step_path, work_dir):
    """Mesh a candidate .step, reusing the cached STL the scorer may have made."""
    cached = osp.join(osp.splitext(step_path)[0] + "_gt", "tmp.stl")
    if osp.exists(cached):
        return cached
    os.makedirs(work_dir, exist_ok=True)
    out = osp.join(work_dir, osp.basename(osp.splitext(step_path)[0]) + ".stl")
    if not osp.exists(out):
        our_render.export_stl(step_path, out)
    return out if osp.exists(out) else None


def edit_masks(start_step, cand_steps, work_dir):
    """xor(start, candidate) for each candidate, on ONE shared voxel frame.

    The frame is sized from the START solid alone — never from a reference, which a
    selector is not allowed to see. Candidate bounds still participate so nothing
    falls outside the grid.
    """
    from src.utils import evals_diff as ed

    s_stl = _stl_for(start_step, work_dir)
    c_stls = [_stl_for(p, work_dir) for p in cand_steps]
    if not s_stl or any(x is None for x in c_stls):
        return None
    sm = _mesh(s_stl)
    cms = [_mesh(x) for x in c_stls]
    if sm is None or any(m is None for m in cms):
        return None

    vs, lo, hi = ed._shared_voxel_frame([sm] + cms, VOXEL_DIVISOR, size_meshes=[sm])
    if vs <= 0:
        return None
    dims = ed._grid_dims(vs, lo, hi)
    if np.prod(dims, dtype=float) > getattr(ed, "_MAX_GRID_VOXELS", 1e9):
        return None
    S = ed._occupancy_mask(sm, vs, lo, hi, dims, None)
    return [np.logical_xor(S, ed._occupancy_mask(m, vs, lo, hi, dims, None))
            for m in cms]


def utility(a, b, kernel="iou"):
    """Similarity between two edit masks. Both empty = identical answers."""
    inter = int(np.logical_and(a, b).sum())
    na, nb = int(a.sum()), int(b.sum())
    if na == 0 and nb == 0:
        return 1.0
    if kernel == "f1":
        return 2.0 * inter / (na + nb) if (na + nb) else 0.0
    union = int(np.logical_or(a, b).sum())
    return inter / union if union else 0.0


def _clusters(masks, kernel):
    """Group candidates whose edits are effectively identical.

    MBR treats agreement as evidence, which is only valid for independent draws.
    These are not: attempts 2 and 3 are refinements conditioned on QA's critique of
    attempt 1, and the router has an explicit "reproduced already-rejected geometry"
    escalation because the same solid genuinely recurs. Uncollapsed, two copies of
    one wrong answer outvote a unique right answer purely by being duplicated —
    measured on this project's own candidates, where a group scoring
    0.7911|0.7911|0.9029 selected 0.7911.
    """
    out = []
    for i in range(len(masks)):
        for cl in out:
            if utility(masks[i], masks[cl[0]], kernel) >= DUP_EPS:
                cl.append(i)
                break
        else:
            out.append([i])
    return out


def select(start_step, cand_steps, work_dir, kernel="iou"):
    """Pick the consensus candidate, or abstain.

    Returns {"index", "consensus", "n_distinct", "abstained", "reason"}. `index` is
    None when the selector declines, and the caller must then keep whatever it would
    have kept anyway.

    It abstains below three DISTINCT answers because the objective is degenerate
    there: with two candidates u(a,b) == u(b,a), so the consensus is identical for
    both and `argmax` would be decided by list order rather than by geometry. That
    is not a conservative choice, it is the only correct one — an earlier version
    without this guard picked the earliest (usually rejected) attempt every time and
    scored 0.177 BELOW what the pipeline shipped.
    """
    n = len(cand_steps)
    if n < 2:
        return {"index": None, "consensus": [], "n_distinct": n,
                "abstained": True, "reason": "fewer than 2 candidates"}

    masks = edit_masks(start_step, cand_steps, work_dir)
    if masks is None:
        return {"index": None, "consensus": [], "n_distinct": None,
                "abstained": True, "reason": "could not voxelise candidates"}

    cl = _clusters(masks, kernel)
    if len(cl) < MIN_DISTINCT:
        return {"index": None, "consensus": [0.0] * n, "n_distinct": len(cl),
                "abstained": True,
                "reason": f"only {len(cl)} distinct answer(s); MBR needs "
                          f"{MIN_DISTINCT}"}

    heads = [c[0] for c in cl]
    U = np.zeros((len(heads), len(heads)))
    for a in range(len(heads)):
        for b in range(a + 1, len(heads)):
            u = utility(masks[heads[a]], masks[heads[b]], kernel)
            U[a, b] = U[b, a] = u
    cons_cluster = U.sum(axis=1)
    best = int(np.argmax(cons_cluster))

    consensus = [0.0] * n
    for ci, group in enumerate(cl):
        for i in group:
            consensus[i] = float(cons_cluster[ci])
    return {"index": heads[best],
            "consensus": [round(c, 4) for c in consensus],
            "n_distinct": len(cl), "abstained": False,
            "reason": f"consensus over {len(cl)} distinct answers"}
