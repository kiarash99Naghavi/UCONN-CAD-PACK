"""Chart factories and design tokens for the dashboard.

Everything here takes plain Python data (lists, dicts) and returns a Plotly
figure or a style dict. Nothing here imports Dash, the benchmark, or open3d, so
`dashboard.py` can import it freely without a cycle and it can be exercised on
its own.

The palette is the validated categorical set: slot order is the colour-blind
safety mechanism, not decoration, so series are assigned slots in order and the
order is never shuffled per chart. Charts that put every pair on screen at once
(scatter) use at most the first three slots, which are the ones that clear the
all-pairs separation floor.
"""

import plotly.graph_objects as go

# --------------------------------------------------------------------------
# design tokens
# --------------------------------------------------------------------------
SURFACE = "#ffffff"
SURFACE_SUNK = "#f5f7fb"
INK = "#151a24"
INK_2 = "#5b6472"
INK_3 = "#8a93a3"
GRID = "#eceff5"
AXIS = "#d7dce6"

# categorical slots, in fixed order
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"
S4, S5, S6 = "#eda100", "#e87ba4", "#008300"
S7, S8 = "#4a3aa7", "#e34948"
SERIES = [S1, S2, S3, S4, S5, S6, S7, S8]

# one hue for "this is us", one recessive tone for everything we compare against
OURS_COLOR = "#2a78d6"
BASE_COLOR = "#b9c2d0"
GT_COLOR = "#1baf7a"

# status colours, reserved: never reused as a series colour
GOOD, WARN, BAD = "#0f7b52", "#b45309", "#c02626"

FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, "
        "Helvetica, Arial, sans-serif")

# difficulty is an ordered, three-valued dimension, so it gets the three
# all-pairs-safe slots and always in the same order
DIFF_COLOR = {"easy": S3, "medium": S1, "hard": S2}
DIFF_ORDER = ["easy", "medium", "hard"]


def score_color(v):
    """Traffic light for a 0..1 benchmark score."""
    if not isinstance(v, (int, float)):
        return INK_3
    return GOOD if v >= 0.6 else WARN if v >= 0.25 else BAD


def _base_layout(fig, title=None, height=320, legend=True, margin=None):
    fig.update_layout(
        title=(dict(text=title, font=dict(size=13.5, color=INK),
                    x=0, xanchor="left", y=0.97, pad=dict(b=8))
               if title else None),
        font=dict(family=FONT, size=12, color=INK_2),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        height=height,
        margin=margin or dict(l=8, r=14, t=44 if title else 14, b=8),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right",
                    x=1, font=dict(size=11, color=INK_2),
                    bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=AXIS,
                        font=dict(family=FONT, size=12, color=INK)),
        bargap=0.28, bargroupgap=0.12,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=AXIS,
                     ticks="outside", tickcolor=AXIS, ticklen=4,
                     tickfont=dict(size=11, color=INK_2))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False,
                     linecolor="rgba(0,0,0,0)", ticks="",
                     tickfont=dict(size=11, color=INK_2))
    return fig


def empty_fig(message, height=260):
    """A chart-shaped placeholder, so a missing dataset does not collapse the
    grid it sits in."""
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False,
                       font=dict(family=FONT, size=12.5, color=INK_3))
    fig.update_layout(paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
                      height=height, margin=dict(l=8, r=8, t=8, b=8),
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig


# --------------------------------------------------------------------------
# bars
# --------------------------------------------------------------------------
def ranked_bar(labels, values, colors=None, title=None, height=320,
               value_fmt="{:.3f}", xtitle=None, horizontal=True):
    """One measure across several entities, ranked.

    Horizontal by default: method names are long, and a horizontal bar reads
    them left to right instead of turning them 45 degrees. `colors` is the
    label -> colour map shared by every chart on the page, so an entity keeps
    its colour when the sort order moves it.
    """
    pairs = [(l, v) for l, v in zip(labels, values) if v is not None]
    if not pairs:
        return empty_fig("no scores yet", height)
    pairs.sort(key=lambda p: p[1])          # smallest first -> best on top
    labs = [p[0] for p in pairs]
    vals = [p[1] for p in pairs]
    colors = colors or {}
    cols = [colors.get(l, BASE_COLOR) for l in labs]
    texts = [value_fmt.format(v) for v in vals]

    fig = go.Figure(go.Bar(
        x=vals if horizontal else labs,
        y=labs if horizontal else vals,
        orientation="h" if horizontal else "v",
        marker=dict(color=cols, line=dict(width=0)),
        text=texts, textposition="outside",
        textfont=dict(size=11.5, color=INK_2, family=FONT),
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>" + (xtitle or "value")
                      + " %{x:.3f}<extra></extra>",
    ))
    _base_layout(fig, title, height, legend=False,
                 margin=dict(l=8, r=52, t=44 if title else 14, b=26))
    if horizontal:
        fig.update_xaxes(showgrid=True, gridcolor=GRID, range=[0, 1.06],
                         title=dict(text=xtitle, font=dict(size=11,
                                                           color=INK_3)))
        fig.update_yaxes(showgrid=False, tickfont=dict(size=11.5, color=INK))
    return fig


def grouped_metric_bar(entities, series, title=None, height=340,
                       ytitle="score (0 to 1)"):
    """x = entity, one bar group per entity, one colour per measure.

    `series` is an ordered list of (name, [value per entity]). Three measures
    is the normal case, which is exactly the count the first three palette
    slots are validated for.
    """
    if not entities or not series:
        return empty_fig("no scores for this task", height)
    fig = go.Figure()
    for i, (name, vals) in enumerate(series):
        fig.add_trace(go.Bar(
            name=name, x=entities, y=vals,
            marker=dict(color=SERIES[i], line=dict(width=2, color=SURFACE)),
            text=[f"{v:.2f}" if isinstance(v, (int, float)) else ""
                  for v in vals],
            textposition="outside", cliponaxis=False,
            textfont=dict(size=10, color=INK_3, family=FONT),
            hovertemplate="<b>%{x}</b><br>" + name
                          + " %{y:.3f}<extra></extra>",
        ))
    _base_layout(fig, title, height)
    fig.update_yaxes(range=[0, 1.12],
                     title=dict(text=ytitle, font=dict(size=11, color=INK_3)))
    fig.update_xaxes(tickfont=dict(size=11, color=INK))
    return fig


def grouped_by_difficulty(difficulties, series, title=None, height=330,
                          ytitle="mean diff F1"):
    """x = difficulty band, one bar per method. Method keeps its colour across
    every chart on the page, so the eye can follow one entity."""
    if not series:
        return empty_fig("no per-difficulty scores yet", height)
    fig = go.Figure()
    for name, vals, colour in series:
        fig.add_trace(go.Bar(
            name=name, x=difficulties, y=vals,
            marker=dict(color=colour, line=dict(width=2, color=SURFACE)),
            text=[f"{v:.2f}" if isinstance(v, (int, float)) else ""
                  for v in vals],
            textposition="outside", cliponaxis=False,
            textfont=dict(size=9.5, color=INK_3, family=FONT),
            hovertemplate="<b>%{x}</b><br>" + name
                          + " diff F1 %{y:.3f}<extra></extra>",
        ))
    _base_layout(fig, title, height)
    fig.update_yaxes(range=[0, 1.1],
                     title=dict(text=ytitle,
                                font=dict(size=11, color=INK_3)))
    return fig


# --------------------------------------------------------------------------
# scatter
# --------------------------------------------------------------------------
def parity_scatter(points, title=None, height=400,
                   xlabel="best published model on that task, diff F1",
                   ylabel="our pipeline, diff F1"):
    """One dot per task: us on y, the field on x, with the break-even line.

    Above the line we beat the field on that task, below it we lose. Colour is
    difficulty, which is three categories, so every pair on screen is one of
    the three all-pairs-safe slots.
    """
    if not points:
        return empty_fig("run a few tasks to fill this in", height)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[0, 1], y=[0, 1], mode="lines", showlegend=False, hoverinfo="skip",
        line=dict(color=AXIS, width=2, dash="dot")))
    fig.add_annotation(x=0.30, y=0.90, text="we win", showarrow=False,
                       font=dict(family=FONT, size=11, color=INK_3))
    fig.add_annotation(x=0.90, y=0.28, text="we lose", showarrow=False,
                       font=dict(family=FONT, size=11, color=INK_3))
    for d in DIFF_ORDER:
        sub = [p for p in points if p.get("difficulty") == d]
        if not sub:
            continue
        fig.add_trace(go.Scatter(
            name=d, x=[p["x"] for p in sub], y=[p["y"] for p in sub],
            mode="markers",
            marker=dict(size=13, color=DIFF_COLOR[d],
                        line=dict(width=2, color=SURFACE)),
            customdata=[p.get("label", "") for p in sub],
            hovertemplate="<b>%{customdata}</b><br>baseline %{x:.3f}"
                          "<br>ours %{y:.3f}<extra></extra>",
        ))
    _base_layout(fig, title, height)
    fig.update_xaxes(range=[-0.04, 1.04], showgrid=True, gridcolor=GRID,
                     title=dict(text=xlabel, font=dict(size=11, color=INK_3)))
    fig.update_yaxes(range=[-0.04, 1.04],
                     title=dict(text=ylabel, font=dict(size=11, color=INK_3)))
    return fig




def _rgba(hex_colour, alpha):
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def violin_group(rows, title=None, height=430, highlight=None,
                 ytitle="diff F1 per task"):
    """Every method as a narrow violin on one shared axis, best first.

    Stacked rows made the reader hold one shape in memory while looking at the
    next. Standing the violins side by side on a single scale turns the
    comparison into a height comparison, which needs no memory at all: the
    body that sits higher is the method that scores higher. Narrow on purpose,
    because the width of a violin carries no meaning here, only its vertical
    extent does, and a fat body invites reading area as importance.
    """
    if not rows:
        return empty_fig("no scores yet", height)

    # best first, so the panel reads as a ladder. Colour is per method and set
    # by the caller, so reordering cannot repaint anything.
    ordered = sorted(
        [(n, [x for x in v if isinstance(x, (int, float))], c)
         for n, v, c in rows],
        key=lambda r: -(sum(r[1]) / len(r[1]) if r[1] else 0))

    fig = go.Figure()
    fig.add_hline(y=0.6, line=dict(color=AXIS, width=1.4, dash="dot"))
    fig.add_annotation(x=1, y=0.6, xref="paper", yanchor="bottom",
                       xanchor="right", text="solved outright", showarrow=False,
                       font=dict(family=FONT, size=10.5, color=INK_3))

    for name, vals, colour in ordered:
        if not vals:
            continue
        lead = name == highlight
        fig.add_trace(go.Violin(
            y=vals, x=[name] * len(vals), name=name, width=0.62,
            fillcolor=_rgba(colour, 0.34 if lead else 0.22),
            line=dict(color=colour, width=2.4 if lead else 1.3),
            # the kernel is clipped to the metric's range: diff F1 cannot fall
            # outside 0 to 1, and an unclipped estimate shows density there
            # purely as a smoothing artifact
            span=[0, 1], spanmode="manual", bandwidth=0.045,
            box=dict(visible=True, width=0.16, fillcolor=SURFACE,
                     line=dict(color=INK_2, width=1.1)),
            meanline=dict(visible=True, color=INK, width=1.6),
            points="all", pointpos=0, jitter=0.5,
            marker=dict(size=3.4, color=INK_2, opacity=0.4,
                        line=dict(width=0)),
            showlegend=False, hoveron="violins+points+kde",
            hovertemplate="<b>" + name
                          + "</b><br>diff F1 %{y:.3f}<extra></extra>",
        ))
    _base_layout(fig, title, height, legend=False,
                 margin=dict(l=8, r=16, t=48 if title else 18, b=34))
    fig.update_layout(violingap=0.35, violingroupgap=0)
    fig.update_xaxes(showgrid=False, tickfont=dict(size=12, color=INK))
    # the axis title stays short: rotated ninety degrees, a clause about what
    # the box and the mean line are is unreadable, and the caption says it
    fig.update_yaxes(range=[-0.04, 1.06], showgrid=True, gridcolor=GRID,
                     title=dict(text=ytitle,
                                font=dict(size=11, color=INK_3)))
    return fig






def cost_density(points, title=None, height=420,
                 xlabel="spend on the task (US dollars)", ylabel="diff F1",
                 xprefix="$"):
    """One density panel per difficulty: where a task of that band lands.

    A single bubble cloud put three populations on one pair of axes and left
    the reader to separate them by colour, which is exactly the job a small
    multiple does better. Faceting by difficulty makes the comparison a
    comparison of shapes: easy piles up cheap and high, hard spreads right and
    sits low. The filled contours are a binned density, so they say where
    tasks of that band concentrate; every task is still drawn as a point on
    top, because sixteen samples is not enough density to be believed on its
    own.
    """
    from plotly.subplots import make_subplots

    pts = [p for p in points
           if isinstance(p.get("x"), (int, float))
           and isinstance(p.get("y"), (int, float))]
    if not pts:
        return empty_fig("no finished runs yet", height)

    bands = [d for d in DIFF_ORDER if any(p.get("difficulty") == d
                                          for p in pts)]
    counts = {d: sum(1 for p in pts if p.get("difficulty") == d)
              for d in bands}
    fig = make_subplots(
        rows=1, cols=len(bands), shared_yaxes=True, horizontal_spacing=0.045,
        subplot_titles=[f"{d}  ·  {counts[d]} tasks" for d in bands])

    xmax = max(p["x"] for p in pts) * 1.06 or 1.0
    for i, d in enumerate(bands, start=1):
        sub = [p for p in pts if p.get("difficulty") == d]
        colour = DIFF_COLOR[d]
        fig.add_trace(go.Histogram2dContour(
            x=[p["x"] for p in sub], y=[p["y"] for p in sub],
            colorscale=[[0, "rgba(0,0,0,0)"], [0.35, _rgba(colour, 0.16)],
                        [1, _rgba(colour, 0.62)]],
            # coarse bins on purpose: a fine grid over sixteen points draws
            # islands around single tasks and calls them structure
            nbinsx=5, nbinsy=5, ncontours=6,
            contours=dict(coloring="fill", showlines=False),
            showscale=False, hoverinfo="skip",
        ), row=1, col=i)
        fig.add_trace(go.Scatter(
            x=[p["x"] for p in sub], y=[p["y"] for p in sub], mode="markers",
            marker=dict(size=8, color=colour, opacity=0.9,
                        line=dict(width=1.4, color=SURFACE)),
            customdata=[p.get("label", "") for p in sub],
            hovertemplate="<b>%{customdata}</b><br>" + xprefix
                          + "%{x:,.3f}<br>diff F1 %{y:.3f}<extra></extra>",
            showlegend=False,
        ), row=1, col=i)

    _base_layout(fig, title, height, legend=False,
                 margin=dict(l=8, r=14, t=62 if title else 34, b=44))
    fig.update_xaxes(range=[0, xmax], showgrid=True, gridcolor=GRID,
                     tickprefix=xprefix, zeroline=False, linecolor=AXIS,
                     ticks="outside", tickcolor=AXIS, ticklen=4,
                     tickfont=dict(size=10.5, color=INK_2),
                     title=dict(text=xlabel, font=dict(size=10.5,
                                                       color=INK_3)))
    fig.update_yaxes(range=[-0.06, 1.06], showgrid=True, gridcolor=GRID,
                     tickfont=dict(size=10.5, color=INK_2))
    fig.update_yaxes(title=dict(text=ylabel, font=dict(size=11, color=INK_3)),
                     row=1, col=1)
    for ann in fig.layout.annotations:
        ann.font = dict(size=11.5, color=INK, family=FONT)
    return fig


