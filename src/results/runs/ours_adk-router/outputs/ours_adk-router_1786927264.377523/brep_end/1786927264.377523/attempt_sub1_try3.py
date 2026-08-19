def my_cad_function(args):
    import cadquery as cq
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def vfmt(v):
        return f"({v.x:.3f},{v.y:.3f},{v.z:.3f})"

    def dist(a, b):
        return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5

    def largest_solid(shp):
        sols = shp.Solids() if hasattr(shp, "Solids") else []
        if not sols:
            return None
        return max(sols, key=lambda s: s.Volume())

    def invalid_solid_count(shp):
        try:
            sols = shp.Solids()
        except Exception:
            return 1
        bad = 0
        for s in sols:
            try:
                if not s.isValid():
                    bad += 1
            except Exception:
                bad += 1
        return bad

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("SELECTED: 0 solids (ERROR)")
        return shape

    # Work on the (only) solid; on multi-body we'd pick by ownership, but file reports solids=1.
    solid = sols[0]
    bb0 = solid.BoundingBox()
    print(
        "INFO: base bbox "
        f"min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) "
        f"max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f})"
    )

    # ---- Numbers named by the sub-goal (preserve functional bore) ----
    preserve_r = 14.1421
    rtol = 0.20
    preserve_bspline_center = cq.Vector(88.268, 208.538, -437.769)  # face #20 center in the provided index
    print("NUMBERS:")
    print(f"  preserve bore radius r={preserve_r:.4f} mm (tolerance ±{rtol:.3f})")
    print(f"  preserve BSPLINE face near center={vfmt(preserve_bspline_center)}")

    # Witness bore circular edges (r ~ 14.1421)
    def bore_edge_indices(shp):
        out = []
        for i, e in enumerate(shp.Edges()):
            if e.geomType() != "CIRCLE":
                continue
            try:
                r = e.radius()
            except Exception:
                continue
            if abs(r - preserve_r) <= rtol:
                out.append(i)
        return out

    bore_before = bore_edge_indices(solid)
    print(
        f"SELECTED: {len(bore_before)} circular edges with r≈{preserve_r:.4f}±{rtol:.3f} (bore witness) "
        f"before idx={(bore_before[:40] + (["..."] if len(bore_before) > 40 else []))}"
    )

    # ---- Defeaturing helper ----
    def defeature_once(cur_solid, face_to_remove):
        d = BRepAlgoAPI_Defeaturing()
        d.SetShape(cur_solid.wrapped)
        d.AddFaceToRemove(face_to_remove.wrapped)
        d.Build()
        res = cq.Shape.cast(d.Shape())
        # normalize to a solid if possible
        sol = largest_solid(res)
        if sol is None:
            return None
        try:
            sol = sol.clean()
        except Exception:
            pass
        return sol

    # ---- Target blend/chamfer face descriptors (anchored by index-provided centers/radii) ----
    # These correspond to the explicitly cited blend/chamfer families (except the functional bore which we preserve).
    targets = [
        # CONE chamfers (#0 and #4 in the index listing)
        {"name": "cone chamfer near z~-442", "type": "CONE", "center": (249.416, 238.592, -442.406), "radius": None, "dmax": 25.0},
        {"name": "cone chamfer near z~-343", "type": "CONE", "center": (249.416, 238.592, -342.594), "radius": None, "dmax": 25.0},

        # SPHERE corner (#23)
        {"name": "sphere corner", "type": "SPHERE", "center": (13.433, 213.433, -432.500), "radius": None, "dmax": 30.0},

        # BSPLINE corner to remove (#25) - keep the BSPLINE at preserve_bspline_center (face #20)
        {"name": "bspline corner (remove)", "type": "BSPLINE", "center": (8.539, 308.268, -437.770), "radius": None, "dmax": 30.0},

        # Cylindrical corner blends
        {"name": "cyl blend r30 @ (89.887,211.675,-388.245)", "type": "CYLINDER", "center": (89.887, 211.675, -388.245), "radius": 30.0, "dmax": 40.0},
        {"name": "cyl blend r30 @ (56.041,205.083,-434.497)", "type": "CYLINDER", "center": (56.041, 205.083, -434.497), "radius": 30.0, "dmax": 40.0},
        {"name": "cyl blend r30 @ (4.954,265.938,-434.251)", "type": "CYLINDER", "center": (4.954, 265.938, -434.251), "radius": 30.0, "dmax": 50.0},
        {"name": "cyl blend r30 @ (11.581,309.839,-388.266)", "type": "CYLINDER", "center": (11.581, 309.839, -388.266), "radius": 30.0, "dmax": 50.0},
        {"name": "cyl blend r30 @ (10.901,210.901,-380.000)", "type": "CYLINDER", "center": (10.901, 210.901, -380.000), "radius": 30.0, "dmax": 50.0},

        {"name": "cyl blend r35 @ (251.045,237.040,-392.500)", "type": "CYLINDER", "center": (251.045, 237.040, -392.500), "radius": 35.0, "dmax": 40.0},
        {"name": "cyl blend r10 @ (296.311,290.760,-391.429)", "type": "CYLINDER", "center": (296.311, 290.760, -391.429), "radius": 10.0, "dmax": 40.0},
        {"name": "cyl blend r5 @ (99.965,232.852,-444.516)", "type": "CYLINDER", "center": (99.965, 232.852, -444.516), "radius": 5.0, "dmax": 35.0},
        {"name": "cyl blend r5 @ (98.183,318.183,-392.500)", "type": "CYLINDER", "center": (98.183, 318.183, -392.500), "radius": 5.0, "dmax": 35.0},
        {"name": "cyl blend r2.5 @ (299.086,251.114,-390.457)", "type": "CYLINDER", "center": (299.086, 251.114, -390.457), "radius": 2.5, "dmax": 35.0},
    ]

    print(f"INFO: defeature targets={len(targets)} (will skip if not found or would break validity)")

    cur = solid
    v_start = solid.Volume()
    successes = 0

    for t in targets:
        faces = cur.Faces()
        want_type = t["type"]
        want_center = cq.Vector(*t["center"])
        want_r = t.get("radius", None)
        dmax = t.get("dmax", 30.0)

        # filter candidates
        cands = []
        for f in faces:
            if f.geomType() != want_type:
                continue
            # preserve the functional bore: any CYLINDER near r=14.1421
            if f.geomType() == "CYLINDER":
                try:
                    rr = f.radius()
                except Exception:
                    rr = None
                if rr is not None and abs(rr - preserve_r) <= rtol:
                    continue
                if want_r is not None and (rr is None or abs(rr - want_r) > 0.35):
                    continue
            if f.geomType() == "BSPLINE":
                # preserve BSPLINE face #20 by center proximity
                if dist(f.Center(), preserve_bspline_center) < 5.0:
                    continue
            cands.append(f)

        print(f"SELECTED: {len(cands)} faces candidate for {t['name']} (type={want_type}" + (f", r≈{want_r}" if want_r is not None else "") + ")")
        if not cands:
            continue

        # choose nearest to target center
        best = min(cands, key=lambda f: dist(f.Center(), want_center))
        dsel = dist(best.Center(), want_center)
        info_r = ""
        if best.geomType() in ("CYLINDER", "CONE", "SPHERE"):
            try:
                info_r = f" r={best.radius():.4f}"
            except Exception:
                info_r = ""
        print(
            f"INFO: picked face for {t['name']} area={best.Area():.3f} center={vfmt(best.Center())}{info_r}  "
            f"dist_to_target={dsel:.3f} mm (limit {dmax:.1f})"
        )
        if dsel > dmax:
            print(f"WARN: face too far from target center; skipping {t['name']}")
            continue

        # attempt defeature
        before_bore = bore_edge_indices(cur)
        before_bad = invalid_solid_count(cur)
        v_before = cur.Volume()

        out = None
        try:
            out = defeature_once(cur, best)
        except Exception as e:
            print(f"FAIL: defeaturing exception for {t['name']}: {e}")
            out = None

        if out is None:
            print(f"FAIL: defeaturing produced no solid for {t['name']}")
            continue

        # validate
        out_bad = invalid_solid_count(out)
        bore_after = bore_edge_indices(out)
        ok_bore = (len(bore_after) == len(before_bore))
        ok_valid = (out_bad <= before_bad and out.isValid())

        print(
            f"CHECK: {t['name']}  volume {v_before:.3f} -> {out.Volume():.3f} (dV={out.Volume()-v_before:+.3f})  "
            f"invalidSolids {before_bad} -> {out_bad}  boreWitnessEdges {len(before_bore)} -> {len(bore_after)}"
        )

        if not ok_bore:
            print(f"REJECT: {t['name']} would alter bore witness edges; keeping current solid")
            continue

        if not ok_valid:
            # one more attempt to heal
            try:
                out2 = out.clean()
            except Exception:
                out2 = out
            out2_bad = invalid_solid_count(out2)
            ok_valid2 = (out2_bad <= before_bad and out2.isValid())
            print(f"CHECK: heal(clean) invalidSolids {out_bad} -> {out2_bad}  isValid={getattr(out2, 'isValid', lambda: False)()}")
            if not ok_valid2:
                print(f"REJECT: {t['name']} would invalidate solid; keeping current solid")
                continue
            out = out2

        cur = out
        successes += 1
        print(f"OK: accepted defeature for {t['name']}  (successes so far: {successes})")

    # Final checks / self-report
    v_end = cur.Volume()
    bb1 = cur.BoundingBox()
    print(f"RESULT: defeaturing successes={successes} of targets={len(targets)}")
    print(f"RESULT: volume {v_start:.3f} -> {v_end:.3f} (delta {v_end - v_start:+.3f})")
    print(
        "RESULT: bbox "
        f"min=({bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}) "
        f"max=({bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f})"
    )
    bore_after_final = bore_edge_indices(cur)
    print(
        f"SELECTED: {len(bore_after_final)} circular edges with r≈{preserve_r:.4f}±{rtol:.3f} (bore witness) "
        f"after idx={(bore_after_final[:40] + (["..."] if len(bore_after_final) > 40 else []))}"
    )

    # Guarantee: never return a newly-invalid solid; if somehow invalid, fall back to last known-good (original)
    if not cur.isValid():
        print("ERROR: final edited solid is invalid; falling back to original solid")
        cur = solid

    if len(sols) == 1:
        return cur

    out = cq.Compound.makeCompound([s for s in sols if s is not solid] + [cur])
    return out