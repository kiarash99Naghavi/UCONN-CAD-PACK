"""Deterministic cue extraction from instruction text — topics, sizes, directions.

This module parses words, not geometry. `extract_cues` turns an instruction, a
sub-goal and the strategist's focus terms into three stable lists: which
feature TOPICS are mentioned (holes, rounds, slots, ...), which SIZES are
quoted and what each most likely measures, and which DIRECTIONS are named. It
is regex-only, costs zero tokens, and touches no B-rep.

Its ONLY consumer is `tools/skillref.py`, the sole importer of this module,
which scores those cues against the cadquery-editor skill's recipe sections to
decide WHICH RECIPE SECTIONS to attach to the executor's prompt. That is the
entire sanctioned use: choosing text to add to a prompt.

WHY THE GEOMETRY-SLICING HALF WAS REMOVED. This module used to also export
`focused_text()`, which rendered only the index sections the extracted cues
pointed at and dropped the rest. That is a filter on what the model can SEE,
and it is unsafe in a way no amount of tuning fixes: the executor's contract
tells it to select by measured numbers from the geometry index, so a sliced
index can omit the very family the sub-goal needs while still demanding a
measured selection. The agent cannot distinguish a feature that is ABSENT from
the part from one that is merely HIDDEN from its prompt — so it invents plausible
numbers, the selector matches nothing, and the attempt is a clean no-op that
looks like a modelling mistake rather than a retrieval one.

The rule that replaced it: retrieval and filtering may decide only which recipe
to attach. They may never decide what geometry an agent is allowed to see. The
executor is always handed the complete index (`geometry.to_prompt`).
"""

import re

# ---------------------------------------------------------------------------
# Cue extraction
# ---------------------------------------------------------------------------

# topic -> word stems (matched case-insensitively on word boundaries; a
# trailing '*' in the stem allows suffixes: fillet* matches fillets/filleted)
_TOPIC_WORDS = {
    "holes":     ["hole*", "bore*", "drill*", "counterbore*", "countersink*",
                  "tap", "taps", "tapped", "thread*", "screw*", "port*",
                  "opening*", "outlet*", "inlet*", "vent*"],
    "cylinders": ["pin", "pins", "boss*", "shaft*", "rod", "rods", "stud*",
                  "cylinder*", "cylindrical", "bearing*", "peg", "pegs",
                  "button*", "knob*", "plug*"],
    "rounds":    ["fillet*", "round*", "radius", "radii", "blend*"],
    "chamfers":  ["chamfer*", "bevel*"],
    "edges":     ["edge", "edges"],
    "planes":    ["face", "faces", "surface*", "wall", "walls", "panel*",
                  "flat", "side", "sides", "profile*", "flange*"],
    "vertical":  ["vertical", "draft", "drafts"],
    "slots":     ["slot*", "groove*", "pocket*", "cutout*", "cut-out*",
                  "recess*", "notch*", "keyway*"],
    "ribs":      ["rib", "ribs", "gusset*", "support*"],
    "global":    ["scale*", "resize*", "taller", "shorter", "wider", "narrower",
                  "height", "width", "length", "thick*", "mirror*", "symmetric*",
                  "symmetry", "prolong*", "extend*", "shrink*", "enlarge*",
                  "bigger", "smaller", "weight"],
}

_DIRECTIONS = ["top", "bottom", "upper", "lower", "front", "back", "left",
               "right", "center", "central", "vertical", "horizontal"]

# 0.2 mm / 0,2mm / 1.7 millimetre / 0,5cm  (the dataset uses comma decimals)
_NUM_UNIT = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(mm|millimet(?:re|er)s?|cm|centimet(?:re|er)s?)\b",
    re.IGNORECASE)
# R=0,2  r = 1.5  d=3  D = 2.6  (unit often omitted in this shorthand)
_SHORTHAND = re.compile(r"\b([rd])\s*=\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)

# 120 degrees / 45deg / 2.5°  — an ANGLE, not a length.
#
# Angles used to parse as nothing at all, and that silence had a cost. A
# sub-goal reading "rotate the copy 120 degrees about Z" produced no topic, no
# size and no direction, so `skillref` concluded there was nothing to localize
# and withheld the find-the-feature playbook (§2) — on precisely the task that
# cannot be done without first measuring where the existing instances sit.
# Measured: that sub-goal was handed the add-a-body playbook instead.
_ANGLE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:°|deg\b|degs\b|degrees?\b)",
                    re.IGNORECASE)


def compile_stems(stems):
    """One case-insensitive word-boundary pattern from a list of word stems.

    A trailing '*' allows suffixes: fillet* matches fillets/filleted. Shared
    with `skillref`, which scores recipe sections off the same style of table.
    """
    alts = [s[:-1] + r"\w*" if s.endswith("*") else s for s in stems]
    return re.compile(r"\b(?:%s)\b" % "|".join(alts), re.IGNORECASE)


def _topic_patterns():
    return {topic: compile_stems(stems) for topic, stems in _TOPIC_WORDS.items()}


_TOPIC_RE = _topic_patterns()


def _to_mm(value, unit):
    v = float(value.replace(",", "."))
    return v * 10.0 if unit and unit.lower().startswith("c") else v


def _classify_size(text, start, end):
    """What a mentioned number most likely measures, from ±30 chars around it.

    Blend words win over radius words: "a 0.1 mm fillet" sizes NEW geometry,
    and must not be read as a reference to an existing r=0.1 family.
    """
    ctx = text[max(0, start - 30):min(len(text), end + 30)].lower()
    if "chamfer" in ctx or "bevel" in ctx:
        return "chamfer"
    if "fillet" in ctx or "round" in ctx:
        return "fillet"
    if re.search(r"\bdia|diamet|\bd\s*=", ctx):
        return "diameter"
    if re.search(r"\bradi|\br\s*=", ctx):
        return "radius"
    if re.search(r"thick|wall", ctx):
        return "thickness"
    return "length"


def extract_cues(text):
    """Deterministic parse of instruction/goal text into geometric cues.

    Returns {"topics": [..], "sizes": [(kind, value)], "directions": [..]}.
    Every size is in mm except kind "angle", which is in degrees — the one
    entry that is not a length, kept in the same list because its only job is
    the same as the others': evidence that this sub-goal has something
    concrete to find before it can change it.
    Order of topics follows _TOPIC_WORDS declaration order (stable output).
    """
    topics = [t for t, pat in _TOPIC_RE.items() if pat.search(text)]
    sizes = []
    for m in _NUM_UNIT.finditer(text):
        sizes.append((_classify_size(text, m.start(), m.end()),
                      round(_to_mm(m.group(1), m.group(2)), 4)))
    for m in _SHORTHAND.finditer(text):
        kind = "radius" if m.group(1).lower() == "r" else "diameter"
        mm = round(_to_mm(m.group(2), None), 4)
        if (kind, mm) not in sizes:
            sizes.append((kind, mm))
    for m in _ANGLE.finditer(text):
        deg = round(float(m.group(1).replace(",", ".")), 4)
        if ("angle", deg) not in sizes:
            sizes.append(("angle", deg))
    directions = [d for d in _DIRECTIONS
                  if re.search(r"\b%s\b" % d, text, re.IGNORECASE)]
    return {"topics": topics, "sizes": sizes, "directions": directions}


# ---------------------------------------------------------------------------
# Demo / manual check — cues only; this module no longer reads geometry:
#   PYTHONPATH=$(pwd):$(pwd)/../.. uv run python -m src.tools.focus \
#       "Add 0.2 mm chamfer to the hole edges"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    text = " ".join(sys.argv[1:])
    if not text:
        raise SystemExit('usage: focus "<instruction text>"')
    print(extract_cues(text))
