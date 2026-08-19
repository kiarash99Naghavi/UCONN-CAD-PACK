def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # ---- helpers ----
    def vdot(a: cq.Vector, b: cq.Vector) -> float:
        return a.x * b.x + a.y * b.y + a.z * b.z

    def vunit(v: cq.Vector) -> cq.Vector:
        L = v.Length
        if L == 0:
            return cq.Vector(0, 0, 0)
        return cq.Vector(v.x / L, v.y / L, v.z / L)

    def proj_range_on_dir(shp, d: cq.Vector):
        d = vunit(d)
        vs = shp.Vertices()
        if len(vs) == 0:
            return None
        vals = [vdot(v.Center(), d) for v in vs]
        return (min(vals), max(vals))

    def find_cut_face_on_plane(part, mid_s, vdir: cq.Vector, tol_s=0.5):
        # Return largest planar face lying on the split plane (dot(center,vdir) ~ mid_s)
        cands = []
        for i, f in enumerate(part.Faces()):
            if f.geomType() != "PLANE":
                continue
            c = f.Center()
            s = vdot(c, vdir)
            if abs(s - mid_s) > tol_s:
                continue
            try:
                n = f.normalAt()
            except Exception:
                continue
            n = vunit(n)
            if abs(vdot(n, vdir)) < 0.99:
                continue
            cands.append((f.Area(), i, f, c, n, s))

        print(f"SELECTED: {len(cands)} faces for split-plane cut face candidates")
        if len(cands) == 0:
            return None
        cands.sort(key=lambda t: t[0], reverse=True)
        area, idx, f, c, n, s = cands[0]
        print(f"  cut-face pick: idx={idx} area={area:.3f} center={c.toTuple()} n={n.toTuple()} s={s:.3f}")
        return f

    # ---- pick the sole solid ----
    solids = base.Solids()
    if len(solids) == 0:
        raise ValueError("No solids found in imported STEP")
    if len(solids) != 1:
        print(f"WARNING: expected 1 solid, found {len(solids)}; editing solid[0] only")
    sol = solids[0]

    print(f"INPUT: solids={len(solids)} volume={sol.Volume():.3f} area={sol.Area():.3f}")

    # ---- resolve and print the named faces to confirm index agreement ----
    faces = sol.Faces()
    for fi in [9, 22, 45, 49, 11, 24, 33, 43]:
        f = faces[fi]
        c = f.Center()
        n = vunit(f.normalAt())
        print(f"FACECHK #{fi}: area={f.Area():.3f} center={c.toTuple()} normal={n.toTuple()} type={f.geomType()}")

    # ---- target direction and distances ----
    vH = vunit(cq.Vector(0.0, 0.259, 0.966))
    d_move = 2.5  # mm each side
    target_sep = 591.0

    f22 = faces[22]
    f49 = faces[49]
    s22 = vdot(f22.Center(), vH)
    s49 = vdot(f49.Center(), vH)
    sep0 = s49 - s22
    mid0 = 0.5 * (s49 + s22)
    print(f"REF: s(face#22)={s22:.6f}  s(face#49)={s49:.6f}  sep0={sep0:.6f}  mid0={mid0:.6f}")

    # ---- build split plane at the midpoint between face #22 and #49, normal=vH ----
    c_sol = sol.Center()
    s_sol = vdot(c_sol, vH)
    mid_point = c_sol + vH.multiply(mid0 - s_sol)
    print(f"SPLIT-PLANE: origin={mid_point.toTuple()} normal={vH.toTuple()} (mid_s={mid0:.6f})")

    pl = cq.Plane(origin=mid_point.toTuple(), normal=vH.toTuple(), xDir=(1.0, 0.0, 0.0))
    wp = cq.Workplane(pl)

    bb = sol.BoundingBox()
    S = max(bb.xlen, bb.ylen, bb.zlen) * 6.0
    print(f"TOOLS: big box size S={S:.3f} based on bbox xlen={bb.xlen:.3f} ylen={bb.ylen:.3f} zlen={bb.zlen:.3f}")

    upper_tool = wp.box(S, S, S, centered=(True, True, False)).val()  # from plane to +vH
    lower_tool = wp.transformed(offset=(0, 0, -S)).box(S, S, S, centered=(True, True, False)).val()  # -vH to plane

    upper_part = sol.intersect(upper_tool)
    lower_part = sol.intersect(lower_tool)

    up_sols = upper_part.Solids()
    lo_sols = lower_part.Solids()
    print(f"SPLIT: upper solids={len(up_sols)} lower solids={len(lo_sols)}")
    if len(up_sols) != 1 or len(lo_sols) != 1:
        # fall back: use the shapes directly (may be compounds)
        print("WARNING: split did not yield exactly one solid per side; proceeding with the returned shapes")
        upper = upper_part
        lower = lower_part
    else:
        upper = up_sols[0]
        lower = lo_sols[0]

    print(f"SPLIT-VOL: upper={upper.Volume():.3f} lower={lower.Volume():.3f} sum={upper.Volume()+lower.Volume():.3f} (orig={sol.Volume():.3f})")

    # ---- find the split cut face to build the bridge (extrusion of the cut section) ----
    cut_face = find_cut_face_on_plane(upper, mid0, vH, tol_s=0.5)
    if cut_face is None:
        # try on lower
        cut_face = find_cut_face_on_plane(lower, mid0, vH, tol_s=0.5)
    if cut_face is None:
        print("SELECTED: 0 faces for split-plane cut face (cannot build bridge) -- returning input")
        return shape

    # ---- build bridge: extrude cut face along vH to span the new 5mm gap, with tiny overlap for fusing ----
    overlap = 0.2
    extrude_len = 5.0 + 2.0 * overlap
    bridge_vec = vH.multiply(extrude_len)
    bridge = cq.Solid.extrudeLinear(cut_face, bridge_vec)
    bridge = bridge.translate(vH.multiply(-(d_move + overlap)).toTuple())

    print("BRIDGE:")
    print(f"  extrude_len={extrude_len:.3f} (target gap=5.000, overlap={overlap:.3f})")
    print(f"  bridge center={bridge.Center().toTuple()}")
    bbb = bridge.BoundingBox()
    print(f"  bridge bbox xlen={bbb.xlen:.3f} ylen={bbb.ylen:.3f} zlen={bbb.zlen:.3f}")

    # ---- move the halves ----
    upper_m = upper.translate(vH.multiply(d_move).toTuple())
    lower_m = lower.translate(vH.multiply(-d_move).toTuple())

    # ---- fuse into final ----
    out = lower_m.fuse(bridge).fuse(upper_m)

    # ---- verification: separation of outer faces along vH is 591mm, midpoint unchanged, X envelope unchanged ----
    out_solids = out.Solids()
    if len(out_solids) == 1:
        out_s = out_solids[0]
    else:
        out_s = out  # keep as compound/shape if fuse yields multiple
        print(f"WARNING: output has {len(out_solids)} solids")

    # find extreme planar faces along vH
    vHn = vH
    planar_vH = []
    for i, f in enumerate(out_s.Faces()):
        if f.geomType() != "PLANE":
            continue
        n = vunit(f.normalAt())
        if abs(vdot(n, vHn)) > 0.99:
            planar_vH.append((i, f, f.Area(), f.Center(), n, vdot(f.Center(), vHn)))
    print(f"SELECTED: {len(planar_vH)} planar faces with normals parallel to vH for height verification")

    if len(planar_vH) == 0:
        print("VERIFY: could not find planar vH faces -- returning edited shape anyway")
        return out

    top = max(planar_vH, key=lambda t: t[5])
    bot = min(planar_vH, key=lambda t: t[5])
    it, ft, at, ct, nt, st = top
    ib, fb, ab, cb, nb, sb = bot

    sep1 = st - sb
    mid1 = 0.5 * (st + sb)

    print("VERIFY OUTER FACES (by extreme projection along vH):")
    print(f"  bottom: idx={ib} area={ab:.3f} center={cb.toTuple()} n={nb.toTuple()} s={sb:.6f}")
    print(f"  top:    idx={it} area={at:.3f} center={ct.toTuple()} n={nt.toTuple()} s={st:.6f}")
    print(f"  sep1={sep1:.6f} (target {target_sep:.3f})  delta={sep1-target_sep:.6f}")
    print(f"  mid1={mid1:.6f} (orig mid0 {mid0:.6f})  delta={mid1-mid0:.6f}")

    # envelope checks
    x0 = proj_range_on_dir(sol, cq.Vector(1, 0, 0))
    x1 = proj_range_on_dir(out_s, cq.Vector(1, 0, 0))
    print(f"X-ENVELOPE: before={x0} after={x1} dxmin={(x1[0]-x0[0]):.6f} dxmax={(x1[1]-x0[1]):.6f}")

    # check span along u (perpendicular in YZ to vH): u = [0,0.966,-0.259]
    u = vunit(cq.Vector(0.0, 0.966, -0.259))
    u0 = proj_range_on_dir(sol, u)
    u1 = proj_range_on_dir(out_s, u)
    print(f"PROFILE-SPAN along u=[0,0.966,-0.259]: before={u0} after={u1} dmin={(u1[0]-u0[0]):.6f} dmax={(u1[1]-u0[1]):.6f}")

    bb0 = sol.BoundingBox()
    bb1 = out_s.BoundingBox()
    print(f"BBOX: before xlen={bb0.xlen:.3f} ylen={bb0.ylen:.3f} zlen={bb0.zlen:.3f} | after xlen={bb1.xlen:.3f} ylen={bb1.ylen:.3f} zlen={bb1.zlen:.3f}")

    return out