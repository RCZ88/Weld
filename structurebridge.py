#!/usr/bin/env python3
"""structurebridge - buildings out of textured blocks.

The fourth track. A structure is a volume of cells; each cell carries one of
the Core 12 forms, an orientation, a material, and an optional cladding skin.
It compiles to the engine's 64-bit sparse cell record, so what this writes is
exactly what STRATA reads.

designbridge.py is imported, never edited: it supplies the PNG writer and the
software renderer, and it compiles the textures that materials bind to.

    python3 structurebridge.py check  ruin.sb
    python3 structurebridge.py build  ruin.sb --out ./out

The grammar composes volumes rather than listing cells, because a 24x12x24
building is 7,000 cells and nobody - model or human - writes that by hand.
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (HERE, os.path.join(HERE, "selfcheck"), os.path.dirname(HERE)):
    if os.path.isfile(os.path.join(cand, "designbridge.py")):
        sys.path.insert(0, cand)
        break
try:
    import designbridge as db
except ImportError:
    db = None

# ---------------------------------------------------------------- Core 12
# Locked with the engine on 2026-11-27. Both sides read these numbers, so the
# FormLUT and this compiler cannot drift. Face bits: 0=+X 1=-X 2=+Y 3=-Y 4=+Z 5=-Z.
PX, NX, PY, NY, PZ, NZ = 1, 2, 4, 8, 16, 32
ALL_FACES = PX | NX | PY | NY | PZ | NZ

FACE_NAMES = {"+x": PX, "-x": NX, "+y": PY, "-y": NY, "+z": PZ, "-z": NZ}
FACE_NORMAL = {PX: (1, 0, 0), NX: (-1, 0, 0), PY: (0, 1, 0),
               NY: (0, -1, 0), PZ: (0, 0, 1), NZ: (0, 0, -1)}


def _box(x0, y0, z0, x1, y1, z1):
    """A convex piece as half-spaces: (nx,ny,nz,d) meaning n.p <= d."""
    return [(-1, 0, 0, -x0), (1, 0, 0, x1),
            (0, -1, 0, -y0), (0, 1, 0, y1),
            (0, 0, -1, -z0), (0, 0, 1, z1)]


def _clip(piece, plane):
    return piece + [plane]


FORMS = [
    # name,            ori, pieces,                                            support,    fluid,    seal,              claddable,        mass
    ("cube",             1, [_box(0, 0, 0, 1, 1, 1)],                          "STD",      "SOLID",  ALL_FACES,         ALL_FACES,        1.00),
    ("slab_half",        6, [_box(0, 0, 0, 1, .5, 1)],                         "STD",      "SOLID",  NY,                NY,               0.50),
    ("plate_quarter",    6, [_box(0, 0, 0, 1, .25, 1)],                        "NON_LOAD", "SOLID",  NY,                NY,               0.25),
    ("stair",           12, [_box(0, 0, 0, 1, .5, 1), _box(0, .5, .5, 1, 1, 1)], "STD",    "SOLID",  NY | PZ,           NY | PZ,          0.75),
    ("slope_45",        12, [_clip(_box(0, 0, 0, 1, 1, 1), (0, 1, -1, 0))],     "STD",     "SOLID",  NY | PZ,           NY | PZ,          0.50),
    ("slope_corner",    24, [_clip(_clip(_box(0, 0, 0, 1, 1, 1), (-1, 1, 0, 0)), (0, 1, -1, 0))],
                                                                                "STD",     "SOLID",  NY,                NY,               0.33),
    ("chamfer_edge",    12, [_clip(_box(0, 0, 0, 1, 1, 1), (0, 1, -1, .75))],   "STD",     "SOLID",  NY | PZ,           NY | PZ,          0.97),
    ("column",           3, [_box(.125, 0, .125, .875, 1, .875)],              "COLUMN",   "SOLID",  0,                 0,                0.56),
    ("girder",           3, [_box(.3125, 0, .3125, .6875, 1, .6875)],          "SPAN",     "PASS",   0,                 0,                0.14),
    ("corbel_arch",     12, [_box(0, .75, 0, 1, 1, 1), _box(0, 0, .75, 1, .75, 1),
                             _box(0, .5, .5, 1, .75, .75)],                     "BRACKET", "PASS",   PY | PZ,           PY | PZ,          0.50),
    ("channel_trough",  12, [_box(0, 0, 0, 1, .25, 1), _box(0, .25, 0, .25, 1, 1),
                             _box(.75, .25, 0, 1, 1, 1)],                       "STD",     "FLOW_Z", NY | PX | NX,      NY | PX | NX,     0.63),
    ("trim_edge",       12, [_box(0, 0, .75, 1, .25, 1)],                      "NON_LOAD", "PASS",   0,                 0,                0.06),
]
FORM_ID = {f[0]: i for i, f in enumerate(FORMS)}


# ---------------------------------------------------------------- rotations
def _rotations():
    """The 24 proper rotations of a cube, as integer 3x3 matrices.

    Index 0 is the identity, which is why a plain cube has one valid
    orientation and everything else is a permutation of the axes.
    """
    def mul(a, b):
        return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
                     for i in range(3))
    I = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    rx = ((1, 0, 0), (0, 0, -1), (0, 1, 0))
    ry = ((0, 0, 1), (0, 1, 0), (-1, 0, 0))
    seen, out = set(), []
    stack = [I]
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
        stack.append(mul(rx, m))
        stack.append(mul(ry, m))
    # A stable order so an orientation index means the same thing every run.
    out.sort()
    out.remove(I)
    return [I] + out


ROT = _rotations()


def rot_point(m, p):
    """Rotate a point of the unit cell about its centre."""
    c = [p[i] - 0.5 for i in range(3)]
    r = [sum(m[i][j] * c[j] for j in range(3)) for i in range(3)]
    return tuple(r[i] + 0.5 for i in range(3))


def rot_faces(m, mask):
    """Carry a face mask through a rotation."""
    out = 0
    for bit, n in FACE_NORMAL.items():
        if not (mask & bit):
            continue
        r = tuple(sum(m[i][j] * n[j] for j in range(3)) for i in range(3))
        for b2, n2 in FACE_NORMAL.items():
            if all(abs(r[k] - n2[k]) < 1e-6 for k in range(3)):
                out |= b2
                break
    return out


# ---------------------------------------------------------------- geometry
def piece_tris(planes):
    """Triangles of a convex piece given as half-spaces n.p <= d.

    General enough to cover every Core 12 form from its own definition rather
    than hand-written meshes, which is what keeps the renderer honest about
    slopes and chamfers instead of drawing them as cubes.
    """
    pts = []
    n = len(planes)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                a, b, c = planes[i], planes[j], planes[k]
                m = [list(a[:3]), list(b[:3]), list(c[:3])]
                rhs = [a[3], b[3], c[3]]
                det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                       - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                       + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
                if abs(det) < 1e-9:
                    continue
                inv = []
                for r in range(3):
                    row = []
                    for cc in range(3):
                        sub = [[m[rr][ccc] for ccc in range(3) if ccc != r]
                               for rr in range(3) if rr != cc]
                        minor = sub[0][0] * sub[1][1] - sub[0][1] * sub[1][0]
                        row.append(((-1) ** (r + cc)) * minor / det)
                    inv.append(row)
                p = tuple(sum(inv[r][cc] * rhs[cc] for cc in range(3)) for r in range(3))
                if all(sum(pl[t] * p[t] for t in range(3)) <= pl[3] + 1e-6 for pl in planes):
                    if not any(all(abs(p[t] - q[t]) < 1e-6 for t in range(3)) for q in pts):
                        pts.append(p)
    tris = []
    for pl in planes:
        nvec = pl[:3]
        on = [p for p in pts if abs(sum(nvec[t] * p[t] for t in range(3)) - pl[3]) < 1e-6]
        if len(on) < 3:
            continue
        cx = tuple(sum(p[t] for p in on) / len(on) for t in range(3))
        # Build a 2D basis in the face plane so the ring can be ordered.
        ref = on[0]
        u = tuple(ref[t] - cx[t] for t in range(3))
        ul = math.sqrt(sum(c * c for c in u)) or 1.0
        u = tuple(c / ul for c in u)
        v = (nvec[1] * u[2] - nvec[2] * u[1],
             nvec[2] * u[0] - nvec[0] * u[2],
             nvec[0] * u[1] - nvec[1] * u[0])
        vl = math.sqrt(sum(c * c for c in v)) or 1.0
        v = tuple(c / vl for c in v)
        def ang(p):
            d = tuple(p[t] - cx[t] for t in range(3))
            return math.atan2(sum(d[t] * v[t] for t in range(3)),
                              sum(d[t] * u[t] for t in range(3)))
        ring = sorted(on, key=ang)
        for i in range(1, len(ring) - 1):
            tris.append((ring[0], ring[i], ring[i + 1]))
    return tris


_TRI_CACHE = {}


def form_tris(form_id, ori):
    key = (form_id, ori)
    if key in _TRI_CACHE:
        return _TRI_CACHE[key]
    m = ROT[ori % len(ROT)]
    out = []
    for piece in FORMS[form_id][2]:
        for t in piece_tris(piece):
            out.append(tuple(rot_point(m, p) for p in t))
    _TRI_CACHE[key] = out
    return out


# ---------------------------------------------------------------- document
class Cell:
    __slots__ = ("mat", "form", "ori", "clad", "cladmask", "crack", "light")

    def __init__(self, mat, form, ori=0, clad=0, cladmask=0, crack=0, light=0):
        self.mat, self.form, self.ori = mat, form, ori
        self.clad, self.cladmask, self.crack = clad, cladmask, crack
        self.light = light          # 0-15, emitted by this cell's material

    def packed(self):
        """The engine's 64-bit sparse cell record.

        Bits 0-50 are the locked Core 12 layout and are not touched. Light
        goes in 51-54, which were free: 4 bits for the same 0-15 scale the
        propagation uses, so the engine can read a cell's own emission
        without a side table.
        """
        return ((self.mat & 0xFFFF)
                | ((self.form & 0x3F) << 16)
                | ((self.ori & 0x1F) << 22)
                | ((self.clad & 0xFFFF) << 27)
                | ((self.cladmask & 0x3F) << 43)
                | ((self.crack & 0x3) << 49)
                | ((self.light & 0xF) << 51))


class Doc:
    def __init__(self):
        self.name = "structure"
        self.size = (16, 8, 16)
        self.seed = 1
        self.materials = {}          # alias -> {"id", "texture", "color"}
        self.cells = {}              # (x,y,z) -> Cell
        self.diags = []
        self.last_room = None

    def err(self, line, msg, fix=""):
        self.diags.append(("error", line, msg, fix))

    def warn(self, line, msg, fix=""):
        self.diags.append(("warn", line, msg, fix))

    @property
    def errors(self):
        return [d for d in self.diags if d[0] == "error"]


def _rand(seed, *xs):
    h = seed * 374761393
    for x in xs:
        h = (h ^ int(x) * 668265263) * 1274126177 & 0xFFFFFFFF
        h ^= h >> 15
    return (h & 0xFFFFFF) / 0xFFFFFF


def _strip_comment(raw: str) -> str:
    """Remove a trailing comment without eating a hex colour.

    `#` is both the comment character and the hex prefix, so `color=#6E7378`
    was being read as `color=` and every material came out colourless. A `#`
    counts as a comment only at the start of a line or after whitespace;
    attached to a value, it is part of the value.
    """
    out = []
    for i, ch in enumerate(raw):
        if ch == "#" and (i == 0 or raw[i - 1].isspace()):
            break
        out.append(ch)
    return "".join(out).strip()


def _tup(s):
    s = s.strip().strip("()[]")
    try:
        return tuple(int(round(float(p))) for p in s.split(",") if p.strip() != "")
    except ValueError:
        return None


# ---------------------------------------------------------------- parser
def parse(text):
    doc = Doc()
    next_mat = 1
    for ln, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if not line:
            continue
        toks, kw, tups = [], {}, []
        buf, depth = "", 0
        for ch in line:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == " " and depth == 0:
                if buf:
                    toks.append(buf)
                buf = ""
            else:
                buf += ch
        if buf:
            toks.append(buf)
        head = toks[0].lower()
        for t in toks[1:]:
            if t.startswith("("):
                tups.append(t)
            elif "=" in t:
                k, v = t.split("=", 1)
                kw[k.strip().lower()] = v.strip()
            else:
                tups.append(t) if t.startswith("(") else toks.append(t)
        words = [t for t in toks[1:] if "=" not in t and not t.startswith("(")]

        def mat_id(alias, required=True):
            nonlocal doc
            a = (alias or "").strip()
            if a in doc.materials:
                return doc.materials[a]["id"]
            if required and a:
                doc.err(ln, f"unknown material '{a}'",
                        "declare it first:  material " + a + " <texture_name> color=#3A3F45")
            return 0

        def form_id(name, default="cube"):
            f = (name or default).strip().lower()
            if f not in FORM_ID:
                doc.err(ln, f"unknown form '{f}'",
                        "one of: " + ", ".join(FORM_ID))
                return 0
            return FORM_ID[f]

        def put(x, y, z, mat, form, ori=0, clad=0, cladmask=0, crack=0):
            sx, sy, sz = doc.size
            if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
                return False
            doc.cells[(x, y, z)] = Cell(mat, form, ori, clad, cladmask, crack)
            return True

        if head == "structure":
            doc.name = (words[0].strip("\"'") if words else "structure")
            if "size" in kw:
                t = _tup(kw["size"])
                if t and len(t) == 3:
                    doc.size = t
                else:
                    doc.err(ln, "size needs three numbers", "size=(24,12,24)")
            doc.seed = int(kw.get("seed", 1))
            continue

        if head == "material":
            if len(words) < 1:
                doc.err(ln, "material needs an alias",
                        "material wall soot_iron_plate color=#3A3F45")
                continue
            alias = words[0]
            texture = words[1] if len(words) > 1 else ""
            try:
                lvl = int(float(kw.get("light", 0)))
            except ValueError:
                doc.err(ln, "light must be a whole number 0-15",
                        "material lamp glowstone color=#FFD9A0 light=14")
                lvl = 0
            if lvl < 0 or lvl > 15:
                doc.err(ln, f"light={lvl} is outside 0-15",
                        "0 emits nothing, 15 is the brightest block in the game")
                lvl = max(0, min(15, lvl))
            doc.materials[alias] = {
                "id": next_mat, "texture": texture,
                "color": kw.get("color", "#9AA0A8"),
                "light": lvl,
            }
            next_mat += 1
            continue

        if head == "place":
            t = _tup(tups[0]) if tups else None
            if not t or len(t) != 3:
                doc.err(ln, "place needs a cell", "place (4,1,4) form=column material=pillar")
                continue
            mask = 0
            for f in (kw.get("cladmask", "") or "").split(","):
                mask |= FACE_NAMES.get(f.strip().lower(), 0)
            put(t[0], t[1], t[2], mat_id(kw.get("material")),
                form_id(kw.get("form")), int(kw.get("ori", 0)),
                mat_id(kw.get("clad"), False) if kw.get("clad") else 0, mask,
                int(kw.get("damage", 0)))
            continue

        if head == "fill":
            if len(tups) < 2:
                doc.err(ln, "fill needs two corners", "fill (0,0,0) (24,1,24) material=floor")
                continue
            a, b = _tup(tups[0]), _tup(tups[1])
            if not a or not b or len(a) != 3 or len(b) != 3:
                doc.err(ln, "fill corners need three numbers each", "fill (0,0,0) (24,1,24)")
                continue
            m, f, o = mat_id(kw.get("material")), form_id(kw.get("form")), int(kw.get("ori", 0))
            hollow = kw.get("hollow", "").lower() in ("1", "true", "yes")
            lo = [min(a[i], b[i]) for i in range(3)]
            hi = [max(a[i], b[i]) for i in range(3)]
            for x in range(lo[0], hi[0] + 1):
                for y in range(lo[1], hi[1] + 1):
                    for z in range(lo[2], hi[2] + 1):
                        if hollow and not (x in (lo[0], hi[0]) or y in (lo[1], hi[1])
                                           or z in (lo[2], hi[2])):
                            continue
                        put(x, y, z, m, f, o)
            continue

        if head == "room":
            if len(tups) < 2:
                doc.err(ln, "room needs two corners",
                        "room (0,0,0) (12,6,10) walls=wall floor=floor")
                continue
            a, b = _tup(tups[0]), _tup(tups[1])
            if not a or not b:
                doc.err(ln, "room corners need three numbers each", "room (0,0,0) (12,6,10)")
                continue
            lo = [min(a[i], b[i]) for i in range(3)]
            hi = [max(a[i], b[i]) for i in range(3)]
            wall = mat_id(kw.get("walls")) if kw.get("walls") else 0
            flr = mat_id(kw.get("floor")) if kw.get("floor") else 0
            ceil = mat_id(kw.get("ceiling")) if kw.get("ceiling") not in (None, "none") else 0
            wf = form_id(kw.get("wallform", "cube"))
            ff = form_id(kw.get("floorform", "cube"))
            for x in range(lo[0], hi[0] + 1):
                for y in range(lo[1], hi[1] + 1):
                    for z in range(lo[2], hi[2] + 1):
                        onx = x in (lo[0], hi[0])
                        onz = z in (lo[2], hi[2])
                        if y == lo[1] and flr:
                            put(x, y, z, flr, ff)
                        elif y == hi[1] and ceil:
                            put(x, y, z, ceil, ff)
                        elif (onx or onz) and wall:
                            put(x, y, z, wall, wf)
            doc.last_room = (tuple(lo), tuple(hi))
            continue

        if head in ("door", "window"):
            if not doc.last_room:
                doc.err(ln, f"{head} must follow a room", "put a room line above it")
                continue
            lo, hi = doc.last_room
            side = (words[0].lower() if words else "south")
            at = _tup(kw.get("at", "(0,0)")) or (0, 0)
            w = int(kw.get("width", 2))
            h = int(kw.get("height", 3 if head == "door" else 2))
            base = lo[1] + (0 if head == "door" else 1 + at[1])
            every = int(kw.get("every", 0))
            spans = []
            if side in ("north", "south"):
                z = lo[2] if side == "north" else hi[2]
                xs = range(lo[0] + 1, hi[0], max(1, every)) if every else [lo[0] + at[0]]
                for x0 in xs:
                    spans.append([(x0 + i, z) for i in range(w)])
            else:
                x = hi[0] if side == "east" else lo[0]
                zs = range(lo[2] + 1, hi[2], max(1, every)) if every else [lo[2] + at[0]]
                for z0 in zs:
                    spans.append([(x, z0 + i) for i in range(w)])
            for span in spans:
                for (cx, cz) in span:
                    for y in range(base, base + h):
                        doc.cells.pop((cx, y, cz), None)
            continue

        if head == "colonnade":
            a = _tup(tups[0]) if tups else None
            b = _tup(kw.get("to", "")) if kw.get("to") else None
            if not a or not b:
                doc.err(ln, "colonnade needs a start and to=",
                        "colonnade (4,1,4) to=(20,1,4) material=pillar every=4")
                continue
            m, f = mat_id(kw.get("material")), form_id(kw.get("form", "column"))
            every = max(1, int(kw.get("every", 4)))
            height = int(kw.get("height", 1))
            steps = max(abs(b[0] - a[0]), abs(b[2] - a[2]))
            n = steps // every
            for i in range(n + 1):
                t = (i * every) / steps if steps else 0
                x = int(round(a[0] + (b[0] - a[0]) * t))
                z = int(round(a[2] + (b[2] - a[2]) * t))
                for dy in range(height):
                    put(x, a[1] + dy, z, m, f, int(kw.get("ori", 0)))
            continue

        if head == "stairs":
            a = _tup(tups[0]) if tups else None
            b = _tup(kw.get("to", "")) if kw.get("to") else None
            if not a or not b:
                doc.err(ln, "stairs needs a start and to=",
                        "stairs (4,1,20) to=(10,7,20) material=wall facing=east")
                continue
            m = mat_id(kw.get("material"))
            f = form_id(kw.get("form", "stair"))
            facing = kw.get("facing", "east").lower()
            ori = {"north": 0, "east": 1, "south": 2, "west": 3}.get(facing, 0)
            rise = abs(b[1] - a[1]) or 1
            run = max(abs(b[0] - a[0]), abs(b[2] - a[2])) or 1
            steps = max(rise, run)
            for i in range(steps + 1):
                t = i / steps
                x = int(round(a[0] + (b[0] - a[0]) * t))
                y = int(round(a[1] + (b[1] - a[1]) * t))
                z = int(round(a[2] + (b[2] - a[2]) * t))
                put(x, y, z, m, f, ori)
            continue

        if head == "collapse":
            what = (words[0].lower() if words else "all")
            amount = float(kw.get("amount", 0.3))
            seed = int(kw.get("seed", doc.seed))
            sx, sy, sz = doc.size
            doomed = []
            for (x, y, z), c in doc.cells.items():
                if what == "roof" and y < sy - max(1, sy // 3):
                    continue
                if what == "walls" and y >= sy - max(1, sy // 3):
                    continue
                # Higher cells go first, which is what actually happens.
                bias = 0.5 + 0.5 * (y / max(1, sy - 1))
                if _rand(seed, x, y, z) < amount * bias:
                    doomed.append((x, y, z))
            for k in doomed:
                doc.cells.pop(k, None)
            continue

        if head == "rubble":
            amount = float(kw.get("amount", 0.3))
            seed = int(kw.get("seed", doc.seed + 1))
            m = mat_id(kw.get("material")) if kw.get("material") else 0
            floor_y = int(kw.get("y", 0))
            sx, sy, sz = doc.size
            for x in range(sx):
                for z in range(sz):
                    if (x, floor_y, z) in doc.cells:
                        continue
                    r = _rand(seed, x, z)
                    if r < amount:
                        form = FORM_ID["plate_quarter"] if r < amount * 0.6 else FORM_ID["slab_half"]
                        put(x, floor_y, z, m or 1, form, 0, crack=3)
            continue

        if head == "damage":
            amount = float(kw.get("amount", 0.5))
            seed = int(kw.get("seed", doc.seed + 2))
            for (x, y, z), c in doc.cells.items():
                if _rand(seed, x, y, z) < amount:
                    c.crack = min(3, c.crack + 1 + int(_rand(seed + 7, x, y, z) * 3))
            continue

        doc.err(ln, f"unknown statement '{head}'",
                "one of: structure, material, place, fill, room, door, window, "
                "colonnade, stairs, collapse, rubble, damage")
    return doc


# ---------------------------------------------------------------- validation
# ---------------------------------------------------------------------------
# LIGHT
#
# One cell is one STRATA block is one metre, and light runs on the voxel
# scale the game already uses: 0-15, losing one level per block travelled.
# So a level 14 lamp reaches 14 blocks into open air, and "is there enough
# light here" is a question with a numeric answer rather than a look.
#
# Occlusion is not a new table: a form already declares its fluid mode and
# its mass in the Core 12 contract, and those say how much of a cell is
# actually filled. A column or a thin plate lets light past; a cube does not.
# ---------------------------------------------------------------------------
LIGHT_MAX = 15
DARK_THRESHOLD = 7          # at or below this, a standable cell reads as dark


def _blocks_light(cell):
    """True when a filled cell stops light rather than letting it through."""
    if cell is None:
        return False
    spec = FORMS[cell.form] if 0 <= cell.form < len(FORMS) else None
    if spec is None:
        return True
    if spec[4] == "PASS":           # grates, bars, anything declared open
        return False
    return spec[7] >= 0.5           # mass: a half-filled cell or more is opaque


def propagate_light(doc):
    """Flood light out from every emitting cell.

    Returns {(x, y, z): level} covering emitters and the air around them.
    Air cells are not in doc.cells - the grid is sparse - so the result is
    the only place an empty cell's light level exists.
    """
    mat_light = {m["id"]: int(m.get("light", 0) or 0) for m in doc.materials.values()}
    sx, sy, sz = doc.size
    grid, queue = {}, []

    for (x, y, z), cell in doc.cells.items():
        lvl = mat_light.get(cell.mat, 0)
        cell.light = lvl                       # the cell's own emission
        if lvl > 0:
            grid[(x, y, z)] = lvl
            queue.append((x, y, z, lvl))

    # Breadth-first, brightest first, so a cell is never revisited downward.
    queue.sort(key=lambda q: -q[3])
    head = 0
    while head < len(queue):
        x, y, z, lvl = queue[head]
        head += 1
        if lvl <= 1 or grid.get((x, y, z), 0) > lvl:
            continue
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                           (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            nx, ny, nz = x + dx, y + dy, z + dz
            if not (0 <= nx < sx and 0 <= ny < sy and 0 <= nz < sz):
                continue
            nxt = lvl - 1
            if nxt <= grid.get((nx, ny, nz), 0):
                continue
            if _blocks_light(doc.cells.get((nx, ny, nz))):
                continue               # lights the face, not the far side
            grid[(nx, ny, nz)] = nxt
            queue.append((nx, ny, nz, nxt))
    return grid


def light_survey(doc, grid=None):
    """Where a player could stand, and whether they could see.

    A standable cell is empty, has something solid under it and headroom
    above - the places that actually matter for whether a build is lit.
    """
    if grid is None:
        grid = propagate_light(doc)
    sx, sy, sz = doc.size
    emitters = [(p, c.light) for p, c in doc.cells.items() if c.light > 0]
    standable, dark, darkest = 0, 0, LIGHT_MAX
    for (x, y, z), cell in list(doc.cells.items()):
        above, head = (x, y + 1, z), (x, y + 2, z)
        if y + 1 >= sy or above in doc.cells or not _blocks_light(cell):
            continue
        if head in doc.cells and _blocks_light(doc.cells.get(head)):
            continue
        standable += 1
        lvl = grid.get(above, 0)
        if lvl <= DARK_THRESHOLD:
            dark += 1
            darkest = min(darkest, lvl)
    return {
        "emitters": len(emitters),
        "brightest": max([l for _, l in emitters], default=0),
        "lit_cells": len(grid),
        "standable": standable,
        "dark_standable": dark,
        "darkest": 0 if dark == 0 else darkest,
        "threshold": DARK_THRESHOLD,
    }


def validate(doc, allowed_forms=None):
    """Everything that can be checked without running the game."""
    out = []
    # A project may restrict itself to a subset of the twelve. Enforced here
    # rather than only asked for in the prompt, because a rule that is only
    # requested is a rule that is sometimes ignored.
    allowed = [f for f in (allowed_forms or []) if f in FORM_ID]
    if allowed and len(allowed) < len(FORMS):
        ok = {FORM_ID[f] for f in allowed}
        stray = {}
        for (x, y, z), c in doc.cells.items():
            if c.form not in ok:
                stray.setdefault(FORMS[c.form][0], []).append((x, y, z))
        if stray:
            for nm, where in sorted(stray.items(), key=lambda kv: -len(kv[1])):
                out.append(("error",
                            f"{len(where)} cell(s) use '{nm}', which this project does not allow "
                            f"(first at {where[0]})",
                            "allowed forms: " + ", ".join(allowed)))
    sx, sy, sz = doc.size
    if not doc.cells:
        out.append(("error", "the structure is empty - nothing was placed", ""))
        return out

    bad_ori = []
    bad_clad = []
    for (x, y, z), c in doc.cells.items():
        name, nori, _p, _s, _f, _seal, claddable, _m = (
            FORMS[c.form][0], FORMS[c.form][1], FORMS[c.form][2], FORMS[c.form][3],
            FORMS[c.form][4], FORMS[c.form][5], FORMS[c.form][6], FORMS[c.form][7])
        if c.ori >= nori:
            bad_ori.append((x, y, z, name, c.ori, nori))
        if c.cladmask:
            allowed = rot_faces(ROT[c.ori % len(ROT)], claddable)
            if c.cladmask & ~allowed:
                bad_clad.append((x, y, z, name))
    if bad_ori:
        x, y, z, nm, o, n = bad_ori[0]
        out.append(("error",
                    f"{len(bad_ori)} cell(s) use an orientation the form does not have, "
                    f"e.g. ({x},{y},{z}) {nm} ori={o} but {nm} has {n}",
                    f"use ori=0..{n - 1} on a {nm}"))
    if bad_clad:
        x, y, z, nm = bad_clad[0]
        out.append(("error",
                    f"{len(bad_clad)} cell(s) clad a face the form cannot clad, "
                    f"e.g. ({x},{y},{z}) on a {nm}",
                    "cladding is only legal on faces that are full squares on that form"))

    # Standing-up check. Provisional: plain connectivity to the ground, not the
    # weighted BFS the engine will run. It still catches the thing that matters,
    # which is a chunk of building hanging in the air after a collapse.
    ground = {k for k in doc.cells if k[1] == 0}
    seen = set(ground)
    stack = list(ground)
    while stack:
        x, y, z = stack.pop()
        for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            n = (x + d[0], y + d[1], z + d[2])
            if n in doc.cells and n not in seen:
                seen.add(n)
                stack.append(n)
    floating = set(doc.cells) - seen
    if floating:
        ex = sorted(floating)[0]
        out.append(("warn",
                    f"{len(floating)} cell(s) are not connected to the ground, "
                    f"e.g. {ex}",
                    "they would fall on spawn - add support, or lower the collapse amount"))
    else:
        out.append(("ok", f"every one of the {len(doc.cells)} cells reaches the ground", ""))

    used = {c.mat for c in doc.cells.values()}
    declared = {m["id"] for m in doc.materials.values()}
    unused = [a for a, m in doc.materials.items() if m["id"] not in used]
    if unused:
        out.append(("warn", "declared but never used: " + ", ".join(unused), ""))
    if 0 in used:
        out.append(("error", "some cells have no material",
                    "give every place/fill/room a material="))
    return out


# ---------------------------------------------------------------- output
def colored_tris(doc):
    """World triangles with a colour per material, for the preview render."""
    bycol = {m["id"]: m.get("color", "#9AA0A8") for m in doc.materials.values()}
    out = []
    for (x, y, z), c in doc.cells.items():
        rgba = db.parse_color(bycol.get(c.mat, "#9AA0A8")) if db else (154, 160, 168, 255)
        if c.crack:
            # Damage reads as a slight darkening, not a blackout: STRATA's
            # palette is already dark, and three stages at 18% each turned the
            # whole ruin into a silhouette.
            k = 1.0 - 0.06 * c.crack
            rgba = (int(rgba[0] * k), int(rgba[1] * k), int(rgba[2] * k), rgba[3])
        # Preview-only exposure lift. The real palette is soot and iron, which
        # is correct in the game and unreadable in a 250px thumbnail, so the
        # preview is brightened toward mid-grey. Materials stay distinguishable
        # from each other, which is what the picture is for.
        rgba = (min(255, int(rgba[0] * 0.72) + 68),
                min(255, int(rgba[1] * 0.72) + 70),
                min(255, int(rgba[2] * 0.72) + 74), rgba[3])
        for t in form_tris(c.form, c.ori):
            out.append((tuple((p[0] + x, p[1] + y, p[2] + z) for p in t), rgba))
    return out


FORM_GLYPH = {0: "#", 1: "_", 2: ".", 3: "S", 4: "/", 5: "v", 6: "c",
              7: "I", 8: "H", 9: "A", 10: "U", 11: "-"}


def ascii_plan(doc, y):
    """One floor, read from above. Buildings survive this projection in a way
    creatures do not - a plan is how architecture has always been drawn."""
    sx, sy, sz = doc.size
    rows = []
    for z in range(sz):
        row = ""
        for x in range(sx):
            c = doc.cells.get((x, y, z))
            row += FORM_GLYPH.get(c.form, "?") if c else " "
        rows.append(f"  |{row}|")
    return "\n".join(rows)


def report(doc, allowed_forms=None):
    sx, sy, sz = doc.size
    issues = validate(doc, allowed_forms)
    errs = [i for i in issues if i[0] == "error"] + [d for d in doc.diags if d[0] == "error"]
    L = ["=" * 62,
         f"STRUCTURE '{doc.name}'   {len(doc.cells)} cells in {sx}x{sy}x{sz}",
         f"STATUS: {'FAIL' if errs else 'PASS'}   {len(errs)} error(s)",
         "=" * 62, ""]
    if doc.diags:
        L.append("PARSE")
        for sev, ln, msg, fix in doc.diags:
            L.append(f"  [{sev.upper()}] line {ln}: {msg}")
            if fix:
                L.append(f"          FIX: {fix}")
        L.append("")
    # Light, in the report, because "is this lit" is a question with a number
    # for an answer and the AI cannot see the render.
    sur = light_survey(doc)
    L.append("LIGHT")
    if not sur["emitters"]:
        L.append("  no emitting material - every standable cell is at level 0")
        L.append(f"  add one, e.g.  material lamp glowstone color=#FFD9A0 light=14")
    else:
        L.append(f"  {sur['emitters']} emitter(s), brightest level {sur['brightest']}")
        L.append(f"  {sur['lit_cells']} cell(s) receive light")
        if sur["standable"]:
            pct = 100.0 * sur["dark_standable"] / sur["standable"]
            L.append(f"  {sur['dark_standable']} of {sur['standable']} standable cells "
                     f"at or below level {sur['threshold']} ({pct:.0f}% dark)")
            if sur["dark_standable"]:
                L.append(f"  darkest standable cell is level {sur['darkest']}")
    L.append("")
    L.append("CHECKS")
    for sev, msg, fix in issues:
        L.append(f"  [{sev.upper():5s}] {msg}")
        if fix:
            L.append(f"          -> {fix}")
    L.append("")
    counts = {}
    for c in doc.cells.values():
        counts[FORMS[c.form][0]] = counts.get(FORMS[c.form][0], 0) + 1
    L.append("FORMS USED")
    for nm, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        L.append(f"  {FORM_GLYPH[FORM_ID[nm]]}  {nm:16s} {n}")
    mass = sum(FORMS[c.form][-1] for c in doc.cells.values())
    L.append("")
    L.append(f"MASS  {mass:.1f} cell-equivalents "
             f"({mass / max(1, len(doc.cells)):.2f} average per cell)")
    L.append("")
    L.append("FLOOR PLANS  (top-down, one per level that has anything)")
    for y in range(sy):
        if not any(k[1] == y for k in doc.cells):
            continue
        L.append(f"  y={y}")
        L.append(ascii_plan(doc, y))
    return "\n".join(L)


def export_json(doc, path):
    sx, sy, sz = doc.size
    out = {
        "name": doc.name,
        "format": "strata.structure.v1",
        "size": [sx, sy, sz],
        "seed": doc.seed,
        "forms": [{"id": i, "name": f[0], "orientations": f[1], "support": f[3],
                   "fluid": f[4], "seal": f[5], "claddable": f[6], "mass": f[-1]}
                  for i, f in enumerate(FORMS)],
        "materials": [{"id": m["id"], "alias": a, "texture": m["texture"],
                       "color": m["color"]} for a, m in doc.materials.items()],
        "cells": [{"at": [x, y, z], "packed": c.packed()}
                  for (x, y, z), c in sorted(doc.cells.items())],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    return path


def preview_png(doc, path, w=460, h=380, yaw=35.0, pitch=24.0):
    if db is None:
        return None
    tris = colored_tris(doc)
    if not tris:
        return None
    db.render_mesh_png(tris, w, h, yaw=yaw, pitch=pitch).save_png(path)
    return path


def contact_sheet(doc, path, w=250):
    """Four angles. A building hides its mistakes from a single camera even
    more thoroughly than a creature does."""
    if db is None:
        return None
    tris = colored_tris(doc)
    if not tris:
        return None
    sheet = db.Image(w * 2, w * 2, (18, 19, 23, 255))
    for i, (yaw, pitch) in enumerate([(0, 8), (35, 24), (-75, 18), (20, 72)]):
        img = db.render_mesh_png(tris, w, w, bg=(26, 28, 34, 255), yaw=yaw, pitch=pitch)
        ox, oy = (i % 2) * w, (i // 2) * w
        for y in range(w):
            for x in range(w):
                sheet.put(ox + x, oy + y, img.get(x, y), blend=False)
    sheet.save_png(path)
    return path


def compile_text(text):
    return parse(text)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="structurebridge",
                                 description="Compile a STRATA structure document.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="compile and print the report")
    c.add_argument("file")
    c.add_argument("--sheet", default="", help="also write a four-angle contact sheet")
    b = sub.add_parser("build", help="compile and export")
    b.add_argument("file")
    b.add_argument("--out", default="./out")
    a = ap.parse_args(argv)

    doc = parse(open(a.file, encoding="utf-8").read())
    print(report(doc))
    if a.cmd == "check":
        if getattr(a, "sheet", ""):
            made = contact_sheet(doc, a.sheet)
            print(f"\nContact sheet: {made}" if made else "\n(no contact sheet)")
        return 1 if doc.errors else 0

    os.makedirs(a.out, exist_ok=True)
    slug = doc.name
    made = [export_json(doc, os.path.join(a.out, slug + ".structure.json"))]
    p = preview_png(doc, os.path.join(a.out, slug + "_preview.png"))
    if p:
        made.append(p)
    s = contact_sheet(doc, os.path.join(a.out, slug + "_sheet.png"))
    if s:
        made.append(s)
    print("\nEXPORTED")
    for m in made:
        print(f"  {m}  ({os.path.getsize(m)} bytes)")
    return 1 if doc.errors else 0


if __name__ == "__main__":
    sys.exit(main())
