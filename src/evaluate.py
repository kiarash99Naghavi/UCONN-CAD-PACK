"""Scoring — runs the benchmark's own metric code against our outputs.

We import the repo's evals directly rather than reimplementing them, and call
them exactly as the benchmark's own eval does (run_feature_gt_similarity_eval
passes no extra kwargs, so pre_align stays at its default False), so our
numbers are directly comparable with the published baselines. Failures score
0.0, exactly as visualise_results.py:154 does.
"""

import json
import os.path as osp

from . import config

METRICS = ["chamfer_similarity_norm", "volume_f1", "diff_f1"]


def _db():
    from src.utils.db import DatabaseManager
    from src.utils.process_config import load_config
    return DatabaseManager(load_config(config.BENCH_CONFIG))


def _gt_and_start(request_id, db):
    """(gt_stl_rel, start_stl_rel) for a request, both relative to db.root_dir."""
    req = db.requests.find_one({"_id": request_id})
    if not req:
        raise ValueError(f"no such request: {request_id}")

    def stl_of(brep_id):
        b = db.breps.find_one({"_id": brep_id})
        if not b or not b.get("stl"):
            return None
        return b["stl"][0] if isinstance(b["stl"], list) else b["stl"]

    start = stl_of(req["brep_start"])
    gt = None
    for e in db.edits.find({"request": request_id}):
        if e["user"] == req["user"]:          # the request author's own edit
            gt = stl_of(e.get("brep_end"))
            break
    return gt, start


def score_output(request_id, out_dir, db=None):
    """Score one produced folder. `out_dir` holds tmp.stl.

    Returns {metric: value}; every metric is 0.0 when no geometry was produced,
    which is exactly how the benchmark treats a failed edit.
    """
    from src.utils import evals_diff as ed
    from src.utils import evals_feature_geometric as efg

    own = db is None
    db = db or _db()
    try:
        # Must be absolute: the repo's _resolve_mesh_path joins onto
        # db.root_dir, so a relative path silently resolves to the wrong file
        # and every metric comes back 0.0 looking like a genuine result.
        pred = osp.join(osp.abspath(out_dir), "tmp.stl")
        if not osp.exists(pred):
            return {m: 0.0 for m in METRICS} | {"failed": True}

        gt, start = _gt_and_start(request_id, db)
        if not gt:
            return {m: None for m in METRICS} | {"error": "no ground-truth edit"}

        ed.clear_start_cache()
        out = {}
        try:
            out["chamfer_similarity_norm"] = float(
                efg.chamfer_similarity_norm(gt, pred, db))
        except Exception as e:
            out["chamfer_similarity_norm"], out["chamfer_error"] = 0.0, str(e)
        for name, fn in (("volume_f1", ed.volumetric_f1), ("diff_f1", ed.diff_f1)):
            try:
                v = fn(gt, pred, db, **({"start_rel": start} if name == "diff_f1" else {}))
                out[name] = 0.0 if (v is None or v != v) else float(v)
            except Exception as e:
                out[name], out[f"{name}_error"] = 0.0, str(e)
        out["failed"] = False
        return out
    finally:
        if own:
            db.close_connection()


def score_run(user_id, write=True):
    """Score every output under results/runs/<user_id>/outputs.

    Writes results/scores/<user_id>.json and returns the summary.
    """
    root = osp.join(config.RESULTS, "runs", user_id, "outputs")
    if not osp.isdir(root):
        return {"error": f"no outputs at {root}"}

    import os
    # One row per REQUEST, and a request is usually run more than once while
    # tuning. Keyed naively, whichever output happened to come last in
    # `sorted(listdir)` won — directory names are `<user>_<float timestamp>`, so
    # that sort is lexicographic, not chronological, and the surviving run was
    # effectively arbitrary. Collect every attempt first, then keep the newest
    # by the timestamp inside settings.json, and say how many were dropped so a
    # mean over 5 rows is never mistaken for a mean over the 18 runs it hides.
    outputs = {}
    for edit_id in sorted(os.listdir(root)):
        be = osp.join(root, edit_id, "brep_end")
        if not osp.isdir(be):
            continue
        for ts in os.listdir(be):
            d = osp.join(be, ts)
            sf = osp.join(d, "settings.json")
            if not osp.exists(sf):
                continue
            with open(sf) as f:
                st = json.load(f)
            rid = st.get("edit_request_id")
            if not rid:
                continue
            outputs.setdefault(rid, []).append((st.get("start_time") or 0.0, d, st))

    n_runs = sum(len(v) for v in outputs.values())
    db = _db()
    rows = {}
    try:
        for rid, attempts in outputs.items():
            when, d, st = max(attempts, key=lambda a: a[0])
            sc = score_output(rid, d, db)
            sc["edit_id"] = st.get("edit_id")
            sc["scored_at"] = when
            sc["runs_for_this_request"] = len(attempts)
            # A run that shipped the untouched input is scoreable but is not an
            # edit; without this the fallback looks like a genuine 0.99 chamfer.
            sc["geometry_source"] = st.get("geometry_source", "edited")
            sc["cost_estimate"] = st.get("token_counts", {}).get("cost_estimate")
            sc["total_tokens"] = st.get("token_counts", {}).get("total_tokens")
            rows[rid] = sc
    finally:
        db.close_connection()

    means = {}
    for m in METRICS:
        vals = [r[m] for r in rows.values() if isinstance(r.get(m), (int, float))]
        means[m] = round(sum(vals) / len(vals), 4) if vals else None
    costs = [r["cost_estimate"] for r in rows.values() if r.get("cost_estimate")]

    # SPLIT BY BODY COUNT. Measured against the published baselines, this
    # pipeline is far ahead on single-body parts and behind on multi-body ones
    # — a gap that came from the geometry index not saying which body owned an
    # entity, and one a headline mean hides completely. Half this benchmark's
    # parts have more than one body, so the split is reported next to the mean
    # and stays visible if it ever regresses.
    split = {"single_solid": {}, "multi_solid": {}}
    try:
        import cadquery as cq
        buckets = {"single_solid": [], "multi_solid": []}
        db2 = _db()
        try:
            for rid, sc in rows.items():
                try:
                    req = db2.requests.find_one({"_id": rid})
                    brep = db2.breps.find_one({"_id": req["brep_start"]})
                    rel = brep["step"][0] if isinstance(brep["step"], list) else brep["step"]
                    n = len(cq.importers.importStep(
                        osp.join(db2.root_dir, rel)).val().Solids())
                except Exception:
                    continue
                buckets["multi_solid" if n > 1 else "single_solid"].append(sc)
        finally:
            db2.close_connection()
        for name, items in buckets.items():
            split[name]["n"] = len(items)
            for m in METRICS:
                vals = [r[m] for r in items if isinstance(r.get(m), (int, float))]
                split[name][m] = round(sum(vals) / len(vals), 4) if vals else None
    except Exception as e:
        split = {"error": str(e)}

    summary = {
        "user_id": user_id,
        "n_scored": len(rows),
        "n_runs_found": n_runs,
        "n_superseded": n_runs - len(rows),
        "n_failed": sum(1 for r in rows.values() if r.get("failed")),
        # Runs that produced no surviving edit and shipped the input instead.
        # These score ~0.99 chamfer / ~0.99 volume and 0.0 diff, so a headline
        # mean that ignores them says far more about the metrics than about the
        # harness.
        "n_unedited_fallback": sum(1 for r in rows.values()
                                   if r.get("geometry_source") == "input-unedited"),
        "means": means,
        "means_by_topology": split,
        "mean_cost_per_edit": round(sum(costs) / len(costs), 4) if costs else None,
        "per_request": rows,
    }

    if write:
        import os as _os
        d = osp.join(config.RESULTS, "scores")
        _os.makedirs(d, exist_ok=True)
        with open(osp.join(d, f"{user_id}.json"), "w") as f:
            json.dump(summary, f, indent=2)
    return summary


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default="ours_adk-router")
    a = ap.parse_args()
    s = score_run(a.user_id)
    print(json.dumps({k: v for k, v in s.items() if k != "per_request"}, indent=2))
