def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    solids = base.Solids()
    print(f"INFO: imported solids={len(solids)} faces={len(base.Faces())} edges={len(base.Edges())} verts={len(base.Vertices())}")

    # We expect a single solid; if not, only edit solid[0] and recombine.
    solid0 = solids[0]

    # --- Numbers named by the sub-goal ---
    cx, cz = 0.0, 10.0
    axis = cq.Vector(0.0, 1.0, 0.0)
    y0, y1 = 0.0, 15.0
    r_vertex = 10.0
    print("TARGET NUMBERS:")
    print(f"  center (X,Z)=({cx},{cz})  vertices on r={r_vertex}")
    print(f"  axis={tuple(axis.toTuple())}  span Y={y0}..{y1}")
    print("  clocking: one vertex toward world +Z (approx at (X,Z)=(0,20))")

    # --- Identify (current) edges likely belonging to this feature by true circle center/radius ---
    # QA warning: mouths often are partial arcs, so do NOT use Edge.Center() (centroid). Use true circle center.
    edges = base.Edges()
    circle_like = []
    circle_like_near = []

    try:
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_CurveType
        have_ocp = True
    except Exception as e:
        have_ocp = False
        print(f"WARNING: OCP adaptor import failed; circle-center detection will be less reliable: {e}")

    for i, e in enumerate(edges):
        try:
            gt = e.geomType()
        except Exception:
            continue
        if gt != "CIRCLE":
            continue
        r = None
        ctrue = None
        if have_ocp:
            try:
                adp = BRepAdaptor_Curve(e.wrapped)
                if adp.GetType() == GeomAbs_CurveType.GeomAbs_Circle:
                    circ = adp.Circle()
                    loc = circ.Location()
                    ctrue = (loc.X(), loc.Y(), loc.Z())
                    r = circ.Radius()
            except Exception:
                pass
        if r is None:
            try:
                r = e.radius()
            except Exception:
                continue
        circle_like.append((i, r, ctrue))
        if ctrue is not None:
            if abs(r - r_vertex) < 0.2 or abs(r - (r_vertex + 0.02)) < 0.25:
                if abs(ctrue[0] - cx) < 0.25 and abs(ctrue[2] - cz) < 0.25 and (abs(ctrue[1] - y0) < 0.25 or abs(ctrue[1] - y1) < 0.25):
                    circle_like_near.append((i, r, ctrue))

    print(f"SELECTED: {len(circle_like)} circular edges total (geomType==CIRCLE)")
    print(f"SELECTED: {len(circle_like_near)} circular edges with true center near (0,{{0 or 15}},10) and r~10 for target-feature diagnostics")
    if circle_like_near:
        print("  matched edge indices (idx, r, true_center):")
        for (i, r, ctrue) in circle_like_near[:30]:
            print(f"    edge_idx={i}  r={r:.6f}  c=({ctrue[0]:.6f},{ctrue[1]:.6f},{ctrue[2]:.6f})")
        if len(circle_like_near) > 30:
            print(f"    (showing 30 of {len(circle_like_near)})")

    # Also report planar +/-Y faces with inner loops near the hole center
    faces = base.Faces()
    y_faces = []
    for fi, f in enumerate(faces):
        if f.geomType() != "PLANE":
            continue
        try:
            n = f.normalAt()
        except Exception:
            continue
        if abs(abs(n.y) - 1.0) > 1e-6:
            continue
        try:
            inn = f.innerWires()
            if len(inn) == 0:
                continue
        except Exception:
            continue
        c = f.Center()
        if abs(c.x - cx) < 2.0 and (abs(c.y - y0) < 1.0 or abs(c.y - y1) < 1.0) and abs(c.z - cz) < 2.0:
            counts = []
            try:
                counts = [len(w.Edges()) for w in inn]
            except Exception:
                counts = []
            y_faces.append((fi, f.Area(), c.toTuple(), n.toTuple(), len(inn), counts))

    print(f"SELECTED: {len(y_faces)} planar +/-Y faces with inner loops near target center (diagnostic)")
    for (fi, a, ctu, ntu, nw, counts) in y_faces:
        print(f"  face_idx={fi} area={a:.6f} center=({ctu[0]:.6f},{ctu[1]:.6f},{ctu[2]:.6f}) n=({ntu[0]:.1f},{ntu[1]:.1f},{ntu[2]:.1f}) innerWires={nw} innerEdgeCounts={counts}")

    # --- Rebuild the feature by: (1) filling the existing (misclocked) hex opening, (2) recutting a correctly clocked hex ---
    # Fill with an oversize cylinder so we definitely erase the existing hex opening (and any residual circular artifacts).
    # This does not change the external bbox because it is fully contained in the boss (outer r~14.9 at this location).
    plug_r = 10.6
    tool_y_start = -1.0
    tool_len = (y1 - y0) + 2.0  # 17mm from -1..16 covers the 0..15 span robustly

    plug_cyl = cq.Solid.makeCylinder(
        plug_r,
        tool_len,
        pnt=cq.Vector(cx, tool_y_start, cz),
        dir=axis
    )
    bb_plug = plug_cyl.BoundingBox()
    print("TOOL: plug cylinder")
    print(f"  r={plug_r}  y=[{tool_y_start},{tool_y_start + tool_len}]  bbox x=[{bb_plug.xmin:.3f},{bb_plug.xmax:.3f}] y=[{bb_plug.ymin:.3f},{bb_plug.ymax:.3f}] z=[{bb_plug.zmin:.3f},{bb_plug.zmax:.3f}]")

    # Desired hex: vertices on r=10 circle, one vertex at +Z direction (X,Z)=(0,20).
    def norm_deg(a):
        a = a % 360.0
        return a + 360.0 if a < 0 else a

    def smallest_signed_diff(a, b):
        # a-b wrapped to [-180,180)
        return (a - b + 180.0) % 360.0 - 180.0

    def build_hex_vertices(angle0_deg):
        pts = []
        angs = []
        for k in range(6):
            a = math.radians(angle0_deg + 60.0 * k)
            x = cx + r_vertex * math.cos(a)
            z = cz + r_vertex * math.sin(a)
            pts.append((x, tool_y_start, z))
            angs.append(norm_deg(math.degrees(math.atan2(z - cz, x - cx))))
        return pts, angs

    target_angle0 = 90.0
    pts_world, angs = build_hex_vertices(target_angle0)

    # Clocking self-check: ensure a vertex actually hits angle~90. If closest is ~30deg away, rotate by that amount.
    diffs = [smallest_signed_diff(a, target_angle0) for a in angs]
    best_i = min(range(6), key=lambda i: abs(diffs[i]))
    best_diff = diffs[best_i]
    print("HEX CLOCKING CHECK (pre-correction):")
    print(f"  target vertex angle={target_angle0:.3f}deg; closest vertex v{best_i} angle={angs[best_i]:.3f}deg diff={best_diff:.3f}deg")

    if abs(abs(best_diff) - 30.0) < 2.0:
        corr = -best_diff
        print(f"CLOCKING: detected ~30deg misclock relative to requested vertex direction; applying correction rotation {corr:.3f}deg")
        pts_world, angs = build_hex_vertices(target_angle0 + corr)
        diffs2 = [smallest_signed_diff(a, target_angle0) for a in angs]
        best_i2 = min(range(6), key=lambda i: abs(diffs2[i]))
        print(f"  after correction: closest vertex v{best_i2} angle={angs[best_i2]:.3f}deg diff={diffs2[best_i2]:.3f}deg")

    print("HEX VERTICES (world @ y=tool_y_start; angle in XZ about center):")
    for i, ((x, y, z), a) in enumerate(zip(pts_world, angs)):
        print(f"  v{i}: angle={a:.3f}deg  world=({x:.6f},{y:.6f},{z:.6f})")

    # Explicit vertex target check: the vertex at max Z should be near (0, tool_y_start, 20)
    vmax = max(pts_world, key=lambda p: p[2])
    print("VERTEX TARGET CHECK:")
    print(f"  max-Z vertex = ({vmax[0]:.6f},{vmax[1]:.6f},{vmax[2]:.6f}) vs target approx (0,{tool_y_start},20)")
    print(f"  deltas: dX={vmax[0]-0.0:.6f}  dZ={vmax[2]-20.0:.6f}")

    # Build hex prism tool from 3D polygon to avoid workplane axis ambiguity.
    pts_vec = [cq.Vector(x, y, z) for (x, y, z) in pts_world]
    hex_wire = cq.Wire.makePolygon(pts_vec, close=True)
    hex_face = cq.Face.makeFromWires(hex_wire)
    hex_tool = cq.Solid.extrudeLinear(hex_face, cq.Vector(0.0, tool_len, 0.0))
    bb_hex = hex_tool.BoundingBox()
    print("TOOL: hex prism")
    print(f"  extrude y=[{tool_y_start},{tool_y_start + tool_len}]  bbox x=[{bb_hex.xmin:.3f},{bb_hex.xmax:.3f}] y=[{bb_hex.ymin:.3f},{bb_hex.ymax:.3f}] z=[{bb_hex.zmin:.3f},{bb_hex.zmax:.3f}]")

    # Apply: fill then cut
    bb0 = solid0.BoundingBox()
    filled = solid0.fuse(plug_cyl)
    out_solid = filled.cut(hex_tool)

    # Recompound if needed
    if len(solids) > 1:
        out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != 0] + [out_solid])
    else:
        out = out_solid

    bb1 = out.BoundingBox()
    print("BBOX CHECK (must be unchanged):")
    print(f"  before: xmin={bb0.xmin:.6f} xmax={bb0.xmax:.6f} ymin={bb0.ymin:.6f} ymax={bb0.ymax:.6f} zmin={bb0.zmin:.6f} zmax={bb0.zmax:.6f}")
    print(f"  after : xmin={bb1.xmin:.6f} xmax={bb1.xmax:.6f} ymin={bb1.ymin:.6f} ymax={bb1.ymax:.6f} zmin={bb1.zmin:.6f} zmax={bb1.zmax:.6f}")
    print(f"  delta : dxmin={bb1.xmin-bb0.xmin:.6f} dxmax={bb1.xmax-bb0.xmax:.6f} dymin={bb1.ymin-bb0.ymin:.6f} dymax={bb1.ymax-bb0.ymax:.6f} dzmin={bb1.zmin-bb0.zmin:.6f} dzmax={bb1.zmax-bb0.zmax:.6f}")

    # Diagnostic: report any remaining r~10 circular edges with true center near target (should be 0 if mouths are straight)
    out_edges = out.Edges() if hasattr(out, "Edges") else out_solid.Edges()
    rem = []
    if have_ocp:
        for i, e in enumerate(out_edges):
            try:
                if e.geomType() != "CIRCLE":
                    continue
                adp = BRepAdaptor_Curve(e.wrapped)
                if adp.GetType() != GeomAbs_CurveType.GeomAbs_Circle:
                    continue
                circ = adp.Circle()
                loc = circ.Location()
                ctrue = (loc.X(), loc.Y(), loc.Z())
                r = circ.Radius()
                if abs(r - r_vertex) < 0.25 and abs(ctrue[0] - cx) < 0.25 and abs(ctrue[2] - cz) < 0.25 and (abs(ctrue[1] - y0) < 0.25 or abs(ctrue[1] - y1) < 0.25):
                    rem.append(i)
            except Exception:
                continue
    print(f"POSTCHECK: remaining circular edges r~10 with true center near (0,{{0 or 15}},10) = {len(rem)} idx={rem}")

    return out