"""SessionState — the shared, inspectable memory the router drives.

The baseline's only "state" is an append-only list of chat messages that grows
until the context is mostly stale copies of itself. Here state is explicit and
structured: the router reads it to decide what happens next, each agent gets
only the slice it needs, and the whole run is serialisable for the dashboard.
"""

import json
import time
from dataclasses import dataclass, field, asdict


@dataclass
class SubTask:
    idx: int
    goal: str                       # what this step must achieve
    rationale: str = ""             # why the strategist ordered it here
    focus: list = field(default_factory=list)  # feature terms for index focusing
    # canonical geometric tags from skillref.TAG_SECTIONS; pin recipe selection
    tags: list = field(default_factory=list)
    # Bounding-box faces this sub-goal is allowed to move ('+X', '-Z', ...).
    # [] means the outer envelope must not change; None means the strategist
    # never declared one, which disables the router's envelope gate entirely.
    envelope: list = None
    status: str = "pending"         # pending | active | done | failed
    attempts: int = 0
    qa_notes: list = field(default_factory=list)
    # The strategist's original wording, kept when a partial acceptance
    # rewrites `goal` to describe only the remaining work. Refinement goals
    # are always rebuilt from here so consecutive partials replace the
    # remainder block instead of nesting inside each other.
    goal_original: str = ""
    # How many times the strategist has already re-planned this sub-goal after
    # every attempt of a round was rejected (capped by config.MAX_REPLANS).
    replans: int = 0


@dataclass
class SessionState:
    request_id: str
    instruction: str
    input_step: str
    work_dir: str

    geometry_text: str = ""         # compact B-rep index of the input
    plan_summary: str = ""
    subtasks: list = field(default_factory=list)
    cursor: int = 0

    script: str = ""                # current best my_cad_function source
    accepted_step: str = ""         # last geometry that passed QA
    # Every state QA ever accepted, so a refinement that turns out worse can be
    # rolled back instead of being inherited by the rest of the run.
    checkpoints: list = field(default_factory=list)
    last_step: str = ""             # geometry from the most recent run
    last_error: str = ""
    last_qa: dict = field(default_factory=dict)
    partial_accepts: int = 0        # sub-goals kept as "directionally right"

    # one record per executed attempt, with its rendered views — this is what
    # the dashboard shows so you can watch the edit evolve step by step
    steps: list = field(default_factory=list)

    events: list = field(default_factory=list)
    status: str = "created"         # created|planning|executing|qa|done|failed
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    # ---- helpers -------------------------------------------------------
    def log(self, kind, msg, **extra):
        self.events.append({"t": round(time.time() - self.started, 2),
                            "kind": kind, "msg": msg, **extra})

    @property
    def current(self):
        if 0 <= self.cursor < len(self.subtasks):
            return self.subtasks[self.cursor]
        return None

    @property
    def done(self):
        return self.cursor >= len(self.subtasks)

    def progress(self):
        total = len(self.subtasks) or 1
        finished = sum(1 for s in self.subtasks if s.status == "done")
        return finished, total

    def to_dict(self):
        d = asdict(self)
        d["subtasks"] = [asdict(s) if not isinstance(s, dict) else s
                         for s in self.subtasks]
        return d

    def save(self, path):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
