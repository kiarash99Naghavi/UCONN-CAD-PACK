"""Skill retrieval — the right recipe, not the whole cookbook.

`Skills/reference/recipes_edit.md` is the cadquery-editor skill's edit-first
reference: fourteen sections, the core ones executed against real benchmark
STEP files before being written down. It is exactly what the executor is
missing — CadQuery has several similarly-named methods for each idea, and
inventing a plausible one is the most common way an attempt dies.

It is also ~11k tokens, and the executor is called up to
MAX_SUBTASKS x MAX_ATTEMPTS_PER_SUBTASK times per run, so pasting the file
wholesale would cost more than everything else in the run combined. This module
picks the one or two sections a given sub-goal actually needs, deterministically
and for zero tokens.

Selection is a priority ladder, strongest signal first, and a rung is only ever
taken whole. The strategist labels each sub-goal with 1-2 canonical
GEOMETRIC TAGS (`TAG_SECTIONS` below); a tag names an operation family and pins
its section outright, so the step that decided what the edit is also decides
which playbook teaches it. Next comes §2, the localization toolkit, whenever
the sub-goal names something that has to be found first. Whatever budget is
left over is then filled by regex keyword scoring over the instruction,
sub-goal and focus terms, using the same cue vocabulary `focus` uses on the
geometry index. Untagged sub-goals — old sessions, a model that skipped the
field — fall through to keyword scoring alone, exactly as before tags existed.

Failure-safe by construction, exactly like `focus`: a missing file, an
unparsable file, or a sub-goal that matches nothing all yield an empty string,
and the executor then renders the prompt it renders today.
"""

import os.path as osp
import re

from .. import config
from . import focus as foc

# --- canonical tags ---------------------------------------------------------

# Canonical geometric tags the strategist may attach to a sub-goal. Each tag
# names an operation family and pins the recipe section(s) that teach it, so
# tag-based selection is deterministic where keyword scoring is fuzzy.
TAG_SECTIONS = {
    "add-body":      [3],
    "fillet-chamfer": [4],
    "mirror-pattern": [5],
    "cut-hole-slot": [6],
    "resize-feature": [7],
    "profile-swap":  [8],
    "remove-blend":  [9],
    "scale-transform": [10],
    "hollow-shell":  [10],
    "text-emboss":   [11],
    "draft-angle":   [12],
    "move-delete":   [13],
    "thread-area":   [14],
}

# One line per tag, for embedding in the strategist prompt. Phrased as what the
# sub-goal DOES, not as the section title, because the strategist is choosing
# from the instruction's wording and never sees the recipes themselves.
_TAG_HELP = {
    "add-body":       "add a new solid (pin, screw, boss, rib, flange, lug, "
                      "cover, bracket) anchored to existing geometry",
    "fillet-chamfer": "round or bevel existing edges — add a fillet radius or "
                      "a chamfer",
    "mirror-pattern": "duplicate existing geometry — mirror it, or repeat it "
                      "as a circular/polar or linear pattern, including "
                      "rotating a copy into a gap",
    "cut-hole-slot":  "remove material — a hole, bore, slot, pocket, groove or "
                      "through-cut, including enlarging an existing hole",
    "resize-feature": "change a dimension of a feature that is already there — "
                      "bigger, smaller, deeper, longer, thicker",
    "profile-swap":   "replace a feature's cross-section with a different one "
                      "(round to hex, square to round)",
    "remove-blend":   "remove or replace an existing fillet/chamfer — make a "
                      "blended edge sharp again",
    "scale-transform": "scale or transform the whole body or a sub-body",
    "hollow-shell":   "hollow the part out or shell it to a wall thickness",
    "text-emboss":    "emboss or engrave text or a label on a face",
    "draft-angle":    "apply a draft/taper angle to faces",
    "move-delete":    "move an existing feature to a new position, or delete "
                      "it and heal the face",
    "thread-area":    "edit a threaded region — threads, tapped holes, screw "
                      "features",
}


def tags_help():
    """The tag vocabulary as one line per tag, for prompt embedding."""
    return "\n".join(f"  {tag} — {_TAG_HELP.get(tag, '')}" for tag in TAG_SECTIONS)


def normalize_tags(tags):
    """Known tag names from raw model output, lowercased, de-duplicated.

    Unknown strings are dropped rather than raising: a tag is a routing hint,
    and a hallucinated one should cost nothing more than the keyword scoring
    the sub-goal would have got anyway.
    """
    out = []
    for t in tags or []:
        t = str(t).strip().lower()
        if t in TAG_SECTIONS and t not in out:
            out.append(t)
    return out


# --- section scoring --------------------------------------------------------

# focus's index topics -> the recipe sections that answer them. Sharing the
# vocabulary means the strategist's per-sub-goal `focus` terms steer the recipe
# choice and the geometry index together, from one parse.
_TOPIC_SECTIONS = {
    "holes":     [6],
    "cylinders": [3],
    "rounds":    [4],
    "chamfers":  [4],
    "edges":     [4],
    "planes":    [2],
    "vertical":  [2],
    "slots":     [6],
    "ribs":      [3],
    # focus's "global" bucket is deliberately broad — it holds scale, mirror,
    # bigger, height, width AND thickness — so it cannot pick a recipe on its
    # own. The operation words below separate §5 / §7 / §10 precisely; mapping
    # it to §10 here made every "0,5mm thickness" read as a hollowing job.
    "global":    [],
}

# Verbs and phrasings that name an *operation* rather than a feature, which is
# what the index-oriented topic table above cannot see. "Make the holes bigger"
# and "add a hole" share the topic `holes` but need different recipes.
_SECTION_WORDS = {
    3:  ["add", "adds", "added", "adding", "new", "attach*", "mount*",
         "duplicate*", "copy", "copies", "copied", "extra", "another",
         "additional", "place*", "insert*", "creat*", "build*", "grow*",
         "flange*", "boss*", "lug*", "tab", "tabs", "cover*", "bracket*"],
    5:  ["mirror*", "symmetric*", "symmetry", "pattern*", "array*", "polar",
         "circular", "evenly", "spaced", "rotate*", "rotation*", "degrees",
         "deg", "instances"],
    6:  ["cut*", "pocket*", "slot*", "groove*", "remove*", "drill*", "bore*",
         "hole*", "through", "thru", "clearance*", "weight", "lighter",
         "lighten*", "lightening"],
    7:  ["bigger", "larger", "smaller", "longer", "shorter", "taller", "deeper",
         "shallower", "wider", "narrower", "thick*", "thin*", "enlarge*",
         "reduce*", "increase*", "decrease*", "resize*", "diameter*", "depth"],
    8:  ["hexagon*", "hexagonal", "hex", "square", "triangular", "profile*",
         "cross-section*", "section*", "shape"],
    9:  ["defeature*", "sharp*", "unfillet*", "unround*", "unblend*"],
    # "wall thickness" belongs here; a bare "0,5mm thickness" is a dimension
    # (§7), so thickness words are NOT listed — `wall` is what makes it a
    # hollowing job.
    10: ["scale*", "scaling", "hollow*", "shell*", "wall", "walls",
         "offset*", "transform*"],
    11: ["text", "engrav*", "emboss*", "letter*", "label*", "font*",
         "inscri*", "write", "written"],
    12: ["draft", "drafts", "drafted", "taper*"],
    # \bmove\b does not match inside "remove" (word boundary), so §6's cut
    # vocabulary and this move/delete vocabulary stay separate.
    13: ["move", "moved", "moving", "reposition*", "relocat*", "shift*",
         "translate*", "delete*", "collision*", "interfer*"],
    14: ["thread*", "tap", "taps", "tapped", "tapping", "screw*"],
}

# §9 is "replace or REMOVE a blend" — only when removal is actually asked for,
# otherwise every fillet request would drag it in alongside §4.
_REMOVAL_NEAR_BLEND = re.compile(
    r"\b(remove|delete|replace|get rid of|without)\b[^.]{0,40}"
    r"\b(fillet|round|blend|chamfer)", re.IGNORECASE)

_SECTION_RE = {sid: foc.compile_stems(words) for sid, words in _SECTION_WORDS.items()}

# Applies to every edit: imports are usually multi-solid Compounds, and a
# result that silently drops the bodies it did not touch scores near zero while
# looking plausible in the renders. Cheap enough (~600 tokens) to always carry.
_ALWAYS = 1
# The localization toolkit (~1300 tokens after slimming), added whenever the
# sub-goal has to FIND something and the budget still allows — claimed ahead of
# the keyword-ranked fill, behind only §1 and the strategist's own tag.
_LOCALIZE = 2

_HEADER = """\
RECIPES FOR THIS KIND OF EDIT
(from the cadquery-editor skill, `reference/recipes_edit.md`. Snippets with a
"Verified on ..." footer were executed against real benchmark STEP files and
the measured effect recorded. Prefer these patterns over remembered API
shapes, but they are starting points, not scripts — ADAPT them: their
`path = "data/.../*.step"` lines are illustrative, your input is ALWAYS
args["input_file"], never write files, and DO print your measurements —
selection counts, volumes, bboxes — so a silent miss is visible.)

"""


# --- loading ----------------------------------------------------------------

_SECTION_HEAD = re.compile(r"^## (\d+)\.\s*(.+)$", re.MULTILINE)
_CACHE = {}


def load_sections(path=None):
    """{section_id: (title, body)} parsed from recipes_edit.md, cached.

    Returns {} for a missing or unparsable file — every caller treats that as
    "no recipes available" rather than an error, so a mis-set SKILLS_DIR can
    only ever cost the feature, never the run.
    """
    path = path or osp.join(config.SKILLS_DIR, "reference", "recipes_edit.md")
    path = osp.abspath(path)
    if path in _CACHE:
        return _CACHE[path]

    sections = {}
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        heads = list(_SECTION_HEAD.finditer(text))
        for i, m in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            body = text[m.start():end].strip()
            sections[int(m.group(1))] = (m.group(2).strip(), body)
    except OSError:
        sections = {}

    _CACHE[path] = sections
    return sections


def _tokens(s):
    """Rough token count. Only ever used to compare against a budget."""
    return len(s) // 4


# --- selection --------------------------------------------------------------

def score_sections(text, cues=None):
    """{section_id: score} for one blob of instruction / goal / focus text.

    Both signals count equally: a topic the geometry index also recognises, and
    an operation word the index has no concept of.
    """
    cues = cues or foc.extract_cues(text)
    scores = {}
    for topic in cues["topics"]:
        for sid in _TOPIC_SECTIONS.get(topic, []):
            scores[sid] = scores.get(sid, 0) + 1
    for sid, pat in _SECTION_RE.items():
        hits = len(set(m.group(0).lower() for m in pat.finditer(text)))
        if hits:
            scores[sid] = scores.get(sid, 0) + min(hits, 3)
    # "Remove the R2 fillet and leave it sharp" also reads as a blend request,
    # and §4 (add a blend) would otherwise outscore §9 and eat the budget. An
    # explicit removal is unambiguous, so it wins outright.
    if _REMOVAL_NEAR_BLEND.search(text):
        scores[9] = scores.get(9, 0) + 3
    elif 9 in scores:
        del scores[9]
    return scores


def select(text, emphasis="", budget=None, sections=None, tags=None):
    """Ordered section ids to show, within the token budget.

    Priority order, strongest signal first: §1 always, the strategist's tag
    sections (or, untagged, the single top-ranked section standing in for the
    tag it never got), §2 localization when the sub-goal has something to find,
    then the keyword-ranked fill.

    `tags` are the strategist's canonical geometric tags for this sub-goal.
    They are seated first, right after the always-on section, because the
    planner named the operation family explicitly and that beats guessing it
    back out of the prose, and nothing below may displace them. Unknown tags
    are ignored, and `tags=None` leaves behaviour identical to pure keyword
    selection.

    §2 sits above the ranked fill, not below it: a keyword runner-up is a guess
    made from the prose the tag already summarised, while §2 is how the
    executor finds the feature it was told to change. Ranked sections keep
    their greedy `continue`, so a smaller runner-up still gets the leftovers.

    `emphasis` (the sub-goal and its focus terms) is scored twice. The overall
    instruction has to be in the mix — it carries dimensions and context the
    sub-goal paraphrases away — but it also describes the OTHER sub-goals, and
    unweighted it wins: on "add a third blade ... and radii on all four long
    edges", sub-goal 0 (a rotate-copy) was being handed the blend ladder.
    """
    sections = load_sections() if sections is None else sections
    if not sections:
        return []
    budget = config.RECIPES_MAX_TOKENS if budget is None else budget

    cues = foc.extract_cues(" . ".join(t for t in (text, emphasis) if t))
    scores = score_sections(text)
    if emphasis:
        for sid, v in score_sections(emphasis).items():
            scores[sid] = scores.get(sid, 0) + 2 * v

    chosen, spent = [], _tokens(_HEADER)        # the framing header is not free
    if _ALWAYS in sections:                     # the multi-body return contract
        chosen.append(_ALWAYS)
        spent += _tokens(sections[_ALWAYS][1])

    ranked = sorted((sid for sid in scores if sid in sections and sid != _ALWAYS),
                    key=lambda sid: (-scores[sid], sid))

    # Tagged sections first, so the budget is spent on the operation the
    # strategist actually named before anything below can displace it.
    for tag in normalize_tags(tags):
        for sid in TAG_SECTIONS[tag]:
            if sid not in sections or sid in chosen:
                continue
            cost = _tokens(sections[sid][1])
            if spent + cost > budget:
                continue                        # a later, smaller one may fit
            chosen.append(sid)
            spent += cost

    # Untagged sub-goals have no named operation, so the top-ranked section is
    # the only thing playing that role and it is seated in the tag's place —
    # otherwise §2 takes the room first and the operation loses: measured on
    # "add a fillet to the top edges" with no tag, §4 (1912t) scores 4 to §3's
    # 1, yet ships §1+§2+§3 — the add-a-body playbook for a fillet request.
    if len(chosen) == (_ALWAYS in sections) and ranked:
        sid = ranked[0]
        cost = _tokens(sections[sid][1])
        if spent + cost <= budget:
            chosen.append(sid)
            spent += cost

    # Anything that has to be *found* before it can be changed benefits from the
    # localization toolkit, and it is claimed BEFORE the keyword-ranked fill:
    # a section the strategist did not ask for and only a noun matched is a
    # weaker bet than the playbook for finding the feature at all. Considered
    # last, §2 (1231t) was dropped exactly when the tag's own section was large
    # — measured: `fillet-chamfer` shipped §1+§4+§6 and lost §2, where §6
    # (cuts/holes, 827t) had been pulled only because the wording said "hole
    # edges" — the noun, not the operation.
    needs_localizing = bool(cues["topics"] or cues["sizes"] or cues["directions"])
    if (needs_localizing and _LOCALIZE in sections and _LOCALIZE not in chosen
            and spent + _tokens(sections[_LOCALIZE][1]) <= budget):
        chosen.append(_LOCALIZE)
        spent += _tokens(sections[_LOCALIZE][1])

    # Whatever is left goes to the keyword-ranked runners-up.
    for sid in (s for s in ranked if s not in chosen):
        cost = _tokens(sections[sid][1])
        if spent + cost > budget:
            continue                            # a later, smaller one may fit
        chosen.append(sid)
        spent += cost

    return sorted(chosen)


def recipes_for(instruction, *emphasis, budget=None, tags=None):
    """(prompt_block, [section_ids]) for a sub-goal.

    Takes the overall instruction, then the
    sub-goal and the strategist's focus terms, which weigh double because they
    describe THIS step rather than the whole job — plus the sub-goal's canonical
    `tags`, which pin their sections outright. ("", []) means "add nothing to
    the prompt": the skill is disabled, missing, or has nothing to say here.
    """
    if not config.USE_SKILL_RECIPES:
        return "", []

    sections = load_sections()
    chosen = select(instruction or "", " . ".join(t for t in emphasis if t),
                    budget, sections, tags=tags)
    if not chosen:
        return "", []

    body = "\n\n".join(sections[sid][1] for sid in chosen)
    return _HEADER + body, chosen


# ---------------------------------------------------------------------------
# Demo / manual check:
#   PYTHONPATH=$(pwd):$(pwd)/../.. uv run python -m src.tools.skillref \
#       "Add a flange 0,5mm thick on the flat bottom surface"
#   ... --tags add-body,fillet-chamfer "Add a flange ..."   # pin sections
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # usage: skillref [--tags a,b] "<instruction>" ["<sub-goal>" ["<focus>"]]
    argv = sys.argv[1:]
    tags = []
    if argv and argv[0].startswith("--tags"):
        raw = argv[0][7:] if "=" in argv[0] else (argv.pop(1) if len(argv) > 1 else "")
        argv.pop(0)
        tags = normalize_tags(raw.split(","))
    if not argv:
        raise SystemExit("usage: skillref [--tags a,b] \"<instruction>\" "
                         "[\"<sub-goal>\" [\"<focus terms>\"]]\n\n"
                         + tags_help())
    instruction, emphasis = argv[0], argv[1:]
    secs = load_sections()
    if not secs:
        raise SystemExit(f"no recipes found under {config.SKILLS_DIR}")

    whole = sum(_tokens(b) for _, b in secs.values())
    text, ids = recipes_for(instruction, *emphasis, tags=tags)
    blob = " . ".join([instruction] + emphasis)
    weighted = score_sections(instruction)
    for sid, v in score_sections(" . ".join(emphasis)).items():
        weighted[sid] = weighted.get(sid, 0) + 2 * v
    print(f"cues:   {foc.extract_cues(blob)}")
    print(f"tags:   {tags or '(none)'} -> pinned "
          f"{sorted({s for t in tags for s in TAG_SECTIONS[t]})}")
    print(f"scores: {dict(sorted(weighted.items()))}  (sub-goal terms weigh 2x)")
    for sid in ids:
        print(f"  -> E{sid}: {secs[sid][0]}  (~{_tokens(secs[sid][1])} tok)")
    print(f"\nselected ~{_tokens(text)} tok of ~{whole} tok whole file "
          f"({100 - 100 * _tokens(text) // max(whole, 1)}% saved)")
