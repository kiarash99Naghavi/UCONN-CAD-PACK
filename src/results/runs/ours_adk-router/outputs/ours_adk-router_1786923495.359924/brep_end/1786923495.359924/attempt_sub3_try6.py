def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # -----------------------------
    # Helpers
    # -----------------------------
    def vstr(v):
        return f"[{v.x:.3f},{v.y:.3f},{v.z:.3f}]"

    def unit(v):
        v = cq.Vector(v.x, v.y, v.z)
        L = v.Length
        if L < 1e-12:
            return cq.Vector(0, 0, 0)
        return v.multiply(1.0 / L)

    def dot(a, b):
        return a.x * b.x + a.y * b.y + a.z * b.z

    def tool_for_edge(solid, edge, n_broad_out, axis, R, trim_each_end=0.5):
        """Build a *corner-only* shaving tool = (R x R prism) - (R-cylinder), swept along the edge direction.
        The remaining boundary after cut is an exact R circular arc tangent to broad and side tangent planes.
        """

        p0 = cq.Vector(edge.startPoint().x, edge.startPoint().y, edge.startPoint().z)
        p1 = cq.Vector(edge.endPoint().x, edge.endPoint().y, edge.endPoint().z)

        # order along axis
        t0 = dot(p0, axis)
        t1 = dot(p1, axis)
        if t1 < t0:
            p0, p1 = p1, p0
            t0, t1 = t1, t0

        # direction along the edge, anchored to axis
        d_edge = unit(p1.sub(p0))
        if dot(d_edge, axis) < 0:
            d_edge = d_edge.multiply(-1)

        full_len = (p1.sub(p0)).Length
        if full_len <= 2 * trim_each_end + 1.0:
            trim_each_end = max(0.1, 0.5 * (full_len - 1.0))

        p0t = p0.add(d_edge.multiply(trim_each_end))
        p1t = p1.add(d_edge.multiply(-trim_each_end))
        L = (p1t.sub(p0t)).Length
        if L <= 0.5:
            return None

        pm = cq.Vector(edge.Center().x, edge.Center().y, edge.Center().z)
        c_s = cq.Vector(solid.Center().x, solid.Center().y, solid.Center().z)

        # side normal guess from broad normal and axis (no kernel fillet)
        n_side_guess = unit(axis.cross(n_broad_out))
        if n_side_guess.Length < 1e-9:
            # axis parallel to broad normal (shouldn't happen here)
            n_side_guess = cq.Vector(1, 0, 0)

        # choose sign so it points outward (away from solid center)
        if dot(n_side_guess, pm.sub(c_s)) < 0:
            n_side_out = n_side_guess.multiply(-1)
        else:
            n_side_out = n_side_guess

        # We want sketch +X and +Y to go *into* the solid from the sharp edge:
        # xDir ~ (-n_side_out), yDir ~ (-n_broad_out)
        # But because Plane defines yDir from (normal x xDir), we will try both xDir signs
        # and pick the one with the larger intersection volume with the target solid.

        def build_with_xdir(xdir):
            xdir = unit(xdir)
            # ensure yDir aligns with -n_broad_out (flip if needed)
            ydir = unit(d_edge.cross(xdir))
            if dot(ydir, n_broad_out.multiply(-1)) < 0:
                xdir = xdir.multiply(-1)
                ydir = unit(d_edge.cross(xdir))

            plane0 = cq.Plane(origin=(p0t.x, p0t.y, p0t.z), normal=(d_edge.x, d_edge.y, d_edge.z), xDir=(xdir.x, xdir.y, xdir.z))

            # R x R prism from the sharp edge line (origin), extending into the solid along +x,+y
            prism = (
                cq.Workplane(plane0)
                .moveTo(R / 2.0, R / 2.0)
                .rect(R, R, centered=True)
                .extrude(L)
                .val()
            )

            # Cylinder centered at (R,R) in that local corner square; subtracting from prism
            # yields (square - quarter circle) cross-section.
            c0 = p0t.add(xdir.multiply(R)).add(ydir.multiply(R))
            cyl = cq.Solid.makeCylinder(R, L, pnt=c0, dir=d_edge)
            tool = prism.cut(cyl)

            # measure intersection with the solid to validate orientation
            inter = solid.intersect(tool)
            try:
                v_tool = tool.Volume()
            except Exception:
                v_tool = float("nan")
            try:
                v_inter = inter.Volume()
            except Exception:
                v_inter = 0.0

            return tool, xdir, ydir, v_tool, v_inter

        # try both xDir polarities (robust against sign mistakes)
        tool_a, x_a, y_a, vta, via = build_with_xdir(n_side_out.multiply(-1))
        tool_b, x_b, y_b, vtb, vib = build_with_xdir(n_side_out)  # flipped

        if vib > via:
            tool, xdir, ydir, vt, vi = tool_b, x_b, y_b, vtb, vib
            pick = "B"
        else:
            tool, xdir, ydir, vt, vi = tool_a, x_a, y_a, vta, via
            pick = "A"

        print(
            f"TOOL BUILT[{pick}]: edge_len={full_len:.3f} trimmed_len={L:.3f}  "
            f"p0t={vstr(p0t)}  d_edge={vstr(d_edge)}  n_broad_out={vstr(n_broad_out)}  "
            f"xdir(in)={vstr(xdir)}  ydir(in)={vstr(ydir)}  "
            f"V_tool={vt:.3f}  V_intersect={vi:.3f}  (ratio={(vi / vt) if (vt and vt>1e-9) else 0.0:.3f})"
        )

        # If tool barely intersects, it's likely outside; still return (we'll see cut volume).
        return tool

    # -----------------------------
    # Identify solids and target s3
    # -----------------------------
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids from imported STEP")
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        print(
            f"  solid s{i}: vol={s.Volume():.3f}  bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})  lens=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    # Find s3 by its bbox (from prompt)
    s3_idx = None
    for i, s in enumerate(solids):
        bb = s.BoundingBox()
        if (
            abs(bb.xmin - (-173.821)) < 0.5
            and abs(bb.xmax - (173.821)) < 0.5
            and abs(bb.ymin - (0.0)) < 0.5
            and abs(bb.ymax - (12.7)) < 0.5
            and abs(bb.zmin - (-101.355)) < 0.5
            and abs(bb.zmax - (101.355)) < 0.5
        ):
            s3_idx = i
            break
    if s3_idx is None:
        # fallback: widest in X with y 0..12.7 and z about 202.709
        cands = []
        for i, s in enumerate(solids):
            bb = s.BoundingBox()
            if abs(bb.ylen - 12.7) < 1.0 and bb.xlen > 300 and abs(bb.zlen - 202.709) < 5.0:
                cands.append((bb.xlen, i))
        cands.sort(reverse=True)
        s3_idx = cands[0][1] if cands else 0

    s3 = solids[s3_idx]
    bb3_before = s3.BoundingBox()
    v3_before = s3.Volume()
    print(
        f"USING: solid s{s3_idx} as s3 (edit target)  bbox_before=({bb3_before.xmin:.3f},{bb3_before.ymin:.3f},{bb3_before.zmin:.3f})..({bb3_before.xmax:.3f},{bb3_before.ymax:.3f},{bb3_before.zmax:.3f})  vol_before={v3_before:.3f}"
    )

    # -----------------------------
    # Anchors from the prompt
    # -----------------------------
    axis = unit(cq.Vector(0.881, 0.0, -0.473))
    R = 1.27
    print(f"ANCHOR: s3 longitudinal axis={vstr(axis)}")
    print(f"RADIUS: target corner rounding R={R:.3f} mm (explicit swept-boolean shaving tools; NO kernel fillet/chamfer)")

    # The four broad planar arm faces (global indices from geometry index)
    faces = base.Faces()
    face_ids = [124, 126, 138, 140]
    named_centers = [
        cq.Vector(106.007, 12.7, -56.901),
        cq.Vector(106.007, 0.0, -56.901),
        cq.Vector(-106.007, 12.7, 56.901),
        cq.Vector(-106.007, 0.0, 56.901),
    ]

    broad_faces = []
    for fi, nc in zip(face_ids, named_centers):
        if fi >= len(faces):
            print(f"SELECTED: 0 faces for global face_idx #{fi} (out of range)")
            continue
        f = faces[fi]
        c = cq.Vector(f.Center().x, f.Center().y, f.Center().z)
        bb = f.BoundingBox()
        gt = f.geomType()
        n_txt = "(n/a)"
        if gt == "PLANE":
            n = f.normalAt()
            n_txt = vstr(n)
        print(
            f"SELECTED: 1 face for global face_idx #{fi}  type={gt}  center={vstr(c)}  area={f.Area():.3f}  normal={n_txt}  "
            f"bbox=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})..({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})  "
            f"delta_to_named_center={(c.sub(nc)).Length:.3f}"
        )

        # ensure it belongs to s3 (by checking its center is within s3 bbox)
        belongs = (
            bb3_before.xmin - 1e-6 <= c.x <= bb3_before.xmax + 1e-6
            and bb3_before.ymin - 1e-6 <= c.y <= bb3_before.ymax + 1e-6
            and bb3_before.zmin - 1e-6 <= c.z <= bb3_before.zmax + 1e-6
        )
        print(f"CHECK: global face_idx #{fi} belongs_to_s3={belongs}")
        broad_faces.append(f)

    print(f"SELECTED: {len(broad_faces)} broad planar faces (expected 4) for building paths")

    # -----------------------------
    # Identify longitudinal path edges from each face outer boundary
    # and build corner-only shaving tools (NO fillet/chamfer)
    # -----------------------------
    trim_each_end = 0.5
    print(f"TRIM: each sweep tool shortened by {trim_each_end:.2f} mm at both ends to avoid modifying shoulder/tip faces")

    path_edges = []
    for j, f in enumerate(broad_faces):
        if f.geomType() != "PLANE":
            print(f"SELECTED: 0 edges for face[{j}] (not planar); skipping")
            continue

        n_broad_out = unit(f.normalAt())
        ow = f.outerWire()
        edges = ow.Edges()
        print(f"SELECTED: {len(edges)} edges from outerWire of broad face[{j}] for path finding")

        cands = []
        for e in edges:
            try:
                L = e.Length()
            except Exception:
                continue
            # direction from endpoints
            p0 = cq.Vector(e.startPoint().x, e.startPoint().y, e.startPoint().z)
            p1 = cq.Vector(e.endPoint().x, e.endPoint().y, e.endPoint().z)
            d = unit(p1.sub(p0))
            align = abs(dot(d, axis))
            if L > 40.0 and align > 0.97:
                pm = cq.Vector(e.Center().x, e.Center().y, e.Center().z)
                cands.append((L, align, pm, e))

        print(f"SELECTED: {len(cands)} longitudinal candidate edges on face[{j}] (L>40, |dot(axis)|>0.97)")
        cands_sorted = sorted(cands, key=lambda t: -t[0])
        for k, (L, align, pm, _) in enumerate(cands_sorted[:6]):
            print(f"  cand[{k}] L={L:.3f}  align={align:.4f}  mid={vstr(pm)}")

        # IMPORTANT: round ALL applicable long corners -> take up to 2 longitudinal edges per broad face
        # (these are the two long outer boundaries of the broad face).
        take = cands_sorted[:2]
        print(f"SELECTED: {len(take)} path edges from face[{j}] (expected 2 per face) for longitudinal corner shaving")
        for (L, align, pm, e) in take:
            path_edges.append((j, f, n_broad_out, e, L, align, pm))

    print(f"SELECTED: {len(path_edges)} total path edges (expected 8 = 2 per each of 4 faces)")
    for (j, _, _, e, L, align, pm) in path_edges:
        print(f"  PATH: from face[{j}]  L={L:.3f} align={align:.4f} mid={vstr(pm)}")

    tools = []
    for (j, f, n_broad_out, e, L, align, pm) in path_edges:
        tool = tool_for_edge(s3, e, n_broad_out, axis, R, trim_each_end=trim_each_end)
        if tool is None:
            print(f"WARNING: tool build failed for face[{j}] path; skipping")
            continue
        tools.append(tool)

    print(f"SELECTED: {len(tools)} shaving tools built (expected 8)")

    # -----------------------------
    # Apply tools to s3 ONLY
    # -----------------------------
    s3_edited = s3
    removed_total = 0.0
    for i, tool in enumerate(tools):
        try:
            # measure actual removal from intersection (more reliable than before/after delta when booleans are complex)
            inter = s3_edited.intersect(tool)
            v_inter = inter.Volume() if inter is not None else 0.0
        except Exception:
            v_inter = 0.0

        vb = s3_edited.Volume()
        try:
            s3_edited = s3_edited.cut(tool)
        except Exception as e:
            print(f"ERROR: tool[{i}] cut failed: {e}")
            # keep whatever succeeded so far
            continue
        va = s3_edited.Volume()
        dv = vb - va
        removed_total += dv
        print(f"CUT: applied tool[{i}]  intersect_volume={v_inter:.3f} mm^3  removed_volume={dv:.3f} mm^3")

    bb3_after = s3_edited.BoundingBox()
    v3_after = s3_edited.Volume()
    print(f"CUT SUMMARY: total_removed_volume={removed_total:.3f} mm^3  (s3 vol delta={v3_after - v3_before:+.3f} mm^3)")
    print(
        f"s3 bbox AFTER: ({bb3_after.xmin:.3f},{bb3_after.ymin:.3f},{bb3_after.zmin:.3f})..({bb3_after.xmax:.3f},{bb3_after.ymax:.3f},{bb3_after.zmax:.3f})"
    )
    print(
        "s3 bbox delta: "
        f"dxmin={bb3_after.xmin - bb3_before.xmin:+.3f}, dxmax={bb3_after.xmax - bb3_before.xmax:+.3f}, "
        f"dymin={bb3_after.ymin - bb3_before.ymin:+.3f}, dymax={bb3_after.ymax - bb3_before.ymax:+.3f}, "
        f"dzmin={bb3_after.zmin - bb3_before.zmin:+.3f}, dzmax={bb3_after.zmax - bb3_before.zmax:+.3f}"
    )

    # -----------------------------
    # Verification: print measured radii of ~1.27mm from resulting cylindrical faces aligned to blade axis
    # -----------------------------
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder

        cyl_hits = []
        for f in s3_edited.Faces():
            if f.geomType() != "CYLINDER":
                continue
            ad = BRepAdaptor_Surface(f.wrapped, True)
            if ad.GetType() != GeomAbs_Cylinder:
                continue
            cyl = ad.Cylinder()
            r = float(cyl.Radius())
            d = cyl.Axis().Direction()
            ax = unit(cq.Vector(d.X(), d.Y(), d.Z()))
            align = abs(dot(ax, axis))
            if abs(r - R) <= 0.02 and align > 0.98:
                c = cq.Vector(f.Center().x, f.Center().y, f.Center().z)
                cyl_hits.append((r, align, c))

        print(f"SELECTED: {len(cyl_hits)} cylindrical faces on s3_edited with radius ~ {R:.2f} mm and axis aligned to blade axis")
        for k, (r, align, c) in enumerate(sorted(cyl_hits, key=lambda t: -t[1])[:40]):
            print(f"  R_CHECK[{k}]: r={r:.4f} mm  axis_align={align:.4f}  face_center={vstr(c)}")
    except Exception as e:
        print(f"WARNING: cylinder-radius verification failed: {e}")

    # -----------------------------
    # Recompound: preserve s0,s1,s2 unchanged and keep solid order unchanged
    # -----------------------------
    vols_before = [s.Volume() for s in solids]
    out_solids = list(solids)
    out_solids[s3_idx] = s3_edited
    vols_after = [out_solids[i].Volume() for i in range(len(out_solids))]
    for i in range(len(out_solids)):
        dv = vols_after[i] - vols_before[i]
        tag = "(edited s3)" if i == s3_idx else "(must be unchanged)"
        print(f"VOLUME CHECK: solid index {i} {tag}  before={vols_before[i]:.3f} after={vols_after[i]:.3f}  delta={dv:+.3f}")

    out = cq.Compound.makeCompound(out_solids)
    return out