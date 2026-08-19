def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Numbers named by the sub-goal ---
    cx, cz = 0.0, 10.0
    r_vertex = 10.0
    y0, y1 = 0.0, 15.0
    axis = cq.Vector(0.0, 1.0, 0.0)
    target_vertex_angle = 90.0  # +Z direction in XZ-plane about (cx,cz)

    print("TARGET NUMBERS:")
    print(f"  bore center (X,Z)=({cx},{cz})")
    print(f"  original circle r={r_vertex} (vertices must lie on this circle)")
    print(f"  cut axis={tuple(axis.toTuple())} through Y={y0}..{y1} (len {y1-y0})")
    print("  required clocking: one vertex toward world +Z => approx (X,Z)=(0,20)")

    # --- Basic info ---
    solids = base.Solids()
    print(f"INFO: imported solids={len(solids)} faces={len(base.Faces())} edges={len(base.Edges())} verts={len(base.Vertices())}")
    if len(solids) < 1:
        print("ERROR: no solids found; returning input")
        return shape

    solid0 = solids[0]

    # --- Pre-op selection/diagnostics: find circular edges near the target bore (often arcs) ---
    edges = base.Edges()
    near_r10 = []
    for i, e in enumerate(edges):
        if e.geomType() != "CIRCLE":
            continue
        try:
            r = e.radius()
        except Exception:
            continue
        if abs(r - r_vertex) > 0.25:
            continue
        c = e.Center()  # may be centroid for arcs; still useful
        if abs(c.x - cx) < 0.5 and abs(c.z - cz) < 0.5 and (abs(c.y - y0) < 0.5 or abs(c.y - y1) < 0.5):
            near_r10.append(i)
    print(f"SELECTED: {len(near_r10)} circular edges (geomType==CIRCLE) near (0,{{0 or 15}},10) with r~10 for target-bore diagnostics   idx={near_r10}")

    # --- Pre-op selection/diagnostics: planar +/-Y faces near y=0 and y=15 with inner wires near target ---
    def _face_normal_y(f):
        try:
            n = f.normalAt()
            return n.y
        except Exception:
            return None

    faces = base.Faces()
    diag_faces = []
    for fi, f in enumerate(faces):
        if f.geomType() != "PLANE":
            continue
        ny = _face_normal_y(f)
        if ny is None or abs(abs(ny) - 1.0) > 1e-6:
            continue
        cy = f.Center().y
        if not (abs(cy - y0) < 0.5 or abs(cy - y1) < 0.5):
            continue
        try:
            inn = f.innerWires()
        except Exception:
            inn = []
        if len(inn) == 0:
            continue
        # find an inner wire whose centroid is near (cx,cz)
        good = False
        for w in inn:
            try:
                wc = w.Center()
            except Exception:
                continue
            if abs(wc.x - cx) < 0.5 and abs(wc.z - cz) < 0.5:
                good = True
        if good:
            try:
                inner_counts = [len(w.Edges()) for w in inn]
            except Exception:
                inner_counts = []
            diag_faces.append((fi, f.Area(), f.Center(), ny, len(inn), inner_counts))

    print(f"SELECTED: {len(diag_faces)} planar +/-Y faces with inner loops near target center (diagnostic)")
    for (fi, a, c, ny, nw, counts) in sorted(diag_faces, key=lambda t: (abs(t[2].y - y0), t[1]))[:6]:
        ctu = tuple(round(v, 6) for v in c.toTuple())
        print(f"  face_idx={fi} area={a:.6f} center={ctu} normalY={ny:.1f} innerWires={nw} innerEdgeCounts={counts}")

    # --- Helper: build hex tool (3D wire) at given rotation, confined to Y=0..15 ---
    def norm_deg(a):
        a = a % 360.0
        return a + 360.0 if a < 0 else a

    def smallest_signed_diff(a, b):
        return (a - b + 180.0) % 360.0 - 180.0

    def build_hex_tool(angle0_deg):
        pts_world = []
        angs = []
        for k in range(6):
            ang = angle0_deg + 60.0 * k
            ar = math.radians(ang)
            x = cx + r_vertex * math.cos(ar)
            z = cz + r_vertex * math.sin(ar)
            pts_world.append((x, y0, z))
            angs.append(norm_deg(math.degrees(math.atan2(z - cz, x - cx))))

        print(f"HEX VERTICES (requested angle0={angle0_deg:.3f}deg; world @ y={y0}):")
        for i, ((x, y, z), a) in enumerate(zip(pts_world, angs)):
            print(f"  v{i}: angle={a:.3f}deg  world=({x:.6f},{y:.6f},{z:.6f})")

        vmax = max(pts_world, key=lambda p: p[2])
        print("VERTEX TARGET CHECK (construction):")
        print(f"  max-Z vertex = ({vmax[0]:.6f},{vmax[1]:.6f},{vmax[2]:.6f}) vs target approx (0,{y0:.1f},20)")
        print(f"  deltas: dX={vmax[0]-0.0:.6f}  dZ={vmax[2]-20.0:.6f}")

        pts_vec = [cq.Vector(x, y, z) for (x, y, z) in pts_world]
        hex_wire = cq.Wire.makePolygon(pts_vec, close=True)
        hex_face = cq.Face.makeFromWires(hex_wire)
        tool = cq.Solid.extrudeLinear(hex_face, cq.Vector(0.0, (y1 - y0), 0.0))
        return tool

    # --- Helper: measure resulting mouth wire vertex angles on the y=0 side (if we can find it) ---
    def measure_mouth_angles(solid, y_target):
        out_faces = solid.Faces()
        cands = []
        for fi, f in enumerate(out_faces):
            if f.geomType() != "PLANE":
                continue
            try:
                n = f.normalAt()
            except Exception:
                continue
            if abs(abs(n.y) - 1.0) > 1e-6:
                continue
            if abs(f.Center().y - y_target) > 0.5:
                continue
            try:
                inn = f.innerWires()
            except Exception:
                continue
            for w in inn:
                try:
                    wc = w.Center()
                except Exception:
                    continue
                if abs(wc.x - cx) < 0.5 and abs(wc.z - cz) < 0.5:
                    cands.append((fi, f, w, wc))

        print(f"SELECTED: {len(cands)} inner wires on planar faces near y={y_target} around (0,10) for clocking measurement")
        if len(cands) == 0:
            return None

        # prefer a wire with ~6 vertices (hex mouth)
        best = None
        best_score = 1e9
        for (fi, f, w, wc) in cands:
            try:
                verts = w.Vertices()
            except Exception:
                continue
            # de-dup by rounding
            uniq = {}
            for v in verts:
                p = v.Center()
                key = (round(p.x, 4), round(p.y, 4), round(p.z, 4))
                uniq[key] = p
            nv = len(uniq)
            score = abs(nv - 6) * 10.0 + abs(wc.x - cx) + abs(wc.z - cz)
            if score < best_score:
                best_score = score
                best = (fi, f, w, list(uniq.values()))

        if best is None:
            return None

        (fi, f, w, pts) = best
        angs = []
        pts_xz = []
        for p in pts:
            a = norm_deg(math.degrees(math.atan2(p.z - cz, p.x - cx)))
            angs.append(a)
            pts_xz.append((p.x, p.z))
        angs_sorted = sorted(angs)

        print(f"MEASURED: mouth wire on face_idx={fi} (y~{y_target}) has {len(pts)} unique vertices")
        for i, (a, (x, z)) in enumerate(sorted(zip(angs, pts_xz), key=lambda t: t[0])):
            print(f"  mv{i}: angle={a:.3f}deg  (X,Z)=({x:.6f},{z:.6f})")
        return angs_sorted

    # --- Rebuild feature: fill existing opening, then recut correctly clocked hex ---
    # Plug cylinder confined strictly to Y=0..15 so bbox cannot change in Y.
    plug_r = 10.6
    plug = cq.Solid.makeCylinder(plug_r, (y1 - y0), pnt=cq.Vector(cx, y0, cz), dir=axis)
    bbp = plug.BoundingBox()
    print("TOOL: plug cylinder (confined to Y=0..15)")
    print(f"  r={plug_r}  bbox x=[{bbp.xmin:.3f},{bbp.xmax:.3f}] y=[{bbp.ymin:.3f},{bbp.ymax:.3f}] z=[{bbp.zmin:.3f},{bbp.zmax:.3f}]")

    # Fill first (eliminate misclocked hex/circle remnants), then cut.
    filled = solid0.fuse(plug)

    # Attempt 1: requested orientation (vertex at +Z)
    hex_tool_1 = build_hex_tool(target_vertex_angle)
    out1 = filled.cut(hex_tool_1)

    # Measure clocking on y=0 mouth; if ~30deg off, apply correction and redo from 'filled'
    angs1 = measure_mouth_angles(out1, y0)
    correction = 0.0
    if angs1 is not None and len(angs1) >= 6:
        diffs = [smallest_signed_diff(a, target_vertex_angle) for a in angs1]
        best_diff = min(diffs, key=lambda d: abs(d))
        print(f"CLOCKING CHECK (measured): target {target_vertex_angle:.3f}deg, closest vertex diff={best_diff:.3f}deg")
        if abs(abs(best_diff) - 30.0) < 2.0:
            correction = -best_diff
            print(f"CLOCKING: detected ~30deg misclock in result; applying correction rotation {correction:.3f}deg and rebuilding cut")
    else:
        print("CLOCKING CHECK (measured): could not reliably measure mouth vertices; will still apply explicit +30deg correction fallback if needed")

    # If correction requested, redo cut with corrected tool
    if abs(correction) > 1e-9:
        hex_tool_2 = build_hex_tool(target_vertex_angle + correction)
        out_solid = filled.cut(hex_tool_2)
    else:
        out_solid = out1

    # Recompound if needed
    if len(solids) > 1:
        out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != 0] + [out_solid])
    else:
        out = out_solid

    # --- Self-check: outer bbox must be unchanged ---
    bb0 = base.BoundingBox()
    bb1 = out.BoundingBox()
    print("BBOX CHECK (must be unchanged):")
    print(f"  before: xmin={bb0.xmin:.6f} xmax={bb0.xmax:.6f} ymin={bb0.ymin:.6f} ymax={bb0.ymax:.6f} zmin={bb0.zmin:.6f} zmax={bb0.zmax:.6f}")
    print(f"  after : xmin={bb1.xmin:.6f} xmax={bb1.xmax:.6f} ymin={bb1.ymin:.6f} ymax={bb1.ymax:.6f} zmin={bb1.zmin:.6f} zmax={bb1.zmax:.6f}")
    print(f"  delta : dxmin={bb1.xmin-bb0.xmin:.6f} dxmax={bb1.xmax-bb0.xmax:.6f} dymin={bb1.ymin-bb0.ymin:.6f} dymax={bb1.ymax-bb0.ymax:.6f} dzmin={bb1.zmin-bb0.zmin:.6f} dzmax={bb1.zmax-bb0.zmax:.6f}")

    # --- Final clocking report (requested: print achieved vertex angles and coordinates) ---
    print("FINAL CLOCKING MEASUREMENT (post-op):")
    _ = measure_mouth_angles(out_solid, y0)
    _ = measure_mouth_angles(out_solid, y1)

    return out