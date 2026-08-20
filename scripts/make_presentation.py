#!/usr/bin/env python3
"""Build presentation/UCONN-CAD-PACK.pdf from files inside this submission
folder only, so anyone who cloned the PR can rebuild it:

    python3 submissions/UCONN-CAD-PACK/scripts/make_presentation.py

Needs matplotlib and pillow (both come with the repo's uv sync).
"""

import os
import os.path as osp
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

_HERE = osp.dirname(osp.abspath(__file__))
SUB = osp.dirname(_HERE)
RES = osp.join(SUB, "src", "results")

INK = "#1c2333"
MUTED = "#5a6478"
ACCENT = "#1f77b4"
PANEL = "#f4f6fb"
GOOD = "#2e7d32"

# The five request_ids the organisers named for qualitative evaluation.
# Renders and scores are the ones shipped in this folder; the scores are the
# per-request entries in src/results/scores/ours_adk-router.json.
EXAMPLES = [
    {
        "rid": "SUJ2G2UMJQR7PMBX_1759209987.785593",
        "text": "Please convert the round edges of the gear into straight spur gear teeth.",
        "difficulty": "medium",
        "start": "render_cache/SUJ2G2UMJQR7PMBX_1759210034.374472/gt_toprightiso.png",
        "gt": "render_cache/SUJ2G2UMJQR7PMBX_1759210435.8404129/gt_toprightiso.png",
        "ours": "runs/ours_adk-router/outputs/ours_adk-router_1786920446.817543/brep_end/1786920446.817543/tmp_toprightiso.jpg",
        "scores": (0.991, 0.972, 0.131),
    },
    {
        "rid": "3YH2WFSRM22W7DKT_1769773335.525203",
        "text": "Add cylindrical heads on the long pins to prevent link arms against slipping off.",
        "difficulty": "medium",
        "start": "render_cache/3YH2WFSRM22W7DKT_1769773358.5396488/gt_toprightiso.png",
        "gt": "render_cache/3YH2WFSRM22W7DKT_1769776990.4257011/gt_toprightiso.png",
        "ours": "runs/ours_adk-router/outputs/ours_adk-router_1786930879.385055/brep_end/1786930879.385055/tmp_toprightiso.jpg",
        "scores": (0.985, 0.994, 0.579),
    },
    {
        "rid": "B7A2N74ZJBF9MZHU_1770174133.012106",
        "text": "Prolong black lever sticking out to the front by 5cm for better manipulation.",
        "difficulty": "easy",
        "start": "render_cache/B7A2N74ZJBF9MZHU_1770174182.8249135/gt_toprightiso.png",
        "gt": "render_cache/B7A2N74ZJBF9MZHU_1770174221.1854131/gt_toprightiso.png",
        "ours": "runs/ours_adk-router/outputs/ours_adk-router_1786845103.729266/brep_end/1786845103.729266/tmp_toprightiso.jpg",
        "scores": (0.985, 1.000, 0.797),
    },
    {
        "rid": "F332D3FXML85WLR2_1769607142.566352",
        "text": "Add third rotor blade to the assembly, same design as the other two, radii on all four long edges, thinner central portion.",
        "difficulty": "hard",
        "start": "render_cache/F332D3FXML85WLR2_1769607241.221253/gt_toprightiso.png",
        "gt": "render_cache/F332D3FXML85WLR2_1769608193.091736/gt_toprightiso.png",
        "ours": "runs/ours_adk-router/outputs/ours_adk-router_1786923495.359924/brep_end/1786923495.359924/tmp_toprightiso.jpg",
        "scores": (0.992, 0.936, 0.783),
    },
    {
        "rid": "ZK22J6VYRKQ2RTFD_1758874422.1403751",
        "text": "Add a connecting hole of 1.7 mm diameter and apply 0.1 mm grooves to increase grip and prevent slipping.",
        "difficulty": "hard",
        "start": "render_cache/ZK22J6VYRKQ2RTFD_1758874483.146758/gt_toprightiso.png",
        "gt": "render_cache/ZK22J6VYRKQ2RTFD_1758875051.4938471/gt_toprightiso.png",
        "ours": "runs/ours_adk-router/outputs/ours_adk-router_1786920437.809964/brep_end/1786920437.809964/tmp_toprightiso.jpg",
        "scores": (0.987, 0.986, 0.073),
    },
]


def new_slide():
    fig = plt.figure(figsize=(13.333, 7.5))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


def header(ax, title):
    ax.text(5, 93, title, fontsize=24, fontweight="bold", color=INK, va="top")
    ax.plot([5, 95], [86.5, 86.5], color=ACCENT, lw=2)


def footer(ax, n):
    ax.text(95, 3, f"UCONN CAD PACK  |  neuralCAD-Edit  |  {n}",
            fontsize=8, color=MUTED, ha="right")


def box(ax, x, y, w, h, title, sub, fc=PANEL, ec=ACCENT):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6",
                                fc=fc, ec=ec, lw=1.4))
    ax.text(x + w / 2, y + h - 2.2, title, ha="center", va="top",
            fontsize=11.5, fontweight="bold", color=INK)
    ax.text(x + w / 2, y + h - 7.2, sub, ha="center", va="top",
            fontsize=8.5, color=MUTED)


def arrow(ax, x1, y1, x2, y2, label=None, color=MUTED, rad=0.0,
          label_dy=1.6):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                 mutation_scale=16, color=color, lw=1.6,
                                 connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((x1 + x2) / 2, min(y1, y2) + label_dy, label, ha="center",
                fontsize=8.2, color=color, style="italic")


def slide_title(pdf):
    fig, ax = new_slide()
    ax.text(50, 62, "UCONN CAD PACK", fontsize=42, fontweight="bold",
            ha="center", color=INK)
    ax.text(50, 52, "Text-instructed 3D CAD editing with a three-agent CadQuery harness",
            fontsize=17, ha="center", color=MUTED)
    ax.text(50, 44, "neuralCAD-Edit benchmark  |  ASME IDETC-CIE 2026 Hackathon (Autodesk)",
            fontsize=13, ha="center", color=MUTED)
    ax.text(50, 30, "48 of 48 edit tasks completed  |  Diff F1 0.39 vs 0.18 for the best published model baseline",
            fontsize=12, ha="center", color=GOOD)
    footer(ax, 1)
    pdf.savefig(fig)
    plt.close(fig)


def slide_method(pdf):
    fig, ax = new_slide()
    header(ax, "Method: plan, execute, gate, verify")

    box(ax, 4, 64, 20, 16, "Strategist",
        "instruction + measured\ngeometry index\n-> 1 to 5 sub-goals,\neach with an envelope")
    box(ax, 30, 64, 20, 16, "Executor",
        "one sub-goal at a time\n-> one CadQuery function,\nrun in a sandboxed\nsubprocess (180 s cap)")
    box(ax, 56, 64, 20, 16, "Deterministic gates",
        "lint (real API names),\nno-op, phantom material,\ndirection, frame drift,\nenvelope. Reject for free.")
    box(ax, 82, 64, 14, 16, "QA agent",
        "7 views before/after\n+ measured diff\naccept / partial / reject")

    arrow(ax, 24.7, 72, 29.3, 72)
    arrow(ax, 50.7, 72, 55.3, 72)
    arrow(ax, 76.7, 72, 81.3, 72)
    # feedback loops, orthogonal elbows at three depths under the pipeline
    def elbow(x_from, x_to, depth, label):
        ax.plot([x_from, x_from, x_to], [63.4, depth, depth],
                color=MUTED, lw=1.4)
        ax.add_patch(FancyArrowPatch((x_to, depth), (x_to, 63.4),
                                     arrowstyle="-|>", mutation_scale=14,
                                     color=MUTED, lw=1.4))
        ax.text((x_from + x_to) / 2, depth - 1.1, label, ha="center",
                va="top", fontsize=8.2, color=MUTED, style="italic")

    elbow(64, 42, 58, "gate feedback (lint repairs in place, attempt not spent)")
    elbow(89, 46, 53, "QA reject: retry with specific guidance")
    elbow(93, 14, 48, "all attempts dead: replan (max 2)")

    bullets = [
        "A stateful router (ADK style) owns the loop: it re-indexes geometry after each accepted sub-goal and forwards QA feedback to the next attempt.",
        "The strategist sees a measured index, not a picture: hole families by radius, blind vs through openings, paired bores, face areas. Colour renders are attached only when the sentence carries an appearance, view or dimension word.",
        "Six deterministic gates reject bad geometry before any model judges it. The lint gate asks the installed CadQuery and OCP whether every attribute exists and repairs the name in place, so a typo never burns an attempt.",
        "QA is a separate model call with no stake in the edit passing. It sees the whole plan, so it never rejects a step for not doing the next step's work.",
        "Everything is measured against the geometry each attempt started from: volume delta, new-face regions, bounding box drift. Renders illustrate, measurements decide.",
    ]
    y = 41
    for b in bullets:
        wrapped = textwrap.fill(b, 150)
        ax.text(5, y, "• " + wrapped, fontsize=10, color=INK, va="top")
        y -= 3.4 * (wrapped.count("\n") + 1) + 2.6
    footer(ax, 2)
    pdf.savefig(fig)
    plt.close(fig)


def slide_scores(pdf):
    fig, ax = new_slide()
    header(ax, "Final scores, benchmark metrics on all 48 edit tasks")

    img = mpimg.imread(osp.join(SUB, "figures", "metric_bar_facets.png"))
    ax_img = fig.add_axes([0.03, 0.10, 0.62, 0.70])
    ax_img.imshow(img)
    ax_img.axis("off")

    rows = [
        ("Surface Chamfer similarity", "0.977"),
        ("Volumetric F1", "0.910"),
        ("Volumetric Difference F1", "0.390"),
        ("Tasks completed", "48 / 48"),
        ("Mean cost per edit", "$0.84"),
    ]
    y = 72
    ax.text(68, 78, "Means over the full split", fontsize=13,
            fontweight="bold", color=INK)
    for name, val in rows:
        ax.text(68, y, name, fontsize=11.5, color=MUTED)
        ax.text(96, y, val, fontsize=11.5, color=INK, ha="right",
                fontweight="bold")
        y -= 5
    ax.text(68, y - 2, textwrap.fill(
        "Scores computed with the benchmark's own metric code "
        "(src/utils/evals_diff.py and evals_feature_geometric.py), same "
        "defaults as the published baselines. Diff F1, the most important "
        "metric, is 2x the best published model baseline (0.18); the human "
        "reference line is 0.60.", 44), fontsize=9.5, color=MUTED, va="top")
    footer(ax, 3)
    pdf.savefig(fig)
    plt.close(fig)


def slide_examples(pdf):
    fig, ax = new_slide()
    header(ax, "The five selected test examples")
    ax.text(5, 84.5, "Input part, our edit, and the human expert's edit (ground truth), toprightiso view. "
                     "Scores are chamfer / volume F1 / diff F1.",
            fontsize=10, color=MUTED, va="top")

    col_x = [0.16, 0.44, 0.72]
    for label, x in zip(["Input", "Ours", "Ground truth"], col_x):
        ax.text(x * 100 + 9, 79, label, fontsize=11, fontweight="bold",
                color=INK, ha="center")

    row_h = 0.148
    top = 0.755
    for i, ex in enumerate(EXAMPLES):
        y0 = top - i * row_h - row_h + 0.02
        for key, x in zip(["start", "ours", "gt"], col_x):
            p = osp.join(RES, ex[key])
            img = mpimg.imread(p)
            a = fig.add_axes([x, y0, 0.18, row_h - 0.006])
            a.imshow(img)
            a.axis("off")
        cy = (y0 + row_h / 2) * 100
        ax.text(4, cy + 5.6, f"[{ex['difficulty']}]", fontsize=8.5,
                color=ACCENT, fontweight="bold", va="center")
        ax.text(4, cy + 3.2, textwrap.fill(ex["text"], 34), fontsize=7.6,
                color=INK, va="top")
        c, v, d = ex["scores"]
        ax.text(93, cy, f"{c:.3f}\n{v:.3f}\n{d:.3f}", fontsize=9,
                color=INK, va="center", ha="left")
    footer(ax, 4)
    pdf.savefig(fig)
    plt.close(fig)


def main():
    out = osp.join(SUB, "presentation", "UCONN-CAD-PACK.pdf")
    os.makedirs(osp.dirname(out), exist_ok=True)
    with PdfPages(out) as pdf:
        slide_title(pdf)
        slide_method(pdf)
        slide_scores(pdf)
        slide_examples(pdf)
    print("wrote", out)


if __name__ == "__main__":
    main()
