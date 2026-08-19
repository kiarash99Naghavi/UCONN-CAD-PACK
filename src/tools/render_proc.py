"""Subprocess entry point for rendering and STL export.

OCC's offscreen viewer on macOS uses a Cocoa window, which may only be driven
from a process's main thread. The dashboard runs the pipeline in a background
thread, where every render fails with "Caught an unknown exception!" — silently,
since a failed view is not fatal. That left the QA agent with zero images while
still reporting success, which is the worst kind of bug: the six-view gate
appeared to work and did nothing.

Rendering in a child process makes this thread-independent, and matches how the
benchmark's own src/scripts_preprocess/cadquery_convert.py operates.
"""

import argparse
import json
import os
import sys
import traceback

# Faces with no colour of their own. Must match geometry.GRAY_RGB — the legend
# printed next to the picture says "all other faces: gray", and this is it.
_GRAY = (0.75, 0.75, 0.75)


def _color_items(shape, groups):
    """[(shape, rgb)] — the solid's faces split into the plan's colour groups.

    Face order is `shape.Faces()`, which is exactly the enumeration
    geometry.py's `cylindrical_face_groups` / `planar_faces` index with (both
    walk `enumerate(importStep(...).val().Faces())`), so a face_idx from the
    geometry index and a face_idx here are the same face. Nothing else in this
    pipeline guarantees that, so the two enumerations must not drift apart.

    Faces are painted, not solids: every face of the part is drawn exactly once,
    either in its group's colour or in the leftover grey. That is what keeps the
    silhouette complete and avoids the z-fighting speckle a colour-over-solid
    overlay risks — measured on the rotor both look identical, and this one
    cannot go wrong.

    Indices are validated against the real face count and de-duplicated: a stale
    plan (rendered against a different STEP) must lose its colours, not crash
    the render.
    """
    import cadquery as cq

    faces = shape.Faces()
    n = len(faces)
    used = set()
    items = []
    for g in groups:
        ids = [i for i in g.get("face_ids") or []
               if isinstance(i, int) and 0 <= i < n and i not in used]
        if not ids:
            continue
        used.update(ids)
        rgb = g.get("rgb") or list(_GRAY)
        items.append((cq.Compound.makeCompound([faces[i] for i in ids]),
                      tuple(float(v) for v in rgb[:3])))
    rest = [f for i, f in enumerate(faces) if i not in used]
    if rest:
        items.append((cq.Compound.makeCompound(rest), _GRAY))
    return items


def _render_colored(items, png_path, proj, width, height):
    """`render_to_png` for a LIST of (shape, rgb) instead of one grey shape.

    The benchmark's own `render_to_png` hard-codes one AIS_Shape at colour
    (0.6, 0.6, 0.6), so it cannot draw a coloured assembly at all: on macOS and
    Linux it takes the OCP/V3d branch, where a `cq.Assembly` is not even a valid
    AIS_Shape argument (verified: TypeError). Only the Windows branch goes
    through `cadquery.vis.show`, which does understand an Assembly and its
    per-child colours — so that is the one platform where the plan is handed
    back to the benchmark's renderer, and everywhere else this mirrors its V3d
    setup exactly (same lights, same white background, same black 2 px face
    boundaries, same FitAll) with one AIS_Shape per colour group.
    """
    if sys.platform == "win32":
        import cadquery as cq
        from src.utils.cadquery_rendering import render_to_png

        asm = cq.Assembly()
        for k, (shp, rgb) in enumerate(items):
            asm.add(shp, name=f"g{k}", color=cq.Color(*rgb))
        render_to_png(asm, png_path, proj=proj, width=width, height=height)
        return

    from OCP.AIS import AIS_InteractiveContext, AIS_Shape, AIS_Shaded
    from OCP.Aspect import Aspect_DisplayConnection, Aspect_TypeOfLine
    from OCP.Graphic3d import Graphic3d_MaterialAspect
    from OCP.OpenGl import OpenGl_GraphicDriver
    from OCP.Prs3d import Prs3d_LineAspect
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB, Quantity_NOC_BLACK
    from OCP.V3d import V3d_Viewer

    display_connection = Aspect_DisplayConnection()
    driver = OpenGl_GraphicDriver(display_connection)
    viewer = V3d_Viewer(driver)
    viewer.SetDefaultLights()
    viewer.SetLightOn()

    context = AIS_InteractiveContext(viewer)
    view = viewer.CreateView()

    if sys.platform == "linux":
        from OCP.Xw import Xw_Window
        window = Xw_Window(display_connection, "offscreen", 0, 0, width, height)
    else:
        import AppKit
        AppKit.NSApplication.sharedApplication()
        from OCP.Cocoa import Cocoa_Window
        window = Cocoa_Window("offscreen", 0, 0, width, height)

    view.SetWindow(window)
    if not window.IsMapped():
        window.Map()
    view.SetBackgroundColor(Quantity_Color(1.0, 1.0, 1.0, Quantity_TOC_RGB))

    for shp, rgb in items:
        wrapped = shp.wrapped if hasattr(shp, "wrapped") else shp
        ais = AIS_Shape(wrapped)
        mat = Graphic3d_MaterialAspect()
        mat.SetSpecularColor(Quantity_Color(0.15, 0.15, 0.15, Quantity_TOC_RGB))
        mat.SetShininess(0.3)
        ais.SetMaterial(mat)
        ais.SetColor(Quantity_Color(rgb[0], rgb[1], rgb[2], Quantity_TOC_RGB))
        drawer = ais.Attributes()
        drawer.SetFaceBoundaryAspect(Prs3d_LineAspect(
            Quantity_Color(Quantity_NOC_BLACK),
            Aspect_TypeOfLine.Aspect_TOL_SOLID, 2.0))
        drawer.SetFaceBoundaryDraw(True)
        # update=False: one redraw for the whole scene below, not one per group.
        context.Display(ais, AIS_Shaded, 0, False)

    view.SetProj(float(proj[0]), float(proj[1]), float(proj[2]))
    view.FitAll()
    view.Redraw()
    view.Dump(str(png_path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True)
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--stem", default="tmp")
    # "ALL" = every projection; "" (or --views omitted with NONE) = no renders,
    # which is what a pure STL export wants.
    ap.add_argument("--views", default="ALL")
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--stl", default="")
    # Path to a geometry.color_plan() JSON. Absent -> today's plain grey render,
    # unchanged down to the call it makes.
    ap.add_argument("--colors", default="")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    out = {"views": {}, "stl": None, "errors": []}

    try:
        import cadquery as cq
        from src.utils.cadquery_rendering import VIEW_PROJECTIONS, render_to_png

        wp = cq.importers.importStep(str(a.step))
        shape = wp.val()

        if a.stl:
            try:
                # DEFAULT tolerance on purpose — the benchmark's own dataset
                # STLs were exported with `exporters.export(wp, path, STL)`
                # (scripts_preprocess/cadquery_convert.py), i.e. tolerance 0.1.
                # Exporting finer (0.01) tessellates curved walls differently
                # from the reference meshes, and on the coffeepot that alone
                # flipped 82 voxels (a cavity-fill blob among them): the
                # UNEDITED input scored diff F1 0.0 against a ground truth
                # whose own diff is empty. Same call, same tessellation, no
                # phantom diff.
                cq.exporters.export(wp, a.stl, cq.exporters.ExportTypes.STL)
                if os.path.exists(a.stl):
                    out["stl"] = a.stl
            except Exception as e:
                out["errors"].append(f"stl: {e}")

        # The face split is done ONCE, not per view: it costs a Faces() walk and
        # a compound per group, and all seven views draw the same scene.
        items = None
        if a.colors:
            try:
                with open(a.colors) as fh:
                    groups = [g for g in json.load(fh) if g.get("face_ids")]
                items = _color_items(shape, groups) if groups else None
            except Exception as e:
                out["errors"].append(f"colors: {e}")

        if a.views.strip().upper() == "ALL":
            views = list(VIEW_PROJECTIONS)
        else:
            views = [v for v in a.views.split(",") if v.strip()]
        for v in views:
            proj = VIEW_PROJECTIONS.get(v)
            if proj is None:
                continue
            png = os.path.join(a.out_dir, f"{a.stem}_{v}.png")
            try:
                if items:
                    # A grey picture still shows the shape; no picture shows
                    # nothing. So a colouring failure degrades per view.
                    try:
                        _render_colored(items, png, proj, a.size, a.size)
                    except Exception as e:
                        out["errors"].append(f"{v} color: {e}")
                        render_to_png(shape, png, proj=proj, width=a.size,
                                      height=a.size)
                else:
                    render_to_png(shape, png, proj=proj, width=a.size,
                                  height=a.size)
                if os.path.exists(png):
                    out["views"][v] = png
            except Exception as e:
                out["errors"].append(f"{v}: {e}")
    except Exception as e:
        out["errors"].append(f"fatal: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)

    print("RENDER: " + json.dumps(out))


if __name__ == "__main__":
    main()
