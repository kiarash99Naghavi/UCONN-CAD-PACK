#!/usr/bin/env python3
"""One bar per method: the mean over all three benchmark metrics.

Aggregation matches the benchmark's own leaderboard convention (see
src/utils/visualise_results.py in the benchmark repo): every metric is
averaged over all edit tasks with missing or NaN scores counted as 0.0,
then the three metric means are averaged into one overall score.
Baselines come from the dataset's published all_results.json; our numbers
from src/results/scores/ours_adk-router.json in this repo.

Run with the benchmark repo available:

    NEURALCAD_REPO=/path/to/IDETC26-Hackathon-Autodesk-neuralCAD-Edit \
        python3 scripts/make_overall_mean_figure.py
"""

import json
import os
import os.path as osp
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

_HERE = osp.dirname(osp.abspath(__file__))
SUB = osp.dirname(_HERE)

REPO = os.environ.get("NEURALCAD_REPO", "")
if not REPO:
    p = _HERE
    for _ in range(6):
        p = osp.dirname(p)
        if osp.exists(osp.join(p, "src", "config", "edit_192_external.json")):
            REPO = p
            break
if not REPO:
    sys.exit("benchmark repo not found; set NEURALCAD_REPO")

METRICS = ["chamfer_similarity_norm", "volume_f1", "diff_f1"]
OURS = "UCONN CAD PACK\n(ours)"
HUMAN = "other human"
MODELS = [  # config order of src/config/edit_192_external.json
    ("gemini-3-pro_cadquery-script", "gemini-3-pro"),
    ("gpt-5.2_cadquery-script", "gpt-5.2"),
    ("claude-sonnet-4.5_cadquery-script", "claude-sonnet-4.5"),
    ("gpt-5.6-sol_openrouter_cadquery-script", "gpt-5.6-sol"),
]

INK = "#1c2333"
MUTED = "#5a6478"
GRAY = "#b3b8c2"
ACCENT = "#1f77b4"


def zero(v):
    return 0.0 if v is None or v != v else float(v)


def main():
    cfg = json.load(open(osp.join(REPO, "src", "config",
                                  "edit_192_external.json")))
    results = json.load(open(osp.join(
        cfg["storage_dir"]["path"], "results", "all_results.json")))
    ours = json.load(open(osp.join(SUB, "src", "results", "scores",
                                   "ours_adk-router.json")))

    def overall(user):
        means = []
        for m in METRICS:
            vals = []
            for diff in results:
                vals += [zero(v) for v in
                         results[diff].get(user, {}).get(m, {}).values()]
            means.append(sum(vals) / len(vals) if vals else 0.0)
        return sum(means) / len(means)

    labels = [short for _, short in MODELS] + [OURS]
    scores = [overall(uid) for uid, _ in MODELS]
    scores.append(sum(ours["means"][m] for m in METRICS) / len(METRICS))
    human = overall(HUMAN)

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    colors = [GRAY] * len(MODELS) + [ACCENT]
    bars = ax.bar(labels, scores, color=colors, width=0.62, zorder=3)
    ax.axhline(human, ls="--", lw=1.4, color=MUTED, zorder=2,
               label=f"human baseline ({human:.2f})")
    for b, v in zip(bars, scores):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.015, f"{v:.2f}",
                ha="center", fontsize=9.5, color=INK)
    ax.set_ylim(0, 1)
    ax.set_ylabel("Mean of the three metrics", color=INK)
    ax.set_title("Overall score: mean of chamfer similarity, volume F1 and "
                 "diff F1\n48 edit tasks, missing scores counted as 0",
                 fontsize=11, color=INK)
    ax.tick_params(colors=INK, labelsize=9)
    ax.grid(axis="y", color="#e3e6ec", lw=0.8, zorder=0)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c9cdd6")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    plt.tight_layout()

    out = osp.join(SUB, "figures", "metric_mean_overall.png")
    plt.savefig(out, dpi=200, bbox_inches="tight")
    print("wrote", out)


if __name__ == "__main__":
    main()
