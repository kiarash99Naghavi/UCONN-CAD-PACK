"""Run one benchmark task through our pipeline WITHOUT the dashboard UI.

Same code path as the dashboard's Run button, deliberately: this imports
`dashboard._run_pipeline` rather than reimplementing it, so the record it
leaves in `results/dashboard_runs/<request_id>.json` is the one `ours_body`
already knows how to render. Open the task in the dashboard afterwards and the
run is there — log lines, per-attempt steps with their ground-truth scores,
token counts, final scores — exactly as if it had been clicked.

Why it exists: the Run button is one task at a time and dies with the browser
tab. Batch work (a sweep over the tasks that have never been run, an A/B after
a pipeline change) wants many tasks in parallel, unattended, with the results
landing somewhere the dashboard picks up on its own.

    python tools/run_headless.py <request_id> [<request_id> ...]
    python tools/run_headless.py --todo 10      # 10 tasks with no record yet
    python tools/run_headless.py --list         # what is done, what is not

Each task is run in THIS process, one after another; parallelism is one
process per task, which is also what keeps a crash in one task from taking the
others with it.
"""

import argparse
import os.path as osp
import sys
import threading
import time

sys.path.insert(0, osp.dirname(osp.dirname(osp.abspath(__file__))))


def _dashboard():
    """Import the dashboard module (heavy: dash, open3d, the whole dataset)."""
    import tools.dashboard as dash_mod
    return dash_mod


def _todo(dash_mod):
    """Request ids with no saved run record, in the dataset's own order."""
    return [rid for rid in dash_mod.REQUESTS
            if not osp.exists(dash_mod._run_record_path(rid))]


def run_one(dash_mod, rid):
    """Run one task to completion, streaming its progress. Returns the record.

    `_run_pipeline` reports progress by appending to `RUNS[rid]["lines"]` — the
    dashboard renders that on a timer, and nothing writes it to stdout. Run
    headless without this and the log file stays empty for the run's whole
    10-40 minutes, then everything appears at once: no way to tell a working
    run from a wedged one, and nothing to watch while it goes. So the pipeline
    goes on a worker thread and this one tails those lines to stdout as they
    are appended, which is also the order the dashboard shows them in.
    """
    import src.pipeline as our_pipeline

    with dash_mod.RUNS_LOCK:
        dash_mod.RUNS[rid] = {"status": "running", "lines": ["starting…"],
                              "scores": None}
    user_id = osp.basename(getattr(our_pipeline, "USER_ID", "ours_adk-router"))

    t0 = time.time()
    worker = threading.Thread(target=dash_mod._run_pipeline,
                              args=(rid, user_id))    # never raises; self-records
    worker.start()

    seen = 0
    while True:
        worker.join(timeout=2.0)
        with dash_mod.RUNS_LOCK:
            lines = list((dash_mod.RUNS.get(rid) or {}).get("lines") or [])
        for line in lines[seen:]:
            print(f"[{time.time() - t0:7.1f}s] {line}", flush=True)
        seen = len(lines)
        if not worker.is_alive():
            break

    with dash_mod.RUNS_LOCK:
        rec = dict(dash_mod.RUNS.get(rid, {}))

    scores = rec.get("scores") or {}
    print(f"[{rid}] {rec.get('status')} in {time.time() - t0:.0f}s  "
          f"chamfer={scores.get('chamfer_similarity_norm')} "
          f"vol_f1={scores.get('volume_f1')} "
          f"diff_f1={scores.get('diff_f1')}", flush=True)
    if rec.get("error"):
        print(f"[{rid}] error: {rec['error']}", flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("request_ids", nargs="*")
    ap.add_argument("--todo", type=int, metavar="N",
                    help="run the first N tasks that have no run record yet")
    ap.add_argument("--list", action="store_true",
                    help="print which tasks have a record and which do not")
    args = ap.parse_args()

    dash_mod = _dashboard()

    if args.list:
        todo = set(_todo(dash_mod))
        for rid, req in dash_mod.REQUESTS.items():
            mark = "TODO" if rid in todo else "done"
            text = (req.get("text") or req.get("prompt") or "").strip()
            print(f"{mark}  {rid}  [{req.get('difficulty')}]  "
                  f"{text[:80].replace(chr(10), ' ')}")
        print(f"\n{len(dash_mod.REQUESTS) - len(todo)} done, {len(todo)} todo")
        return 0

    rids = list(args.request_ids)
    if args.todo:
        rids += _todo(dash_mod)[:args.todo]
    if not rids:
        ap.error("give at least one request id, or --todo N, or --list")

    unknown = [r for r in rids if r not in dash_mod.REQUESTS]
    if unknown:
        ap.error(f"not in this dataset: {', '.join(unknown)}")

    for rid in rids:
        req = dash_mod.REQUESTS[rid]
        text = (req.get("text") or req.get("prompt") or "").strip()
        print(f"\n=== {rid} [{req.get('difficulty')}] ===\n{text}\n", flush=True)
        run_one(dash_mod, rid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
