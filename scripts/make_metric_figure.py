#!/usr/bin/env python3
"""Regenerate metric_bar_facets.png with UCONN CAD PACK added as a model.

Uses the benchmark's own plotting code (src/utils/visualise_results.py) and
its saved all_results.json, so every baseline bar is exactly the published
one. Our bars come from src/results/scores/ours_adk-router.json,
which was itself written by the benchmark's metric code (see
src/evaluate.py).

Run from the repo root after uv sync, with the dataset in place:

    .venv/bin/python submissions/UCONN-CAD-PACK/scripts/make_metric_figure.py
"""

import copy
import json
import os
import os.path as osp
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

_HERE = osp.dirname(osp.abspath(__file__))
SUB = osp.dirname(_HERE)

# repo root: honour NEURALCAD_REPO, else walk up to src/config/...
REPO = os.environ.get("NEURALCAD_REPO", "")
if not REPO:
    p = _HERE
    for _ in range(6):
        p = osp.dirname(p)
        if osp.exists(osp.join(p, "src", "config", "edit_192_external.json")):
            REPO = p
            break
sys.path.insert(0, REPO)

from src.utils import visualise_results as vr
from src.utils.db import DatabaseManager
from src.utils.process_config import load_config

OURS = "UCONN CAD PACK (ours)"


def main():
    cfg = load_config(osp.join(REPO, "src", "config", "edit_192_external.json"))
    db = DatabaseManager(cfg)

    all_results = json.load(open(osp.join(
        cfg["storage_dir"]["path"], "results", "all_results.json")))
    ours = json.load(open(osp.join(SUB, "src", "results",
                                   "scores", "ours_adk-router.json")))

    results = copy.deepcopy(all_results)
    n = 0
    for rid, entry in ours["per_request"].items():
        req = db.requests.find_one({"_id": rid})
        bucket = f"edit_{req['difficulty']}" if not str(
            req["difficulty"]).startswith("edit") else req["difficulty"]
        block = results.setdefault(bucket, {}).setdefault(OURS, {})
        for m in ("chamfer_similarity_norm", "volume_f1", "diff_f1"):
            block.setdefault(m, {})[rid] = entry[m]
        n += 1
    print(f"injected {n} scored requests as '{OURS}'")

    cfg["benchmark_eval_users"]["edit"] = (
        cfg["benchmark_eval_users"]["edit"] + [OURS])
    fig, axes = vr.faceted_bar_plot(cfg, results, request_type="edit", save=False)

    out = osp.join(SUB, "figures", "metric_bar_facets.png")
    os.makedirs(osp.dirname(out), exist_ok=True)
    plt.savefig(out, dpi=200, bbox_inches="tight")
    print("wrote", out)


if __name__ == "__main__":
    main()
