def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve indexed entities and print diagnostics ---
    faces = base.Faces()
    edges = base.Edges()
    print(f"INFO: base faces={len(faces)} edges={len(edges)} solids={len(base.Solids())}")

    try:
        f21 = faces[21]
        print("RESOLVED: face #21 (expected full bore wall)")
        print(f"  geomType={f21.geomType()}  center={tuple(round(v, 6) for v in f21.Center().toTuple())}  area={round(f21.Area(), 6)}")
    except Exception as e:
        print(f"ERROR: could not resolve face #21: {e}")
        f21 = None

    target_edge_idxs = [63, 64]
    resolved_edges = []
    for ei in target_edge_idxs:
        try:
            e = edges[ei]
            resolved_edges.append(e)
            c = e.Center()
            gt = e.geomType()
            extra = ""
            if gt == "CIRCLE":
                try:
                    extra = f" r={round(e.radius(), 6)}"
                except Exception:
                    extra = ""
            print(f"RESOLVED: edge_idx[{ei}] geomType={gt}{extra} center={tuple(round(v, 6) for v in c.toTuple())} length={round(e.Length(), 6)}")
        except Exception as e:
            print(f"ERROR: could not resolve edge_idx[{ei}]: {e}")

    print(f"SELECTED: {1 if f21 else 0} faces for bore wall reference   idx=[21]")
    print(f"SELECTED: {len(resolved_edges)} edges for bore mouth reference   idx={target_edge_idxs}")

    # --- Numbers named by the sub-goal ---
    cx, cz = 0.0, 10.0
    r0 = 10.0
    y0, y1 = 0.0, 15.0
    axis = (0.0, 1.0, 0.0)
    print("TARGET NUMBERS:")
    print(f"  bore center (X,Z)=({cx},{cz})   original r={r0}")
    print(f"  cut axis={axis}   through Y={y0}..{y1} (len {y1-y0})")
    print("  hex clocking: one vertex toward +Z => approx vertex at (X,Z)=(0,20)")

    # --- Build a cylindrical plug to fill the original circular through-bore ---
    # Slight radial oversize to ensure robust fuse into the bore wall without changing outer bbox.
    r_plug = r0 + 0.02
    plug_plane = cq.Plane(origin=(cx, y0, cz), normal=axis, xDir=(1.0, 0.0, 0.0))
    print(f"PLANE: plug_plane origin={plug_plane.origin.toTuple()} normal={plug_plane.zDir.toTuple()} xDir={plug_plane.xDir.toTuple()}")
    plug_wp = cq.Workplane(plug_plane).circle(r_plug).extrude(y1 - y0)
    plug_solid = plug_wp.val()

    # --- Build hex through-cut tool (vertices on r=10 circle), clocked with a vertex at +Z ---
    target_first_angle_deg = 90.0  # +Z direction in XZ-plane when local y->world Z

    def norm_deg(a):
        a = a % 360.0
        return a + 360.0 if a < 0 else a

    def signed_smallest_diff_deg(a, b):
        # a-b wrapped to [-180,180)
        d = (a - b + 180.0) % 360.0 - 180.0
        return d

    # Start with requested angles
    angles_deg = [target_first_angle_deg + 60.0 * k for k in range(6)]

    # Compute achieved first vertex angle from constructed points (should match), and auto-correct if ~30 deg off.
    first_angle_achieved = norm_deg(angles_deg[0])
    diff = signed_smallest_diff_deg(first_angle_achieved, target_first_angle_deg)
    if abs(abs(diff) - 30.0) < 2.0:  # approximately 30-degree misclock
        corr = -diff
        angles_deg = [a + corr for a in angles_deg]
        print(f"CLOCKING: detected ~30deg misclock (diff={diff:.3f}deg). Applying correction rotation {corr:.3f}deg.")
    else:
        print(f"CLOCKING: first vertex angle achieved={first_angle_achieved:.3f}deg (target {target_first_angle_deg:.3f}deg), diff={diff:.3f}deg")

    pts_local = []
    pts_world_at_y0 = []
    achieved_angles = []
    for a in angles_deg:
        ar = math.radians(a)
        xl = r0 * math.cos(ar)
        yl = r0 * math.sin(ar)  # local y maps to world Z offset due to xDir=(1,0,0)
        pts_local.append((xl, yl))
        achieved_angles.append(norm_deg(math.degrees(math.atan2(yl, xl))))
        pts_world_at_y0.append((cx + xl, y0, cz + yl))

    print("HEX VERTICES (angles deg, local(x,zOffset), world at y=0):")
    for i, (ang, pl, pw) in enumerate(zip(achieved_angles, pts_local, pts_world_at_y0)):
        print(f"  v{i}: angle={ang:.3f}  local=({pl[0]:.6f},{pl[1]:.6f})  world=({pw[0]:.6f},{pw[1]:.6f},{pw[2]:.6f})")

    # Tool plane slightly below y=0; extend past both faces for reliable through-cut.
    tool_y_start = y0 - 1.0
    tool_len = (y1 - y0) + 2.0
    tool_plane = cq.Plane(origin=(cx, tool_y_start, cz), normal=axis, xDir=(1.0, 0.0, 0.0))
    print(f"PLANE: hex_tool_plane origin={tool_plane.origin.toTuple()} normal={tool_plane.zDir.toTuple()} xDir={tool_plane.xDir.toTuple()}  extrude_len={tool_len}")

    hex_wp = cq.Workplane(tool_plane).moveTo(pts_local[0][0], pts_local[0][1]).polyline(pts_local[1:]).close().extrude(tool_len)
    hex_solid = hex_wp.val()

    # --- Apply edit on the sole solid ---
    sols = base.Solids()
    if len(sols) != 1:
        print(f"WARNING: expected 1 solid, found {len(sols)}; editing solid[0] and recombining.")
    solid0 = sols[0]

    # Fuse plug to eliminate the circular hole, then cut the hex through.
    filled = solid0.fuse(plug_solid)
    out_solid = filled.cut(hex_solid)

    # Recompound if needed
    if len(sols) > 1:
        out = cq.Compound.makeCompound([s for i, s in enumerate(sols) if i != 0] + [out_solid])
    else:
        out = out_solid

    # --- Placement self-checks ---
    bb0 = base.BoundingBox()
    bb1 = out.BoundingBox()
    print("BBOX CHECK (must be unchanged):")
    print(f"  before: xmin={bb0.xmin:.6f} xmax={bb0.xmax:.6f} ymin={bb0.ymin:.6f} ymax={bb0.ymax:.6f} zmin={bb0.zmin:.6f} zmax={bb0.zmax:.6f}")
    print(f"  after : xmin={bb1.xmin:.6f} xmax={bb1.xmax:.6f} ymin={bb1.ymin:.6f} ymax={bb1.ymax:.6f} zmin={bb1.zmin:.6f} zmax={bb1.zmax:.6f}")
    print(f"  delta : dxmin={bb1.xmin-bb0.xmin:.6f} dxmax={bb1.xmax-bb0.xmax:.6f} dymin={bb1.ymin-bb0.ymin:.6f} dymax={bb1.ymax-bb0.ymax:.6f} dzmin={bb1.zmin-bb0.zmin:.6f} dzmax={bb1.zmax-bb0.zmax:.6f}")

    # Added material by plugging (diagnostic)
    try:
        added = filled.cut(solid0)
        bb_added = added.BoundingBox()
        c_added = added.Center()
        print("ADDED (plug minus overlap) CHECK:")
        print(f"  added_center={tuple(round(v, 6) for v in c_added.toTuple())}")
        print(f"  added_bbox: xmin={bb_added.xmin:.6f} xmax={bb_added.xmax:.6f} ymin={bb_added.ymin:.6f} ymax={bb_added.ymax:.6f} zmin={bb_added.zmin:.6f} zmax={bb_added.zmax:.6f}")
        print(f"  expected roughly within X[-{r0},+{r0}] and Z[{cz-r0},{cz+r0}] and Y[{y0},{y1}]")
    except Exception as e:
        print(f"WARNING: could not compute added-material diagnostic: {e}")

    # Verify the +Z-pointing vertex location in XZ
    v0 = pts_world_at_y0[0]
    print("VERTEX TARGET CHECK:")
    print(f"  v0 world at y=0 = ({v0[0]:.6f},{v0[1]:.6f},{v0[2]:.6f}) vs target approx (X,Z)=(0,20)")
    print(f"  deltas: dX={v0[0]-0.0:.6f}  dZ={v0[2]-20.0:.6f}")

    # --- Post-op feature sanity: confirm no r=10 cylindrical wall remains, and no circular mouth edges near center ---
    out_faces = out.Faces() if hasattr(out, "Faces") else out_solid.Faces()
    cyl10 = []
    for i, f in enumerate(out_faces):
        if f.geomType() == "CYLINDER":
            try:
                r = f._geomAdaptor().Cylinder().Radius()
                if abs(r - r0) < 1e-3:
                    cyl10.append(i)
            except Exception:
                pass
    print(f"POSTCHECK: cylindrical faces with r~10.0 remaining = {len(cyl10)}  face_idxs={cyl10}")

    out_edges = out.Edges() if hasattr(out, "Edges") else out_solid.Edges()
    circ_near = []
    for i, e in enumerate(out_edges):
        if e.geomType() == "CIRCLE":
            try:
                r = e.radius()
            except Exception:
                continue
            if abs(r - r0) < 1e-3:
                c = e.Center()
                if abs(c.x - cx) < 1e-3 and abs(c.z - cz) < 1e-3:
                    circ_near.append(i)
    print(f"POSTCHECK: circular edges r~10 centered near (0,*,10) remaining = {len(circ_near)} edge_idxs={circ_near}")

    # Check inner loops on the two main Y-normal planar faces (mouths should be hex now)
    def face_normal(f):
        try:
            n = f.normalAt()
            return n
        except Exception:
            return None

    planes_y = []
    for i, f in enumerate(out_faces):
        if f.geomType() == "PLANE":
            n = face_normal(f)
            if n is None:
                continue
            if abs(abs(n.y) - 1.0) < 1e-6:
                planes_y.append((i, f, n.y, f.Area(), f.Center().y))
    planes_y.sort(key=lambda t: -t[3])
    print(f"POSTCHECK: found {len(planes_y)} planar faces with |normal.y|~1")
    for (i, f, ny, a, cy) in planes_y[:4]:
        try:
            inn = f.innerWires()
            inner_counts = [len(w.Edges()) for w in inn]
        except Exception:
            inner_counts = []
        print(f"  plane_face idx={i} area={a:.6f} centerY={cy:.6f} normalY={ny:.1f} innerWires={len(inner_counts)} innerEdgeCounts={inner_counts}")

    return out