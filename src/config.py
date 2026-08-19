"""Configuration — everything tunable lives here or in .env."""

import os
import os.path as osp

_HERE = osp.dirname(osp.abspath(__file__))
# Find the benchmark repo root: honour NEURALCAD_REPO, else walk up from
# this file until a directory holding src/config/edit_192_external.json
# appears. Inside submissions/<team>/src that is 3 levels up.
REPO = os.environ.get("NEURALCAD_REPO", "")
if not REPO:
    _p = _HERE
    for _ in range(6):
        _p = osp.dirname(_p)
        if osp.exists(osp.join(_p, "src", "config", "edit_192_external.json")):
            REPO = _p
            break
REPO = osp.abspath(REPO or osp.join(_HERE, "..", "..", ".."))
DATASET = osp.join(REPO, "data", "edit_192_external")
RESULTS = osp.join(_HERE, "results")
BENCH_CONFIG = osp.join(REPO, "src", "config", "edit_192_external.json")


def _load_dotenv():
    """Minimal .env loader (no python-dotenv dependency)."""
    path = osp.join(_HERE, ".env")
    if not osp.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL") or None

# One model per role — a cheap model for QA is a legitimate cost lever.
MODEL_STRATEGIST = os.environ.get("MODEL_STRATEGIST", "gpt-5.2-2025-12-11")
MODEL_EXECUTOR = os.environ.get("MODEL_EXECUTOR", "gpt-5.2-2025-12-11")
MODEL_QA = os.environ.get("MODEL_QA", "gpt-5.2-2025-12-11")

REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "medium")

# $ per 1M tokens, for the cost estimate written into settings.json
COST_IN = float(os.environ.get("COST_PER_1M_INPUT", "1.75"))
COST_OUT = float(os.environ.get("COST_PER_1M_OUTPUT", "14.00"))

# Loop budgets
MAX_SUBTASKS = int(os.environ.get("MAX_SUBTASKS", "5"))
# Attempts that produced judgeable geometry — a real design decision that a
# gate or QA then rejected.
MAX_ATTEMPTS_PER_SUBTASK = int(os.environ.get("MAX_ATTEMPTS_PER_SUBTASK", "3"))
# Extra headroom on top of that for attempts which produced nothing to judge
# (a traceback, a no-op). 0 means the ceiling equals the design budget: three
# attempts per sub-goal, total. Watching runs showed that once a sub-goal
# starts producing no-ops it rarely recovers by attempt 5 or 6 — the extra
# attempts only burned tokens repeating the same failure.
MAX_BARREN_RETRIES = int(os.environ.get("MAX_BARREN_RETRIES", "0"))
# When EVERY attempt of a sub-goal is rejected and nothing was kept, the
# attempts' collected feedback goes back to the strategist for a new strategy
# and the sub-goal re-runs. This caps how many times that can happen per
# sub-goal; the alternative was shipping the unedited input after three
# attempts died in the same doomed operation.
MAX_REPLANS = int(os.environ.get("MAX_REPLANS", "2"))
SCRIPT_TIMEOUT_S = int(os.environ.get("SCRIPT_TIMEOUT_S", "180"))
RENDER_SIZE = int(os.environ.get("RENDER_SIZE", "512"))

# How a sub-goal picks which of its kept checkpoints to ship.
#   "rank" — verdict, then progress, then recency (the original behaviour)
#   "mbr"  — same, but ties are broken by geometric consensus (tools/mbr.py)
# Ranking alone measurably ships worse geometry than the run already built — 0.592
# diff_f1 across 7 of 48 tasks — because a tie falls through to "whichever ran last".
# MBR only ever breaks a TIE, so a full acceptance still beats a partial and a
# refinement that made progress still beats what it refined. Validated offline first
# (tools/mbr_offline.py): on archived candidates it never picked worse than what
# shipped, though it abstains whenever there are fewer than three distinct answers.
SELECTION_POLICY = os.environ.get("SELECTION_POLICY", "mbr")

# Wall clock for ONE model call. The SDK's own default is 600 s with silent
# retries on top, so a single stuck call can outlast everything else in the run
# put together: measured on task 26, attempt 4's executor call took 1015 s —
# 57% of a 30-minute run — to produce one script, and the run was killed before
# it ever finished. A bounded call fails loudly and is retried by the agent's
# own retry loop, which at least says what it is doing.
# 420 s was the first value here and it BACKFIRED, which is worth recording:
# the executor's calls on a hard task genuinely need 360-675 s, so the timeout
# fired on work that was proceeding normally and each retry re-paid the same
# latency. Measured on the task-26 rerun: `executor error: Request timed out.`
# followed by a 675 s call that succeeded — the guard meant to stop one call
# eating a run was making that run slower. 900 s clears the observed worst
# case with margin while still bounding a genuinely stuck call.
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "900"))
# Retries inside the SDK, on top of the agent-level retries. Dropped from 2 to
# 1 for the same reason: past a 900 s ceiling a call is hung rather than slow,
# and a retried reasoning call is charged in full. Worst case is now 2 x 900 s
# instead of 3 x 420 s, and the common case stops being retried at all.
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "1"))

# The cadquery-editor skill. Only `reference/recipes_edit.md` is ever sent to a
# model, and only the sections a given sub-goal needs. OFF by default for the
# recipes-ablation experiment (2026-08-15): the recipes block had grown into
# the single largest prompt block (~4.1k tokens, more than the geometry index)
# and the question is whether it still earns that. USE_SKILL_RECIPES=1 turns
# it back on — each run records the setting in its experiment.json and in
# results/experiments.jsonl, so A/B rows are self-labelling.
SKILLS_DIR = os.environ.get("SKILLS_DIR") or osp.join(_HERE, "..", "Skills")
# 4100, not 4000, and the 100 is not a round number — it is the measured point
# at which §2 (localization) fits alongside the LARGEST tag section. §1 is
# always sent (799 t) and the header costs 134 t, so a `fillet-chamfer` sub-goal
# carrying §4 (1912 t) had 1155 t left and §2 costs 1231 t: 76 tokens short. It
# lost its place to §6 (cuts and holes, 826 t), which keyword scoring pulled in
# because the instruction said "hole edges" — the noun, while the operation was
# a chamfer. So the one playbook that teaches FINDING the hole rims was dropped
# from the one task that is mostly about finding hole rims. Swept 4000 / 4100 /
# 4300 over nine tagged sub-goals: 4100 fixes that case and raises mean spend by
# 45 t (1.4%); 4300 changes nothing at all, so the extra headroom is pure cost.
RECIPES_MAX_TOKENS = int(os.environ.get("RECIPES_MAX_TOKENS", "4100"))
USE_SKILL_RECIPES = os.environ.get("USE_SKILL_RECIPES", "0") not in ("0", "false", "")

# Views sent to the QA agent. The baseline shows the model one image; the six
# orthographic views are what the benchmark itself compares, and the iso leads
# deliberately. The six orthographic views are what the benchmark
# compares, but on a part whose features are arranged in 3D they all flatten it:
# measured on the rotor, QA counted arms off the near-edge-on plan views, read a
# correct 3-blade result as "four blades, over-duplication", and rejected the
# same good geometry three times. The iso is the one view where that arrangement
# is unambiguous, and one extra image is cheap next to a false rejection.
QA_VIEWS = ["toprightiso", "front", "back", "left", "right", "top", "bottom"]
ALL_VIEWS = list(QA_VIEWS)

# Views sent to the EXECUTOR — the full set, of the LAST APPROVED STATE, which
# is exactly what args["input_file"] loads. It used to get none at all, then
# three; but this is the agent that has to find the target feature and decide
# which side of a face material goes on, and three views hid the arrangement
# that a fourth would have shown. Every other agent now sees the whole set.
#
# It is deliberately NOT shown its previous attempt's renders. That geometry was
# discarded and is never loaded, so picturing it invites the model to keep
# refining a part that no longer exists — the opposite of the fresh-start rule
# the rejection feedback states. What it needs from the failed attempt is QA's
# reasoning, and that arrives as text.
EXECUTOR_VIEWS = list(ALL_VIEWS)
SHOW_EXECUTOR_VIEWS = os.environ.get("SHOW_EXECUTOR_VIEWS", "1") not in ("0", "false", "")


def require_key():
    if not API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy src/.env.example to "
            "src/.env and put your key in it.")
