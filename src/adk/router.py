"""StatefulRouter — the state machine that decides who runs next.

              PLAN
                |
                v
    +-----> EXECUTE ----(crash)----+
    |          |                   |
    |          v                   |
    |         RUN                  | attempts left?
    |          |                   |
    |          v                   |
    |    RENDER 6 VIEWS            |
    |          |                   |
    |          v                   |
    +----- QA REVIEW --(reject)----+
               |
            (accept)
               |
               v
     next sub-goal / FINALIZE

Unlike the baseline's flat 10-iteration loop, control here depends on state:
attempts are budgeted per sub-goal, a crash and a QA rejection produce different
feedback, and the geometry that gets promoted is the last one that *passed QA* —
not simply whatever ran last. The baseline has no notion of a good iteration, so
a worse final attempt silently overwrites a better earlier one.
"""

import os
import os.path as osp
import shutil

from .. import config
from ..agents import executor, qa, strategist
from ..tools import geometry as geo
from ..tools import lint
from ..tools import render as rnd
from ..tools import runner
from .llm import Usage

# How many instant re-asks one attempt may spend fixing banned-API calls the
# static lint caught, before the attempt is written off as barren.
MAX_LINT_REPAIRS = 2

# The executor's own way of saying "the thing the sub-goal named is not in
# this part": a selector that matched nothing, a face it looked for and could
# not find. Repeated, it is a verdict on the plan's target.
import re as _re

_TARGET_MISSING_RE = _re.compile(
    r"(could not find|couldn'?t find|not found|no such|unable to (find|locate)"
    r"|matched 0|found 0|0 (faces|edges|candidates) (matched|found)"
    r"|no (matching|candidate|stop|floor) )", _re.IGNORECASE)

# A Python API error the script caught in its own try/except and printed
# instead of raising. The kernel never refused anything and the selection was
# never wrong — the code called a name that does not exist, so the whole
# operation was skipped and the output came back identical to the input.
#
# This has to be told apart from a real no-op. Measured on task 26
# (B7A2N74ZJBF9MZHU_1770171700.697783): attempt 1 misspelled
# `BRepAlgoAPI_Defeaturing.AddFaceToRemove` as `.AddFace`, the try/except
# turned the AttributeError into "geometry unchanged", and that counted as a
# no-op — which flips `last_resort` on for the REST of the sub-goal (see the
# `noops >= 1` condition below). Attempts 2 and 3 were therefore both told to
# stop refining and use a blunt instrument; both broke the outer bounding box
# and were rejected by QA, and the sub-goal retired without ever testing its
# actual approach. A misspelled attribute is not evidence that the approach
# was wrong, and must not be allowed to condemn it.
_API_ERROR_RE = _re.compile(
    r"(has no attribute|is not defined|object is not callable"
    r"|takes no arguments|unexpected keyword argument"
    r"|incompatible function arguments|cannot import name"
    r"|No module named)", _re.IGNORECASE)


# Generic vocabulary that says nothing about WHICH feature an issue is about.
# Only words of 4+ letters (or numbers) are compared at all, so the short
# connectives never reach this set.
_SCOPE_STOP = {
    "this", "that", "these", "those", "with", "from", "into", "onto", "than",
    "then", "there", "their", "which", "while", "when", "where", "what",
    "must", "should", "would", "could", "does", "have", "been", "were",
    "will", "your", "only", "also", "still", "just", "some", "such", "more",
    "most", "over", "under", "each", "both", "same", "other", "another",
    "part", "body", "bodies", "solid", "face", "faces", "edge", "edges",
    "view", "views", "before", "after", "geometry", "geometric", "shape",
    "material", "volume", "surface", "surfaces", "measured", "measurement",
    "change", "changed", "changes", "attempt", "sub", "goal", "customer",
    "instruction", "requested", "request", "missing", "appear", "appears",
    "appeared", "visible", "evidence", "shown", "shows", "show", "new",
    "added", "removed", "make", "made", "create", "created", "still",
}


def _scope_terms(text):
    """The distinctive words and numbers of a piece of text.

    Plural forms are folded onto the singular so "four holes" in a sub-goal
    matches "no hole geometry" in an issue.
    """
    out = set()
    for w in _re.findall(r"[a-zA-Z]{4,}|\d+(?:\.\d+)?", (text or "").lower()):
        if len(w) > 4 and w.isalpha() and w.endswith("s"):
            w = w[:-1]
        if w not in _SCOPE_STOP:
            out.add(w)
    return out


def _belongs_to_another_subgoal(text, sub, pending):
    """True when a QA finding is about work a LATER sub-goal owns.

    QA is told (agents/qa.py) to judge completeness against the current
    sub-goal only, and is shown the rest of the plan so it can tell the
    difference. This is the backstop for when it does it anyway: a finding
    whose distinctive vocabulary comes from a PENDING sub-goal and not from
    this one is not a flaw in this attempt. Measured: "the four D=0.5 mm
    mounting holes are missing" on a flange sub-goal whose sibling sub-goal
    was exactly "cut four cylindrical mounting holes of diameter 0.5 mm" —
    the refinement cut them, and the holes sub-goal then no-opped and was
    rejected.

    Deliberately conservative: `mine` is taken from the strategist's ORIGINAL
    wording, never from the remainder block a previous partial wrote into
    `goal`, so a leaked issue cannot legitimise itself on the next round.
    """
    own = _scope_terms(text)
    if not own:
        return False
    mine = own & _scope_terms(f"{sub.goal_original or sub.goal}")
    foreign = set()
    for p in pending:
        foreign |= own & _scope_terms(getattr(p, "goal_original", "")
                                      or getattr(p, "goal", ""))
    foreign -= _scope_terms(f"{sub.goal_original or sub.goal}")
    if not foreign:
        return False
    # Two distinctive words another sub-goal owns is conclusive; one is enough
    # only when the finding names nothing from this sub-goal at all.
    return len(foreign) >= 2 or not mine


def _refined_goal(sub, verdict):
    """The goal for a refinement attempt: original wording + ONLY what is left.

    The issues folded in here are the post-scope-filter ones (see
    `StatefulRouter._scope_filter`): an out-of-scope complaint reaching this
    point would be written into `sub.goal` verbatim, which is precisely how a
    flange sub-goal's attempt 3 was ordered to cut the next sub-goal's holes.

    After a partial acceptance the kept geometry becomes the input, so a goal
    that restates the whole edit is wrong on both ends of the next attempt:
    the executor is re-asked for work already in the part (adding a second
    copy is the classic partial-refinement failure), and QA judges the
    refinement's delta against a goal whose finished half that delta can no
    longer contain. Measured on the lug bracket: attempt 1 satisfied the
    r=0.4 half and was kept, yet attempt 2 was prompted with the identical
    full goal and the step record showed the same sub-goal line twice.

    Always rebuilt from `goal_original` so a second partial acceptance
    replaces the remainder block instead of nesting inside the first.
    """
    if not sub.goal_original:
        sub.goal_original = sub.goal
    issues = [i.strip() for i in (verdict.get("issues") or []) if i.strip()]
    guidance = (verdict.get("guidance") or "").strip()
    remaining = "\n".join(f"  - {i}" for i in issues) or (
        f"  - {guidance}" if guidance else "")
    if not remaining:
        return sub.goal
    goal = (f"{sub.goal_original}\n\n"
            f"PARTIALLY DONE — an earlier attempt was KEPT and its result is "
            f"the part you are loading. The remainder of this sub-goal is "
            f"ONLY:\n{remaining}\n\n"
            f"QA SAW the remaining flaw in the renders, so it exists. If your "
            f"selection finds ZERO remaining candidates, your filter is too "
            f"strict — the remaining edges are often partial ARCS rather "
            f"than full circles, or open onto curved (non-planar) faces — "
            f"widen the match (drop the planar-mouth requirement, match by "
            f"radius from the curve geometry) instead of returning the input "
            f"unchanged, which scores zero.")
    # The original wording often cites entities by index. Those numbers were
    # enumerated on the part as it stood BEFORE the kept edit, and the edit
    # renumbers everything downstream of the first face it touched — so a
    # refinement that dutifully resolves them lands on the wrong entities,
    # skips them all, and no-ops. The fresh index the router builds for the
    # refinement is authoritative; the goal's numbers are history.
    if any(tag in goal for tag in ("edge_idx", "face_idx", "face #", "edge #")):
        goal += (
            "\n\nCAUTION — STALE ENTITY NUMBERS: the `face #N` / `edge_idx "
            "[...]` numbers above were measured BEFORE the kept edit, and "
            "that edit renumbered the entities. Do NOT resolve those indices "
            "against the part you are loading. Find the remaining features "
            "in the CURRENT geometry index by measured values instead — "
            "radius, position, adjacent surface types — and print what you "
            "matched before modifying it.")
    return goal


class StatefulRouter:
    def __init__(self, state, on_event=None):
        self.state = state
        self.usage = Usage()
        self.on_event = on_event or (lambda *_: None)
        # Geometry fingerprints of every rejected attempt, per sub-goal —
        # kept on the router (not the loop) so they survive strategist
        # replans, which re-enter _advance recursively.
        self._rejected_fps = {}
        # How often a sub-goal's own executor reported it could not find the
        # geometry the goal names. Same lifetime, same reason.
        self._target_missing = {}

    def _record(self, rec):
        """Append a step record, tagging it with the executor exchange that
        produced it — the dashboard folds that open for debugging."""
        io = self.state.__dict__.get("last_exec_io") or {}
        rec.setdefault("prompt_file", self.state.__dict__.get("last_prompt_file"))
        rec.setdefault("prompt_tokens", io.get("sizes", {})
                       .get("TOTAL prompt", 0) // 4)
        # Which playbooks were in front of the model when it wrote this, next
        # to the verdict that attempt earned. Recipe selection has never been
        # measured against outcomes — it is argued for from mechanism — and
        # this is the cheapest way to start: scan the attempt cards and see
        # whether the sections present track the acceptances.
        rec.setdefault("recipes", list(io.get("recipes") or []))
        rec.setdefault("tags", list(io.get("tags") or []))
        self.state.steps.append(rec)

    def _emit(self, msg):
        self.state.log("router", msg)
        self.on_event(self.state, msg)

    # ------------------------------------------------------------------
    def run(self):
        s = self.state
        try:
            self._prepare()
            self._plan()
            while not s.done:
                self._advance()
            # Nothing accepted, or something accepted that is geometrically
            # the input anyway — both ship a part the customer did not ask
            # for. The second case is rare but real: a partial can be kept,
            # every refinement can no-op, and the run then "succeeds" with an
            # untouched part.
            if not s.accepted_step or self._accepted_is_a_no_op():
                self._last_chance_shot()
            s.status = "done" if s.accepted_step else "failed"
        except Exception as e:
            s.status = "failed"
            s.last_error = f"{type(e).__name__}: {e}"
            self._emit(f"run failed: {s.last_error}")
        finally:
            import time
            s.finished = time.time()
        return s

    # ------------------------------------------------------------------
    def _prepare(self):
        s = self.state
        s.status = "preparing"
        self._emit("indexing input geometry")
        insp = geo.inspect(s.input_step)
        if not insp["summary"]["valid"]:
            # Not a warning to act on — the validity gate is relative to this,
            # so the run proceeds — but it explains results that look broken.
            self._emit("  note: the INPUT is already an invalid B-rep; edits "
                       "are judged against that, not against perfection")
        # Runtime-only (not serialised): the raw dict feeds per-sub-goal
        # focused rendering; the full text is what the strategist plans from.
        s.__dict__["inspection"] = insp
        s.__dict__["inspection_for"] = s.input_step
        s.geometry_text = geo.to_prompt(insp)

        before_dir = osp.join(s.work_dir, "views_input")
        s.__dict__["input_views"] = rnd.render_views(
            s.input_step, before_dir, stem="input")
        self._emit(f"rendered {len(s.input_views)} views of the input")

        # A SECOND render of the same input, feature-COLOUR-CODED: one colour
        # per feature family from the index just built, everything ungrouped
        # left gray. The returned plan is the legend, and it goes to the model
        # as a text table beside the images rather than being drawn on them.
        # Only the strategist and the executor see these. QA never gets the
        # feature-family colours — it gets its own colouring instead, keyed to
        # TIME rather than to the index: change_color_plan paints each AFTER
        # face by the step that introduced it (built per attempt in _advance).
        tagged, plan = self._tagged_views(
            s.input_step, osp.join(s.work_dir, "views_input_tagged"), insp,
            stem="input_tag")
        s.__dict__["input_views_tagged"] = tagged
        s.__dict__["input_color_plan"] = plan
        if plan:
            self._emit(f"  colour-coded {len(plan)} feature families for the "
                       f"planning renders")

    def _plan(self):
        s = self.state
        s.status = "planning"
        self._emit("strategist: decomposing the instruction")
        strategist.plan(s, self.usage)
        for t in s.subtasks:
            # The geometric tags ride along on the dashboard line: they say what
            # recipe the sub-goal routed to, next to the entity ids it cites.
            tag = f" [{', '.join(t.tags)}]" if getattr(t, "tags", None) else ""
            self._emit(f"  sub-goal {t.idx}{tag}: {t.goal}")

    # ------------------------------------------------------------------
    def _advance(self):
        """Work the current sub-goal until it passes QA or runs out of attempts."""
        s = self.state
        sub = s.current
        sub.status = "active"
        s.status = "executing"

        # the geometry this sub-goal starts from
        source_step = s.accepted_step or s.input_step
        # Pinned for MBR at the end of the sub-goal: `source_step` moves forward on
        # every kept partial, but the consensus kernel must measure each candidate's
        # edit against the state the SUB-GOAL began at, or the earlier sub-goals'
        # work inflates every candidate's mask identically and washes out the signal.
        s.__dict__["subgoal_start_step"] = source_step
        before_views = (s.input_views if not s.accepted_step
                        else self._views_for(source_step, f"s{sub.idx}_before"))

        # Re-index when a previous sub-goal changed the part: the executor
        # selects by measured radii/positions, and numbers taken from the
        # original input go stale the moment sub-goal 0 is accepted.
        if s.__dict__.get("inspection_for") != source_step:
            try:
                s.__dict__["inspection"] = geo.inspect(source_step)
                s.__dict__["inspection_for"] = source_step
                self._emit("  re-indexed geometry from the accepted state")
            except Exception as e:
                self._emit(f"  re-index failed, keeping previous index: {e}")

        # The feature-coloured twin of `before_views`, for the EXECUTOR only —
        # QA below still gets the natural pair. It is built here, after the
        # re-index, because the colouring is keyed on the index of the exact
        # state being rendered: colouring `source_step` with the previous
        # sub-goal's index would point the legend's face_idx tags at faces that
        # have since moved. From the original input the pair already exists.
        if not s.accepted_step:
            before_views_tagged = s.__dict__.get("input_views_tagged") or {}
            color_plan = s.__dict__.get("input_color_plan") or []
        else:
            before_views_tagged, color_plan = self._tagged_views(
                source_step,
                osp.join(s.work_dir, f"views_s{sub.idx}_before_tagged"),
                s.__dict__.get("inspection"),
                stem=f"s{sub.idx}_before_tag")

        # Two budgets, not one. `design` counts attempts that produced geometry
        # somebody could judge; `barren` counts the ones that produced nothing
        # to look at — a traceback, a no-op, a selector that matched zero
        # entities. Spending a design attempt on a typo is why sub-goals used
        # to run out of road having made only one real proposal.
        # A replanned round opens with the strategist's failure digest as its
        # feedback (set by _replan_subgoal); a first round starts clean.
        feedback = s.__dict__.pop("replan_feedback", None)
        # Attempt numbering CONTINUES across strategist replans: a replanned
        # sub-goal re-enters this method with attempts already spent, and
        # restarting at 1 would overwrite the earlier round's step records and
        # executor io dumps.
        attempt = sub.attempts or 0
        design = barren = noops = 0
        ceiling = attempt + (config.MAX_ATTEMPTS_PER_SUBTASK
                             + config.MAX_BARREN_RETRIES)
        settled = False            # QA accepted (fully or partially)

        # The design budget is the only thing that ends a sub-goal on merit;
        # the ceiling is just a stop so a sub-goal that crashes every time
        # cannot loop forever. Bounding on `barren` directly was wrong — three
        # crashes in a row would retire a sub-goal that had never once put a
        # proposal in front of QA.
        while design < config.MAX_ATTEMPTS_PER_SUBTASK and attempt < ceiling:
            attempt += 1
            sub.attempts = attempt
            self._emit(f"sub-goal {sub.idx}, attempt {attempt}/{ceiling} "
                       f"(proposal {design + 1} of "
                       f"{config.MAX_ATTEMPTS_PER_SUBTASK})")

            # Switch the executor to blunt-instrument mode when refining is no
            # longer the right move: either this is the sub-goal's last chance,
            # or an attempt has already come back a no-op.
            #
            # The no-op threshold was 2, which never fired inside a 3-attempt
            # budget: MAX_BARREN_RETRIES is 0, so one no-op has already spent a
            # third of the sub-goal. Measured on the spanner cutout — attempt 1
            # was kept as a partial, attempts 2 and 3 both returned the input
            # from a `return base` guard, and LAST_RESORT (whose whole job is to
            # forbid exactly that) was never sent, because `design` was 1 and
            # `noops` only reached 1. One wasted attempt is enough evidence that
            # refining the same approach is not working.
            last_resort = (design + 1 >= config.MAX_ATTEMPTS_PER_SUBTASK
                           or noops >= 1)

            try:
                script = executor.build(s, feedback, self.usage,
                                        current_views=before_views,
                                        tagged_views=before_views_tagged,
                                        color_plan=color_plan,
                                        last_resort=last_resort)
            except Exception as e:
                barren += 1
                feedback = f"Your reply was unusable ({e}). Return valid JSON with " \
                           f"a complete my_cad_function."
                self._emit(f"  executor error: {e}")
                continue

            # Static lint: known certain-fail API patterns are cheaper to catch
            # by regex than by spending a subprocess run discovering the
            # AttributeError at runtime (measured: `.hull()` cost a full
            # attempt before this check existed).
            #
            # A hit is now REPAIRED IN PLACE instead of ending the attempt. A
            # banned-API typo is not a design decision — it says nothing about
            # whether this was the right edit to propose — so it has no
            # business consuming one of the sub-goal's three proposals.
            # Measured on a live run: `.hull()` ate sub-goal 0's attempt 3, so
            # the sub-goal retired having never once executed that proposal.
            # The executor is re-asked here, inside the same attempt, with the
            # exact fix text and its own source; only a model that repeats the
            # banned call after MAX_LINT_REPAIRS corrections loses the attempt.
            self._dump_exec_io(sub, attempt)

            problems = lint.check(script)
            repairs = 0
            repair_error = None
            while problems and repairs < MAX_LINT_REPAIRS:
                repairs += 1
                names = ", ".join(n for n, _ in problems)
                self._emit(f"  lint caught {names} — asking for a fix in place "
                           f"(repair {repairs} of {MAX_LINT_REPAIRS}, no "
                           f"attempt spent)")
                # Deliberately a LOCAL. `feedback` is the loop-carried variable
                # the NEXT attempt builds from; overwriting it here would hand
                # that attempt a complaint about a script which has already been
                # fixed, in place of the QA or crash feedback that actually says
                # what to do differently. The lint text is for this re-ask only.
                repair_feedback = lint.feedback(problems, script)
                try:
                    script = executor.build(s, repair_feedback, self.usage,
                                            current_views=before_views,
                                            tagged_views=before_views_tagged,
                                            color_plan=color_plan,
                                            last_resort=last_resort)
                except Exception as e:
                    # Handled by the rejection branch below rather than here, so
                    # one attempt cannot be charged to `barren` twice.
                    repair_error = e
                    break
                self._dump_exec_io(sub, attempt, repair=repairs)
                s.log("lint", f"repaired {names}", subtask=sub.idx,
                      attempt=attempt, rules=[n for n, _ in problems])
                problems = lint.check(script)

            problems = lint.check(script)
            if problems or repair_error is not None:
                barren += 1
                names = ", ".join(n for n, _ in problems)
                if repair_error is not None:
                    self._emit(f"  executor error during lint repair: "
                               f"{repair_error}")
                self._emit(f"  lint rejected the script before running "
                           f"({names}) after {repairs} repair attempt(s)")
                self._record({
                    "sub": sub.idx, "goal": sub.goal, "attempt": attempt,
                    "ok": False, "views": {}, "verdict": "rejected",
                    "issues": [f"static lint: {n}" for n, _ in problems],
                    "gate": "lint", "script": script, "step": None,
                })
                feedback = (
                    f"Your reply was unusable ({repair_error}). Return valid "
                    f"JSON with a complete my_cad_function."
                    if repair_error is not None
                    else lint.feedback(problems, script))
                continue

            attempt_dir = osp.join(s.work_dir, f"sub{sub.idx}_try{attempt}")
            ok, info, log = runner.run_script(script, source_step, attempt_dir)

            if not ok:
                barren += 1
                self._emit(f"  script failed: {info.get('error','?')}")
                s.last_error = info.get("error", "")
                self._record({
                    "sub": sub.idx, "goal": sub.goal, "attempt": attempt,
                    "ok": False, "views": {}, "verdict": "crashed",
                    "error": info.get("error", ""), "issues": [],
                    "script": script, "step": None,
                })
                feedback = executor.failure_feedback(info, log, script)
                continue

            s.last_step = info["step"]
            self._emit(f"  ran ok — {info['faces']} faces, vol {info['volume']}")
            if info.get("validity_note"):
                self._emit(f"  note: {info['validity_note']}")

            diff = geo.compare(source_step, s.last_step)

            # A no-op passes every validity gate — valid solid, non-zero
            # volume, renders fine — so without this it costs a full QA call
            # to discover that nothing happened. Diff F1 scores a no-op zero.
            if diff.get("identical"):
                barren += 1
                # "identical to the input" is the symptom; the cause is in
                # what the script printed — a refused kernel op caught in a
                # try/except, a selection that matched nothing. Put it on the
                # step record so the dashboard (and anyone reading the run)
                # sees what actually has to change next attempt.
                why = executor.noop_diagnosis(log)
                # A no-op the script caused by calling a name that does not
                # exist is a typo, not a design signal: it says nothing about
                # whether this was the right edit to propose, so it must not
                # switch the sub-goal into blunt-instrument mode. It still
                # costs the attempt — the code did run — but the approach
                # survives to be tried properly. See _API_ERROR_RE.
                typo = bool(_API_ERROR_RE.search(why or ""))
                if not typo:
                    noops += 1
                self._emit("  no-op: geometry is unchanged — rejected "
                           "without spending a QA call"
                           + ("; caused by a bad API name, not by the "
                              "approach — keeping refinement mode on"
                              if typo else "")
                           + (f"; script said: {why[:200]}" if why else ""))
                issues = ["no-op: output is geometrically identical to "
                          "the input"]
                if typo:
                    issues.append(
                        "this no-op was a nonexistent API name swallowed by "
                        "the script's own try/except — the approach was never "
                        "actually executed")
                if why:
                    issues.append("cause, from the script's own prints: "
                                  + why)
                # A no-op whose own diagnosis says the TARGET was not there is
                # evidence about the plan, not about the code: a second script
                # cannot select a face the part does not have. The first one
                # may still be a selector bug, so it is retried; a repeat ends
                # the round and hands the strategist the executor's own words,
                # instead of spending the rest of the budget hunting geometry
                # that was never in the model.
                missing = _TARGET_MISSING_RE.search(why or "")
                if missing:
                    n = self._target_missing.get(sub.idx, 0) + 1
                    self._target_missing[sub.idx] = n
                    if n >= 2:
                        issues.insert(0, "TARGET NOT FOUND TWICE: the sub-goal "
                                      "names geometry the executor cannot "
                                      "locate in the part — the target itself "
                                      "is wrong, not the code")
                self._record({
                    "sub": sub.idx, "goal": sub.goal, "attempt": attempt,
                    "ok": True, "views": {}, "verdict": "rejected",
                    "faces": info.get("faces"), "volume": info.get("volume"),
                    "issues": issues,
                    "gate": "no-op",
                    "script": script, "step": info.get("step"),
                })
                if missing and self._target_missing.get(sub.idx, 0) >= 2:
                    self._emit("  the named target could not be located twice "
                               "— escalating to a replan rather than "
                               "re-selecting it")
                    break
                # The script's own prints — "matched 0 faces", a volume that did
                # not move — are the diagnosis, and dropping them is why two
                # consecutive no-op attempts got identical generic advice.
                feedback = executor.noop_feedback(diff, log, script)
                continue

            # A duplicate body dropped on top of the body it was copied from.
            # The summed volume of the compound rises by a whole feature's
            # worth, so the no-op gate above lets it through, but the space the
            # part occupies is unchanged: every render is pixel-identical, QA
            # partial-accepted one at 0.62 confidence, and the voxel metrics
            # scored it exactly 0.0. Caught here, on the numbers, because there
            # is nothing in the pictures for QA to catch it with.
            if diff.get("phantom_material"):
                barren += 1
                noops += 1
                self._emit(f"  phantom material: {diff['phantom_material']} — "
                           "rejected without spending a QA call")
                self._record({
                    "sub": sub.idx, "goal": sub.goal, "attempt": attempt,
                    "ok": True, "views": {}, "verdict": "rejected",
                    "faces": info.get("faces"), "volume": info.get("volume"),
                    "issues": [f"phantom material: {diff['phantom_material']}"],
                    "gate": "phantom-material",
                    "script": script, "step": info.get("step"),
                })
                feedback = (
                    "YOUR NEW BODY IS A COINCIDENT DUPLICATE: "
                    + diff["phantom_material"]
                    + " — a body hidden inside existing material occupies no "
                    "new space and scores exactly zero. Rotate or translate the "
                    "copy into the EMPTY region (for a radial pattern: about the "
                    "measured axis into the angular gap between existing "
                    "instances), and print its bounding box to prove it differs "
                    "from the source body's.\n\n"
                    + executor.noop_feedback(diff, log, script))
                continue

            # A result materially identical to one already rejected for this
            # sub-goal. Same solids/faces/edges counts and the same volume to
            # the printed precision is, in practice, the same shape — and a
            # rejected shape re-derived under a fresh prompt (or a fresh
            # strategy) proves the current premises produce it
            # deterministically. Measured: a through-slot sub-goal's replanned
            # round rebuilt its attempt-3 geometry at attempt 6 to within
            # 0.9 mm^3 and the run spent the whole second budget re-earning a
            # rejection it already had. Break straight to the replan path —
            # more attempts under the same goal can only reproduce it again.
            fp = (str(diff.get("solids")), str(diff.get("faces")),
                  str(diff.get("edges")), str(diff.get("volume_mm3")))
            seen_rejected = self._rejected_fps.setdefault(sub.idx, set())
            if fp in seen_rejected:
                design += 1
                self._emit("  this attempt reproduced already-rejected "
                           "geometry (identical solids/faces/edges/volume) — "
                           "the current goal deterministically yields a "
                           "rejected part; escalating instead of re-judging")
                self._record({
                    "sub": sub.idx, "goal": sub.goal, "attempt": attempt,
                    "ok": True, "views": {}, "verdict": "rejected",
                    "faces": info.get("faces"), "volume": info.get("volume"),
                    "issues": ["reproduced geometry that was already rejected "
                               "in an earlier attempt — the goal's premises "
                               "(target, direction, constraints) force this "
                               "shape and must change"],
                    "gate": "repeat",
                    "script": script, "step": info.get("step"),
                })
                break

            # Faces moved, material did not. This used to be an auto-reject
            # ("the cut missed"), and that was wrong: for a boolean-union of
            # coincident halves, a seam fuse, or a sew/merge sub-goal, a
            # topology-only change with zero volume delta is exactly the
            # correct result. Measured on the mirror-and-merge task, the one
            # attempt that completed the union (24 -> 18 faces, volume
            # unchanged) was thrown out by this gate while four no-ops burned
            # the rest of the budget. QA sees the same numbers plus the goal
            # and the pictures — let it judge.
            if diff.get("no_material_change"):
                self._emit("  topology changed with 0 volume delta — "
                           "sending to QA (correct for merge/fuse goals)")

            # DID IT DO THE KIND OF THING IT WAS ASKED TO DO? A cut that added
            # material, or an add that removed it, is wrong in a way no other
            # gate sees: the magnitude gates ask only whether anything moved,
            # the envelope gate only whether the outside moved, and in the
            # renders a rounded rim and a chamfered one look alike. Cheap,
            # unambiguous for these two tags, and never applied to
            # fillet/chamfer, whose sign depends on the edge rather than the
            # operation.
            conflict = geo.volume_direction_conflict(diff, sub.tags)
            # NOTE: an earlier version of this gate re-checked a would-be
            # rejection against the state the SUB-GOAL started from, so that a
            # refinement trimming a kept partial was not vetoed for having a
            # negative incremental delta on an `add-body` goal. The reasoning
            # was sound but the change was measured HARMFUL and is withdrawn:
            # on task 3YH2WFSRM22W7DKT_1769177116 it waved through attempt 3,
            # which QA then partial-accepted, overwriting attempt 2. Ground
            # truth: attempt 2 scored diff_f1 0.2740 (the best result anyone
            # recorded on that task) and attempt 3 scored 0.0000. The old,
            # blunter gate would have rejected attempt 3 unseen and shipped
            # attempt 2.
            #
            # The real defect is that a refinement replaces its predecessor
            # unconditionally (see the accept path below) — the gate was only
            # ever an accidental brake on it. Restore this escape when that
            # keep policy can tell an improvement from a regression; until
            # then the blunt gate is worth more than the correct one.
            if conflict:
                design += 1
                seen_rejected.add(fp)
                self._emit(f"  wrong direction: {conflict} — rejected without "
                           f"spending a QA call")
                self._record({
                    "sub": sub.idx, "goal": sub.goal, "attempt": attempt,
                    "ok": True, "views": {}, "verdict": "rejected",
                    "faces": info.get("faces"), "volume": info.get("volume"),
                    "issues": [f"wrong direction: {conflict}"],
                    "gate": "direction",
                    "script": script, "step": info.get("step"),
                })
                feedback = (
                    f"YOUR ATTEMPT MOVED MATERIAL THE WRONG WAY: {conflict}.\n\n"
                    f"You selected a real target and operated on it, so the "
                    f"selection is probably fine — what is inverted is the "
                    f"operation or the side you applied it to. Re-read the "
                    f"sub-goal, decide whether it removes or adds, and print "
                    f"the signed volume delta before returning:\n"
                    f"    print('DELTA', out.Volume() - base.Volume())\n"
                    f"If the sign disagrees with the sub-goal, fix it in the "
                    f"SAME attempt rather than returning it.")
                continue

            # A part that was translated or rescaled instead of edited renders
            # identically from every camera — the views are auto-framed — so QA
            # cannot see it, while every metric scores it near zero because
            # none of them aligns the prediction first.
            if diff.get("frame_drift"):
                # A whole-part translation or rescale is normally fatal — but
                # only when it was not asked for. A sub-goal tagged
                # scale-transform (or whose goal text commands scaling)
                # commands exactly this signature: the "drift" IS the edit.
                # Measured: "Scale the part 10x" had every correct attempt
                # auto-rejected here, four in a row, and the strategist
                # replanned around imaginary unit-interpretation bugs until
                # the user killed the run. Such attempts go to QA, which can
                # check the commanded factor and origin against the numbers.
                import re as _re2
                wants_transform = (
                    "scale-transform" in (sub.tags or [])
                    or _re2.search(r"\bscal(e|ed|ing)\b", sub.goal or "",
                                   _re2.IGNORECASE))
                if wants_transform:
                    self._emit(f"  frame changed ({diff['frame_drift']}) — "
                               "the sub-goal commands a whole-part transform, "
                               "so this is the requested edit; sending to QA")
                else:
                    design += 1  # it built something; it put it in the wrong place
                    seen_rejected.add(fp)
                    self._emit(f"  frame drift: {diff['frame_drift']} — rejected "
                               "without spending a QA call")
                    self._record({
                        "sub": sub.idx, "goal": sub.goal, "attempt": attempt,
                        "ok": True, "views": {}, "verdict": "rejected",
                        "faces": info.get("faces"), "volume": info.get("volume"),
                        "issues": [f"frame drift: {diff['frame_drift']}"],
                        "gate": "frame-drift",
                        "script": script, "step": info.get("step"),
                    })
                    feedback = executor.frame_feedback(diff)
                    continue

            # Material on the wrong side of its reference face: the feature is
            # right, the half-space is not. QA has been shown to accept this —
            # it judges against the sub-goal, and the sub-goal is what was
            # wrong — so it is checked here, against the envelope the
            # strategist committed to. No declaration, no check.
            # The declaration is the strategist's plan, and it can simply be
            # wrong: on this rotor it declared "nothing may move" for a sub-goal
            # whose correct answer (the human's own edit) grows +-X by 5.45 mm.
            # So the gate never gets the last word — on the final attempt the
            # geometry goes to QA, which can see the pictures, rather than being
            # thrown away and leaving the sub-goal with nothing.
            undeclared = geo.undeclared_envelope_moves(diff, sub.envelope)
            if undeclared and design + 1 >= config.MAX_ATTEMPTS_PER_SUBTASK:
                self._emit(f"  envelope moved undeclared on the last proposal — "
                           f"letting QA judge it rather than discarding it")
                undeclared = {}
            if undeclared:
                design += 1
                seen_rejected.add(fp)
                moves = ", ".join(f"{f} {mm:+g}mm" for f, mm in sorted(undeclared.items()))
                self._emit(f"  envelope moved undeclared ({moves}) — rejected "
                           "without spending a QA call")
                self._record({
                    "sub": sub.idx, "goal": sub.goal, "attempt": attempt,
                    "ok": True, "views": {}, "verdict": "rejected",
                    "faces": info.get("faces"), "volume": info.get("volume"),
                    "issues": [f"moved bbox faces the sub-goal did not declare: "
                               f"{moves} (declared: {sub.envelope or 'none'})"],
                    "gate": "envelope",
                    "script": script, "step": info.get("step"),
                })
                feedback = executor.envelope_feedback(diff, sub.envelope, undeclared)
                continue

            after_views = rnd.render_views(
                s.last_step, osp.join(attempt_dir, "views"), stem="out")
            if not after_views:
                barren += 1     # nothing to look at, so nothing was decided
                feedback = "The result could not be rendered — the solid is " \
                           "probably degenerate. Produce a clean solid."
                self._emit("  render produced nothing")
                continue

            # A second AFTER render for QA, colour-coded by CHANGE ATTRIBUTION:
            # faces new in this attempt red, faces each earlier accepted step
            # introduced in that step's own colour, everything inherited from
            # the original input gray. The chain is every state QA ever
            # accepted, oldest first; qa.py prints the matching legend. Not the
            # feature-family colouring — that keys colours to the index, this
            # keys them to time. Degrades to the natural views on any failure.
            chain = s.__dict__.setdefault("qa_chain", [s.input_step])
            change_plan = geo.change_color_plan(chain, s.last_step)
            after_views_colored = rnd.render_views_colored(
                s.last_step, osp.join(attempt_dir, "views_changed"),
                change_plan, stem="chg") if change_plan else {}
            if after_views_colored:
                self._emit("  change-coloured the after views for QA")
            else:
                change_plan = []    # no pictures -> no legend

            step_rec = {
                "sub": sub.idx, "goal": sub.goal, "attempt": attempt,
                "ok": True, "views": dict(after_views),
                "views_changed": dict(after_views_colored),
                "faces": info.get("faces"), "volume": info.get("volume"),
                "volume_change_pct": diff.get("volume_change_pct"),
                "new_surface_types": diff.get("new_surface_types"),
                "verdict": "pending", "issues": [],
                "script": script, "step": info.get("step"),
            }
            self._record(step_rec)
            design += 1

            s.status = "qa"
            # Total change since the ORIGINAL part, not just since the last
            # accepted state: a refinement that duplicates a feature looks
            # reasonable step-by-step and only shows up in the running total.
            cumulative = (geo.compare(s.input_step, s.last_step)
                          if source_step != s.input_step else None)
            verdict = qa.review(s, before_views, after_views, diff, self.usage,
                                cumulative=cumulative,
                                colored_after=after_views_colored,
                                change_plan=change_plan,
                                # The rest of the plan, so QA can tell "not
                                # done" from "not this step's job".
                                plan_ctx=qa.plan_context(s))
            # Backstop for the same confusion, applied BEFORE the verdict is
            # recorded, fed back to the executor, or written into the goal.
            verdict = self._scope_filter(sub, verdict)
            step_rec["verdict"] = ("accepted" if verdict.get("achieved")
                                   and not verdict.get("partial")
                                   else "partial" if verdict.get("achieved")
                                   else "rejected")
            step_rec["issues"] = verdict.get("issues", [])[:3]
            step_rec["observation"] = verdict.get("observation", "")[:300]

            if verdict.get("achieved"):
                # Both full and partial acceptance keep the geometry — a
                # directionally-correct edit scores far better than reverting.
                s.script = script
                s.accepted_step = s.last_step
                # This state joins the chain the change-colouring diffs
                # against: from now on its faces are "an earlier step's".
                chain.append(s.last_step)
                self._checkpoint(sub, attempt, step_rec, verdict, script)
                if verdict.get("partial"):
                    s.partial_accepts += 1
                    sub.status = "partial"
                    settled = True
                    self._emit(f"  QA partial — keeping it, refining "
                               f"sub-goal {sub.idx}")
                    if design < config.MAX_ATTEMPTS_PER_SUBTASK:
                        feedback = executor.qa_feedback(verdict, diff, kept=True,
                                                        script=script, log=log)
                        # QA's remaining-work findings become the goal itself,
                        # not just feedback: the goal is what the executor
                        # prompt leads with, what recipe retrieval keys on,
                        # what QA judges the next delta against, and what the
                        # step record (and dashboard) show for the attempt.
                        sub.goal = _refined_goal(sub, verdict)
                        # refine from the accepted state, not the original —
                        # a partial is KEPT, so its renders are the current
                        # state the executor is shown
                        source_step = s.accepted_step
                        before_views = after_views
                        # RE-INDEX THE KEPT STATE BEFORE REFINING. The kept
                        # edit renumbers the entities (+8 faces on the lug
                        # bracket shifted every edge index), so the geometry
                        # index in the prompt — recomputed only at the top of
                        # _advance, on a change of sub-goal — now describes a
                        # part that no longer exists. Measured: the refinement
                        # resolved the goal's edge_idx tags against the kept
                        # part, found 0.1 mm ellipses and r=0.02 circles where
                        # r=1.4 hole rims were promised, skipped all 16
                        # candidates and returned the input — two no-ops
                        # burned the rest of the budget. A fresh index also
                        # lets the colour-coded views follow along instead of
                        # being dropped for pointing at renumbered faces.
                        try:
                            s.__dict__["inspection"] = geo.inspect(source_step)
                            s.__dict__["inspection_for"] = source_step
                            self._emit("  re-indexed the kept geometry for "
                                       "the refinement")
                            before_views_tagged, color_plan = self._tagged_views(
                                source_step,
                                osp.join(s.work_dir,
                                         f"views_s{sub.idx}_t{attempt}_kept_tagged"),
                                s.__dict__.get("inspection"),
                                stem=f"s{sub.idx}_t{attempt}_kept_tag")
                        except Exception as e:
                            self._emit(f"  re-index of the kept state failed "
                                       f"({e}) — refining on the stale index")
                            before_views_tagged, color_plan = None, []
                        continue
                    self._emit("  out of attempts — keeping the partial result")
                    break
                # A refinement completed the sub-goal: the remainder wrapper
                # described work that is now done, so the summary and any
                # later reader get the strategist's original wording back.
                if sub.goal_original:
                    sub.goal = sub.goal_original
                sub.status = "done"
                settled = True
                self._emit(f"  QA accepted sub-goal {sub.idx}")
                break

            self._emit("  QA rejected: " + "; ".join(verdict.get("issues", [])[:2]))
            seen_rejected.add(fp)
            # QA can name a flaw in the SUB-GOAL's own premise (wrong anchor,
            # direction contradicting a measured axis, an invented
            # constraint). Executing a wrong premise again can only reproduce
            # the same rejected part, so the remaining attempts go to the
            # strategist instead of the executor: record the flaw where the
            # failure digest will surface it, and leave the round early.
            flaw = (verdict.get("plan_flaw") or "").strip()
            if flaw:
                step_rec["issues"] = (["PLAN PREMISE WRONG: " + flaw]
                                      + step_rec["issues"])[:3]
                self._emit("  QA says the sub-goal's premise is wrong: "
                           + flaw[:200] + " — escalating to a replan")
                break
            feedback = executor.qa_feedback(verdict, diff, script=script, log=log)
            s.status = "executing"

        if not settled:
            # Only mark failed if nothing was ever kept for this sub-goal; a
            # partial acceptance from an earlier attempt still counts.
            if sub.status != "partial":
                sub.status = "failed"
            spent = (f"{design} proposal(s), {barren} barren attempt(s)")
            self._emit(f"sub-goal {sub.idx} exhausted its attempts "
                       f"({sub.status}; {spent})")
            # EVERY attempt rejected and nothing kept: hand the attempts' own
            # feedback to the strategist for a NEW strategy and re-run this
            # sub-goal from the same state (config.MAX_REPLANS rounds at most).
            # Measured need: the blade-radius task burned 3 attempts on
            # `fillet()` calls the kernel refuses on that edge at every radius,
            # then shipped the unedited input — a replan naming the boolean
            # construction is the difference between 0.0 and a scoreable edit.
            if (sub.status == "failed" and sub.replans < config.MAX_REPLANS
                    and self._replan_subgoal(sub)):
                return self._advance()
            # Rejected geometry is never shipped. Exhausting the attempts
            # reverts to the last approved state — which is already what
            # `s.accepted_step` and `s.script` hold, so the revert is simply
            # leaving them alone. Promoting the last rejected attempt as a
            # fallback shipped a rotor blade QA had rejected three times for
            # sitting on top of an existing blade (diff F1 0.002 —
            # statistically zero). With nothing ever approved, finalize ships
            # the unedited input.
            if s.accepted_step:
                self._emit("  reverting to the last approved state — "
                           "rejected geometry is never shipped")
            else:
                self._emit("  nothing approved for this run yet — the unedited "
                           "input will be shipped (finalize handles it)")

        self._revert_to_best(sub)
        s.cursor += 1

    # ------------------------------------------------------------------
    def _scope_filter(self, sub, verdict):
        """Strip QA findings that belong to a sub-goal that has not run yet.

        A sub-goal is only ever answerable for its own work, but QA sees the
        customer instruction (it needs it to catch invented features and wrong
        premises) and has been observed reading it as a completeness checklist:
        a correctly built flange was marked PARTIAL for "the four D=0.5 mm
        mounting holes are missing" when those holes were the NEXT sub-goal's
        entire content. Everything downstream then compounds it — the issues
        become executor feedback, `_refined_goal` writes them into `sub.goal`,
        and the attempt that dutifully cuts the holes leaves the next sub-goal
        with nothing to do but no-op and be rejected. Both budgets, one
        misread sentence.

        Only PENDING sub-goals are considered: work an earlier step already
        delivered may legitimately be re-examined here, and the prompt handles
        that case.
        """
        s = self.state
        pending = [t for t in s.subtasks
                   if t is not sub and getattr(t, "idx", -1) > sub.idx
                   and t.status in ("pending", "active")]
        if not pending:
            return verdict

        issues = list(verdict.get("issues") or [])
        kept = [i for i in issues
                if not _belongs_to_another_subgoal(i, sub, pending)]
        dropped = [i for i in issues if i not in kept]
        guidance = (verdict.get("guidance") or "").strip()
        flaw = (verdict.get("plan_flaw") or "").strip()
        drop_guidance = bool(guidance) and _belongs_to_another_subgoal(
            guidance, sub, pending)
        drop_flaw = bool(flaw) and _belongs_to_another_subgoal(
            flaw, sub, pending)
        if not (dropped or drop_guidance or drop_flaw):
            return verdict

        out = dict(verdict)
        out["issues"] = kept
        if drop_guidance:
            out["guidance"] = ""
        if drop_flaw:
            out["plan_flaw"] = ""
        for text in dropped + ([guidance] if drop_guidance else []) \
                + ([flaw] if drop_flaw else []):
            self._emit("  QA finding ignored — it belongs to a later sub-goal, "
                       "not this one: " + text[:160])
        # The payload has to list the SAME things the count counts. It used to
        # omit the guidance, so a verdict whose only dropped item was its
        # guidance logged `dropped 1 out-of-scope QA finding(s)` next to
        # `dropped: []` — a record that says something was discarded and not
        # what. Measured on task 19 (SUJ2G2UMJQR7PMBX_1759203739): the dropped
        # text was QA's one actionable instruction ("Redo this step by creating
        # ONLY one vertical annular cylinder (OD 38.1, ID 25.4) centered at
        # [-88.9, 100.0] on the z=266.7 plane"), which is plainly sub-goal 0's
        # own work; 5 of that run's 12 attempts then reproduced byte-identical
        # already-rejected geometry. Whether the scope call was right is a
        # separate question — but it cannot even be reviewed if the discarded
        # text is not in the record.
        s.log("qa-scope", f"sub-goal {sub.idx}: dropped "
                          f"{len(dropped) + drop_guidance + drop_flaw} "
                          f"out-of-scope QA finding(s)",
              subtask=sub.idx, dropped=(dropped[:3]
                                        + ([guidance] if drop_guidance else [])
                                        + ([flaw] if drop_flaw else [])))

        # A partial whose every confirmed finding was another sub-goal's work
        # is a full acceptance: there is nothing left in THIS sub-goal to
        # refine, and refining it can only produce the next sub-goal's edit
        # early — the exact sequence that wasted two budgets.
        if (out.get("achieved") and out.get("partial") and issues
                and not kept and not (out.get("guidance") or "").strip()):
            out["partial"] = False
            self._emit(f"  every QA issue was out of scope — sub-goal "
                       f"{sub.idx} counts as fully achieved")

        # Keep the run's memory consistent with the filtered verdict: qa.review
        # already appended the raw findings to `qa_notes`, and _history() feeds
        # those back into the NEXT QA call, which would re-raise the same
        # out-of-scope complaint it was just told to ignore.
        s.last_qa = out
        if sub.qa_notes and sub.qa_notes[-1].get("attempt") == sub.attempts:
            note = sub.qa_notes[-1]
            note["issues"] = kept[:3]
            note["verdict"] = ("ACCEPTED" if out.get("achieved")
                               and not out.get("partial")
                               else "PARTIAL" if out.get("achieved")
                               else "REJECTED")
        return out

    # ------------------------------------------------------------------
    # Replanning — a fully-rejected sub-goal goes back to the strategist
    # ------------------------------------------------------------------
    def _accepted_is_a_no_op(self):
        """True when the state we are about to ship equals the input.

        Strictly identical only: a genuinely small edit (a 0.2 mm chamfer) must
        not be mistaken for nothing, so this asks the same question the no-op
        gate asks of every attempt, not a magnitude question.
        """
        s = self.state
        if not s.accepted_step or s.accepted_step == s.input_step:
            return bool(s.accepted_step)
        try:
            return bool(geo.compare(s.input_step, s.accepted_step).get("identical"))
        except Exception:
            return False

    def _last_chance_shot(self):
        """One unplanned attempt at the whole instruction, before giving up.

        Runs only when no sub-goal produced anything QA kept — a state that
        otherwise ships the unedited input and scores zero on diff F1. One
        executor call, no planning, no QA: the result is shipped only if the
        objective gates agree it is a real, valid edit, so this can add a
        scoreable result but cannot replace an approved one.
        """
        s = self.state
        digest = self._run_digest()
        self._emit("nothing was accepted — one last unplanned attempt at the "
                   "whole instruction, seeded with what already failed")
        try:
            script = executor.one_shot(s, digest, self.usage)
        except Exception as e:
            self._emit(f"  last-chance attempt could not be written: {e}")
            return
        if not script.strip():
            self._emit("  last-chance attempt returned no function")
            return

        problems = lint.check(script)
        if problems:
            self._emit("  last-chance script rejected by lint "
                       f"({', '.join(n for n, _ in problems)})")
            return

        work = osp.join(s.work_dir, "last_chance")
        ok, info, log = runner.run_script(script, s.input_step, work)
        if not ok:
            self._emit(f"  last-chance script failed: {info.get('error','?')}")
            self._record({"sub": -1, "goal": "one-shot fallback", "attempt": 0,
                          "ok": False, "views": {}, "verdict": "crashed",
                          "error": info.get("error", ""), "issues": [],
                          "gate": "one-shot", "script": script, "step": None})
            return

        diff = geo.compare(s.input_step, info["step"])
        # The same objective gates every planned attempt must pass. A no-op
        # changes nothing, and a drifted frame scores near zero however good
        # it looks, so neither is worth shipping over the untouched input.
        bad = ("no-op" if diff.get("identical") else
               "frame drift" if diff.get("frame_drift") else
               "phantom material" if diff.get("phantom_material") else None)
        if bad:
            self._emit(f"  last-chance attempt rejected ({bad}) — shipping the "
                       f"unedited input as before")
            self._record({"sub": -1, "goal": "one-shot fallback", "attempt": 0,
                          "ok": True, "views": {}, "verdict": "rejected",
                          "issues": [f"one-shot fallback: {bad}"],
                          "gate": "one-shot", "script": script,
                          "step": info.get("step")})
            return

        s.last_step = info["step"]
        s.accepted_step = info["step"]
        s.script = script
        s.__dict__["one_shot_fallback"] = True
        self._emit(f"  last-chance attempt produced a valid edit "
                   f"({info['faces']} faces, vol {info['volume']}, "
                   f"{diff.get('volume_change_pct')}% change) — shipping it "
                   f"instead of the unedited input")
        self._record({"sub": -1, "goal": "one-shot fallback", "attempt": 0,
                      "ok": True, "views": {}, "verdict": "accepted",
                      "faces": info.get("faces"), "volume": info.get("volume"),
                      "volume_change_pct": diff.get("volume_change_pct"),
                      "issues": [], "gate": "one-shot",
                      "script": script, "step": info.get("step")})

    def _run_digest(self):
        """Every attempt of the whole run, one line each — what was tried and
        why it was refused. Short on purpose: it seeds a single prompt."""
        lines = []
        for rec in self.state.steps:
            what = "; ".join(rec.get("issues") or []) \
                or (rec.get("error") or "")[:120] \
                or (rec.get("observation") or "")[:120] \
                or "(no detail)"
            lines.append(
                f"  - sub {rec.get('sub')} attempt {rec.get('attempt')} "
                f"[{rec.get('verdict', '?')}]: refused because: {what[:200]}")
        return "\n".join(lines[-12:])

    def _failure_digest(self, sub):
        """Every attempt of this sub-goal as one line each: verdict, gate, and
        the issues (which carry the no-op causes distilled from the scripts'
        own prints — the evidence the strategist needs)."""
        lines = []
        for rec in self.state.steps:
            if rec.get("sub") != sub.idx:
                continue
            what = "; ".join(rec.get("issues") or []) \
                or (rec.get("observation") or "")[:200] \
                or "(no detail recorded)"
            gate = f", gate: {rec['gate']}" if rec.get("gate") else ""
            lines.append(f"attempt {rec.get('attempt')} "
                         f"[{rec.get('verdict', '?')}{gate}]: {what[:400]}")
        return "\n".join(lines) or "(no attempt records)"

    def _replan_subgoal(self, sub):
        """Install a fresh strategy for a fully-rejected sub-goal.

        Returns True when the strategist produced one (the caller re-enters
        `_advance` on the same, unchanged geometry). Any failure — an LLM
        error, an empty goal — returns False and the sub-goal retires exactly
        as it did before this mechanism existed.
        """
        s = self.state
        digest = self._failure_digest(sub)
        try:
            new = strategist.replan(s, sub, digest, self.usage)
        except Exception as e:
            self._emit(f"  replan unavailable ({e}) — sub-goal stays failed")
            return False
        if not new:
            self._emit("  strategist produced no usable replan — "
                       "sub-goal stays failed")
            return False
        sub.replans += 1
        sub.goal = new["goal"]
        sub.goal_original = new["goal"]     # partial-refine rewrites key off this
        sub.rationale = new.get("rationale") or sub.rationale
        sub.focus = new.get("focus") or sub.focus
        sub.tags = new.get("tags") or sub.tags
        if new.get("envelope") is not None:
            sub.envelope = new["envelope"]
        sub.status = "active"
        # The new strategy may change what remains to be done, so the replan
        # can also revise the PENDING sub-goals — and only those. Everything
        # at or before this sub-goal's index is out of reach by construction:
        # accepted work is never rewritten. An absent key keeps the pending
        # list exactly as planned; [] deletes it.
        if isinstance(new.get("next_subgoals"), list):
            from .state import SubTask
            keep = s.subtasks[:sub.idx + 1]
            room = max(0, config.MAX_SUBTASKS - len(keep))
            revised = [
                SubTask(idx=sub.idx + 1 + i, goal=n["goal"],
                        rationale=n.get("rationale", ""),
                        focus=n.get("focus") or [],
                        tags=n.get("tags") or [],
                        envelope=n.get("envelope"))
                for i, n in enumerate(new["next_subgoals"][:room])
            ]
            dropped = len(s.subtasks) - len(keep)
            s.subtasks = keep + revised
            self._emit(f"  replan revised the pending sub-goals: "
                       f"{dropped} replaced by {len(revised)}")
        sub.qa_notes.append({"attempt": sub.attempts, "verdict": "replanned",
                             "observation": "strategist issued a new strategy "
                                            "after every attempt was rejected",
                             "issues": []})
        # The opening feedback of the new round: what died, and that repeating
        # it is the one forbidden move. Picked up by _advance.
        s.__dict__["replan_feedback"] = f"""\
EVERY ATTEMPT AT THE PREVIOUS STRATEGY FOR THIS SUB-GOAL WAS REJECTED, and the
part is unchanged. The sub-goal above is a NEW strategy from the planner —
follow its prescribed method. What was tried before and how it died (do NOT
repeat any of it):

{digest}"""
        self._emit(f"  strategist replanned sub-goal {sub.idx} "
                   f"(replan {sub.replans}/{config.MAX_REPLANS}): "
                   f"{new['goal'][:160]}")
        s.log("replan", f"sub-goal {sub.idx}: {new['goal'][:240]}",
              subtask=sub.idx, replans=sub.replans, tags=sub.tags,
              envelope=sub.envelope)
        return True

    # ------------------------------------------------------------------
    # Checkpoints — every accepted state is recoverable
    #
    # A failed attempt can never corrupt the model: each attempt runs against
    # `source_step`, the last accepted geometry, and writes to its own
    # directory, so a crash, a no-op or a rejection simply leaves that state
    # untouched. What was missing is the other direction — a *refinement* that
    # QA accepts but which is worse than the partial it replaced. Every
    # acceptance is recorded here, and the end of a sub-goal rolls back to the
    # best one instead of trusting the last.
    # ------------------------------------------------------------------
    @staticmethod
    def _same_complaint(a, b):
        """Do two QA issue lists say the same thing? Token overlap, no model.

        Only used to detect a refinement that came back with the complaint it
        was sent to fix. Deliberately crude: it compares the words, so
        "Gate is not kept within the required Y=-2.5..2.5: measured bbox is
        Y=-5.0..+5.0" and "...measured bbox is Y=-3.128..+3.128" match on
        everything except the numbers, which is exactly the case it is for.
        """
        def toks(issues):
            words = " ".join(issues or []).lower()
            return {w for w in _re.findall(r"[a-z]{4,}", words)}
        ta, tb = toks(a), toks(b)
        if not ta or not tb:
            return False
        return len(ta & tb) >= 0.7 * min(len(ta), len(tb))

    def _checkpoint(self, sub, attempt, step_rec, verdict, script):
        s = self.state
        issues = list(step_rec.get("issues") or [])
        # A refinement is launched to fix ONE stated issue. If QA hands back
        # the same complaint, it did not do its job — and it must not then
        # outrank the checkpoint it was refining, which `_rank` would give it
        # on recency alone. Measured on task 3YH2WFSRM22W7DKT_1769177116:
        # attempt 3 was scoped to "Gate is not kept within Y=-2.5..2.5",
        # returned "Gate is not kept within Y=-2.5..+2.5" (same axis, same
        # constraint, still violated), replaced attempt 2 anyway, and shipped
        # diff_f1 0.0000 where attempt 2 had scored 0.2740 — the best result
        # recorded on that task.
        prev = next((c for c in reversed(s.checkpoints)
                     if c["sub"] == sub.idx), None)
        no_progress = bool(
            prev and step_rec.get("verdict") == "partial"
            and prev.get("verdict") == "partial"
            and self._same_complaint(issues, prev.get("issues")))
        if no_progress:
            self._emit(f"  refinement returned the same complaint it was sent "
                       f"to fix — attempt {attempt} will not outrank attempt "
                       f"{prev.get('attempt')}")
        s.checkpoints.append({
            "sub": sub.idx, "attempt": attempt,
            "step": step_rec.get("step") or s.last_step,
            "script": script,
            "verdict": step_rec.get("verdict"),
            "issues": issues, "no_progress": no_progress,
            "faces": step_rec.get("faces"), "volume": step_rec.get("volume"),
        })

    @staticmethod
    def _rank(cp):
        """Sort key for "which accepted state was best": full beats partial,
        then the LATER attempt. QA confidence is deliberately not consulted:
        every kept refinement starts FROM the previous kept state, so a later
        partial contains the earlier one's work plus whatever it added, and it
        already passed every measured gate (no-op, phantom material, envelope,
        frame drift) plus QA's achieved=true to get checkpointed at all.
        Ranking by confidence measurably shipped a regression: the lug
        bracket's attempt 3 added the four refused tab-hole chamfers via
        cone-cut booleans (+14 faces, -0.25 mm^3), QA's confidence dipped
        0.78 -> 0.67 on "no clear evidence in the views" of a feature one
        pixel wide, and the revert rolled back to attempt 1 — shipping the
        part without the chamfers it had already built."""
        return (1 if cp.get("verdict") == "accepted" else 0,
                0 if cp.get("no_progress") else 1,
                cp.get("attempt", 0))

    def _revert_to_best(self, sub):
        """Roll this sub-goal back to its best accepted state, if that is not
        already the current one."""
        s = self.state
        mine = [c for c in s.checkpoints if c["sub"] == sub.idx and c.get("step")]
        if len(mine) < 2:
            return
        best = max(mine, key=self._rank)

        # MBR: among the checkpoints `_rank` considers EQUALLY good, prefer the one
        # the other candidates agree with. `_rank` is verdict-then-recency, so a tie
        # is broken by "whichever ran last", which is exactly how this pipeline has
        # shipped worse geometry than it built. Consensus is a better tie-break and
        # costs no model call.
        #
        # Deliberately scoped to TIES. A full acceptance still beats a partial, and a
        # refinement that made progress still beats the state it refined — those
        # orderings are earned, and overriding them on geometry alone is how an
        # earlier confidence-based ranking shipped a regression (see `_rank`).
        mbr_note = None
        if config.SELECTION_POLICY == "mbr":
            top = self._rank(best)
            tied = [c for c in mine if self._rank(c) == top]
            if len(tied) > 1:
                from ..tools import mbr as mbr_mod
                pick = mbr_mod.select(
                    s.__dict__.get("subgoal_start_step") or s.input_step,
                    [c["step"] for c in tied],
                    osp.join(s.work_dir, f"mbr_s{sub.idx}"))
                mbr_note = pick
                # Mirror the consensus onto both the checkpoint and the STEP
                # RECORD, because the dashboard renders step records and this is
                # the only place the number can be seen next to the attempt it
                # describes.
                chosen_step = None
                if not pick.get("abstained") and pick.get("index") is not None:
                    chosen_step = tied[pick["index"]]["step"]
                by_step = {r.get("step"): r for r in s.steps if r.get("step")}
                for c, cons in zip(tied, pick.get("consensus") or []):
                    c["mbr_consensus"] = cons
                    rec = by_step.get(c.get("step"))
                    if rec is not None:
                        rec["mbr_consensus"] = cons
                        rec["mbr_picked"] = (c.get("step") == chosen_step)
                        rec["mbr_distinct"] = pick.get("n_distinct")
                if not pick.get("abstained") and pick.get("index") is not None:
                    chosen = tied[pick["index"]]
                    if chosen is not best:
                        self._emit(
                            f"  MBR: {len(tied)} checkpoints ranked equal; "
                            f"attempt {chosen['attempt']} agrees most with the "
                            f"others ({pick['reason']}) — preferring it over "
                            f"attempt {best['attempt']}")
                    best = chosen
                else:
                    self._emit(f"  MBR abstained: {pick.get('reason')}")
        if mbr_note is not None:
            s.log("mbr", f"sub-goal {sub.idx}: {mbr_note.get('reason')}",
                  subtask=sub.idx, abstained=mbr_note.get("abstained"),
                  n_distinct=mbr_note.get("n_distinct"),
                  consensus=mbr_note.get("consensus"))

        if best["step"] == s.accepted_step:
            return
        current = next((c for c in mine if c["step"] == s.accepted_step), None)
        s.accepted_step = best["step"]
        s.script = best["script"]
        if best.get("verdict") == "accepted":
            sub.status = "done"
        self._emit(
            f"  reverted sub-goal {sub.idx} to attempt {best['attempt']} "
            f"({best['verdict']}) — attempt "
            f"{current['attempt'] if current else '?'} was kept but "
            f"ranked lower")
        s.log("revert", f"sub-goal {sub.idx} rolled back to attempt "
                        f"{best['attempt']}", subtask=sub.idx,
              to_attempt=best["attempt"], verdict=best["verdict"])

    def _dump_exec_io(self, sub, attempt, repair=0):
        """Persist and announce exactly what the executor was given, and said.

        Written per attempt rather than logged inline: the prompt is 5-20k
        characters, which would drown the event log, but it is also the only
        place that explains an attempt nobody can account for from the script
        alone. The log gets the one-line shape; the file gets everything.
        """
        s = self.state
        io = s.__dict__.get("last_exec_io")
        if not io:
            return
        d = osp.join(s.work_dir, f"sub{sub.idx}_try{attempt}")
        name = f"executor_io{f'_repair{repair}' if repair else ''}.txt"
        path = osp.join(d, name)
        # Cleared first: a failed write must not leave the PREVIOUS attempt's
        # dump attached to this one, which would put the wrong prompt behind
        # this step's fold — worse than showing none at all.
        s.__dict__["last_prompt_file"] = None
        try:
            os.makedirs(d, exist_ok=True)
            with open(path, "w") as f:
                f.write(executor.io_dump(io))
            s.__dict__["last_prompt_file"] = path
        except OSError as e:
            self._emit(f"  (could not write {name}: {e})")
        self._emit("  executor in/out: " + executor.io_summary(io))

    def _views_for(self, step_path, stem):
        d = osp.join(self.state.work_dir, f"views_{stem}")
        return rnd.render_views(step_path, d, stem=stem)

    def _tagged_views(self, step_path, out_dir, insp, stem):
        """Feature-colour-coded views of `step_path` plus their legend.

        Returns `({}, [])` rather than raising: the colouring is a prompt aid,
        so a backend that cannot tag faces must cost the run its legend, not
        the run. An empty legend is also the signal the prompts key on — both
        agents fall back to the natural renders when the plan is empty.
        """
        if not insp:
            return {}, []
        try:
            views, plan = rnd.render_views_tagged(step_path, out_dir, insp,
                                                  stem=stem)
            return views or {}, plan or []
        except Exception as e:
            self._emit(f"  tagged render unavailable ({e}) — natural views only")
            return {}, []

    # ------------------------------------------------------------------
    def finalize(self, out_root, user_id):
        """Write the run out in the exact layout the benchmark ingests:

            <edit_id>/brep_end/<ts>/{settings.json,tmp.step,tmp.stl,tmp_<view>.jpg}
        """
        import json
        import time

        s = self.state
        ts = s.started
        edit_id = f"{user_id}_{ts}"
        dest = osp.join(out_root, edit_id, "brep_end", str(ts))
        os.makedirs(dest, exist_ok=True)

        settings = {
            "edit_request_id": s.request_id,
            "edit_id": edit_id,
            "start_time": ts,
            "end_time": s.finished or time.time(),
            "isHuman": False,
            "userId": user_id,
            "token_counts": self.usage.as_dict(),
            "plan": {"understanding": s.plan_summary,
                     "subtasks": [{"goal": t.goal, "status": t.status,
                                   "attempts": t.attempts} for t in s.subtasks]},
        }

        # ALWAYS ship geometry. A run with nothing accepted used to write no
        # tmp.step/tmp.stl at all, and the scorer reads a missing STL as 0.0 on
        # all three metrics. But two of those metrics are near-saturated on this
        # benchmark: measured on four benchmark tasks, handing back the
        # UNEDITED INPUT scores 0.915-0.990 chamfer similarity and 0.389-0.998
        # volume F1, because the human's edit is a small delta off the same
        # part. Only Diff F1 reads it as a zero, and a failed run scores zero
        # there either way. The baseline harness gets this for free — its
        # generated function ends in `except: return shape`, so a giving-up
        # iteration still exports the input — which is most of why it beats a
        # harness that fails honestly.
        source, provenance = s.accepted_step, "edited"
        if not source or not osp.exists(source):
            source, provenance = s.input_step, "input-unedited"
            settings["failed_run"] = True
        settings["geometry_source"] = provenance

        if not osp.exists(source):
            settings["failed_run"] = True
            settings["filename"] = None
            settings["unscoreable"] = "no geometry to export, not even the input"
        else:
            step_dest = osp.join(dest, "tmp.step")
            shutil.copy(source, step_dest)
            settings["filename"] = step_dest
            # STL + all 7 views in a single child process
            try:
                pngs, stl = rnd.render_and_export(
                    step_dest, dest, osp.join(dest, "tmp.stl"), stem="tmp")
                from PIL import Image
                for v, p in pngs.items():
                    Image.open(p).convert("RGB").save(
                        osp.join(dest, f"tmp_{v}.jpg"), quality=90)
                    os.remove(p)
                if not stl:
                    settings["stl_error"] = "STL export produced no file"
            except Exception as e:
                settings["render_error"] = str(e)

            # The scorer reads a missing tmp.stl as 0.0 on all three metrics,
            # so a failed combined render+export gets one dedicated STL-only
            # retry in a fresh child before the run is declared unscoreable.
            stl_dest = osp.join(dest, "tmp.stl")
            if not osp.exists(stl_dest) and rnd.export_stl(step_dest, stl_dest):
                settings.pop("stl_error", None)
                self._emit("  STL-only retry succeeded after the combined "
                           "render+export failed")

            for ext in ("step", "stl"):
                if not osp.exists(osp.join(dest, f"tmp.{ext}")):
                    settings["failed_run"] = True
                    settings["unscoreable"] = f"tmp.{ext} was not written"

        if provenance == "input-unedited":
            self._emit("no edit survived — shipping the unedited input so the "
                       "run is scoreable instead of a zero")

        self._persist_step_views(dest)

        with open(osp.join(dest, "settings.json"), "w") as f:
            json.dump(settings, f, indent=2)
        s.save(osp.join(dest, "session_state.json"))
        self._emit(f"wrote {dest}")
        return dest, settings

    def _persist_step_views(self, dest):
        """Copy each attempt's renders, CAD file and script into <dest>/steps/.

        The per-attempt artifacts live in the work directory, which is deleted
        after a clean run. Without this the dashboard's step-by-step gallery
        would go blank the moment a run finished, and the geometry of every
        attempt but the winner would be gone — including the near-misses that
        are the most useful thing to open in a CAD viewer afterwards.

        Naming is `sub<goal>_try<attempt>_<verdict>.step` alongside the
        matching `.py`, so a directory listing reads as the run's history.
        """
        s = self.state
        steps_dir = osp.join(dest, "steps")
        kept = files = 0
        relocated = {}                      # work-dir STEP -> its saved copy

        for rec in s.steps:
            stem = f"sub{rec['sub']}_try{rec['attempt']}_{rec.get('verdict','?')}"
            src_step, src_py = rec.get("step"), rec.get("script")

            if src_step and osp.exists(src_step):
                os.makedirs(steps_dir, exist_ok=True)
                out = osp.join(steps_dir, f"{stem}.step")
                try:
                    shutil.copy(src_step, out)
                    relocated[src_step] = out
                    rec["step"] = out
                    files += 1
                except OSError:
                    pass

            src_io = rec.get("prompt_file")
            if src_io and osp.exists(src_io):
                os.makedirs(steps_dir, exist_ok=True)
                out = osp.join(steps_dir, f"{stem}_executor_io.txt")
                try:
                    shutil.copy(src_io, out)
                    rec["prompt_file"] = out
                    files += 1
                except OSError:
                    pass

            if src_py:
                os.makedirs(steps_dir, exist_ok=True)
                out = osp.join(steps_dir, f"{stem}.py")
                try:
                    with open(out, "w") as f:
                        f.write(src_py)
                    rec["script_file"] = out
                    files += 1
                except OSError:
                    pass

        # Checkpoints point at work-directory STEPs that are about to be
        # deleted; repoint them at the copies, and swap the inlined script for
        # its file so the session JSON does not carry every source twice.
        for cp in s.checkpoints:
            cp["step"] = relocated.get(cp.get("step"), cp.get("step"))
            if cp.pop("script", None):
                cp["script_file"] = osp.join(
                    steps_dir, f"sub{cp['sub']}_try{cp['attempt']}_"
                               f"{cp.get('verdict','?')}.py")

        for rec in s.steps:
            # "views" are the natural renders; "views_changed" the QA-facing
            # change-coloured twins. Both live in the doomed work dir.
            for key, suffix in (("views", ""), ("views_changed", "_chg")):
                views = rec.get(key) or {}
                if not views:
                    continue
                os.makedirs(steps_dir, exist_ok=True)
                moved = {}
                for view, src in views.items():
                    if not osp.exists(src):
                        continue
                    out = osp.join(
                        steps_dir,
                        f"sub{rec['sub']}_try{rec['attempt']}_{view}{suffix}.jpg")
                    try:
                        from PIL import Image
                        Image.open(src).convert("RGB").save(out, quality=85)
                    except Exception:
                        try:
                            shutil.copy(src, out)
                        except OSError:
                            continue
                    moved[view] = out
                    kept += 1
                rec[key] = moved
        if kept or files:
            self._emit(f"kept {kept} step renders and {files} attempt "
                       f"files (.step/.py) in steps/")
