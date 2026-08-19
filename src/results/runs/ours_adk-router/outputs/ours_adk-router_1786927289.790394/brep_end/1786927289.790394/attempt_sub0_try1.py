def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- helpers ---
    def vtuple(v):
        return [float(v.x), float(v.y), float(v.z)]

    def dist(a, b):
        return sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)

    def circle_center_radius(edge):
        # Robust circle extraction via OCP
        try:
            from OCP.BRepAdaptor import BRepAdaptor_Curve
            from OCP.GeomAbs import GeomAbs_Circle
            ad = BRepAdaptor_Curve(edge.wrapped)
            if ad.GetType() != GeomAbs_Circle:
                return None
            c = ad.Circle()
            p = c.Location()
            return [float(p.X()), float(p.Y()), float(p.Z())], float(c.Radius())
        except Exception:
            return None

    def make_block_at(center_xyz, xlen=8.0, ylen=17.0, zlen=8.0):
        # block spans y=-ylen..0 to avoid including outside air behind face y=0
        cx, cy, cz = center_xyz
        block_center = (cx, -ylen/2.0, cz)
        blk = (
            cq.Workplane(cq.Plane.XY())
            .box(xlen, ylen, zlen, centered=(True, True, True))
            .translate(block_center)
            .val()
        )
        return blk

    def extract_void_from_hole(solid, center_xyz, label):
        blk = make_block_at(center_xyz)
        vb = blk.BoundingBox()
        print(f"BLOCK {label}: bbox x[{vb.xmin:.3f},{vb.xmax:.3f}] y[{vb.ymin:.3f},{vb.ymax:.3f}] z[{vb.zmin:.3f},{vb.zmax:.3f}]")
        void = blk.cut(solid)
        try:
            bb = void.BoundingBox()
            vol = void.Volume()
            print(f"EXTRACTED VOID {label}: vol={vol:.6f} bbox y[{bb.ymin:.3f},{bb.ymax:.3f}] (expect ymax~0.0)")
        except Exception as e:
            print(f"EXTRACTED VOID {label}: could not measure ({e})")
        return void

    # --- diagnostics: resolve indexed entities against base ---
    faces = base.Faces()
    edges = base.Edges()
    print(f"SELECTED: {len(base.Solids())} solids in imported STEP")
    if len(faces) > 466:
        f466 = faces[466]
        print(f"RESOLVED: face#466 center={vtuple(f466.Center())} area={f466.Area():.3f} normal={vtuple(f466.normalAt())}")
        try:
            ws = f466.Wires()
            print(f"RESOLVED: face#466 wires={len(ws)}")
        except Exception as e:
            print(f"RESOLVED: face#466 wires=<?> ({e})")
    else:
        print("SELECTED: 0 faces for face#466 (index out of range) -- ABORTING")
        return shape

    for ei in [1350, 1351, 1346]:
        if len(edges) > ei:
            info = circle_center_radius(edges[ei])
            if info:
                c, r = info
                print(f"RESOLVED: edge#{ei} circle center={c} r={r:.4f}")
            else:
                print(f"RESOLVED: edge#{ei} is not a circle (or failed to read geom)")
        else:
            print(f"RESOLVED: edge#{ei} not found (index out of range)")

    # --- isolate s1 for editing, recombine others untouched ---
    solids = base.Solids()
    if len(solids) < 2:
        print("SELECTED: 0 solids for s1 (need at least 2 solids) -- ABORTING")
        return shape

    s1 = solids[1]
    bb1 = s1.BoundingBox()
    print(f"SELECTED: 1 solid for s1 (solid index 1) bbox y[{bb1.ymin:.3f},{bb1.ymax:.3f}] x[{bb1.xmin:.3f},{bb1.xmax:.3f}] z[{bb1.zmin:.3f},{bb1.zmax:.3f}]")

    # --- authority / target coordinates from sub-goal ---
    # keep upper holes at:
    upper_centers = [(35.5, 0.0, 20.0), (44.5, 0.0, 20.0)]
    # remove lower-center hole at:
    old_center = (40.0, 0.0, 10.0)
    # add new lower-corner holes at:
    new_centers = [(35.5, 0.0, 10.0), (44.5, 0.0, 10.0)]
    print("NAMED COORDINATES:")
    print(f"  upper keep: {upper_centers}")
    print(f"  remove/heal: {old_center}")
    print(f"  add: {new_centers}")

    # --- extract exact void geometry for filling and for templated cutting ---
    # Fill the existing lower-center hole using its own void volume
    void_old = extract_void_from_hole(s1, old_center, label="OLD@40,0,10")

    # Template void from an existing upper hole (exact thread/mouth family)
    template_center = (35.5, 0.0, 20.0)
    void_template = extract_void_from_hole(s1, template_center, label="TEMPLATE@35.5,0,20")

    # --- heal/remove old center hole ---
    s1_filled = s1.fuse(void_old)
    try:
        s1_filled = s1_filled.clean()
    except Exception:
        pass

    # Self-check: added material (what changed when filling)
    try:
        added = s1_filled.cut(s1)
        abb = added.BoundingBox()
        print(f"SELF-CHECK: filling added.Center={vtuple(added.Center())} added bbox y[{abb.ymin:.3f},{abb.ymax:.3f}]")
        print(f"SELF-CHECK: fill target center {list(old_center)} delta={ [added.Center().x-old_center[0], added.Center().y-old_center[1], added.Center().z-old_center[2]] }")
    except Exception as e:
        print(f"SELF-CHECK: could not compute added material for fill ({e})")

    # --- cut two new holes by translating the exact template void ---
    dx1, dz1 = (new_centers[0][0] - template_center[0], new_centers[0][2] - template_center[2])
    dx2, dz2 = (new_centers[1][0] - template_center[0], new_centers[1][2] - template_center[2])
    tool1 = void_template.translate((dx1, 0.0, dz1))
    tool2 = void_template.translate((dx2, 0.0, dz2))
    print(f"TOOLS: translate template by (dx={dx1:.3f}, dz={dz1:.3f}) for new hole at {list(new_centers[0])}")
    print(f"TOOLS: translate template by (dx={dx2:.3f}, dz={dz2:.3f}) for new hole at {list(new_centers[1])}")

    s1_out = s1_filled.cut(tool1).cut(tool2)
    try:
        s1_out = s1_out.clean()
    except Exception:
        pass

    # --- recombine solids unchanged except s1 ---
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != 1] + [s1_out])

    # --- verification: face at y=0, normal +Y on edited s1 has 4 openings and correct centers ---
    # Find the back face on edited s1 by normal +Y and y~0, choose largest area.
    cand = []
    for f in s1_out.Faces():
        try:
            n = f.normalAt()
            c = f.Center()
            if n.y > 0.95 and abs(c.y - 0.0) < 1e-3:
                cand.append(f)
        except Exception:
            pass
    print(f"SELECTED: {len(cand)} planar-ish faces on edited s1 with normal +Y and center.y~0")

    back_face = None
    if cand:
        back_face = max(cand, key=lambda ff: ff.Area())
        print(f"SELECTED: 1 back face for verification area={back_face.Area():.3f} center={vtuple(back_face.Center())} normal={vtuple(back_face.normalAt())}")
    else:
        print("SELECTED: 0 back faces for verification -- cannot verify openings")
        return out

    # Identify inner wires and read mouth circle centers (r~1.7)
    try:
        ow = back_face.outerWire()
        allw = back_face.Wires()
        inner = [w for w in allw if not w.isSame(ow)]
    except Exception as e:
        print(f"VERIFY: failed to collect wires ({e})")
        inner = []

    print(f"VERIFY: back face wires total={len(back_face.Wires())} inner={len(inner)} (expect 4)")

    mouth_centers = []
    for wi, w in enumerate(inner):
        found = None
        for e in w.Edges():
            info = circle_center_radius(e)
            if not info:
                continue
            cc, rr = info
            if abs(rr - 1.7) < 0.02 and abs(cc[1] - 0.0) < 1e-3:
                found = cc
                break
        if found:
            mouth_centers.append(found)
            print(f"VERIFY: inner wire {wi} mouth center={found}")
        else:
            print(f"VERIFY: inner wire {wi} no r~1.7 circle edge found")

    # Final check against required centers
    required = [list(p) for p in [(35.5, 0.0, 20.0), (44.5, 0.0, 20.0), (35.5, 0.0, 10.0), (44.5, 0.0, 10.0)]]
    print("FINAL MOUTH CENTERS (detected):")
    for c in mouth_centers:
        print(f"  {c}")

    # Verify no opening remains at [40,0,10]
    bad = [c for c in mouth_centers if dist(c, list(old_center)) < 0.5]
    print(f"VERIFY: openings within 0.5mm of removed center {list(old_center)} count={len(bad)}")

    # Verify coverage of required four
    unmatched_req = []
    for r in required:
        if not any(dist(r, c) < 0.5 for c in mouth_centers):
            unmatched_req.append(r)
    print(f"VERIFY: required centers not matched within 0.5mm count={len(unmatched_req)} missing={unmatched_req}")

    return out