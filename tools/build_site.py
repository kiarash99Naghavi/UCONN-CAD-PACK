#!/usr/bin/env python3
"""Render the GitHub Pages site into `_site/`.

Everything the site shows already exists in this repo — the scores in
`outputs/manifest.json`, the write-ups in `docs/` and `handoff/`, the figures,
the meshes `tools/make_web_meshes.py` tessellated. This turns them into pages.

    python3 -m pip install markdown        # the only dependency
    python3 tools/build_site.py            # -> _site/
    python3 tools/build_site.py --serve    # -> _site/ + http://localhost:8000

Pages:

    index.html          the pitch: headline numbers, a live part, the framework
    gallery.html        all 48 tasks as cards, filterable
    tasks/<id>.html     one per task — three locked 3D viewers and the run detail
    results.html        every score in one sortable table
    <doc>.html          each markdown write-up, with a table of contents

The task pages are generated rather than served from one page with a query
string, so every task has a real URL that can be linked to and shared.

Run `tools/make_web_meshes.py` and `tools/make_site_data.py` first if the
outputs have changed; both need the benchmark checkout's interpreter, which is
why they are separate from this script. This one is pure Python and is what CI
runs on every push.
"""

import argparse
import html
import json
import os
import os.path as osp
import re
import shutil
import sys

REPO = osp.dirname(osp.dirname(osp.abspath(__file__)))
SITE = osp.join(REPO, "site")
OUT = osp.join(REPO, "_site")

REPO_URL = "https://github.com/kiarash99Naghavi/UCONN-CAD-PACK"
BENCHMARK_URL = "https://github.com/AutodeskAILab/IDETC26-Hackathon-Autodesk-neuralCAD-Edit"

NAV = [
    ("index.html", "Overview"),
    ("gallery.html", "Tasks"),
    ("results.html", "Results"),
    ("method.html", "Method"),
    ("architecture.html", "Architecture"),
    ("notes.html", "Engineering notes"),
]

# Markdown write-ups that become their own page. Each entry is
# (output name, nav title, source markdown, one-line subtitle, source label).
DOCS = [
    ("method.html", "Method",
     "docs/method_overview.md",
     "How the harness turns one sentence and one B-rep into an edited part.",
     "docs/method_overview.md"),
    ("architecture.html", "Architecture",
     "ARCHITECTURE.md",
     "The module-by-module contract, generated from the source it describes.",
     "ARCHITECTURE.md"),
    ("notes.html", "Engineering notes",
     "handoff/AGENT_HANDOFF.md",
     "Failure taxonomy, what was tried and rejected, and what to do next.",
     "handoff/AGENT_HANDOFF.md"),
    ("results-detail.html", "Results write-up",
     "handoff/RESULTS.md",
     "The headline numbers, and which fixes have causal evidence behind them.",
     "handoff/RESULTS.md"),
    ("selector-study.html", "Selector study",
     "handoff/mbr/mbr_report.md",
     "Offline MBR study: would a better selector have picked a better candidate?",
     "handoff/mbr/mbr_report.md"),
    ("data.html", "Data schema",
     "handoff/DATA.md",
     "What is in the exported run and candidate records, field by field.",
     "handoff/DATA.md"),
    ("replay.html", "Replay",
     "handoff/REPLAY.md",
     "Drop another model into the executor slot and re-run it for $0 in API.",
     "handoff/REPLAY.md"),
]


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------

def markdown_renderer():
    try:
        import markdown
    except ImportError:
        sys.exit("this needs `python3 -m pip install markdown`")
    return markdown.Markdown(
        # `extra` carries tables and fenced code, which every write-up uses;
        # `toc` gives the headings the ids the sidebar links to.
        extensions=["extra", "sane_lists", "admonition", "toc"],
        extension_configs={"toc": {"permalink": "¶",
                                   "permalink_class": "headline-anchor"}})


def rewrite_links(body, source):
    """Point a write-up's relative links at something that exists on the site.

    The markdown files link to each other and to files in the repo. On the site
    a sibling `.md` becomes the page built from it, anything else becomes a link
    into GitHub at the path it actually has, and in-page anchors are left alone.
    """
    page_of = {src: name for name, _, src, _, _ in DOCS}
    base = osp.dirname(source)

    def fix(match):
        prefix, url = match.group(1), match.group(2)
        if re.match(r"^(https?:|mailto:|#|/)", url):
            return match.group(0)
        target, _, anchor = url.partition("#")
        joined = osp.normpath(osp.join(base, target)) if target else source
        if joined in page_of:
            return f"{prefix}{page_of[joined]}{'#' + anchor if anchor else ''})"
        if target.endswith(".svg") or target.endswith(".png"):
            return f"{prefix}assets/repo/{osp.basename(joined)})"
        return f"{prefix}{REPO_URL}/blob/main/{joined}{'#' + anchor if anchor else ''})"

    return re.sub(r"(\]\()([^)\s]+)\)", fix, body)


def strip_front_matter(text):
    """Drop the generated-file HTML comment some write-ups open with."""
    return re.sub(r"\A<!--.*?-->\s*", "", text, flags=re.S)


# --------------------------------------------------------------------------
# page shell
# --------------------------------------------------------------------------

def page(title, body, *, active="", description="", scripts=(), depth=0,
         wide=False):
    """Wrap page content in the shared shell.

    `depth` is how many directories deep the page sits, so the task pages under
    `tasks/` can share one template with the pages at the root.
    """
    up = "../" * depth
    current = ' aria-current="page"'
    nav = "\n".join(
        '          <a href="%s%s"%s>%s</a>'
        % (up, href, current if href == active else "", label)
        for href, label in NAV)
    tags = "\n".join(
        f'  <script type="module" src="{up}assets/js/{s}"></script>' for s in scripts)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(description)}">
<meta property="og:type" content="website">
<link rel="icon" href="{up}assets/img/favicon.svg">
<link rel="stylesheet" href="{up}assets/css/site.css">
<script>
  /* Applied before first paint so a dark-theme reader never sees a white flash. */
  try {{
    var t = localStorage.getItem("theme");
    if (t) document.documentElement.dataset.theme = t;
  }} catch (e) {{}}
</script>
<script type="importmap">
{{"imports": {{
  "three": "{up}assets/js/vendor/three.module.min.js",
  "three/addons/": "{up}assets/js/vendor/"
}}}}
</script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="topbar">
  <div class="wrap">
    <a class="brand" href="{up}index.html">
      <span class="mark">UCONN CAD PACK</span>
      <span class="sub">neuralCAD-Edit</span>
    </a>
    <nav class="nav" aria-label="Main">
{nav}
      <button class="theme-toggle" type="button" aria-label="Switch theme">☾</button>
    </nav>
  </div>
</header>
<main id="main">
{body}
</main>
<footer class="foot">
  <div class="wrap">
    <div class="foot-grid">
      <div>
        <h4>Repository</h4>
        <ul>
          <li><a href="{REPO_URL}">Source and outputs on GitHub</a></li>
          <li><a href="{REPO_URL}/tree/main/outputs">All 48 edited parts (STEP + STL)</a></li>
          <li><a href="{up}presentation.html">Presentation and demo</a></li>
        </ul>
      </div>
      <div>
        <h4>Write-ups</h4>
        <ul>
          <li><a href="{up}method.html">Method</a></li>
          <li><a href="{up}architecture.html">Architecture</a></li>
          <li><a href="{up}notes.html">Engineering notes</a></li>
          <li><a href="{up}selector-study.html">Selector study</a></li>
          <li><a href="{up}replay.html">Replay for $0</a></li>
        </ul>
      </div>
      <div>
        <h4>Benchmark</h4>
        <ul>
          <li><a href="{BENCHMARK_URL}">IDETC 2026 neuralCAD-Edit</a></li>
          <li><a href="{up}data.html">Exported run data</a></li>
        </ul>
      </div>
    </div>
    <cite>
      Input parts and expert edits come from the neuralCAD-Edit dataset
      (Perrett, Bouchard and McCarthy, 2026), CC BY-NC 4.0, and are shown here
      for comparison with our outputs. All credit for the dataset and the
      ground-truth edits goes to its authors. Team UCONN CAD PACK,
      UConn School of Mechanical, Aerospace and Manufacturing Engineering.
    </cite>
  </div>
</footer>
<script type="module" src="{up}assets/js/app.js"></script>
{tags}
</body>
</html>
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def fmt(value, places=3, dash="—"):
    return dash if value is None else f"{value:.{places}f}"


def tile(label, value, note="", accent=False):
    return f"""<div class="tile">
      <div class="label">{label}</div>
      <div class="value{' accent' if accent else ''}">{value}</div>
      <div class="note">{note}</div>
    </div>"""


def load_tasks():
    path = osp.join(SITE, "data", "tasks.json")
    if not osp.exists(path):
        sys.exit("site/data/tasks.json is missing — run tools/make_site_data.py first")
    with open(path) as f:
        return json.load(f)


def summarise(tasks):
    """Aggregates the pages quote, computed rather than written down."""
    def mean(rows, key):
        vals = [t["scores"].get(key) for t in rows if t["scores"].get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    by_difficulty = {}
    for level in ("easy", "medium", "hard"):
        rows = [t for t in tasks if t["difficulty"] == level]
        by_difficulty[level] = {
            "n": len(rows),
            "diff_f1": mean(rows, "diff_f1"),
            "gpt": mean_baseline(rows, "gpt52_diff_f1"),
            "human": mean_baseline(rows, "other_human_diff_f1"),
        }

    costs = [t["cost_usd"] for t in tasks if t.get("cost_usd") is not None]
    return {
        "n": len(tasks),
        "chamfer": mean(tasks, "chamfer_similarity_norm"),
        "volume_f1": mean(tasks, "volume_f1"),
        "diff_f1": mean(tasks, "diff_f1"),
        "gpt": mean_baseline(tasks, "gpt52_diff_f1"),
        "human": mean_baseline(tasks, "other_human_diff_f1"),
        "cost": sum(costs) / len(costs) if costs else None,
        "by_difficulty": by_difficulty,
        "record": record(tasks),
    }


def mean_baseline(rows, key):
    vals = [t.get("baselines", {}).get(key) for t in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def record(tasks):
    """Win / tie / loss against the single-shot baseline on diff F1."""
    w = t_ = l = 0
    for task in tasks:
        ours = task["scores"].get("diff_f1")
        base = task.get("baselines", {}).get("gpt52_diff_f1")
        if ours is None or base is None:
            continue
        if abs(ours - base) < 1e-9:
            t_ += 1
        elif ours > base:
            w += 1
        else:
            l += 1
    return w, t_, l


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------

def build_index(tasks, stats):
    hero_task = pick_hero(tasks)
    ratio = (stats["diff_f1"] / stats["gpt"]) if stats["gpt"] else None
    w, t_, l = stats["record"]

    rows = "".join(f"""
        <tr>
          <td><span class="badge {level}">{level}</span></td>
          <td class="num">{d['n']}</td>
          <td class="num"><strong>{fmt(d['diff_f1'])}</strong></td>
          <td class="num">{fmt(d['gpt'])}</td>
          <td class="num">{fmt(d['human'])}</td>
          <td class="num">{fmt(d['diff_f1'] / d['gpt'], 2) + '×' if d['gpt'] else '—'}</td>
        </tr>""" for level, d in stats["by_difficulty"].items())

    body = f"""
<section class="hero">
  <div class="wrap">
    <div>
      <span class="eyebrow">ASME IDETC-CIE 2026 Hackathon</span>
      <h1>CAD edits from one sentence, scored against a human expert.</h1>
      <p class="lede">
        A three-agent harness over CadQuery — a strategist that plans from
        measurements, an executor that writes one function per sub-goal, and a QA
        agent with no stake in the edit passing — behind six deterministic gates
        that reject bad geometry before any model looks at it.
        {stats['n']} tasks, all of them producing valid geometry, at
        ${fmt(stats['cost'], 2)} an edit.
      </p>
      <div class="cta-row">
        <a class="btn btn-primary" href="gallery.html">Explore all {stats['n']} edits in 3D →</a>
        <a class="btn btn-ghost" href="method.html">Read the method</a>
      </div>
    </div>
    <div>
      <div class="viewer" id="hero-viewer" data-mesh="{hero_task['meshes'].get('ours', '')}">
        <div class="stage" style="aspect-ratio: 4 / 3"></div>
        <div class="cap"><span class="dot dot-ours"></span>Our edit
          <span class="tris"></span></div>
        <div class="state">loading part…</div>
      </div>
      <p class="mono" style="margin:.7rem 0 0; font-size:.8rem; line-height:1.5">
        {html.escape(hero_task['instruction'])}
        <a href="tasks/{hero_task['id']}.html">Open this task →</a>
      </p>
    </div>
  </div>
</section>

<section class="section">
  <div class="wrap">
    <h2>Final scores</h2>
    <p class="section-lede">
      Computed with the benchmark's own metric code over all {stats['n']} edit
      tasks, same defaults as the published baselines. Diff F1 is the metric that
      discriminates: it compares <em>the material each edit changed</em>, so a
      no-op and a from-scratch rebuild both score near zero.
    </p>
    <div class="tiles">
      {tile("Surface chamfer similarity", fmt(stats['chamfer']),
            "compares the finished parts")}
      {tile("Volumetric F1", fmt(stats['volume_f1']),
            "compares occupied volume")}
      {tile("Volumetric difference F1", fmt(stats['diff_f1']),
            f"<strong>{fmt(ratio, 2)}×</strong> the single-shot baseline "
            f"({fmt(stats['gpt'])})", accent=True)}
      {tile("Cost per edit", "$" + fmt(stats['cost'], 2),
            f"{w} W / {t_} T / {l} L against the baseline")}
    </div>

    <h3>By difficulty</h3>
    <div class="scroll-x">
      <table>
        <thead><tr>
          <th>Band</th><th class="num">Tasks</th><th class="num">Ours</th>
          <th class="num">gpt-5.2</th><th class="num">Second human</th>
          <th class="num">Ratio</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <p class="mono" style="font-size:.82rem">
      Diff F1, averaged within each band. The margin over the baseline is widest
      on the hard band — the opposite of what a brittle pipeline does.
    </p>
    <p><a href="results.html">Every task, every score →</a></p>
  </div>
</section>

<section class="section band">
  <div class="wrap">
    <h2>The framework</h2>
    <p class="section-lede">
      One request flows left to right: the geometry indexer and camera rig
      measure the input part, the strategist turns the instruction into ordered
      sub-goals with envelopes, the executor writes one CadQuery function per
      sub-goal in a sandboxed subprocess, six deterministic gates reject bad
      geometry for free, and the QA agent verifies what survives against seven
      views and the measured diff.
    </p>
    <figure class="figure">
      <img src="assets/repo/architecture.svg" alt="Signal-flow schematic of the
        harness: the task library feeds the part inspector and camera rig, the
        strategist plans ordered sub-goals, the executor writes one CadQuery
        function per sub-goal, deterministic gates and the QA agent reject or
        accept each attempt, and the router carries accepted state forward.">
      <figcaption>
        Generated from the source by <code>tools/blockdiagram.py</code>.
        <a href="architecture.html">The module-by-module contract →</a>
      </figcaption>
    </figure>

    <div class="cols-2" style="margin-top:2.4rem">
      <div class="card">
        <h3>The strategist reads measurements, not pixels</h3>
        <p>It gets hole families grouped by radius, every opening labelled blind
        or through, bores paired into single features, face areas. Colour renders
        are attached only when the instruction carries an appearance, view,
        deictic or dimension word. That is what lets it pick the right slot when
        the part has two congruent ones.</p>
      </div>
      <div class="card">
        <h3>Gates reject before any model judges</h3>
        <p>Real API names, no-op detection, phantom material buried inside the
        part, edit direction against the sub-goal's tag, frame drift, and the
        declared envelope. A rejection here costs zero tokens and hands back
        specific text, not just a retry.</p>
      </div>
      <div class="card">
        <h3>QA has no stake in the edit passing</h3>
        <p>A separate call that sees the sub-goal, the rest of the plan labelled
        done or not-run-yet, seven views before and after, and the measured diff.
        Three verdicts, not two: accepted, partial — kept and refined in place —
        and rejected.</p>
      </div>
      <div class="card">
        <h3>Measurements decide, renders illustrate</h3>
        <p>A 5&nbsp;mm gap on a 984&nbsp;mm part is 2.5 pixels in a render, and
        the camera auto-frames, so a part that shifted looks identical. No
        image-based check can catch that. The numbers gate first.</p>
      </div>
    </div>
  </div>
</section>

<section class="section">
  <div class="wrap">
    <h2>The dashboard</h2>
    <p class="section-lede">
      The method deck, any task's input and ground truth, our result next to the
      human's, per-task and whole-benchmark scores, and saved run replays — all
      in one Dash app. No API key is needed to browse it.
    </p>
    <figure class="figure">
      <img src="assets/repo/CADPACK.gif" alt="Screen recording of the dashboard
        replaying one edit request through the pipeline." loading="lazy">
      <figcaption>Run it with <code>./run_dashboard.sh</code>.</figcaption>
    </figure>
  </div>
</section>
"""
    return page("UCONN CAD PACK — CAD editing from natural language", body,
                active="index.html", scripts=("hero.js",),
                description="A three-agent CadQuery harness for the IDETC 2026 "
                            "Autodesk neuralCAD-Edit benchmark: 48 CAD edits "
                            "from one-sentence instructions, scored against "
                            "human experts and browsable in 3D.")


def pick_hero(tasks):
    """The landing page's live part: the best hard task that has a mesh.

    Not simply the best score overall. The top of that list is
    `SUJ2G2UMJQR7PMBX_1759209917`, where the expert's edit changed zero voxels —
    so "change nothing" scores 1.0 and every real edit scores 0.0. Leading with
    a part we deliberately did not edit would be the wrong first impression in
    both directions: it oversells the number and undersells the work. The hard
    band cannot contain that case and is where the margin over the baseline is
    widest anyway.
    """
    with_mesh = [t for t in tasks if t["meshes"].get("ours")]
    hard = [t for t in with_mesh if t["difficulty"] == "hard"]
    return max(hard or with_mesh or tasks,
               key=lambda t: t["scores"].get("diff_f1") or 0)


def build_gallery(tasks, stats):
    cards = []
    for task in sorted(tasks, key=lambda t: -(t["scores"].get("diff_f1") or 0)):
        thumb = task["thumbs"].get("ours") or task["thumbs"].get("gt")
        shot = (f'<img src="{thumb}" alt="" loading="lazy" decoding="async">'
                if thumb else '<span class="mono">no render</span>')
        f1 = task["scores"].get("diff_f1")
        haystack = html.escape(
            f'{task["instruction"]} {task["id"]} {task["difficulty"]}'.lower(),
            quote=True)
        cards.append(f"""<a class="task-card" href="tasks/{task['id']}.html"
   data-difficulty="{task['difficulty']}"
   data-diff-f1="{f1 if f1 is not None else 0}"
   data-chamfer="{task['scores'].get('chamfer_similarity_norm') or 0}"
   data-volume-f1="{task['scores'].get('volume_f1') or 0}"
   data-cost="{task.get('cost_usd') or 0}"
   data-search="{haystack}">
  <div class="shot">
    {shot}
    <span class="corner badge {task['difficulty']}">{task['difficulty']}</span>
    <span class="f1">diff F1 {fmt(f1)}</span>
  </div>
  <div class="body">
    <span class="instr">{html.escape(task['instruction'])}</span>
    <span class="id">{task['id']}</span>
  </div>
</a>""")

    body = f"""
<section class="section" style="padding-bottom:1rem">
  <div class="wrap">
    <h1>All {stats['n']} edit tasks</h1>
    <p class="section-lede">
      Every task in <code>edit_192_external</code>, with the part we shipped.
      Open any one for three locked 3D viewers — the input part, our edit, and
      the expert's — and an overlay that puts ours and theirs in the same frame.
    </p>

    <div class="toolbar">
      <div class="group">
        <label for="search">Search</label>
        <input type="search" id="search" placeholder="instruction or request id">
      </div>
      <div class="group">
        <label>Difficulty</label>
        <div class="seg" id="difficulty">
          <button type="button" data-value="all" aria-pressed="true">All</button>
          <button type="button" data-value="easy" aria-pressed="false">Easy</button>
          <button type="button" data-value="medium" aria-pressed="false">Medium</button>
          <button type="button" data-value="hard" aria-pressed="false">Hard</button>
        </div>
      </div>
      <div class="group">
        <label for="sort">Sort</label>
        <select id="sort">
          <option value="diffF1-desc">Diff F1, best first</option>
          <option value="diffF1-asc">Diff F1, worst first</option>
          <option value="difficulty-asc">Difficulty, easy first</option>
          <option value="difficulty-desc">Difficulty, hard first</option>
          <option value="cost-desc">Cost, most first</option>
        </select>
      </div>
      <span class="count" id="count"></span>
    </div>

    <div class="grid" id="gallery">
{chr(10).join(cards)}
    </div>
    <p class="empty" id="empty" hidden>No task matches that filter.</p>
  </div>
</section>
"""
    return page("All 48 edit tasks — UCONN CAD PACK", body,
                active="gallery.html", scripts=("gallery.js",),
                description="Browse all 48 neuralCAD-Edit tasks: the "
                            "instruction, our edited part, the expert's edit, "
                            "and the score for each.")


def build_task(task, neighbours, stats):
    previous, following = neighbours
    scores = task["scores"]
    baselines = task.get("baselines", {})
    f1 = scores.get("diff_f1")
    gpt = baselines.get("gpt52_diff_f1")
    human = baselines.get("other_human_diff_f1")

    def compare(ours, other, label):
        if ours is None or other is None:
            return ""
        cls = "win" if ours > other else ("loss" if ours < other else "")
        sign = "+" if ours >= other else "−"
        return (f'<dt>{label}</dt><dd>{fmt(other)} '
                f'<span class="{cls}">{sign}{fmt(abs(ours - other))}</span></dd>')

    subgoals = "".join(
        f'<li>{html.escape(s["goal"])}'
        + ('<span class="tags">'
           + "".join(f'<span class="tag">{html.escape(t)}</span>' for t in s["tags"])
           + "</span>" if s["tags"] else "")
        + "</li>"
        for s in task["plan"]["subgoals"])

    plan_card = ""
    if subgoals:
        understanding = task["plan"]["understanding"]
        plan_card = f"""
      <div class="card">
        <h4>What the strategist planned</h4>
        {f'<p style="font-size:.9rem;color:var(--ink-soft)">{html.escape(understanding)}</p>'
         if understanding else ''}
        <ol class="subgoals">{subgoals}</ol>
      </div>"""

    tokens = task.get("tokens") or {}
    usage = ""
    if tokens:
        usage = f"""
      <div class="card">
        <h4>What it cost</h4>
        <dl class="kv">
          <dt>Model calls</dt><dd>{tokens.get('llm_calls', '—')}</dd>
          <dt>Input tokens</dt><dd>{tokens.get('input_tokens', 0):,}</dd>
          <dt>Output tokens</dt><dd>{tokens.get('output_tokens', 0):,}</dd>
          <dt>Cost</dt><dd>${fmt(task.get('cost_usd'), 3)}</dd>
        </dl>
      </div>"""

    downloads = "".join(
        f'<li><a href="{url}">{name}</a></li>'
        for name, url in sorted(task.get("downloads", {}).items()))

    missing = [r for r in ("input", "ours", "gt") if not task["meshes"].get(r)]
    warning = (f'<p class="doc-note">No web mesh for: {", ".join(missing)}.</p>'
               if missing else "")

    body = f"""
<div class="wrap">
  <section class="task-head">
    <div class="meta">
      <span class="badge {task['difficulty']}">{task['difficulty']}</span>
      <span class="id">{task['id']}</span>
    </div>
    <h1>{html.escape(task['instruction'])}</h1>
  </section>

  <div class="task-layout">
    <div>
      {warning}
      <div class="viewer-row" id="viewers"></div>
      <div class="viewer-bar" id="viewer-bar"></div>
      <p style="font-size:.88rem;color:var(--ink-faint);margin-top:1.2rem">
        All three parts are framed on one shared bounding box and share one
        camera, so what you see between them is the edit and nothing else.
        Nothing here is re-centred or re-scaled to fit.
      </p>
    </div>

    <aside class="side">
      <div class="card">
        <h4>Scores</h4>
        <dl class="kv">
          <dt>Diff F1</dt><dd>{fmt(f1)}</dd>
          <dt>Volume F1</dt><dd>{fmt(scores.get('volume_f1'))}</dd>
          <dt>Chamfer similarity</dt><dd>{fmt(scores.get('chamfer_similarity_norm'))}</dd>
        </dl>
        <div class="bar"><span style="width:{(f1 or 0) * 100:.1f}%"></span></div>
        <h4 style="margin-top:1.4em">Diff F1 against</h4>
        <dl class="kv">
          {compare(f1, gpt, "gpt-5.2 single-shot")}
          {compare(f1, human, "A second human")}
        </dl>
      </div>
      {plan_card}
      {usage}
      <div class="card">
        <h4>Files</h4>
        <ul style="list-style:none;padding:0;margin:0;font-size:.89rem">{downloads}</ul>
      </div>
    </aside>
  </div>

  <nav class="pager">
    {f'<a href="{previous["id"]}.html"><span class="dir">← Previous</span>'
     f'<span>{html.escape(previous["instruction"][:64])}…</span></a>'
     if previous else "<span></span>"}
    {f'<a class="to-end" href="{following["id"]}.html"><span class="dir">Next →</span>'
     f'<span>{html.escape(following["instruction"][:64])}…</span></a>'
     if following else "<span></span>"}
  </nav>
</div>

<script type="application/json" id="task-data">{json.dumps({
    "id": task["id"],
    "meshes": {k: "../" + v for k, v in task["meshes"].items()},
})}</script>
"""
    return page(f"{task['instruction'][:70]} — UCONN CAD PACK", body,
                active="gallery.html", scripts=("task.js",), depth=1,
                description=f"{task['difficulty']} task {task['id']}: "
                            f"{task['instruction']}")


def build_results(tasks, stats):
    rows = []
    for task in sorted(tasks, key=lambda t: -(t["scores"].get("diff_f1") or 0)):
        s = task["scores"]
        b = task.get("baselines", {})
        ours, gpt = s.get("diff_f1"), b.get("gpt52_diff_f1")
        verdict = ""
        if ours is not None and gpt is not None:
            if abs(ours - gpt) < 1e-9:
                verdict = '<span class="mono">tie</span>'
            elif ours > gpt:
                verdict = '<span class="win">win</span>'
            else:
                verdict = '<span class="loss">loss</span>'
        rows.append(f"""
        <tr>
          <td><a href="tasks/{task['id']}.html">{task['id']}</a>
            <span class="instr">{html.escape(task['instruction'])}</span></td>
          <td data-sort="{ {'easy': 0, 'medium': 1, 'hard': 2}[task['difficulty']] }">
            <span class="badge {task['difficulty']}">{task['difficulty']}</span></td>
          <td class="num">{fmt(s.get('diff_f1'))}</td>
          <td class="num">{fmt(gpt)}</td>
          <td class="num">{fmt(b.get('other_human_diff_f1'))}</td>
          <td class="num">{fmt(s.get('volume_f1'))}</td>
          <td class="num">{fmt(s.get('chamfer_similarity_norm'))}</td>
          <td class="num">${fmt(task.get('cost_usd'), 2)}</td>
          <td>{verdict}</td>
        </tr>""")

    w, t_, l = stats["record"]
    body = f"""
<section class="section">
  <div class="wrap">
    <h1>Results</h1>
    <p class="section-lede">
      All {stats['n']} tasks, scored with the benchmark's own metric code.
      Click any column to sort; click a request id for the 3D comparison.
    </p>

    <div class="tiles">
      {tile("Mean diff F1", fmt(stats['diff_f1']),
            f"vs {fmt(stats['gpt'])} single-shot", accent=True)}
      {tile("Against the baseline", f"{w} W / {t_} T / {l} L",
            "per-task, on diff F1")}
      {tile("Mean volume F1", fmt(stats['volume_f1']), "occupied volume")}
      {tile("Mean chamfer", fmt(stats['chamfer']), "surface similarity")}
    </div>

    <figure class="figure" style="margin-top:2.4rem">
      <img src="assets/repo/metric_bar_facets.png" loading="lazy"
        alt="Bar chart comparing our method against the published baselines on
             chamfer similarity, volume F1 and difference F1.">
      <figcaption>Our method beside the published baselines on all three
        metrics. Rebuild with <code>scripts/make_metric_figure.py</code>.</figcaption>
    </figure>
    <figure class="figure">
      <img src="assets/repo/metric_mean_overall.png" loading="lazy"
        alt="Bar chart of the overall score, the mean of the three metrics,
             compared across methods.">
      <figcaption>The mean of the three metrics puts us ahead of every published
        model baseline, with the human expert still on top.</figcaption>
    </figure>

    <h2>Every task</h2>
    <div class="scroll-x">
      <table class="tabular" data-sortable>
        <thead><tr>
          <th class="sortable">Request</th>
          <th class="sortable">Difficulty</th>
          <th class="sortable num">Diff F1</th>
          <th class="sortable num">gpt-5.2</th>
          <th class="sortable num">2nd human</th>
          <th class="sortable num">Volume F1</th>
          <th class="sortable num">Chamfer</th>
          <th class="sortable num">Cost</th>
          <th>vs baseline</th>
        </tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>
    <p style="margin-top:1.6rem"><a href="results-detail.html">The results
      write-up — degenerate tasks, causal evidence for each fix, and the
      caveat on comparability →</a></p>
  </div>
</section>
"""
    return page("Results — UCONN CAD PACK", body, active="results.html",
                description="Per-task scores for all 48 neuralCAD-Edit tasks, "
                            "against the gpt-5.2 baseline and a second human.")


def build_presentation():
    body = """
<section class="section">
  <div class="wrap">
    <h1>Presentation and demo</h1>
    <p class="section-lede">The slide deck and the dashboard recording that
      accompany the submission.</p>
    <p><a class="btn btn-primary" href="assets/repo/UCONN-CAD-PACK.pdf">
      Open the slide deck (PDF)</a></p>
    <figure class="figure" style="margin-top:2rem">
      <img src="assets/repo/CADPACK.gif" loading="lazy"
        alt="Screen recording of the dashboard replaying one edit request.">
      <figcaption>The dashboard replaying one edit request over the pipeline.</figcaption>
    </figure>
  </div>
</section>
"""
    return page("Presentation — UCONN CAD PACK", body,
                description="Slide deck and dashboard demo for the UCONN CAD "
                            "PACK submission.")


def build_doc(name, title, source, subtitle, label, renderer):
    path = osp.join(REPO, source)
    if not osp.exists(path):
        return None
    with open(path) as f:
        text = strip_front_matter(f.read())

    renderer.reset()
    rendered = renderer.convert(rewrite_links(text, source))
    # The write-up supplies its own <h1>; the shell adds the subtitle and the
    # provenance line above it so every doc page opens the same way.
    body = f"""
<div class="wrap">
  <div class="doc">
    <article class="doc-body">
      <p class="doc-note">
        <strong>{html.escape(title)}.</strong> {html.escape(subtitle)}
        Rendered from <a href="{REPO_URL}/blob/main/{source}"><code>{label}</code></a>
        in the repository.
      </p>
      {rendered}
    </article>
    <nav class="toc" aria-label="On this page"><h4>On this page</h4><ul></ul></nav>
  </div>
</div>
"""
    return page(f"{title} — UCONN CAD PACK", body,
                active=name if any(name == h for h, _ in NAV) else "",
                description=subtitle,
                scripts=("mermaid-blocks.js",) if "```mermaid" in text else ())


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

# Files copied out of the repository into `assets/repo/` so the pages can point
# at them with a stable path regardless of where they live in the tree.
REPO_ASSETS = [
    "docs/architecture.svg",
    "docs/agents.svg",
    "figures/metric_bar_facets.png",
    "figures/metric_mean_overall.png",
    "Demo/CADPACK.gif",
    "presentation/UCONN-CAD-PACK.pdf",
]

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="7" fill="#0d2440"/>
<path d="M16 5.5 26 11v10L16 26.5 6 21V11z" fill="none" stroke="#63a8ff"
      stroke-width="2" stroke-linejoin="round"/>
<path d="M6 11l10 5.5L26 11M16 16.5v10" fill="none" stroke="#63a8ff"
      stroke-width="2" stroke-linejoin="round" opacity=".55"/>
</svg>
"""


def write(path, text):
    os.makedirs(osp.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true",
                        help="serve _site/ on localhost:8000 after building")
    args = parser.parse_args()

    tasks = load_tasks()
    stats = summarise(tasks)
    renderer = markdown_renderer()

    if osp.exists(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(osp.join(SITE, "assets"), osp.join(OUT, "assets"))

    for rel in REPO_ASSETS:
        src = osp.join(REPO, rel)
        if osp.exists(src):
            dst = osp.join(OUT, "assets", "repo", osp.basename(rel))
            os.makedirs(osp.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        else:
            print(f"  missing repo asset: {rel}")

    write(osp.join(OUT, "assets", "img", "favicon.svg"), FAVICON)
    # Pages runs Jekyll over anything it is handed unless told not to, and
    # Jekyll drops directories that start with an underscore.
    write(osp.join(OUT, ".nojekyll"), "")

    pages = 0
    write(osp.join(OUT, "index.html"), build_index(tasks, stats)); pages += 1
    write(osp.join(OUT, "gallery.html"), build_gallery(tasks, stats)); pages += 1
    write(osp.join(OUT, "results.html"), build_results(tasks, stats)); pages += 1
    write(osp.join(OUT, "presentation.html"), build_presentation()); pages += 1

    order = sorted(tasks, key=lambda t: -(t["scores"].get("diff_f1") or 0))
    for i, task in enumerate(order):
        neighbours = (order[i - 1] if i else None,
                      order[i + 1] if i + 1 < len(order) else None)
        write(osp.join(OUT, "tasks", task["id"] + ".html"),
              build_task(task, neighbours, stats))
        pages += 1

    for name, title, source, subtitle, label in DOCS:
        rendered = build_doc(name, title, source, subtitle, label, renderer)
        if rendered is None:
            print(f"  skipped {name}: {source} not found")
            continue
        write(osp.join(OUT, name), rendered)
        pages += 1

    total = sum(os.path.getsize(osp.join(d, n))
                for d, _, names in os.walk(OUT) for n in names)
    print(f"{pages} pages -> {osp.relpath(OUT, REPO)}/ ({total / 1e6:.1f} MB)")

    if args.serve:
        import http.server
        import socketserver
        os.chdir(OUT)
        with socketserver.TCPServer(("", 8000),
                                    http.server.SimpleHTTPRequestHandler) as httpd:
            print("serving http://localhost:8000  (ctrl-c to stop)")
            httpd.serve_forever()


if __name__ == "__main__":
    main()
