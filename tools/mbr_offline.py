"""Offline selector study: what SHOULD the pipeline have shipped?

The pipeline generates up to three candidate solids per sub-goal and keeps one.
Measured across this project's runs, the kept one is often not the best one it
built — ~1.9 diff_f1 of "selection regret" on six runs, including a task that
built 0.9898 and shipped 0.0000. That is a SELECTION failure, not a generation
failure, and it can be studied without generating anything new: every candidate
solid, its renders and its ground-truth score are already on disk.

This script re-selects from those existing candidates under several policies and
scores each policy against ground truth.

  shipped   what the pipeline actually kept (the baseline)
  mbr       Minimum Bayes Risk over candidate GEOMETRY, no ground truth used
  oracle    max over candidates — the ceiling any selector could reach
  random    mean over candidates — the floor, i.e. picking blind

MBR (Shi et al., "Natural Language to Code Translation with Execution") picks the
candidate with the highest expected utility against the others:

    c* = argmax_c  sum_{c' != c}  u(c, c')

The adaptation to CAD is the whole point: a CadQuery script's execution output is
a SOLID, so u is a geometric kernel rather than discrete output matching. Two
properties make this more than an analogy:

  * `diff_f1` is itself a voxel-set metric against an unseen reference, so using
    the SAME voxel kernel for u makes the selection objective consistent with the
    scoring function — MBR maximises a proxy for the very thing being graded,
    with the candidate pool standing in for the unavailable reference.
  * the kernel is computed on the EDIT, xor(start, candidate), not on the part.
    On a 99%-unchanged solid a part-level IoU is ~1.0 for every candidate and
    carries no signal at all; the edit-level mask is nearly all signal, and it
    mirrors how `diff_f1` is constructed.

Ground truth is used ONLY to score the policies afterwards, never to choose.

Usage:
    python tools/mbr_offline.py                # MBR + oracle, no API calls
    python tools/mbr_offline.py --limit 5      # quick pass over 5 groups
"""

import argparse
import csv
import json
import os
import os.path as osp
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, osp.dirname(osp.dirname(osp.abspath(__file__))))

from src.utils import evals_diff as ed          # noqa: E402
from src import config as our_config   # noqa: E402
from src import evaluate as ev    # noqa: E402

OUT_DIR = osp.join(our_config.RESULTS, "mbr")
VOXEL_DIVISOR = 128
# Below this the two candidates are the same answer for scoring purposes, so the
# group cannot discriminate between selectors and is reported separately.
CONTESTED_EPS = 0.01


# ---------------------------------------------------------------------------
# gathering candidates
# ---------------------------------------------------------------------------
def cached_stl(step_path):
    """The STL `_attempt_gt_scores` already exported next to a candidate .step."""
    return osp.join(osp.splitext(step_path)[0] + "_gt", "tmp.stl")


def collect_groups():
    """{(request_id, sub_goal): [candidate, ...]} for every executed candidate.

    A candidate counts only when it left geometry AND carries a ground-truth
    score, because a policy can only be graded on candidates that can be graded.
    """
    groups = defaultdict(list)
    for p in sorted(glob_json(osp.join(our_config.RESULTS, "dashboard_runs"))):
        rid = osp.basename(p)[:-5]
        try:
            rec = json.load(open(p))
        except Exception:
            continue
        shipped_run = (rec.get("scores") or {}).get("diff_f1")
        for s in (rec.get("steps") or []):
            step = s.get("step")
            gt = (s.get("gt_scores") or {}).get("diff_f1")
            if not step or gt is None or not osp.exists(step):
                continue
            groups[(rid, s.get("sub"))].append({
                "attempt": s.get("attempt"),
                "verdict": s.get("verdict"),
                "step": step,
                "stl": cached_stl(step),
                "gt": float(gt),
                "shipped_run": shipped_run,
            })
    return groups


def glob_json(d):
    import glob as _g
    return _g.glob(osp.join(d, "*.json"))


def shipped_pick(cands):
    """Which candidate did the pipeline carry forward from this sub-goal?

    The step records do not name it directly, so it is reconstructed from the
    verdicts using the router's own precedence: a full acceptance wins, else the
    last kept partial. When neither exists nothing was kept — the run went on to
    a replan or the one-shot fallback — and the group has no shipped candidate.
    """
    acc = [c for c in cands if c["verdict"] == "accepted"]
    if acc:
        return max(acc, key=lambda c: c["attempt"] or 0), "accepted"
    par = [c for c in cands if c["verdict"] == "partial"]
    if par:
        return max(par, key=lambda c: c["attempt"] or 0), "partial"
    return None, "none-kept"


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
def edit_masks(start_mesh, cand_meshes):
    """xor(start, candidate) for each candidate, on ONE shared voxel frame.

    The frame is sized from the START solid alone — deliberately NOT from the
    ground truth. `diff_f1` sizes its grid on [start, gt] because it is allowed
    to see the reference; a selector is not. Candidate bounds still participate
    so nothing falls outside the grid.
    """
    vs, lo, hi = ed._shared_voxel_frame(
        [start_mesh] + cand_meshes, VOXEL_DIVISOR, size_meshes=[start_mesh])
    if vs <= 0:
        return None, None
    dims = ed._grid_dims(vs, lo, hi)
    if np.prod(dims, dtype=float) > getattr(ed, "_MAX_GRID_VOXELS", 1e9):
        return None, None
    S = ed._occupancy_mask(start_mesh, vs, lo, hi, dims, None)
    out = []
    for m in cand_meshes:
        C = ed._occupancy_mask(m, vs, lo, hi, dims, None)
        out.append(np.logical_xor(S, C))
    return out, vs


def pair_utility(a, b, kernel):
    """Similarity between two EDIT masks."""
    inter = int(np.logical_and(a, b).sum())
    na, nb = int(a.sum()), int(b.sum())
    if na == 0 and nb == 0:
        return 1.0            # both changed nothing: identical answers
    if kernel == "f1":        # mirrors diff_f1's own form
        return 2.0 * inter / (na + nb) if (na + nb) else 0.0
    union = int(np.logical_or(a, b).sum())
    return inter / union if union else 0.0


DUP_EPS = 0.99      # utility above this = the same answer, not a second vote


def dedup_clusters(masks, kernel):
    """Group candidates whose edits are effectively identical.

    MBR treats agreement as evidence, which is only valid when the candidates
    are independent draws. These are NOT: attempts 2 and 3 are refinements
    conditioned on QA's critique of attempt 1, and the router has an explicit
    "reproduced already-rejected geometry" escalation because the same solid
    genuinely recurs. Left uncollapsed, two copies of one wrong answer outvote
    one unique right answer purely by being duplicated.

    Measured on this project's own candidates: a group scoring
    0.7911|0.7911|0.9029 (edit voxels 2190|2190|3094) had MBR select 0.7911,
    and 0.9029|0.7356|0.7356 selected 0.7356. Both are the duplicate pair
    winning on count. Collapsing them to one vote each is standard MBR practice
    and is what makes the consensus a vote over DISTINCT answers.
    """
    reps = []                       # cluster -> list of candidate indices
    for i in range(len(masks)):
        for cl in reps:
            if pair_utility(masks[i], masks[cl[0]], kernel) >= DUP_EPS:
                cl.append(i)
                break
        else:
            reps.append([i])
    return reps


def mbr_pick(masks, kernel, dedup=True):
    """argmax over DISTINCT answers of summed utility against the other answers.

    Returns (chosen_index, consensus_per_candidate, n_distinct). `n_distinct`
    matters: with fewer than three distinct answers the objective is degenerate
    — at two, u(a,b) == u(b,a) makes the consensus identical for both and the
    argmax is decided by list order, not by geometry — so the caller abstains.
    """
    n = len(masks)
    if n == 1:
        return 0, [0.0], 1

    clusters = dedup_clusters(masks, kernel) if dedup else [[i] for i in range(n)]
    n_distinct = len(clusters)
    if n_distinct < 3:
        return None, [0.0] * n, n_distinct

    heads = [cl[0] for cl in clusters]
    U = np.zeros((len(heads), len(heads)))
    for a in range(len(heads)):
        for b in range(a + 1, len(heads)):
            u = pair_utility(masks[heads[a]], masks[heads[b]], kernel)
            U[a, b] = U[b, a] = u
    cons_cluster = U.sum(axis=1)
    best_cluster = int(np.argmax(cons_cluster))

    # spread the cluster's consensus back onto its members for reporting
    consensus = [0.0] * n
    for ci, cl in enumerate(clusters):
        for i in cl:
            consensus[i] = float(cons_cluster[ci])
    return heads[best_cluster], consensus, n_distinct


# ---------------------------------------------------------------------------
# verification — the load-bearing check
# ---------------------------------------------------------------------------
def verify_metric(rid, cands, db, limit=2):
    """Recompute diff_f1 the way the benchmark does and compare to the stored value.

    If this disagrees, the voxel frame here is not the benchmark's and every
    number this script prints downstream is meaningless.
    """
    gt_rel, start_rel = ev._gt_and_start(rid, db)
    if not gt_rel:
        return []
    ed.clear_start_cache()
    sm = ed._load_mesh(start_rel, db)
    gm = ed._load_mesh(gt_rel, db)
    if sm is None or gm is None:
        return []
    out = []
    for c in cands[:limit]:
        pm = ed._load_mesh(c["stl"], db)
        if pm is None:
            continue
        vs, lo, hi = ed._shared_voxel_frame([sm, gm, pm], VOXEL_DIVISOR,
                                            size_meshes=[sm, gm])
        dims = ed._grid_dims(vs, lo, hi)
        S = ed._occupancy_mask(sm, vs, lo, hi, dims, None)
        G = ed._occupancy_mask(gm, vs, lo, hi, dims, None)
        P = ed._occupancy_mask(pm, vs, lo, hi, dims, None)
        got = ed._f1_from_masks(np.logical_xor(S, G), np.logical_xor(S, P))
        out.append(abs(got - c["gt"]))
    return out


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="only N groups")
    ap.add_argument("--kernels", default="iou,f1")
    args = ap.parse_args()

    kernels = [k.strip() for k in args.kernels.split(",") if k.strip()]
    os.makedirs(OUT_DIR, exist_ok=True)
    db = ev._db()

    groups = collect_groups()
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    print(f"groups with >=2 scored candidates: {len(multi)} "
          f"(of {len(groups)} total)")

    rows, skipped, verif = [], [], []
    items = sorted(multi.items())
    if args.limit:
        items = items[:args.limit]

    for gi, ((rid, sub), cands) in enumerate(items, 1):
        cands = sorted(cands, key=lambda c: c["attempt"] or 0)
        missing = [c for c in cands if not osp.exists(c["stl"])]
        if missing:
            skipped.append((rid, sub, f"{len(missing)} candidate(s) without cached STL"))
            continue
        try:
            gt_rel, start_rel = ev._gt_and_start(rid, db)
            ed.clear_start_cache()
            sm = ed._load_mesh(start_rel, db)
            meshes = [ed._load_mesh(c["stl"], db) for c in cands]
            if sm is None or any(m is None for m in meshes):
                skipped.append((rid, sub, "mesh load failed"))
                continue
            masks, vs = edit_masks(sm, meshes)
            if masks is None:
                skipped.append((rid, sub, "voxel frame unusable"))
                continue
        except Exception as e:
            skipped.append((rid, sub, f"{type(e).__name__}: {e}"))
            continue

        if gi <= 5:                       # verify the metric on the first groups
            verif += verify_metric(rid, cands, db)

        gts = [c["gt"] for c in cands]
        oracle = max(gts)
        rnd = float(np.mean(gts))
        ship, ship_kind = shipped_pick(cands)
        ship_gt = ship["gt"] if ship else None
        spread = oracle - min(gts)

        row = {
            "request_id": rid, "sub": sub, "n_candidates": len(cands),
            "attempts": "|".join(str(c["attempt"]) for c in cands),
            "verdicts": "|".join(str(c["verdict"]) for c in cands),
            "gt_scores": "|".join(f"{g:.4f}" for g in gts),
            "edit_voxels": "|".join(str(int(m.sum())) for m in masks),
            "oracle": round(oracle, 6), "random": round(rnd, 6),
            "spread": round(spread, 6),
            "shipped_kind": ship_kind,
            "shipped": None if ship_gt is None else round(ship_gt, 6),
            "shipped_attempt": ship["attempt"] if ship else None,
            "contested": spread > CONTESTED_EPS,
        }
        for k in kernels:
            idx, cons, n_distinct = mbr_pick(masks, k)
            row[f"mbr_{k}_distinct"] = n_distinct
            if idx is None:
                # Fewer than three distinct answers: the objective cannot
                # discriminate. Abstain and keep whatever the pipeline shipped,
                # which is what a deployed selector would do.
                row[f"mbr_{k}_abstained"] = True
                row[f"mbr_{k}"] = None if ship_gt is None else round(ship_gt, 6)
                row[f"mbr_{k}_attempt"] = ship["attempt"] if ship else None
                row[f"mbr_{k}_is_argmax"] = (
                    bool(abs(ship_gt - oracle) < 1e-9) if ship_gt is not None else False)
            else:
                row[f"mbr_{k}_abstained"] = False
                row[f"mbr_{k}"] = round(cands[idx]["gt"], 6)
                row[f"mbr_{k}_attempt"] = cands[idx]["attempt"]
                row[f"mbr_{k}_is_argmax"] = bool(abs(cands[idx]["gt"] - oracle) < 1e-9)
            row[f"mbr_{k}_consensus"] = "|".join(f"{c:.3f}" for c in cons)
        rows.append(row)
        def _fmt(v):
            return "  n/a " if v is None else f"{v:.4f}"
        print(f"  [{gi}/{len(items)}] {rid[:28]} sub{sub} n={len(cands)} "
              f"oracle={_fmt(oracle)} shipped={_fmt(ship_gt)} "
              + " ".join(
                  f"mbr_{k}={_fmt(row['mbr_' + k])}"
                  f"{'(abst)' if row['mbr_' + k + '_abstained'] else ''}"
                  for k in kernels))

    db.close_connection()
    write_outputs(rows, skipped, verif, kernels)


def summarise(rows, kernels):
    """Aggregates, computed only over groups where a shipped candidate exists."""
    usable = [r for r in rows if r["shipped"] is not None]
    contested = [r for r in usable if r["contested"]]
    out = {
        "groups_total": len(rows),
        "groups_with_shipped": len(usable),
        "groups_contested": len(contested),
        "mean_oracle": _m(usable, "oracle"),
        "mean_shipped": _m(usable, "shipped"),
        "mean_random": _m(usable, "random"),
        "selectors": {},
    }
    for k in kernels:
        col = f"mbr_{k}"
        ent = {
            "mean": _m(usable, col),
            "mean_minus_shipped": round(_m(usable, col) - _m(usable, "shipped"), 6),
            "accuracy_picks_argmax": round(
                sum(1 for r in usable if r[f"{col}_is_argmax"]) / max(len(usable), 1), 4),
            "contested_mean": _m(contested, col),
            "contested_mean_minus_shipped": round(
                _m(contested, col) - _m(contested, "shipped"), 6),
            "contested_accuracy": round(
                sum(1 for r in contested if r[f"{col}_is_argmax"]) / max(len(contested), 1), 4),
            "wins": sum(1 for r in contested if r[col] > r["shipped"] + 1e-9),
            "ties": sum(1 for r in contested if abs(r[col] - r["shipped"]) <= 1e-9),
            "losses": sum(1 for r in contested if r[col] < r["shipped"] - 1e-9),
        }
        gap = _m(contested, "oracle") - _m(contested, "shipped")
        ent["recovered_oracle_gap"] = (
            round(ent["contested_mean_minus_shipped"] / gap, 4) if gap > 1e-9 else None)
        # Abstentions are not failures — they are the selector declining to act,
        # and are scored as "keep what shipped". Report them so a good headline
        # built on rarely acting is visible as such.
        ent["abstained"] = sum(1 for r in usable if r.get(f"{col}_abstained"))
        ent["abstained_contested"] = sum(1 for r in contested if r.get(f"{col}_abstained"))
        acted = [r for r in contested if not r.get(f"{col}_abstained")]
        ent["acted_contested"] = len(acted)
        ent["acted_mean_minus_shipped"] = (
            round(_m(acted, col) - _m(acted, "shipped"), 6) if acted else None)
        ent["acted_accuracy"] = (
            round(sum(1 for r in acted if r[f"{col}_is_argmax"]) / len(acted), 4)
            if acted else None)
        out["selectors"][col] = ent
    # bias- vs variance-limited: a low ceiling means no selector can help
    out["bias_limited_groups"] = sum(1 for r in contested if r["oracle"] < 0.1)
    out["variance_limited_groups"] = sum(
        1 for r in contested if r["oracle"] >= 0.1 and r["shipped"] < r["oracle"] - 0.05)

    # Groups where NOTHING was kept. These are excluded from every table above
    # because there is no shipped candidate to compare against — but they are
    # exactly the runs that fell through to the one-shot fallback or shipped the
    # unedited input, i.e. the expensive failures. A selector that is allowed to
    # ship a REJECTED candidate would operate here and nowhere else.
    nk = [r for r in rows if r["shipped_kind"] == "none-kept"]
    out["none_kept_groups"] = len(nk)
    out["none_kept_mean_oracle"] = _m(nk, "oracle")

    # Safety: a group whose candidates all score 0 while the run scored well is
    # a task where shipping the UNEDITED input is correct (a zero-voxel
    # ground-truth diff scores 1.0 for "changed nothing"). Any selector forced
    # to pick a candidate would score 0 there. Counted, because it is the one
    # way this idea can actively destroy a good result.
    out["selector_would_harm_groups"] = sum(
        1 for r in nk if r["oracle"] < 1e-9)
    return out


def _m(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(float(np.mean(vals)), 6) if vals else 0.0


def write_outputs(rows, skipped, verif, kernels):
    if rows:
        cols = list(rows[0].keys())
        with open(osp.join(OUT_DIR, "mbr_groups.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    summary = summarise(rows, kernels)
    summary["skipped"] = [{"request_id": a, "sub": b, "why": c} for a, b, c in skipped]
    summary["metric_verification"] = {
        "candidates_checked": len(verif),
        "max_abs_delta_vs_stored_diff_f1": (max(verif) if verif else None),
        "passed": (max(verif) < 1e-6) if verif else None,
    }
    with open(osp.join(OUT_DIR, "mbr_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    L = ["# Offline selector study — MBR vs what the pipeline shipped", "",
         "Candidates, geometry and ground-truth scores all pre-existed; nothing was",
         "regenerated. Ground truth is used only to grade the selectors, never to choose.", ""]
    v = summary["metric_verification"]
    L += [f"**Metric check**: recomputed `diff_f1` on {v['candidates_checked']} candidates, "
          f"max deviation from the stored value **{v['max_abs_delta_vs_stored_diff_f1']}** "
          f"→ {'PASS' if v['passed'] else 'FAIL'}.", ""]
    L += [f"- groups with >=2 scored candidates: **{summary['groups_total']}**",
          f"- with a reconstructable shipped pick: **{summary['groups_with_shipped']}**",
          f"- contested (candidates differ by >{CONTESTED_EPS}): **{summary['groups_contested']}**",
          f"- skipped: **{len(summary['skipped'])}**", "",
          "## Means over groups with a shipped pick", "",
          f"| selector | mean diff_f1 |", "|---|---|",
          f"| random (floor) | {summary['mean_random']:.4f} |",
          f"| **shipped** (today) | **{summary['mean_shipped']:.4f}** |"]
    for k in kernels:
        L.append(f"| mbr-{k} | {summary['selectors']['mbr_'+k]['mean']:.4f} |")
    L.append(f"| oracle (ceiling) | {summary['mean_oracle']:.4f} |")
    L += ["", "## On contested groups only", "",
          "| selector | mean | vs shipped | picks argmax | W/T/L | oracle gap recovered |",
          "|---|---|---|---|---|---|"]
    for k in kernels:
        e = summary["selectors"][f"mbr_{k}"]
        rec = e["recovered_oracle_gap"]
        rec_txt = "n/a" if rec is None else f"{rec * 100:.0f}%"
        L.append(f"| mbr-{k} | {e['contested_mean']:.4f} | "
                 f"{e['contested_mean_minus_shipped']:+.4f} | "
                 f"{e['contested_accuracy'] * 100:.0f}% | "
                 f"{e['wins']}/{e['ties']}/{e['losses']} | {rec_txt} |")
    L += ["", "## Where the selector actually acted", "",
          "Fewer than three DISTINCT candidate answers makes the MBR objective",
          "degenerate (at two, the consensus is symmetric and the pick is decided by",
          "list order), so it abstains and keeps what shipped. These rows separate",
          "'chose well' from 'declined to choose'.", "",
          "| selector | abstained (contested) | acted on | vs shipped when acting | accuracy when acting |",
          "|---|---|---|---|---|"]
    for k in kernels:
        e = summary["selectors"][f"mbr_{k}"]
        amd = e["acted_mean_minus_shipped"]
        acc = e["acted_accuracy"]
        L.append(f"| mbr-{k} | {e['abstained_contested']}/{summary['groups_contested']} | "
                 f"{e['acted_contested']} | "
                 f"{'n/a' if amd is None else f'{amd:+.4f}'} | "
                 f"{'n/a' if acc is None else f'{acc * 100:.0f}%'} |")
    L += ["", "## Bias vs variance", "",
          f"- bias-limited (oracle < 0.1 — no selector can help): "
          f"**{summary['bias_limited_groups']}**",
          f"- variance-limited (oracle >= 0.1 and shipped well below it): "
          f"**{summary['variance_limited_groups']}**", "",
          "## Groups where nothing was kept", "",
          "Excluded from every table above (no shipped candidate to compare against),",
          "but these are the runs that fell through to the one-shot fallback or shipped",
          "the unedited input. A selector allowed to ship a REJECTED candidate would",
          "operate here and essentially nowhere else.", "",
          f"- count: **{summary['none_kept_groups']}**",
          f"- mean best-candidate score available in them: "
          f"**{summary['none_kept_mean_oracle']:.4f}**", "",
          "### Safety counter-example", "",
          f"- groups where every candidate scores 0 but shipping the UNEDITED input is "
          f"correct: **{summary['selector_would_harm_groups']}**", "",
          "  On a task whose ground-truth diff is zero voxels, \"changed nothing\" scores",
          "  1.0 and any real edit scores 0.0. A selector forced to pick a candidate",
          "  destroys that result. Any deployed selector must keep \"ship nothing\" as a",
          "  candidate, not just the geometries it generated.", ""]
    with open(osp.join(OUT_DIR, "mbr_report.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nwrote {OUT_DIR}/mbr_groups.csv, mbr_summary.json, mbr_report.md")


if __name__ == "__main__":
    raise SystemExit(main())
