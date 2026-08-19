"""Source-pattern lint for generated CadQuery scripts — run BEFORE execution.

The pipeline discovers a broken API call by running the script in a subprocess
and reading the traceback, which costs a whole attempt out of the sub-goal's
budget. Measured: the executor emitted `.hull()`, the run died with
`AttributeError: 'Workplane' object has no attribute 'hull'`, and an attempt was
spent learning something that was knowable from the source text alone. A regex
over the generated source catches that class of mistake for free and hands the
executor the same precise remedy it would have got from the crash.

WHAT BELONGS HERE — and what does not:

* Only CERTAIN failures. A pattern listed here rejects the script outright, so
  a false positive costs a real attempt exactly like the crash it was meant to
  prevent, except the model is now being told to fix code that was correct. A
  pattern that is *sometimes* legitimate must not be listed, no matter how often
  it is wrong. Every entry below either names an API that does not exist in this
  environment at all, or a call form whose result is always unusable.

* Only failures visible STATICALLY. Runtime traps — a chamfer the OCC kernel
  refuses, a selector that matches zero faces, a boolean that invalidates a
  solid — cannot be seen in the source and stay where they are, in
  `agents/executor.py` `_TRAPS`, keyed by error signature. This module is the
  same body of knowledge keyed by source pattern instead, so it can fire before
  the subprocess starts. The two tables overlap by design (`hull`, `normalAt`,
  the OCP `*Iterator` classes): whichever one sees the problem first pays less.
"""

import ast
import difflib
import importlib
import inspect
import re
import typing
from functools import lru_cache

# (compiled pattern, short name, remedy handed back to the executor).
PATTERNS = [
    (re.compile(r"\.hull\s*\("),
     "hull() does not exist",
     "`Workplane.hull()` does not exist in this CadQuery version, in any form, "
     "and neither does `cadquery.func.hull`. Build the slot / obround outline "
     "explicitly: `slot2D(length, width)` on a workplane, two circles joined by "
     "their common tangent lines, or a `moveTo`/`lineTo`/`radiusArc`/`close` "
     "sketch — then `extrude` / `cutBlind` / `cutThruAll` it."),

    # Two-argument normalAt only. `[^()]*` cannot cross a parenthesis, so a
    # single nested-paren argument — `normalAt(cq.Vector(x, y, z))`, which is
    # legal and returns a Vector — cannot match: the comma inside `Vector(...)`
    # is unreachable from outside its parens. That is the whole point of
    # excluding parens from the character class rather than using `.*`.
    (re.compile(r"\bnormalAt\s*\([^()]*,[^()]*\)"),
     "normalAt(u, v) returns a tuple",
     "`Face.normalAt(u, v)` with TWO arguments does not raise — it silently "
     "returns a (Vector, Vector) TUPLE instead of a Vector, so try/except "
     "cannot catch it and every later use of that value fails somewhere else, "
     "far from the real mistake. Call `f.normalAt()` with NO arguments."),

    # `TopoDS_Iterator` is the ONE Iterator class OCP really has (verified: it
    # constructs from a TopoDS_Shape and walks its direct children), so the
    # alternation below deliberately excludes it — `TopoDS_\w+Iterator` needs at
    # least one character between the underscore and `Iterator`, which
    # `TopoDS_Iterator` does not have. Rejecting a working class would cost a
    # repair round-trip on correct code, which is exactly what this table
    # forbids. Everything else in the family is absent: TopTools has no
    # Iterator at all, and TopExp/TopAbs expose only `TopExp_Explorer`.
    (re.compile(r"\b(?:TopTools|TopExp|TopAbs)\w*Iterator\w*\b"
                r"|\bTopoDS_\w+Iterator\w*\b"),
     "OCP has no *Iterator classes",
     "That class DOES NOT EXIST — the only iterator OCP has is "
     "`TopoDS_Iterator`; there is no `TopTools_*Iterator` at all. OCP lists are "
     "directly Python-iterable, and static methods take an `_s` suffix "
     "(`TopExp.MapShapesAndAncestors_s`). You do not need any of it: iterate "
     "`face.Edges()` / `shape.Faces()` at the CadQuery level and de-duplicate "
     "on `e.hashCode()`."),

    (re.compile(r"\b(?:cq|cadquery)\.func\."),
     "cadquery.func does not exist here",
     "There is no `cadquery.func` module in this environment — importing from "
     "it or calling through it is an immediate AttributeError. Use the "
     "`Workplane` and `Shape` methods instead."),

    (re.compile(r"\bexporters\.export\s*\(|\.save\s*\(|\bcq\.vis\b"
                r"|from\s+cadquery\.vis\s+import|\bshow_object\s*\("),
     "no file writes or viewers",
     "Do not write files and do not open a viewer. The function must RETURN "
     "the shape — the harness does all the exporting and rendering, and a "
     "script that exports or calls `show_object` either crashes headless or "
     "returns None and is scored as having produced nothing."),

    # The entries below were harvested from the crash census across 89 runs:
    # AttributeError and TypeError on APIs that do not exist here accounted for
    # 59% of all crashed attempts. Each was checked against the installed
    # cadquery/OCP before being listed — every one is absent, never merely
    # unusual, which is what this table requires.
    (re.compile(r"\.BoundingBox\s*\(\s*\)\s*\.\s*(?:toTuple|min|max)\b"),
     "BoundBox has no toTuple/min/max",
     "`BoundBox` exposes `.xmin/.ymin/.zmin/.xmax/.ymax/.zmax` (and `.xlen`, "
     "`.center`) — there is no `.toTuple()`, `.min` or `.max`. Read the six "
     "scalars directly: `bb = shape.BoundingBox(); lo = (bb.xmin, bb.ymin, "
     "bb.zmin)`."),

    (re.compile(r"\bTopoDS_\w+\s*\([^()]*\)\s*\.\s*wrapped\b"),
     "raw TopoDS shapes have no .wrapped",
     "`.wrapped` is the CadQuery wrapper's handle on the raw OCP shape, so it "
     "exists on `cq.Face`/`cq.Solid`, never on a raw `TopoDS_*`. You are "
     "already holding the OCP object — drop the `.wrapped`. To go the other "
     "way, wrap it: `cq.Shape.cast(topods_shape)`."),

    (re.compile(r"\.Faces\s*\(\s*\)\s*\[[^\]]*\]\s*\.\s*extrude\s*\("),
     "Face has no extrude()",
     "`extrude` is a `Workplane` operation, not a `Face` method. Build the "
     "prism explicitly instead: `cq.Solid.extrudeLinear(face, cq.Vector(dx, "
     "dy, dz))`, or sketch the profile on a workplane and extrude that."),

    (re.compile(r"\bWorkplane\b[^\n]*\.isValid\s*\(|\bwp\s*\.\s*isValid\s*\("),
     "isValid() is a Shape method",
     "`isValid()` lives on `Shape`, not on `Workplane`. Call it on the solid: "
     "`out.val().isValid()` (or `shape.isValid()` when you already hold one)."),

    (re.compile(r"\bTopExp\s*\.\s*MapShapes(?:AndAncestors|AndUniqueAncestors)?"
                r"\s*\((?!\s*\))"),
     "OCP static methods need the _s suffix",
     "In this OCP binding the static methods are `TopExp.MapShapes_s`, "
     "`TopExp.MapShapesAndAncestors_s`, `TopExp.MapShapesAndUniqueAncestors_s` "
     "— without `_s` the attribute does not exist. Simpler still, stay at the "
     "CadQuery level: `shape.Faces()`, `face.Edges()`."),

    (re.compile(r"\.common\s*\("),
     "boolean intersection is intersect(), not common()",
     "`common()` does not exist on `Workplane`, `Shape`, `Solid`, `Compound` "
     "or `Face` in this CadQuery version — the boolean intersection is "
     "`a.intersect(b)`. (`common` is the OCC-level name; CadQuery renames it.)"),

    (re.compile(r"\.text\s*\([^()]*\bcut\s*="),
     "Workplane.text has no cut= argument",
     "`Workplane.text()` in this version takes no `cut=` keyword. Produce the "
     "text as its own solid and boolean it yourself: `txt = "
     "wp.text(...).val()` then `base.cut(txt)` to engrave or `base.union(txt)` "
     "to emboss."),

    # The largest AttributeError family in the second census: 8 crashed
    # attempts across 5 sessions, reported as either "'TopoDS_Solid' object has
    # no attribute 'wrapped'" (6) or "'Workplane' object has no attribute
    # 'wrapped'" (2). Both are the same bug — makeCompound reads `.wrapped` off
    # every element itself, so an element that is ALREADY a raw TopoDS_* (or is
    # a Workplane, which has no `.wrapped` at all) dies inside the call.
    # Verified against the install: makeCompound([s.wrapped, t.wrapped]) raises,
    # makeCompound([s, t]) returns a Compound.
    #
    # `[^()]*` cannot cross a parenthesis, so the legitimate
    # `makeCompound([cq.Shape.cast(f.wrapped) for f in ...])` — where the
    # `.wrapped` belongs to an INNER call and the list element is a proper
    # Shape — is unreachable from outside `cast(`, and neither is the
    # `[_wrapped(p) for p in parts]` helper-call form. Only a `.wrapped` that is
    # itself a list element can match, and that one always raises. Note that a
    # `hasattr(c, 'wrapped')` guard does NOT make it safe: on a CadQuery Shape
    # the guard passes and hands makeCompound the raw TopoDS anyway (measured).
    (re.compile(r"\bmakeCompound\s*\(\s*\[?[^()]*\.wrapped\b"),
     "makeCompound() takes CadQuery Shapes, not .wrapped",
     "`cq.Compound.makeCompound(...)` reads `.wrapped` off each element "
     "ITSELF, so every element must be a CADQUERY object (`cq.Solid`, "
     "`cq.Face`, `cq.Shape`) — handing it raw `TopoDS_*` objects raises "
     "`'TopoDS_Solid' object has no attribute 'wrapped'`, and handing it "
     "`Workplane`s raises the same thing about `Workplane`. Drop the "
     "`.wrapped`: `cq.Compound.makeCompound([s for s in shape.Solids()] + "
     "[edited])`. If you are genuinely holding raw OCP shapes, wrap them first "
     "with `cq.Shape.cast(topods_shape)`; if you are holding Workplanes, take "
     "`.val()`."),

    # 3 crashed attempts across 2 sessions, all the same unguarded top-of-
    # function import. Verified: OCP.TopoDS exports the CLASS `TopoDS` (with
    # `Face_s`/`Solid_s`/... statics) and the `TopoDS_*` shape types — there is
    # no lowercase `topods` module or object, so the import is a hard
    # ImportError before a single line of geometry runs. `\b` on both sides
    # keeps this off `TopoDS` (different case) and off identifiers like
    # `topods_shape`.
    (re.compile(r"\btopods\b"),
     "OCP has no lowercase `topods`",
     "`from OCP.TopoDS import topods` is an ImportError — there is no "
     "lowercase `topods` in this OCP. The downcasts live on the `TopoDS` CLASS "
     "with an `_s` suffix: `from OCP.TopoDS import TopoDS` then "
     "`TopoDS.Face_s(shp)` / `TopoDS.Solid_s(shp)`. Easier still, stay in "
     "CadQuery: `cq.Shape.cast(topods_shape)` gives you a `cq.Face`/`cq.Solid` "
     "directly, and `shape.Faces()` / `face.Edges()` avoid the downcast "
     "entirely."),
]


# --------------------------------------------------------------------------
# generic attribute check — introspection instead of one regex per typo
# --------------------------------------------------------------------------
#
# Every PATTERNS entry above is one hand-written regex for one hallucinated
# name, harvested after that name had already cost a run. The failure mode is
# structural, not a list of special cases: both libraries here carry a CLOSED,
# fully enumerable set of attributes, and the executor guesses names out of the
# C++ docs, another binding's spelling, or a CadQuery version that never
# existed. Two measured examples, one per library:
#
#   * task 26 (B7A2N74ZJBF9MZHU_1770171700.697783) called
#     `BRepAlgoAPI_Defeaturing.AddFace`; the real name here is
#     `AddFaceToRemove`. The script's own try/except swallowed the
#     AttributeError into a no-op and the wasted attempt flipped the rest of
#     the sub-goal into LAST_RESORT mode.
#   * task 17 (4S7JQK6ZQMAD25GL_1758863286) called `wp.roundedRect(...)`, which
#     does not exist on `Workplane` in any CadQuery version. That crashed
#     attempt 1 outright, leaving the sub-goal two attempts to do three
#     attempts' work.
#
# Asking the INSTALLED library whether the attribute exists generalises the
# whole table: any misspelling on any covered class is caught before the
# subprocess starts, with the real name offered back. It is sound in the
# direction that matters — a name is reported ONLY when the class was resolved
# by import and the attribute is genuinely absent. Anything unresolvable,
# dynamic, or ambiguous is silently skipped, because a false positive costs a
# repair round-trip on correct code, which this module forbids.
#
# On scope: an earlier version of this check covered OCP only, on the belief
# that CadQuery's plugin mechanism made `dir()` unreliable there. That belief
# was wrong and worth recording, because it excluded the LARGER of the two
# failure families — the executor's hand-written blocklist in the prompt is
# almost entirely CadQuery names, not OCP ones. Verified against the install:
# `Workplane`, `Sketch`, `Shape`, `Solid`, `Face`, `Edge`, `Wire`, `Vector` and
# `Compound` define neither `__getattr__` nor a custom `__getattribute__`, so
# `hasattr` is the whole truth for them. `Assembly` DOES define `__getattr__`
# and is therefore excluded by name below.
_LIB_ROOTS = ("OCP", "cadquery")

# Excluded despite being a CadQuery class: it resolves attributes dynamically,
# so absence from the type says nothing about whether the call works.
_DYNAMIC_CLASSES = {"Assembly"}

# Attributes every Python object carries plus the pybind11 bookkeeping that
# does not show up in `dir()` on some builds. Never reported.
_ALWAYS_OK = {"__class__", "__doc__", "__dict__", "__module__", "__init__"}


@lru_cache(maxsize=None)
def _returns_self(cls, attr):
    """True when `cls.attr` is annotated as returning its own type.

    CadQuery's fluent API is what makes chains typeable without a hand-kept
    table of return types: it is fully annotated, and every method that hands
    back the same object for further chaining is annotated with the TypeVar
    `T` rather than a concrete class. `Workplane.center` and `Workplane.rect`
    are `~T`; `Workplane.val` is a Union and `findSolid` is a Solid|Compound,
    and both correctly stop the propagation here. Reading the annotation is
    what lets `wp = cq.Workplane(plane).center(x, z)` be typed at all — which
    is exactly how the `roundedRect` script was written, so without this the
    check would not have seen it.
    """
    fn = getattr(cls, attr, None)
    if fn is None:
        return False
    try:
        ann = inspect.signature(fn).return_annotation
    except (TypeError, ValueError):
        return False
    return isinstance(ann, typing.TypeVar)


def _resolve(module_name, attr=None):
    """The live object for `module_name[.attr]`, or None if it cannot load."""
    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return None
    if attr is None:
        return mod
    return getattr(mod, attr, None)


def _covered(name):
    """Is this dotted module name one of the libraries we introspect?"""
    root = (name or "").split(".")[0]
    return root in _LIB_ROOTS


def _usable(obj):
    """A class this check may reason about."""
    return (isinstance(obj, type)
            and obj.__name__ not in _DYNAMIC_CLASSES
            and not hasattr(obj, "__getattr__"))


def _lib_names(tree):
    """Map local name -> live class / module, for the imports this script made.

    Covers every spelling the executor uses: `from OCP.X import Y`,
    `import OCP.X as x`, `import cadquery as cq`, `from cadquery import
    Workplane`.
    """
    classes, modules = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not _covered(node.module):
                continue
            for a in node.names:
                if a.name == "*":
                    continue
                obj = _resolve(node.module, a.name)
                if _usable(obj):
                    classes[a.asname or a.name] = obj
                elif obj is not None and not isinstance(obj, type):
                    modules[a.asname or a.name] = obj      # a submodule
        elif isinstance(node, ast.Import):
            for a in node.names:
                if not _covered(a.name):
                    continue
                mod = _resolve(a.name)
                if mod is not None:
                    modules[a.asname or a.name.split(".")[-1]] = mod
    return classes, modules


def _patched(tree, classes, modules):
    """Attributes the script itself assigns onto a covered class.

    `cq.Workplane.myHelper = _my_helper` is how CadQuery plugins are
    registered, and after that line the attribute genuinely exists. Rare in
    generated scripts, but a script that does it is correct and must not be
    rejected for it.
    """
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if (isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and (t.value.id in classes or t.value.id in modules)):
                out.add(t.attr)
            elif (isinstance(t, ast.Attribute)
                  and isinstance(t.value, ast.Attribute)):
                out.add(t.attr)          # cq.Workplane.foo = ...
    return out


def _bindings(tree):
    """Every value each local name is bound to: name -> [value node | None].

    `None` marks a binding whose type cannot be read off the syntax — a loop
    target, a parameter, tuple unpacking, an `except ... as e`. A name with any
    such binding is never typed below, which is what keeps the inference sound.
    """
    binds = {}

    def bind(target, value=None):
        if isinstance(target, ast.Name):
            binds.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for el in target.elts:
                bind(el)                       # unpacking: type unknown

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                bind(t, node.value)
        elif isinstance(node, ast.AnnAssign):
            bind(node.target, node.value)
        elif isinstance(node, ast.AugAssign):
            bind(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bind(node.target)
        elif isinstance(node, ast.comprehension):
            bind(node.target)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                bind(node.optional_vars)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for a in (list(args.posonlyargs) + list(args.args)
                      + list(args.kwonlyargs)
                      + [x for x in (args.vararg, args.kwarg) if x]):
                binds.setdefault(a.arg, []).append(None)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            binds.setdefault(node.name, []).append(None)

    return binds


def _chain_type(value, classes, modules, depth=0):
    """The class an expression evaluates to, or None when it cannot be known.

    Resolves a constructor call and then walks any self-returning methods
    chained onto it, so `cq.Workplane(plane).center(x, z)` types as Workplane.
    Returns None the moment anything is uncertain — an unannotated method, a
    method annotated with a concrete return type, a static call, a name that
    was never imported. None means "no opinion", never "wrong".
    """
    if not isinstance(value, ast.Call) or depth > 24:
        return None
    f = value.func
    if isinstance(f, ast.Name):                        # Cls(...)
        return classes.get(f.id)
    if isinstance(f, ast.Attribute):
        if isinstance(f.value, ast.Name):
            mod = modules.get(f.value.id)              # cq.Workplane(...)
            if mod is not None:
                cand = getattr(mod, f.attr, None)
                return cand if _usable(cand) else None
            if f.value.id in classes:                  # Cls.static(...)
                return None                            # return type unknown
        recv = _chain_type(f.value, classes, modules, depth + 1)
        if recv is not None and _returns_self(recv, f.attr):
            return recv
    return None


def _instance_types(tree, classes, modules):
    """Local name -> the class it holds, where that can be known for certain.

    Two inferences, both sound:

    * A CONSTRUCTOR call. The value of `SomeClass(...)` is an instance of
      `SomeClass`; there is no way for that to be wrong.
    * A SELF-RETURNING method on an already-typed value. `Workplane.center` is
      annotated `-> T`, so `cq.Workplane(plane).center(x, z)` is still a
      Workplane. Propagation stops the moment a method is annotated with
      anything else (`val`, `findSolid`) or is not annotated at all, so an
      unknown return type yields no type rather than a wrong one.

    Anything else — an element pulled out of a list, a value returned by the
    script's own helper — is left untyped and therefore unchecked.

    A name may be bound MORE THAN ONCE as long as every binding yields the SAME
    class, because then the type is the same whichever branch ran. That is not
    a corner case: the executor's standard shape for an API it is unsure of is
    exactly `try: df = Cls(arg)` / `except TypeError: df = Cls()`, and the run
    this check was written for used it. Requiring a single binding silently
    skipped the very script that motivated the check.
    """
    def cls_of(value):
        return _chain_type(value, classes, modules)

    def is_none_sentinel(v):
        # `df = None` ahead of a try/except that constructs it is the other
        # half of the executor's standard shape. It carries no type
        # information and must not veto the constructor bindings: if control
        # really did reach an attribute access with `df` still None, the call
        # raises AttributeError there too, so treating the name as the
        # constructed class never turns a working script into a finding.
        return isinstance(v, ast.Constant) and v.value is None

    out = {}
    for name, values in _bindings(tree).items():
        kinds = {cls_of(v) for v in values if not is_none_sentinel(v)}
        if len(kinds) == 1:
            cls = kinds.pop()
            if cls is not None:
                out[name] = cls
    return out


def _suggest(cls, attr):
    """`did you mean` text for a missing attribute, from the real namespace."""
    real = [a for a in dir(cls) if not a.startswith("_")]
    close = difflib.get_close_matches(attr, real, n=3, cutoff=0.6)
    if not close:
        # A wrong-name guess often shares a prefix rather than an edit
        # distance — `AddFace` vs `AddFaceToRemove` scores 0.58 and misses the
        # cutoff, which is exactly the case that motivated this check.
        low = attr.lower()
        close = [a for a in real
                 if a.lower().startswith(low) or low.startswith(a.lower())][:3]
    if close:
        return "Did you mean " + " / ".join(f"`{c}`" for c in close) + "? "
    return ""


def _api_problems(script):
    """Attribute accesses the installed CadQuery / OCP does not actually have.

    Same `(short_name, remedy)` shape as PATTERNS so it drops straight into
    `check()` and therefore into the router's free repair-in-place loop.
    """
    try:
        tree = ast.parse(script or "")
    except SyntaxError:
        # The subprocess will report the syntax error with a line number, which
        # is a better message than anything this module could synthesise.
        return []

    try:
        classes, modules = _lib_names(tree)
        if not classes and not modules:
            return []
        instances = _instance_types(tree, classes, modules)
        patched = _patched(tree, classes, modules)

        # Only attributes that are CALLED are checked. A method is defined on
        # the class in both libraries, so `hasattr(cls, name)` is authoritative
        # for it; a DATA attribute may be created per-instance in `__init__`
        # and then does not exist on the class at all. Measured: `cq.Plane`
        # assigns `self.zDir` in its constructor, so `hasattr(cq.Plane,
        # "zDir")` is False while `plane.zDir` works perfectly — three scripts
        # whose geometry was accepted read exactly that, and checking bare
        # attribute reads reported all three. Calls alone cost a little
        # coverage and remove the entire false-positive class.
        called = {id(n.func) for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}

        found, seen = [], set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if id(node) not in called:
                continue
            attr = node.attr
            if attr in _ALWAYS_OK or attr in patched:
                continue

            owner = None
            if isinstance(node.value, ast.Name):
                base = node.value.id
                if base in instances:           # v = Cls(...) ; v.attr
                    owner = instances[base]
                elif base in classes:           # Cls.attr (statics, enums)
                    owner = classes[base]
                elif base in modules:           # module.Name
                    owner = modules[base]
            elif isinstance(node.value, ast.Call):
                # The fluent form with no local in between:
                # `cq.Workplane(p).center(x, y).roundedRect(...)`
                owner = _chain_type(node.value, classes, modules)

            if owner is None or hasattr(owner, attr):
                continue

            what = getattr(owner, "__name__", None) or getattr(
                owner, "__package__", None) or "it"
            key = (what, attr)
            if key in seen:
                continue
            seen.add(key)
            lib = "OCP" if (owner.__module__ or "").startswith("OCP") \
                else "CadQuery"
            found.append((
                f"{what}.{attr} does not exist here",
                f"`{attr}` is not an attribute of `{what}` in the {lib} build "
                f"installed here — the call raises AttributeError, and a "
                f"try/except around it turns the whole attempt into a no-op "
                f"that changes nothing. {_suggest(owner, attr)}"
                f"Names lifted from the OCC C++ docs, from pythonocc, or from "
                f"a different CadQuery version are often spelled differently "
                f"here (OCP suffixes C++ statics with `_s`). If no close name "
                f"fits, the method does not exist in any form — build the "
                f"result from operations that do."))
        return found[:5]
    except Exception:
        # Introspection must never be the reason a good script is rejected.
        return []


def _code_only(src):
    """`src` with whole-line comments blanked out.

    Measured false positive: a script that had ALREADY got the boolean right
    carried the note `# CadQuery Solid does not provide .common(); use intersect
    to compute the shared volume` — and the `.common(` rule rejected it, costing
    a repair round-trip to fix code that was correct and a comment that was
    true. Only lines whose first non-space character is `#` are dropped, which
    can never turn code into a comment; the worst case is a missed detection
    inside a docstring, and a miss is the cheap direction of this trade.
    """
    return "\n".join("" if line.lstrip().startswith("#") else line
                     for line in (src or "").splitlines())


def check(script):
    """Every distinct pattern that matches `script`, as (short_name, fix_text).

    One entry per pattern, not per match: the model needs to be told about each
    broken API once, and repeating the same remedy five times because the same
    call appears five times only buries the other findings.
    """
    src = _code_only(script)
    found = [(name, fix) for rx, name, fix in PATTERNS if rx.search(src)]
    # The regex table first: its remedies are written for the specific mistake
    # and say what to do instead, where the generic check can only report that
    # a name is absent. Both feed the same repair loop, so a script with one of
    # each is told about both in one round-trip.
    seen = {n for n, _ in found}
    return found + [(n, f) for n, f in _api_problems(src) if n not in seen]


# Same budget as executor._SCRIPT_TAIL_CHARS reasoning: keep the tail, which is
# where the edit and the return live — a little shorter here because the
# offending calls are being quoted separately above it anyway.
_SCRIPT_TAIL_CHARS = 4000


def feedback(problems, script):
    """Turn lint findings into instructions the executor can act on."""
    src = (script or "").strip()
    if len(src) > _SCRIPT_TAIL_CHARS:
        src = "# ... start of function omitted ...\n" + src[-_SCRIPT_TAIL_CHARS:]

    blocks = "\n\n".join(f"{i}. {name}\n{fix}"
                         for i, (name, fix) in enumerate(problems, 1))

    return f"""\
YOUR SCRIPT WAS REJECTED BEFORE RUNNING — it calls APIs that are known to fail
in this environment, caught by a static check so the attempt was not wasted on a
guaranteed crash.

{blocks}

THE FUNCTION YOU SUBMITTED:

{src}

Fix ONLY these calls and keep the rest of your approach — the selection logic,
the measurements and the construction you already worked out are not what was
rejected, and rewriting the function from scratch is how the same broken call
comes back a second time."""
