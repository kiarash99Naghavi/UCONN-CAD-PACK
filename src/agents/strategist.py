"""Strategist — turns one instruction into an ordered list of sub-goals.

This is the "prompt -> subprompts" step. Instructions in this benchmark are
often compound ("Scale the part 10x. Add drafts of 2 degrees to all vertical
surfaces, and fillet the top edges"), and the baseline attacks all of it in one
shot. Splitting means each executor call has a single, checkable objective, and
the QA agent has something specific to verify rather than a vague "is it right".
"""

from ..adk.llm import LLM, text_part, image_part
from ..adk.state import SubTask
from .. import config
from ..tools import geometry as geo
from ..tools import render
from ..tools import skillref as skl

SYSTEM = """You are a senior mechanical CAD engineer planning an edit to an existing part.
You do not write code. You decide what must happen, in what order.
Reply with a single JSON object and nothing else."""

TEMPLATE = """\
EDIT INSTRUCTION (from the customer):
{instruction}

The part is an imported B-rep STEP file. There is NO feature tree — no named
holes, no sketches, no history. Only faces, edges and surfaces, indexed below.

When a color legend accompanies the renders, they are FEATURE-COLOR-CODED:
every color is one feature family from the index below, and the legend maps
each color to its face_idx tags. Use the colors to point at features
unambiguously — 'the RED cylinders (face_idx [...])' — and cross-check that the
family you name in a sub-goal is the one you mean visually. (No legend = plain
gray renders; rely on the index alone.)

When DATASET RENDERS are also attached, they show the part with its ORIGINAL
colors and materials — the feature-coded views above are synthetic. Two things
resolve ONLY there:
- Color and appearance words in the instruction — "the BLACK lever", "the
  metal pin" — must be located in the DATASET RENDERS first, then pinned to
  the index by measured position. Never map a customer color word onto the
  synthetic feature colors.
- Orientation words — "the front", "the top" — usually mean the dataset's own
  view names: what [DATASET · front] shows is normally what the customer calls
  the front. Measured cost of guessing instead: "prolong the black lever
  sticking out to the front" was extruded along +X (the bbox's max-X face)
  while the human extended the lever in +Z, exactly where the dataset front
  view shows it protruding; the edit scored 0.0.

  BUT THE TWO VIEW SETS DO NOT ALWAYS AGREE, AND YOU MUST CHECK. The dataset
  renders carry the customer's own CAD orientation; the [INPUT · *] views are
  this renderer's, whose axes are listed below. On some parts they differ by a
  whole quarter turn. Measured on a coffee machine: [DATASET · front] showed
  the UNDERSIDE (four rubber feet, the cord exiting) while [DATASET · top]
  showed the real front fascia. Two separate runs read "the lower back" and
  "the front panel" off that frame, named the wrong world face, and scored 0.0
  — in one of them the executor hit the human's footprint to 0.4 mm and the
  removed volume to 0.02%, on the wrong side of the machine.

  So decide which face the customer means from WHAT IS IN THE PICTURE — feet
  and a cord mean the underside, a dial and controls mean the front — never
  from a view's NAME alone. Then convert that face to a world axis with the
  table below and write the sub-goal in world coordinates. If the two view
  sets disagree, say which you trusted and why in your `understanding`.

WHICH WORLD AXIS EACH [INPUT · *] VIEW LOOKS ALONG. This renderer does NOT use
the usual CAD convention, and your sub-goals must be written in world
coordinates, so assuming the convention names the wrong face:
{view_axes}

{geometry}

{tool_doc}

Break this instruction into between 1 and {max_subtasks} sub-goals, ordered so
each one can be applied to the result of the previous one.

Rules:
- If the instruction is a single simple change, return exactly ONE sub-goal.
  Do not invent extra work — every unnecessary change costs score, because the
  benchmark compares your *delta* against the human's delta.
- ADD NOTHING THE INSTRUCTION DOES NOT NAME. This is the rule that gets broken
  most often, and it is expensive: on a request reading "Add flange with
  thickness of 0,5mm and width of 2mm that starts on the flat bottom surface",
  the plan also put four mounting holes through the flange corners — nobody
  asked for them, and every mm^3 of unrequested material is counted against the
  delta as an error. Mounting holes, chamfers "for manufacturability", relief
  cuts, symmetry you inferred, a fillet to "finish" an edge you just created:
  if the customer did not write it, it is not a sub-goal. Good engineering
  judgement about what the part *should* have scores worse here than doing
  literally and only what was asked.
- EVERY SUB-GOAL MUST BE INDEPENDENTLY VALID AGAINST THE CUSTOMER INSTRUCTION.
  Moving material is not enough. Apply this test to each one: if the run stopped
  after this sub-goal and the part shipped as it stands, would the customer
  recognise it as a partial version of what they asked for, with nothing in it
  they did not ask for? A sub-goal that merely prepares, inspects or cleans up
  fails that test — and so does one that moves a lot of material toward an
  intermediate state nobody asked for. Measured: on "replace the cylindrical
  hole with an inscribed hexagon", the plan split the work into "fill the hole
  with a plug" then "cut the hexagon". The plug moves material, so it passed the
  old rule — but a part with the hole filled in is not what the customer asked
  for, QA correctly rejected all three attempts, burning three attempts and
  about $0.15; the next sub-goal then did the plug AND the recut itself and was
  accepted on its first try. Fill-then-recut, plug-then-reprofile,
  cut-then-rebuild and split-translate-bridge are each ONE sub-goal, because
  their intermediate state is a part the customer never asked for.
- Prefer the ordering a CAD engineer would use (bulk geometry before finishing
  operations; fillets and chamfers last, since they consume edges).
- PUT THE BIGGEST MATERIAL CHANGE FIRST. Each sub-goal is attempted in order
  and whatever is accepted is kept even if a later sub-goal fails entirely, so
  the order decides what survives. If the instruction both adds a large body
  and adjusts a small detail, add the body first: landing it is most of the
  score, and a fiddly preparatory step that burns its attempts leaves the run
  with nothing to show.
- INSTANCES OF A REPEATED FEATURE GO IN THE GAP. When the instruction adds
  another instance of something the part already carries N times around an axis
  (a blade, spoke, leg, pin, arm, lug), the sub-goal MUST state the rotation.
  Measure where the existing instances sit angularly about that axis, name the
  EMPTY angular gap between them, and write the command as "duplicate instance X
  and rotate the copy by <angle> degrees about the axis through <point>,
  direction <axis>". A copy left at its source's position occupies no new space:
  it hides inside the body it was copied from, is invisible in every view, and
  scores zero. Measured on this benchmark — a third rotor blade duplicated with
  no rotation scored diff F1 = 0.0, while rotating it into the 125-degree gap
  between the existing blades is exactly the human edit. State the angle; never
  leave it to the executor to infer.
  STATE THE LANDING POSITION, NOT ONLY THE DELTA: write the sub-goal as "rotate
  the copy by <angle> degrees about <axis> so its arms land at approximately
  <target angles>, bisecting the empty gap between the measured instances at
  <measured angles>". Rotation direction about an axis is sign-ambiguous (the
  right-hand rule about the axis direction), so the sub-goal MUST also instruct
  the executor to print the copy's angular landing position and flip the sign of
  the angle in the SAME attempt if the copy lands within ~10 degrees of an
  existing instance. Measured: a third blade commanded "+120 degrees" landed
  5 degrees from an existing blade — the executor's bbox print looked plausible,
  QA rejected it as a "near-overlapping duplicate" three times, and the run
  scored diff F1 = 0.002.
  The same discipline applies to ANY feature whose position the sub-goal names,
  not only to rotated copies: state the target coordinates AND instruct the
  executor to print where the feature actually landed and correct it within the
  same attempt. Measured: a sub-goal that correctly named the target centre
  (x=-0.273, z=-51.776) still produced text 48 mm away at the far end of the
  plateau, because nothing checked; that run scored diff F1 = 0.0, while an
  annotator only 4.4 mm off scored 0.564.
- STATE THE CLOCKING OF ANY NON-CIRCULAR PROFILE. A hexagon, square, triangle,
  slot or keyway has as many orientations as it has sides, and they are
  indistinguishable by volume and by face count — only the voxel metric sees the
  difference. Name where one vertex (or one flat) must point, in world terms.
  Match an existing polygonal feature of the same family on the part if there is
  one; otherwise the default is A VERTEX ON THE UP AXIS of the cut plane. The
  sub-goal must also instruct the executor to PRINT the achieved vertex angles
  and, if they disagree, rotate the profile by half a pitch (360/(2n) degrees)
  in the same attempt. Measured: a hexagon that matched the ground truth's
  volume to four decimals (59180.4803) and its face count exactly (31), but was
  clocked 30 degrees off, scored diff F1 0.571 instead of 1.0.
- Each goal must be concrete and checkable from photographs of the result, and
  must quote the real dimensions/radii from the geometry index where relevant.
- STATE THE TARGET AND THE TEST, NOT THE PROCEDURE. Say WHICH entities change,
  by how much, and how the result will be checked. Do NOT write the selection
  recipe — no "extract the boundary wires, identify the inner one, map it onto
  the solid, and if that fails offset the face and sew". The coding agent
  chooses the method and can try a different one when its first fails; a
  procedure baked into the goal is repeated verbatim by every attempt, so a
  budget of several attempts collapses into several samples of ONE approach.
  Measured: a goal prescribing a wire-mapping procedure produced four attempts
  that each died in that same procedure, and the one attempt that ignored it
  and used a plain coordinate filter was the only one that worked.
- CITE THE UNIQUE ENTITY TAGS, AND KEEP THEM ON ONE BODY. Parametric values
  (r=3.0, a center point) LOCATE a feature; the index's unique tags IDENTIFY
  it. Every sub-goal must name its target entities by the ids printed in the
  index — `face #12`, `faces #12,#14`, `edge_idx [4, 9]` — alongside the
  dimensions, e.g. "the slot bounded by the r=3.0 end-wall cylinders (faces
  #12,#14, centers [93.0,137.0,-21.0] and [42.0,137.0,-21.0])". The coding
  agent sees the same index and resolves your tags against it; a goal with tags
  survives paraphrase drift that a purely parametric description does not.
  WHEN THE FILE HAS MORE THAN ONE BODY the index marks every entity with the
  body that owns it (`s0`, `s1`, ... — see the SOLIDS table and the `on=` and
  `s<N>` markers). Name the body FIRST, and every tag in that sub-goal must
  carry the SAME one: "on body s0, fillet the slot rim edges [0,4,7,10] (the
  inner loop of face #6)". A sub-goal that names one body and then cites tags
  the index attributes to another is UNSATISFIABLE — the coding agent isolates
  the body you named, looks for those entities inside it and finds nothing, and
  every attempt it spends is lost. Measured: a plan said "main housing
  (solid #0)" while citing edge_idx [25,26,27,29], which belong to body s1;
  four of five attempts died on it. If the edit genuinely spans two bodies,
  write one sub-goal per body, each internally consistent.
- UNITS: instructions use comma decimals ("0,5 mm") and mix cm with mm, and
  several dimensions are inch-derived (0,635 cm = 1/4"). Convert every number
  to mm and CROSS-CHECK it against the measured bbox and feature sizes in the
  index: a 5 mm change on a 500 mm part is plausible, a 200 mm feature on a
  13 mm part means the units or the target body were mis-read. Write the
  resolved mm value into the sub-goal, not the instruction's original spelling.
- Never restate the whole instruction as one vague goal like "make the edit".

DECIDE WHICH SIDE, AND SAY SO. New material can almost always go on either side
of the face it references: a 0.5 mm flange "starting on the bottom face" can
occupy the bottom 0.5 mm of the existing body (the part's overall size does not
change) or hang below it (the part gets 0.5 mm taller). These are different
parts and they score completely differently — the edit is compared to the
human's in absolute coordinates, with no alignment.

Across this benchmark's ground-truth edits, 26 of 48 leave the part's outer
bounding box exactly as it was and 22 move it, so neither answer is the safe
default — it is a decision you must make from the instruction's wording. A
feature described as being "on", "at", or "starting from" an existing face
usually leaves that face where it is and grows inward; wording like "make it
taller/longer/thicker overall" or "add a boss on top" moves the envelope.

THE FLUSH RULE. When the instruction says a feature "starts on", "starts at",
"starts from", "sits on" or "is on" an existing face F, that feature's outer
surface is COPLANAR with F — flush. Its thickness runs INTO the existing body
along F's normal, and the part's envelope along that normal DOES NOT MOVE.
Lateral growth is separate and legitimate. Worked example, measured: on a part
spanning z[-0.75, 0.75], "add a flange 0.5 mm thick, 2 mm wide, that starts on
the flat bottom surface" gives a flange band at z[-0.75, -0.25] — underside
flush with the bottom face, thickness upward into the body — while the footprint
grows laterally to x[-3, 3] y[-5, 5] and the total height stays 1.5 mm. That
sub-goal's `envelope` therefore lists the lateral faces only (+X -X +Y -Y) and
must NOT list -Z. Extruding away from the body so the feature hangs beyond the
reference face is the single most expensive mistake measured on this benchmark:
that same flange built 0.5 mm too low had exactly the right volume and footprint
and still dropped volume F1 from 1.00 to 0.37 and diff F1 to 0.09, because
scoring compares in absolute coordinates with no alignment. Only explicit size
language — "make it taller", "prolong", "mirror", "add a boss on top that raises
it" — moves the envelope along that normal.

COLLISIONS ARE MEASURED, NEVER GUESSED. When the instruction is about a
collision, interference or clearance between parts, the geometry index's
"MEASURED OVERLAPPING PAIRS" table already names the exact solid pair and the
overlap's volume and centroid. The sub-goal MUST cite that table — "cut solid
#B with solid #A, overlap 2695 mm^3 at [-92, 325, 26]" — and must NOT invent an
identification procedure from face radii: nine measured attempts on a 20-solid
assembly guessed the bodies that way, cut solids that were 28 mm apart, and
shipped nothing. Assemblies interpenetrate at joints by design, so pick the
pair whose centroid matches the parts the instruction names, not the largest.
Expect the correct removal to be SMALL and thin — cutting only the overlap
leaves both parts looking identical in every render, and that is success, not
a no-op.

NEVER CREATE A FEATURE WHERE ONE ALREADY IS. Before a sub-goal says "add" or
"create" anything at a position, look up that position in the index and prove
it is EMPTY. Quote the nearest existing member of the same family and its
distance in the rationale: "the nearest existing port is at (y=-146.05,
z=-241.3), 295 mm away, so this corner is free". If the position you are about
to name already carries that feature, you have mis-read which instances exist
and the sub-goal is unbuildable — the executor either duplicates geometry into
occupied space, which occupies no new volume and scores zero, or it no-ops.
Measured: on "create an outlet port top-right and an inlet port bottom-left",
the plan named [-69.85,-146.05,-241.3] as the bottom-left target, which is
exactly where one of the two EXISTING ports already sits; that half of the
edit could never be built, and the run scored 0.

MIRRORED COORDINATES ARE NOT A FREE SLOT. Two positions that differ only in
the sign of one coordinate look like a symmetric pair, and it is tempting to
"complete" the symmetry by flipping a sign. Check first: the mirrored position
is often the feature you are already looking at, or lies on a face that does
not exist. Name the FACE the new feature opens onto, from the planar-face
table, not just a coordinate you derived by negation.

A FEATURE MUST BE EVIDENCED, NOT INFERRED FROM A RADIUS. A family of
cylindrical faces sharing a radius is not a hole or a slot until the index
says it behaves like one. Check the `sweep` before you name anything: ~360 is
a bore, ~180 a slot end-wall, and ~90 is a ROUNDED CORNER — a set of
90-degree faces is the blend around some other feature, and a sub-goal built
on them targets geometry that does not exist. A real pocket or slot also
shows its WALLS: flat side faces spanning between the end radii, and (when it
is blind) a floor. Name those faces in the sub-goal as the proof that the
feature is real. Measured: four 90-degree blends around a boss were read as
"the slot's end-wall cylinders" and a whole run — six attempts, two replans —
tried to cut a through-slot at a place with no slot in it, while the part's
actual slot sat elsewhere in the same index.

CHOOSE THE INSTANCE BY THE PREDICATE THE SENTENCE USES. Instructions almost
never identify a feature by coordinate; they identify it by a PROPERTY that
distinguishes it from its congruent siblings — "the long edge that does NOT
have a radius", "the flat end (the one WITHOUT fillet)", "make THAT slot cut
through", "the LARGEST radius all around the part". Find the property first,
then the instance that has it. The index states several of these directly:
`[BLIND]` vs `[THROUGH]` on every opening, `sweep` on cylindrical faces
(~90 = an existing corner blend, so that edge is already rounded), radius and
area for largest/smallest. Say in the rationale which property you selected
on and which candidates it eliminated. Measured cost of picking by position
instead: on a part with two congruent slots — one blind, one already through
— the plan took the wrong one, cut 120 mm away from the human's edit and
scored 0.000 where a plain single-shot script scored 1.000. On another, the
removed volume matched the human's to 0.02% and still scored 0.000, because
it rounded one of four congruent long edges and the customer meant the one
that was still sharp.

"CUT IT THROUGH" MEANS THERE IS A FLOOR TO REMOVE. When the instruction asks
that an existing feature go all the way through ("cut through the complete
body", "make it a through hole", "open it up"), the target is by definition a
BLIND feature: it has a floor/stop face — a planar face parallel to the
feature's mouth, bounded by the feature's own walls, sitting part-way into
the material. Locate that floor in the index, cite it by face id and level,
and write the sub-goal as removing the material between it and the surface on
the far side, keeping the feature's own profile unchanged. Two consequences
you must respect:
- A candidate with NO floor is already through, and cannot be the target.
  Pick the candidate that has one.
- The material removed is the plug between the floor and the far surface —
  usually a large, obvious volume. If the removal you are planning is a thin
  sliver, or reaches beyond the feature's own profile into the outer walls of
  the part, the target or the direction is wrong.

WHEN THE CUSTOMER POINTS, ENUMERATE BEFORE YOU PICK. Instructions often name
a feature deictically or with a bare singular — "that vertical slot", "the
hole", "this pocket" — while the part carries several candidates. Do not take
the first family that matches the word. In the sub-goal's `rationale`, name
the candidates the index offers with their coordinates, then choose using the
instruction's own stated purpose and say why: "to decrease
weight" favours the candidate whose edit removes real material; "cut through"
requires one that is currently blind; a described location or the renders
settle the rest. Picking the wrong instance is unrecoverable — every attempt,
every replan and every QA verdict then argues about geometry the customer
never meant.

DIRECTIONS COME FROM MEASURED AXES, NEVER FROM HABIT. The index prints an
`axis` for every cylindrical family and a `normal` for every planar face —
those are the only legitimate sources of direction. When a sub-goal anchors
new or modified geometry to an existing feature (a bore, boss, slot, shaft),
its direction MUST be that feature's measured axis, quoted from the index; a
bare world-axis literal (+X/+Y/+Z) is acceptable only when no anchor feature
exists. Check the bbox extents before writing "rotate about Z" or "the stack
direction" — on an assembly the stack axis is usually the one the part is
THINNEST along, and a copy rotated about any other axis swings out of the
assembly plane. Orientation words in the instruction — "top", "front", "up" — name
the part's FUNCTIONAL frame, which routinely differs from the model's
coordinate frame (parts are often modelled lying down). Before using such a
word, declare the mapping from it to a world direction and state your
evidence (the dataset views, the mounting/base features, the bbox
proportions); never let "top" silently mean the largest max-Z face.

EVERY CONSTRAINT MUST CITE ITS SOURCE. Each restriction you write into a
sub-goal — flush / no protrusion, a span, "through the whole thickness", a
symmetry, an extent — must be traceable to either the customer's own words
or a measured value in the index, and the sub-goal must say which. A
constraint with no source is an invention, and inventions are the most
expensive class of error here: the executor builds to them, QA verifies
against them, and the whole run stays self-consistent while being wrong. If
you catch yourself writing a restriction the customer never stated, delete
it or ground it in a measurement.

A NAMED FUNCTION DECIDES SHAPE AND REACH. When the instruction names a
component or a purpose — a mount, handle, support, fastener, "to hold X",
"to reach Y", "to reduce weight" — model the geometry that function
requires: its characteristic features (a fastener has a head and protrudes
past its hole; a handle has clearance; an arm's far end sits where the work
is), and material WHERE THE FUNCTION NEEDS IT, which is frequently outside
the current envelope. Collapsing a named component to the simplest primitive
that fits inside existing material discards exactly the geometry that
distinguishes the edit — the score is carried by the material that enters or
leaves the envelope, not by filling internal voids. State the mount point,
the direction, and the far-end coordinate, and sanity-check that the far end
lands where the stated function needs it, never in free space.
Three tests every projecting or engaging body must pass before you write it
into a sub-goal:
- THE FAR END ACTS ON SOMETHING. Name, in the rationale, the thing the far
  end reaches, holds, presses on or covers, and its measured coordinate. A
  body whose far end hangs in empty air acting on nothing has the wrong
  direction — reconsider before blaming anything else.
- A DIMENSION MATCH IS AN ENGAGEMENT. When the body's cross-section matches
  an existing feature family to within tolerance — a bore of the same
  diameter, a slot of the same width — that match is evidence the body is
  meant to ENGAGE that feature: run it along the feature's measured axis,
  through it, rather than mounting it on a nearby face and pointing it
  along a face normal.
- THROUGH MEANS OUT BOTH SIDES. A body seated in or passing through a
  through-feature extends past BOTH mouths — a head, flange or bend at one
  end and a protruding tip past the other — unless the customer explicitly
  says flush. Material that stops exactly at a mouth reads as unfinished.
- ERR LONG, NOT SHORT. Measured across this benchmark, when we find the right
  region we typically move about a THIRD of the material the human moved: on
  8 of 24 scored tasks the volume ratio was under 0.70, median 0.35. A pin
  was built 7.2 mm long into a bore the human filled with a 31.1 mm pin —
  right axis, right diameter, perfectly contained, and it scored 0.428 while
  a single-shot script that ran slightly LONG scored 0.898. A feature fully
  contained inside its host is the single most common way a correct-looking
  edit loses most of its score, because the metric compares occupied space
  and containment contributes almost none. So when a dimension is not stated
  outright, size the feature so it reaches and passes the surface it must
  serve, and state that extent numerically in the sub-goal.
- A MOUNTING PHRASE NAMES THE ATTACHMENT, NOT THE DIRECTION. "Mounted
  from/at/on the top" (or the side, the front, a named face) fixes where
  the body ATTACHES; it says nothing about which way the body extends. The
  direction comes from the far-end test above — from what the body must
  reach to do its job. Extending straight away from the mount face, out
  into open space, is the default reading to be MOST suspicious of: it is
  what the geometry suggests and almost never what the function needs.
  When the direction is ambiguous, enumerate the plausible directions in
  the rationale with the far-end coordinate each one implies, and pick the
  one whose far end reaches the region the stated function names.

FREE PARAMETERS ARE SIZED FROM THE PURPOSE, NOT FROM WHAT IS NEARBY. When
the instruction leaves a quantity to your discretion ("amount can be chosen
by the editor", "some", "appropriate size"), derive it from the stated
purpose applied to the host region's measured extent: first state a numeric
budget — the percentage of the host body's volume to remove or add, the
fraction of a face to cover, the span a pattern must reach with uniform
pitch and sensible edge margins — then choose count/size/spacing to meet
that budget, and write the budget into the sub-goal so QA can check it.
Matching the size or spacing of pre-existing features is NOT by itself a
justification — those features usually serve a different function than the
one being requested. A discretionary edit that moves an order of magnitude
less material than its purpose implies is a timid near-no-op and scores
like one. For a PATTERN serving an area-wide purpose (lightening,
ventilation, drainage, grip), the span comes first: the pattern covers the
WHOLE host face, edge margin to edge margin, with uniform pitch — then the
count falls out of the span and pitch. Never pick a comfortable count and
center it as a compact patch: a purpose that applies to the whole face is
served only where the pattern actually reaches, and a cluster in the middle
of a large face leaves most of the purpose undone. Quote the host face's
measured extents from the index and write the outermost hole centers
NUMERICALLY, within roughly one pitch of the face's edges. And the pattern
drills through the host's THINNEST dimension — its wall thickness, read
from the host body's own measured extents — so every hole is short and
opens on the two LARGE faces. Holes that already exist in other bodies, or
that serve a different function, are not the axis authority for a new
pattern; the host's own geometry is.

GEOMETRIC TAGS — label every sub-goal with 1-2 tags from this list; the tag
routes the exact verified recipe for that operation to the coding agent, so a
wrong tag hands it the wrong playbook:
{tags_help}

For each sub-goal, list in `envelope` exactly the bounding-box faces that
sub-goal is allowed to move, as `+X`, `-X`, `+Y`, `-Y`, `+Z`, `-Z`. Use `[]`
when the outer envelope must not change at all — that is the common case for
holes, chamfers, fillets, pockets and internal features. An attempt that moves
a face you did not list is rejected automatically, so list every face you
genuinely intend to move, and no others.

Return JSON:
{{
  "understanding": "<one sentence: what the customer actually wants>",
  "subtasks": [
    {{"goal": "<imperative, specific, measurable>", "rationale": "<why here in the order>",
      "focus": ["<2-5 short terms naming the feature types, sizes and directions this sub-goal touches, e.g. \\"hole\\", \\"d=2.6\\", \\"top face\\", \\"fillet\\">"],
      "tags": ["<1-2 tags from the GEOMETRIC TAGS list>"],
      "envelope": ["<bbox faces this sub-goal may move: any of +X -X +Y -Y +Z -Z; [] if the outer size must not change>"]}}
  ]
}}

The `focus` terms select which sections of the geometry index the coding agent
will see for that sub-goal — name every feature kind it must locate."""


_BBOX_FACES = {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}

# At most two: a tag pins a whole recipe section, and three of them would spend
# the executor's entire recipe budget before keyword scoring sees any of it.
_MAX_TAGS = 2


def _tags(sub):
    """Canonical geometric tags for one sub-goal, unknown ones dropped.

    A tag decides which verified recipe the executor is shown, so an invented
    one must degrade to "no tag" (keyword scoring, as before) rather than route
    the wrong playbook. A bare string is accepted too — models write
    "tags": "add-body" often enough that iterating it as characters, and so
    losing the tag entirely, is not worth the strictness.
    """
    raw = sub.get("tags") or []
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    return skl.normalize_tags(raw)[:_MAX_TAGS]


def _envelope(sub):
    """Declared bbox faces for one sub-goal, or None for "not declared".

    The router rejects an attempt that moves an undeclared face, so a parsing
    slip here would burn a real attempt. Two guards: an absent key stays None
    (gate off), and a non-empty declaration that parses to nothing — the model
    wrote prose like ["the bottom"] — is treated as undeclared rather than as
    "no face may move", which would reject every attempt. A genuine `[]` is
    kept: that is the common and meaningful case.
    """
    if "envelope" not in sub:
        return None
    raw = sub.get("envelope")
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.split()
    faces = [f for f in (str(t).strip().upper().replace(" ", "") for t in raw)
             if f in _BBOX_FACES]
    if raw and not faces:
        return None
    return sorted(set(faces))


def _legend_text(plan):
    """The colour legend as fixed-width text lines, one per feature family.

    The legend travels as TEXT next to the images rather than being drawn on
    them: painted labels fight the geometry for pixels, while a table can carry
    the full face_idx list the sub-goals must cite.
    """
    # Every row comes from the plan itself — including the muted "other faces"
    # shades (or, for a pre-adjacency inspection, the gray sentinel), so the
    # legend never claims a gray remainder the render does not have.
    return "\n".join(
        f"  {str(p.get('name', '?')):<8} {p.get('label', '')}  {p.get('tags', '')}"
        for p in plan)


REPLAN_TEMPLATE = """\
You planned the sub-goal below, and EVERY executed attempt at it was rejected.
Nothing was kept: the part is unchanged from the state the sub-goal started
from. You are re-planning THIS ONE sub-goal, not the whole job.

CUSTOMER INSTRUCTION (unchanged, for context):
{instruction}

WHICH WORLD AXIS EACH [INPUT · *] VIEW LOOKS ALONG — this renderer does NOT use
the usual CAD convention, and a replan that re-anchors the target to a
different face must name it in these world coordinates:
{view_axes}
If the attempts below failed because the edit landed on the wrong FACE rather
than because it was built wrong, re-check the orientation here before moving
the anchor: measured on a coffee machine, two successive replans each moved
the anchor to a different wrong face and the run scored 0.0.

THE SUB-GOAL WHOSE STRATEGY FAILED:
{goal}

WHAT EACH ATTEMPT DID AND WHY IT WAS REJECTED (the executor's own prints are
inside the no-op causes — read them, they usually name the killer):
{digest}

SUB-GOALS STILL PENDING AFTER THIS ONE (not yet attempted — you MAY revise
these if your new strategy changes what remains to be done; work already
accepted before this sub-goal is untouchable and is not listed):
{pending}

{geometry}

Write a NEW strategy for the same customer outcome. Rules:
- Do NOT restate the failed approach in softer words. If every attempt died in
  the same operation — e.g. `BRep_API: command not done` repeating means the
  OCC kernel REFUSES fillet/chamfer on those edges at any size — the new goal
  must PRESCRIBE a different construction (build the blend as an explicit
  boolean: cut/union a rounded or conical tool solid along the measured edge),
  never ask for the same kernel call again.
- If attempts died selecting nothing, the new goal must name the target by
  measured values from the index above — radius, position, adjacent surface
  types — not by the entity indices the failed goal used.
- RE-EXAMINE THE PREMISES BEFORE THE METHOD. The attempts inherited every
  identification your failed goal made — which instance of a repeated
  feature family, which direction, which face. If the digest shows attempts
  landing in the wrong region, cutting bodies the instruction never named,
  or repeatedly producing the same rejected geometry, the strategy is not
  the problem — the ANCHOR is. The new goal must then target a DIFFERENT
  candidate (enumerate the other instances of the family from the index,
  with coordinates, and say why the new pick fits the instruction better);
  re-detailing the same instance with a new method wastes the round.
- The customer's dimensions are NOT negotiable: never shrink a named radius or
  thickness to appease the kernel.
- Stay an EDIT of the imported part; rebuilding from scratch scores zero.
- THE PROVENANCE RULE STILL APPLIES. A replan is the moment invented
  constraints creep in: every restriction, extent and demanded piece of
  evidence in the new goal must still trace to the customer's words or a
  measured index value, cited inline. Do not harden a new acceptance
  criterion ("must span the full height", "openings must be visible on
  plane P") that the instruction does not imply — the QA agent will enforce
  whatever you write, and an invented criterion that contradicts the
  customer's own wording condemns every future attempt, however correct.

GEOMETRIC TAGS — label the new sub-goal with 1-2 tags from this list:
{tags_help}

Return JSON:
{{
  "goal": "<the revised sub-goal, self-contained, with the concrete method>",
  "rationale": "<why this strategy avoids exactly what killed the last one>",
  "focus": ["<up to 6 feature terms for index focusing>"],
  "tags": ["<1-2 tags>"],
  "envelope": ["<bbox faces this may move: +X -X +Y -Y +Z -Z; [] if the outer size must not change>"],
  "next_subgoals": [<OPTIONAL: replacements for the pending sub-goals listed
    above, same shape as this object minus next_subgoals. OMIT the key to keep
    them exactly as they are; [] deletes them; you cannot touch accepted work.>]
}}"""


def replan(state, sub, digest, usage):
    """A fresh strategy for one fully-rejected sub-goal, or {} on failure.

    Text-only on purpose: the failure digest and the geometry index carry the
    evidence, and the renders were already judged (and rejected) by QA — what
    changed since the original plan is knowledge of HOW the attempts died, not
    how the part looks.
    """
    llm = LLM(config.MODEL_STRATEGIST, usage, role="strategist-replan")
    insp = state.__dict__.get("inspection")
    pending = [t for t in state.subtasks if t.idx > sub.idx]
    prompt = REPLAN_TEMPLATE.format(
        instruction=state.instruction,
        view_axes=render.view_axis_table(),
        goal=sub.goal_original or sub.goal,
        digest=digest,
        pending="\n".join(f"  {t.idx}. {t.goal}" for t in pending)
                or "  (none — this is the last sub-goal)",
        geometry=geo.to_prompt(insp) if insp else state.geometry_text,
        tags_help=skl.tags_help(),
    )
    out = llm.json(SYSTEM, [text_part(prompt)])
    if not (out.get("goal") or "").strip():
        return {}
    new = {
        "goal": out["goal"].strip(),
        "rationale": out.get("rationale", ""),
        "focus": [str(t) for t in (out.get("focus") or [])][:6],
        "tags": _tags(out),
        "envelope": _envelope(out),
    }
    # Revised PENDING sub-goals, parsed with the same guards as plan(). Only
    # present when the key was sent: absent means "keep them", [] means
    # "delete them" — the router applies this strictly AFTER the failed
    # sub-goal, so accepted work can never be rewritten from here.
    if isinstance(out.get("next_subgoals"), list):
        new["next_subgoals"] = [
            {"goal": (n.get("goal") or "").strip(),
             "rationale": n.get("rationale", ""),
             "focus": [str(t) for t in (n.get("focus") or [])][:6],
             "tags": _tags(n),
             "envelope": _envelope(n)}
            for n in out["next_subgoals"]
            if isinstance(n, dict) and (n.get("goal") or "").strip()
        ]
    return new


def plan(state, usage):
    llm = LLM(config.MODEL_STRATEGIST, usage, role="strategist")
    prompt = TEMPLATE.format(
        instruction=state.instruction,
        view_axes=render.view_axis_table(),
        geometry=state.geometry_text,
        tool_doc=geo.TOOL_DOC,
        max_subtasks=config.MAX_SUBTASKS,
        tags_help=skl.tags_help(),
    )

    parts = [text_part(prompt)]
    # The input part, as the human sees it — the full view set, each labelled.
    # Three views was a false economy: this agent decides which side material
    # goes on and which angular sector a copy lands in, and it was doing that
    # from the iso plus two orthos while QA got six. It plans once per run, so
    # the extra images are paid once.
    #
    # When the feature-coloured set is available it REPLACES the natural one
    # here: this agent's job is to name the right family, and a colour plus a
    # face_idx table is a far less ambiguous handle on "which cylinders" than a
    # gray render. QA still gets the natural views — see the router.
    views = state.__dict__.get("input_views", {})
    tagged = state.__dict__.get("input_views_tagged") or {}
    color_plan = state.__dict__.get("input_color_plan") or []
    coloured = bool(tagged and color_plan)
    if coloured:
        views = tagged
    shown = [v for v in config.ALL_VIEWS if views.get(v)]
    if shown:
        if coloured:
            parts.append(text_part(
                "THE INPUT PART, FEATURE-COLOR-CODED — each color is one "
                "feature family; the table maps colors to the index's unique "
                "tags:\n" + _legend_text(color_plan)
                + "\nViews in this order: " + ", ".join(shown)))
        else:
            parts.append(text_part(
                "THE INPUT PART — views in this order: " + ", ".join(shown)))
        for v in shown:
            parts.append(text_part(f"[INPUT · {v}]"))
            parts.append(image_part(views[v]))

    # The dataset's own renders, original colors and materials. Attached AFTER
    # the working views so the model reads them as reference: this is where
    # "the black lever" and "the front" resolve. Paid once per run.
    dataset = state.__dict__.get("dataset_views") or {}
    ds_shown = [v for v in config.ALL_VIEWS if dataset.get(v)]
    if ds_shown:
        parts.append(text_part(
            "DATASET RENDERS — the SAME part with its ORIGINAL colors and "
            "materials (the views above are synthetic). Resolve the "
            "instruction's color words and view words here; the view names "
            "are the customer's own frame: " + ", ".join(ds_shown)))
        for v in ds_shown:
            try:
                img = image_part(dataset[v])
            except OSError:
                continue
            parts.append(text_part(f"[DATASET · {v}]"))
            parts.append(img)

    out = llm.json(SYSTEM, parts)
    subs = out.get("subtasks") or []
    if not subs:  # never leave the router with nothing to do
        subs = [{"goal": state.instruction, "rationale": "fallback: unsplit instruction"}]

    state.plan_summary = out.get("understanding", "")
    state.subtasks = [
        SubTask(idx=i, goal=s.get("goal", ""), rationale=s.get("rationale", ""),
                focus=[str(t) for t in (s.get("focus") or [])][:6],
                tags=_tags(s),
                envelope=_envelope(s))
        for i, s in enumerate(subs[:config.MAX_SUBTASKS])
    ]
    state.log("plan", f"{len(state.subtasks)} sub-goals",
              understanding=state.plan_summary,
              goals=[s.goal for s in state.subtasks],
              tags=[s.tags for s in state.subtasks],
              envelopes=[s.envelope for s in state.subtasks])
    return state.subtasks
