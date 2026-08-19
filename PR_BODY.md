# [UCONN CAD PACK] Final submission

<!-- Draft PR body. Title the PR exactly as the heading above. -->

Final submission from team UCONN CAD PACK: a three-agent CadQuery harness (strategist, executor, QA) behind six deterministic geometry gates, driven by a stateful router.

Everything lives in `submissions/UCONN-CAD-PACK/`:

- **Code**: the full pipeline under `src/`, the dashboard under `tools/`.
- **Output CAD files**: `outputs/<request_id>/ours.step` and `ours.stl` for all 48 edit tasks, with the ground truth `gt.stl` beside each for comparison (included with the benchmark creators' permission, CC BY-NC 4.0, cited in the README).
- **Scores**, computed with the benchmark's own metric code over all 48 tasks:
  - Surface Chamfer similarity: **0.978**
  - Volumetric F1: **0.910**
  - Volumetric Difference F1 (most important): **0.369**, vs 0.18 for the best published model baseline
  - `figures/metric_bar_facets.png` has the full comparison, and `outputs/manifest.json` the per-request numbers.
- **Presentation**: `presentation/UCONN-CAD-PACK.pdf`, with the method overview, the metric chart, and the five organiser-named test examples on one slide.
- **Dashboard**: `./submissions/UCONN-CAD-PACK/run_dashboard.sh` from the repo root starts a Dash app with the method deck, side-by-side geometry viewers, per-task scores, and saved run replays. No API key needed to browse.

The 3-minute video is uploaded to the shared Drive folder as `UCONN-CAD-PACK.mp4`.
