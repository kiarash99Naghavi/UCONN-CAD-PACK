"""Executor — writes the CadQuery function for exactly one sub-goal.

Sees: the sub-goal, the geometry index, the script that produced the currently
accepted state, and whatever went wrong last attempt (traceback or QA notes).
Does not see: the other sub-goals. Keeping the window narrow is what keeps the
context from ballooning across a run.
"""

from ..adk.llm import LLM, image_part, text_part
from .. import config
from ..tools import geometry as geo
from ..tools import skillref as skl

SYSTEM = """You are an expert CadQuery programmer editing an existing B-rep solid.
You write one Python function and nothing else.
Reply with a single JSON object and nothing else."""

CONTRACT = '''\
Your function must have exactly this shape:

def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    # ... your edit ...
    return shape          # a Workplane, Shape, or Assembly

Once you return it, the function is executed in CadQuery. You will then be
shown renders of the result and everything the function printed, and you can
refine it. Print freely — those prints are your instruments; a selection that
silently matched nothing is the most common way an attempt dies, and a printed
count of 0 catches it.

Requirements:
- ALWAYS start from args["input_file"] and EDIT the imported shape. Never
  rebuild the part from scratch — the benchmark scores your *change* against
  the human's change, so a rebuild that merely looks similar scores near zero.
- Change only what the sub-goal asks for; leave every other feature untouched.
- The sub-goal's `face #N` / `edge_idx [...]` tags are indices into the geometry
  index above, enumerated in the order CadQuery returns entities: resolve them as
  `base = shape.val() if hasattr(shape, 'val') else shape;
  target = base.Faces()[N]` (same for `.Edges()`), then PRINT the resolved
  entity's center/area and check it against the index line before using it — a
  mismatch means you resolved against a different shape than the index describes.
  When the renders are color-coded, the legend's colors correspond to exactly
  these face_idx tags — use them to confirm you resolved the right entity.
- Return the shape. Do not write files, do not exit.
- Keep the input's coordinate frame. All metrics voxelize your result and the
  human's in ABSOLUTE world coordinates with no alignment step, so position is
  graded as strictly as shape: never translate, rotate, re-centre or rescale
  the part, and place new geometry at explicit absolute coordinates (beware
  `centered=True`, `.center()`, and fresh workplanes at the origin).
- When the sub-goal adds material "on" or "at" an existing face, decide which
  side it goes: default to keeping the outer envelope unchanged unless the
  instruction says the part gets bigger.
- A screw or bolt is NEVER just a cylinder: it has a HEAD — HEXAGONAL unless
  the instruction says otherwise — sitting on the shaft. Build both: a hex
  prism (`.polygon(6, d)` with d the circumscribed diameter) unioned with the
  cylindrical shank, head seated flush on the surface the screw enters. A
  shaft-only "screw" is scored as missing material exactly where the human
  put the head.
- A screw's or pin's shank does NOT stop flush where the hole ends: real
  fasteners PROTRUDE. Run the shank past the hole's far mouth by a visible
  offset (a thread-or-two — on the order of the shank diameter, unless the
  instruction gives a length), and PRINT the shank's extreme along the hole
  axis against the hole's exit plane to prove the overhang exists. A pin cut
  to exactly the hole depth reads flush in every render and scores as missing
  the protruding material the human modelled.

REPORT EVERY SELECTION, every time. The moment your code narrows the part down
to the entities it will operate on, print one line per selection step:

    SELECTED: <n> <what> for <purpose>   e.g.
    SELECTED: 4 edges for the slot rim fillet  idx=[0, 4, 7, 10]
    SELECTED: 0 faces matching normal +Z on solid 1   <-- this is the bug

An attempt that changes nothing is rejected without a QA call, and if it
printed nothing there is no way to tell a selector that matched the wrong
entity from one that matched none — the next attempt then gets generic advice
and repeats the mistake. Measured: 69% of no-op attempts across this project
printed no diagnosis at all, and they are the single largest block of wasted
budget. A count of zero is the most valuable line you can print: say so
loudly, and do not silently fall through to returning the input.

PLACEMENT SELF-CHECK, in the same function, every time:
1. List every number the sub-goal names: coordinates, a face centre, a target
   angle, a level the feature must reach but not exceed.
2. Isolate the new material generically — `added = out.cut(base)`
   (`removed = base.cut(out)` for cuts), which works for text, polygons,
   bosses, plateaus and blades alike. Print `added.Center()`,
   `added.BoundingBox()`, its extreme along the growth axis.
3. Print each of those against the number the sub-goal named, and the delta.
4. Off by more than ~1 mm on any axis, or across a plane the sub-goal said not
   to exceed: TRANSLATE OR REBUILD AND RE-PRINT IN THE SAME ATTEMPT. Never
   return a feature whose printed position disagrees with the sub-goal. If it
   named no coordinates, print anyway and change nothing.
A recipe may offer a sharper measurement for a feature type (a landing angle
for rotations, vertex angles for polygons, a top level for text) — prefer those
over the bbox. Measured: a text feature built 48 mm from the centre its
sub-goal named scored zero while looking correct in every render.

EDIT THE IMPORTED SOLID; NEVER REBUILD IT. Load the STEP and apply local
operations to it — cut, fuse, fillet, chamfer on the entities you selected.
Do NOT re-author the whole part: no scale-by-1.0, no global sew/heal/ShapeFix
pass "to be safe", no reconstructing the body from its faces, no re-tessellation
of anything you did not change. The score compares your result to the input
volume-by-volume, and a face that was rebuilt lands in slightly different
coordinates than the one it replaced even when the shape is visually
identical — so a global rebuild sprays small differences across the entire
part and buries the actual edit in noise. On a multi-body file, operate on the
ONE body that owns your target and recompound the rest untouched:

    sols = base.Solids()
    edited = sols[k].cut(tool)            # or .fillet(...) / .union(...)
    out = cq.Compound.makeCompound(
        [s for i, s in enumerate(sols) if i != k] + [edited])

Every other body then comes through byte-identical, which is both correct and
exactly what the metric rewards.

ABSOLUTE ANCHORS ARE BUILT, NEVER PICKED. When the sub-goal names an absolute
plane or coordinate for a feature ("on the plane z=-7.5", "centered at
(0, ±15) on the bottom face"), construct the sketch plane directly from those
numbers — `cq.Workplane(cq.Plane(origin=(x, y, z), normal=(nx, ny, nz)))` —
and NEVER by selecting a face and offsetting from it: a face pick that lands
on a different surface silently re-anchors the whole feature there, and the
placement self-check then compares the feature against the wrong reference.
Print the plane's origin before sketching on it.

A DRAFT IS A WEDGE BOOLEAN, NOT A REBUILD. CadQuery has no draft operation,
and rebuilding the walls tilted is how a 2-degree draft turns into a doubled
footprint or a skewed base. Instead, for each face to be drafted: keep its
hinge edge (the edge on the neutral plane) exactly where it is, and CUT the
thin wedge between the face and its tilted copy — a prism swept from the
hinge line, thickness growing from 0 at the hinge to H*tan(angle) at the far
edge, where H is the face's height off the neutral plane. (Material-adding
drafts UNION the wedge instead.) Do the faces one at a time in try/except and
keep the successes. Verify with two prints: the neutral-plane face's bbox
BEFORE vs AFTER (must be identical — the hinge does not move), and one far
edge's measured shift vs H*tan(angle). The part's outer bbox on the neutral
plane must not change at all.

TO REACH "UP TO THE BODY", MEASURE THE LANDING WITH A PROBE. When a feature
must run from its anchor until it meets the part ("up to the next surface",
"reach the wall"), do not rely on an `until=` end condition resolving against
the face you hope for. Measure the stopping height yourself: intersect the
part with a thin probe prism along the feature's axis at the feature's (x, y)
— `probe = part.intersect(box_around_axis)` — read the near side of
`probe.BoundingBox()` along the axis, build the feature as an explicit solid
from the anchor plane to that measured height (plus a small overlap into the
body so the union fuses), and print both the measured height and the built
extent. This works identically for curved, tilted and stepped targets, and
the print proves where the feature actually stopped.

API FORMS THAT DO NOT EXIST HERE (cadquery 2.8.0 / OCP 7.9.3) — every line
below was a crashed or silently-wrong attempt in this pipeline, so check your
code against it before you return:
- `f.normalAt()` takes NO ARGUMENTS. `f.normalAt(u, v)` does not raise: it
  returns a (Vector, Vector) TUPLE, so the failure surfaces far away as
  "'tuple' has no attribute 'z'". This is the single most frequent mistake here.
- `Vector.Length` is a PROPERTY (`v.Length`); `Edge.Length()` is a method.
  Calling `v.Length()` gives "'float' object is not callable".
- `BoundBox` has `.xmin/.xmax/.ymin/.../.xlen/.center` — no `.toTuple()`, no
  `.min`, no `.max`.
- `Wire.IsClosed()` is capitalised; there is no `isClosed` anywhere.
- On a Shape/Solid the booleans are `fuse` / `cut` / `intersect` — `union` and
  `common` exist only on `Workplane` / in OCC. `chamfer` on a Shape takes three
  arguments: `solid.chamfer(length, None, edgeList)`.
- `Workplane` has no `.hull()`, `.wrapped` or `.isValid()`; go through `.val()`.
- `Face` has no `.extrude()`; use `cq.Solid.extrudeLinear(face, vec)`.
- `cq.Compound.makeCompound([...])` reads `.wrapped` off each element itself,
  so pass CadQuery Shapes — never `.wrapped` TopoDS objects, never Workplanes.
- `Wire.offset2D(d)` returns a LIST of Wires (`Workplane.offset2D` does not).
- `cq.Plane.XY` is a classmethod: CALL it — `cq.Workplane(cq.Plane.XY())`.
- OCP: no lowercase `topods`, no `TopTools_*Iterator`, and the statics need an
  `_s` suffix (`TopExp.MapShapes_s`, `TopoDS.Face_s(shp)`); `cq.Shape.cast(t)`
  converts back. Prefer staying in CadQuery: `shape.Faces()`, `face.Edges()`.

Approach freely — plain CadQuery selectors (`.faces(">Z")`,
`.edges("%CIRCLE")`, `NearestToPointSelector`), filtering `shape.Faces()` /
`shape.Edges()` by the measured values in the geometry index, or direct
construction all work. Keep the selection simple, and where an operation can
fail on some entities (fillet/chamfer over many edges), prefer doing it
per-entity in a try/except and keeping the successes: a partial edit scores, a
crash does not.
'''

TEMPLATE = """\
OVERALL INSTRUCTION (context only — do NOT try to do all of it now):
{instruction}

YOUR SINGLE SUB-GOAL FOR THIS STEP:
{goal}
Why this step is here: {rationale}

{geometry}

{tool_doc}

{contract}

{recipes}{prior}

{feedback}
{last_resort}
Return JSON:
{{
  "my_cad_function": "<the complete function source as a string>"
}}"""

# Sent on the final proposal for a sub-goal, or once bespoke selection code has
# matched nothing twice. It deliberately contradicts part of the contract above,
# and says so: the contract's advice ("select from the measured index, not with
# string selectors") is right on average and wrong here, because the observed
# failure is not a mis-selection but 180 lines of hand-rolled region tests and
# adjacency maps that select NOTHING and exit through `return shape`. The
# single-shot baseline wins exactly these tasks with a four-line selector chain.
LAST_RESORT = """
THIS IS YOUR LAST PROPOSAL FOR THIS SUB-GOAL. If it does not land, the part
ships without this edit and scores zero on the only metric that measures
editing at all.

So change tactics instead of refining — WRITE THE SIMPLEST THING THAT COULD
POSSIBLY WORK. Every extra stage of custom selection logic is another place to
silently match nothing, and that is how the previous attempts died. In order of
preference:

1. A plain CadQuery selector chain: `.faces(">Z")`, `.edges("%CIRCLE")`,
   `.edges("|Z")`, `cq.selectors.NearestToPointSelector((x, y, z))`. It may
   pick a slightly wrong entity; your bespoke selection has already picked
   NOTHING more than once, which is strictly worse.
2. ONE filter expression over `shape.Edges()` or `shape.Faces()` with a
   generous tolerance, then the operation. No ownership search, no multi-stage
   scoring, no nested fallback ladders.
3. The bluntest version of the sub-goal that still counts as the edit: one hole
   family instead of all of them, a subset of the intended edges, one feature
   instead of the set. The one thing you may NOT blunt is a dimension the
   CUSTOMER named — a radius, a thickness, a hole diameter — so blunt the edge
   count or the feature count, never the number, and if the kernel refuses a
   blend at the customer's radius take the boolean route instead of shrinking
   it. A partial edit scores; nothing scores zero.

Whatever you do, DO NOT hand back the input untouched. Catch a failing
operation and keep whatever succeeded, but never end a branch with
`return shape` as a safety net — that path is what turned the last attempts
into no-ops, and a no-op is scored exactly like a crash.
"""

PRIOR_NONE = "This is the first change; args[\"input_file\"] is the original part."
PRIOR_SOME = """\
The accepted state so far was produced by this function. args["input_file"] now
points at ITS output, so start from the current state — do not re-apply what is
already done:

{script}"""

# A partial acceptance KEEPS the flawed geometry, so the next attempt starts
# from a part that already contains the mistake. Left to itself the model reads
# "do not re-apply what is already done" and adds a second copy of the feature
# instead of correcting the first — measured on the rotor: a blade placed at
# the wrong angle was kept, and the refinement added another blade beside it,
# leaving six arms on a rotor that should have three.
PRIOR_PARTIAL = """\
The current state was produced by the function below AND KEPT, even though QA
found it only partly right. args["input_file"] points at ITS output, so the
flawed feature is ALREADY IN THE PART you are about to load:

{script}

YOU ARE FIXING THAT FEATURE, NOT ADDING ANOTHER ONE. If QA said it is in the
wrong place, at the wrong angle, or duplicated, then locate it (it is the
newest/odd-one-out body or face group — compare against the counts in the index
above) and MOVE, ROTATE, CUT AWAY or REBUILD it. Adding a second copy beside
the first makes the part worse than leaving it alone, and QA now checks for
exactly that.

DO NOT WRITE AN "IS IT ALREADY DONE?" GUARD. The partial work is already in
this part, so any check of the form "if the feature is already at the target,
return unchanged" WILL fire and hand back the input — which scores zero, the
same as a crash. Measured on a spanner cutout: the plan was to move an end-wall
from z=-110 to z=-120, the kept partial had moved a 10 mm-wide strip of it, and
the next script searched for the end-wall, found the strip already sitting at
z=-120, printed "already near target; returning unmodified" and returned the
input — twice, burning both remaining attempts.

The flaw QA named is a flaw in EXTENT, POSITION or SHAPE, not in existence.
Test for the thing QA actually complained about — is the feature the full width
it should be, at the right angle, the right depth — never merely whether
something exists near the target. When in doubt, rebuild the whole feature
correctly rather than probing whether it is already right."""


def _view_parts(views, caption):
    """Image parts for a view dict, or [] when there is nothing to show."""
    if not config.SHOW_EXECUTOR_VIEWS or not views:
        return []
    picked = [(v, views[v]) for v in config.EXECUTOR_VIEWS if views.get(v)]
    if not picked:
        return []
    parts = [text_part(f"{caption} ({', '.join(v for v, _ in picked)}):")]
    shown = 0
    for view, path in picked:
        try:
            img = image_part(path)          # first, so a failure leaves no
        except OSError:                     # orphan label behind
            continue
        parts.append(text_part(f"[{view}]"))
        parts.append(img)
        shown += 1
    return parts if shown else []


def _legend_text(plan):
    """The colour legend as fixed-width text lines, one per feature family.

    Same shape as the strategist's, deliberately duplicated: the two prompts
    must read identically to the model, and tools/ belongs to the renderer.
    """
    # Every row comes from the plan itself — including the muted "other faces"
    # shades (or, for a pre-adjacency inspection, the gray sentinel), so the
    # legend never claims a gray remainder the render does not have.
    return "\n".join(
        f"  {str(p.get('name', '?')):<8} {p.get('label', '')}  {p.get('tags', '')}"
        for p in plan)


def build(state, feedback_text, usage, current_views=None, last_resort=False,
          tagged_views=None, color_plan=None):
    """Write the function for the current sub-goal.

    `current_views` renders the state args["input_file"] will load — the only
    geometry the executor is shown. It is optional; without it the prompt is
    text-only, exactly as it was before images were wired in.

    `tagged_views` + `color_plan` are the feature-COLOUR-CODED twin of that
    same state and its legend. When both are present they are shown INSTEAD of
    `current_views`, with the legend as a text table beside the images: the
    contract asks this agent to resolve `face #N` tags against the index, and a
    colour that maps to an explicit face_idx list is the cheapest way to check
    it resolved the entity the sub-goal meant. Absent (or empty — the router
    empties them after a partial acceptance, whose index is stale), the natural
    renders are used exactly as before.

    The previous attempt's renders are deliberately NOT shown. A rejected
    attempt's geometry is discarded and never loaded, so picturing it invites
    the model to keep iterating on a part that no longer exists; what it needs
    from that attempt is QA's reasoning, which arrives as text in
    `feedback_text`. (A PARTIAL acceptance is the exception that proves the
    rule: there the flawed geometry was KEPT, so it is the current state and
    already shown by `current_views`.)

    `last_resort` swaps refinement for the bluntest thing that could work; the
    router sets it on the final proposal and after repeated no-ops.
    """
    llm = LLM(config.MODEL_EXECUTOR, usage, role="executor")
    sub = state.current

    # Refining a partial is a different job from continuing after a clean
    # acceptance, and needs a different framing of the same script.
    refining = sub.status == "partial" and bool(state.script)
    if not state.script:
        prior = PRIOR_NONE
    elif refining:
        prior = PRIOR_PARTIAL.format(script=state.script)
    else:
        prior = PRIOR_SOME.format(script=state.script)
    fb = feedback_text or "No previous attempt for this sub-goal."

    # THE COMPLETE INDEX, ALWAYS. Retrieval may decide which recipe to attach;
    # it may never decide what geometry this agent is allowed to see. A sliced
    # index can omit the very family a sub-goal needs while the contract below
    # still demands selection "by measured numbers", and the model cannot tell
    # an absent feature from a hidden one — so it invents numbers, the selector
    # matches nothing, and the attempt is a silent no-op. Filtering is confined
    # to recipe selection, which is `skillref`'s job. Re-rendered from the
    # current accepted state when we have it; otherwise the text carried on the
    # state.
    insp = state.__dict__.get("inspection")
    geometry_text = geo.to_prompt(insp) if insp else state.geometry_text
    focus_terms = " ".join(sub.focus or [])

    # Verified recipes for this kind of edit, from the cadquery-editor skill.
    # The strategist's focus terms steer the section choice, plus its geometric
    # tags, which pin their sections outright. This is the ONLY thing those cues
    # are allowed to narrow — text added to the prompt, never geometry. getattr
    # keeps sessions planned before tags existed loadable. Empty when the skill
    # is disabled or has nothing for this sub-goal.
    tags = getattr(sub, "tags", None) or []
    recipes, recipe_ids = skl.recipes_for(state.instruction, sub.goal, focus_terms,
                                          tags=tags)
    # Carries its own trailing break, so with no recipes the prompt is exactly
    # the one this agent rendered before the skill existed.
    recipes = recipes + "\n\n" if recipes else ""

    prompt = TEMPLATE.format(
        instruction=state.instruction,
        goal=sub.goal,
        rationale=sub.rationale,
        geometry=geometry_text,
        tool_doc=geo.TOOL_DOC,
        contract=CONTRACT,
        recipes=recipes,
        prior=prior,
        feedback=fb,
        last_resort=LAST_RESORT if last_resort else "",
    )
    parts = [text_part(prompt)]
    coloured = (_view_parts(tagged_views,
                            "THE PART AS IT IS NOW, FEATURE-COLOR-CODED — this "
                            "is exactly what args[\"input_file\"] loads, and "
                            "the only geometry you are shown; each color is "
                            "one feature family")
                if tagged_views and color_plan else [])
    if coloured:
        parts.append(text_part(
            "COLOR LEGEND — each color is one feature family; the table maps "
            "colors to the index's unique tags:\n" + _legend_text(color_plan)))
        parts += coloured
    else:
        parts += _view_parts(current_views,
                             "THE PART AS IT IS NOW — this is exactly what "
                             "args[\"input_file\"] loads, and the only geometry "
                             "you are shown")

    def _views_used(views):
        if not (config.SHOW_EXECUTOR_VIEWS and views):
            return []
        return [v for v in config.EXECUTOR_VIEWS if views.get(v)]

    images = ([f"current:{v}" for v in _views_used(current_views)]
              + [f"tagged:{v}" for v in _views_used(tagged_views)])
    n_images = sum(1 for p in parts if p.get("type") == "image_url")

    out = llm.json(SYSTEM, parts)
    script = (out.get("my_cad_function") or "").strip()

    # Exactly what this agent was given and exactly what it said, kept on the
    # runtime state (not a dataclass field, so it never reaches
    # session_state.json). The router writes it next to the attempt and the
    # dashboard folds it open on demand: when an attempt fails for a reason
    # nobody can explain from the script alone, the answer is almost always
    # something that was, or was not, in this prompt.
    state.__dict__["last_exec_io"] = {
        "system": SYSTEM,
        "prompt": prompt,
        "n_images": n_images,
        "image_views": images,
        "script": script,
        "recipes": recipe_ids,
        "tags": tags,
        "last_resort": last_resort,
        "prior_kind": ("none" if not state.script
                       else "partial (fix it in place)" if refining
                       else "accepted (build on it)"),
        # every block of the prompt, so an oversized one is obvious at a glance
        "sizes": {
            "geometry index": len(geometry_text),
            "recipes": len(recipes),
            "prior script": len(prior),
            "feedback": len(fb),
            "contract + tool doc": len(CONTRACT) + len(geo.TOOL_DOC),
            "last resort": len(LAST_RESORT) if last_resort else 0,
            "TOTAL prompt": len(prompt),
        },
    }

    if "def my_cad_function" not in script:
        raise ValueError("executor did not return a my_cad_function definition")
    state.log("execute", f"sub-goal {sub.idx}: wrote a candidate function",
              subtask=sub.idx, attempt=sub.attempts, recipes=recipe_ids,
              tags=tags, last_resort=last_resort, images=n_images)
    return script


def io_summary(io):
    """One live-log line: what the executor was handed, biggest block first."""
    if not io:
        return ""
    sizes = io["sizes"]
    total = sizes["TOTAL prompt"]
    parts = ", ".join(f"{k} {v // 4}t" for k, v in sorted(
        ((k, v) for k, v in sizes.items() if k != "TOTAL prompt" and v),
        key=lambda kv: -kv[1]))
    bits = [f"~{total // 4} tokens ({parts})", f"{io['n_images']} image(s)",
            f"prior: {io['prior_kind']}"]
    if io["tags"]:
        bits.append(f"tags {', '.join(io['tags'])}")
    if io["recipes"]:
        bits.append(f"recipes {io['recipes']}")
    if io["last_resort"]:
        bits.append("LAST RESORT")
    return " · ".join(bits)


def io_dump(io):
    """The whole exchange as one readable file."""
    if not io:
        return ""
    sizes = "\n".join(f"  {k:<22} {v:>7} chars  (~{v // 4} tokens)"
                      for k, v in io["sizes"].items())
    return f"""\
========================= EXECUTOR CALL =========================
tags        : {', '.join(io['tags']) or '(none)'}
recipes     : {io['recipes'] or '(none)'}
prior script: {io['prior_kind']}
last resort : {io['last_resort']}
images      : {io['n_images']}

PROMPT SIZE BY BLOCK
{sizes}

========================= SYSTEM =========================
{io['system']}

========================= USER PROMPT =========================
{io['prompt']}

========================= REPLY · my_cad_function =========================
{io['script']}
"""


# A generated function runs 40-120 lines; keep the tail, which is where the
# edit and the return live, and where the traceback almost always points.
_SCRIPT_TAIL_CHARS = 6000


def _ran_block(script, log):
    """The code that just ran and what it printed, verbatim.

    Without this the model is handed a traceback for code it cannot see: the
    prompt only ever showed the last *accepted* script, so a failed attempt was
    invisible to its own retry. It then rewrites from scratch and reintroduces
    the same bug — the same AttributeError killed attempts 1 and 3 of one
    sub-goal in a measured run.
    """
    src = (script or "").strip()
    if len(src) > _SCRIPT_TAIL_CHARS:
        src = "# ... start of function omitted ...\n" + src[-_SCRIPT_TAIL_CHARS:]
    out = (log or "").strip() or "(the function printed nothing)"
    return f"""\
THE FUNCTION YOU JUST RAN:

{src}

WHAT IT PRINTED:
{out}"""


# The escape hatch for a blend the kernel will not perform. Referenced from the
# _TRAPS entries below AND from noop_feedback, so the advice a crash gets and
# the advice a no-op gets cannot drift apart. Measured on the blade task: the
# correct edge was selected and `solid.fillet(6.35, [edge])` still raised
# `BRep_API: command not done` (r=3.0 on the SAME edge succeeded); the script's
# fallback returned the unchanged solid three times, three no-ops, score 0.0,
# on a task another model scored 1.0.
MANUAL_BLEND_FIX = '''\
When the OCC kernel refuses a fillet at a radius the CUSTOMER specified, the
radius is not negotiable — change the METHOD. For a CONVEX edge `e` that is a
LINE between two PLANE faces, blend it with booleans instead:

      adj = [f for f in solid.Faces() if any(x.hashCode() == e.hashCode() for x in f.Edges())]
      n1, n2 = [f.normalAt().normalized() for f in adj[:2]]
      P, t = e.positionAt(0.5), e.tangentAt(0.5).normalized()
      b = (n1 + n2).normalized()
      C = P - b * (r / b.dot(n1))            # axis of the fillet cylinder
      slab = lambda n: cq.Workplane(cq.Plane(P, (cq.Vector(0,0,1) if abs(n.z)<0.9
                       else cq.Vector(1,0,0)).cross(n).normalized(), n)
                       ).rect(BIG, BIG).extrude(-r).val()
      corner = slab(n1).intersect(slab(n2)).intersect(solid)
      cutter = corner.cut(cq.Solid.makeCylinder(r, L, C - t*L/2, t))
      out    = solid.cut(cutter)

Three checks, all cheap:
- CONVEXITY FIRST: `solid.isInside(P - b*eps)` must be True and
  `solid.isInside(P + b*eps)` False. On a CONCAVE edge this removes the very
  material a fillet would have added.
- The slabs are unbounded along `t`: if `corner` runs longer than `e.Length()`,
  clip it to the edge's endpoints before cutting.
- PRINT `removed = solid.Volume() - out.Volume()` and check it against
  `cutter.Volume()`, and for a 90-degree corner against
  `(1 - math.pi/4) * r**2 * L`.

This reproduced a ground-truth blade radius to 0.003% (removed 3296.9 against
an expected 3297.0) on an edge where `fillet()` refused outright.'''


# The circular-edge counterpart. Measured on the lug-rim bracket: chamfer(0.2)
# on four r=1.4 arc edges raised `BRep_API: command not done` at EVERY size
# (0.05/0.1/0.2), per-edge and batched, and fillet() too — on the original
# input as much as on the edited one, so the refusal is a property of the
# edges, not of prior edits. Two attempts caught it in try/except, returned
# the input unchanged, and scored 0.0 as no-ops. The cone cut below reproduced
# the 0.2 mm chamfer to 2.5% of the analytic wedge volume on those same edges.
MANUAL_CIRC_CHAMFER_FIX = '''\
When the refused edge is a CIRCLE or circular ARC (a hole entrance or a boss
rim), the blend is a surface of revolution — build it by cutting a CONE. Two
traps first:

- For an ARC, `e.Center()` is the arc's CENTER OF MASS, not the circle
  center: on a measured 3.25 mm arc of an r=1.4 circle they differ by 1.1 mm,
  so a cutter placed at `e.Center()` carves the wrong region entirely. Take
  the true center and axis from the curve:

      from OCP.BRepAdaptor import BRepAdaptor_Curve
      circ = BRepAdaptor_Curve(e.wrapped).Circle()
      L, A = circ.Location(), circ.Axis().Direction()
      C = cq.Vector(L.X(), L.Y(), L.Z())
      n = cq.Vector(A.X(), A.Y(), A.Z()).normalized()

- Decide which side holds material BEFORE building the cutter — do not trust
  the sub-goal's wording ("hole" may turn out to be a boss rim). With
  `pm = e.positionAt(0.5)` and `radial = (pm - C).normalized()`:

      probe = lambda d, rr: solid.isInside(C + d*(c/2) + radial*rr)
      # Axial direction into material. Probe INSIDE the rim radius, BOTH ways
      # — do not shortcut this with a single probe or a probe outside r: on a
      # boss rim, outside-r is air in both directions, so an outside probe
      # picks a direction at random and the cutter is built floating in air
      # (measured: cutterVol 0.2, removed 0.000, attempt died as a no-op).
      into = n if probe(n, r - 0.05) else (-n if probe(-n, r - 0.05) else None)
      if into is None:      # both inside-r probes are air: a hole entrance —
                            # material lies OUTSIDE r, so orient by that
          into = n if probe(n, r + 0.05) else -n
      boss  = not probe(into, r + 0.05)         # True: material INSIDE r (rim)
                                                # False: material OUTSIDE r (hole)

Then cut (c = chamfer leg, r = edge radius):

      if boss:      # convex rim: wedge between a widening cone and the rim
          cutter = cq.Solid.makeCylinder(r + 0.2, c, C, into).cut(
                   cq.Solid.makeCone(r - c, r, c, pnt=C, dir=into))
      else:         # hole entrance: cone ring, bore left untouched
          cutter = cq.Solid.makeCone(r + c, r, c, pnt=C, dir=into).cut(
                   cq.Solid.makeCylinder(r, c + 0.1, C - into*0.05, into))
      out = solid.cut(cutter)

If the edge is a PARTIAL arc, clip the cutter to the arc's angular span first
— an unclipped ring gouges a groove wherever the rest of the circle runs
through solid material (measured: 6x the intended removal). Skip this for a
full 360-degree circle:

      pts  = [C + (e.positionAt(i/16) - C)*3.0 for i in range(17)]
      ring = [C - into*0.05] + [p - into*0.05 for p in pts]
      wire = cq.Wire.assembleEdges([cq.Edge.makeLine(a, b)
                                    for a, b in zip(ring, ring[1:] + ring[:1])])
      pie  = cq.Solid.extrudeLinear(cq.Face.makeFromWires(wire), into*(c + 0.1))
      cutter = cutter.intersect(pie)

PRINT `before.Volume() - out.Volume()` and check it against the wedge
estimate `0.5 * c*c * sum(e.Length() for e in edges) * ((r -/+ c/2) / r)`
(minus for a boss rim, plus for a hole entrance). If the cut removed ZERO
volume, the cutter is floating in air on the wrong side of the surface —
FLIP `into`, rebuild the cutter and re-cut IN THE SAME ATTEMPT; zero removal
is never evidence the edge was already done.

THE VOLUME CHECK ALONE IS NOT ENOUGH: a cutter clipped to the wrong angular
span removes exactly the right amount of material from the wrong part of the
rim and passes it. Measured: a lug-rim retry removed 0.25 mm^3 (expected
0.24) but carved the +Z side of each rim circle while the physical arc — and
the human's chamfer — sat on the opposite side; the attempt scored diff F1
0.0 with a perfect volume print. So ALSO PRINT WHERE it came off:
`removed = before.cut(out)`, then `removed.Center()` against the mean of the
selected arcs' own midpoints `e.positionAt(0.5)`. They must agree to within
about `c` plus a voxel; a mismatch on the order of `r` means the cut landed
on the wrong span — rebuild the pie clip from the edges you actually
selected and re-cut IN THE SAME ATTEMPT. This recipe reproduced a refused
0.2 mm lug-rim chamfer to 2.5% (removed 0.2475 against an expected 0.2414) on
four arcs where `chamfer()` AND `fillet()` were refused at every size.'''


# Error signature -> (token that must appear in the script, precise remedy).
# Each was diagnosed by replaying a real failed attempt; the contract carries
# the same facts, but a model that has already crashed twice on one of them is
# demonstrably not reading them, so the exact fix is put in front of it.
_TRAPS = [
    ("object has no attribute 'z'", "normalAt(",
     "`Face.normalAt(u, v)` with TWO arguments returns a (Vector, Vector) "
     "TUPLE instead of a Vector, and does not raise — so try/except cannot "
     "catch it. Call `f.normalAt()` with NO arguments, or pass a single "
     "point: `f.normalAt(cq.Vector(x, y, z))`."),
    ("Unsupported point/vector type", "normalAt(",
     "The tuple came from `Face.normalAt(u, v)`: called with two arguments it "
     "returns (normal, point) instead of a Vector. Call `f.normalAt()` with "
     "no arguments."),
    ("object has no attribute 'getAngle'", "normalAt(",
     "`Face.normalAt(u, v)` returns a (Vector, Vector) tuple. Call "
     "`f.normalAt()` with no arguments."),
    ("object has no attribute 'BoundingBox'", "offset2D(",
     "`Wire.offset2D(d)` returns a LIST of Wires. Index it, or pick the "
     "largest by area. (`Workplane.offset2D` returns a Workplane — different "
     "type, same name.)\n\n"
     "      wires = face.outerWire().offset2D(2.0)   # list, possibly several\n"
     "      outer = max(wires, key=lambda w: cq.Face.makeFromWires(w).Area())\n"
     "      ring  = cq.Face.makeFromWires(outer, [face.outerWire()])"),
    ("chamfer() missing 1 required positional argument", "chamfer(",
     "On a SHAPE (Solid/Compound), `chamfer()` takes THREE arguments: "
     "`solid.chamfer(length, length2, edgeList)` — there is no two-argument "
     "form, so `solid.chamfer(c, [edges])` binds the list to `length2` and "
     "dies. For a symmetric 45-degree chamfer pass None in the middle: "
     "`solid.chamfer(c, None, [edges])`. (The two-argument form exists only "
     "on Workplane, a different class.)"),
    ("object has no attribute 'hull'", "",
     "`Workplane.hull()` does not exist in this CadQuery version, in any "
     "form, and neither does `cadquery.func.hull`. Build a slot / obround "
     "outline explicitly: two circles joined by their common tangent lines, "
     "`.rect()` unioned with two `.circle()` ends, or a sketch of "
     "`.moveTo/.lineTo/.radiusArc/.close()` extruded."),
    ("BRep_API: command not done", "",
     "The OCC kernel REFUSED this chamfer/fillet outright. If the edge set is "
     "already CORRECT and the radius is one the CUSTOMER named, this refusal "
     "is TERMINAL for `fillet()`: the same edge at the same radius will be "
     "refused every time, retrying is a wasted attempt, and shrinking the "
     "radius is not the edit that was asked for. Switch methods — pick the "
     "recipe by the refused edge's TYPE.\n\n"
     + MANUAL_BLEND_FIX + "\n\n" + MANUAL_CIRC_CHAMFER_FIX + "\n\n"
     "Only if the operation covers a MULTI-EDGE family whose membership you "
     "are not sure of: try a DIFFERENT edge set first — drop edges already "
     "tangent to a blend, or do one family at a time inside a try/except so a "
     "single bad edge cannot kill the whole operation and you keep the "
     "successes."),
    ("no suitable edges", "",
     "The OCC kernel refused this edge set for a chamfer/fillet. Retrying at a "
     "smaller value will fail the same way, and if the radius is one the "
     "CUSTOMER named you may not shrink it: for a single correct edge this is "
     "TERMINAL for `fillet()`, so change methods — pick the recipe by the "
     "refused edge's TYPE.\n\n"
     + MANUAL_BLEND_FIX + "\n\n" + MANUAL_CIRC_CHAMFER_FIX + "\n\n"
     "For a multi-edge family whose membership you are unsure of, the "
     "secondary branch still applies: pick different edges, one family at a "
     "time, keeping whatever succeeds."),
    ("TopTools_ListIteratorOfListOfShape", "",
     "That class DOES NOT EXIST in OCP — there are no *Iterator classes at "
     "all, OCP lists are directly Python-iterable, and static methods take an "
     "`_s` suffix (`TopExp.MapShapesAndAncestors_s`). You do not need any of "
     "it: iterate `face.Edges()` for the edges of a face and de-duplicate on "
     "`e.hashCode()`."),
    # ---- runtime traps: nothing in the SOURCE says these will happen, so
    # tools/lint.py cannot see them and they have to be caught after the crash.
    ("object has no attribute 'isClosed'", "",
     "There is no `isClosed` anywhere in this CadQuery — the method is "
     "CAPITALISED: `wire.IsClosed()` (same on `Edge`). Note that a "
     "`hasattr(w, 'isClosed')` guard does not fix this, it just skips the check "
     "silently; and an `except AttributeError:` branch that retries "
     "`w.isClosed()` raises again from inside the handler, which is how this "
     "crash usually reaches the harness. Write `w.IsClosed()` everywhere."),

    ("'float' object is not callable", "Length(",
     "`cq.Vector.Length` is a PROPERTY, not a method: write `v.Length` with no "
     "parentheses (or `v.Length` compared directly). Only `Shape` subclasses — "
     "`Edge.Length()`, `Wire.Length()` — have it as a callable method, which is "
     "why the same spelling works on an edge and dies on a vector."),

    ("Bnd_Box is void", "",
     "`BoundingBox()` was called on an EMPTY shape — the OCC box has no "
     "content because the boolean before it produced nothing: a `cut` whose "
     "tool swallowed the whole body, an `intersect` whose operands do not "
     "overlap, or a compound built from an empty list. The bounding box is the "
     "messenger, not the bug. Print the counts first and stop there if the "
     "operation produced nothing:\n\n"
     "      print(f'after cut: solids={len(res.Solids())} vol={res.Volume()}')\n"
     "      if not res.Solids():        # the boolean missed entirely\n"
     "          ...                     # fix the tool's position/size, do NOT\n"
     "                                  # go on to measure an empty shape\n\n"
     "The usual cause is a tool solid built at the wrong absolute coordinates, "
     "so check the tool's own `Center()`/`BoundingBox()` against the part's "
     "BEFORE you boolean them."),

    ("Cannot find a solid on the stack", "",
     "A `Workplane` operation that needs an existing solid (`cutBlind`, "
     "`cutThruAll`, `hole`, `extrude(combine=True)`, `fillet`, `chamfer`) was "
     "called on a workplane that has no solid on it — a fresh "
     "`cq.Workplane('XY')` carries only your sketch, never the imported part. "
     "Seed the workplane with the part, or skip the workplane entirely:\n\n"
     "      wp  = cq.Workplane(obj=base)          # base is a cq.Solid/Compound\n"
     "      out = wp.faces('>Z').workplane().hole(6.0)\n"
     "      # or, simpler and always available:\n"
     "      out = base.cut(tool_solid)            # Shape-level boolean\n\n"
     "Building the tool as its own solid and doing `base.cut(tool)` / "
     "`base.fuse(tool)` avoids this class of error completely.\n\n"
     "BUT IF YOU WERE ROUNDING A 2D PROFILE RATHER THAN THE PART — a rounded "
     "rectangle or obround outline you are about to extrude — seeding the "
     "workplane with the part is the WRONG fix: it would round the PART's "
     "edges. `Workplane.fillet` is a 3D solid operation and always calls "
     "`findSolid()`, so it can never round a pending sketch. Use one of these "
     "instead (all three verified against this install, identical result):\n\n"
     "      wp.sketch().rect(w, h).vertices().fillet(r).finalize().extrude(t)\n"
     "      # or build the Sketch separately:\n"
     "      sk = cq.Sketch().rect(w, h).vertices().fillet(r)\n"
     "      cq.Workplane('XY').placeSketch(sk).extrude(t)\n"
     "      # or extrude first, then round the prism's vertical edges:\n"
     "      cq.Workplane('XY').rect(w, h).extrude(t).edges('|Z').fillet(r)\n\n"
     "`cq.Workplane(...).rect(w, h).vertices().fillet(r).extrude(t)` is the "
     "exact form that raises this error, and it has cost a full attempt on two "
     "separate tasks."),

    ("~zero volume", "",
     "You returned a shape with essentially no material. That is almost never "
     "the edit — it is one of: returning the TOOL solid instead of the result, "
     "returning `a.intersect(b)` where the two do not overlap, a `cut` whose "
     "tool enclosed the whole part, or a compound assembled from an empty "
     "list. Return the EDITED PART, and prove it before returning:\n\n"
     "      print(f'volume {base.Volume():.3f} -> {out.Volume():.3f}')\n\n"
     "If that print shows a near-zero result, fix it IN THE SAME ATTEMPT — the "
     "harness rejects the shape outright, so returning it burns the attempt "
     "exactly like a crash."),

    ("Add(): incompatible function arguments", "DraftAngle",
     "`BRepOffsetAPI_DraftAngle.Add` requires exactly "
     "`(TopoDS_Face, gp_Dir, float, gp_Pln, bool)` — the fourth argument is the "
     "NEUTRAL PLANE as a `gp_Pln`, and passing an edge or a face there is what "
     "raised this. Build the plane from numbers: "
     "`gp_Pln(gp_Pnt(0, 0, z_hinge), gp_Dir(0, 0, 1))`.\n\n"
     "Better: do not use `BRepOffsetAPI_DraftAngle` at all. The contract's "
     "wedge-boolean route (cut the thin prism between each face and its tilted "
     "copy, hinge edge held fixed) is the method that has actually worked here, "
     "it does one face at a time so a single refusal cannot kill the run, and "
     "it lets you verify the hinge did not move."),

    ("broke geometry that was intact", "",
     "Your boolean invalidated a solid that was fine before. The result is "
     "only rejected when the count of invalid solids GOES UP, so isolate the "
     "damage: cut only the solid that owns the target face, check "
     "`edited.isValid()` yourself, and if it comes back False keep that solid "
     "UNCHANGED and recompound the rest — a partial edit scores, a rejected "
     "one does not:\n\n"
     "      owner  = <the solid carrying the face you measured>\n"
     "      edited = owner.cut(tool)\n"
     "      if not edited.isValid():\n"
     "          edited = edited.clean()       # sometimes repairs it\n"
     "      if not edited.isValid():\n"
     "          edited = owner                # keep it rather than break it\n"
     "      out = cq.Compound.makeCompound(\n"
     "          [s for s in shape.Solids() if s is not owner] + [edited])"),
    ("isValid()", "",
     "The result is not a valid B-rep and the input could not be re-read for "
     "comparison. Cut only the solid that owns the target face, try "
     "`out.clean()`, and verify `isValid()` before returning."),
]


def _trap_hint(error, script):
    """The precise fix for a known trap, when this crash is one of them."""
    err, src = str(error or ""), script or ""
    for sig, needs, fix in _TRAPS:
        if sig in err and (not needs or needs in src):
            return f"\nTHIS IS A KNOWN TRAP — the fix is specific:\n{fix}\n"
    return ""


def failure_feedback(info, log, script=""):
    """Turn a crashed run into instructions the model can act on."""
    return f"""\
YOUR PREVIOUS ATTEMPT FAILED. Fix it.

Error: {info.get('error', 'unknown')}
{_trap_hint(info.get('error'), script)}
{_ran_block(script, log)}

Diagnose the actual cause in the code above before rewriting — you are editing
THIS function, not starting over, and rewriting from scratch is how the same
error comes back a second time. If a selector matched nothing or matched the
wrong entities, select differently using the geometry index rather than
retrying the same selector with different syntax."""


import re as _re

# One pattern per way a clean run ends up changing nothing, tuned against real
# logs: exceptions the script caught itself, selections that matched zero
# entities, per-entity loops that skipped everything.
_NOOP_SIGNS = _re.compile(
    r"raised|traceback|error|exception|standard_fail|brep_api|refused|"
    r"no suitable|not done|no live match|out of range|no match|"
    r"matched 0\b|selected 0\b|found 0\b|applied[ =:]0\b|applied 0\b|"
    r"\b0 (?:faces|edges|entities|candidates)\b|skip", _re.IGNORECASE)


def noop_diagnosis(log):
    """Distill WHY a clean run changed nothing, from the script's own prints.

    "Output is geometrically identical to the input" names the symptom; the
    cause — a kernel refusal caught in a try/except, a selection that matched
    nothing, indices resolved against the wrong part — is in the prints.
    Surfacing it on the step record and at the top of the retry feedback is
    the difference between a retry that fixes the cause and a retry that
    repeats it: measured on the lug bracket, two consecutive no-ops received
    only the generic symptom line and reproduced the same skip-everything run.
    """
    lines = [l.strip() for l in (log or "").splitlines()
             if l.strip() and not l.startswith("RESULT:")]
    hits, seen = [], set()
    for l in lines:
        if not _NOOP_SIGNS.search(l):
            continue
        # collapse per-entity repeats: the same complaint about idx 120 and
        # idx 287 is one cause, not two, and slots are scarce
        key = _re.sub(r"\d+", "#", l)[:80]
        if key in seen:
            continue
        seen.add(key)
        hits.append(l[:200])
    if not hits:                        # no signature — the tail usually holds
        hits = [l[:200] for l in lines[-3:]]        # the script's own summary
    return "; ".join(hits[:5])


def noop_feedback(diff, log="", script="", topology_only=False):
    """The script ran cleanly but moved no material."""
    if topology_only:
        head = f"""\
YOUR PREVIOUS ATTEMPT CHANGED THE TOPOLOGY BUT MOVED NO MATERIAL.

Face and edge counts changed ({diff.get('faces')} faces, {diff.get('edges')}
edges) while the volume did not move at all ({diff.get('volume_mm3')}) and the
bounding box is identical. That means your tool solid touched the part without
overlapping it: it imprinted edges on a face instead of cutting through
material. A cut that removes 0 mm^3 scores exactly like a crash.

The usual causes are a tool that is coplanar with the face instead of crossing
it, a cut depth of zero, or a cutter positioned just outside the body. Make the
tool overlap the material generously — start it above the face and run it well
past the far side — and PRINT the volume before and after so you can see it
move."""
    else:
        head = f"""\
YOUR PREVIOUS ATTEMPT RAN WITHOUT ERROR BUT CHANGED NOTHING.

The output solid is geometrically identical to the input: {diff.get('faces')}
faces, {diff.get('edges')} edges, volume unchanged. A no-op scores ZERO on the
metric that matters, so this is as bad as a crash."""
        why = noop_diagnosis(log)
        if why:
            head += f"""

WHAT YOUR OWN PRINTS SAY WENT WRONG — fix THIS, not the symptom:
  {why}"""

    # _TRAPS is keyed on an error string and a no-op has no error, so a blend
    # the kernel silently refused inside a try/except could never reach the
    # blend remedy. It reaches it here instead: measured on the blade task,
    # three attempts caught the refusal, returned the input unchanged and
    # scored 0.0 without ever being shown the boolean route.
    blend = f"""

YOUR BLEND WAS REFUSED, NOT MIS-SELECTED. Your ladder returned the input
because `applied == 0`; a boolean blend cannot be refused by the kernel.
Pick the recipe by the refused edge's TYPE — straight edge or circular edge:

{MANUAL_BLEND_FIX}

{MANUAL_CIRC_CHAMFER_FIX}""" if any(
        op in (script or "") for op in ("fillet(", "chamfer(", "blend(")) else ""

    return f"""\
{head}

{_ran_block(script, log)}

Read what it printed first — a count of 0, or a volume that did not move, is
the whole diagnosis. The usual cause is a selection that matched nothing: a
selector string that found no entities, a radius/position tolerance too tight
to match anything, or an operation whose result was computed but never
returned.

Fix it by:
- PRINTING how many entities your selection matched before you modify them,
  e.g. print(f"selected {{len(edges)}} edges"). If it prints 0, the selection is
  the bug, not the operation.
- Widening the tolerance, or selecting from the geometry index by a value you
  can see listed there.
- Making sure you return the MODIFIED shape, not the original one you loaded.
- Changing the APPROACH if you already tried the same selection twice —
  repeating it with different syntax will produce the same nothing.{blend}"""


def _axis_hint(diff, undeclared):
    """A rotation about the wrong axis has a signature: the part suddenly grows
    along the direction it is thinnest in."""
    size = diff.get("bbox_before") or []
    if len(size) != 3 or not undeclared:
        return ""
    axes = dict(zip(("X", "Y", "Z"), size))
    thin = min(axes, key=axes.get)
    grown = {f[-1] for f in undeclared}
    if thin not in grown:
        return ""
    biggest = max(abs(mm) for f, mm in undeclared.items() if f.endswith(thin))
    return (f"\nThe part is THINNEST along {thin} ({axes[thin]} mm), and that is "
            f"the axis that grew, by {biggest} mm. When a duplicated body swings "
            f"out along a part's thin axis, the cause is almost always a "
            f"rotation about the WRONG AXIS: the shaft/stack axis of an "
            f"assembly like this is the thin one ({thin}), so a copy rotated "
            f"about {thin} stays in the plane of the original. Rotate about "
            f"{thin}, not about the long axes.\n")


def envelope_feedback(diff, declared, undeclared):
    """The edit moved a bbox face the sub-goal never said it would move."""
    moves = ", ".join(f"{face} by {mm} mm" for face, mm in sorted(undeclared.items()))
    allowed = ", ".join(declared) if declared else "none — the outer envelope had to stay put"
    return f"""\
YOUR PREVIOUS ATTEMPT PUT THE MATERIAL ON THE WRONG SIDE.

This sub-goal declared which faces of the bounding box it may move: {allowed}.
Your result moved: {moves}.
{_axis_hint(diff, undeclared)}
{geo.envelope_text(diff)}

The score compares your solid to the human's in absolute world coordinates with
no alignment step, so a correctly-shaped feature built on the wrong side of its
reference face scores close to zero while looking right in every render.

The usual cause is extruding away from the parent instead of into it. When a
feature "starts on" or "sits at" an existing face, that face normally stays
exactly where it is and the new material occupies the body's own space: extrude
in the opposite direction, or sink the tool solid into the parent (e.g.
`.workplane(offset=-t)` before extruding) and union it. Re-check the sign of
every direction vector and offset you used."""


ONE_SHOT_SYSTEM = """You are an expert CadQuery function generator.
Reply with a single JSON object and nothing else."""

ONE_SHOT_TEMPLATE = """\
Edit the existing model according to the instruction below.

INSTRUCTION (from the customer):
{instruction}

{geometry}

{contract}

THIS IS THE RUN'S LAST CHANCE. The planned, step-by-step approach was tried
and produced nothing shippable; without a working function here the customer
gets their part back UNCHANGED, which scores zero on the only metric that
measures editing.

WHAT WAS ALREADY TRIED, AND HOW IT FAILED — do not walk into these again:
{digest}

So do NOT reproduce that strategy. Write the SIMPLEST function that could
possibly perform the customer's edit:
- Prefer one plain CadQuery selector chain (`.faces(">Z")`, `.edges("%CIRCLE")`,
  `cq.selectors.NearestToPointSelector((x, y, z))`) or ONE filter over
  `shape.Faces()` / `shape.Edges()` with a generous tolerance. No multi-stage
  selection, no fallback ladders, no bespoke wire-mapping — those are what
  failed above.
- If the full edit is out of reach, do the BLUNTEST version that is still
  recognisably what the customer asked for: one feature instead of the whole
  pattern, a subset of the edges. A partial edit in the right place scores; an
  unchanged part does not.
- The one thing you may NOT compromise is a dimension the customer stated.
- Catch failures around the operation and return the best shape you have, but
  NEVER hand back the input untouched without trying.

Return JSON:
{{
  "my_cad_function": "<the complete function source>"
}}"""


def one_shot(state, digest, usage, geometry_text=""):
    """A single, unplanned attempt at the whole instruction — the last resort.

    The pipeline's strength is decomposition; its failure mode is spending a
    whole budget executing one wrong plan. When nothing survived, the cheapest
    remaining option is what the benchmark's own harness does: hand the raw
    instruction and the part to one model and ask for the simplest script that
    performs it. Seeded with the digest of what already failed so it does not
    re-enter the same dead ends.
    """
    llm = LLM(config.MODEL_EXECUTOR, usage, role="one-shot-fallback")
    prompt = ONE_SHOT_TEMPLATE.format(
        instruction=state.instruction,
        geometry=geometry_text or state.geometry_text,
        contract=CONTRACT,
        digest=digest or "  (no attempt details recorded)",
    )
    out = llm.json(ONE_SHOT_SYSTEM, [text_part(prompt)])
    script = (out.get("my_cad_function") or "").strip()
    state.__dict__["last_exec_io"] = {
        "sizes": {"TOTAL prompt": len(prompt)},
        "recipes": [], "tags": ["one-shot-fallback"],
    }
    return script


def qa_feedback(verdict, diff, kept=False, script="", log=""):
    """Turn a QA verdict into instructions the model can act on.

    `kept` distinguishes the two cases that read almost identically but call
    for opposite actions: a rejection threw the geometry away (start fresh
    against the last approved state, with QA's issues as things NOT to do), a
    partial KEPT it (the flaw is in the part you are about to load — repair it
    in place).

    `script`/`log` carry the attempt being judged and what it printed. Crash
    and no-op feedback have always included them; a QA rejection did not, so
    the one path where the model most needs to know WHICH selector it used and
    what that selector actually matched was the one path that withheld it. The
    baseline harness this pipeline is measured against keeps the whole
    conversation — code, stdout and render — across ten turns, and that memory
    is most of why it recovers from a bad first attempt.
    """
    if kept:
        issues = "\n".join(f"  - {i}" for i in verdict.get("issues", [])) or "  - (none given)"
        head = """\
YOUR PREVIOUS ATTEMPT RAN AND WAS KEPT, BUT ONLY PARTLY RIGHT.

Its geometry is now the input you are about to load. The problems below are
IN THAT PART. Fix them where they are — move, rotate, cut away or rebuild the
feature you added. Do NOT add a second copy of it: that is the single most
expensive mistake at this step, because the part ends up with two of something
the customer asked for once, and every later step inherits it."""
        return f"""\
{head}

What QA saw in the six orthographic views:
{verdict.get('observation', '')}

Problems:
{issues}

Guidance: {verdict.get('guidance', '')}

Measured geometric change of that attempt:
  faces {diff.get('faces')}   edges {diff.get('edges')}
  volume {diff.get('volume_mm3')}  ({diff.get('volume_change_pct')}%)
  new surface types: {diff.get('new_surface_types')}
{geo.envelope_text(diff)}

Rewrite the function to address these specific problems."""

    donts = "\n".join(f"  - DO NOT repeat this: {i}"
                      for i in verdict.get("issues", [])) \
        or "  - (no specific issue named)"
    return f"""\
YOUR PREVIOUS ATTEMPT WAS REJECTED AND DISCARDED.

Its geometry is gone. args["input_file"] is the last APPROVED state, untouched
by the rejected attempt — nothing that script did is in the part you are about
to load, so never write an "is it already done?" guard.

The script and its output are reproduced below. READ WHAT YOUR SELECTORS
ACTUALLY MATCHED before you decide what to change: if a selector matched the
wrong entity, or matched nothing, that is the thing to fix, and rewriting the
whole approach from scratch usually reintroduces the same mistake in new words.
Keep whatever demonstrably worked; change the part QA objected to.
{_ran_block(script, log)}

WHAT NOT TO DO (from QA's rejection):
{donts}

What QA saw:
{verdict.get('observation', '')}

The one correction QA named: {verdict.get('guidance', '')}

Measured geometric change of that attempt:
  faces {diff.get('faces')}   edges {diff.get('edges')}
  volume {diff.get('volume_mm3')}  ({diff.get('volume_change_pct')}%)
  new surface types: {diff.get('new_surface_types')}
{geo.envelope_text(diff)}

Fix what QA objected to. If the objection is about EXTENT, POSITION or
COMPLETENESS — too small, too far, only some of the features — correct those
numbers and keep the approach that located the target. If it is structural —
wrong feature, wrong side, wrong axis, wrong body — change the approach
itself, since better parameters cannot rescue the wrong target."""


def frame_feedback(diff):
    """The whole part moved instead of being edited — no QA call needed."""
    return f"""\
YOUR PREVIOUS ATTEMPT MOVED THE PART INSTEAD OF EDITING IT.

{diff.get('frame_drift')}

The score compares your solid to the human's in absolute world coordinates with
no alignment step, so a perfectly-shaped part in the wrong position scores close
to zero. Position is not cosmetic here.

  bbox before: min {diff.get('bbox_min_before')}  max {diff.get('bbox_max_before')}
  bbox after:  min {diff.get('bbox_min_after')}   max {diff.get('bbox_max_after')}

Rewrite the function so it keeps the input's frame exactly: no translate, no
rotate, no re-centring, no unit conversion. Build every new feature at absolute
coordinates read from the geometry index, and apply the edit to the imported
shape rather than reconstructing it."""
