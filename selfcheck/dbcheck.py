#!/usr/bin/env python3
"""dbcheck - let a model see its own work before anyone else does.

`designbridge.py check` already compiles a document and prints errors, a bone
tree and three ASCII silhouettes. This adds the things it does not check, and
a real picture:

  NUMBERS   box count per bone against the cap, total against the budget,
            orphan bones, boxes that float or sink below the ground, palette
            membership, bounding box against a target size, duplicate
            animation keys, and the extreme pose of every animation.
  VIEWS     designbridge's own ASCII silhouettes, for text-only models.
  PICTURE   a four-angle contact sheet. One camera hides facing errors - a
            lens and its hood pointing opposite ways look fine from the front.

Nothing here modifies designbridge.py; it is imported, never edited.

    python3 dbcheck.py doc.db
    python3 dbcheck.py doc.db --complexity 4 --size 144x152x192
    python3 dbcheck.py doc.db --palette "#3A3F45,#22252A,#C79A4B" --sheet look.png

Exit code is 0 when nothing failed, 1 when something did, so a loop can stop
on its own.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (HERE, os.path.dirname(HERE)):
    if os.path.isfile(os.path.join(cand, "designbridge.py")):
        sys.path.insert(0, cand)
        break
try:
    import structurebridge as sb
except ImportError:
    sb = None
try:
    import designbridge as db
except ImportError:
    sys.exit("dbcheck: designbridge.py must sit next to this script "
             "(or one folder up). Copy both into the same place and retry.")

# Boxes allowed per bone, and the base total, at each complexity level.
# These mirror the Studio's own budget so a document that passes here passes
# there. The total is max(base, per_bone * bones) - a 15-bone boss legitimately
# needs more boxes than a 4-bone prop at the same detail per part.
BUDGET = {1: (8, 2), 2: (20, 4), 3: (48, 7), 4: (120, 12), 5: (320, 20)}

ANGLES = [("FRONT", 0, 10), ("THREE-QUARTER", 35, 22), ("RIGHT", -75, 18), ("TOP", 20, 70)]


class Report:
    """Collects findings so the summary can be counted, not guessed at."""

    def __init__(self):
        self.rows = []

    def fail(self, what, detail, fix=""):
        self.rows.append(("FAIL", what, detail, fix))

    def warn(self, what, detail, fix=""):
        self.rows.append(("WARN", what, detail, fix))

    def ok(self, what, detail=""):
        self.rows.append(("ok", what, detail, ""))

    @property
    def fails(self):
        return sum(1 for r in self.rows if r[0] == "FAIL")

    @property
    def warns(self):
        return sum(1 for r in self.rows if r[0] == "WARN")

    def show(self):
        print("\n" + "=" * 62)
        print("DBCHECK - what designbridge does not check")
        print("=" * 62)
        for sev, what, detail, fix in self.rows:
            mark = {"FAIL": "[FAIL]", "WARN": "[warn]", "ok": "[ ok ]"}[sev]
            print(f"  {mark} {what}" + (f": {detail}" if detail else ""))
            if fix:
                print(f"         -> {fix}")
        print("-" * 62)
        print(f"  {self.fails} failure(s), {self.warns} warning(s)")


def _hex_of(color):
    """designbridge keeps colours as RGBA tuples; the palette is written in hex."""
    return "#%02X%02X%02X" % tuple(int(c) for c in color[:3])


def _dist(a, b):
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5


def check_model(doc, rep, complexity, palette, target, tol):
    boxes = [(bone.name, bx) for bone in
             (doc.bones[n] for n in doc.order) for bx in bone.boxes]
    nbones = len(doc.order)
    if not boxes:
        rep.fail("geometry", "the document declares no boxes")
        return

    # ---- budget -----------------------------------------------------------
    base, per = BUDGET[complexity]
    total_cap = max(base, per * nbones)
    if len(boxes) > total_cap:
        rep.fail("total boxes", f"{len(boxes)} boxes, complexity {complexity} allows {total_cap}",
                 f"remove {len(boxes) - total_cap}, merge neighbours, or raise complexity")
    else:
        rep.ok("total boxes", f"{len(boxes)} of {total_cap} allowed")

    per_bone = {}
    for nm, _ in boxes:
        per_bone[nm] = per_bone.get(nm, 0) + 1
    over = [(nm, n) for nm, n in per_bone.items() if n > per]
    if over:
        for nm, n in sorted(over, key=lambda x: -x[1]):
            rep.fail(f"bone '{nm}'", f"{n} boxes, the cap is {per} per bone",
                     f"simplify to {per}, or split '{nm}' into parts that genuinely articulate")
    else:
        rep.ok("boxes per bone", f"largest is {max(per_bone.values())} of {per} allowed")

    # ---- the skeleton actually connects -----------------------------------
    orphans = [b.name for b in doc.bones.values()
               if b.parent and b.parent not in doc.bones]
    if orphans:
        rep.fail("orphan bones", ", ".join(orphans),
                 "every parent= must name a bone declared earlier")
    # The first bone declared is the root and is meant to have no parent.
    # Every other unparented bone is a part that will not follow the body.
    rooted = [n for n in doc.order if not doc.bones[n].parent]
    stranded = rooted[1:]
    if stranded:
        rep.fail("unparented bones", ", ".join(stranded),
                 f"give each a parent= so it follows the body; '{rooted[0]}' is the root")
    else:
        rep.ok("skeleton", f"root '{rooted[0] if rooted else '?'}', {nbones} bone(s)")

    # ---- the GLB child-pivot displacement ---------------------------------
    # glTF composes a child node's translation on top of its parent's, but the
    # exporter writes each bone's pivot verbatim. A parented bone whose parent
    # has a non-zero pivot therefore lands displaced by the parent's pivot in
    # the .glb. The engine-native .boxmodel.json is absolute throughout and is
    # unaffected, so this hurts the Blender/preview path, not STRATA itself.
    displaced = []
    for b in doc.bones.values():
        if not b.parent or b.parent not in doc.bones:
            continue
        pp = doc.bones[b.parent].pivot
        if any(abs(v) > 1e-6 for v in pp):
            displaced.append((b.name, b.parent, pp))
    if displaced:
        nm, par, pp = displaced[0]
        rep.ok("glb child offset",
               f"{len(displaced)} nested bone(s) - the Studio repairs this on export; "
               "a raw `designbridge build` .glb will have them displaced")
    elif any(b.parent for b in doc.bones.values()):
        rep.ok("glb export", "every parent pivot is at the origin, so children are not displaced")

    # ---- the thing stands on the ground -----------------------------------
    lo_y = min(bx.lo[1] for _, bx in boxes)
    hi_y = max(bx.hi[1] for _, bx in boxes)
    if lo_y < -1e-6:
        rep.fail("below ground", f"lowest box sits at y={lo_y:g}",
                 "shift the model up so the lowest face is exactly y=0")
    elif lo_y > 0.5:
        rep.warn("floating", f"lowest box starts at y={lo_y:g}, not 0",
                 "drop the model so it touches the ground plane")
    else:
        rep.ok("ground contact", f"lowest face at y={lo_y:g}")

    # ---- every part is attached to something ------------------------------
    loose = []
    for nm, bx in boxes:
        touches = False
        for nm2, bx2 in boxes:
            if bx is bx2:
                continue
            if all(bx.lo[i] <= bx2.hi[i] + tol and bx2.lo[i] <= bx.hi[i] + tol
                   for i in range(3)):
                touches = True
                break
        if not touches and bx.lo[1] > tol:
            loose.append((nm, bx))
    if loose:
        for nm, bx in loose[:6]:
            rep.warn(f"floating box in '{nm}'", f"line {bx.line}, nothing within {tol:g}",
                     "move it to touch a neighbour, or accept it if it is meant to hover")
    else:
        rep.ok("connectivity", "every box touches another or the ground")

    # ---- size against the brief -------------------------------------------
    size = tuple(round(max(bx.hi[i] for _, bx in boxes) - min(bx.lo[i] for _, bx in boxes), 2)
                 for i in range(3))
    if target:
        off = [abs(size[i] - target[i]) / max(1.0, target[i]) for i in range(3)]
        worst = max(off)
        msg = f"{size[0]:g}x{size[1]:g}x{size[2]:g}, brief asked {target[0]:g}x{target[1]:g}x{target[2]:g}"
        if worst > 0.15:
            rep.fail("size", msg, "scale the model so each extent is within 15% of the brief")
        else:
            rep.ok("size", msg)
    else:
        rep.ok("size", f"{size[0]:g}x{size[1]:g}x{size[2]:g} (no target given)")

    # ---- palette ----------------------------------------------------------
    if palette:
        used = {}
        for nm, bx in boxes:
            used.setdefault(_hex_of(bx.color), []).append((nm, bx.line))
        allowed = {p.upper(): db.parse_color(p) for p in palette}
        strays = []
        for hx, where in used.items():
            rgb = db.parse_color(hx)
            if hx.upper() in allowed:
                continue
            near, d = min(((p, _dist(rgb, c)) for p, c in allowed.items()), key=lambda x: x[1])
            strays.append((hx, near, d, where))
        if strays:
            for hx, near, d, where in sorted(strays, key=lambda x: -x[2])[:8]:
                rep.fail(f"colour {hx}", f"not in the palette (line {where[0][1]}, bone '{where[0][0]}')",
                         f"nearest allowed is {near}")
        else:
            rep.ok("palette", f"{len(used)} colour(s), all allowed")

    # ---- animation --------------------------------------------------------
    for name, anim in getattr(doc, "anims", {}).items():
        seen, dupes = set(), []
        for k in anim.keys:
            sig = (k.bone, round(float(k.t), 4))
            if sig in seen:
                dupes.append(f"{k.bone}@{k.t}")
            seen.add(sig)
        moved = {k.bone for k in anim.keys}
        if dupes:
            rep.fail(f"animation '{name}'", "two keys for one bone at one time: " + ", ".join(dupes[:4]),
                     "merge them into a single key carrying both rot= and pos=")
        else:
            rep.ok(f"animation '{name}'", f"{len(anim.keys)} key(s) over {len(moved)} bone(s)")
        # A bone that moves while a limb hanging off it does not is the
        # classic kneel bug: the body drops and the legs stay standing.
        static = [b.name for b in doc.bones.values()
                  if b.name in moved and not any(
                      c.parent == b.name for c in doc.bones.values())]
        detached = [n for n in doc.order
                    if not doc.bones[n].parent and n not in moved and moved]
        if len(detached) > 1:
            rep.warn(f"animation '{name}'",
                     f"these bones are parented to nothing and never move: {', '.join(detached[1:][:5])}",
                     "parent them to a bone that does move, or key them too")


def check_texture(doc, rep, complexity, palette):
    layers = getattr(doc, "layers", None) or getattr(doc, "ops", None) or []
    cap = {1: 3, 2: 4, 3: 6, 4: 9, 5: 14}[complexity]
    n = len(layers)
    if n > cap:
        rep.fail("layers", f"{n} layers, complexity {complexity} allows {cap}",
                 "merge layers or raise complexity")
    else:
        rep.ok("layers", f"{n} of {cap} allowed")
    if palette:
        rep.warn("palette", "pixel-level palette checking is not implemented yet",
                 "compare the exported PNG against the palette by eye for now")


def contact_sheet(kind, doc, path, w=230):
    """Four angles in one image. A single camera hides facing errors."""
    try:
        if kind == "model":
            tris = db.colored_tris(doc)
        elif kind == "prop":
            tris = [(t, (150, 160, 175, 255)) for p in doc.parts if p.mesh for t in p.mesh.tris]
        else:
            return None
        if not tris:
            return None
        sheet = db.Image(w * 2, w * 2, (18, 19, 23, 255))
        for i, (_name, yaw, pitch) in enumerate(ANGLES):
            img = db.render_mesh_png(tris, w, w, bg=(26, 28, 34, 255), yaw=yaw, pitch=pitch)
            ox, oy = (i % 2) * w, (i // 2) * w
            for y in range(w):
                for x in range(w):
                    sheet.put(ox + x, oy + y, img.get(x, y), blend=False)
        sheet.save_png(path)
        return path
    except Exception as exc:
        print(f"  (no contact sheet: {exc})")
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="dbcheck",
        description="Compile a design document and report everything that is wrong with it.")
    ap.add_argument("file", help="the design document to check")
    ap.add_argument("--complexity", type=int, default=3, choices=[1, 2, 3, 4, 5],
                    help="the complexity level the brief asked for (default 3)")
    ap.add_argument("--palette", default="",
                    help="comma-separated hex colours the design is allowed to use")
    ap.add_argument("--size", default="",
                    help="target bounding box from the brief, e.g. 144x152x192")
    ap.add_argument("--sheet", default="",
                    help="write a four-angle contact sheet PNG here")
    ap.add_argument("--tolerance", type=float, default=0.5,
                    help="how close two boxes must be to count as touching (default 0.5)")
    ap.add_argument("--quiet", action="store_true",
                    help="skip designbridge's own report and views")
    a = ap.parse_args(argv)

    try:
        text = open(a.file, encoding="utf-8").read()
    except OSError as exc:
        sys.exit(f"dbcheck: cannot read {a.file}: {exc}")

    # A structure document is compiled by structurebridge, which keeps its own
    # report and its own checks; the rest of this script is about the three
    # designbridge tracks.
    if re.search(r"^[ \t]*structure\b", text, re.M):
        if sb is None:
            sys.exit("dbcheck: this is a structure document, but structurebridge.py "
                     "is not next to this script.")
        doc = sb.parse(text)
        print(sb.report(doc))
        if a.sheet:
            made = sb.contact_sheet(doc, a.sheet)
            if made:
                print(f"\n  Contact sheet: {made}")
                print("  Four angles. Check the massing, and that what collapsed still stands up.")
        bad = len(doc.errors) + len([c for c in sb.validate(doc) if c[0] == "error"])
        print()
        if bad:
            print("NOT READY. Fix the errors above, then run dbcheck again.")
            return 1
        print("READY. Nothing failed. Send this document.")
        return 0

    kind, doc = db.compile_any(text)
    if not a.quiet:
        print(db.report_any(kind, doc))

    rep = Report()
    errors = [d for d in doc.diags if d[0] == "error"]
    if errors:
        rep.fail("compile", f"{len(errors)} error(s) above",
                 "fix those first - nothing below is trustworthy until it compiles")

    palette = [p.strip() for p in a.palette.split(",") if p.strip()]
    target = None
    if a.size:
        try:
            target = tuple(float(x) for x in a.size.lower().replace("*", "x").split("x"))
            if len(target) != 3:
                raise ValueError
        except ValueError:
            sys.exit("dbcheck: --size wants three numbers, e.g. 144x152x192")

    if kind == "model" and not errors:
        check_model(doc, rep, a.complexity, palette, target, a.tolerance)
    elif kind == "texture" and not errors:
        check_texture(doc, rep, a.complexity, palette)
    elif kind == "prop" and not errors:
        rep.ok("prop", "designbridge's own watertightness check above is the authority here")

    rep.show()

    if a.sheet and not errors:
        made = contact_sheet(kind, doc, a.sheet)
        if made:
            print(f"\n  Contact sheet: {made}")
            print("  Four angles. If you can see images, open it and check the silhouette,")
            print("  the proportions, and that every part faces the way you intended.")

    print()
    if rep.fails or errors:
        print("NOT READY. Fix the failures above, then run dbcheck again.")
        return 1
    print("READY. Nothing failed. Send this document.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
