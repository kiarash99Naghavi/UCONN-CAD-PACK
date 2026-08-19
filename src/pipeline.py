"""Run the agent pipeline on one request, then score it.

CLI:
    cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
    PYTHONPATH=$(pwd):$(pwd)/../.. uv run python -m src.pipeline \
        --request <request_id> [--user-id ours-v1]
"""

import argparse
import datetime
import json
import os
import os.path as osp
import shutil
import time

from . import config
from .adk.router import StatefulRouter
from .adk.state import SessionState

USER_ID = os.environ.get("OUR_USER_ID", "ours_adk-router")

# The dataset-render gate: the original-color renders are attached to the
# strategist ONLY when the instruction contains a word they can resolve and
# the synthetic renders cannot — an appearance word ("the BLACK lever", "the
# metal pin") or an orientation word in the customer's frame ("the front").
# A purely dimensional instruction ("add a 0.2 mm chamfer to the hole edges")
# skips them entirely.
import re as _re

_APPEARANCE_WORDS = _re.compile(
    r"\b(black|white|red|green|blue|yellow|orange|purple|pink|brown|gr[ae]y|"
    r"silver|gold(en)?|dark|light|colou?r(ed|ful)?|metal(lic)?|steel|"
    r"alumin(um|ium)|brass|copper|rubber|plastic|wood(en)?|glass|"
    r"transparent|shiny|matte)\b", _re.IGNORECASE)
_VIEW_WORDS = _re.compile(
    r"\b(front|back|rear|top|bottom|left|right|side(ways)?|upper|lower|"
    r"above|below|behind|upward|downward|forward|vertical|horizontal)\b",
    _re.IGNORECASE)
# "Taller", "wider", "deeper" name an AXIS in the customer's own frame, and
# which of the part's three dimensions is the tall one is precisely what the
# synthetic index cannot say — it has face normals and a bounding box, none of
# which know which way up the object sits on a bench.
#
# Measured on task 25 (B7A2N74ZJBF9MZHU_1770171470, "Make the whole frame
# structure taller by 0,5cm"): the gate found no cue word, the renders were
# skipped, and the strategist grew the frame 5 mm along its 568 mm axis. The
# human had grown it 5 mm along the PERPENDICULAR 168 mm axis — the two
# directions are exactly orthogonal (dot product 0). QA accepted, chamfer came
# out 0.994 and volume_f1 0.867, and diff_f1 was 0.009: a clean run that edited
# the wrong dimension. An axis word is as much a pointer at the renders as
# "the black lever" is.
_DIMENSION_WORDS = _re.compile(
    r"\b(tall(er|est)?|high(er|est)?|short(er|est)?|deep(er|est)?|"
    r"wide(r|st)?|narrow(er|est)?|thick(er|est)?|thin(ner|nest)?|"
    r"long(er|est)?|height|width|depth|length|thickness)\b", _re.IGNORECASE)
# A customer writing "THAT slot" is pointing at something they can see, and
# the renders are the only record of what they were looking at. Measured: on
# "change that vertical slot to cut through", the renders were skipped as
# having no cue word, and the plan picked a feature 100 mm away from the one
# the human edited.
_DEICTIC_WORDS = _re.compile(
    r"\b(that|this|these|those)\s+\w+", _re.IGNORECASE)


def _wants_dataset_renders(text):
    """The cue word that justifies attaching the dataset renders, or None."""
    m = (_APPEARANCE_WORDS.search(text or "")
         or _VIEW_WORDS.search(text or "")
         or _DIMENSION_WORDS.search(text or "")
         or _DEICTIC_WORDS.search(text or ""))
    return m.group(0) if m else None


def _db():
    from src.utils.db import DatabaseManager
    from src.utils.process_config import load_config
    return DatabaseManager(load_config(config.BENCH_CONFIG))


def load_request(request_id, db=None):
    """(instruction, input_step_abs_path, request_record)

    The request record additionally carries `_start_stl_abs`: the dataset's own
    canonical mesh of the input. When a run ships the input unedited, that mesh
    — not a re-tessellation of the STEP — is what "unedited" means to the
    scorer (see run_request), because the two are not always the same design:
    measured on the coffeepot, the dataset STL carries a feature at y≈178 that
    the STEP does not contain, so every STEP-derived export of the UNCHANGED
    part flipped 82 voxels and scored diff F1 0.0.
    """
    own = db is None
    db = db or _db()
    try:
        req = db.requests.find_one({"_id": request_id})
        if not req:
            raise ValueError(f"no such request: {request_id}")
        brep = db.breps.find_one({"_id": req["brep_start"]})
        rel = brep["step"][0] if isinstance(brep["step"], list) else brep["step"]
        stl = brep.get("stl")
        stl_rel = stl[0] if isinstance(stl, list) else stl
        req["_start_stl_abs"] = osp.join(db.root_dir, stl_rel) if stl_rel else None
        # The dataset's own renders of the input, WITH its original colors and
        # materials. Our STEP-derived renders are synthetic gray / feature-
        # coded, so an instruction like "prolong the BLACK lever sticking out
        # to the FRONT" is undecodable from them — measured: the strategist
        # guessed +X for a lever the human extended in +Z, QA deferred to the
        # sub-goal, diff F1 0.0. These jpgs are also the naming authority for
        # view words: the dataset's front.jpg IS what the customer calls the
        # front.
        #
        # All seven views, downscaled 1024 -> 512 px (vision pricing is per
        # image tile) and cached. Whether they are ATTACHED at all is decided
        # per run by _wants_dataset_renders in run_request: the set travels
        # only when the instruction actually contains an appearance or
        # orientation word the synthetic renders cannot resolve. On any
        # downscale failure the original path is used — worst case is the old
        # price, never a lost view.
        req["_start_renders"] = {}
        for v in ("toprightiso", "front", "back", "left", "right", "top",
                  "bottom"):
            p = osp.join(db.root_dir, "breps", f"{req['brep_start']}_{v}.jpg")
            if not osp.exists(p):
                continue
            small = osp.join(config.RESULTS, "render_cache", "dataset512",
                             osp.basename(p))
            try:
                if not osp.exists(small):
                    from PIL import Image
                    os.makedirs(osp.dirname(small), exist_ok=True)
                    img = Image.open(p).convert("RGB")
                    img.thumbnail((512, 512))
                    img.save(small, quality=88)
                req["_start_renders"][v] = small
            except Exception:
                req["_start_renders"][v] = p
        return req.get("text", ""), osp.join(db.root_dir, rel), req
    finally:
        if own:
            db.close_connection()


def task_index(request_id, db=None):
    """(n, total): this request's stable 1-based number among the benchmark's
    tasks, from the sorted list of all request ids. Deterministic across runs
    and machines, so 'task 07/48' always names the same request. (0, total)
    for an id the DB does not know."""
    own = db is None
    db = db or _db()
    try:
        ids = sorted(r["_id"] for r in db.requests.find({}))
    finally:
        if own:
            db.close_connection()
    n = ids.index(request_id) + 1 if request_id in ids else 0
    return n, len(ids)


def run_request(request_id, user_id=USER_ID, on_event=None, keep_work=False):
    """Full run for one request. Returns (state, dest_dir, settings)."""
    db = _db()
    try:
        instruction, input_step, req = load_request(request_id, db)
        task_no, task_total = task_index(request_id, db)
    finally:
        db.close_connection()

    run_root = osp.join(config.RESULTS, "runs", user_id)
    work_dir = osp.join(run_root, "_work", f"{request_id}_{int(time.time())}")
    os.makedirs(work_dir, exist_ok=True)

    state = SessionState(request_id=request_id, instruction=instruction,
                         input_step=input_step, work_dir=work_dir)
    # Dataset renders of the input (original colors/materials) for the
    # strategist — attached only when the instruction has a cue word they
    # resolve (see _wants_dataset_renders). Runtime-only, never serialized.
    cue = _wants_dataset_renders(instruction)
    renders = req.get("_start_renders") or {}
    state.__dict__["dataset_views"] = renders if cue else {}
    state.log("start", f"[task {task_no:02d}/{task_total}] "
                       f"[{req.get('difficulty','?')}] {instruction}")
    if cue and renders:
        state.log("router", f"  instruction says '{cue}' — attaching "
                            f"{len(renders)} dataset render(s) with original "
                            f"colors for the strategist")
    elif renders:
        state.log("router", "  no appearance/orientation words — dataset "
                            "renders skipped (saves the image tokens)")

    router = StatefulRouter(state, on_event=on_event)
    try:
        router.run()
    except Exception as e:
        # A user stop (or anything else escaping the router) used to unwind
        # past this point, and every durable artifact — output dir,
        # settings.json, the experiments ledger row, run_log.txt — is written
        # downstream of it: the run was lost entirely while valid partial
        # geometry sat in the work dir. Ship whatever state exists and record
        # the run instead. The on_event callback is silenced because it is
        # the very channel that re-raises a requested stop on every emit.
        state.status = ("stopped" if type(e).__name__ == "StopRequested"
                        else "failed")
        state.last_error = f"{type(e).__name__}: {e}"
        router.on_event = lambda *_: None
        state.log("router", f"run aborted ({state.last_error}) — finalizing "
                            f"and recording what exists instead of discarding "
                            f"the run")
    dest, settings = router.finalize(osp.join(run_root, "outputs"), user_id)

    # An UNEDITED shipment is represented by the dataset's canonical mesh, not
    # by our re-tessellation of the STEP: the two can genuinely differ (the
    # coffeepot's STL carries geometry its STEP lacks), and against a ground
    # truth whose voxel diff is empty a single spurious flipped voxel scores
    # 0.0. Only the no-edit path does this — an EDITED result has no canonical
    # mesh and must be exported from its own geometry.
    if settings.get("geometry_source") == "input-unedited":
        src_stl = req.get("_start_stl_abs")
        if src_stl and osp.exists(src_stl):
            try:
                shutil.copy(src_stl, osp.join(dest, "tmp.stl"))
                settings["stl_source"] = "dataset-canonical-mesh"
                with open(osp.join(dest, "settings.json"), "w") as f:
                    json.dump(settings, f, indent=2)
                state.log("artifacts", "unedited shipment: dataset's own STL "
                                       "used instead of a re-tessellation")
            except OSError:
                pass

    # Preserve the scripts of anything that went wrong. Deleting them made the
    # first live run impossible to diagnose.
    _save_failed_attempts(state, dest)

    _save_experiment(state, dest, settings, req, task_no, task_total)

    clean = state.status == "done" and all(
        t.status in ("done", "partial") for t in state.subtasks)
    if not keep_work and clean:
        shutil.rmtree(work_dir, ignore_errors=True)
    elif not clean:
        settings["work_dir_kept"] = work_dir
    return state, dest, settings


def _save_experiment(state, dest, settings, req, task_no, task_total):
    """One record per experiment: which task (1-48), when, under what config.

    Three artifacts, so an A/B over the whole benchmark can be assembled
    without re-opening 48 session_state.json files:
      - <dest>/experiment.json   — the record for THIS run
      - <dest>/run_log.txt       — every event of the run as readable text
        (the per-attempt scripts, geometry and full executor prompts are
        already in <dest>/steps/, persisted by finalize)
      - results/experiments.jsonl — the record appended to one ledger,
        newest last, one line per run across all experiments
    """
    stamp = datetime.datetime.now().astimezone()
    record = {
        "timestamp": stamp.isoformat(timespec="seconds"),
        "task_number": task_no,
        "task_total": task_total,
        "task_name": state.request_id,
        "difficulty": req.get("difficulty", "?"),
        "instruction": state.instruction,
        "status": state.status,
        "use_skill_recipes": config.USE_SKILL_RECIPES,
        "recipes_max_tokens": config.RECIPES_MAX_TOKENS,
        "models": {"strategist": config.MODEL_STRATEGIST,
                   "executor": config.MODEL_EXECUTOR,
                   "qa": config.MODEL_QA},
        "token_counts": settings.get("token_counts"),
        "geometry_source": settings.get("geometry_source"),
        "output_dir": dest,
    }
    try:
        with open(osp.join(dest, "experiment.json"), "w") as f:
            json.dump(record, f, indent=2)

        lines = [f"task        : {task_no:02d}/{task_total}  {state.request_id}",
                 f"time        : {record['timestamp']}",
                 f"recipes     : {'ON' if config.USE_SKILL_RECIPES else 'OFF'} "
                 f"(budget {config.RECIPES_MAX_TOKENS}t)",
                 f"status      : {state.status}",
                 f"instruction : {state.instruction}", ""]
        for e in state.events:
            extra = {k: v for k, v in e.items() if k not in ("t", "kind", "msg")}
            lines.append(f"[{e['t']:>8.2f}s] {e['kind']:<10} {e['msg']}"
                         + (f"  {extra}" if extra else ""))
        with open(osp.join(dest, "run_log.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")

        with open(osp.join(config.RESULTS, "experiments.jsonl"), "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        state.log("artifacts", f"experiment record not fully saved: {e}")


def _save_failed_attempts(state, dest):
    """Copy every generated script that crashed into the output directory."""
    import glob
    saved = 0
    for path in sorted(glob.glob(osp.join(state.work_dir, "sub*_try*",
                                          "candidate_function.py"))):
        tag = osp.basename(osp.dirname(path))          # e.g. sub0_try2
        try:
            shutil.copy(path, osp.join(dest, f"attempt_{tag}.py"))
            saved += 1
        except OSError:
            pass
    if saved:
        state.log("artifacts", f"kept {saved} generated script(s) for inspection")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True)
    ap.add_argument("--user-id", default=USER_ID)
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--score", action="store_true", help="score it afterwards")
    a = ap.parse_args()

    def log(_state, msg):
        print(f"  {msg}")

    state, dest, settings = run_request(a.request, a.user_id, on_event=log,
                                        keep_work=a.keep_work)
    print(f"\nstatus   : {state.status}")
    print(f"output   : {dest}")
    print(f"tokens   : {settings['token_counts']}")

    if a.score:
        from .evaluate import score_output
        print(f"scores   : {score_output(a.request, dest)}")


if __name__ == "__main__":
    main()
