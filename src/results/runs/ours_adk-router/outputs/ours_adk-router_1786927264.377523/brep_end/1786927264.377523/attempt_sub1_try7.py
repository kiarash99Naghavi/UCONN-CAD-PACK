def my_cad_function(args):
    import cadquery as cq
    import math

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- OCP imports (use *_s statics; the non-suffixed names were rejected) ---
    from OCP.TopExp import TopExp
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    from OCP.TopoDS import TopoDS
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_BSplineSurface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
    from OCP.TopExp import TopExp_Explorer

    def vfmt(v):
        return f"({v.x:.3f},{v.y:.3f},{v.z:.3f})"

    def bbfmt(bb):
        return (
            f"min=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}) "
            f"max=({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f})"
        )

    # Numbers named by the sub-goal
    bore_r = 14.1421
    bore_axis = (1.0, 0.0, 0.0)
    bore_yz = (270.0, -400.0)
    bore_xspan = (100.0, 300.0)
    bbox_target = (0.0, 300.0, 200.0, 320.0, -445.0, -340.0)
    print(
        "NUMBERS: protected bore r=14.1421 axis||+X center (Y,Z)=(270,-400) X=100..300; "
        "blind opening planes at Y=230 and X=300; required outer bbox "
        "X=0..300 Y=200..320 Z=-445..-340"
    )

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("SELECTED: 0 solids (ERROR)")
        return shape

    solid = sols[0]
    bb0 = solid.BoundingBox()
    print(f"INFO: base bbox {bbfmt(bb0)}")

    faces = solid.Faces()
    edges = solid.Edges()
    print(f"INFO: solid faces={len(faces)} edges={len(edges)}")

    # --- Build adjacency: edge -> faces (on unchanged solid) ---
    # Use OCP MapShapesAndAncestors_s for robust adjacency.
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(solid.wrapped, TopAbs_EDGE, TopAbs_FACE, m)

    def adj_faces_of_edge(e_wrapped):
        # returns list of TopoDS_Face
        if not m.Contains(e_wrapped):
            return []
        lst = m.FindFromKey(e_wrapped)
        out = []
        it = lst.cbegin()
        while it.More():
            out.append(TopoDS.Face_s(it.Value()))
            it.Next()
        return out

    # Helper: find face index by IsSame
    def face_index_from_wrapped(fw):
        for i, f in enumerate(faces):
            if f.wrapped.IsSame(fw):
                return i
        return None

    # --- Resolve key faces by index for diagnostics (must match prompt index) ---
    diag_idxs = [5, 29, 33, 23, 25, 20, 0, 14, 1, 15, 11]
    for di in diag_idxs:
        if 0 <= di < len(faces):
            f = faces[di]
            try:
                print(
                    f"INFO: resolved face #{di} type={f.geomType()} area={f.Area():.3f} center={vfmt(f.Center())} normal={vfmt(f.normalAt())} wires={len(f.Wires())}"
                )
            except Exception as e:
                print(f"WARN: could not print diagnostics for face #{di}: {e}")

    # --- Protected set ---
    protected = set()

    # 1) Protect the full 360-degree cylindrical bore face (face#33 per index).
    if 0 <= 33 < len(faces):
        protected.add(33)
        print("SELECTED: 1 face for protected full-bore cylindrical wall  idx=[33]")
    else:
        print("SELECTED: 0 faces for protected full-bore cylindrical wall (ERROR: face#33 missing)")

    # 2) Protect faces reachable inward from inner opening loops on face#5 (Y=230) and face#29 (X=300)
    seed_faces = []
    seed_edge_w = []
    for fi in [5, 29]:
        if not (0 <= fi < len(faces)):
            continue
        f = faces[fi]
        ws = f.Wires()
        if len(ws) <= 1:
            print(f"INFO: face#{fi} has no inner wires; cannot seed inward protection from loops")
            continue
        inner = ws[1:]
        # collect inner-loop edges
        for w in inner:
            for e in w.Edges():
                seed_edge_w.append(e.wrapped)
        seed_faces.append(fi)

    print(f"SELECTED: {len(seed_faces)} opening faces with inner loops for inward-protection seed  idx={seed_faces}")
    print(f"SELECTED: {len(seed_edge_w)} edges from inner loops for inward-protection traversal seed")

    # BFS from faces adjacent to those inner-loop edges
    q = []
    seen = set(protected)

    for ew in seed_edge_w:
        for afw in adj_faces_of_edge(ew):
            afi = face_index_from_wrapped(afw)
            if afi is None:
                continue
            if afi not in seen:
                seen.add(afi)
                q.append(afi)

    # Also include the opening faces themselves
    for fi in seed_faces:
        if fi not in seen:
            seen.add(fi)
            q.append(fi)

    # Conservative inward traversal: do NOT cross into known exterior treatment candidates (defined later),
    # but do traverse across planar/cylindrical interior cavity walls.
    # Since we don't yet know the candidate set, first do a limited traversal by restricting to faces
    # whose centers are reasonably close to the openings region (Y around 230 or X around 300) OR are the bore.
    # This prevents runaway protection over the entire exterior.
    def near_opening_region(f):
        c = f.Center()
        return (abs(c.y - 230.0) < 60.0) or (abs(c.x - 300.0) < 60.0) or (abs(c.y - 270.0) < 60.0 and abs(c.z + 400.0) < 60.0)

    while q:
        fi = q.pop(0)
        protected.add(fi)
        f = faces[fi]
        if not near_opening_region(f) and fi != 33:
            continue
        for e in f.Edges():
            afws = adj_faces_of_edge(e.wrapped)
            for afw in afws:
                afi = face_index_from_wrapped(afw)
                if afi is None or afi in seen:
                    continue
                # Only traverse through non-decorative surface types (avoid accidentally protecting exterior blends)
                at = faces[afi]
                ga = BRepAdaptor_Surface(at.wrapped, True)
                gt = ga.GetType()
                if gt in (GeomAbs_Sphere, GeomAbs_BSplineSurface):
                    continue
                # cones are often chamfers at openings, so don't traverse into cones either
                if gt == GeomAbs_Cone:
                    continue
                seen.add(afi)
                q.append(afi)

    print(f"SELECTED: {len(protected)} faces protected (bore + inward from opening loops)  idx={sorted(list(protected))}")

    # --- Identify candidate exterior treatments (blend/bevel) by adjacency + measured signatures ---
    # We only consider non-protected faces.

    # Helper: get UV bounds (fixed API name UVBounds_s)
    def uv_bounds(face_wrapped):
        # BRepTools.UVBounds_s returns (u1,u2,v1,v2)
        try:
            u1, u2, v1, v2 = BRepTools.UVBounds_s(face_wrapped)
            return float(u1), float(u2), float(v1), float(v2)
        except Exception as e:
            print(f"WARN: UVBounds_s failed on a face: {e}")
            return None

    # Helper: classify by surface signature for candidate list
    def is_candidate_signature(fi):
        f = faces[fi]
        ga = BRepAdaptor_Surface(f.wrapped, True)
        gt = ga.GetType()

        if gt == GeomAbs_Cylinder:
            cyl = ga.Cylinder()
            r = float(cyl.Radius())
            uv = uv_bounds(f.wrapped)
            if uv is None:
                return False
            u1, u2, v1, v2 = uv
            sweep_deg = abs(u2 - u1) * 180.0 / math.pi
            # Candidate exterior cylinders from prompt; do NOT select full bores.
            if sweep_deg > 300.0:
                return False
            # keep only radii in the named families
            for rr in (30.0, 35.0, 10.0, 5.0, 2.5):
                if abs(r - rr) < 1e-2:
                    # Only corner-blend sized sweeps
                    if 10.0 <= sweep_deg <= 140.0:
                        return True
            return False

        if gt == GeomAbs_Cone:
            # Exterior cones near the given centers are chamfer-like; treat as candidate
            return True

        if gt == GeomAbs_Sphere:
            return True

        if gt == GeomAbs_BSplineSurface:
            return True

        if gt == GeomAbs_Plane:
            # Planar bevel strips have oblique normals including
            # [0,-0.707,-0.707], [0,-0.707,0.707], and [0.707,0,-0.707]
            n = f.normalAt()
            # normalize
            ln = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z) or 1.0
            nx, ny, nz = n.x / ln, n.y / ln, n.z / ln
            targets = [
                (0.0, -0.70710678, -0.70710678),
                (0.0, -0.70710678, 0.70710678),
                (0.70710678, 0.0, -0.70710678),
            ]
            for tx, ty, tz in targets:
                dot = nx * tx + ny * ty + nz * tz
                if abs(dot) > 0.995:
                    return True
            return False

        return False

    candidate = [fi for fi in range(len(faces)) if fi not in protected and is_candidate_signature(fi)]
    print(f"SELECTED: {len(candidate)} faces matching candidate treatment signatures (non-protected)  idx={sorted(candidate)}")

    # Build neighbor list for each candidate and classify by bridging 2 or 3 support faces
    # A face is treated as exterior blend/bevel ONLY when it bridges two or three exterior support surfaces.
    # Here we interpret "support surfaces" as adjacent non-candidate, non-protected faces.
    candidate_set = set(candidate)

    treatment_faces = set()
    support_faces = set()
    trihedral_faces = set()

    for fi in candidate:
        f = faces[fi]
        neigh = set()
        for e in f.Edges():
            afws = adj_faces_of_edge(e.wrapped)
            for afw in afws:
                afi = face_index_from_wrapped(afw)
                if afi is None or afi == fi:
                    continue
                neigh.add(afi)
        # supports: non-candidate, non-protected
        supports = [nfi for nfi in neigh if (nfi not in candidate_set) and (nfi not in protected)]
        # If candidate meets only other candidates/protected, it's not a confirmed exterior treatment.
        if len(set(supports)) in (2, 3):
            treatment_faces.add(fi)
            for sfi in set(supports):
                support_faces.add(sfi)
            if len(set(supports)) == 3:
                trihedral_faces.add(fi)

        print(
            f"INFO: candidate face#{fi} type={f.geomType()} center={vfmt(f.Center())} neigh={sorted(list(neigh))} "
            f"supports(non-cand/non-prot)={sorted(list(set(supports)))}"
        )

    print(f"SELECTED: {len(treatment_faces)} CONFIRMED treatment faces (bridge 2 or 3 supports)  idx={sorted(list(treatment_faces))}")
    print(f"SELECTED: {len(trihedral_faces)} trihedral treatment faces (3 supports)  idx={sorted(list(trihedral_faces))}")
    print(f"SELECTED: {len(support_faces)} support faces to reconstruct  idx={sorted(list(support_faces))}")

    # If nothing confirmed, force a non-empty selection by falling back to the explicit measured candidates by index
    # (still excluding protected). This prevents a silent no-op.
    if len(treatment_faces) == 0:
        fallback = [i for i in [4, 6, 10, 12, 20, 23, 25, 27, 30, 32, 0, 14, 1, 11, 15] if 0 <= i < len(faces) and i not in protected]
        treatment_faces = set(fallback)
        print(f"WARN: 0 confirmed treatments by adjacency; FALLBACK selecting by known candidate indices  idx={sorted(list(treatment_faces))}")

        # collect supports from fallback
        support_faces = set()
        for fi in sorted(list(treatment_faces)):
            f = faces[fi]
            neigh = set()
            for e in f.Edges():
                for afw in adj_faces_of_edge(e.wrapped):
                    afi = face_index_from_wrapped(afw)
                    if afi is None or afi == fi or afi in protected or afi in treatment_faces:
                        continue
                    neigh.add(afi)
            # take up to 3 neighbors as supports
            for sfi in list(neigh)[:3]:
                support_faces.add(sfi)
        print(f"SELECTED: {len(support_faces)} fallback support faces to reconstruct  idx={sorted(list(support_faces))}")

    # --- Rebuild support faces as extended surface patches (no booleans; use sewing+cutting to retrim) ---
    patch_faces_wrapped = []
    replaced_ok = set()

    for si in sorted(list(support_faces)):
        if si in protected:
            print(f"INFO: support face idx={si} is protected; not reconstructing")
            continue

        sf = faces[si]
        if len(sf.Wires()) > 1:
            # Rebuilding would drop inner loops; keep original to preserve openings.
            print(f"INFO: support face idx={si} has inner wires (loops); not reconstructing to preserve openings")
            continue

        try:
            # FIXED API: Surface_s
            surf = BRep_Tool.Surface_s(sf.wrapped)
            uv = uv_bounds(sf.wrapped)
            if uv is None:
                print(f"WARN: support face idx={si} has no UV bounds; skipping")
                continue
            u1, u2, v1, v2 = uv

            ga = BRepAdaptor_Surface(sf.wrapped, True)
            gt = ga.GetType()

            # Choose extension amounts based on surface type
            # NOTE: these are param extensions (radians for cylinder U).
            if gt == GeomAbs_Plane:
                du, dv = 200.0, 200.0
            elif gt == GeomAbs_Cylinder:
                # extend angular range modestly and axial range substantially
                du, dv = 0.75, 120.0
            elif gt == GeomAbs_Cone:
                du, dv = 0.75, 120.0
            else:
                # Avoid reconstructing exotic supports; keep original
                print(f"INFO: support face idx={si} surfaceType={gt} not reconstructed")
                continue

            mf = BRepBuilderAPI_MakeFace(surf, u1 - du, u2 + du, v1 - dv, v2 + dv, 1e-7)
            if not mf.IsDone():
                print(f"WARN: MakeFace failed for support face idx={si}; keeping original")
                continue

            newf = mf.Face()
            patch_faces_wrapped.append(newf)
            replaced_ok.add(si)

            print(
                f"SELECTED: rebuilt support face idx={si} geomType={sf.geomType()} UV=({u1:.6f},{u2:.6f},{v1:.6f},{v2:.6f}) "
                f"ext=(du={du},dv={dv})"
            )
        except Exception as e:
            print(f"WARN: could not rebuild support face idx={si}: {e}")

    print(f"SELECTED: {len(replaced_ok)} support faces successfully rebuilt  idx={sorted(list(replaced_ok))}")
    print(f"SELECTED: {len(patch_faces_wrapped)} extended support face patches to add")

    if len(patch_faces_wrapped) == 0:
        # No replacements means removing treatments would create an open shell; do not proceed.
        # However returning unchanged would be a no-op. Instead, keep treatments (no removal) and report.
        print("ERROR: 0 support patches created; cannot perform shell replacement without opening the model. Returning input unchanged.")
        return shape

    # --- Build explicitly bounded replacement shell: keep unaffected originals, remove treatments & replaced supports ---
    keep_wrapped = []
    removed_all = set(treatment_faces) | set(replaced_ok)

    for i, f in enumerate(faces):
        if i in protected:
            keep_wrapped.append(f.wrapped)
            continue
        if i in removed_all:
            continue
        keep_wrapped.append(f.wrapped)

    print(f"SELECTED: {len(keep_wrapped)} original faces kept for sewing")
    print(f"SELECTED: {len(removed_all)} original faces removed (treatments + old trims of rebuilt supports)  idx={sorted(list(removed_all))}")

    # Sew with cutting enabled so extended faces are trimmed by their intersections.
    sew = BRepBuilderAPI_Sewing(1e-6, True, True, True, False)
    for fw in keep_wrapped:
        sew.Add(fw)
    for pf in patch_faces_wrapped:
        sew.Add(pf)

    sew.Perform()
    sewed = sew.SewedShape()

    shells = []
    ex = TopExp_Explorer(sewed, TopAbs_SHELL)
    while ex.More():
        shells.append(TopoDS.Shell_s(ex.Current()))
        ex.Next()

    print(f"INFO: sewing produced shells={len(shells)}")
    if len(shells) != 1:
        print("ERROR: sewing did not produce exactly 1 shell; returning input unchanged")
        return shape

    ms = BRepBuilderAPI_MakeSolid()
    ms.Add(shells[0])
    if not ms.IsDone():
        print("ERROR: MakeSolid not done; returning input unchanged")
        return shape

    out = cq.Shape.cast(ms.Solid())

    # --- Validate: outer envelope unchanged ---
    bb1 = out.BoundingBox()
    print(f"CHECK: output bbox {bbfmt(bb1)}")

    deltas = [
        abs(bb1.xmin - bbox_target[0]),
        abs(bb1.xmax - bbox_target[1]),
        abs(bb1.ymin - bbox_target[2]),
        abs(bb1.ymax - bbox_target[3]),
        abs(bb1.zmin - bbox_target[4]),
        abs(bb1.zmax - bbox_target[5]),
    ]
    db = max(deltas)
    print(f"VERIFY: bbox max-delta vs required envelope = {db:.6f} mm")

    # Placement self-check: isolate changed material (diagnostic)
    try:
        # added/removed are computed via booleans ONLY as diagnostics; model editing here is sewing-based.
        removed_dbg = solid.cut(out)
        added_dbg = out.cut(solid)
        print(f"CHECK: diagnostic removed volume={removed_dbg.Volume():.3f} mm^3")
        print(f"CHECK: diagnostic added volume={added_dbg.Volume():.3f} mm^3")
        if removed_dbg.Volume() > 1e-6:
            rbb = removed_dbg.BoundingBox()
            print(f"CHECK: diagnostic removed bbox {bbfmt(rbb)}")
        if added_dbg.Volume() > 1e-6:
            abb = added_dbg.BoundingBox()
            print(f"CHECK: diagnostic added bbox {bbfmt(abb)}")
    except Exception as e:
        print(f"WARN: diagnostic added/removed computation failed: {e}")

    if db > 0.05:
        print("ERROR: envelope changed beyond tolerance; rejecting reconstruction and returning input unchanged.")
        return shape

    try:
        print(f"CHECK: output valid={out.isValid()}")
    except Exception as e:
        print(f"WARN: could not validate output isValid(): {e}")

    try:
        osols = out.Solids()
        print(f"CHECK: output solids={len(osols)}")
        if len(osols) != 1:
            print("ERROR: output solid count != 1; returning input unchanged")
            return shape
    except Exception as e:
        print(f"WARN: could not count output solids: {e}")

    if len(sols) == 1:
        return out

    # Multi-solid safety: re-compound untouched solids
    comp = cq.Compound.makeCompound([s for i, s in enumerate(sols) if i != 0] + [out])
    return comp