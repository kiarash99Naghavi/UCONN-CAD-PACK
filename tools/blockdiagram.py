#!/usr/bin/env python3
"""Generate ARCHITECTURE.md — the block-diagram model of this project.

Run it after any change to the code:

    python3 tools/blockdiagram.py

It needs nothing but the standard library, never imports the project (so it
works without CadQuery, without `uv`, and without an API key), and rewrites the
file only when the content actually changes — so a no-op run leaves the git
working tree clean.

WHY IT IS PART DECLARED, PART DERIVED
-------------------------------------
A diagram scraped purely from imports is noise: it shows that `router` imports
`geometry` but not that the geometry index is what lets the executor select a
hole by measured radius instead of guessing a selector string. So the *shape* of
the system — blocks, their input/output ports, the order the flow visits them —
is declared below, by hand, in `BLOCKS` and `EDGES`.

Everything checkable is then derived from the source on every run:

  * each block's file must exist; its line count, content hash, one-line
    docstring summary and public API are read live;
  * every declared edge carries `evidence` — a symbol that must appear in the
    calling file — so an edge that stops being real gets reported;
  * any module in the scanned tree that no block claims is reported as
    unmapped;
  * the tunable knobs table is parsed out of `src/config.py`.

Anything that drifts lands in the "Drift report" section at the bottom of the
generated file instead of silently rotting. `--check` turns drift into a
non-zero exit code for CI or a pre-commit hook.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DEFAULT = ROOT / "ARCHITECTURE.md"
SVG_REL = "docs/architecture.svg"          # written next to the markdown
SVG_AGENTS_REL = "docs/agents.svg"

# Trees whose .py files must all be claimed by a block (the vendored benchmark
# submodule under sourcecode/ is deliberately out of scope — it is not ours).
SCAN_ROOTS = ["src", "tools", "test"]
SCAN_EXCLUDE = ("results", "__pycache__", ".venv", "node_modules")


# ---------------------------------------------------------------------------
# The declared model
# ---------------------------------------------------------------------------

@dataclass
class Block:
    id: str                     # mermaid node id — [A-Za-z0-9_] only
    name: str                   # short display name
    layer: str                  # key into LAYERS
    role: str                   # one line: what it is responsible for
    inputs: list                # input ports, most important first
    outputs: list               # output ports, most important first
    file: str = ""              # repo-relative path, "" for external systems
    note: str = ""              # the non-obvious thing worth knowing


@dataclass
class Edge:
    src: str
    dst: str
    label: str = ""
    kind: str = "flow"          # flow | data | proc | opt
    evidence: tuple = ()        # (repo-relative file, substring that must exist)


LAYERS = {
    "entry":    ("Entry points", "#dbeafe", "#1e40af"),
    "orch":     ("Orchestration (ADK)", "#ede9fe", "#5b21b6"),
    "agent":    ("LLM agents", "#dcfce7", "#166534"),
    "tool":     ("Tool layer (deterministic)", "#fef3c7", "#92400e"),
    "external": ("External systems", "#e5e7eb", "#374151"),
}

BLOCKS = [
    # ---- entry points ----------------------------------------------------
    Block(
        id="pipeline", name="pipeline.run_request", layer="entry",
        file="src/pipeline.py",
        role="Runs one benchmark request end to end: load it, drive the router, write the submission.",
        inputs=["request_id", "user_id", "on_event callback", "benchmark DB (mongita)"],
        outputs=["SessionState", "output dir <edit_id>/brep_end/<ts>/", "settings.json"],
        note="Keeps the work dir when anything failed, and copies every crashed script out for inspection. It also decides whether the dataset's own colour renders travel with the request: they are attached only when the instruction contains a word the synthetic index cannot answer — an appearance word, a view word, a deictic \"that\", or a dimension word (\"taller\", \"wider\", \"thicker\"), since which of three extents is the tall one is exactly what a bounding box does not know.",
    ),
    Block(
        id="evaluate", name="evaluate.score_output", layer="entry",
        file="src/evaluate.py",
        role="Scores a produced folder with the benchmark's own metric code, so numbers are comparable to the published baselines.",
        inputs=["request_id", "output dir holding tmp.stl", "ground-truth STL from the DB"],
        outputs=["chamfer_similarity_norm", "volume_f1", "diff_f1", "results/scores/<user>.json"],
        note="A missing STL scores 0.0 on all three metrics — exactly how the benchmark treats a failed edit.",
    ),
    Block(
        id="dashboard", name="tools/dashboard.py", layer="entry",
        file="tools/dashboard.py",
        role="Five-tab Dash UI: the method, the task, ground truth vs any baseline, a live run of our harness, and the results.",
        inputs=["request_id picked in the UI", "run button", "docs/method_overview.md + docs/method_nodes.json", "results/dashboard_runs/*.json", "results/scores/ours_adk-router.json", "the dataset's own all_results.json"],
        outputs=["interactive 3D meshes", "per-attempt gallery of the seven QA views", "per-task and benchmark-wide charts", "a clickable pipeline diagram"],
        note="Runs the pipeline on a background thread — which is why all rendering shells out to a child process. Every score it shows is one run per task, the most recent: the per-task record and the sweep file are compared on the run timestamp inside the output path and the newer one wins outright, scores and geometry together, so a chart and the part beside it are never different runs.",
    ),
    Block(
        id="dashviz", name="tools/dashviz.py", layer="entry",
        file="tools/dashviz.py",
        role="Chart factories and design tokens for the dashboard.",
        inputs=["plain lists and dicts of scores, costs and labels"],
        outputs=["Plotly figures", "the categorical palette and status colours"],
        note="Imports neither Dash nor the benchmark, so it can be exercised on its own and cannot cycle back into the UI module. Series take palette slots in a fixed order because the order is what keeps adjacent pairs separable under colour-blind simulation; charts that put every pair on screen at once (the scatters) are held to three.",
    ),
    Block(
        id="run_headless", name="tools/run_headless.py", layer="entry",
        file="tools/run_headless.py",
        role="Batch runner: the dashboard's Run button without the browser, one task after another.",
        inputs=["request ids", "--todo N (tasks with no run record yet)", "--list"],
        outputs=["the same results/dashboard_runs/<request_id>.json record the UI reads", "progress + final scores on stdout"],
        note="Calls `dashboard._run_pipeline` rather than reimplementing it, so a headless run and a clicked run leave identical records. The pipeline goes on a worker thread only so this process can tail its log lines to stdout while a 10-40 minute run is in flight.",
    ),
    Block(
        id="browse", name="tools/browse.py", layer="entry",
        file="tools/browse.py",
        role="CLI task browser: list the 48 requests, or open one task's renders.",
        inputs=["--list", "request id", "--open"],
        outputs=["instruction text", "topology summary", "dataset JPGs"],
    ),

    # ---- orchestration ---------------------------------------------------
    Block(
        id="state", name="adk.state.SessionState", layer="orch",
        file="src/adk/state.py",
        role="The shared, inspectable blackboard the router drives and every agent reads a slice of.",
        inputs=["request_id", "instruction", "input_step", "work_dir"],
        outputs=["subtasks[]", "geometry_text", "accepted_step", "checkpoints[]", "steps[] with renders", "events[]"],
        note="Explicit structured state, not a growing chat log — that is what keeps the context from ballooning across a run.",
    ),
    Block(
        id="router", name="adk.router.StatefulRouter", layer="orch",
        file="src/adk/router.py",
        role="The state machine: who runs next, how many attempts a sub-goal gets, which geometry gets promoted.",
        inputs=["SessionState", "on_event callback"],
        outputs=["accepted_step per sub-goal", "checkpoints", "finalized submission folder"],
        note="Two budgets per sub-goal: design attempts (a real proposal was judged) and barren ones (a crash or a no-op). It also owns the two filters that keep one sub-goal's trouble out of another's: a no-op traced to a misspelled API name does not condemn the approach, and a QA finding whose vocabulary belongs to a pending sub-goal is dropped before it can be written into this sub-goal's text.",
    ),
    Block(
        id="llm", name="adk.llm.LLM", layer="orch",
        file="src/adk/llm.py",
        role="OpenAI-compatible client with hard JSON guarantees and per-session token accounting.",
        inputs=["system prompt", "content parts (text + base64 images)", "model id"],
        outputs=["parsed dict", "Usage: tokens, calls, cost estimate"],
        note="A malformed reply is retried with the parse error fed back — the baseline degrades to {} and burns an iteration. One call is bounded at LLM_TIMEOUT_S (420 s) with 2 SDK retries, because the SDK's own 600 s default let a single stuck call take 57% of a run.",
    ),

    # ---- agents ----------------------------------------------------------
    Block(
        id="strategist", name="agents.strategist.plan", layer="agent",
        file="src/agents/strategist.py",
        role="Turns one customer instruction into 1-5 ordered, checkable sub-goals.",
        inputs=["instruction", "geometry index text", "tag vocabulary", "3 views of the input part"],
        outputs=["understanding", "subtasks: goal, rationale, focus, tags, envelope"],
        note="`envelope` is the contract the router later enforces: which bbox faces this sub-goal may move. Its prompt is where the selection rules live: pick the instance by the PROPERTY the sentence uses (`[BLIND]` vs `[THROUGH]`, sweep angle, largest radius) rather than by position, prove a position is empty before putting a new feature there, and size an unstated dimension long enough to reach through the surface it serves, because a feature buried inside its host adds almost nothing to diff F1.",
    ),
    Block(
        id="executor", name="agents.executor.build", layer="agent",
        file="src/agents/executor.py",
        role="Writes one CadQuery `my_cad_function` for exactly one sub-goal, and turns every failure mode into actionable feedback.",
        inputs=["sub-goal + tags", "geometry index", "verified recipes", "last accepted script", "feedback from the last attempt", "up to 6 view images"],
        outputs=["Python function source", "approach sentence", "feedback text for the next attempt"],
        note="Never sees the other sub-goals. `last_resort` mode swaps refinement for the bluntest thing that could work. Its prompt carries the list of API forms that do not exist in this build (cadquery 2.8.0 / OCP 7.9.3), and its feedback builders match a crash to the specific remedy for it — an empty `Bnd_Box` means the boolean produced nothing, \"cannot find a solid on the stack\" means the workplane was never seeded with the part.",
    ),
    Block(
        id="qa", name="agents.qa.review", layer="agent",
        file="src/agents/qa.py",
        role="The acceptance gate — a separate model that judges the edit against the sub-goal and can reject it.",
        inputs=["sub-goal + customer instruction", "the rest of the plan, labelled done / not run yet", "6 ortho views before + after", "measured diff", "cumulative diff", "its own earlier verdicts"],
        outputs=["achieved", "partial", "confidence", "issues[]", "plan_flaw", "guidance"],
        note="Three outcomes, not two: partial keeps the geometry and refines it, rejection discards it. Completeness is judged against THIS sub-goal only — the other sub-goals are quoted in the prompt so that work which has not been reached yet is not read as work that is missing, and the router drops any finding that slips through anyway.",
    ),

    # ---- tool layer ------------------------------------------------------
    Block(
        id="geometry", name="tools.geometry", layer="tool",
        file="src/tools/geometry.py",
        role="Turns an opaque B-rep into a queryable index, and two STEPs into a measured diff.",
        inputs=["STEP path", "before/after STEP pair"],
        outputs=["inspect(): hole families, cylinders, planar faces, BLIND/THROUGH per opening, bores and ports as paired mouths",
                 "to_prompt(): compact index text",
                 "compare(): faces/edges/volume delta, envelope growth, frame drift, no-op, phantom material, wrong direction"],
        note="Selection by measured radius and position is what replaces guessing selector strings — and where the instruction selects by a PROPERTY (\"that slot\", \"the end without a fillet\"), the index states the property: every opening is labelled `[BLIND]` or `[THROUGH]` (a solid-classifier probe between the two mouths, so two pockets sunk from opposite faces are not read as one through-feature), and two coaxial rings of one radius are printed as the single bore or port they bound, with its length.",
    ),
    Block(
        id="skillref", name="tools.skillref", layer="tool",
        file="src/tools/skillref.py",
        role="Selects the verified recipe sections for a sub-goal from the cadquery-editor skill, within a token budget.",
        inputs=["instruction", "sub-goal", "focus terms", "geometric tags"],
        outputs=["recipe text", "section ids", "tag vocabulary for the strategist prompt"],
        note="A tag pins a section outright; keyword scoring fills the rest of the budget.",
    ),
    Block(
        id="focus", name="tools.focus", layer="tool",
        file="src/tools/focus.py",
        role="Deterministic cue extraction from text: which feature topics are named, which sizes are quoted and what each measures, which directions are mentioned.",
        inputs=["instruction", "sub-goal", "strategist focus terms"],
        outputs=["topics", "sizes (mm, plus angles in degrees)", "directions"],
        note="Regex only, zero tokens, touches no B-rep. Its geometry-slicing half was removed: retrieval may decide which RECIPE to attach, never which geometry an agent is allowed to see.",
    ),
    Block(
        id="lint", name="tools.lint", layer="tool",
        file="src/tools/lint.py",
        role="Static check of the generated source for APIs that are certain to fail here, before a subprocess is spent.",
        inputs=["generated function source"],
        outputs=["[(rule, precise remedy)]", "repair feedback text"],
        note="A hit is repaired in place inside the same attempt — a banned-API typo is not a design decision. Two checks feed the same repair loop: a hand-written table of named traps, each harvested after it cost a run, and a generic one that parses the script and asks the INSTALLED cadquery/OCP whether an attribute really exists, offering the closest real name back. The generic half only reports a CALLED attribute on a name it typed with certainty (a constructor, or a chain of methods CadQuery annotates as returning their own type), skips anything dynamic, and is wrapped so introspection can never be the reason a good script is rejected. Whole-line comments are blanked before matching, after a correct script was rejected for a remark in its own comment.",
    ),
    Block(
        id="runner", name="tools.runner.run_script", layer="tool",
        file="src/tools/runner.py",
        role="Executes the generated function in an isolated subprocess with a timeout.",
        inputs=["function source", "input STEP", "attempt dir"],
        outputs=["ok", "info: step path, faces, edges, volume, bbox", "trimmed stdout/stderr log"],
    ),
    Block(
        id="exec_script", name="tools.exec_script", layer="tool",
        file="src/tools/exec_script.py",
        role="The child process: exec the function, unwrap the return value, gate validity, export tmp.step.",
        inputs=["--function_file", "--input_file", "--output_dir"],
        outputs=["tmp.step", "RESULT: {json} line on stdout", "the function's own prints"],
        note="Validity is judged relative to the input — 3 of the 48 inputs are already invalid B-reps.",
    ),
    Block(
        id="render", name="tools.render", layer="tool",
        file="src/tools/render.py",
        role="Renders the seven benchmark projections of a STEP, and exports the scored STL.",
        inputs=["STEP path", "view list", "image size"],
        outputs=["{view: png}", "tmp.stl"],
    ),
    Block(
        id="render_proc", name="tools.render_proc", layer="tool",
        file="src/tools/render_proc.py",
        role="The child process that actually drives OCC's offscreen viewer.",
        inputs=["--step", "--views", "--stl"],
        outputs=["PNG per view", "STL", "RENDER: {json} line"],
        note="Must not run on a background thread: OCC's macOS viewer needs a process main thread, and fails silently off it.",
    ),
    Block(
        id="config", name="config", layer="tool",
        file="src/config.py",
        role="Every tunable in one place, overridable from src/.env.",
        inputs=[".env", "environment variables"],
        outputs=["model ids per role", "loop budgets", "view lists", "paths", "cost rates"],
    ),

    # ---- external --------------------------------------------------------
    Block(
        id="api", name="OpenAI-compatible API", layer="external",
        role="Serves the three agent roles; one model id per role, so a cheaper QA model is a real cost lever.",
        inputs=["chat.completions with json_object response format"],
        outputs=["JSON replies", "usage counters"],
    ),
    Block(
        id="cadquery", name="CadQuery / OCC kernel", layer="external",
        role="Does the actual B-rep work: import STEP, edit, export STEP/STL, render projections.",
        inputs=["STEP", "generated Python"],
        outputs=["edited solid", "STEP", "STL", "PNG views"],
        note="Refuses some edits Fusion performed happily — BRep_API: command not done on whole hole families at every size.",
    ),
    Block(
        id="dataset", name="dataset + mongita DB", layer="external",
        role="The 48 requests, the 237 STEP/STL parts and their renders, and the records that say which part is an input and which is an answer key.",
        inputs=["request_id"],
        outputs=["instruction text", "input .step", "ground-truth .stl"],
        note="sourcecode/IDETC26-…/data/edit_192_external — CC BY-NC 4.0, not redistributed.",
    ),
    Block(
        id="metrics", name="benchmark metric code", layer="external",
        role="The repo's own evals, imported rather than reimplemented so our numbers are comparable to the published baselines.",
        inputs=["ground-truth STL", "predicted STL", "start STL for diff F1"],
        outputs=["chamfer similarity (norm)", "volume F1", "diff F1"],
        note="src/utils/evals_diff.py + evals_feature_geometric.py in the submodule.",
    ),
    Block(
        id="skillfile", name="Skills/reference/recipes_edit.md", layer="external",
        role="The verified CadQuery recipe book the executor is fed sections of.",
        inputs=["section ids selected by tag and keyword"],
        outputs=["recipe text within RECIPES_MAX_TOKENS"],
    ),
]

EDGES = [
    # entry -> orchestration
    Edge("dataset", "pipeline", "instruction + input STEP", "data",
         ("src/pipeline.py", "db.requests.find_one")),
    Edge("pipeline", "state", "new SessionState", "data",
         ("src/pipeline.py", "SessionState(")),
    Edge("pipeline", "router", "run()", "flow",
         ("src/pipeline.py", "router.run()")),
    Edge("dashboard", "pipeline", "run_request on a thread", "flow",
         ("tools/dashboard.py", "run_request")),
    Edge("dashboard", "evaluate", "score_output", "flow",
         ("tools/dashboard.py", "score_output")),
    Edge("dashviz", "dashboard", "Plotly figures + design tokens", "data",
         ("tools/dashboard.py", "dashviz")),
    Edge("dataset", "browse", "records + renders", "data",
         ("tools/browse.py", "db.")),
    Edge("run_headless", "dashboard", "_run_pipeline(rid, user) on a worker thread", "flow",
         ("tools/run_headless.py", "_run_pipeline")),

    # router fan-out
    Edge("router", "geometry", "inspect the input, re-index after each accept", "flow",
         ("src/adk/router.py", "geo.inspect(")),
    Edge("router", "render", "render the before views", "flow",
         ("src/adk/router.py", "rnd.render_views(")),
    Edge("router", "strategist", "plan(state)", "flow",
         ("src/adk/router.py", "strategist.plan(")),
    Edge("router", "executor", "build(sub-goal, feedback)", "flow",
         ("src/adk/router.py", "executor.build(")),
    Edge("router", "lint", "check(script) before running it", "flow",
         ("src/adk/router.py", "lint.check(")),
    Edge("router", "runner", "run_script(script, source STEP)", "flow",
         ("src/adk/router.py", "runner.run_script(")),
    Edge("router", "geometry", "compare(before, after) -> gates", "flow",
         ("src/adk/router.py", "geo.compare(")),
    Edge("router", "qa", "review(before views, after views, diff)", "flow",
         ("src/adk/router.py", "qa.review(")),
    Edge("router", "state", "checkpoint / revert to best", "data",
         ("src/adk/router.py", "s.checkpoints.append(")),
    Edge("router", "render", "finalize: 7 views + tmp.stl", "flow",
         ("src/adk/router.py", "render_and_export(")),

    # agents -> llm -> api
    Edge("strategist", "llm", "json(system, parts)", "flow",
         ("src/agents/strategist.py", "llm.json(")),
    Edge("executor", "llm", "json(system, parts)", "flow",
         ("src/agents/executor.py", "llm.json(")),
    Edge("qa", "llm", "json(system, parts)", "flow",
         ("src/agents/qa.py", "llm.json(")),
    Edge("llm", "api", "chat.completions", "flow",
         ("src/adk/llm.py", "chat.completions.create")),

    # agent -> tool
    Edge("strategist", "skillref", "tags_help()", "data",
         ("src/agents/strategist.py", "skl.tags_help(")),
    Edge("executor", "skillref", "recipes_for(tags, focus)", "flow",
         ("src/agents/executor.py", "skl.recipes_for(")),
    Edge("skillref", "focus", "extract_cues() — topics, sizes, directions", "data",
         ("src/tools/skillref.py", "foc.extract_cues(")),
    Edge("skillref", "skillfile", "load + score sections", "data",
         ("src/tools/skillref.py", "SKILLS_DIR")),

    # tool -> subprocess -> kernel
    Edge("runner", "exec_script", "subprocess", "proc",
         ("src/tools/runner.py", "subprocess.run")),
    Edge("render", "render_proc", "subprocess", "proc",
         ("src/tools/render.py", "subprocess.run")),
    Edge("exec_script", "cadquery", "exec the generated edit", "flow",
         ("src/tools/exec_script.py", "importStep")),
    Edge("render_proc", "cadquery", "render_to_png + STL export", "flow",
         ("src/tools/render_proc.py", "render_to_png")),
    Edge("geometry", "cadquery", "topology + measurement", "data",
         ("src/tools/geometry.py", "importStep")),

    # scoring
    Edge("router", "evaluate", "submission folder", "data", ()),
    Edge("dataset", "evaluate", "ground-truth STL", "data",
         ("src/evaluate.py", "stl_of(")),
    Edge("evaluate", "metrics", "evals_diff + evals_feature_geometric", "flow",
         ("src/evaluate.py", "evals_diff")),

    # config is read by everyone; one edge keeps the picture readable
    Edge("config", "router", "budgets, view lists, model ids", "data",
         ("src/adk/router.py", "config.MAX_ATTEMPTS_PER_SUBTASK")),
]

# ---------------------------------------------------------------------------
# The signal-flow view — what the SVG block diagram draws
#
# BLOCKS/EDGES above is a MODULE map: who calls whom. This is the other half,
# the one an engineer reads first: what SIGNAL travels along each wire, in the
# order one attempt actually flows. Blocks are placed on an explicit grid
# because a hand-placed schematic beats an auto-routed one every time, and the
# placement is stable enough that it only changes when the pipeline does.
#
# Ports are DERIVED from the wires: every signal arriving at a block becomes an
# input port, every signal leaving becomes an output port, deduplicated by name.
# So a block cannot show a port nothing is connected to, and a wire cannot exist
# without appearing on both blocks it touches.
# ---------------------------------------------------------------------------

@dataclass
class Sig:
    """One wire: `signal` is what travels along it."""
    src: str
    dst: str
    signal: str
    src_port: str = ""       # defaults to `signal`
    dst_port: str = ""       # defaults to `signal`
    route: str = "auto"      # auto | lane | margin
    lane: int = 0            # which horizontal channel, when routed
    chan: int = 0            # which left-margin channel, when routed
    kind: str = "fwd"        # fwd | back

    @property
    def sp(self):
        return self.src_port or self.signal

    @property
    def dp(self):
        return self.dst_port or self.signal


@dataclass
class SBlock:
    id: str
    name: str                # the label under the box
    row: int
    col: int
    kind: str                # data | tool | agent | orch | out
    sub: str = ""            # second line under the box (the file)


# kind -> (fill, stroke, label for the legend)
SKINDS = {
    "data":  ("#e8edf4", "#5b6b83", "dataset / files on disk"),
    "orch":  ("#e9e3fb", "#6d3fc4", "router — the state machine"),
    "agent": ("#d9f4e2", "#1c7a45", "LLM agent (one JSON call via adk/llm.py)"),
    "tool":  ("#fdeec8", "#a2691a", "deterministic tool"),
    "proc":  ("#fde4dc", "#b4441f", "runs CadQuery in a child process"),
    "out":   ("#d7e6fb", "#1f4fa8", "what gets shipped and scored"),
}

SBLOCKS = [
    # row 0 — read the task and look at the part
    SBlock("dataset", "Task library", 0, 0, "data",
           "sourcecode/…/data/edit_192_external"),
    SBlock("geo_index", "Part inspector", 0, 1, "tool",
           "src/tools/geometry.py"),
    SBlock("views_in", "Camera rig", 0, 2, "proc",
           "src/tools/render.py"),
    SBlock("strategist", "Strategist", 0, 3, "agent",
           "src/agents/strategist.py"),

    # row 1 — decide what to do next and write the code
    SBlock("router", "Run controller", 1, 0, "orch",
           "src/adk/router.py"),
    SBlock("skillref", "Skill provider", 1, 1, "tool",
           "src/tools/skillref.py"),
    SBlock("executor", "Executor", 1, 2, "agent",
           "src/agents/executor.py"),
    SBlock("lint", "Regex check", 1, 3, "tool",
           "src/tools/lint.py"),

    # row 2 — run it, measure it, look at it, judge it
    SBlock("runner", "Sandbox runner", 2, 0, "proc",
           "src/tools/runner.py → exec_script.py"),
    SBlock("geo_diff", "Change measurer", 2, 1, "tool",
           "src/tools/geometry.py"),
    SBlock("views_out", "Camera rig (after)", 2, 2, "proc",
           "src/tools/render.py"),
    SBlock("qa", "QA inspector", 2, 3, "agent",
           "src/agents/qa.py"),

    # row 3 — ship it and score it
    SBlock("finalize", "Submission writer", 3, 0, "orch",
           "src/adk/router.py"),
    SBlock("submission", "Submission folder", 3, 1, "out",
           "results/runs/<user>/outputs/…"),
    SBlock("evaluate", "Benchmark scorer", 3, 2, "tool",
           "src/evaluate.py"),
    SBlock("scores", "The three metrics", 3, 3, "out",
           "0.0 for a crash, a no-op or a missing file"),
]

SIGNALS = [
    # --- row 0 : read the task -----------------------------------------
    Sig("dataset", "geo_index", "input .step"),
    Sig("dataset", "views_in", "input .step", src_port="input .step"),
    Sig("dataset", "strategist", "instruction", route="lane", lane=0),
    Sig("geo_index", "strategist", "geometry index"),
    Sig("views_in", "strategist", "3 views"),

    # --- row 0 -> row 1 : the plan -------------------------------------
    Sig("strategist", "router", "ordered sub-goals", route="margin", chan=0),

    # --- row 1 : write one function ------------------------------------
    Sig("router", "skillref", "tags + focus terms"),
    Sig("skillref", "executor", "verified recipes"),
    Sig("router", "executor", "sub-goal + feedback", route="lane", lane=1),
    Sig("geo_index", "executor", "geometry index", src_port="geometry index",
        route="margin", chan=1),
    Sig("executor", "lint", "function source"),
    Sig("lint", "executor", "repair request", route="lane", lane=2, kind="back"),

    # --- row 1 -> row 2 : run it ---------------------------------------
    Sig("lint", "runner", "clean source", route="margin", chan=2),
    Sig("router", "runner", "current .step", route="lane", lane=3),

    # --- row 2 : measure, look, judge ----------------------------------
    Sig("runner", "geo_diff", "tmp.step"),
    Sig("runner", "views_out", "tmp.step", src_port="tmp.step",
        route="lane", lane=4),
    Sig("runner", "executor", "traceback + prints", route="margin", chan=3,
        kind="back"),
    Sig("geo_diff", "qa", "measured diff", route="lane", lane=5),
    Sig("views_out", "qa", "6 ortho views"),
    Sig("geo_diff", "router", "gate verdicts", route="margin", chan=4,
        kind="back"),
    Sig("qa", "router", "accept / partial / reject", route="margin", chan=5,
        kind="back"),

    # --- row 3 : ship and score ----------------------------------------
    Sig("router", "finalize", "accepted .step", route="margin", chan=6),
    Sig("finalize", "submission", "tmp.step + tmp.stl + 7 jpgs"),
    Sig("submission", "evaluate", "predicted .stl"),
    Sig("evaluate", "scores", "chamfer · volume F1 · diff F1"),
    Sig("dataset", "evaluate", "ground-truth .stl", src_port="ground-truth .stl",
        route="margin", chan=7),
]

# ---------------------------------------------------------------------------
# The agent view — the same three agents, opened up
#
# The schematic above draws each agent as a single block, which hides the thing
# that actually decides whether a run scores: what goes INTO each prompt, what
# is parsed back OUT of it, and which of those outputs come back round as the
# next attempt's input. That is what this second schematic shows, and the loops
# are drawn as loops rather than described in prose.
# ---------------------------------------------------------------------------

ABLOCKS = [
    # row 0 — plan once, per request
    SBlock("plan_src", "What the planner sees", 0, 0, "data",
           "instruction + the measured part"),
    SBlock("strategist", "Strategist", 0, 1, "agent",
           "src/agents/strategist.py"),
    SBlock("queue", "Sub-goal queue", 0, 2, "orch",
           "src/adk/router.py · 1-5 sub-goals, in order"),

    # row 1 — write one function, per attempt
    SBlock("exec_src", "What the coder sees", 1, 0, "data",
           "index · recipes · the last script"),
    SBlock("executor", "Executor", 1, 1, "agent",
           "src/agents/executor.py"),
    SBlock("gates", "Run it, then gate it", 1, 2, "tool",
           "src/adk/router.py · lint · crash · no-op · "
           "phantom · direction · drift · envelope"),

    # row 2 — judge it, per attempt
    SBlock("qa_src", "What the inspector sees", 2, 0, "data",
           "pictures + numbers, before and after"),
    SBlock("qa", "QA inspector", 2, 1, "agent", "src/agents/qa.py"),
    SBlock("decide", "Verdict applied", 2, 2, "orch",
           "src/adk/router.py · 3 proposals per sub-goal"),

    # row 3 — the two shared services every agent goes through
    SBlock("feedback", "Feedback builders", 3, 0, "tool",
           "src/agents/executor.py · one per failure mode"),
    SBlock("llm", "Model gateway", 3, 1, "tool",
           "src/adk/llm.py · one model id per role"),
]

ASIGNALS = [
    # --- the plan, made once -------------------------------------------
    Sig("plan_src", "strategist", "instruction"),
    Sig("plan_src", "strategist", "geometry index"),
    Sig("plan_src", "strategist", "3 views of the part"),
    Sig("plan_src", "strategist", "tag vocabulary (13)"),
    Sig("strategist", "queue", "sub-goals + tags + envelope"),
    Sig("strategist", "llm", "prompt ⇄ validated JSON", kind="bidi",
        route="lane"),

    # --- one attempt ----------------------------------------------------
    Sig("queue", "executor", "current sub-goal", route="lane"),
    Sig("exec_src", "executor", "geometry index", src_port="geometry index"),
    Sig("exec_src", "executor", "verified recipes"),
    Sig("exec_src", "executor", "the accepted script"),
    Sig("exec_src", "executor", "current + last views"),
    Sig("feedback", "executor", "what to do differently", route="lane",
        kind="back"),
    Sig("executor", "gates", "my_cad_function"),
    Sig("executor", "llm", "prompt ⇄ validated JSON", kind="bidi",
        src_port="prompt ⇄ validated JSON", route="lane"),
    Sig("gates", "executor", "repair in place, no attempt spent", route="lane",
        kind="back"),
    Sig("gates", "feedback", "crash · no-op · phantom · direction · drift · envelope",
        route="lane"),

    # --- the judgement --------------------------------------------------
    Sig("gates", "qa_src", "tmp.step that survived the gates", route="lane"),
    Sig("qa_src", "qa", "the sub-goal + the instruction"),
    Sig("qa_src", "qa", "the rest of the plan"),
    Sig("qa_src", "qa", "6 views before, 6 after"),
    Sig("qa_src", "qa", "measured diff"),
    Sig("qa_src", "qa", "total change since the original"),
    Sig("qa_src", "qa", "your own earlier verdicts"),
    Sig("qa", "decide", "achieved · partial · confidence · issues · guidance"),
    Sig("qa", "llm", "prompt ⇄ validated JSON",
        src_port="prompt ⇄ validated JSON", kind="bidi", route="lane"),
    Sig("decide", "feedback", "rejected: start fresh / partial: fix in place",
        route="lane", kind="back"),
    Sig("decide", "queue", "sub-goal settled — advance the cursor",
        route="lane", kind="back"),
]

# Files that exist for packaging reasons and need no block of their own.
IGNORED_FILES = {"src/__init__.py", "src/adk/__init__.py",
                 "src/agents/__init__.py", "src/tools/__init__.py",
                 "tools/blockdiagram.py",
                 "test/freecad_model.py", "test/your_model.py"}


# ---------------------------------------------------------------------------
# Derivation from the source
# ---------------------------------------------------------------------------

@dataclass
class FileFacts:
    path: str
    exists: bool = False
    loc: int = 0
    sha: str = ""
    summary: str = ""
    api: list = field(default_factory=list)


def _summary_of(doc: str) -> str:
    """First paragraph of a docstring, collapsed onto one line."""
    if not doc:
        return ""
    para = doc.strip().split("\n\n")[0]
    return re.sub(r"\s+", " ", para).strip()


def _sig(fn: ast.AST) -> str:
    a = fn.args
    names = [p.arg for p in a.posonlyargs] + [p.arg for p in a.args]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [p.arg for p in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return f"{fn.name}({', '.join(names)})"


def read_file(rel: str) -> FileFacts:
    f = FileFacts(path=rel)
    p = ROOT / rel
    if not p.is_file():
        return f
    src = p.read_text(encoding="utf-8", errors="replace")
    f.exists = True
    f.loc = len(src.splitlines())
    f.sha = hashlib.sha256(src.encode()).hexdigest()[:8]
    try:
        tree = ast.parse(src)
    except SyntaxError:
        f.summary = "(unparseable)"
        return f
    f.summary = _summary_of(ast.get_docstring(tree) or "")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            f.api.append(_sig(node))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = [_sig(m) for m in node.body
                       if isinstance(m, ast.FunctionDef)
                       and not m.name.startswith("_")]
            f.api.append(node.name + (f" [{', '.join(methods)}]" if methods else ""))
    return f


def scan_modules() -> list:
    """Every .py file in the scanned trees, repo-relative and sorted."""
    found = []
    for root in SCAN_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            rel = p.relative_to(ROOT)
            if any(part in SCAN_EXCLUDE for part in rel.parts):
                continue
            found.append(str(rel))
    return found


def config_knobs() -> list:
    """(name, env var, default, comment) for every os.environ-backed setting."""
    p = ROOT / "src/config.py"
    if not p.is_file():
        return []
    src = p.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    def env_call(node):
        """The os.environ.get(...) call anywhere inside an expression."""
        for n in ast.walk(node):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "get"
                    and isinstance(n.func.value, ast.Attribute)
                    and n.func.value.attr == "environ"):
                return n
        return None

    def literal(node):
        try:
            return repr(ast.literal_eval(node))
        except Exception:
            return "…"

    knobs = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        call = env_call(node.value)
        if call is None or not call.args:
            continue
        name = node.targets[0].id
        var = literal(call.args[0]).strip("'\"")
        default = literal(call.args[1]) if len(call.args) > 1 else "unset"
        # the comment block immediately above the assignment, if any
        i, comment = node.lineno - 2, []
        while i >= 0 and lines[i].lstrip().startswith("#"):
            comment.insert(0, lines[i].lstrip("# ").strip())
            i -= 1
        knobs.append((name, var, default, " ".join(comment)))
    return knobs


def git_provenance() -> str:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    commit = run("rev-parse", "--short", "HEAD") or "unknown"
    dirty = " + uncommitted changes" if run("status", "--porcelain") else ""
    return f"`{commit}`{dirty}"


# ---------------------------------------------------------------------------
# Mermaid rendering
# ---------------------------------------------------------------------------

def esc(text: str) -> str:
    """Make a string safe inside a quoted mermaid label.

    Mermaid sets node labels as innerHTML, so a path template like
    `<edit_id>/brep_end/` is parsed as an unknown tag and silently vanishes;
    `#` opens an entity reference; `{}` and `"` end the label early.
    """
    return (str(text).replace('"', "'").replace("#", "no.")
            .replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", " ").replace("{", "(").replace("}", ")"))


def node(b: Block, ports: int = 2) -> str:
    """One mermaid node: the name over its top input and output ports."""
    ins = ", ".join(b.inputs[:ports]) + ("…" if len(b.inputs) > ports else "")
    outs = ", ".join(b.outputs[:ports]) + ("…" if len(b.outputs) > ports else "")
    label = f"<b>{esc(b.name)}</b>"
    if ins:
        label += f"<br/><i>in ▸</i> {esc(ins)}"
    if outs:
        label += f"<br/><i>out ▸</i> {esc(outs)}"
    return f'    {b.id}["{label}"]'


ARROWS = {"flow": "-->", "data": "-.->", "proc": "==>", "opt": "-.->"}


def edge_line(e: Edge) -> str:
    arrow = ARROWS.get(e.kind, "-->")
    label = esc(e.label) + (" (optional)" if e.kind == "opt" else "")
    return f'    {e.src} {arrow}|"{label}"| {e.dst}' if label else f"    {e.src} {arrow} {e.dst}"


def block_diagram(blocks: list) -> str:
    by_layer = {k: [b for b in blocks if b.layer == k] for k in LAYERS}
    L = ["```mermaid", "flowchart TB"]
    for key, (title, *_rest) in LAYERS.items():
        members = by_layer[key]
        if not members:
            continue
        L.append(f'  subgraph {key}_g["{esc(title)}"]')
        L.append("    direction TB")
        L += [node(b) for b in members]
        L.append("  end")
    L.append("")
    L += [edge_line(e) for e in EDGES]
    L.append("")
    for key, (_t, fill, stroke) in LAYERS.items():
        L.append(f"  classDef {key} fill:{fill},stroke:{stroke},stroke-width:1px,color:#0b1220;")
    for key in LAYERS:
        ids = ",".join(b.id for b in by_layer[key])
        if ids:
            L.append(f"  class {ids} {key};")
    L.append("```")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# SVG rendering — a schematic, not a graph dump
#
# Mermaid renders on GitHub and in most Markdown viewers, but not everywhere,
# and its auto-router will not give you a schematic: named ports on the block
# edges, labelled signal lines, feedback routed cleanly around the back. So the
# signal-flow view is drawn here, by hand, in dependency-free SVG.
#
# Routing is orthogonal, as a schematic's is. Two cases:
#   * neighbours in the same row -> a three-segment elbow through the gap
#     between their columns;
#   * everything else -> out of the source, down into a horizontal LANE in the
#     gap below the source's row, along it, then up or down a vertical that
#     rides a COLUMN GAP (never over a block) into the target's left edge.
# Lanes and column-gap verticals are allocated one per wire, so no two wires
# ever share a line and every corner is unambiguous.
# ---------------------------------------------------------------------------

SVG_FONT = ("ui-sans-serif, -apple-system, 'Segoe UI', Roboto, "
            "'Helvetica Neue', Arial, sans-serif")

# Text metrics without a font engine. 0.545em per character is a good average
# for this stack at these sizes; every box is sized from it with slack.
_CHAR_W = 0.545


def _tw(text, size):
    return len(str(text)) * size * _CHAR_W


def _xml(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _svg_text(x, y, text, size=9, weight="normal", anchor="start",
              fill="#1e293b", opacity=None, style=""):
    op = f' opacity="{opacity}"' if opacity is not None else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}" fill="{fill}"'
            f'{op}{style}>{_xml(text)}</text>')


def _rounded(points, r=4.0):
    """An orthogonal polyline as an SVG path with rounded corners."""
    pts = [points[0]]
    for p in points[1:]:
        if p != pts[-1]:
            pts.append(p)
    if len(pts) < 3:
        return "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    out = [f"M {pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        # step back from the corner along the incoming leg, and forward along
        # the outgoing one, by at most half of either leg
        rin = min(r, abs(x1 - x0) / 2 or r, abs(y1 - y0) / 2 or r)
        rout = min(r, abs(x2 - x1) / 2 or r, abs(y2 - y1) / 2 or r)
        rr = max(1.0, min(rin, rout))
        ax = x1 - rr if x1 > x0 else (x1 + rr if x1 < x0 else x1)
        ay = y1 - rr if y1 > y0 else (y1 + rr if y1 < y0 else y1)
        bx = x1 + rr if x2 > x1 else (x1 - rr if x2 < x1 else x1)
        by = y1 + rr if y2 > y1 else (y1 - rr if y2 < y1 else y1)
        out.append(f"L {ax:.1f},{ay:.1f}")
        out.append(f"Q {x1:.1f},{y1:.1f} {bx:.1f},{by:.1f}")
    out.append(f"L {pts[-1][0]:.1f},{pts[-1][1]:.1f}")
    return " ".join(out)


# geometry constants, all in SVG user units
PAD_L, PAD_R, PAD_T, PAD_B = 74, 46, 74, 82
COL_GAP = 104
PORT_H = 15.0            # vertical pitch of the port labels inside a block
BOX_TOP = 15.0           # padding above the first port
BOX_BOT = 11.0
MIN_BOX_H = 50.0
MIN_BOX_W = 132.0
MAX_BOX_W = 250.0
STUB = 13.0              # how far a wire leaves a block before it turns
LANE_PITCH = 12.0        # vertical spacing of horizontal lanes
LANE_EDGE = 48.0         # first lane clears the name + file caption
                         # printed under every block
VERT_PITCH = 8.0         # horizontal spacing of column-gap verticals


def _svg_layout(blocks, signals):
    """Place every block on the grid and resolve each wire's geometry."""
    byid = {b.id: b for b in blocks}
    for s in signals:                       # fail loudly on a typo
        for end in (s.src, s.dst):
            if end not in byid:
                raise KeyError(f"signal refers to unknown block {end!r}")

    # ports, in declaration order, deduplicated by label
    ins, outs = {b.id: [] for b in blocks}, {b.id: [] for b in blocks}
    for s in signals:
        if s.sp not in outs[s.src]:
            outs[s.src].append(s.sp)
        if s.dp not in ins[s.dst]:
            ins[s.dst].append(s.dp)

    # box sizes, then one width per column so the grid reads as a grid
    # Ports normally sit side by side, inputs left and outputs right. When the
    # two label columns cannot both fit they are STACKED instead — every input,
    # then every output — because a box whose port names overlap each other is
    # worse than a taller box.
    size, stack = {}, {}
    for b in blocks:
        wi = max([_tw(t, 9) for t in ins[b.id]] or [0])
        wo = max([_tw(t, 9) for t in outs[b.id]] or [0])
        stacked = wi + wo + 34 > MAX_BOX_W
        stack[b.id] = stacked
        rows = (len(ins[b.id]) + len(outs[b.id]) if stacked
                else max(len(ins[b.id]), len(outs[b.id])))
        w = min(MAX_BOX_W, max(MIN_BOX_W, _tw(b.name, 10) + 24,
                               max(wi, wo) + 24 if stacked else wi + wo + 34))
        size[b.id] = (w, max(MIN_BOX_H, BOX_TOP + rows * PORT_H + BOX_BOT))

    ncol = max(b.col for b in blocks) + 1
    nrow = max(b.row for b in blocks) + 1
    col_w = [max(size[b.id][0] for b in blocks if b.col == c) for c in range(ncol)]
    row_h = [max(size[b.id][1] for b in blocks if b.row == r) for r in range(nrow)]

    col_x, x = [], PAD_L
    for c in range(ncol):
        col_x.append(x)
        x += col_w[c] + COL_GAP
    total_w = x - COL_GAP + PAD_R

    # how many lanes each row gap must carry decides how tall that gap is
    lane_edges = [s for s in signals if _svg_needs_lane(s, byid)]
    per_row = {}
    for s in lane_edges:
        per_row.setdefault(byid[s.src].row, []).append(s)
    lane_seq = {id(s): i for row in per_row.values() for i, s in enumerate(row)}

    row_y, y = [], PAD_T
    for r in range(nrow):
        row_y.append(y)
        gap = LANE_EDGE + max(0, len(per_row.get(r, [])) - 1) * LANE_PITCH
        y += row_h[r] + max(58.0, gap + 16.0)
    total_h = y - max(58.0, LANE_EDGE) + PAD_B

    place = {}
    for b in blocks:
        w, h = col_w[b.col], size[b.id][1]
        place[b.id] = {"x": col_x[b.col], "y": row_y[b.row] + (row_h[b.row] - h) / 2,
                       "w": w, "h": h, "ins": ins[b.id], "outs": outs[b.id],
                       # outputs start below the inputs on a stacked block
                       "ooff": len(ins[b.id]) if stack[b.id] else 0}
    return {"place": place, "byid": byid, "col_x": col_x, "col_w": col_w,
            "row_y": row_y, "row_h": row_h, "lane_seq": lane_seq,
            "per_row": per_row, "w": total_w, "h": total_h,
            "blocks": blocks, "signals": signals}


def _svg_needs_lane(s, byid):
    """True when a plain elbow would have to cross a block to get there."""
    a, b = byid[s.src], byid[s.dst]
    return not (a.row == b.row and b.col == a.col + 1)


def _port_xy(L, bid, label, side):
    p = L["place"][bid]
    seq = p["ins"] if side == "in" else p["outs"]
    i = seq.index(label) + (0 if side == "in" else p["ooff"])
    y = p["y"] + BOX_TOP + i * PORT_H + PORT_H / 2
    return (p["x"] if side == "in" else p["x"] + p["w"]), y


def _svg_blocks(L):
    out = []
    for b in L["blocks"]:
        p = L["place"][b.id]
        fill, stroke, _ = SKINDS[b.kind]
        x, y, w, h = p["x"], p["y"], p["w"], p["h"]
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                   f'height="{h:.1f}" rx="3" fill="{fill}" stroke="{stroke}" '
                   f'stroke-width="1.2" filter="url(#sh)"/>')
        for i, t in enumerate(p["ins"]):
            yy = y + BOX_TOP + i * PORT_H + PORT_H / 2 + 3.2
            out.append(_svg_text(x + 8, yy, t, 9, fill="#0f172a"))
        for i, t in enumerate(p["outs"], start=p["ooff"]):
            yy = y + BOX_TOP + i * PORT_H + PORT_H / 2 + 3.2
            out.append(_svg_text(x + w - 8, yy, t, 9, anchor="end", fill="#0f172a"))
        out.append(_svg_text(x + w / 2, y + h + 14, b.name, 10, weight="600",
                             anchor="middle", fill="#0f172a"))
        if b.sub:
            out.append(_svg_text(x + w / 2, y + h + 25, b.sub, 8,
                                 anchor="middle", fill="#64748b"))
    return out


def _svg_wires(L):
    byid, out = L["byid"], []
    # Verticals are allocated per ROW and column-gap, not per column alone.
    # Keyed on the column only, the counter never reset between rows: the
    # fourth row of elbows through the same gap was pushed 100 px right of the
    # midpoint, which put the vertical inside the block it was arriving at and
    # dropped the arrowhead on top of that block's port labels.
    gaps = {}
    for s in L["signals"]:
        if not _svg_needs_lane(s, byid):
            k = (byid[s.src].row, byid[s.src].col)
            gaps[k] = gaps.get(k, 0) + 1
    vert_used = {}

    for s in L["signals"]:
        x1, y1 = _port_xy(L, s.src, s.sp, "out")
        x2, y2 = _port_xy(L, s.dst, s.dp, "in")
        colour = {"back": "#b45309", "bidi": "#1f4fa8"}.get(s.kind, "#334155")
        marker = {"back": "ab", "bidi": "as"}.get(s.kind, "af")
        start = ' marker-start="url(#as)"' if s.kind == "bidi" else ""

        if not _svg_needs_lane(s, byid):
            gap_key = (byid[s.src].row, byid[s.src].col)
            k = vert_used.setdefault(gap_key, 0)
            vert_used[gap_key] = k + 1
            # fan the verticals symmetrically about the middle of the gap, then
            # clamp them inside it, so no amount of wires can reach a block
            n = gaps.get(gap_key, 1)
            xm = (x1 + x2) / 2 + (k - (n - 1) / 2.0) * VERT_PITCH
            lo, hi = min(x1, x2) + STUB, max(x1, x2) - STUB
            if lo < hi:
                xm = max(lo, min(hi, xm))
            pts = [(x1, y1), (xm, y1), (xm, y2), (x2, y2)]
            # The port labels inside both blocks already name this signal, and
            # a third copy of it across a 100px gap only collides with them.
            label = "" if (s.sp == s.signal and s.dp == s.signal) else s.signal
            lx, ly, anchor = x1 + 6, y1 - 5, "start"
        else:
            srow = byid[s.src].row
            seq = L["lane_seq"][id(s)]
            lane_y = (L["row_y"][srow] + L["row_h"][srow] + LANE_EDGE
                      + seq * LANE_PITCH)
            xe = x1 + STUB + (seq % 3) * VERT_PITCH
            xa = x2 - STUB - (seq % 4) * VERT_PITCH
            pts = [(x1, y1), (xe, y1), (xe, lane_y), (xa, lane_y),
                   (xa, y2), (x2, y2)]
            label = s.signal
            lx = xe + (xa - xe) * (0.34 + 0.16 * (seq % 3))
            ly, anchor = lane_y - 4, "middle"

        out.append(f'<path d="{_rounded(pts)}" fill="none" stroke="{colour}" '
                   f'stroke-width="1.25"{start} marker-end="url(#{marker})"/>')
        if label:
            out.append(_svg_text(lx, ly, label, 8.5, anchor=anchor,
                                 fill=colour, style=' class="lbl"'))
    return out


def _svg_legend(L, y):
    """Only the kinds and wire styles this particular diagram actually uses."""
    out, x = [], PAD_L
    used = {b.kind for b in L["blocks"]}
    kinds = {k for s in L["signals"] for k in [s.kind]}

    def item(draw, text):
        nonlocal x, y
        if x + 26 + _tw(text, 8.5) > L["w"] - PAD_R:
            x, y = PAD_L, y + 15
        out.append(draw(x, y))
        out.append(_svg_text(x + 20, y + 1, text, 8.5, fill="#475569"))
        x += 28 + _tw(text, 8.5)

    for kind, (fill, stroke, text) in SKINDS.items():
        if kind not in used:
            continue
        item(lambda px, py, f=fill, s=stroke:
             f'<rect x="{px:.1f}" y="{py - 8:.1f}" width="13" height="11" '
             f'rx="2" fill="{f}" stroke="{s}" stroke-width="1.1"/>', text)

    for kind, colour, marker, text in (
            ("back", "#b45309", "ab", "feedback — the attempt is retried"),
            ("bidi", "#1f4fa8", "as", "request ⇄ reply — one LLM call")):
        if kind in kinds:
            item(lambda px, py, c=colour, m=marker:
                 f'<line x1="{px:.1f}" y1="{py - 3:.1f}" x2="{px + 15:.1f}" '
                 f'y2="{py - 3:.1f}" stroke="{c}" stroke-width="1.25" '
                 f'marker-end="url(#{m})"/>', text)
    return out, y


def build_svg(blocks, signals, title, subtitle):
    """One schematic, as a standalone SVG document."""
    L = _svg_layout(blocks, signals)
    body = _svg_blocks(L) + _svg_wires(L)
    legend, last_y = _svg_legend(L, L["h"] - 32)
    height = max(L["h"], last_y + 26)

    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{L["w"]:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {L["w"]:.0f} {height:.0f}" '
        f'font-family="{SVG_FONT}">',
        '<defs>',
        '  <filter id="sh" x="-20%" y="-20%" width="150%" height="160%">',
        '    <feDropShadow dx="1.4" dy="1.8" stdDeviation="1.3" '
        'flood-color="#0f172a" flood-opacity="0.18"/>',
        '  </filter>',
        '  <marker id="af" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">',
        '    <path d="M 0 1 L 9 5 L 0 9 z" fill="#334155"/></marker>',
        '  <marker id="ab" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">',
        '    <path d="M 0 1 L 9 5 L 0 9 z" fill="#b45309"/></marker>',
        '  <marker id="as" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
        '    <path d="M 0 1 L 9 5 L 0 9 z" fill="#1f4fa8"/></marker>',
        '  <style>.lbl{paint-order:stroke;stroke:#ffffff;stroke-width:2.6px;'
        'stroke-linejoin:round}</style>',
        '</defs>',
        f'<rect width="{L["w"]:.0f}" height="{height:.0f}" fill="#ffffff"/>',
        _svg_text(L["w"] / 2, 30, title, 16, weight="700", anchor="middle",
                  fill="#0f172a"),
        _svg_text(L["w"] / 2, 48, subtitle, 9.5, anchor="middle",
                  fill="#64748b"),
    ]
    return "\n".join(head + body + legend + ["</svg>", ""])


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

ASCII_STRIP = r"""
  request_id
      │
      ▼
 ┌──────────────┐   instruction + input .step
 │  benchmark   │──────────────────────────────┐
 │  DB (mongita)│                              │
 └──────────────┘                              ▼
                                    ┌─────────────────────┐
                                    │  geometry.inspect   │  opaque 266-face solid
                                    │  (B-rep index)      │  ──► hole families, cylinders,
                                    └─────────┬───────────┘      planar faces, bbox
                                              │ index text
                                              ▼
 ┌───────────────────────────────────────────────────────────────────────────┐
 │                        StatefulRouter  (adk/router.py)                     │
 │                                                                            │
 │   STRATEGIST ──► ordered sub-goals  [goal, tags, envelope]                  │
 │        │                                                                   │
 │        ▼   for each sub-goal, up to 3 judged proposals                      │
 │   ┌──────────────────────────────────────────────────────────────────┐     │
 │   │ EXECUTOR ─► my_cad_function ─► lint ─► subprocess run ─► compare  │     │
 │   │      ▲                                                    │       │     │
 │   │      │              gates: crash · no-op · phantom         │       │     │
 │   │      │            direction · frame drift · envelope       ▼       │     │
 │   │      │                                            render 6 views   │     │
 │   │      │                                                    │       │     │
 │   │      └──── feedback ◄──── QA verdict ◄─── accept/partial/reject    │     │
 │   └──────────────────────────────────────────────────────────────────┘     │
 │                                                                            │
 │   accepted geometry only ──► checkpoint ──► revert to best ──► next sub-goal│
 └───────────────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
                        finalize: tmp.step + tmp.stl + 7 jpgs + settings.json
                                              │
                                              ▼
                        evaluate: chamfer similarity · volume F1 · diff F1
"""


CONTEXT_DIAGRAM = """```mermaid
flowchart LR
  A["<b>Input</b><br/>one .step B-rep<br/>+ one sentence of intent"]
  B["<b>This harness</b><br/>plan ▸ write CadQuery ▸ run ▸ look ▸ judge ▸ retry"]
  C["<b>Submission</b><br/>tmp.step + tmp.stl<br/>+ 7 rendered views"]
  D["<b>Scoring</b><br/>vs the human expert's edit<br/>in absolute coordinates"]
  E["<b>chamfer similarity</b><br/><b>volume F1</b><br/><b>diff F1</b> ◂ the one that counts"]
  A --> B --> C --> D --> E
  classDef io fill:#dbeafe,stroke:#1e40af,color:#0b1220;
  classDef sys fill:#ede9fe,stroke:#5b21b6,color:#0b1220;
  classDef out fill:#dcfce7,stroke:#166534,color:#0b1220;
  class A,C io;
  class B,D sys;
  class E out;
```"""


STATE_DIAGRAM = """```mermaid
stateDiagram-v2
  direction TB

  Prepare: PREPARE — index the input B-rep, render its 7 views
  Plan: PLAN — the strategist turns the instruction into ordered sub-goals
  Execute: EXECUTE — the executor writes my_cad_function for ONE sub-goal
  Lint: LINT — static check for APIs that cannot work in this environment
  Run: RUN — isolated subprocess, 180 s timeout, exports tmp.step
  Gates: GATES — measured diff against the geometry it started from
  Render: RENDER — the 6 orthographic views the benchmark itself compares
  QA: QA — a separate model, before vs after, pictures plus the numbers
  Best: CHECKPOINT — roll back to this sub-goal's best accepted state
  Finalize: FINALIZE — ship tmp.step, tmp.stl, 7 jpgs, settings.json

  [*] --> Prepare
  Prepare --> Plan
  Plan --> Execute
  Execute --> Lint
  Lint --> Execute: banned API — repaired in place, no attempt spent
  Lint --> Run: clean
  Run --> Execute: crash or timeout — barren, traceback + prints fed back
  Run --> Gates: ran ok
  Gates --> Execute: no-op or phantom material — barren, no QA call spent
  Gates --> Execute: wrong direction — a cut that ADDED material, or an add that REMOVED it
  Gates --> Execute: frame drift or undeclared envelope move — proposal spent
  Gates --> Render: material moved, and only where it was allowed to
  Render --> QA
  QA --> QA: scope filter — findings that belong to another sub-goal are dropped
  QA --> Execute: rejected — geometry discarded, next proposal starts fresh
  QA --> Execute: partial — geometry KEPT, refine it in place
  QA --> Best: accepted
  Execute --> Best: attempt budget exhausted
  Best --> Execute: next sub-goal
  Best --> Finalize: no sub-goals left
  Finalize --> [*]
```"""


SEQUENCE_DIAGRAM = """```mermaid
sequenceDiagram
  autonumber
  participant R as Router
  participant X as Executor (LLM)
  participant L as lint
  participant S as runner ▸ subprocess
  participant G as geometry
  participant V as render ▸ subprocess
  participant Q as QA (LLM)

  R->>X: sub-goal, index, recipes, last script, feedback, 3-6 views
  X-->>R: my_cad_function source + approach
  R->>L: check(source)
  L-->>R: banned API? → re-ask X in place, no attempt spent
  R->>S: run(source, source.step)
  S-->>R: ok, tmp.step, faces/edges/volume/bbox, printed log
  R->>G: compare(source.step, tmp.step)
  G-->>R: delta, envelope growth, frame drift, no-op, phantom material, wrong direction
  Note over R: gates reject here without spending a QA call
  R->>V: render 6 orthographic views
  V-->>R: {view: png}
  R->>Q: sub-goal, the rest of the plan, before + after views, the numbers, prior verdicts
  Q-->>R: achieved / partial / confidence / issues / guidance
  Note over R: findings that belong to a later sub-goal are dropped here
  alt accepted or partial
    R->>R: checkpoint, promote accepted_step
  else rejected
    R->>X: qa_feedback — start fresh from the last approved state
  end
```"""


ARTIFACTS = """```
src/results/
└── runs/<user_id>/
    ├── _work/<request>_<ts>/            deleted after a clean run, kept on failure
    │   ├── views_input/input_<view>.png
    │   └── sub<N>_try<M>/
    │       ├── candidate_function.py    what the executor wrote
    │       ├── tmp.step                 what it produced
    │       └── views/out_<view>.png     what QA looked at
    └── outputs/<user_id>_<ts>/brep_end/<ts>/     ← the benchmark ingests this
        ├── settings.json                request id, tokens, cost, plan, provenance
        ├── tmp.step   tmp.stl           the STL is what gets scored
        ├── tmp_<view>.jpg               7 views: iso + 6 orthographic
        ├── session_state.json           the whole run, replayable
        └── steps/
            ├── sub<N>_try<M>_<verdict>.step   every attempt's geometry
            ├── sub<N>_try<M>_<verdict>.py     and the script that made it
            └── sub<N>_try<M>_<view>.jpg
```"""


def render_doc(facts: dict, drift: list, knobs: list, stamp: str) -> str:
    """The whole document, minus the generation timestamp line."""
    P = []
    add = P.append

    add("# Architecture — UCONN CAD PACK\n")
    add("> **Generated file — do not edit by hand.** Regenerate with "
        "`python3 tools/blockdiagram.py` after any change to the code; a hook "
        "does it automatically when Claude edits a file.\n")
    add(f"Source commit: {stamp}\n")
    add("**What this project is.** An agentic harness for the IDETC 2026 Autodesk "
        "*neuralCAD-Edit* benchmark. It is given one CAD part as a STEP file and one "
        "sentence describing an edit, and it must produce the edited part. It is scored "
        "against the edit a human expert made from the same instruction — and the metric "
        "that matters, **diff F1**, compares *your change* to *their change*, so a "
        "no-op and a from-scratch rebuild both score near zero.\n")
    add("**How to read this file.** The diagrams are Mermaid — they render on GitHub "
        "and in most Markdown viewers, and the source below each fence is readable as "
        "plain text if yours does not. Section 6 is the per-module I/O contract; "
        "sections 8 and 9 are generated live from the source, so if they disagree with "
        "the prose, trust them and regenerate.\n")

    add("\n---\n\n## 1 · The schematic — one edit request, end to end\n")
    add("Every block shows its named ports: inputs on the left edge, outputs on "
        "the right, and each wire is labelled with the signal it carries. Amber "
        "wires are feedback — they send the attempt back to be retried. Rows run "
        "left to right, top to bottom.\n\n")
    add(f"![Signal-flow schematic of the CADEDITOR harness]({SVG_REL})\n\n")
    add(f"<sub>Generated by [`tools/blockdiagram.py`](tools/blockdiagram.py) — "
        f"open [{SVG_REL}]({SVG_REL}) on its own for a full-size view.</sub>\n")
    add("\nThe same thing in plain text, for a terminal:\n")
    add("```" + ASCII_STRIP + "```\n")

    add("\n### Every wire in that schematic\n")
    add("\n| from | signal | to |\n|---|---|---|\n")
    names = {b.id: b.name for b in SBLOCKS}
    for s in SIGNALS:
        arrow = "↺ " if s.kind == "back" else ""
        add(f"| `{names[s.src]}` | {arrow}**{s.signal}** | `{names[s.dst]}` |\n")
    add("\n`↺` marks a feedback wire — it sends the attempt back to be retried.\n")

    add("\n---\n\n## 2 · Inside the three agents\n")
    add("The schematic above draws each agent as one block, which hides the "
        "thing that actually decides whether a run scores: **what goes into "
        "each prompt, what is parsed back out of it, and which of those outputs "
        "come round again as the next attempt's input**. Same conventions — "
        "named ports, labelled wires, amber for feedback — with the shared "
        "JSON client drawn once at the bottom.\n\n")
    add(f"![Input, output and loop structure of the strategist, executor and "
        f"QA agents]({SVG_AGENTS_REL})\n\n")
    add(f"<sub>Open [{SVG_AGENTS_REL}]({SVG_AGENTS_REL}) on its own for a "
        f"full-size view.</sub>\n")

    add("\n**The three loops, and what bounds each one.**\n\n"
        "| Loop | Trigger | Bound | What changes on the way round |\n"
        "|---|---|---|---|\n"
        "| **JSON retry** | the reply did not parse | 3 tries, inside one call | "
        "the parse error is appended and the model is asked again — the baseline "
        "degrades to `{}` here and burns a whole iteration |\n"
        "| **lint repair** | the source calls an API that cannot work here | "
        "2 repairs, **no attempt spent** | the executor is re-asked in place with "
        "the exact remedy and its own source; a typo is not a design decision |\n"
        "| **attempt** | a gate or QA rejected the result | 3 judged proposals "
        "per sub-goal, plus barren attempts that produced nothing to judge | "
        "the feedback text — a traceback, a no-op diagnosis, an envelope "
        "violation, or QA's issues — and on the last proposal the executor "
        "switches to *last resort* mode |\n"
        "| **sub-goal** | the sub-goal settled or ran out of attempts | the "
        "plan's length, 1-5 | the router rolls back to the best checkpoint, "
        "re-indexes the geometry from the accepted state, and advances |\n")

    add("\n**Every wire in the agent schematic**\n")
    add("\n| from | signal | to |\n|---|---|---|\n")
    anames = {b.id: b.name for b in ABLOCKS}
    for s in ASIGNALS:
        mark = {"back": "↺ ", "bidi": "⇄ "}.get(s.kind, "")
        add(f"| `{anames[s.src]}` | {mark}**{s.signal}** | `{anames[s.dst]}` |\n")

    add("\n---\n\n## 3 · Context — what goes in, what comes out\n")
    add(CONTEXT_DIAGRAM + "\n")
    add("\n| | |\n|---|---|\n"
        "| **Input** | `request.brep_start` → a `.step` B-rep with no feature history, "
        "plus the instruction text (e.g. *\"Add 0.2 mm chamfer to the hole edges to improve fitting\"*) |\n"
        "| **Output** | `tmp.step`, `tmp.stl`, seven `.jpg` views and `settings.json`, "
        "in the exact folder layout the benchmark ingests |\n"
        "| **Scored on** | the STL, voxelised in **absolute world coordinates with no alignment** — "
        "a correct part in the wrong position scores near zero |\n"
        "| **Never seen** | the ground-truth edit |\n")

    add("\n---\n\n## 4 · Block diagram — every module, its inputs and its outputs\n")
    add("Solid arrows are control flow, dotted arrows are data, thick arrows cross a "
        "process boundary. Each block shows its top input and output ports; the full "
        "contract is in section 6.\n")
    add(block_diagram(BLOCKS) + "\n")

    add("\n---\n\n## 5 · Control flow — the router state machine\n")
    add("This is the part that differs most from a standard \"loop N times\" harness. "
        "Control depends on state: attempts are budgeted per sub-goal, a crash and a QA "
        "rejection produce different feedback, several cheap deterministic gates reject "
        "an attempt *before* spending a QA call, and the geometry that gets promoted is "
        "the last one that **passed QA** — never simply whatever ran last.\n")
    add(STATE_DIAGRAM + "\n")
    add("\n**The two budgets.** A sub-goal gets `MAX_ATTEMPTS_PER_SUBTASK` *design "
        "proposals* — attempts that produced geometry somebody could judge. Attempts "
        "that produced nothing to look at (a traceback, a no-op, a selector that matched "
        "zero entities) are counted separately as *barren*, so a typo cannot retire a "
        "sub-goal that never once put a proposal in front of QA.\n")
    add("\n**The gates, in the order they fire** — each one rejects without an LLM call:\n\n"
        "| Gate | Fires when | Why it exists |\n|---|---|---|\n"
        "| `lint` | the source names an API that cannot work here | repaired in place, no attempt spent |\n"
        "| `crash` | the subprocess raised or timed out | traceback + the script's own prints go back to the executor |\n"
        "| `no-op` | output is geometrically identical to the input | scores exactly zero; costs a QA call to discover otherwise. A no-op whose cause was a nonexistent API name swallowed by the script's own `try/except` is charged as a typo, not as a design signal, so it does not flip the sub-goal into *last resort* mode |\n"
        "| `phantom-material` | summed volume rose but occupied space did not | a duplicate body hidden inside existing material — invisible in every render |\n"
        "| `direction` | a sub-goal tagged `cut-hole-slot` ADDED material, or one tagged `add-body` REMOVED it | the only gate that asks whether the attempt did the right KIND of thing; never applied to fillet/chamfer, whose sign belongs to the edge and not the operation, and never to a sub-goal carrying both tags. After a kept partial the question is re-asked against the state the sub-goal *started* from, so trimming an oversized addition is not vetoed |\n"
        "| `frame-drift` | the part was translated, rescaled or re-centred | views are auto-framed, so QA cannot see it; every metric scores it near zero |\n"
        "| `envelope` | a bbox face moved that the sub-goal never declared | material on the wrong side of its reference face |\n"
        "| `QA` | the model judges it against the sub-goal | three outcomes: accepted / partial (kept and refined) / rejected (discarded) |\n"
        "| `QA scope filter` | a QA finding's distinctive words come from a sub-goal that has not run yet | QA is shown the rest of the plan so it can tell *not done* from *not this step's job*; this is the deterministic backstop, applied before the verdict reaches the executor or is written into the goal. A partial whose every issue was out of scope is promoted to a full acceptance |\n")

    add("\n---\n\n## 6 · One attempt, in order\n")
    add(SEQUENCE_DIAGRAM + "\n")

    add("\n---\n\n## 7 · Block reference — I/O contract per module\n")
    for key, (title, *_r) in LAYERS.items():
        members = [b for b in BLOCKS if b.layer == key]
        if not members:
            continue
        add(f"\n### {title}\n")
        for b in members:
            f = facts.get(b.file) if b.file else None
            head = f"#### `{b.name}`"
            add(f"\n{head}\n")
            if b.file:
                loc = f" · {f.loc} lines · `{f.sha}`" if f and f.exists else " · **MISSING**"
                add(f"[`{b.file}`]({b.file}){loc}\n")
            add(f"\n{b.role}\n")
            add("\n| | |\n|---|---|\n")
            add(f"| **in** | {' · '.join(f'`{i}`' for i in b.inputs)} |\n")
            add(f"| **out** | {' · '.join(f'`{o}`' for o in b.outputs)} |\n")
            if f and f.api:
                add(f"| **public API** | {', '.join(f'`{a}`' for a in f.api)} |\n")
            if b.note:
                add(f"\n> {b.note}\n")

    add("\n---\n\n## 8 · What a run leaves on disk\n")
    add(ARTIFACTS + "\n")

    add("\n---\n\n## 9 · Tunable knobs\n")
    add("Parsed live from [`src/config.py`](src/config.py); "
        "override any of them in `src/.env`.\n\n")
    if knobs:
        add("| Setting | Env var | Default | Notes |\n|---|---|---|---|\n")
        for name, var, default, comment in knobs:
            note = re.sub(r"\s+", " ", comment)[:180]
            add(f"| `{name}` | `{var}` | `{default}` | {note} |\n")
    else:
        add("_(config.py could not be parsed)_\n")

    add("\n---\n\n## 10 · Drift report\n")
    add("Checked on every regeneration: every block's file must exist, every arrow in "
        "section 3 must correspond to a symbol that really appears in the calling file, "
        "and every module in `src/`, `tools/` and `test/` must be claimed "
        "by a block.\n\n")
    if drift:
        add(f"**{len(drift)} item(s) need attention** — the diagram above may be stale:\n\n")
        for d in drift:
            add(f"- {d}\n")
        add("\nFix by updating `BLOCKS` / `EDGES` in "
            "[`tools/blockdiagram.py`](tools/blockdiagram.py), then regenerating.\n")
    else:
        add("✅ No drift. Every block maps to a file that exists, every arrow is backed "
            "by a real call, and every module in the scanned tree is covered.\n")

    add("\n---\n\n## 11 · Running it\n")
    add("""```bash
# the dashboard — watch a run happen, step by step
./run_dashboard.sh                       # http://127.0.0.1:8050

# one request, headless
cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
PYTHONPATH=$(pwd):$(pwd)/../.. uv run python -m src.pipeline \\
    --request <request_id> --score

# score everything produced so far
PYTHONPATH=$(pwd):$(pwd)/../.. uv run python -m src.evaluate \\
    --user-id ours_adk-router

# regenerate this document
python3 tools/blockdiagram.py
```
""")
    add("\nPython **3.12 exactly**, via `uv`. An API key must be in "
        "`src/.env` (copy `.env.example`).\n")
    return "".join(P)


# ---------------------------------------------------------------------------
# Drift checks
# ---------------------------------------------------------------------------

def check_drift(facts: dict) -> list:
    out = []

    for b in BLOCKS:
        if not b.file:
            continue
        f = facts.get(b.file)
        if not f or not f.exists:
            out.append(f"**missing file** — block `{b.name}` points at `{b.file}`, "
                       f"which no longer exists")

    ids = {b.id for b in BLOCKS}
    sources = {}
    for e in EDGES:
        for end in (e.src, e.dst):
            if end not in ids:
                out.append(f"**unknown block** — an arrow refers to `{end}`")
        if not e.evidence:
            continue
        rel, symbol = e.evidence
        p = ROOT / rel
        if rel not in sources:
            sources[rel] = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
        src = sources[rel]
        if src is None:
            out.append(f"**unverifiable arrow** — `{e.src} → {e.dst}` cites `{rel}`, "
                       f"which does not exist")
        elif symbol not in src:
            out.append(f"**stale arrow** — `{e.src} → {e.dst}` (\"{e.label}\") expects "
                       f"`{symbol}` in `{rel}`, and it is not there any more")

    # the schematic is a second view of the same system, and drifts the same way
    for blocks, signals in ((SBLOCKS, SIGNALS), (ABLOCKS, ASIGNALS)):
        sids = {b.id for b in blocks}
        for s in signals:
            for end in (s.src, s.dst):
                if end not in sids:
                    out.append(f"**unknown schematic block** — a signal "
                               f"refers to `{end}`")
    for b in SBLOCKS + ABLOCKS:
        rel = (b.sub or "").split(" ")[0]
        if rel.endswith(".py") and not (ROOT / rel).is_file():
            out.append(f"**missing file** — schematic block `{b.name}` is "
                       f"captioned `{rel}`, which does not exist")

    claimed = {b.file for b in BLOCKS if b.file} | IGNORED_FILES
    for rel in scan_modules():
        if rel in claimed:
            continue
        if (ROOT / rel).stat().st_size == 0:
            continue
        out.append(f"**unmapped module** — `{rel}` exists but no block describes it")
    return out


# ---------------------------------------------------------------------------

STAMP_RE = re.compile(r"^<!-- blockdiagram: body=([0-9a-f]{12}) generated=(.*?) -->$",
                      re.M)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(OUT_DEFAULT), help="output markdown file")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the model has drifted from the source")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--hook", action="store_true",
                    help="hook mode: say nothing unless the document changed or "
                         "the model drifted, and never fail the tool call")
    a = ap.parse_args()
    if a.hook:
        a.quiet = True

    facts = {b.file: read_file(b.file) for b in BLOCKS if b.file}
    drift = check_drift(facts)
    body = render_doc(facts, drift, config_knobs(), git_provenance())

    # The schematic. Written only when it changes, for the same reason the
    # markdown is: a hook that fires on every edit must not churn the tree.
    drawings = [
        (SVG_REL, build_svg(
            SBLOCKS, SIGNALS, "CADEDITOR — one edit request, end to end",
            "port names are the signals on the wires · rows run left to right, "
            "top to bottom · amber wires retry the attempt")),
        (SVG_AGENTS_REL, build_svg(
            ABLOCKS, ASIGNALS, "Inside the three agents",
            "what goes into each prompt, what is parsed back out, and where "
            "the output comes round again as the next attempt's input")),
    ]
    svg_changed = False
    for rel, svg in drawings:
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != svg:
            path.write_text(svg, encoding="utf-8")
            svg_changed = True

    out = Path(a.out)
    digest = hashlib.sha256(body.encode()).hexdigest()[:12]

    # Only the timestamp changes on a no-op run, and a doc that rewrites itself
    # on every hook fire would show up as a permanent diff. Keep the old stamp
    # when the body is identical, and do not touch the file at all.
    previous = out.read_text(encoding="utf-8", errors="replace") if out.is_file() else ""
    prev = STAMP_RE.search(previous)
    unchanged = bool(prev and prev.group(1) == digest) and not svg_changed

    if unchanged:
        if not a.quiet:
            print(f"blockdiagram: {out.name} already up to date "
                  f"({len(BLOCKS)} blocks, {len(EDGES)} arrows, {len(drift)} drift)")
    elif prev and prev.group(1) == digest:
        if not a.quiet:
            print(f"blockdiagram: redrew the schematics "
                  f"({len(SBLOCKS) + len(ABLOCKS)} blocks, "
                  f"{len(SIGNALS) + len(ASIGNALS)} signals); "
                  f"{out.name} unchanged")
    else:
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        out.write_text(f"<!-- blockdiagram: body={digest} generated={when} -->\n{body}",
                       encoding="utf-8")
        if not a.quiet:
            print(f"blockdiagram: wrote {out} — {len(BLOCKS)} blocks, "
                  f"{len(EDGES)} arrows, {len(drift)} drift item(s)")
            for d in drift:
                print("  ! " + re.sub(r"[*`]", "", d))

    if a.hook:
        # Silence is the normal case: an edit that does not change the picture
        # should not add a line to the transcript. Speak up only when the
        # document moved, or when the model no longer matches the code.
        if unchanged and not drift:
            return 0
        msg = []
        if svg_changed:
            msg.append("schematics redrawn")
        if not (prev and prev.group(1) == digest):
            msg.append(f"{out.name} regenerated")
        if drift:
            plain = [re.sub(r"[*`]", "", d) for d in drift]
            msg.append(f"{len(drift)} architecture drift item(s): "
                       + "; ".join(plain[:3])
                       + (" …" if len(plain) > 3 else "")
                       + " — update BLOCKS/EDGES in tools/blockdiagram.py")
        print(json.dumps({"systemMessage": "blockdiagram: " + ". ".join(msg)}))
        return 0        # never fail the tool call that triggered the hook

    return 1 if (a.check and drift) else 0


if __name__ == "__main__":
    sys.exit(main())
