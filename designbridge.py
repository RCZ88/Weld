#!/usr/bin/env python3
"""
DesignBridge - a text-only CAD bridge for language models.

Single file, stdlib only. Runs in any Python 3.8+ sandbox.

The idea: text models cannot see, and are bad at coordinates and meshes.
So they never emit geometry. They emit a short declarative DSL describing
2D profiles and the operations that sweep them into 3D. This file compiles
that DSL into real watertight triangle meshes, checks them, and reports back
in plain text (errors with fix hints + ASCII silhouettes) so the model can
debug its own design without eyes.

  python3 designbridge.py prompt --brief "a coffee mug"   # prompt for the AI
  python3 designbridge.py check design.db                 # text feedback
  python3 designbridge.py build design.db -o out/         # STL/OBJ/SVG
  python3 designbridge.py guide                           # how-to-think doc
"""
import sys, os, math, json, re, struct, argparse, hashlib, base64, zlib

VERSION = "0.1.0"


# ====================================================================
# IMAGING: PNG writer, colour, software 3D preview render
# ====================================================================
# ---------------------------------------------------------------- color
def parse_color(s, default=(0, 0, 0, 255)):
    """#rgb, #rrggbb, #rrggbbaa, or a small set of names -> (r,g,b,a)."""
    if isinstance(s, (tuple, list)):
        c = list(s) + [255] * (4 - len(s))
        return tuple(int(max(0, min(255, v))) for v in c[:4])
    if not isinstance(s, str):
        return default
    s = s.strip().lower()
    NAMES = {
        "black": "#000000", "white": "#ffffff", "red": "#d03030", "green": "#3a9a3a",
        "blue": "#3060d0", "yellow": "#e0c030", "orange": "#e08020", "purple": "#8040a0",
        "brown": "#6b4a2a", "gray": "#808080", "grey": "#808080", "cyan": "#30b0c0",
        "magenta": "#c040a0", "pink": "#e090b0", "lime": "#80d040", "navy": "#203060",
        "teal": "#208080", "olive": "#808020", "maroon": "#802020", "silver": "#c0c0c0",
        "gold": "#d0a830", "tan": "#c0a070", "none": "#00000000", "transparent": "#00000000",
    }
    s = NAMES.get(s, s)
    if not s.startswith("#"):
        return default
    h = s[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h) + "ff"
    elif len(h) == 4:
        h = "".join(c * 2 for c in h)
    elif len(h) == 6:
        h = h + "ff"
    elif len(h) != 8:
        return default
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4, 6))
    except ValueError:
        return default


def hex_of(c):
    return "#" + "".join(f"{int(max(0,min(255,v))):02x}" for v in c[:3])


def mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def over(fg, bg):
    """Alpha-composite fg over bg, both RGBA 0-255."""
    fa = fg[3] / 255.0
    if fa <= 0: return bg
    if fa >= 1: return fg
    ba = bg[3] / 255.0
    oa = fa + ba * (1 - fa)
    if oa <= 0: return (0, 0, 0, 0)
    out = [int(round((fg[i] * fa + bg[i] * ba * (1 - fa)) / oa)) for i in range(3)]
    return (out[0], out[1], out[2], int(round(oa * 255)))


# ---------------------------------------------------------------- image
class Image:
    """A plain RGBA raster. Pure stdlib, writes a real PNG with zlib."""
    __slots__ = ("w", "h", "px")

    def __init__(self, w, h, bg=(0, 0, 0, 0)):
        self.w, self.h = w, h
        self.px = [list(bg) for _ in range(w * h)]

    def _i(self, x, y):
        return y * self.w + x

    def get(self, x, y):
        return tuple(self.px[self._i(x, y)])

    def put(self, x, y, c, blend=True):
        if 0 <= x < self.w and 0 <= y < self.h:
            if blend and len(c) == 4 and c[3] < 255:
                self.px[self._i(x, y)] = list(over(c, tuple(self.px[self._i(x, y)])))
            else:
                self.px[self._i(x, y)] = list(c if len(c) == 4 else tuple(c) + (255,))

    def fill(self, c):
        self.px = [list(c) for _ in range(self.w * self.h)]

    def rect(self, x0, y0, x1, y1, c):
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1)):
                self.put(x, y, c)

    def scale_nn(self, factor):
        """Nearest-neighbour upscale, for showing a 16x16 tile big."""
        f = int(factor)
        out = Image(self.w * f, self.h * f)
        for y in range(self.h):
            for x in range(self.w):
                c = self.get(x, y)
                out.rect(x * f, y * f, x * f + f, y * f + f, c)
        return out

    def to_png_bytes(self):
        raw = bytearray()
        for y in range(self.h):
            raw.append(0)                       # filter type 0 for each scanline
            row = self.px[y * self.w:(y + 1) * self.w]
            for p in row:
                raw += bytes((p[0], p[1], p[2], p[3]))
        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 6, 0, 0, 0)   # 8-bit RGBA
        idat = zlib.compress(bytes(raw), 9)
        return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

    def save_png(self, path):
        with open(path, "wb") as f:
            f.write(self.to_png_bytes())


# ---------------------------------------------------------------- 3D preview render
def _normalize(v):
    L = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / L, v[1] / L, v[2] / L)


def render_mesh_png(colored_tris, W=420, H=340, bg=(28, 30, 36, 255),
                    yaw=35.0, pitch=22.0, light=(-0.4, 0.75, 0.5)):
    """Flat/Lambert software render of a list of (tri, rgba) to a real PNG.
    tri is ((x,y,z),(x,y,z),(x,y,z)). A z-buffer handles occlusion; shading is
    N.L plus a little ambient, so a text model comes back as an actual shaded
    picture the user can look at - not ASCII."""
    if not colored_tris:
        return Image(W, H, bg)
    verts = [v for tri, _ in colored_tris for v in tri]
    cx = sum(v[0] for v in verts) / len(verts)
    cy = sum(v[1] for v in verts) / len(verts)
    cz = sum(v[2] for v in verts) / len(verts)
    rad = max(math.sqrt((v[0] - cx) ** 2 + (v[1] - cy) ** 2 + (v[2] - cz) ** 2)
              for v in verts) or 1.0

    ry, rp = math.radians(yaw), math.radians(pitch)
    cyw, syw = math.cos(ry), math.sin(ry)
    cp, sp = math.cos(rp), math.sin(rp)

    def view(p):
        x, y, z = p[0] - cx, p[1] - cy, p[2] - cz
        x, z = x * cyw + z * syw, -x * syw + z * cyw      # yaw about up axis
        y, z = y * cp - z * sp, y * sp + z * cp           # pitch
        return (x, y, z)

    L = _normalize(light)
    margin = 0.86
    scale = min(W, H) / (2.2 * rad) * margin
    ox, oy = W / 2.0, H / 2.0
    img = Image(W, H, bg)
    zbuf = [1e18] * (W * H)

    for tri, col in colored_tris:
        a, b, c = (view(p) for p in tri)
        # face normal in view space; cull back faces (CCW front)
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        n = _normalize((u[1] * w[2] - u[2] * w[1],
                        u[2] * w[0] - u[0] * w[2],
                        u[0] * w[1] - u[1] * w[0]))
        if n[2] <= 0:                                     # facing away
            continue
        lam = max(0.0, n[0] * L[0] + n[1] * L[1] + n[2] * L[2])
        shade = 0.30 + 0.70 * lam                         # ambient + diffuse
        base = col if len(col) == 4 else tuple(col) + (255,)
        if base[3] == 0:
            continue
        rgb = (int(base[0] * shade), int(base[1] * shade), int(base[2] * shade), base[3])
        # project (orthographic; y flipped for screen)
        def scr(p):
            return (ox + p[0] * scale, oy - p[1] * scale, p[2])
        pa, pb, pc = scr(a), scr(b), scr(c)
        minx = max(0, int(min(pa[0], pb[0], pc[0])))
        maxx = min(W - 1, int(max(pa[0], pb[0], pc[0])) + 1)
        miny = max(0, int(min(pa[1], pb[1], pc[1])))
        maxy = min(H - 1, int(max(pa[1], pb[1], pc[1])) + 1)
        det = (pb[1] - pc[1]) * (pa[0] - pc[0]) + (pc[0] - pb[0]) * (pa[1] - pc[1])
        if abs(det) < 1e-9:
            continue
        for y in range(miny, maxy + 1):
            for x in range(minx, maxx + 1):
                w0 = ((pb[1] - pc[1]) * (x - pc[0]) + (pc[0] - pb[0]) * (y - pc[1])) / det
                w1 = ((pc[1] - pa[1]) * (x - pc[0]) + (pa[0] - pc[0]) * (y - pc[1])) / det
                w2 = 1 - w0 - w1
                if w0 < -0.001 or w1 < -0.001 or w2 < -0.001:
                    continue
                z = w0 * pa[2] + w1 * pb[2] + w2 * pc[2]
                idx = y * W + x
                if z < zbuf[idx]:
                    zbuf[idx] = z
                    img.px[idx] = list(rgb)
    return img

# ====================================================================
# TEXTURE TRACK: procedural 2D tiles -> PNG + material + recipe
# ====================================================================
"""Procedural texture track: a declarative DSL compiles to a real albedo PNG,
a material PNG (R,G = tangent normal, B = metallic, A = roughness - the layout
STRATA's Atlas.java uses), and a JSON recipe that re-paints the same tile.

A texture is a stack of layers, painted in order over a base fill. The model
never emits pixels; it names patterns and the compiler paints them. This is the
2D analogue of the 3D approach: the AI reasons about intent, not RGB values."""


def _hash01(*xs):
    h = hashlib.md5((",".join(map(str, xs))).encode()).digest()
    return int.from_bytes(h[:4], "big") / 0xffffffff


def _vnoise(x, y, seed, scale):
    """Smooth value noise in [0,1] at a given feature scale."""
    fx, fy = x / scale, y / scale
    x0, y0 = math.floor(fx), math.floor(fy)
    tx, ty = fx - x0, fy - y0
    sx, sy = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)   # smoothstep
    def g(ix, iy): return _hash01(ix, iy, seed)
    a = g(x0, y0) + sx * (g(x0 + 1, y0) - g(x0, y0))
    b = g(x0, y0 + 1) + sx * (g(x0 + 1, y0 + 1) - g(x0, y0 + 1))
    return a + sy * (b - a)


def _fbm(x, y, seed, scale, octaves=3):
    v, amp, tot, sc = 0.0, 1.0, 0.0, scale
    for o in range(octaves):
        v += amp * _vnoise(x, y, seed + o * 37, sc)
        tot += amp; amp *= 0.5; sc *= 0.5
    return v / tot


TEXTURE_LAYERS = ("fill", "noise", "gradient", "brick", "planks", "checker",
                  "scatter", "veins", "bevel", "outline", "dither", "border", "stripes")


class TexLayer:
    def __init__(self, kind, args, line):
        self.kind, self.args, self.line = kind, args, line


class TexDoc:
    def __init__(self):
        self.name, self.size, self.layers = "texture", 16, []
        self.diags, self.repairs = [], []
        self.metallic, self.roughness = 0.0, 0.8     # defaults for the material map

    def err(self, line, msg, fix="", code=""):
        self.diags.append(("error", line, msg, fix, code))

    def warn(self, line, msg, fix="", code=""):
        self.diags.append(("warn", line, msg, fix, code))

    @property
    def errors(self): return [d for d in self.diags if d[0] == "error"]


def paint(doc):
    """Run the layer stack into an albedo Image."""
    N = doc.size
    img = Image(N, N, (0, 0, 0, 0))
    seedbase = int(_hash01(doc.name) * 1e6)
    palette = set()

    def note_palette(im):
        pass

    for layer in doc.layers:
        k, a = layer.kind, layer.args
        seed = seedbase + int(a.get("seed", 0))
        if k == "fill":
            img.fill(parse_color(a.get("color", "#808080")))
        elif k == "noise":
            lo = parse_color(a.get("dark", a.get("a", "#00000040")))
            hi = parse_color(a.get("light", a.get("b", "#ffffff40")))
            scale = float(a.get("scale", 4)); oct = int(a.get("octaves", 3))
            amt = float(a.get("amount", a.get("contrast", 0.5)))
            for y in range(N):
                for x in range(N):
                    t = _fbm(x, y, seed, max(1.0, scale), oct)
                    t = 0.5 + (t - 0.5) * amt * 2
                    c = mix(lo, hi, max(0.0, min(1.0, t)))
                    img.put(x, y, c)
        elif k == "gradient":
            c0 = parse_color(a.get("from", "#000")); c1 = parse_color(a.get("to", "#fff"))
            vert = str(a.get("dir", "v")).lower().startswith("v")
            for y in range(N):
                for x in range(N):
                    t = (y if vert else x) / (N - 1)
                    img.put(x, y, mix(c0, c1, t))
        elif k in ("brick", "planks"):
            mortar = parse_color(a.get("mortar", a.get("gap", "#3a3a3a")))
            bw = int(a.get("w", 8 if k == "brick" else N))
            bh = int(a.get("h", 4 if k == "brick" else 4))
            gap = int(a.get("line", 1))
            jit = float(a.get("jitter", 0.12))
            face = a.get("color")
            for y in range(N):
                row = y // bh
                off = (bw // 2) * (row % 2) if k == "brick" else 0
                for x in range(N):
                    xx = (x + off) % N
                    on_h = (y % bh) < gap
                    on_v = (xx % bw) < gap
                    if on_h or on_v:
                        img.put(x, y, mortar)
                    elif face:
                        cell = (row, (x + off) // bw)
                        shade = 1.0 + (_hash01(cell[0], cell[1], seed) - 0.5) * jit * 2
                        base = parse_color(face)
                        img.put(x, y, tuple(int(max(0, min(255, base[i] * shade))) for i in range(3)) + (base[3],))
        elif k == "checker":
            c0 = parse_color(a.get("a", "#fff")); c1 = parse_color(a.get("b", "#000"))
            sz = int(a.get("size", 4))
            for y in range(N):
                for x in range(N):
                    img.put(x, y, c0 if ((x // sz) + (y // sz)) % 2 == 0 else c1)
        elif k == "stripes":
            c0 = parse_color(a.get("a", "#fff")); c1 = parse_color(a.get("b", "#000"))
            sz = int(a.get("size", 2)); vert = str(a.get("dir", "v")).lower().startswith("v")
            for y in range(N):
                for x in range(N):
                    img.put(x, y, c0 if ((x if vert else y) // sz) % 2 == 0 else c1)
        elif k == "scatter":
            col = parse_color(a.get("color", "#ffffff"))
            amt = float(a.get("amount", 0.2)); sz = int(a.get("size", 1))
            for y in range(N):
                for x in range(N):
                    if _hash01(x, y, seed) < amt:
                        for dy in range(sz):
                            for dx in range(sz):
                                img.put(x + dx, y + dy, col)
        elif k == "veins":
            col = parse_color(a.get("color", "#d0a030"))
            scale = float(a.get("scale", 5)); thr = float(a.get("amount", 0.12))
            for y in range(N):
                for x in range(N):
                    n = _fbm(x, y, seed, scale, 2)
                    if abs(n - 0.5) < thr:
                        img.put(x, y, col)
        elif k == "dither":
            col = parse_color(a.get("color", "#00000040")); amt = float(a.get("amount", 0.5))
            for y in range(N):
                for x in range(N):
                    if (x + y) % 2 == 0 and _hash01(x, y, seed) < amt:
                        img.put(x, y, col)
        elif k == "bevel":
            lt = parse_color(a.get("light", "#ffffff60")); dk = parse_color(a.get("dark", "#00000060"))
            w = int(a.get("width", 1))
            for i in range(w):
                for x in range(i, N - i):
                    img.put(x, i, lt); img.put(i, x, lt)             # top + left
                    img.put(x, N - 1 - i, dk); img.put(N - 1 - i, x, dk)  # bottom + right
        elif k in ("outline", "border"):
            col = parse_color(a.get("color", "#000")); w = int(a.get("width", 1))
            for i in range(w):
                for x in range(N):
                    img.put(x, i, col); img.put(x, N - 1 - i, col)
                    img.put(i, x, col); img.put(N - 1 - i, x, col)
    return img


def material_map(doc, albedo):
    """Build the RG=normal, B=metallic, A=roughness tile from a height estimate
    (luminance as a cheap heightfield) plus the declared metallic/roughness."""
    N = doc.size
    mat = Image(N, N, (128, 128, 0, 0))
    met = int(max(0, min(255, doc.metallic * 255)))
    rgh = int(max(0, min(255, doc.roughness * 255)))

    def lum(x, y):
        c = albedo.get(x % N, y % N)
        return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255.0
    for y in range(N):
        for x in range(N):
            dx = lum(x + 1, y) - lum(x - 1, y)
            dy = lum(x, y + 1) - lum(x, y - 1)
            nx, ny, nz = -dx, -dy, 1.0
            L = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            r = int((nx / L * 0.5 + 0.5) * 255)
            g = int((ny / L * 0.5 + 0.5) * 255)
            mat.put(x, y, (r, g, met, rgh), blend=False)
    return mat


def palette_of(img):
    counts = {}
    for p in img.px:
        if p[3] == 0: continue
        k = (p[0], p[1], p[2])
        counts[k] = counts.get(k, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def recipe_json(doc):
    return {
        "name": doc.name, "size": doc.size,
        "metallic": doc.metallic, "roughness": doc.roughness,
        "layers": [{"kind": l.kind, **{k: v for k, v in l.args.items()}} for l in doc.layers],
    }


# ---------------------------------------------------------------- texture parse
def _strip_comment(line):
    """Strip a trailing comment, but never the # inside a hex colour. A comment
    hash is one at the start of the line or following whitespace; a hash glued
    to a token (color=#333, (#fff)) is data."""
    s = line
    if s.lstrip().startswith("#") or s.lstrip().startswith("//"):
        return ""
    out = []
    for i, ch in enumerate(s):
        if ch == "#" and i > 0 and s[i - 1] in " \t":
            break
        if ch == "/" and i + 1 < len(s) and s[i + 1] == "/" and (i == 0 or s[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out)


def _split_kv(s):
    out, buf, q = [], "", None
    for ch in s:
        if q:
            buf += ch
            if ch == q: q = None
            continue
        if ch in "\"'":
            q = ch; buf += ch; continue
        if ch.isspace():
            if buf: out.append(buf); buf = ""
            continue
        buf += ch
    if buf: out.append(buf)
    return out


def _val(s):
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _suggest(word, opts):
    def lev(a, b):
        if not a: return len(b)
        if not b: return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            cur = [i + 1]
            for j, cb in enumerate(b):
                cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
            prev = cur
        return prev[-1]
    best = min(opts, key=lambda o: lev(word.lower(), o.lower()))
    return best if lev(word.lower(), best.lower()) <= 2 else None


def parse_texture(text, doc=None):
    doc = doc or TexDoc()
    # strip sentinels / fences, like the 3D parser
    if "<<<DESIGN" in text:
        text = text.split("<<<DESIGN", 1)[1].split(">>>", 1)[0]
        doc.repairs.append("extracted content between <<<DESIGN and >>> sentinels")
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            doc.repairs.append("stripped markdown code fence")
            text = re.sub(r"^[a-zA-Z0-9_+-]*\n", "", parts[1])
    for sm, pl in {"“": '"', "”": '"', "‘": "'", "’": "'"}.items():
        if sm in text:
            text = text.replace(sm, pl); doc.repairs.append("normalized smart quotes")

    for ln, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw).strip()
        if not line: continue
        toks = _split_kv(line)
        head = toks[0].lower()
        words = [t for t in toks[1:] if "=" not in t]
        kw = {}
        for t in toks[1:]:
            if "=" in t:
                k, v = t.split("=", 1); kw[k.strip().lower()] = _val(v)

        if head == "texture":
            if words and words[0][0] in "\"'":
                doc.name = words[0].strip("\"'")
            elif words:
                doc.name = words[0]
            doc.size = int(kw.get("size", 16))
            if doc.size < 1 or doc.size > 256:
                doc.err(ln, f"texture size {doc.size} is out of range", "use 8, 16, 32 or 64", "SIZE")
                doc.size = 16
            if "metallic" in kw: doc.metallic = float(kw["metallic"])
            if "roughness" in kw: doc.roughness = float(kw["roughness"])
            continue
        if head == "material":
            if "metallic" in kw: doc.metallic = float(kw["metallic"])
            if "roughness" in kw: doc.roughness = float(kw["roughness"])
            continue
        if head in TEXTURE_LAYERS:
            doc.layers.append(TexLayer(head, kw, ln))
            continue
        alt = _suggest(head, list(TEXTURE_LAYERS) + ["texture", "material"])
        doc.err(ln, f"unknown texture layer '{head}'",
                f"did you mean '{alt}'?" if alt else
                "layers: " + ", ".join(TEXTURE_LAYERS), "LAYER")

    if not doc.layers and not doc.errors:
        doc.err(0, "texture has no layers - nothing to paint",
                "start with a fill:  fill color=#808080", "EMPTY")
    return doc


# ---------------------------------------------------------------- texture feedback
_RAMP = ".:-=+*oO#@"


def _closest_name(c):
    NAMED = {"black": (0, 0, 0), "white": (255, 255, 255), "gray": (128, 128, 128),
             "red": (200, 50, 50), "green": (60, 150, 60), "blue": (50, 90, 200),
             "yellow": (220, 200, 50), "orange": (220, 130, 40), "brown": (120, 80, 40),
             "tan": (200, 170, 110), "gold": (210, 170, 50), "cyan": (60, 180, 200),
             "purple": (140, 70, 170), "pink": (230, 150, 180), "dark": (50, 50, 55)}
    best, bd = "?", 1e9
    for n, rc in NAMED.items():
        d = sum((c[i] - rc[i]) ** 2 for i in range(3))
        if d < bd: bd, best = d, n
    return best


def texture_report(doc, img=None, grid=True):
    L = []
    bar = "=" * 60
    img = img if img is not None else (paint(doc) if not doc.errors else None)
    ok = not doc.errors
    L.append(bar)
    L.append(f"TEXTURE '{doc.name}'   {doc.size}x{doc.size}px   "
             f"metallic {doc.metallic:.2f}  roughness {doc.roughness:.2f}")
    L.append(f"STATUS: {'PASS' if ok else 'FAIL'}   {len(doc.errors)} error(s)")
    L.append(bar)
    if doc.repairs:
        L.append("")
        L.append("INPUT REPAIRS:")
        for r in dict.fromkeys(doc.repairs): L.append("  - " + r)
    if doc.errors:
        L.append("")
        L.append("ERRORS")
        for s, line, msg, fix, code in doc.diags:
            if s != "error": continue
            L.append(f"  [ERROR] line {line}: {msg}")
            if fix: L.append(f"          FIX: {fix}")
    L.append("")
    L.append(f"LAYERS ({len(doc.layers)}): " + " -> ".join(l.kind for l in doc.layers))
    if img is not None:
        pal = palette_of(img)
        L.append("")
        L.append(f"PALETTE ({len(pal)} colors), most used first:")
        for c, n in pal[:8]:
            pct = 100.0 * n / (doc.size * doc.size)
            L.append(f"  {hex_of(c)}  {_closest_name(c):<7} {pct:4.0f}%")
        if len(pal) > 24:
            L.append(f"  ...and {len(pal) - 24} more - that is a lot of colors for a tile; "
                     "pixel-art reads best with a tight palette (6-16).")
        if grid:
            L.append("")
            L.append("PREVIEW - luminance map (@ = bright, '.' = dark, ' ' = transparent).")
            L.append("Each cell is one pixel; use it to check the pattern, not the colour:")
            step = max(1, doc.size // 32)
            for y in range(0, doc.size, step):
                row = "  |"
                for x in range(0, doc.size, step):
                    c = img.get(x, y)
                    if c[3] == 0:
                        row += " "
                    else:
                        lu = (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255.0
                        row += _RAMP[min(len(_RAMP) - 1, int(lu * (len(_RAMP) - 1) + 0.5))]
                L.append(row + "|")
            L.append("  +" + "-" * (doc.size // step) + "+")
            L.append("  A real colour PNG preview is saved alongside; the user can see it.")
    L.append("")
    L.append(bar)
    if ok:
        L.append("PASS. Check the palette and the pattern preview against your intent, then\n"
                 "this texture is ready to export (albedo PNG + material PNG + recipe JSON).")
    else:
        L.append("Fix the errors and resend the whole texture between <<<DESIGN and >>>.")
    L.append(bar)
    return "\n".join(L)

# ====================================================================
# MODEL TRACK: box models + bones + animation -> GLB + JSON
# ====================================================================
"""Voxel/box-model track: a declarative DSL for blocky entity models with named
bones, pivots and keyframe animation. The AI describes boxes and bones; the
compiler builds geometry and emits:
  - GLB  (binary glTF 2.0) - the universal format: Blender, three.js, Godot,
    Unity, any website. Bones become nodes, animation becomes a glTF animation.
  - a box-model JSON close to STRATA's MobRenderer layout (cubes + pivots).
  - OBJ for a static snapshot.

Coordinates are in 'pixels' like Minecraft models (16 px = 1 block), Y up.
This is the shape text models reason about best, and it covers mobs, bosses,
props and machines."""


class Box:
    def __init__(self, lo, hi, color, line):
        self.lo, self.hi, self.color, self.line = lo, hi, color, line


class Bone:
    def __init__(self, name, pivot, parent, line):
        self.name, self.pivot, self.parent, self.line = name, pivot, parent, line
        self.boxes = []


class Keyframe:
    def __init__(self, t, bone, rot=None, pos=None, scale=None):
        self.t, self.bone, self.rot, self.pos, self.scale = t, bone, rot, pos, scale


class Anim:
    def __init__(self, name, loop, line):
        self.name, self.loop, self.line = name, loop, line
        self.keys = []


class ModelDoc:
    def __init__(self):
        self.name = "model"
        self.bones = {}            # name -> Bone, in declaration order
        self.order = []
        self.anims = {}
        self.diags, self.repairs = [], []

    def err(self, line, msg, fix="", code=""):
        self.diags.append(("error", line, msg, fix, code))

    def warn(self, line, msg, fix="", code=""):
        self.diags.append(("warn", line, msg, fix, code))

    @property
    def errors(self): return [d for d in self.diags if d[0] == "error"]
    @property
    def warnings(self): return [d for d in self.diags if d[0] == "warn"]


# ---------------------------------------------------------------- geometry
# A box's 6 faces as (4 corner offsets, outward normal). CCW seen from outside.
_FACES = [
    ("east",  [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)], (1, 0, 0)),
    ("west",  [(0, 0, 1), (0, 1, 1), (0, 1, 0), (0, 0, 0)], (-1, 0, 0)),
    ("up",    [(0, 1, 1), (1, 1, 1), (1, 1, 0), (0, 1, 0)], (0, 1, 0)),
    ("down",  [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)], (0, -1, 0)),
    ("south", [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], (0, 0, 1)),
    ("north", [(1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 1, 0)], (0, 0, -1)),
]


def box_tris(box):
    """Return list of (v0,v1,v2) world-space triangles for a box."""
    lo, hi = box.lo, box.hi
    def corner(o):
        return (lo[0] + (hi[0] - lo[0]) * o[0],
                lo[1] + (hi[1] - lo[1]) * o[1],
                lo[2] + (hi[2] - lo[2]) * o[2])
    tris = []
    for _, offs, _n in _FACES:
        c = [corner(o) for o in offs]
        tris.append((c[0], c[1], c[2]))
        tris.append((c[0], c[2], c[3]))
    return tris


def colored_tris(doc):
    """All triangles with their part colour, for the software preview render."""
    out = []
    for name in doc.order:
        for box in doc.bones[name].boxes:
            for t in box_tris(box):
                out.append((t, box.color))
    return out


def bbox(doc):
    pts = [c for name in doc.order for box in doc.bones[name].boxes
           for c in (box.lo, box.hi)]
    if not pts:
        return (0, 0, 0), (0, 0, 0)
    return (tuple(min(p[i] for p in pts) for i in range(3)),
            tuple(max(p[i] for p in pts) for i in range(3)))


# ---------------------------------------------------------------- GLB export
def _pack_mesh(doc):
    """Flatten every box into interleaved position+normal+color vertices,
    grouped per bone so glTF nodes can carry the animation."""
    bone_prims = []
    for name in doc.order:
        bone = doc.bones[name]
        pos, nrm, col, idx = [], [], [], []
        n = 0
        for box in bone.boxes:
            lo, hi = box.lo, box.hi
            r, g, b, a = box.color
            def corner(o):
                return (lo[0] + (hi[0] - lo[0]) * o[0],
                        lo[1] + (hi[1] - lo[1]) * o[1],
                        lo[2] + (hi[2] - lo[2]) * o[2])
            for _, offs, nv in _FACES:
                for o in offs:
                    # glTF node will translate by pivot; store relative to pivot
                    c = corner(o)
                    pos.append((c[0] - bone.pivot[0], c[1] - bone.pivot[1], c[2] - bone.pivot[2]))
                    nrm.append(nv)
                    col.append((r / 255.0, g / 255.0, b / 255.0, a / 255.0))
                idx += [n, n + 1, n + 2, n, n + 2, n + 3]
                n += 4
        bone_prims.append((name, bone, pos, nrm, col, idx))
    return bone_prims


def export_glb(doc, path):
    prims = _pack_mesh(doc)
    bin_parts = []
    offset = 0
    accessors, bufferViews = [], []

    def add_view(data, target=None):
        nonlocal offset
        pad = (4 - (len(data) % 4)) % 4
        data = data + b"\x00" * pad
        bufferViews.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data) - pad})
        if target: bufferViews[-1]["target"] = target
        bin_parts.append(data)
        vid = len(bufferViews) - 1
        offset += len(data)
        return vid

    nodes, meshes = [], []
    # root bones become nodes; each with a mesh if it has boxes
    name_to_node = {}
    for i, (name, bone, pos, nrm, col, idx) in enumerate(prims):
        node = {"name": name, "translation": list(map(float, bone.pivot))}
        if pos:
            # position accessor
            pdata = b"".join(struct.pack("<3f", *p) for p in pos)
            pv = add_view(pdata, 34962)
            mn = [min(p[k] for p in pos) for k in range(3)]
            mx = [max(p[k] for p in pos) for k in range(3)]
            pa = len(accessors)
            accessors.append({"bufferView": pv, "componentType": 5126, "count": len(pos),
                              "type": "VEC3", "min": mn, "max": mx})
            ndata = b"".join(struct.pack("<3f", *v) for v in nrm)
            nv = add_view(ndata, 34962)
            na = len(accessors)
            accessors.append({"bufferView": nv, "componentType": 5126, "count": len(nrm), "type": "VEC3"})
            cdata = b"".join(struct.pack("<4f", *c) for c in col)
            cv = add_view(cdata, 34962)
            ca = len(accessors)
            accessors.append({"bufferView": cv, "componentType": 5126, "count": len(col), "type": "VEC4"})
            idata = b"".join(struct.pack("<I", v) for v in idx)
            iv = add_view(idata, 34963)
            ia = len(accessors)
            accessors.append({"bufferView": iv, "componentType": 5125, "count": len(idx), "type": "SCALAR"})
            meshes.append({"name": name, "primitives": [{
                "attributes": {"POSITION": pa, "NORMAL": na, "COLOR_0": ca},
                "indices": ia, "material": 0}]})
            node["mesh"] = len(meshes) - 1
        nodes.append(node)
        name_to_node[name] = i

    # parent/child wiring
    children_of = {i: [] for i in range(len(nodes))}
    roots = []
    for name, bone, *_ in prims:
        i = name_to_node[name]
        if bone.parent and bone.parent in name_to_node:
            children_of[name_to_node[bone.parent]].append(i)
        else:
            roots.append(i)
    for i, kids in children_of.items():
        if kids: nodes[i]["children"] = kids

    gltf = {
        "asset": {"version": "2.0", "generator": "DesignBridge"},
        "scene": 0, "scenes": [{"nodes": roots}],
        "nodes": nodes, "meshes": meshes, "accessors": accessors,
        "bufferViews": bufferViews,
        "materials": [{"name": "vertexcolor", "pbrMetallicRoughness": {
            "baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0, "roughnessFactor": 0.9}}],
    }

    # animations -> glTF channels (rotation/translation per bone over time)
    animations = []
    for aname, anim in doc.anims.items():
        by_bone = {}
        for k in anim.keys:
            by_bone.setdefault(k.bone, []).append(k)
        channels, samplers = [], []
        for bname, keys in by_bone.items():
            if bname not in name_to_node: continue
            node = name_to_node[bname]
            keys = sorted(keys, key=lambda k: k.t)
            times = [k.t for k in keys]
            for chan_path, extract, comp in (
                ("rotation", lambda k: k.rot, 4),
                ("translation", lambda k: k.pos, 3)):
                kk = [k for k in keys if extract(k) is not None]
                if not kk: continue
                path_is_rot = (chan_path == "rotation")
                t = [k.t for k in kk]
                tdata = b"".join(struct.pack("<f", float(v)) for v in t)
                tv = add_view(tdata)
                ta = len(accessors)
                accessors.append({"bufferView": tv, "componentType": 5126, "count": len(t),
                                  "type": "SCALAR", "min": [min(t)], "max": [max(t)]})
                vals = []
                for k in kk:
                    if path_is_rot:
                        vals.append(_euler_quat(extract(k)))
                    else:
                        p = extract(k)
                        vals.append((p[0], p[1], p[2], 0))
                if path_is_rot:
                    vdata = b"".join(struct.pack("<4f", *v) for v in vals)
                    typ = "VEC4"
                else:
                    vdata = b"".join(struct.pack("<3f", v[0], v[1], v[2]) for v in vals)
                    typ = "VEC3"
                vv = add_view(vdata)
                va = len(accessors)
                accessors.append({"bufferView": vv, "componentType": 5126, "count": len(vals), "type": typ})
                samplers.append({"input": ta, "output": va, "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1,
                                 "target": {"node": node, "path": chan_path}})
        if channels:
            animations.append({"name": aname, "channels": channels, "samplers": samplers})
    if animations:
        gltf["animations"] = animations

    buf = b"".join(bin_parts)
    gltf["buffers"] = [{"byteLength": len(buf)}]
    jtext = json.dumps(gltf, separators=(",", ":")).encode()
    jtext += b" " * ((4 - len(jtext) % 4) % 4)
    bpad = buf + b"\x00" * ((4 - len(buf) % 4) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(jtext) + 8 + len(bpad))
    glb += struct.pack("<II", len(jtext), 0x4E4F534A) + jtext
    glb += struct.pack("<II", len(bpad), 0x004E4942) + bpad
    with open(path, "wb") as f:
        f.write(glb)
    return len(glb)


def _euler_quat(deg):
    """XYZ Euler degrees -> quaternion (x,y,z,w) for glTF."""
    rx, ry, rz = [math.radians(d) / 2 for d in deg]
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    return (sx * cy * cz + cx * sy * sz,
            cx * sy * cz - sx * cy * sz,
            cx * cy * sz + sx * sy * cz,
            cx * cy * cz - sx * sy * sz)


def export_obj(doc, path):
    with open(path, "w") as f:
        f.write(f"# DesignBridge model {doc.name}\n")
        n = 1
        for name in doc.order:
            f.write(f"o {name}\n")
            for box in doc.bones[name].boxes:
                for a, b, c in box_tris(box):
                    for v in (a, b, c):
                        f.write(f"v {v[0]:.4f} {v[1]:.4f} {v[2]:.4f}\n")
                    f.write(f"f {n} {n+1} {n+2}\n"); n += 3


def export_strata_json(doc, path):
    """Box-model JSON close to STRATA's MobRenderer blocky models: a list of
    bones, each with a pivot and axis-aligned cubes (origin+size) and colour.
    STRATA already renders boxes with per-vertex colour, so this maps directly."""
    out = {"name": doc.name, "format": "designbridge.boxmodel.v1", "bones": []}
    for name in doc.order:
        bone = doc.bones[name]
        out["bones"].append({
            "name": name, "parent": bone.parent, "pivot": list(bone.pivot),
            "cubes": [{"origin": list(b.lo),
                       "size": [b.hi[i] - b.lo[i] for i in range(3)],
                       "color": hex_of(b.color)} for b in bone.boxes],
        })
    if doc.anims:
        out["animations"] = {}
        for aname, anim in doc.anims.items():
            out["animations"][aname] = {"loop": anim.loop, "keys": [
                {"t": k.t, "bone": k.bone,
                 **({"rot": list(k.rot)} if k.rot else {}),
                 **({"pos": list(k.pos)} if k.pos else {})}
                for k in anim.keys]}
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    return out


# ---------------------------------------------------------------- model parse
def _strip_comment_m(line):
    if line.lstrip().startswith(("#", "//")):
        return ""
    out = []
    for i, ch in enumerate(line):
        if ch == "#" and i > 0 and line[i - 1] in " \t":
            break
        out.append(ch)
    return "".join(out)


def _toks(s):
    out, buf, depth, q = [], "", 0, None
    for ch in s:
        if q:
            buf += ch
            if ch == q: q = None
            continue
        if ch in "\"'": q = ch; buf += ch; continue
        if ch in "([": depth += 1; buf += ch; continue
        if ch in ")]": depth -= 1; buf += ch; continue
        if depth == 0 and ch.isspace():
            if buf: out.append(buf); buf = ""
            continue
        buf += ch
    if buf: out.append(buf)
    return out


def _tuple(s):
    s = s.strip()
    if s and s[0] in "([":
        inner = s[1:-1] if s[-1] in ")]" else s[1:]
        return tuple(float(p) for p in inner.split(",") if p.strip())
    return None


def _time(v):
    v = str(v).strip()
    if v.endswith("s"):
        return float(v[:-1])
    return float(v)


def parse_model(text, doc=None):
    doc = doc or ModelDoc()
    if "<<<DESIGN" in text:
        text = text.split("<<<DESIGN", 1)[1].split(">>>", 1)[0]
        doc.repairs.append("extracted content between <<<DESIGN and >>> sentinels")
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            doc.repairs.append("stripped markdown code fence")
            text = re.sub(r"^[a-zA-Z0-9_+-]*\n", "", parts[1])

    cur_bone, cur_anim = None, None
    for ln, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment_m(raw).rstrip()
        if not line.strip(): continue
        indented = len(line) - len(line.lstrip()) > 0
        toks = _toks(line.strip())
        head = toks[0].lower()
        words = [t for t in toks[1:] if "=" not in t and not t.startswith("(")]
        tups = [t for t in toks[1:] if t.startswith("(")]
        kw = {}
        for t in toks[1:]:
            if "=" in t and not t.startswith("("):
                k, v = t.split("=", 1); kw[k.strip().lower()] = v.strip()

        if head == "model":
            doc.name = (words[0].strip("\"'") if words else "model")
            cur_bone = cur_anim = None
            continue
        if head == "bone":
            if not words:
                doc.err(ln, "bone needs a name", "bone body pivot=(0,0,0)", "NAME"); continue
            nm = words[0]
            if nm in doc.bones:
                doc.err(ln, f"bone '{nm}' already defined", "use a different name", "DUP"); continue
            pivot = _tuple(kw.get("pivot", "(0,0,0)")) or (0, 0, 0)
            pivot = tuple(list(pivot) + [0.0] * (3 - len(pivot)))[:3]
            parent = kw.get("parent")
            b = Bone(nm, pivot, parent, ln)
            doc.bones[nm] = b; doc.order.append(nm)
            cur_bone, cur_anim = b, None
            continue
        if head == "box":
            frm = _tuple(kw.get("from", "")) 
            to = _tuple(kw.get("to", ""))
            if frm is None or to is None:
                # allow positional: box (x,y,z) (x,y,z)
                if len(tups) >= 2:
                    frm, to = _tuple(tups[0]), _tuple(tups[1])
            if frm is None or to is None:
                doc.err(ln, "box needs from= and to= corners",
                        "box from=(-4,0,-2) to=(4,12,2) color=#8a5a3a", "DIM"); continue
            lo = tuple(min(frm[i], to[i]) for i in range(3))
            hi = tuple(max(frm[i], to[i]) for i in range(3))
            if any(hi[i] - lo[i] <= 0 for i in range(3)):
                doc.err(ln, "box has zero thickness on an axis",
                        "give from and to different values on every axis", "DIM"); continue
            color = parse_color(kw.get("color", "#b0b0b0"))
            box = Box(lo, hi, color, ln)
            if cur_bone is None:
                doc.err(ln, "box is outside any bone",
                        "declare a bone first:  bone body\\n  box from=... to=...", "ORDER"); continue
            cur_bone.boxes.append(box)
            continue
        if head in ("animation", "anim"):
            if not words:
                doc.err(ln, "animation needs a name", 'animation "attack" loop=false', "NAME"); continue
            nm = words[0].strip("\"'")
            loop = str(kw.get("loop", "false")).lower() in ("true", "yes", "1", "on", "loop")
            a = Anim(nm, loop, ln); doc.anims[nm] = a
            cur_anim, cur_bone = a, None
            continue
        if head in ("key", "keyframe") or (cur_anim is not None and head in doc.bones):
            # forms:  key bone=arm t=0.2 rot=(-90,0,0)
            #         key arm t=0.2 rot=(...)      (bone as first word)
            #         arm t=0.2 rot=(...)          (bone as the statement head)
            if cur_anim is None:
                doc.err(ln, "keyframe outside an animation",
                        'put it under:  animation "name"', "ORDER"); continue
            bone = kw.get("bone") or (words[0] if words and head in ("key", "keyframe") else head)
            if bone not in doc.bones:
                from_sg = _suggest_m(bone, list(doc.bones))
                doc.err(ln, f"keyframe targets unknown bone '{bone}'",
                        f"did you mean '{from_sg}'?" if from_sg else
                        f"bones: {', '.join(doc.order)}", "REF"); continue
            t = _time(kw.get("t", kw.get("at", "0")))
            rot = _tuple(kw.get("rot", "")) if "rot" in kw else None
            pos = _tuple(kw.get("pos", "")) if "pos" in kw else None
            if rot is None and pos is None:
                doc.err(ln, "keyframe does nothing (no rot= or pos=)",
                        "key arm t=0.2 rot=(-90,0,0)", "EMPTY"); continue
            cur_anim.keys.append(Keyframe(t, bone, rot, pos))
            continue

        alt = _suggest_m(head, ["model", "bone", "box", "animation", "key"])
        doc.err(ln, f"unknown statement '{head}'",
                f"did you mean '{alt}'?" if alt else
                "statements: model, bone, box, animation, key", "STMT")

    # validation
    for nm, b in doc.bones.items():
        if b.parent and b.parent not in doc.bones:
            alt = _suggest_m(b.parent, list(doc.bones))
            doc.err(b.line, f"bone '{nm}' has unknown parent '{b.parent}'",
                    f"did you mean 'parent={alt}'?" if alt else
                    f"bones: {', '.join(doc.order)}", "REF")
    if not doc.bones and not doc.errors:
        doc.err(0, "model has no bones - nothing to build",
                "bone body\\n  box from=(-4,0,-2) to=(4,12,2) color=#8a5a3a", "EMPTY")
    bones_with_boxes = [nm for nm in doc.order if doc.bones[nm].boxes]
    if doc.bones and not bones_with_boxes and not doc.errors:
        doc.warn(0, "no bone has any boxes - the model is empty",
                 "add a box to a bone", "EMPTY")
    return doc


def _suggest_m(word, opts):
    if not opts: return None
    def lev(a, b):
        if not a: return len(b)
        if not b: return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            cur = [i + 1]
            for j, cb in enumerate(b):
                cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
            prev = cur
        return prev[-1]
    best = min(opts, key=lambda o: lev(word.lower(), o.lower()))
    return best if lev(word.lower(), best.lower()) <= 2 else None

# ---------------------------------------------------------------- tokens
# Design tokens for physical design. These carry engineering knowledge so
# the model does not have to invent (and hallucinate) numbers.
TOKENS = {
    # wall thickness, FDM-printable
    "wall.thin": 1.2, "wall.std": 2.4, "wall.thick": 3.2, "wall.structural": 4.8,
    # fit clearances between mating parts
    "clear.press": 0.05, "clear.slip": 0.20, "clear.loose": 0.40, "clear.sloppy": 0.80,
    # cosmetic radii
    "fillet.sm": 1.0, "fillet.md": 3.0, "fillet.lg": 6.0, "fillet.xl": 12.0,
    # metric fastener hardware
    "m3.hole": 3.4, "m3.tap": 2.5, "m3.head": 6.0, "m3.nut": 5.5,
    "m4.hole": 4.5, "m4.tap": 3.3, "m4.head": 8.0, "m4.nut": 7.0,
    "m5.hole": 5.5, "m5.tap": 4.2, "m5.head": 10.0, "m5.nut": 8.0,
    # ergonomics
    "grip.finger": 22.0, "grip.hand": 45.0, "grip.thumb": 20.0,
    # print process
    "layer": 0.2, "nozzle": 0.4, "overhang.max_deg": 45.0,
}
BUILD_VOLUME = (220.0, 220.0, 250.0)   # typical desktop FDM printer

PRIMS = ("circle", "rect", "poly", "path", "hole", "ellipse", "ngon", "slot")
OPS = ("extrude", "revolve", "loft")
KEYWORDS = ("design", "profile", "solid", "assembly", "part", "note")


# ---------------------------------------------------------------- utils
def lev(a, b):
    """Levenshtein distance, for did-you-mean hints."""
    if a == b: return 0
    if not a: return len(b)
    if not b: return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
        prev = cur
    return prev[-1]


def suggest(word, options):
    if not options: return None
    best = min(options, key=lambda o: lev(word.lower(), o.lower()))
    return best if lev(word.lower(), best.lower()) <= max(2, len(word) // 3) else None


def near(a, b, eps=1e-9):
    return abs(a - b) < eps


# ---------------------------------------------------------------- 2D geometry
def dedupe(pts, closed=True, eps=1e-7):
    """Drop repeated neighbours. Two identical consecutive points make a
    zero-length edge, which quietly breaks triangulation and leaves the mesh
    non-manifold - so every generated outline passes through here."""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    if closed and len(out) > 1 and abs(out[0][0] - out[-1][0]) < eps \
            and abs(out[0][1] - out[-1][1]) < eps:
        out.pop()
    return out


def poly_area(p):
    """Signed area. Positive = counter-clockwise."""
    s = 0.0
    for i in range(len(p)):
        x1, y1 = p[i]; x2, y2 = p[(i + 1) % len(p)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def ccw(p):
    return p if poly_area(p) > 0 else p[::-1]


def cw(p):
    return p if poly_area(p) < 0 else p[::-1]


def seg_hit(a, b, c, d):
    """True if open segments ab and cd properly cross (shared endpoints ignored)."""
    def o(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)
    for p in (a, b):
        for q in (c, d):
            if near(p[0], q[0], 1e-9) and near(p[1], q[1], 1e-9):
                return False
    o1, o2, o3, o4 = o(a, b, c), o(a, b, d), o(c, d, a), o(c, d, b)
    return o1 != o2 and o3 != o4 and o1 != 0 and o2 != 0 and o3 != 0 and o4 != 0


def self_intersects(p):
    n = len(p)
    for i in range(n):
        for j in range(i + 1, n):
            if seg_hit(p[i], p[(i + 1) % n], p[j], p[(j + 1) % n]):
                return (i, j)
    return None


def point_in_poly(pt, poly):
    x, y = pt; inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi: inside = not inside
    return inside


def seg_dist(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def poly_gap(p, q):
    """Minimum distance between two polygon boundaries."""
    best = float("inf")
    for i in range(len(p)):
        for j in range(len(q)):
            best = min(best, seg_dist(p[i], q[j], q[(j + 1) % len(q)]),
                       seg_dist(q[j], p[i], p[(i + 1) % len(p)]))
    return best


# ---------------------------------------------------------------- triangulation
# A faithful port of Mapbox's earcut algorithm (ISC licence), without its
# z-order hash optimisation. Ear clipping is the only tessellation that
# introduces no new vertices, which is what keeps the extruded caps welded to
# the side walls and the whole solid watertight. The two recovery passes
# (cureLocalIntersections, splitEarcut) are what make it survive the awkward
# outlines a language model will hand us.

class _N:
    __slots__ = ("i", "x", "y", "prev", "next", "steiner")

    def __init__(self, i, x, y):
        self.i, self.x, self.y = i, x, y
        self.prev = self.next = None
        self.steiner = False


def _area(p, q, r):
    """Negative for a counter-clockwise turn."""
    return (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)


def _eq(a, b):
    return a.x == b.x and a.y == b.y


def _sign(x):
    return (x > 0) - (x < 0)


def _ring_area(ring):
    """> 0 for counter-clockwise."""
    s, n = 0.0, len(ring)
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[i - 1]
        s += (xj - xi) * (yi + yj)
    return s


def _insert(i, x, y, last):
    p = _N(i, x, y)
    if last is None:
        p.prev = p.next = p
    else:
        p.next = last.next
        p.prev = last
        last.next.prev = p
        last.next = p
    return p


def _remove(p):
    p.next.prev = p.prev
    p.prev.next = p.next


def _link_ring(ring, base, want_ccw):
    n = len(ring)
    ccw = _ring_area(ring) > 0
    order = range(n) if want_ccw == ccw else range(n - 1, -1, -1)
    last = None
    for i in order:
        last = _insert(base + i, ring[i][0], ring[i][1], last)
    if last is not None and _eq(last, last.next):
        _remove(last)
        last = last.next
    return last


def _filter_points(start, end=None):
    if start is None:
        return None
    end = end or start
    p, again = start, True
    while again or p is not end:
        again = False
        if not p.steiner and _eq(p, p.next):
            _remove(p)
            p = end = p.prev
            if p is p.next:
                return None
            again = True
        else:
            p = p.next
    return end


def _point_in_tri(ax, ay, bx, by, cx, cy, px, py):
    return ((cx - px) * (ay - py) >= (ax - px) * (cy - py)
            and (ax - px) * (by - py) >= (bx - px) * (ay - py)
            and (bx - px) * (cy - py) >= (cx - px) * (by - py))


def _is_ear(ear):
    a, b, c = ear.prev, ear, ear.next
    ar = _area(a, b, c)
    if ar > 0:
        return False                                  # reflex: never an ear
    if ar == 0:
        # collinear. Clipping it emits a zero-area triangle, which is harmless
        # and keeps vertex b on the boundary where the side wall expects it.
        return min(a.x, c.x) - 1e-9 <= b.x <= max(a.x, c.x) + 1e-9 \
            and min(a.y, c.y) - 1e-9 <= b.y <= max(a.y, c.y) + 1e-9
    p = c.next
    while p is not a:
        if _point_in_tri(a.x, a.y, b.x, b.y, c.x, c.y, p.x, p.y) \
                and _area(p.prev, p, p.next) >= 0:
            return False
        p = p.next
    return True


def _on_segment(p, q, r):
    return (min(p.x, r.x) <= q.x <= max(p.x, r.x)
            and min(p.y, r.y) <= q.y <= max(p.y, r.y))


def _intersects(p1, q1, p2, q2):
    o1, o2 = _sign(_area(p1, q1, p2)), _sign(_area(p1, q1, q2))
    o3, o4 = _sign(_area(p2, q2, p1)), _sign(_area(p2, q2, q1))
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, p2, q1): return True
    if o2 == 0 and _on_segment(p1, q2, q1): return True
    if o3 == 0 and _on_segment(p2, p1, q2): return True
    if o4 == 0 and _on_segment(p2, q1, q2): return True
    return False


def _intersects_polygon(a, b):
    p = a
    while True:
        if p.i != a.i and p.next.i != a.i and p.i != b.i and p.next.i != b.i \
                and _intersects(p, p.next, a, b):
            return True
        p = p.next
        if p is a:
            return False


def _locally_inside(a, b):
    if _area(a.prev, a, a.next) < 0:
        return _area(a, b, a.next) >= 0 and _area(a, a.prev, b) >= 0
    return _area(a, b, a.prev) < 0 or _area(a, a.next, b) < 0


def _middle_inside(a, b):
    p, inside = a, False
    px, py = (a.x + b.x) / 2.0, (a.y + b.y) / 2.0
    while True:
        if ((p.y > py) != (p.next.y > py)) and p.next.y != p.y \
                and px < (p.next.x - p.x) * (py - p.y) / (p.next.y - p.y) + p.x:
            inside = not inside
        p = p.next
        if p is a:
            return inside


def _valid_diagonal(a, b):
    return (a.next.i != b.i and a.prev.i != b.i and not _intersects_polygon(a, b)
            and ((_locally_inside(a, b) and _locally_inside(b, a) and _middle_inside(a, b))
                 and (_area(a.prev, a, b.prev) or _area(a, b.prev, b))
                 or (_eq(a, b) and _area(a.prev, a, a.next) > 0
                     and _area(b.prev, b, b.next) > 0)))


def _split_polygon(a, b):
    a2, b2 = _N(a.i, a.x, a.y), _N(b.i, b.x, b.y)
    an, bp = a.next, b.prev
    a.next, an.prev = b, a2
    b.prev = a
    a2.next, a2.prev = an, b2
    b2.next, b2.prev = a2, bp
    bp.next = b2
    return b2


def _cure_local_intersections(start, out):
    p = start
    while True:
        a, b = p.prev, p.next.next
        if not _eq(a, b) and _intersects(a, p, p.next, b) \
                and _locally_inside(a, b) and _locally_inside(b, a):
            out.append(((a.x, a.y), (p.x, p.y), (b.x, b.y)))
            _remove(p); _remove(p.next)
            p = start = b
        p = p.next
        if p is start:
            break
    return _filter_points(p)


def _earcut_linked(ear, out, depth=0):
    if ear is None or depth > 64:
        return
    stop = ear
    while ear.prev is not ear.next:
        prev, nxt = ear.prev, ear.next
        if _is_ear(ear):
            out.append(((prev.x, prev.y), (ear.x, ear.y), (nxt.x, nxt.y)))
            _remove(ear)
            ear = stop = nxt.next
            continue
        ear = nxt
        if ear is stop:                               # stuck: try to recover
            cured = _cure_local_intersections(_filter_points(ear), out)
            if cured is not None and cured is not ear:
                _earcut_linked(cured, out, depth + 1)
                return
            # last resort: cut the polygon in two along a valid diagonal
            a = ear
            while True:
                b = a.next.next
                while b is not a.prev:
                    if a.i != b.i and _valid_diagonal(a, b):
                        c = _split_polygon(a, b)
                        _earcut_linked(_filter_points(a, a), out, depth + 1)
                        _earcut_linked(_filter_points(c, c), out, depth + 1)
                        return
                    b = b.next
                a = a.next
                if a is ear:
                    return
    return


def _leftmost(start):
    p = m = start
    while True:
        if p.x < m.x or (p.x == m.x and p.y < m.y):
            m = p
        p = p.next
        if p is start:
            return m


def _find_hole_bridge(hole, outer):
    p, qx, m = outer, float("-inf"), None
    hx, hy = hole.x, hole.y
    while True:
        if hy <= p.y and hy >= p.next.y and p.next.y != p.y:
            x = p.x + (hy - p.y) / (p.next.y - p.y) * (p.next.x - p.x)
            if x <= hx and x > qx:
                qx = x
                m = p if p.x < p.next.x else p.next
                if x == hx:
                    return m
        p = p.next
        if p is outer:
            break
    if m is None:
        return None
    stop, mx, my, tan_min = m, m.x, m.y, float("inf")
    p = m
    while True:
        if hx >= p.x >= mx and hx != p.x and _point_in_tri(
                hx if hy < my else qx, hy, mx, my, qx if hy < my else hx, hy, p.x, p.y):
            tan = abs(hy - p.y) / (hx - p.x)
            if _locally_inside(p, hole) and (tan < tan_min or
                                             (tan == tan_min and p.x > m.x)):
                m, tan_min = p, tan
        p = p.next
        if p is stop:
            return m


def _eliminate_holes(holes_nodes, outer, skipped=None):
    queue = sorted((_leftmost(h) for h in holes_nodes if h is not None),
                   key=lambda n: (n.x, n.y))
    for hole in queue:
        bridge = _find_hole_bridge(hole, outer)
        if bridge is None:
            if skipped is not None:
                skipped.append(hole.i)
            continue
        b2 = _split_polygon(bridge, hole)
        _filter_points(b2, b2.next)
        outer = _filter_points(outer, outer.next) or outer
    return outer


def triangulate(outer, holes=None, skipped=None):
    """Tessellate a 2D outline, with holes, into triangles that use only the
    original vertices. Returns a list of ((x,y),(x,y),(x,y))."""
    if not outer or len(outer) < 3:
        return []
    node = _link_ring(list(outer), 0, True)            # outer ring: CCW
    if node is None:
        return []
    if holes:
        base = len(outer)
        hn = []
        for h in holes:
            if h and len(h) >= 3:
                hn.append(_link_ring(list(h), base, False))   # holes: CW
                base += len(h)
        node = _eliminate_holes(hn, node, skipped)
    out = []
    _earcut_linked(_filter_points(node), out)
    return out


# ---------------------------------------------------------------- mesh
class Mesh:
    def __init__(self, name="mesh"):
        self.name = name
        self.tris = []                  # list of (v0, v1, v2), each v = (x,y,z)

    def add(self, a, b, c):
        self.tris.append((a, b, c))

    def quad(self, a, b, c, d):
        self.add(a, b, c); self.add(a, c, d)

    def bbox(self):
        if not self.tris: return (0, 0, 0), (0, 0, 0)
        xs = [v[0] for t in self.tris for v in t]
        ys = [v[1] for t in self.tris for v in t]
        zs = [v[2] for t in self.tris for v in t]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def volume(self):
        """Signed volume via the divergence theorem. mm^3."""
        v = 0.0
        for a, b, c in self.tris:
            v += (a[0] * (b[1] * c[2] - b[2] * c[1])
                  - a[1] * (b[0] * c[2] - b[2] * c[0])
                  + a[2] * (b[0] * c[1] - b[1] * c[0]))
        return v / 6.0

    def area(self):
        s = 0.0
        for a, b, c in self.tris:
            u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
            s += 0.5 * math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        return s

    def manifold(self):
        """Returns (ok, n_open_edges, n_bad_winding). A closed, consistently
        wound surface has every undirected edge shared by exactly 2 triangles
        and every directed edge used exactly once."""
        q = lambda v: (round(v[0], 5), round(v[1], 5), round(v[2], 5))
        und, dire = {}, {}
        for a, b, c in self.tris:
            for p, r in ((a, b), (b, c), (c, a)):
                p, r = q(p), q(r)
                if p == r: continue
                k = tuple(sorted((p, r)))
                und[k] = und.get(k, 0) + 1
                dire[(p, r)] = dire.get((p, r), 0) + 1
        open_e = sum(1 for v in und.values() if v != 2)
        bad = sum(1 for v in dire.values() if v != 1)
        return (open_e == 0 and bad == 0), open_e, bad

    def transform(self, at=(0, 0, 0), rot=(0, 0, 0), scale=1.0):
        rx, ry, rz = [math.radians(a) for a in rot]
        cx, sx = math.cos(rx), math.sin(rx)
        cy, sy = math.cos(ry), math.sin(ry)
        cz, sz = math.cos(rz), math.sin(rz)
        out = Mesh(self.name)
        for tri in self.tris:
            nt = []
            for x, y, z in tri:
                x, y, z = x * scale, y * scale, z * scale
                y, z = y * cx - z * sx, y * sx + z * cx
                x, z = x * cy + z * sy, -x * sy + z * cy
                x, y = x * cz - y * sz, x * sz + y * cz
                nt.append((x + at[0], y + at[1], z + at[2]))
            out.add(*nt)
        return out


def extrude(outer, holes, h, taper=0.0, twist=0.0, steps=1, skipped=None):
    """Sweep a 2D profile along +Z. taper = degrees of draft, twist = degrees
    over the full height. Produces a closed manifold."""
    m = Mesh()
    steps = max(1, int(steps) if (taper or twist) else 1)
    rings_o, rings_h = [], []
    for s in range(steps + 1):
        t = s / steps
        z = h * t
        off = -math.tan(math.radians(taper)) * z
        ang = math.radians(twist) * t
        ca, sa = math.cos(ang), math.sin(ang)
        def xf(ring, sign):
            out = []
            for x, y in ring:
                r = math.hypot(x, y)
                if r > 1e-9:
                    k = (r + sign * off) / r
                    x, y = x * k, y * k
                out.append((x * ca - y * sa, x * sa + y * ca, z))
            return out
        rings_o.append(xf(ccw(outer), 1))
        rings_h.append([xf(cw(hh), -1) for hh in holes])

    # side walls, outer (normals point out)
    for s in range(steps):
        lo, hi = rings_o[s], rings_o[s + 1]
        n = len(lo)
        for i in range(n):
            j = (i + 1) % n
            m.quad(lo[i], lo[j], hi[j], hi[i])
    # side walls, holes (reversed winding so normals point into the cavity)
    for hidx in range(len(holes)):
        for s in range(steps):
            lo, hi = rings_h[s][hidx], rings_h[s + 1][hidx]
            n = len(lo)
            for i in range(n):
                j = (i + 1) % n
                m.quad(lo[i], lo[j], hi[j], hi[i])
    # caps
    for tri in triangulate(outer, holes, skipped):
        bo = [(p[0], p[1], 0.0) for p in tri]
        m.add(bo[0], bo[2], bo[1])                      # bottom, normal -Z
    top_o = [(p[0], p[1]) for p in rings_o[-1]]
    top_h = [[(p[0], p[1]) for p in hh] for hh in rings_h[-1]]
    for tri in triangulate(top_o, top_h, skipped):
        m.add(*[(p[0], p[1], h) for p in tri])          # top, normal +Z
    return m


def _close_to_axis(path):
    """A revolve profile only bounds a solid together with its axis. Close the
    open polyline onto the axis (x=0) so the sweep is always a real solid."""
    pts = [(abs(x), y) for x, y in path]
    out = []
    for p in pts:                                   # drop duplicate neighbours
        if not out or abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    if out[0][0] > 1e-9:
        out.insert(0, (0.0, out[0][1]))
    if out[-1][0] > 1e-9:
        out.append((0.0, out[-1][1]))
    if abs(out[0][1] - out[-1][1]) < 1e-9 and len(out) > 2:
        out = out[:-1]                              # already closed on the axis
    return out


def revolve(path, angle=360.0, segments=64):
    """Revolve a 2D profile around an axis of its own plane.

    axis='y': spin about the vertical axis x=0. Profile x is radius, profile y
    is height. This is the lathe move - mugs, vases, bottles, knobs, wheels.
    The profile is closed onto the axis automatically, so an open polyline
    like (0,0) (40,0) (40,95) (37,95) (37,4) (0,4) yields a watertight mug.
    """
    segments = max(3, int(segments))
    angle = max(1e-6, min(360.0, float(angle)))
    full = abs(angle - 360.0) < 1e-6
    cs = _close_to_axis(path)                       # closed cross-section
    n = segments
    da = math.radians(angle) / n
    nrings = n if full else n + 1

    def place(x, y, a):
        ca, sa = math.cos(a), math.sin(a)
        return (x * ca, x * sa, y)

    rings = [[place(p[0], p[1], da * s) for p in cs] for s in range(nrings)]
    m = Mesh()
    K = len(rings)
    for s in range(K if full else K - 1):
        A, B = rings[s], rings[(s + 1) % K]
        for i in range(len(cs)):
            j = (i + 1) % len(cs)
            r0, r1 = cs[i][0], cs[j][0]
            on0, on1 = r0 < 1e-9, r1 < 1e-9
            if on0 and on1:
                continue                            # edge lies on the axis
            if on0:
                m.add(A[i], B[j], A[j])             # collapsed quad -> triangle
            elif on1:
                m.add(A[i], B[i], A[j])
            else:
                m.quad(A[i], B[i], B[j], A[j])
    if not full:                                    # two flat faces close the wedge
        for tri in triangulate(cs):
            m.add(*[place(p[0], p[1], 0.0) for p in tri])
            m.add(*[place(p[0], p[1], math.radians(angle)) for p in tri][::-1])
    return m


def loft(a, b, h, steps=1):
    """Blend profile a at z=0 into profile b at z=h. Both are resampled to a
    common vertex count, so a circle can morph into a square."""
    A, B = ccw(a), ccw(b)
    n = max(len(A), len(B))
    A, B = resample(A, n), resample(B, n)
    m = Mesh()
    rings = []
    for s in range(steps + 1):
        t = s / steps
        rings.append([(A[i][0] * (1 - t) + B[i][0] * t,
                       A[i][1] * (1 - t) + B[i][1] * t, h * t) for i in range(n)])
    for s in range(steps):
        lo, hi = rings[s], rings[s + 1]
        for i in range(n):
            j = (i + 1) % n
            m.quad(lo[i], lo[j], hi[j], hi[i])
    for tri in triangulate(A):
        m.add((tri[0][0], tri[0][1], 0), (tri[2][0], tri[2][1], 0), (tri[1][0], tri[1][1], 0))
    for tri in triangulate(B):
        m.add(*[(p[0], p[1], h) for p in tri])
    return m


def resample(poly, n):
    """Even-arclength resample of a closed polygon to n points."""
    segs, total = [], 0.0
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        d = math.hypot(b[0] - a[0], b[1] - a[1]); segs.append((a, b, d)); total += d
    out, step = [], total / n
    for k in range(n):
        want, acc = k * step, 0.0
        for a, b, d in segs:
            if acc + d >= want - 1e-12 and d > 0:
                t = (want - acc) / d
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)); break
            acc += d
        else:
            out.append(poly[0])
    return out

# ---------------------------------------------------------------- diagnostics
class Diag:
    __slots__ = ("sev", "line", "msg", "fix", "code")

    def __init__(self, sev, line, msg, fix="", code=""):
        self.sev, self.line, self.msg, self.fix, self.code = sev, line, msg, fix, code

    def __repr__(self):
        loc = f"line {self.line}" if self.line else "input"
        s = f"  [{self.sev.upper()}] {loc}: {self.msg}"
        if self.fix: s += f"\n          FIX: {self.fix}"
        return s


class Profile:
    def __init__(self, name, line):
        self.name, self.line = name, line
        self.outer, self.holes, self.open_path = [], [], None
        self.bad = False

    @property
    def kind(self):
        return "path" if self.open_path else "closed"


class Solid:
    def __init__(self, name, op, line):
        self.name, self.op, self.line = name, op, line
        self.args, self.mesh, self.color = {}, None, None
        self.colorspec = None


class Part:
    def __init__(self, solid, line):
        self.solid, self.line = solid, line
        self.at, self.rot, self.scale, self.mirror = (0, 0, 0), (0, 0, 0), 1.0, None
        self.mesh = None
        self.color = None


class Doc:
    def __init__(self):
        self.name, self.units = "untitled", "mm"
        self.profiles, self.solids, self.parts = {}, {}, []
        self.notes, self.diags, self.repairs = [], [], []

    def err(self, *a, **k): self.diags.append(Diag("error", *a, **k))
    def warn(self, *a, **k): self.diags.append(Diag("warn", *a, **k))
    def info(self, *a, **k): self.diags.append(Diag("info", *a, **k))
    @property
    def errors(self): return [d for d in self.diags if d.sev == "error"]
    @property
    def warnings(self): return [d for d in self.diags if d.sev == "warn"]


# ---------------------------------------------------------------- lexing
_SMART = {"“": '"', "”": '"', "‘": "'", "’": "'",
          "–": "-", "—": "-", "×": "x", "−": "-",
          "（": "(", "）": ")", "，": ",", " ": " "}


def normalize(text, repairs):
    """Strip everything a chat model wraps around its answer, and repair the
    punctuation it tends to get wrong. Every repair is logged so the model
    learns what it did."""
    t = text
    for k, v in _SMART.items():
        if k in t:
            t = t.replace(k, v); repairs.append(f"normalized unicode punctuation {k!r} -> {v!r}")
    if "<<<DESIGN" in t:
        t = t.split("<<<DESIGN", 1)[1]
        t = t.split(">>>", 1)[0] if ">>>" in t else t
        repairs.append("extracted content between <<<DESIGN and >>> sentinels")
    elif "```" in t:
        parts = t.split("```")
        if len(parts) >= 3:
            body = parts[1]
            body = re.sub(r"^[a-zA-Z0-9_+-]*\n", "", body)
            t = body; repairs.append("stripped markdown code fence")
    if "\t" in t:
        t = t.replace("\t", "  "); repairs.append("converted tabs to spaces")
    return t


def split_tokens(s):
    """Split a statement into tokens, keeping quoted strings and (tuples) whole."""
    out, buf, depth, quote = [], "", 0, None
    for ch in s:
        if quote:
            buf += ch
            if ch == quote: quote = None
            continue
        if ch in "\"'":
            quote = ch; buf += ch; continue
        if ch in "([": depth += 1; buf += ch; continue
        if ch in ")]":
            depth -= 1; buf += ch; continue
        if depth == 0 and (ch.isspace() or ch == ","):
            if buf: out.append(buf); buf = ""
            continue
        buf += ch
    if buf: out.append(buf)
    return out


_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$", re.I)
_UNIT_SCALE = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "deg": 1.0, "°": 1.0}


_NUM_UNIT = re.compile(r"(?<![\w.])(\d+\.?\d*|\.\d+)\s*(mm|cm|m|in|deg|\u00b0)(?![\w.])", re.I)


def eval_expr(s, doc, line):
    """Numbers, design tokens and simple arithmetic: 40, $wall.std, 40-$wall.std*2."""
    import ast as _ast
    raw = s
    s = s.strip()
    # a unit is only a unit directly after a number, so $wall.thin keeps its tail
    s = _NUM_UNIT.sub(lambda m: repr(float(m.group(1)) * _UNIT_SCALE[m.group(2).lower()]), s)
    scale = 1.0
    if _NUM.match(s):
        return float(s) * scale
    def tok_sub(mo):
        key = mo.group(1)
        if key in TOKENS: return repr(TOKENS[key])
        alt = suggest(key, list(TOKENS))
        doc.err(line, f"unknown design token ${key}",
                f"use ${alt} (= {TOKENS[alt]}) or a plain number" if alt
                else "run `designbridge.py tokens` to list valid tokens", "TOKEN")
        return "0"
    expr = re.sub(r"\$([A-Za-z_][\w.]*)", tok_sub, s)
    if not re.fullmatch(r"[-+*/().\d\s eE]*", expr):
        doc.err(line, f"cannot read numeric value {raw!r}",
                "use a plain number (40), a token ($wall.std) or simple arithmetic "
                "(40-$wall.std*2)", "VALUE")
        return 0.0
    try:
        node = _ast.parse(expr, mode="eval")
        for n in _ast.walk(node):
            if not isinstance(n, (_ast.Expression, _ast.BinOp, _ast.UnaryOp, _ast.Constant,
                                  _ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.USub, _ast.UAdd)):
                raise ValueError
        return float(eval(compile(node, "<expr>", "eval"), {"__builtins__": {}}, {})) * scale
    except Exception:
        doc.err(line, f"cannot evaluate {raw!r}", "use a plain number or simple arithmetic", "VALUE")
        return 0.0


def parse_value(s, doc, line):
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", s):      # a hex colour, kept as text
        return s
    if s and s[0] in "([":
        inner = s[1:-1] if s[-1] in ")]" else s[1:]
        return tuple(eval_expr(p, doc, line) for p in inner.split(",") if p.strip())
    if s.lower() in ("true", "yes", "on"): return True
    if s.lower() in ("false", "no", "off"): return False
    if re.fullmatch(r"[A-Za-z_][\w]*", s): return s
    return eval_expr(s, doc, line)


def parse_statement(raw, doc, line):
    """-> (head, positionals, kwargs). Accepts key=val, key: val and key = val."""
    s = re.sub(r"\s*(?<![<>=!])=\s*", "=", raw.strip())
    s = re.sub(r"\b([A-Za-z_]\w*)\s*:\s*(?=\S)", r"\1=", s)
    toks = split_tokens(s)
    if not toks: return None, [], {}
    head = toks[0].lower()
    pos, kw = [], {}
    for t in toks[1:]:
        if "=" in t and not t.startswith("("):
            k, v = t.split("=", 1)
            kw[k.strip().lower()] = parse_value(v, doc, line)
        else:
            pos.append(t)
    return head, pos, kw


def check_keys(kw, allowed, doc, line, what):
    for k in list(kw):
        if k not in allowed:
            alt = suggest(k, allowed)
            doc.err(line, f"{what} does not take '{k}'",
                    f"did you mean '{alt}'?" if alt else f"valid keys: {', '.join(sorted(allowed))}",
                    "KEY")
            kw.pop(k)


# ---------------------------------------------------------------- shapes
def shape_points(head, kw, doc, line):
    """Turn a 2D primitive into a point list. Closed shapes -> polygon,
    'path' -> open polyline."""
    seg = int(kw.get("segments", 64))
    at = kw.get("at", (0.0, 0.0))
    if not isinstance(at, tuple) or len(at) < 2: at = (0.0, 0.0)
    ox, oy = at[0], at[1]
    rot = math.radians(float(kw.get("rot", 0.0) or 0.0))

    def fin(pts):
        if rot:
            c, s = math.cos(rot), math.sin(rot)
            pts = [(x * c - y * s, x * s + y * c) for x, y in pts]
        pts = [(x + ox, y + oy) for x, y in pts]
        return dedupe(pts, closed=(head != "path"))

    if head in ("circle", "ellipse"):
        if head == "circle":
            r = float(kw.get("r", kw.get("d", 0) / 2 if kw.get("d") else 0))
            rx = ry = r
        else:
            rx, ry = float(kw.get("rx", 0)), float(kw.get("ry", 0))
        if rx <= 0 or ry <= 0:
            doc.err(line, f"{head} needs a positive radius",
                    "circle r=20   (or d=40)" if head == "circle" else "ellipse rx=30 ry=15", "DIM")
            return []
        seg = max(8, min(256, seg))
        return fin([(rx * math.cos(2 * math.pi * i / seg), ry * math.sin(2 * math.pi * i / seg))
                    for i in range(seg)])

    if head in ("rect", "slot"):
        w, h = float(kw.get("w", 0)), float(kw.get("h", 0))
        if head == "slot" and h <= 0: h = w
        if w <= 0 or h <= 0:
            doc.err(line, f"{head} needs positive w and h", f"{head} w=60 h=30 r=4", "DIM")
            return []
        r = float(kw.get("r", w / 2 if head == "slot" else 0))
        r = max(0.0, min(r, min(w, h) / 2))   # exact, so coincident arc centres
        #                                        produce exactly duplicate points
        #                                        that dedupe() can remove
        hw, hh = w / 2, h / 2
        if r <= 1e-9:
            return fin([(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)])
        q = max(2, seg // 8)
        pts = []
        for cx, cy, a0 in ((hw - r, hh - r, 0), (-(hw - r), hh - r, 90),
                           (-(hw - r), -(hh - r), 180), (hw - r, -(hh - r), 270)):
            for i in range(q + 1):
                a = math.radians(a0 + 90 * i / q)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return fin(pts)

    if head == "ngon":
        n, r = int(kw.get("n", 6)), float(kw.get("r", 0))
        if n < 3 or r <= 0:
            doc.err(line, "ngon needs n>=3 and positive r", "ngon n=6 r=10", "DIM"); return []
        a0 = math.radians(float(kw.get("rot", 0.0) or 0.0))
        return [(ox + r * math.cos(a0 + 2 * math.pi * i / n),
                 oy + r * math.sin(a0 + 2 * math.pi * i / n)) for i in range(n)]

    if head in ("poly", "path"):
        pts = kw.get("_pos", [])
        if len(pts) < (3 if head == "poly" else 2):
            doc.err(line, f"{head} needs at least {3 if head == 'poly' else 2} points",
                    "poly (0,0) (20,0) (20,10)" if head == "poly"
                    else "path (0,0) (40,0) (40,95)", "DIM")
            return []
        return fin(pts)
    return []


# ---------------------------------------------------------------- parse
PRIM_KEYS = {
    "circle": {"r", "d", "at", "segments", "rot"},
    "ellipse": {"rx", "ry", "at", "segments", "rot"},
    "rect": {"w", "h", "r", "at", "segments", "rot"},
    "slot": {"w", "h", "r", "at", "segments", "rot"},
    "ngon": {"n", "r", "at", "rot"},
    "poly": {"at", "rot"},
    "path": {"at", "rot"},
}
OP_KEYS = {
    "extrude": {"from", "h", "height", "taper", "twist", "steps", "color"},
    "revolve": {"from", "angle", "segments", "color"},
    "loft": {"from", "to", "h", "height", "steps", "color"},
}


def parse(text):
    doc = Doc()
    text = normalize(text, doc.repairs)
    lines = text.splitlines()
    cur_profile, in_assembly = None, False

    for ln, raw in enumerate(lines, 1):
        line = _strip_comment(raw).rstrip()
        if not line.strip(): continue
        indented = len(line) - len(line.lstrip()) > 0
        bare = line.strip()
        if bare.split(None, 1)[0].lower() == "note":
            rest = bare[4:].strip()
            if rest.count('"') == 1:
                doc.err(ln, "this note has an unclosed quote",
                         "a note is one line: note \"...\" - break long text into "
                         "several note lines", "QUOTE")
            doc.notes.append(rest.strip("\"'").strip())
            continue
        head, pos, kw = parse_statement(line, doc, ln)
        if head is None: continue
        if pos:
            tup = [p for p in pos if p.startswith("(") or p.startswith("[")]
            kw["_pos"] = [parse_value(p, doc, ln) for p in tup]
            kw["_words"] = [p for p in pos if p not in tup]

        # ---- top level
        if head == "design":
            doc.name = (kw.get("_words") or [""])[0].strip("\"'") or kw.get("name", "untitled")
            if pos and pos[0][0] in "\"'": doc.name = pos[0].strip("\"'")
            doc.units = str(kw.get("units", "mm"))
            cur_profile, in_assembly = None, False
            continue
        if head == "profile":
            nm = (kw.get("_words") or [None])[0]
            if not nm:
                doc.err(ln, "profile needs a name", "profile body", "NAME"); continue
            if nm in doc.profiles:
                doc.err(ln, f"profile '{nm}' already defined on line {doc.profiles[nm].line}",
                        "give this profile a different name", "DUP"); continue
            cur_profile = Profile(nm, ln); doc.profiles[nm] = cur_profile
            in_assembly = False
            continue
        if head == "assembly":
            in_assembly, cur_profile = True, None
            continue
        if head == "solid":
            cur_profile, in_assembly = None, False
            words = kw.get("_words") or []
            nm = words[0] if words else None
            op = next((w.lower() for w in words[1:] if w.lower() in OPS), None)
            if op is None:
                op = next((w.lower() for w in words if w.lower() in OPS), None)
                if op and nm and nm.lower() == op: nm = None
            if not nm:
                doc.err(ln, "solid needs a name", "solid wall from=body extrude h=95", "NAME"); continue
            if not op:
                doc.err(ln, f"solid '{nm}' has no operation",
                        "add one of: extrude, revolve, loft -- e.g. "
                        f"solid {nm} from=<profile> extrude h=20", "OP"); continue
            if nm in doc.solids:
                doc.err(ln, f"solid '{nm}' already defined on line {doc.solids[nm].line}",
                        "rename it", "DUP"); continue
            kw.pop("_words", None); kw.pop("_pos", None)
            if op == "revolve" and "axis" in kw:
                kw.pop("axis")
                doc.err(ln, "revolve has no 'axis' option - it always spins about Z",
                        f"drop axis=, then lay the part over in the assembly: "
                        f"part {nm} rot=(0,90,0)", "ARG")
            check_keys(kw, OP_KEYS[op], doc, ln, f"{op}")
            s = Solid(nm, op, ln); s.args = kw; doc.solids[nm] = s
            if "color" in kw:
                s.colorspec = kw.get("color")
            continue

        # ---- inside a profile
        if cur_profile is not None and (indented or head in PRIMS):
            is_hole = head == "hole"
            if is_hole:
                words = kw.get("_words") or []
                sub = words[0].lower() if words else "circle"
                if sub not in PRIM_KEYS:
                    alt = suggest(sub, list(PRIM_KEYS))
                    doc.err(ln, f"hole shape '{sub}' is not a 2D primitive",
                            f"did you mean 'hole {alt} ...'?" if alt
                            else "use: hole circle r=10  /  hole rect w=20 h=8", "PRIM"); continue
                head = sub
            if head not in PRIM_KEYS:
                alt = suggest(head, list(PRIM_KEYS) + list(KEYWORDS))
                doc.err(ln, f"unknown statement '{head}' inside profile '{cur_profile.name}'",
                        f"did you mean '{alt}'?" if alt
                        else "profiles contain: circle, rect, ellipse, ngon, slot, poly, path, hole",
                        "PRIM"); continue
            kwc = {k: v for k, v in kw.items() if not k.startswith("_")}
            check_keys(kwc, PRIM_KEYS[head], doc, ln, head)
            kwc["_pos"] = kw.get("_pos", [])
            pts = shape_points(head, kwc, doc, ln)
            if not pts: continue
            if head == "path":
                if is_hole:
                    doc.err(ln, "a hole cannot be an open path", "use a closed shape for holes", "PRIM")
                elif cur_profile.open_path or cur_profile.outer:
                    doc.err(ln, f"profile '{cur_profile.name}' already has a shape",
                            "one outer shape per profile; put extra shapes in their own profile",
                            "DUP")
                else:
                    cur_profile.open_path = pts
            elif is_hole:
                if not cur_profile.outer:
                    doc.err(ln, f"profile '{cur_profile.name}' has a hole before its outer shape",
                            "declare the outer shape first, then its holes", "ORDER")
                else:
                    cur_profile.holes.append(pts)
            else:
                if cur_profile.outer or cur_profile.open_path:
                    doc.err(ln, f"profile '{cur_profile.name}' already has an outer shape "
                                f"- did you mean 'hole {head} ...'?",
                            f"hole {head} " + " ".join(f"{k}={v}" for k, v in kwc.items()
                                                      if not k.startswith('_')), "DUP")
                else:
                    cur_profile.outer = pts
            continue

        # ---- inside the assembly
        if head == "part":
            if not in_assembly:
                doc.warn(ln, "'part' used outside an assembly block",
                         "add a line 'assembly' before the part list", "ORDER")
            words = kw.get("_words") or []
            if not words:
                doc.err(ln, "part needs a solid name", "part wall at=(0,0,0)", "NAME"); continue
            p = Part(words[0], ln)
            kwc = {k: v for k, v in kw.items() if not k.startswith("_")}
            check_keys(kwc, {"at", "rot", "rotate", "scale", "mirror"}, doc, ln, "part")
            at = kwc.get("at", (0, 0, 0)); rot = kwc.get("rot", kwc.get("rotate", (0, 0, 0)))
            pad = lambda t: tuple(list(t) + [0.0] * (3 - len(t)))[:3] if isinstance(t, tuple) \
                else (float(t), 0.0, 0.0)
            p.at, p.rot = pad(at), pad(rot)
            p.scale = float(kwc.get("scale", 1.0) or 1.0)
            p.mirror = kwc.get("mirror")
            doc.parts.append(p)
            continue

        alt = suggest(head, list(KEYWORDS) + list(PRIMS))
        doc.err(ln, f"unknown statement '{head}'",
                f"did you mean '{alt}'?" if alt else
                "statements are: design, profile, solid, assembly, part, note", "STMT")

    if not doc.profiles and not doc.errors:
        doc.err(0, "no profiles found - the design is empty",
                "every design starts with a 2D profile, e.g.\n"
                "               profile body\n"
                "                 circle r=30", "EMPTY")
    return doc


# ---------------------------------------------------------------- compile
def build(doc):
    """Turn validated solids into meshes, then place them in the assembly."""
    for name, s in doc.solids.items():
        src = s.args.get("from")
        if not src:
            doc.err(s.line, f"solid '{name}' has no source profile",
                    f"solid {name} from=<profile> {s.op} ...", "REF"); continue
        if src not in doc.profiles:
            alt = suggest(str(src), list(doc.profiles))
            doc.err(s.line, f"solid '{name}' refers to unknown profile '{src}'",
                    f"did you mean 'from={alt}'?" if alt
                    else f"defined profiles: {', '.join(doc.profiles) or '(none)'}", "REF"); continue
        p = doc.profiles[src]
        if p.bad:
            doc.info(s.line, f"solid '{name}' was not built: profile '{src}' has errors above",
                     "fix the profile and this solid will build itself")
            continue

        if s.op == "extrude":
            if p.kind == "path":
                doc.err(s.line, f"cannot extrude profile '{src}' - it is an open path",
                        "extrude needs a closed shape (circle/rect/poly). "
                        "Open paths are for revolve.", "KIND"); continue
            h = float(s.args.get("h", s.args.get("height", 0)))
            if h <= 0:
                doc.err(s.line, f"extrude height must be positive (got {h})",
                        f"solid {name} from={src} extrude h=20", "DIM"); continue
            taper = float(s.args.get("taper", 0)); twist = float(s.args.get("twist", 0))
            steps = int(s.args.get("steps", 12 if (taper or twist) else 1))
            if abs(taper) >= 89:
                doc.err(s.line, f"taper of {taper} deg is impossible",
                        "keep taper between -45 and 45", "DIM"); continue
            off = math.tan(math.radians(taper)) * h
            if off > 0:
                cen = (sum(x for x, _ in p.outer) / len(p.outer),
                       sum(y for _, y in p.outer) / len(p.outer))
                inr = min(seg_dist(cen, p.outer[i], p.outer[(i + 1) % len(p.outer)])
                          for i in range(len(p.outer)))
                if off >= inr - 0.01:
                    doc.err(s.line, f"taper={taper} deg over h={h:g} pulls the outline in by "
                                    f"{off:.2f}{doc.units}, but it is only {inr:.2f}{doc.units} "
                                    "from its centre - it closes to a point before the top",
                            f"use taper<={math.degrees(math.atan(inr * 0.8 / h)):.1f} "
                            f"or h<={inr * 0.8 / math.tan(math.radians(taper)):.1f}", "TAPER")
                    continue
                bad = False
                for k, hh in enumerate(p.holes):
                    g = poly_gap(p.outer, hh) - 2 * off
                    if g < TOKENS["wall.thin"] - 0.06:
                        doc.err(s.line, f"taper={taper} deg over h={h:g} thins the wall at hole "
                                        f"{k + 1} to {max(g, 0):.2f}{doc.units} at the top"
                                        + (" - the hole breaks out through the side" if g < 0 else ""),
                                "a taper shrinks the outside and widens the holes, so it eats "
                                "the wall twice as fast. Reduce taper, reduce h, or start with "
                                "a thicker wall", "TAPER")
                        bad = True
                if bad:
                    continue
            sk = []
            s.mesh = extrude(p.outer, p.holes, h, taper, twist, steps, sk)
            if sk:
                doc.err(s.line, f"solid '{name}': {len(set(sk))} hole(s) of profile "
                                f"'{src}' could not be cut - they overlap the outline "
                                "or each other",
                        "holes must be fully inside the outer shape and must not touch "
                        "one another", "HOLE")
                s.mesh = None
                continue

        elif s.op == "revolve":
            path = p.open_path or p.outer
            if not path:
                doc.err(s.line, f"profile '{src}' is empty", "give it a shape", "EMPTY"); continue
            ang = float(s.args.get("angle", 360))
            seg = int(s.args.get("segments", 64))
            if p.holes:
                doc.warn(s.line, f"revolve ignores the {len(p.holes)} hole(s) in profile '{src}'",
                         "shape the cavity into the path itself: go up the outside, over the rim, "
                         "down the inside, then back to the axis", "KIND")
            s.mesh = revolve(path, ang, seg)

        elif s.op == "loft":
            to = s.args.get("to")
            if not to or to not in doc.profiles:
                alt = suggest(str(to), list(doc.profiles)) if to else None
                doc.err(s.line, f"loft needs a valid 'to' profile" + (f" ('{to}' is unknown)" if to else ""),
                        f"did you mean 'to={alt}'?" if alt
                        else f"solid {name} loft from={src} to=<profile> h=20", "REF"); continue
            q = doc.profiles[to]
            if q.bad:
                doc.info(s.line, f"solid '{name}' was not built: profile '{to}' has errors above",
                         "fix the profile and this solid will build itself")
                continue
            if p.kind == "path" or q.kind == "path":
                doc.err(s.line, "loft needs two closed profiles",
                        "use circle/rect/poly for both ends", "KIND"); continue
            h = float(s.args.get("h", s.args.get("height", 0)))
            if h <= 0:
                doc.err(s.line, f"loft height must be positive (got {h})",
                        f"solid {name} loft from={src} to={to} h=20", "DIM"); continue
            if p.holes or q.holes:
                doc.warn(s.line, "loft ignores holes in its profiles",
                         "loft the outside, then subtract nothing - or use extrude for hollow parts",
                         "KIND")
            s.mesh = loft(p.outer, q.outer, h, max(1, int(s.args.get("steps", 12))))

    # implicit assembly: if nobody wrote one, every solid sits at the origin
    if not doc.parts and doc.solids:
        for nm in doc.solids:
            p = Part(nm, doc.solids[nm].line); doc.parts.append(p)
        doc.info(0, f"no assembly block - placed all {len(doc.parts)} solid(s) at the origin",
                 "add an 'assembly' block with 'part <name> at=(x,y,z)' to position them")

    for p in doc.parts:
        s = doc.solids.get(p.solid)
        if s is None:
            alt = suggest(p.solid, list(doc.solids))
            doc.err(p.line, f"part refers to unknown solid '{p.solid}'",
                    f"did you mean 'part {alt} ...'?" if alt
                    else f"defined solids: {', '.join(doc.solids) or '(none)'}", "REF"); continue
        if s.mesh is None: continue
        m = s.mesh.transform(p.at, p.rot, p.scale)
        if p.mirror:
            ax = {"x": 0, "y": 1, "z": 2}.get(str(p.mirror).lower())
            if ax is None:
                doc.err(p.line, f"mirror axis '{p.mirror}' is not x, y or z", "mirror=x", "ARG")
            else:
                mm = Mesh(m.name)
                for a, b, c in m.tris:
                    f = lambda v: tuple(-v[i] if i == ax else v[i] for i in range(3))
                    mm.add(f(a), f(c), f(b))          # flip winding to keep normals out
                m = mm
        m.name = p.solid
        p.mesh = m
        cs = getattr(s, "colorspec", None)
        if cs:
            p.color = parse_color(cs if isinstance(cs, str) else "#a0a5af")
    return doc


def merged(doc):
    m = Mesh(doc.name)
    for p in doc.parts:
        if p.mesh: m.tris.extend(p.mesh.tris)
    return m


# ---------------------------------------------------------------- lint
def lint_profiles(doc):
    """2D checks. These run before anything is built, so a broken outline is
    reported once, where it came from, instead of cascading into a pile of
    confusing mesh errors downstream."""
    for nm, p in doc.profiles.items():
        before = len(doc.errors)
        ring = p.outer or p.open_path or []
        if not ring:
            doc.warn(p.line, f"profile '{nm}' is empty and was never built",
                     "give it a shape, or delete the profile", "EMPTY")
            p.bad = True
            continue
        if p.outer:
            if abs(poly_area(p.outer)) < 1e-6:
                doc.err(p.line, f"profile '{nm}' has zero area",
                        "check that the dimensions are non-zero", "DIM")
            hit = self_intersects(p.outer)
            if hit:
                doc.err(p.line, f"profile '{nm}' crosses itself (edge {hit[0]} cuts edge {hit[1]})",
                        "reorder the poly points so the outline runs once around without "
                        "doubling back", "SELFX")
            for i, h in enumerate(p.holes):
                outside = [v for v in h if not point_in_poly(v, p.outer)]
                if outside:
                    whole = len(outside) == len(h)
                    doc.err(p.line,
                            f"hole {i + 1} of profile '{nm}' "
                            + ("lies completely outside" if whole else
                               f"pokes outside the outline at {len(outside)} of {len(h)} points,"
                               " so it is a notch rather than a hole")
                            + (" the outer shape" if whole else ""),
                            "move it inside with at=(x,y), make it smaller, or enlarge the "
                            "outer shape. A hole must be fully enclosed - to cut a notch in "
                            "the edge, draw the outline with poly instead", "HOLE")
                    continue
                gap = poly_gap(p.outer, h)
                if gap < TOKENS["wall.thin"] - 0.06:
                    doc.err(p.line, f"wall between hole {i + 1} and the outside of '{nm}' is "
                                    f"{gap:.2f}{doc.units} - too thin to print",
                            f"keep at least ${{wall.thin}} = {TOKENS['wall.thin']}{doc.units}; "
                            f"shrink the hole or grow the outer shape", "WALL")
                elif gap < TOKENS["wall.std"] - 0.06:
                    doc.warn(p.line, f"wall between hole {i + 1} and the outside of '{nm}' is "
                                     f"{gap:.2f}{doc.units} - printable but fragile",
                             f"aim for $wall.std = {TOKENS['wall.std']}{doc.units}", "WALL")
                for j, h2 in enumerate(p.holes[i + 1:], i + 2):
                    if poly_gap(h, h2) < TOKENS["wall.thin"] - 0.06:
                        doc.warn(p.line, f"holes {i + 1} and {j} of '{nm}' nearly merge",
                                 "space them at least $wall.std apart", "WALL")
        if p.open_path:
            if len(p.open_path) < 2:
                doc.err(p.line, f"path '{nm}' needs at least 2 points", "add points", "DIM")
            elif all(abs(x) < 1e-9 for x, _ in p.open_path):
                doc.err(p.line, f"path '{nm}' lies entirely on the axis, so it sweeps nothing",
                        "profile x is the radius - give the points a positive x", "DIM")
        p.bad = len(doc.errors) > before
    return doc


def lint_build(doc):
    """3D and assembly checks, once the meshes exist."""
    # --- unused declarations
    used_p = {s.args.get("from") for s in doc.solids.values()} | \
             {s.args.get("to") for s in doc.solids.values()}
    for nm, p in doc.profiles.items():
        if nm not in used_p:
            doc.warn(p.line, f"profile '{nm}' is never used by a solid",
                     f"add: solid {nm}_body from={nm} extrude h=10", "UNUSED")
    used_s = {p.solid for p in doc.parts}
    for nm, s in doc.solids.items():
        if nm not in used_s:
            doc.warn(s.line, f"solid '{nm}' is never placed in the assembly",
                     f"add: part {nm} at=(0,0,0)", "UNUSED")

    # --- per part geometry
    boxes = []
    for p in doc.parts:
        if not p.mesh: continue
        m = p.mesh
        ok, open_e, bad = m.manifold()
        vol = m.volume()
        lo, hi = m.bbox()
        boxes.append((p, lo, hi))
        if not ok:
            doc.err(p.line, f"part '{p.solid}' is not a closed solid "
                            f"({open_e} open edge(s), {bad} inconsistent)",
                    "this is a geometry bug, not your mistake - simplify the profile "
                    "(fewer poly points, no self-crossing) and report it", "MANIFOLD")
        if vol <= 0:
            doc.err(p.line, f"part '{p.solid}' has non-positive volume ({vol:.1f})",
                    "the surface is inside out; check that poly points run "
                    "counter-clockwise", "VOLUME")
        elif vol < 20:
            doc.warn(p.line, f"part '{p.solid}' is only {vol:.1f} {doc.units}^3 - "
                             "smaller than a grain of rice",
                     "check your units: these dimensions are millimetres", "SCALE")
        size = tuple(hi[i] - lo[i] for i in range(3))
        if min(size) < TOKENS["nozzle"] * 2:
            thin = "XYZ"[size.index(min(size))]
            doc.warn(p.line, f"part '{p.solid}' is {min(size):.2f}{doc.units} thin in {thin}",
                     f"a 0.4{doc.units} nozzle cannot print below "
                     f"{TOKENS['nozzle'] * 2}{doc.units}", "WALL")

    # --- assembly level
    for i in range(len(boxes)):
        pi, lo1, hi1 = boxes[i]
        for j in range(i + 1, len(boxes)):
            pj, lo2, hi2 = boxes[j]
            ov = [min(hi1[k], hi2[k]) - max(lo1[k], lo2[k]) for k in range(3)]
            if all(o > 0.05 for o in ov):
                v = ov[0] * ov[1] * ov[2]
                doc.warn(pi.line, f"parts '{pi.solid}' and '{pj.solid}' interpenetrate "
                                  f"(~{v:.0f} {doc.units}^3 of shared space)",
                         "parts are kept separate, they are not fused. Either move one "
                         f"by {min(ov):.2f}{doc.units}, or build the shape as a single "
                         "profile with holes so the cavity is real geometry", "OVERLAP")
    if len(boxes) > 1:
        for p, lo, hi in boxes:
            if lo[2] > 0.05:
                rests = any(q is not p
                            and min(hi[0], hi2[0]) > max(lo[0], lo2[0]) - 0.5
                            and min(hi[1], hi2[1]) > max(lo[1], lo2[1]) - 0.5
                            and hi2[2] > lo[2] - 0.5 and lo2[2] < hi[2] + 0.5
                            for q, lo2, hi2 in boxes)
                if not rests:
                    doc.warn(p.line, f"part '{p.solid}' floats {lo[2]:.1f}{doc.units} above z=0 "
                                     "with nothing under it",
                             f"lower it to at=(x,y,0) or set at z={0:.0f}, "
                             "or add the part it should sit on", "FLOAT")

    # --- whole design
    for p, lo, hi in boxes:
        size = [hi[i] - lo[i] for i in range(3)]
        over = [("XYZ"[i], size[i], BUILD_VOLUME[i]) for i in range(3)
                if size[i] > BUILD_VOLUME[i]]
        if over:
            ax, got, lim = over[0]
            doc.warn(p.line, f"part '{p.solid}' is {got:.0f}{doc.units} in {ax}, over a "
                             f"typical {lim:.0f}{doc.units} print bed",
                     "scale it down, or split this part in two and join them", "BUILD")
    m = merged(doc)
    if m.tris:
        lo, hi = m.bbox()
        size = [hi[i] - lo[i] for i in range(3)]
        if max(size) > 0 and max(size) < 5:
            doc.warn(0, f"the whole design is only {max(size):.1f}{doc.units} across",
                     "dimensions are in millimetres - a mug is about 95mm tall, "
                     "a phone 160mm", "SCALE")
    return doc


# ---------------------------------------------------------------- ascii views
SHADE = ":-=+*oO#%@"   # any material shows at least ":", so it can never
#                        be mistaken for the "." that means empty space
VIEWS = {   # name -> (u axis, v axis, depth axis, u label, v label)
    "FRONT": (0, 2, 1, "X", "Z"),
    "SIDE":  (1, 2, 0, "Y", "Z"),
    "TOP":   (0, 1, 2, "X", "Y"),
}


def rasterize(mesh, view, W=40, H=20):
    """Shoot one ray per character cell through the solid and add up how much
    material it actually passes through. Not the silhouette depth - the real
    material - so a hollow part reads as hollow even though nobody can see it.
    Returns (thickness grid, hit grid, bounds)."""
    ui, vi, di, _, _ = VIEWS[view]
    lo, hi = mesh.bbox()
    du, dv = hi[ui] - lo[ui], hi[vi] - lo[vi]
    if du <= 0 and dv <= 0:
        return None, None, None
    # a character cell is about twice as tall as it is wide
    scale = min((W - 1) / du if du > 1e-9 else 1e9,
                2.0 * (H - 1) / dv if dv > 1e-9 else 1e9)
    cu = (W - 1 - du * scale) / 2.0
    cv = (H - 1 - dv * scale / 2.0) / 2.0
    hits = [[None] * W for _ in range(H)]

    def px(p):
        return (cu + (p[ui] - lo[ui]) * scale,
                cv + (hi[vi] - p[vi]) * scale / 2.0, p[di])

    for tri in mesh.tris:
        a, b, c = (px(p) for p in tri)
        det = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
        if abs(det) < 1e-12:
            continue
        x0 = max(0, int(math.floor(min(a[0], b[0], c[0]))))
        x1 = min(W - 1, int(math.ceil(max(a[0], b[0], c[0]))))
        y0 = max(0, int(math.floor(min(a[1], b[1], c[1]))))
        y1 = min(H - 1, int(math.ceil(max(a[1], b[1], c[1]))))
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                w1 = ((x - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (y - a[1])) / det
                w2 = ((b[0] - a[0]) * (y - a[1]) - (x - a[0]) * (b[1] - a[1])) / det
                if w1 < 0.0 or w2 < 0.0 or w1 + w2 > 1.0:
                    continue
                d = a[2] + w1 * (b[2] - a[2]) + w2 * (c[2] - a[2])
                if hits[y][x] is None:
                    hits[y][x] = [d]
                else:
                    hits[y][x].append(d)

    thick = [[0.0] * W for _ in range(H)]
    hit = [[False] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            hs = hits[y][x]
            if not hs:
                continue
            hit[y][x] = True
            hs.sort()
            merged_d = [hs[0]]
            for d in hs[1:]:
                if d - merged_d[-1] > 1e-6:
                    merged_d.append(d)
            t = 0.0
            for i in range(0, len(merged_d) - 1, 2):
                t += merged_d[i + 1] - merged_d[i]
            thick[y][x] = t
    return thick, hit, (lo, hi)


def ascii_view(mesh, view, W=40, H=20, units="mm"):
    thick, hit, bounds = rasterize(mesh, view, W, H)
    ui, vi, di, ul, vl = VIEWS[view]
    if thick is None:
        return [f"{view}: (empty)"]
    lo, hi = bounds
    peak = max((t for row in thick for t in row), default=0.0)
    out = [f"{view}  ({ul} right, {vl} up, looking along {'XYZ'[di]})"]
    for y in range(H):
        row = ""
        for x in range(W):
            if not hit[y][x]:
                row += "."
            elif peak <= 1e-9:
                row += "#"
            else:
                k = thick[y][x] / peak
                row += SHADE[min(len(SHADE) - 1, max(0, int(k * (len(SHADE) - 1) + 0.5)))]
        out.append("  |" + row + "|")
    out.append(f"  +{'-' * W}+")
    out.append(f"  {ul}: {lo[ui]:.1f} .. {hi[ui]:.1f}{units}    "
               f"{vl}: {lo[vi]:.1f} .. {hi[vi]:.1f}{units}")
    out.append(f"  material along the ray:  '.' none   "
               f"':' thin   '#' mid   '@' {peak:.1f}{units}")
    return out


def ascii_profile(prof, W=40, H=18, units="mm"):
    ring = prof.outer or prof.open_path or []
    if not ring: return [f"profile {prof.name}: (empty)"]
    xs = [p[0] for p in ring] + [p[0] for h in prof.holes for p in h]
    ys = [p[1] for p in ring] + [p[1] for h in prof.holes for p in h]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    dx, dy = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    scale = min((W - 1) / dx, (H - 1) * 2.0 / dy)
    g = [[" "] * W for _ in range(H)]

    def cell(p):
        return (int(round((p[0] - x0) * scale)), int(round((y1 - p[1]) * scale / 2.0)))

    def stroke(a, b, ch):
        (ax, ay), (bx, by) = cell(a), cell(b)
        n = max(abs(bx - ax), abs(by - ay), 1)
        for i in range(n + 1):
            x = ax + (bx - ax) * i // n; y = ay + (by - ay) * i // n
            if 0 <= x < W and 0 <= y < H: g[y][x] = ch

    if prof.outer:
        for y in range(H):
            for x in range(W):
                p = (x0 + x / scale, y1 - y * 2.0 / scale)
                if point_in_poly(p, prof.outer) and not any(point_in_poly(p, h) for h in prof.holes):
                    g[y][x] = ":"
        for i in range(len(prof.outer)):
            stroke(prof.outer[i], prof.outer[(i + 1) % len(prof.outer)], "#")
        for h in prof.holes:
            for i in range(len(h)):
                stroke(h[i], h[(i + 1) % len(h)], "o")
    else:
        for i in range(len(ring) - 1):
            stroke(ring[i], ring[i + 1], "#")
        stroke(ring[0], ring[0], "A"); stroke(ring[-1], ring[-1], "B")

    rows = ["".join(r) for r in g]
    while rows and not rows[-1].strip(): rows.pop()          # trim the padding
    while rows and not rows[0].strip(): rows.pop(0)
    out = [f"profile {prof.name}  ({prof.kind}"
           + (f", {len(prof.holes)} hole(s)" if prof.holes else "") + ")"]
    out += ["  |" + r + "|" for r in rows]
    out.append(f"  +{'-' * W}+")
    if prof.outer:
        out.append(f"  # outline   : interior   o hole      "
                   f"area {abs(poly_area(prof.outer)):.0f}{units}^2")
    else:
        out.append(f"  A = first point {ring[0]}, B = last point {ring[-1]} "
                   f"(x is radius, y is height; the axis is at x=0)")
    out.append(f"  extent X {x0:.1f}..{x1:.1f}{units}   Y {y0:.1f}..{y1:.1f}{units}")
    return out


# ---------------------------------------------------------------- report
def stats(doc):
    m = merged(doc)
    lo, hi = m.bbox()
    open_e, bad = m.manifold()[1:]
    return {
        "name": doc.name, "units": doc.units,
        "profiles": len(doc.profiles), "solids": len(doc.solids), "parts": len(doc.parts),
        "triangles": len(m.tris),
        "volume_mm3": round(m.volume(), 2), "surface_mm2": round(m.area(), 2),
        "bbox": [round(hi[i] - lo[i], 2) for i in range(3)],
        "bbox_min": [round(v, 2) for v in lo], "bbox_max": [round(v, 2) for v in hi],
        "watertight": all(p.mesh.manifold()[0] for p in doc.parts if p.mesh) and bool(doc.parts),
        "merged_open_edges": open_e, "merged_bad_winding": bad,
        "parts_touch": open_e > 0 or bad > 0,
        "errors": len(doc.errors), "warnings": len(doc.warnings),
        "per_part": [{"name": p.solid,
                      "volume_mm3": round(p.mesh.volume(), 2),
                      "bbox": [round(p.mesh.bbox()[1][i] - p.mesh.bbox()[0][i], 2) for i in range(3)],
                      "at": list(p.at), "watertight": p.mesh.manifold()[0],
                      "triangles": len(p.mesh.tris)}
                     for p in doc.parts if p.mesh],
    }


REPAIR_PROTOCOL = """\
HOW TO REPLY TO THIS REPORT
  1. Fix ERRORS first, exactly as the FIX lines say. Change nothing else.
  2. Then judge the WARNINGS. Each one is a real risk, but you may accept one
     on purpose - if you do, say why in a `note` line.
  3. Compare the VIEWS against what you intended. The views are the only way
     you can see this object. Ask yourself, for each view: is the silhouette
     the right shape, is the proportion right, is anything missing?
  4. Re-send the WHOLE design inside <<<DESIGN ... >>>. Never send a fragment
     or a patch - the compiler reads the full document every time.
  5. Change one thing per round. If two rounds in a row make it worse, go back
     to the last version that compiled and take a smaller step."""


def report(doc, views=True, profiles=True, width=40):
    S = stats(doc)
    L = []
    bar = "=" * 66
    L.append(bar)
    L.append(f"DESIGNBRIDGE {VERSION}   design {doc.name!r}   units {doc.units}")
    ok = not doc.errors
    L.append(f"STATUS: {'PASS' if ok else 'FAIL'}   "
             f"{len(doc.errors)} error(s), {len(doc.warnings)} warning(s)")
    L.append(bar)

    if doc.repairs:
        L.append("")
        L.append("INPUT REPAIRS -- your output was not clean text, we fixed it:")
        for r in dict.fromkeys(doc.repairs):
            L.append(f"  - {r}")
        L.append("  Send the design inside <<<DESIGN ... >>> with no markdown fence "
                 "to avoid this.")

    if doc.errors:
        L.append("")
        L.append(f"ERRORS -- {len(doc.errors)} blocking problem(s). "
                 "Affected solids were not built.")
        for d in sorted(doc.errors, key=lambda d: d.line):
            L.append(repr(d))
    if doc.warnings:
        L.append("")
        L.append(f"WARNINGS -- {len(doc.warnings)} thing(s) that compiled but look wrong.")
        for d in sorted(doc.warnings, key=lambda d: d.line):
            L.append(repr(d))
    infos = [d for d in doc.diags if d.sev == "info"]
    if infos:
        L.append("")
        L.append("NOTES")
        for d in infos: L.append(repr(d))

    L.append("")
    L.append("MEASUREMENTS")
    L.append(f"  profiles {S['profiles']}   solids {S['solids']}   parts {S['parts']}"
             f"   triangles {S['triangles']}")
    if S["triangles"]:
        b = S["bbox"]
        L.append(f"  bounding box   {b[0]:.1f} x {b[1]:.1f} x {b[2]:.1f} {doc.units}"
                 f"   (X x Y x Z)")
        L.append(f"  volume         {S['volume_mm3'] / 1000.0:.2f} cm^3"
                 f"   ({S['volume_mm3']:.0f} {doc.units}^3)")
        L.append(f"  surface area   {S['surface_mm2'] / 100.0:.2f} cm^2")
        L.append(f"  every part closed   {'yes' if S['watertight'] else 'NO - see errors'}")
        if S["parts_touch"] and len(S["per_part"]) > 1:
            L.append("  parts share coincident faces, which is normal for an assembly")
        if len(S["per_part"]) > 1:
            L.append("  per part:")
            for q in S["per_part"]:
                bb = q["bbox"]
                L.append(f"    {q['name']:<14} {bb[0]:6.1f} x {bb[1]:6.1f} x {bb[2]:6.1f}"
                         f"   {q['volume_mm3'] / 1000.0:7.2f} cm^3"
                         f"   at ({q['at'][0]:.0f},{q['at'][1]:.0f},{q['at'][2]:.0f})"
                         f"   {'closed' if q['watertight'] else 'OPEN'}")

    if profiles and doc.profiles:
        L.append("")
        L.append("PROFILES -- the 2D shapes your 3D solids are swept from")
        for nm, p in doc.profiles.items():
            if p.outer or p.open_path:
                L.append("")
                L += ["  " + x for x in ascii_profile(p, W=width, units=doc.units)]

    if views and S["triangles"]:
        L.append("")
        L.append("VIEWS -- three orthographic views of the whole assembly. Each character")
        L.append("is a ray fired through the solid, shaded by how much MATERIAL it passed")
        L.append("through: '.' none, ':' thin, '#' middling, '@' the most. So a hollow part")
        L.append("reads as a bright rim around a dim middle.")
        m = merged(doc)
        for v in ("FRONT", "SIDE", "TOP"):
            L.append("")
            L += ["  " + x for x in ascii_view(m, v, W=width, H=18, units=doc.units)]

    if doc.notes:
        L.append("")
        L.append("YOUR NOTES")
        for n in doc.notes: L.append(f"  - {n}")

    L.append("")
    L.append(bar)
    L.append(REPAIR_PROTOCOL if (doc.errors or doc.warnings) else
             "STATUS PASS. The design compiled to a closed solid. Check the VIEWS "
             "against your intent;\nif the silhouettes look right, this design is "
             "ready to export to STL.")
    L.append(bar)
    return "\n".join(L)


def diff_stats(old, new):
    L = ["CHANGE SINCE LAST ROUND"]
    for k, lbl in (("errors", "errors"), ("warnings", "warnings"),
                   ("parts", "parts"), ("triangles", "triangles")):
        if old.get(k) != new.get(k):
            L.append(f"  {lbl}: {old.get(k)} -> {new.get(k)}")
    ob, nb = old.get("bbox", [0, 0, 0]), new.get("bbox", [0, 0, 0])
    if ob != nb:
        L.append(f"  bounding box: {ob[0]}x{ob[1]}x{ob[2]} -> {nb[0]}x{nb[1]}x{nb[2]}")
    ov, nv = old.get("volume_mm3", 0), new.get("volume_mm3", 0)
    if ov != nv:
        pct = (nv - ov) / ov * 100 if ov else 0
        L.append(f"  volume: {ov / 1000:.2f} -> {nv / 1000:.2f} cm^3 ({pct:+.0f}%)")
    if new.get("errors", 0) > old.get("errors", 0):
        L.append("  WARNING: you introduced new errors. Revert to the last version "
                 "that passed and take a smaller step.")
    return "\n".join(L) if len(L) > 1 else "CHANGE SINCE LAST ROUND\n  (nothing measurable changed)"


# ---------------------------------------------------------------- export
def write_stl(mesh, path, binary=True):
    tris = mesh.tris
    if binary:
        with open(path, "wb") as f:
            f.write(b"DesignBridge " + VERSION.encode() + b" " * (80 - 13 - len(VERSION)))
            f.write(struct.pack("<I", len(tris)))
            for a, b, c in tris:
                u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
                w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
                n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
                L = math.sqrt(sum(t * t for t in n)) or 1.0
                f.write(struct.pack("<3f", *[t / L for t in n]))
                for v in (a, b, c): f.write(struct.pack("<3f", *v))
                f.write(b"\0\0")
    else:
        with open(path, "w") as f:
            f.write(f"solid {mesh.name}\n")
            for a, b, c in tris:
                f.write(" facet normal 0 0 0\n  outer loop\n")
                for v in (a, b, c): f.write(f"   vertex {v[0]:.5f} {v[1]:.5f} {v[2]:.5f}\n")
                f.write("  endloop\n endfacet\n")
            f.write(f"endsolid {mesh.name}\n")


def write_obj(doc, path):
    with open(path, "w") as f:
        f.write(f"# DesignBridge {VERSION} - {doc.name}\n")
        n = 1
        for p in doc.parts:
            if not p.mesh: continue
            f.write(f"o {p.solid}\n")
            for a, b, c in p.mesh.tris:
                for v in (a, b, c): f.write(f"v {v[0]:.5f} {v[1]:.5f} {v[2]:.5f}\n")
                f.write(f"f {n} {n + 1} {n + 2}\n"); n += 3


def write_svg(doc, path):
    """All 2D profiles, laid out in a row. Useful as laser-cut source and as a
    human sanity check on what the model actually drew."""
    boxes, x = [], 0.0
    for nm, p in doc.profiles.items():
        ring = p.outer or p.open_path or []
        if not ring: continue
        xs = [q[0] for q in ring]; ys = [q[1] for q in ring]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        boxes.append((nm, p, x - min(xs), -min(ys), w, h)); x += w + 10
    H = max([b[5] for b in boxes], default=10) + 20
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{x + 10:.0f}mm" '
           f'height="{H:.0f}mm" viewBox="0 0 {x + 10:.1f} {H:.1f}">',
           '<g transform="scale(1,-1) translate(0,%.1f)" fill="none" '
           'stroke="#000" stroke-width="0.4">' % (-H + 10)]
    for nm, p, ox, oy, w, h in boxes:
        d = lambda ring, close: "M " + " L ".join(f"{q[0] + ox:.3f},{q[1] + oy:.3f}"
                                                  for q in ring) + (" Z" if close else "")
        if p.outer:
            out.append(f'<path d="{d(p.outer, True)}" fill="#eee"/>')
            for hh in p.holes:
                out.append(f'<path d="{d(hh, True)}" fill="#fff"/>')
        else:
            out.append(f'<path d="{d(p.open_path, False)}" stroke="#c00"/>')
    out += ["</g>", "</svg>"]
    open(path, "w").write("\n".join(out))


# ---------------------------------------------------------------- grammar doc
GRAMMAR = """\
THE LANGUAGE (complete - there is nothing else)

Every design is a plain-text document. One statement per line. Two-space
indent puts a shape inside the profile above it. '#' starts a comment.

  design "<name>" units=mm          first line, once

  profile <name>                    a 2D shape, the source of all 3D
    <shape>                           exactly one outer shape
    hole <shape>                      zero or more holes inside it

  solid <name> from=<profile> extrude h=<mm> [taper=<deg>] [twist=<deg>]
  solid <name> from=<profile> revolve [angle=<deg>] [segments=<n>]
  solid <name> loft from=<profA> to=<profB> h=<mm>

  assembly                          optional; omit it and every solid sits at 0
    part <solid> [at=(x,y,z)] [rot=(rx,ry,rz)] [scale=<n>] [mirror=x|y|z]

  note "<text>"                     record a decision you made on purpose

SHAPES usable as an outer shape or as a hole
  circle  r=<mm>           or d=<mm>
  ellipse rx=<mm> ry=<mm>
  rect    w=<mm> h=<mm>    [r=<corner radius>]
  slot    w=<mm> h=<mm>              fully rounded ends
  ngon    n=<sides> r=<mm>
  poly    (x,y) (x,y) (x,y) ...      closed outline, counter-clockwise
  path    (x,y) (x,y) ...            OPEN polyline, only for revolve
  all shapes also take  at=(x,y)  rot=<deg>  segments=<n>

VALUES
  numbers are millimetres          40        40mm      2.5
  design tokens                   $wall.std    $m3.hole    $clear.slip
  arithmetic                      40-$wall.std*2        (0,0) (40-2.4,0)

THE THREE OPERATIONS, AND WHEN EACH ONE IS RIGHT
  extrude   the shape is constant along one straight direction.
            brackets, plates, enclosures, keycaps, gears, signs, boxes.
            taper gives draft angle; twist gives a spiral column.
  revolve   the object has one axis of rotational symmetry.
            mugs, vases, bottles, bowls, knobs, wheels, lampshades, lenses.
            angle<360 leaves a wedge, useful for cutaway views.
  loft      the cross-section changes from one shape to another.
            adapters, funnels, tapered handles, round-to-square transitions.

HOLLOW OBJECTS - the single most important technique
  There is no 3D boolean subtract. You get hollow parts by putting the void
  into the 2D profile, which is more reliable and always watertight.

  For extrude: give the profile a hole.
      profile tube
        circle r=20
        hole circle r=20-$wall.std      # a 2.4mm-walled tube
      solid body from=tube extrude h=60

  For revolve: walk the pen around the whole cross-section. A revolve always
  spins about the vertical axis Z; in its profile x is the RADIUS and y is the
  HEIGHT, and x=0 is the spin axis. To lay the result on its side, rotate the
  part in the assembly: part body rot=(0,90,0).
  Go up the outside, across the rim, down the inside, then back to the axis:
      profile mug_section
        path (0,0) (40,0) (40,95) (40-$wall.std,95) (40-$wall.std,$wall.std*2) (0,$wall.std*2)
      solid body from=mug_section revolve segments=72
  That is a mug: 80mm across, 95mm tall, 2.4mm wall, 4.8mm floor.

ASSEMBLY IS PLACEMENT, NOT FUSION
  Parts are positioned next to each other, never merged. Two parts that
  occupy the same space are reported as an error of intent, not fused into
  one shape. So: everything that must be ONE piece of material must come
  from ONE profile. Use the assembly for things that really are separate
  pieces - a lid and a box, a handle pinned to a body, a repeated boss."""


TIER_LOW = """\
KEEP IT SIMPLE. Use only: circle, rect, hole, path, extrude, revolve.
Avoid loft, taper and twist. Prefer two simple parts over one clever profile.
Give every number as a plain millimetre value."""


REFERENCE = """\
DIMENSION ANCHORS - use these so your sizes are believable
  Hand: palm width 85, comfortable grip diameter 30-45, finger thickness 18.
  Mug: 80 dia x 95 tall, handle opening 35 x 55, wall 3 when printed.
  Phone 160 x 75 x 8.  Credit card 85.6 x 54.  AA cell 14.5 dia x 50.5.
  Pen 9 dia x 140.  Water bottle 70 dia x 250.  Mouse 115 x 62 x 38.
  USB-C cutout 9.0 x 3.2.  M3 screw: 3.4 hole, 6.0 head.  Wall plate 70 x 115.
  Chair seat 450 high, 400 deep.  Desk 730 high.  Door handle 1000 high.
  Print bed 220 x 220 x 250 - anything larger must be split.

  State the overall size in millimetres BEFORE writing any line of the design.
  An object that comes back 9mm tall when you wanted 95 is the most common
  failure of all, and the MEASUREMENTS block will catch it."""


THINKING = """\
HOW TO THINK ABOUT A SHAPE YOU CANNOT SEE

You have no eyes, so do not try to imagine a picture. Work down this ladder
instead. Each rung is a question with a short written answer, and the answers
produce the design almost mechanically.

  1. FUNCTION. What must this object do, hold, resist or fit? Name the one
     surface that does the real work - the rim you drink from, the face that
     bolts to the wall, the bore the shaft passes through. Everything else is
     in service of it.

  2. GENERATING VIEW. Of the three views, which single 2D outline defines the
     object? Say it out loud: "seen from the front, its cross-section is ...".
     If you cannot finish that sentence, you do not understand the shape yet
     and you must break it into smaller parts that you can.

  3. CONSTRUCTION STRATEGY. Match the answer above to one operation:
       the same cross-section all the way along  -> extrude
       spun around one axis                      -> revolve
       one shape blending into another           -> loft
       genuinely separate pieces                 -> several solids in assembly
     Decide this before you write any number.

  4. DIMENSION ANCHORS. Write the three overall dimensions in millimetres,
     anchored to something real from the reference list. Then derive the inner
     dimensions from the outer ones by subtracting a wall token, never by
     guessing both independently - that is how walls end up 0.3mm or 11mm.

  5. TOLERANCE AND WALL. Any two parts that must mate need a clearance token
     between them ($clear.slip for a lid, $clear.press for a pin). Any
     material that must survive needs at least $wall.std. Use the tokens; they
     encode what actually prints.

  6. VERIFY, DO NOT TRUST. Compile the design and read the report. You are
     reading three things: did it close into a solid, are the measurements the
     ones you intended, and do the three silhouettes match the sentence you
     wrote at rung 2.

READING THE ASCII VIEWS
  Three orthographic views. Each character is one ray fired through the solid,
  and the shade is how much MATERIAL that ray passed through - not how far
  away the surface is. '.' none, ':' thin, '#' middling, '@' the most.
  That one detail is what lets you see inside: a hollow part shows a bright
  rim of wall around a dim middle, because the ray through the middle crosses
  only two thin walls while the ray through the rim travels along them.

  FRONT is the elevation: check height against width, and the outline.
  SIDE shows depth: a part that looks identical in FRONT and SIDE is
       rotationally symmetric - right for a mug, wrong for a bracket.
  TOP is the footprint: check symmetry, and where the holes sit.

  Read them in this order:
    - How many separate masses do you count? It should equal your part count.
    - Is the widest point where you intended it?
    - Is the part hollow where it should be hollow? Look for the bright rim
      and dim centre. A uniformly bright mass means your cavity never got
      made - the most common silent failure there is.
    - Is anything floating, with nothing beneath it?
    - Compare the printed extents under each view against the three
      dimensions you committed to. Numbers beat impressions.

  The views cannot tell you about surface finish, colour or beauty. They can
  tell you whether you built the object you described. That is what they are
  for."""


FAILURES = """\
THE SIX FAILURES THAT ACTUALLY HAPPEN, AND WHAT CAUSES THEM
  "part is not a closed solid"      a poly whose points double back and cross,
                                    or an extrude pointed at an open path.
                                    Simplify the outline, keep it convex if
                                    you can, run the points once around.
  "wall ... too thin to print"      you chose the inner and outer size
                                    independently. Derive one from the other:
                                    hole circle r=20-$wall.std
  "parts interpenetrate"            you expected assembly to fuse or subtract.
                                    It does neither. Move the part, or rebuild
                                    the shape as one profile with holes.
  "whole design is only 9mm"        you thought in centimetres or in no unit
                                    at all. Everything is millimetres.
  TOP view is a solid disc          your revolve path never came back down the
                                    inside, so you made a plug, not a vessel.
  "floats above z=0"                an at=(x,y,z) with a z you did not intend,
                                    usually copied from another part."""



# ---------------------------------------------------------------- texture & model prompts
_OUTPUT_CONTRACT = """OUTPUT CONTRACT - follow exactly
  1. First, in at most four short lines, state your plan: what the asset is, its
     palette or parts, and its overall size.
  2. Then the source, alone, between the sentinels, no markdown fence:
        <<<DESIGN
        ...
        >>>
  3. Nothing after the closing sentinel. Send the WHOLE source every round."""


def prompt_texture(brief, runnable=False):
    P = []
    P.append("You are painting a small game TEXTURE - a tile like a Minecraft block face.\n"
             "You cannot see, so you will not write pixels. You name a stack of layers and\n"
             "a compiler paints them, then reports the palette and pattern back to you, and\n"
             "saves a real PNG the user can look at.")
    P.append("")
    P.append("THE BRIEF\n  " + brief.strip())
    P.append("")
    P.append("""HOW TO THINK ABOUT A TEXTURE
  1. MATERIAL. Name the real material (mossy stone, oak planks, rusted iron).
  2. BASE COLOUR. One fill colour it mostly reads as.
  3. VARIATION. What breaks up the flat fill? noise for grain, brick/planks for
     courses, scatter for specks (moss, rust), veins for ore.
  4. DEPTH. bevel or an outline to fake edges and read at 16px.
  5. MATERIAL MAP. metallic (0 = non-metal, 1 = metal) and roughness (0 = mirror,
     1 = matte) go on the texture line; the compiler derives the normal map.
  Keep the palette tight - 6 to 16 colours reads as clean pixel-art.""")
    P.append("")
    P.append("""THE LANGUAGE (one statement per line, '#' starts a comment)
  texture "<name>" size=16 [metallic=0..1] [roughness=0..1]
  then any number of layers, painted in order:
    fill     color=#rrggbb
    noise    dark=#rrggbbaa light=#rrggbbaa scale=<px> amount=0..1 [seed=N]
    gradient from=#.. to=#.. dir=v|h
    brick    color=#.. mortar=#.. w=<px> h=<px> line=1 [jitter=0..1]
    planks   color=#.. mortar=#.. h=<px> line=1
    checker  a=#.. b=#.. size=<px>
    stripes  a=#.. b=#.. size=<px> dir=v|h
    scatter  color=#.. amount=0..1 size=1 [seed=N]      # specks: moss, rust, stars
    veins    color=#.. scale=<px> amount=0..1 [seed=N]  # ore veins, cracks
    dither   color=#rrggbbaa amount=0..1
    bevel    light=#rrggbbaa dark=#rrggbbaa [width=1]    # fake raised edge
    outline  color=#.. [width=1]
  Colours are #rgb, #rrggbb, #rrggbbaa, or names (moss is not a name - use a hex).
  An 8-digit hex ends in an alpha byte: #4a7a3a80 is half-transparent.""")
    P.append("")
    P.append("""WORKED EXAMPLE - mossy cobblestone
<<<DESIGN
texture "mossy_cobble" size=16 roughness=0.9
fill color=#6b6b6b
noise dark=#55555570 light=#88888870 scale=3 amount=0.6
brick color=#6b6b6b mortar=#3f3f3f w=8 h=5 line=1 jitter=0.18
scatter color=#4a7a3a amount=0.22 seed=3
bevel light=#9a9a9a50 dark=#40404060
>>>
Stone base, noisy grain, cobble courses, moss specks, a soft bevel for depth.""")
    P.append("")
    P.append(_OUTPUT_CONTRACT)
    if runnable:
        P.append("")
        P.append("COMPILE IT YOURSELF\n  Write the source to tex.db, then run:\n"
                 "      python3 designbridge.py check tex.db\n"
                 "  Read the palette and preview, fix, repeat to STATUS: PASS, then:\n"
                 "      python3 designbridge.py build tex.db -o out/\n"
                 "  You get albedo PNG + material PNG + a recipe JSON.")
    else:
        P.append("")
        P.append("WHAT HAPPENS NEXT\n  Your texture is painted and you get back: the palette, a\n"
                 "  pattern preview, and a note of any errors. A real colour PNG is saved for\n"
                 "  the user. Adjust and resend the whole texture.")
    return "\n".join(P)


def prompt_model(brief, runnable=False):
    P = []
    P.append("You are building a blocky 3D MODEL - a voxel entity like a Minecraft mob or\n"
             "boss, made of coloured boxes grouped into bones that can be animated. You\n"
             "cannot see, so you name boxes and bones; a compiler builds the geometry,\n"
             "renders a real picture for the user, and describes it back to you in words\n"
             "and three ASCII views so you can correct the proportions.")
    P.append("")
    P.append("THE BRIEF\n  " + brief.strip())
    P.append("")
    P.append("""HOW TO THINK ABOUT A MODEL
  1. PARTS. List the body parts as boxes: body, head, two arms, two legs, tail...
  2. SIZES in pixels (16 px = 1 block). A humanoid mob is about 12 wide, 30 tall.
     Anchor to that: a boss is just bigger. Head ~8 cube, limbs ~4 thick.
  3. BONES and PIVOTS. Each movable part is a bone. Its pivot is the joint it
     rotates about - a shoulder pivot sits at the top of the arm, not its centre.
     Parent limbs to the body so moving the body moves everything.
  4. PLACEMENT. A box is two opposite corners in world space (from and to). Put
     the model's feet near y=0 and build upward; centre it on x=0.
  5. ANIMATION. A keyframe rotates or moves one bone at a time t (seconds). Two
     or three keys make a swing: rest -> extreme -> rest.""")
    P.append("")
    P.append("""THE LANGUAGE (two-space indent puts a box inside the bone above it)
  model "<name>"
  bone <name> [parent=<bone>] [pivot=(x,y,z)]
    box from=(x,y,z) to=(x,y,z) color=#rrggbb
    box ...                                      # a bone can hold several boxes
  animation "<name>" [loop=true|false]
    key <bone> t=<seconds> rot=(rx,ry,rz)        # degrees, about the pivot
    key <bone> t=<seconds> pos=(dx,dy,dz)        # offset in pixels
  pivot defaults to (0,0,0); parent defaults to none (a root bone).""")
    P.append("")
    P.append("""WORKED EXAMPLE - a simple boss torso with a swinging arm
<<<DESIGN
model "sentinel"
bone body pivot=(0,0,0)
  box from=(-7,0,-4) to=(7,16,4) color=#52603f
bone head parent=body pivot=(0,16,0)
  box from=(-5,16,-5) to=(5,24,5) color=#5f6f4a
bone arm_r parent=body pivot=(-7,15,0)
  box from=(-11,3,-3) to=(-7,15,3) color=#47553a
animation "slam" loop=false
  key arm_r t=0 rot=(0,0,0)
  key arm_r t=0.3 rot=(-120,0,0)
  key arm_r t=0.6 rot=(0,0,0)
>>>
The arm's pivot is its shoulder (-7,15,0), so it swings from the top, not the middle.""")
    P.append("")
    P.append("""READING THE VIEWS
  FRONT, SIDE, TOP are ray-cast silhouettes. Count the masses - they should equal
  your parts. Check height vs width against your sizes. A box in the wrong place
  shows up as a mass where you did not intend one. The colour PNG is for the user;
  the views are for you.""")
    P.append("")
    P.append(_OUTPUT_CONTRACT)
    if runnable:
        P.append("")
        P.append("COMPILE IT YOURSELF\n  Write the source to model.db, then run:\n"
                 "      python3 designbridge.py check model.db\n"
                 "  Read the bone tree and the three views, fix, repeat to PASS, then:\n"
                 "      python3 designbridge.py build model.db -o out/\n"
                 "  You get GLB (Blender/web/engine) + a box JSON (your engine) + OBJ.")
    else:
        P.append("")
        P.append("WHAT HAPPENS NEXT\n  Your model is built and you get back the bone tree, three\n"
                 "  ASCII views and any errors; a real colour PNG is saved for the user. Adjust\n"
                 "  and resend the whole model.")
    return "\n".join(P)


def prompt_for(kind, brief, runnable=False):
    if kind == "texture":
        return prompt_texture(brief, runnable)
    if kind == "model":
        return prompt_model(brief, runnable)
    return prompt(brief, runnable=runnable)


def prompt(brief, tier="high", runnable=False, tokens=True):
    P = []
    P.append("You are designing a real three-dimensional object. You cannot see, "
             "and you will not\nwrite any mesh, vertex list or coordinate soup. "
             "You will write a short declarative\ndesign document, and a compiler "
             "will build it, measure it, and describe it back to\nyou in words and "
             "ASCII silhouettes so you can correct it.")
    P.append("")
    P.append("THE BRIEF\n  " + brief.strip())
    P.append("")
    P.append(THINKING)
    P.append("")
    P.append(GRAMMAR)
    if tier == "low":
        P.append(""); P.append(TIER_LOW)
    if tokens:
        P.append("")
        P.append("DESIGN TOKENS - named values that encode real manufacturing limits.\n"
                 "Prefer a token to a number you invented.")
        groups = {}
        for k, v in TOKENS.items():
            groups.setdefault(k.split(".")[0], []).append((k, v))
        for g, items in groups.items():
            P.append("  " + "  ".join(f"${k}={v}" for k, v in items))
    P.append("")
    P.append(REFERENCE)
    P.append("")
    P.append(FAILURES)
    P.append("")
    P.append("WORKED EXAMPLE -- a mug, built the right way\n"
             "<<<DESIGN\n"
             'design "Coffee Mug" units=mm\n'
             "note \"80 dia x 95 tall, 2.4 wall: anchored to a real mug\"\n"
             "profile body_section\n"
             "  path (0,0) (40,0) (40,95) (37.6,95) (37.6,4.8) (0,4.8)\n"
             "profile handle_ring\n"
             "  slot w=18 h=58\n"
             "  hole slot w=18-$wall.std*2 h=58-$wall.std*2\n"
             "solid body from=body_section revolve segments=72\n"
             "solid handle from=handle_ring extrude h=10\n"
             "assembly\n"
             "  part body at=(0,0,0)\n"
             "  part handle at=(44,0,50) rot=(90,0,0)\n"
             ">>>\n"
             "The body is one revolve, so the cavity is real geometry and the part is\n"
             "watertight. The handle is a separate piece, because it is a separate\n"
             "piece. The numbers come from the anchor list, and the wall comes from a token.")
    P.append("")
    P.append("OUTPUT CONTRACT -- follow exactly\n"
             "  1. First, in at most six short lines of prose, answer rungs 1 to 4 of the\n"
             "     ladder: function, generating view, construction strategy, overall size.\n"
             "  2. Then the design, alone, between the sentinels, with no markdown fence,\n"
             "     no prose and no blank decoration inside:\n"
             "        <<<DESIGN\n"
             "        design \"...\" units=mm\n"
             "        ...\n"
             "        >>>\n"
             "  3. Nothing after the closing sentinel.\n"
             "  Send the WHOLE document every round, never a patch.")
    P.append("")
    P.append("SELF-CHECK before you send -- answer each one silently\n"
             "  - Is every profile used by a solid, and every solid placed by a part?\n"
             "  - Does each revolve path start and end at x=0, and come back down the\n"
             "    inside if the object is meant to be hollow?\n"
             "  - Did I derive every inner dimension from an outer one minus a wall token?\n"
             "  - Do my three overall dimensions match the size I committed to at rung 4?\n"
             "  - Could any two parts occupy the same space?")
    if runnable:
        P.append("")
        P.append("YOU CAN COMPILE THIS YOURSELF\n"
                 "  designbridge.py is available to you. Write your design to design.db,\n"
                 "  then run:\n"
                 "      python3 designbridge.py check design.db\n"
                 "  Read the report, fix what it says, and repeat until STATUS: PASS.\n"
                 "  Then run:\n"
                 "      python3 designbridge.py build design.db -o out/\n"
                 "  Do not show the user a design that has not reached PASS.\n"
                 "  Useful extras:  `guide` prints the full method,  `tokens` lists tokens,\n"
                 "  `check design.db --prev last.json` reports what changed since last round.")
    else:
        P.append("")
        P.append("WHAT HAPPENS NEXT\n"
                 "  Your design will be compiled and you will receive a report: errors with\n"
                 "  line numbers and fixes, measurements, and three ASCII silhouettes.\n"
                 "  " + REPAIR_PROTOCOL.splitlines()[0].lower())
    return "\n".join(P)


GUIDE = "\n\n".join(["DESIGNBRIDGE -- HOW TO DESIGN WITHOUT EYES\n" + "=" * 50,
                     THINKING, GRAMMAR, REFERENCE, FAILURES, REPAIR_PROTOCOL])


# ---------------------------------------------------------------- pipeline
def compile_text(text):
    doc = parse(text)
    lint_profiles(doc)
    build(doc)
    lint_build(doc)
    return doc



# ---------------------------------------------------------------- model report
def _model_mesh(doc):
    m = Mesh(doc.name)
    for name in doc.order:
        for box in doc.bones[name].boxes:
            for t in box_tris(box):
                m.add(*t)
    return m


def model_report(doc, width=40):
    L = []
    bar = "=" * 62
    ok = not doc.errors
    lo, hi = bbox(doc)
    size = [round(hi[i] - lo[i], 1) for i in range(3)]
    nboxes = sum(len(doc.bones[n].boxes) for n in doc.order)
    L.append(bar)
    L.append(f"MODEL '{doc.name}'   {len(doc.order)} bones, {nboxes} boxes, "
             f"{len(doc.anims)} animation(s)")
    L.append(f"STATUS: {'PASS' if ok else 'FAIL'}   {len(doc.errors)} error(s), "
             f"{len(doc.warnings)} warning(s)")
    L.append(bar)
    if doc.repairs:
        L.append("")
        L.append("INPUT REPAIRS:")
        for r in dict.fromkeys(doc.repairs): L.append("  - " + r)
    if doc.errors:
        L.append("")
        L.append("ERRORS")
        for s, line, msg, fix, code in doc.diags:
            if s != "error": continue
            L.append(f"  [ERROR] line {line}: {msg}")
            if fix: L.append(f"          FIX: {fix}")
    if doc.warnings:
        L.append("")
        L.append("WARNINGS")
        for s, line, msg, fix, code in doc.diags:
            if s != "warn": continue
            L.append(f"  [WARN] line {line}: {msg}")
            if fix: L.append(f"         FIX: {fix}")
    L.append("")
    L.append(f"SIZE   {size[0]} x {size[1]} x {size[2]} px   "
             f"({size[0]/16:.1f} x {size[1]/16:.1f} x {size[2]/16:.1f} blocks)")
    L.append("")
    L.append("BONE TREE  (pivot is the point a bone rotates about)")
    kids = {}
    for n in doc.order:
        kids.setdefault(doc.bones[n].parent, []).append(n)
    def walk(parent, depth):
        for n in kids.get(parent, []):
            b = doc.bones[n]
            L.append("  " + "  " * depth + f"{n}  pivot=({b.pivot[0]:.0f},{b.pivot[1]:.0f},"
                     f"{b.pivot[2]:.0f})  {len(b.boxes)} box(es)")
            walk(n, depth + 1)
    walk(None, 0)
    for n in doc.order:                     # orphans whose parent is unknown
        if doc.bones[n].parent not in kids and doc.bones[n].parent is not None \
                and doc.bones[n].parent not in doc.bones:
            L.append(f"  {n}  (parent '{doc.bones[n].parent}' missing)")
    if doc.anims:
        L.append("")
        L.append("ANIMATIONS")
        for an in doc.anims.values():
            span = max([k.t for k in an.keys], default=0)
            movers = sorted({k.bone for k in an.keys})
            L.append(f"  {an.name}  {'loop' if an.loop else 'once'}  {span:.2f}s  "
                     f"{len(an.keys)} keys on: {', '.join(movers)}")
    if not doc.errors and nboxes:
        m = _model_mesh(doc)
        L.append("")
        L.append("VIEWS - three orthographic silhouettes. Each char is a ray fired through")
        L.append("the model; shade = how much box it crossed. Check proportions and that")
        L.append("every part you named actually shows up where you intended.")
        for v in ("FRONT", "SIDE", "TOP"):
            L.append("")
            L += ["  " + x for x in ascii_view(m, v, W=width, H=16, units="px")]
        L.append("")
        L.append("A real colour PNG preview is saved alongside; the user can see it.")
    L.append("")
    L.append(bar)
    if ok:
        L.append("PASS. Check the bone tree, the views and the colour preview, then this\n"
                 "model exports to GLB (web/Blender/engine) + box JSON (STRATA) + OBJ.")
    else:
        L.append("Fix the errors and resend the whole model between <<<DESIGN and >>>.")
    L.append(bar)
    return "\n".join(L)


def export_mesh_glb(doc, path):
    """GLB for a prop: one mesh, per-part vertex colour from each solid's colour."""
    pos, nrm, col, idx = [], [], [], []
    n = 0
    for p in doc.parts:
        if not p.mesh:
            continue
        c = getattr(p, "color", None) or (160, 165, 175, 255)
        for a, b, cc in p.mesh.tris:
            u = (b[0]-a[0], b[1]-a[1], b[2]-a[2]); w = (cc[0]-a[0], cc[1]-a[1], cc[2]-a[2])
            nx, ny, nz = (u[1]*w[2]-u[2]*w[1], u[2]*w[0]-u[0]*w[2], u[0]*w[1]-u[1]*w[0])
            Ln = math.sqrt(nx*nx+ny*ny+nz*nz) or 1.0
            for v in (a, b, cc):
                pos.append(v); nrm.append((nx/Ln, ny/Ln, nz/Ln))
                col.append((c[0]/255, c[1]/255, c[2]/255, c[3]/255 if len(c) > 3 else 1.0))
            idx += [n, n+1, n+2]; n += 3
    if not pos:
        return 0
    parts, off, acc, bv = [], 0, [], []
    def view(data, tgt):
        nonlocal off
        pad = (4 - len(data) % 4) % 4
        bv.append({"buffer": 0, "byteOffset": off, "byteLength": len(data), "target": tgt})
        parts.append(data + b"\x00" * pad); off += len(data) + pad
        return len(bv) - 1
    pd = b"".join(struct.pack("<3f", *p) for p in pos)
    pv = view(pd, 34962); mn = [min(p[k] for p in pos) for k in range(3)]; mx = [max(p[k] for p in pos) for k in range(3)]
    pa = len(acc); acc.append({"bufferView": pv, "componentType": 5126, "count": len(pos), "type": "VEC3", "min": mn, "max": mx})
    nv = view(b"".join(struct.pack("<3f", *v) for v in nrm), 34962)
    na = len(acc); acc.append({"bufferView": nv, "componentType": 5126, "count": len(nrm), "type": "VEC3"})
    cv = view(b"".join(struct.pack("<4f", *c) for c in col), 34962)
    ca = len(acc); acc.append({"bufferView": cv, "componentType": 5126, "count": len(col), "type": "VEC4"})
    iv = view(b"".join(struct.pack("<I", v) for v in idx), 34963)
    ia = len(acc); acc.append({"bufferView": iv, "componentType": 5125, "count": len(idx), "type": "SCALAR"})
    buf = b"".join(parts)
    gltf = {"asset": {"version": "2.0", "generator": "DesignBridge"}, "scene": 0,
            "scenes": [{"nodes": [0]}], "nodes": [{"name": doc.name, "mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": pa, "NORMAL": na, "COLOR_0": ca},
                        "indices": ia, "material": 0}]}],
            "materials": [{"pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.85}}],
            "accessors": acc, "bufferViews": bv, "buffers": [{"byteLength": len(buf)}]}
    jt = json.dumps(gltf, separators=(",", ":")).encode(); jt += b" " * ((4 - len(jt) % 4) % 4)
    bp = buf + b"\x00" * ((4 - len(buf) % 4) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(jt) + 8 + len(bp))
    glb += struct.pack("<II", len(jt), 0x4E4F534A) + jt + struct.pack("<II", len(bp), 0x004E4942) + bp
    open(path, "wb").write(glb)
    return len(glb)


# ---------------------------------------------------------------- unified dispatch
def kind_of(text):
    """Sniff the first real statement to route a source to its track."""
    body = text
    if "<<<DESIGN" in body:
        body = body.split("<<<DESIGN", 1)[1].split(">>>", 1)[0]
    elif "```" in body:
        parts = body.split("```")
        if len(parts) >= 3:
            body = parts[1]
    for raw in body.splitlines():
        s = raw.strip().lower()
        if not s or s.startswith(("#", "//")):
            continue
        head = s.split()[0]
        if head == "texture":
            return "texture"
        if head == "model":
            return "model"
        if head in ("design", "profile", "solid"):
            return "prop"
        return "prop"      # default: the 3D geometry track
    return "prop"


def compile_any(text):
    """Compile any of the three source kinds; returns (kind, doc)."""
    k = kind_of(text)
    if k == "texture":
        return k, parse_texture(text)
    if k == "model":
        return k, parse_model(text)
    return k, compile_text(text)


def report_any(kind, doc, width=40):
    if kind == "texture":
        return texture_report(doc)
    if kind == "model":
        return model_report(doc, width)
    return report(doc, width=width)


def preview_any(kind, doc, path, big=10):
    """Write a real PNG preview for the Studio UI. Returns the path or None."""
    try:
        if kind == "texture":
            if doc.errors:
                return None
            paint(doc).scale_nn(big).save_png(path)
            return path
        if kind == "model":
            tris = colored_tris(doc)
            if not tris:
                return None
            render_mesh_png(tris, 460, 380).save_png(path)
            return path
        # prop: render the merged mesh with a default colour
        tris = [(t, (150, 160, 175, 255)) for p in doc.parts if p.mesh for t in p.mesh.tris]
        if not tris:
            return None
        render_mesh_png(tris, 460, 380).save_png(path)
        return path
    except Exception:
        return None


def export_any(kind, doc, outdir, slug):
    """Write every output format for a compiled asset. Returns list of paths."""
    os.makedirs(outdir, exist_ok=True)
    made = []
    j = os.path.join
    if kind == "texture":
        if doc.errors:
            return made
        alb = paint(doc)
        alb.save_png(j(outdir, slug + ".png")); made.append(j(outdir, slug + ".png"))
        alb.scale_nn(16).save_png(j(outdir, slug + "@16x.png")); made.append(j(outdir, slug + "@16x.png"))
        material_map(doc, alb).save_png(j(outdir, slug + "_material.png"))
        made.append(j(outdir, slug + "_material.png"))
        with open(j(outdir, slug + ".recipe.json"), "w") as f:
            json.dump(recipe_json(doc), f, indent=2)
        made.append(j(outdir, slug + ".recipe.json"))
    elif kind == "model":
        if doc.errors:
            return made
        export_glb(doc, j(outdir, slug + ".glb")); made.append(j(outdir, slug + ".glb"))
        export_obj(doc, j(outdir, slug + ".obj")); made.append(j(outdir, slug + ".obj"))
        export_strata_json(doc, j(outdir, slug + ".boxmodel.json"))
        made.append(j(outdir, slug + ".boxmodel.json"))
        p = preview_any(kind, doc, j(outdir, slug + "_preview.png"))
        if p: made.append(p)
    else:
        if doc.errors:
            return made
        m = merged(doc)
        if m.tris:
            write_stl(m, j(outdir, slug + ".stl")); made.append(j(outdir, slug + ".stl"))
            write_obj(doc, j(outdir, slug + ".obj")); made.append(j(outdir, slug + ".obj"))
            export_mesh_glb(doc, j(outdir, slug + ".glb")); made.append(j(outdir, slug + ".glb"))
        if doc.profiles:
            write_svg(doc, j(outdir, slug + "_profiles.svg")); made.append(j(outdir, slug + "_profiles.svg"))
        p = preview_any(kind, doc, j(outdir, slug + "_preview.png"))
        if p: made.append(p)
    return made


# ---------------------------------------------------------------- self test
SELFTEST = [
    ("tube",       'profile p\n  circle r=20\n  hole circle r=15\nsolid s from=p extrude h=10', 5489),
    ("slot",       'profile p\n  slot w=18 h=58\n  hole slot w=13.2 h=53.2\nsolid s from=p extrude h=10', 3089),
    ("rounded",    'profile p\n  rect w=40 h=40 r=20\n  hole rect w=30 h=30 r=15\nsolid s from=p extrude h=10', 5463),
    ("multihole",  'profile p\n  rect w=80 h=40 r=5\n  hole circle r=4 at=(-25,0)\n'
                   '  hole circle r=4 at=(25,0)\n  hole slot w=10 h=20 at=(0,0)\n'
                   'solid s from=p extrude h=6', 17398),
    ("concave",    'profile p\n  poly (0,0) (60,0) (60,20) (20,20) (20,50) (0,50)\n'
                   '  hole circle r=5 at=(8,8)\nsolid s from=p extrude h=8', 13773),
    ("revolve",    'profile p\n  path (0,0) (40,0) (40,95) (37.6,95) (37.6,4.8) (0,4.8)\n'
                   'solid s from=p revolve segments=72', 76805),
    ("wedge",      'profile p\n  path (5,0) (20,0) (20,40) (5,40)\n'
                   'solid s from=p revolve angle=120 segments=24', 16734),
    ("loft",       'profile a\n  circle r=20\nprofile b\n  rect w=24 h=24\n'
                   'solid s loft from=a to=b h=30', 12225),
    ("twist",      'profile p\n  ngon n=6 r=15\nsolid s from=p extrude h=60 twist=180 steps=24', 34093),
    ("taper",      'profile p\n  circle r=25\n  hole circle r=20\n'
                   'solid s from=p extrude h=10 taper=5 steps=8', 5822),
    ("ellipse",    'profile p\n  ellipse rx=40 ry=15\n  hole ellipse rx=34 ry=9\n'
                   'solid s from=p extrude h=20', 18443),
]
MUSTFAIL = [
    ("self-crossing outline", 'profile p\n  poly (0,0) (40,0) (0,30) (40,30)\n'
                              'solid s from=p extrude h=5', "SELFX"),
    ("hole outside outline",  'profile p\n  circle r=10\n  hole circle r=4 at=(30,0)\n'
                              'solid s from=p extrude h=5', "HOLE"),
    ("wall too thin",         'profile p\n  circle r=10\n  hole circle r=9.5\n'
                              'solid s from=p extrude h=5', "WALL"),
    ("taper closes the form", 'profile p\n  circle r=10\nsolid s from=p extrude h=80 taper=20', "TAPER"),
    ("unknown token",         'profile p\n  circle r=$wall.enormous\nsolid s from=p extrude h=5', "TOKEN"),
    ("misspelled key",        'profile p\n  circle radius=10\nsolid s from=p extrude h=5', "KEY"),
    ("missing profile",       'profile p\n  circle r=10\nsolid s from=q extrude h=5', "REF"),
    ("extrude an open path",  'profile p\n  path (0,0) (10,0) (10,10)\nsolid s from=p extrude h=5', "KIND"),
    ("revolve axis removed",  'profile p\n  path (0,0) (10,0) (10,10)\n'
                              'solid s from=p revolve axis=x', "ARG"),
]


def selftest(verbose=False):
    import random
    fails = []
    for name, body, want_vol in SELFTEST:
        d = compile_text('design "t" units=mm\n' + body)
        m = d.parts[0].mesh if d.parts and d.parts[0].mesh else None
        if m is None:
            fails.append(f"{name}: did not build ({[e.msg for e in d.errors]})"); continue
        ok = m.manifold()[0]
        vol = m.volume()
        if not ok:
            fails.append(f"{name}: not watertight {m.manifold()}")
        if abs(vol - want_vol) > max(2.0, want_vol * 0.002):
            fails.append(f"{name}: volume {vol:.1f}, expected about {want_vol}")
        if verbose:
            print(f"  {name:12} watertight={ok} vol={vol:9.1f} tris={len(m.tris)}")
    for name, body, code in MUSTFAIL:
        d = compile_text('design "t" units=mm\n' + body)
        codes = {x.code for x in d.diags}
        if code not in codes:
            fails.append(f"{name}: expected a {code} diagnostic, got {sorted(codes)}")
        elif verbose:
            print(f"  {name:24} -> {code} reported")

    # tessellation must conserve area exactly, on arbitrary valid outlines
    random.seed(11); bad = 0
    for _ in range(200):
        n = random.randint(5, 20)
        # one radius per vertex, angles strictly increasing: a star-shaped
        # polygon, which is guaranteed simple - so any failure is ours, not the
        # test data's
        pts = []
        for i in range(n):
            a = 2 * math.pi * i / n
            r = random.uniform(14, 34)
            pts.append((r * math.cos(a), r * math.sin(a)))
        holes = []
        for cx, cy in [(-7, 0), (7, 0), (0, 8), (0, -8)][:random.randint(0, 4)]:
            rr = random.uniform(1.0, 2.4); k = random.randint(3, 12)
            holes.append([(cx + rr * math.cos(2 * math.pi * i / k),
                           cy + rr * math.sin(2 * math.pi * i / k)) for i in range(k)])
        if not extrude(pts, holes, 5).manifold()[0]:
            bad += 1; continue
        want = abs(poly_area(pts)) - sum(abs(poly_area(h)) for h in holes)
        got = sum(abs(poly_area(list(t))) for t in triangulate(pts, holes))
        if abs(got - want) > 1e-6 * max(1.0, want):
            bad += 1
    if bad:
        fails.append(f"random tessellation: {bad}/200 wrong")
    elif verbose:
        print("  random tessellation      -> 200/200 watertight, area exact")

    # --- texture track: paints, produces a valid PNG, non-empty palette
    import struct as _st
    td = parse_texture('texture "t" size=16\nfill color=#808080\n'
                       'noise dark=#60606060 light=#a0a0a060 scale=3\n'
                       'scatter color=#4a7a3a amount=0.2 seed=1\noutline color=#333')
    if td.errors:
        fails.append("texture: clean source reported errors")
    else:
        ti = paint(td)
        png = ti.to_png_bytes()
        if png[:8] != b"\x89PNG\r\n\x1a\n":
            fails.append("texture: PNG signature wrong")
        if len(palette_of(ti)) < 2:
            fails.append("texture: palette collapsed to one colour")
        mm = material_map(td, ti)
        if mm.w != 16:
            fails.append("texture: material map wrong size")
        if verbose:
            print(f"  texture track            -> PNG valid, {len(palette_of(ti))} colours, material map ok")
    tb = parse_texture('texture "b"\nwobble color=#fff')      # bad layer
    if not tb.errors:
        fails.append("texture: bad layer not caught")
    elif verbose:
        print("  texture bad layer        -> rejected")

    # --- model track: builds, exports a valid GLB with animation
    md = parse_model('model "m"\nbone body pivot=(0,0,0)\n'
                     '  box from=(-6,0,-3) to=(6,14,3) color=#4a6a3a\n'
                     'bone arm parent=body pivot=(6,13,0)\n'
                     '  box from=(6,3,-2) to=(10,14,2) color=#45603a\n'
                     'animation "a" loop=false\n  key arm t=0 rot=(0,0,0)\n'
                     '  key arm t=0.3 rot=(-90,0,0)')
    if md.errors:
        fails.append(f"model: clean source reported errors {[e[2] for e in md.errors]}")
    else:
        import tempfile, os as _os
        tf = tempfile.mkdtemp()
        gp = _os.path.join(tf, "m.glb")
        export_glb(md, gp)
        d = open(gp, "rb").read()
        magic, ver, length = _st.unpack("<III", d[:12])
        if magic != 0x46546C67 or ver != 2 or length != len(d):
            fails.append("model: GLB header invalid")
        jlen = _st.unpack("<I", d[12:16])[0]
        g = json.loads(d[20:20 + jlen])
        off = 20 + jlen
        blen = _st.unpack("<I", d[off:off + 4])[0]
        if g["buffers"][0]["byteLength"] != blen:
            fails.append("model: GLB buffer length mismatch")
        for i, a in enumerate(g["accessors"]):
            bv = g["bufferViews"][a["bufferView"]]
            comp = {5126: 4, 5125: 4, 5123: 2}[a["componentType"]]
            nc = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
            if bv.get("byteOffset", 0) + a["count"] * comp * nc > blen:
                fails.append(f"model: GLB accessor {i} overruns buffer")
        if not g.get("animations"):
            fails.append("model: animation missing from GLB")
        _os.remove(gp); _os.rmdir(tf)
        if verbose and not fails:
            print("  model track              -> GLB valid, accessors in-bounds, animation present")
    mb = parse_model('model "b"\nbone body\n  box from=(0,0,0) to=(0,5,5)')  # zero-thickness
    if not mb.errors:
        fails.append("model: zero-thickness box not caught")
    elif verbose:
        print("  model bad box            -> rejected")

    # --- dispatcher routes each kind correctly
    if kind_of('texture "t"\nfill color=#000') != "texture":
        fails.append("dispatch: texture misrouted")
    if kind_of('model "m"\nbone b') != "model":
        fails.append("dispatch: model misrouted")
    if kind_of('design "d"\nprofile p') != "prop":
        fails.append("dispatch: prop misrouted")
    if verbose and not any("dispatch" in f for f in fails):
        print("  dispatcher               -> texture/model/prop routed correctly")
    return fails


# ---------------------------------------------------------------- cli
def _read(src):
    if src == "-": return sys.stdin.read()
    with open(src) as f: return f.read()


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="designbridge",
        description="A text-only CAD bridge: language models write a small design "
                    "language, this compiles it to real 3D geometry and reports back "
                    "in words they can read.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("prompt", help="generate the prompt to give an AI")
    p.add_argument("--brief", required=True, help="what to make, in plain words")
    p.add_argument("--kind", choices=["texture", "model", "prop"], default="model",
                   help="texture (2D tile), model (blocky 3D), or prop (smooth 3D)")
    p.add_argument("--runnable", action="store_true",
                   help="tell the AI it can run this checker itself")
    p.add_argument("-o", "--out")

    c = sub.add_parser("check", help="compile a design and print the text report")
    c.add_argument("file", help="design file, or - for stdin")
    c.add_argument("--json", action="store_true")
    c.add_argument("--no-views", action="store_true")
    c.add_argument("--no-profiles", action="store_true")
    c.add_argument("--width", type=int, default=40)
    c.add_argument("--prev", help="stats json from the previous round, to show a diff")
    c.add_argument("--save-stats", help="write this round's stats json")

    b = sub.add_parser("build", help="compile and export an asset to real files")
    b.add_argument("file")
    b.add_argument("-o", "--out", default="out")
    b.add_argument("--width", type=int, default=40)
    b.add_argument("--force", action="store_true", help="export even with errors")

    sub.add_parser("guide", help="print the full how-to-think guide")
    t = sub.add_parser("selftest", help="verify the geometry kernel and the linter")
    t.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("tokens", help="list the design tokens")
    s = sub.add_parser("serve", help="paste-loop web UI on localhost")
    s.add_argument("--port", type=int, default=8765)

    a = ap.parse_args(argv)

    if a.cmd == "prompt":
        txt = prompt_for(a.kind, a.brief, a.runnable)
        if a.out:
            open(a.out, "w").write(txt); print(f"wrote {a.out}")
        else:
            print(txt)
        return 0

    if a.cmd == "guide":
        print(GUIDE); return 0

    if a.cmd == "selftest":
        fails = selftest(a.verbose)
        if fails:
            print(f"FAILED ({len(fails)}):")
            for f in fails: print("  -", f)
            return 1
        print(f"all good: {len(SELFTEST)} solids built and measured, "
              f"{len(MUSTFAIL)} bad designs correctly rejected, "
              "200 random outlines tessellated exactly")
        return 0

    if a.cmd == "tokens":
        w = max(len(k) for k in TOKENS)
        for k, v in TOKENS.items():
            print(f"  ${k:<{w}}  {v:>8}")
        print(f"\n  build volume: {BUILD_VOLUME[0]:.0f} x {BUILD_VOLUME[1]:.0f} "
              f"x {BUILD_VOLUME[2]:.0f} mm")
        return 0

    if a.cmd == "serve":
        return serve(a.port)

    if a.cmd == "check":
        text = _read(a.file)
        kind, doc = compile_any(text)
        if a.json:
            diags = ([{"severity": d.sev, "line": d.line, "message": d.msg,
                       "fix": d.fix, "code": d.code} for d in doc.diags]
                     if kind == "prop" else
                     [{"severity": d[0], "line": d[1], "message": d[2],
                       "fix": d[3], "code": d[4]} for d in doc.diags])
            print(json.dumps({"kind": kind, "report": report_any(kind, doc, a.width),
                              "diagnostics": diags,
                              "ok": not doc.errors}, indent=2))
        else:
            print(report_any(kind, doc, a.width))
        return 1 if doc.errors else 0

    if a.cmd == "build":
        text = _read(a.file)
        kind, doc = compile_any(text)
        print(report_any(kind, doc, a.width))
        if doc.errors and not a.force:
            print("\nNOT EXPORTED: fix the errors above, or pass --force.")
            return 1
        name = getattr(doc, "name", "asset")
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "asset"
        made = export_any(kind, doc, a.out, slug)
        print("\nEXPORTED (" + kind + ")")
        for f in made:
            print(f"  {f}  ({os.path.getsize(f)} bytes)")
        return 0

    ap.print_help()
    return 0


# ---------------------------------------------------------------- studio ui
STUDIO_HTML = r"""<!doctype html><html><head><meta charset=utf-8>
<title>DesignBridge Studio</title><style>
*{box-sizing:border-box}
:root{--bg:#121318;--panel:#171920;--edge:#262933;--ink:#e9e7e2;--dim:#8b8f9a;
 --accent:#e2b24a;--good:#6cc08b;--bad:#e8785a;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--ink);font:14px/1.5 ui-sans-serif,system-ui,sans-serif;overflow:hidden}
header{height:46px;display:flex;align-items:center;gap:14px;padding:0 16px;border-bottom:1px solid var(--edge)}
header b{font-weight:650;letter-spacing:.02em}
header .k{font-size:11px;color:var(--dim);border:1px solid var(--edge);border-radius:4px;padding:2px 7px}
#kind{color:var(--accent);border-color:#4a3f20}
main{display:grid;grid-template-columns:230px 1fr 1fr;height:calc(100% - 46px)}
.col{min-width:0;display:flex;flex-direction:column;border-right:1px solid var(--edge)}
.col:last-child{border-right:0}
.hd{padding:8px 12px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;
 color:var(--dim);border-bottom:1px solid var(--edge);display:flex;align-items:center;gap:8px}
.hd .sp{flex:1}
button{background:#23262f;color:var(--ink);border:1px solid var(--edge);border-radius:5px;
 padding:5px 10px;font:600 12px/1 inherit;cursor:pointer}
button:hover{border-color:#3a3e49}
button.p{background:var(--accent);color:#1a1205;border-color:transparent}
button.sm{padding:3px 7px;font-size:11px}
select,input{background:#23262f;color:var(--ink);border:1px solid var(--edge);border-radius:5px;
 padding:5px 8px;font:inherit;font-size:12px}
#projsel{width:100%}
#hist{flex:1;overflow:auto}
.v{padding:9px 12px;border-bottom:1px solid var(--edge);cursor:pointer}
.v:hover{background:#1c1f27}.v.sel{background:#20242e;border-left:2px solid var(--accent)}
.v .n{font-size:12.5px;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v .m{font-size:10.5px;color:var(--dim)}
.v .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px}
.ed{flex:1;display:flex;flex-direction:column}
textarea{flex:1;margin:0;padding:12px;background:var(--bg);color:var(--ink);border:0;
 outline:0;resize:none;font:12.5px/1.5 var(--mono);white-space:pre;tab-size:2}
#note{margin:8px 12px;width:calc(100% - 24px)}
.row{display:flex;gap:8px;padding:8px 12px;border-top:1px solid var(--edge);align-items:center;flex-wrap:wrap}
#prev{background:#0c0d11;display:flex;align-items:center;justify-content:center;
 min-height:230px;border-bottom:1px solid var(--edge);padding:10px}
#prev img{max-width:100%;max-height:300px;image-rendering:auto}
#prev.pix img{image-rendering:pixelated}
#rep{flex:1;overflow:auto;margin:0;padding:12px;font:11.5px/1.4 var(--mono);white-space:pre;color:#cfd2d8}
.status{font-size:11px;padding:2px 8px;border-radius:4px}
.ok{background:#16351f;color:var(--good)}.fail{background:#3a1c16;color:var(--bad)}
small{color:var(--dim)}
</style></head><body>
<header>
 <b>DesignBridge</b><span class=k id=kind>-</span>
 <span style=flex:1></span>
 <small id=hint>Ctrl+Enter compiles | Ctrl+S saves a version</small>
</header>
<main>
 <div class=col>
  <div class=hd><select id=projsel></select></div>
  <div class=hd style="border-top:0"><button class="sm" id=newproj>+ New</button>
    <span class=sp></span><button class="sm" id=export>Export</button></div>
  <div id=hist></div>
 </div>
 <div class="col ed">
  <div class=hd>Source
    <span class=sp></span>
    <select id=kindpick>
      <option value=auto>auto-detect</option>
      <option value=texture>texture</option>
      <option value=model>model</option>
      <option value=prop>prop</option>
    </select>
    <button class="sm" id=ex>Example</button>
    <button class="sm" id=prompt>Copy AI prompt</button>
  </div>
  <textarea id=src spellcheck=false placeholder="Write a texture, a model, or a prop here.
Click Example to load one, or 'Copy AI prompt' to get instructions to paste into another AI."></textarea>
  <input id=note placeholder="note for this version (what changed?)">
  <div class=row>
    <button class="p" id=compile>Compile</button>
    <button id=save>Save version</button>
    <span class=sp style=flex:1></span>
    <span id=stat class=status>idle</span>
  </div>
 </div>
 <div class=col>
  <div class=hd>Preview <span class=sp></span><small id=pvnote></small></div>
  <div id=prev><small>compile to see a preview</small></div>
  <div class=hd style="border-top:1px solid var(--edge)">Feedback &amp; evaluation</div>
  <pre id=rep>The report the AI reads appears here: errors with fixes, measurements,
palette or bone tree, and the ASCII views. The colour preview above is for you.</pre>
 </div>
</main>
<script>
const $=s=>document.getElementById(s);
let cur={project:null, versions:[], sel:null, lastKind:null};
async function jget(u){return (await fetch(u)).json()}
async function jpost(u,b){return (await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})).json()}
async function loadProjects(){
  const d=await jget('/api/projects');
  const sel=$('projsel'); sel.innerHTML='';
  d.projects.forEach(p=>{const o=document.createElement('option');o.value=p;o.textContent=p;sel.appendChild(o)});
  if(d.projects.length){ if(!cur.project||!d.projects.includes(cur.project)) cur.project=d.projects[0];
    sel.value=cur.project; await loadHistory(); }
  else { cur.project=null; $('hist').innerHTML='<div class=v><small>no versions yet - Compile then Save</small></div>'; }
}
async function loadHistory(){
  if(!cur.project){return}
  const d=await jget('/api/project/'+encodeURIComponent(cur.project));
  cur.versions=d.versions;
  const h=$('hist'); h.innerHTML='';
  d.versions.slice().reverse().forEach(v=>{
    const el=document.createElement('div'); el.className='v'+(v.id===cur.sel?' sel':'');
    el.innerHTML=`<div class=n><span class=dot style="background:${v.ok?'var(--good)':'var(--bad)'}"></span>${v.note||'(no note)'}</div><div class=m>v${v.id} - ${v.kind} - ${v.time}</div>`;
    el.onclick=()=>openVersion(v.id);
    h.appendChild(el);
  });
}
async function openVersion(id){
  const d=await jget(`/api/version/${encodeURIComponent(cur.project)}/${id}`);
  $('src').value=d.source; $('rep').textContent=d.report; cur.sel=id;
  setKind(d.kind); showPreview(d.preview); setStatus(d.ok);
  loadHistory();
}
function setKind(k){cur.lastKind=k;$('kind').textContent=k||'-';
  $('prev').className=(k==='texture')?'pix':''}
function setStatus(ok){const s=$('stat');s.className='status '+(ok?'ok':'fail');s.textContent=ok?'PASS':'FAIL'}
function showPreview(url){const p=$('prev');
  if(url){p.innerHTML='';const im=new Image();im.src=url+'?t='+Date.now();p.appendChild(im)}
  else p.innerHTML='<small>no preview (fix errors first)</small>'}
async function compile(){
  $('stat').textContent='...';
  const d=await jpost('/api/compile',{source:$('src').value, kind:$('kindpick').value});
  $('rep').textContent=d.report; setKind(d.kind); setStatus(d.ok);
  showPreview(d.preview);
  $('pvnote').textContent=d.ok?'':'errors - preview may be stale';
}
async function save(){
  if(!$('src').value.trim())return;
  const d=await jpost('/api/save',{project:cur.project, source:$('src').value, note:$('note').value, kind:$('kindpick').value});
  cur.project=d.project; cur.sel=d.id; $('note').value='';
  await loadProjects(); $('projsel').value=cur.project; await loadHistory(); openVersion(d.id);
}
$('compile').onclick=compile;
$('save').onclick=save;
$('projsel').onchange=e=>{cur.project=e.target.value;cur.sel=null;loadHistory()};
$('newproj').onclick=async()=>{const n=window.prompt('New asset name (e.g. rusted_iron, stalker_mob):','');if(!n)return;cur.project=n;cur.sel=null;
  $('src').value='';$('rep').textContent='';$('prev').innerHTML='';
  $('projsel').insertAdjacentHTML('beforeend',`<option value="${n}">${n}</option>`);$('projsel').value=n;
  $('hist').innerHTML='<div class=v><small>new asset - Compile then Save</small></div>'};
$('ex').onclick=async()=>{const k=$('kindpick').value==='auto'?'model':$('kindpick').value;
  const t=await (await fetch('/api/example?kind='+k)).text();$('src').value=t;compile()};
$('prompt').onclick=async()=>{const b=window.prompt('Describe what to make:','a rusted iron block');if(!b)return;
  const k=$('kindpick').value==='auto'?'model':$('kindpick').value;
  const t=await (await fetch(`/api/prompt?kind=${k}&brief=${encodeURIComponent(b)}`)).text();
  await navigator.clipboard.writeText(t);$('stat').textContent='prompt copied'};
$('export').onclick=async()=>{if(!cur.project||cur.sel==null){alert('open a saved version first');return}
  const d=await jpost('/api/export',{project:cur.project,id:cur.sel});
  $('rep').textContent='EXPORTED to '+d.dir+'\n\n'+d.files.join('\n')+'\n\n'+$('rep').textContent};
$('src').addEventListener('keydown',e=>{
  if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();compile()}
  if(e.ctrlKey&&(e.key==='s'||e.key==='S')){e.preventDefault();save()}});
loadProjects();
</script></body></html>"""


EX_TEXTURE = '''texture "rusted_iron" size=16 metallic=0.3 roughness=0.7
fill color=#8a8f98
noise dark=#6a6f7860 light=#aab0ba60 scale=3 amount=0.5
scatter color=#8a4a2a amount=0.3 seed=4
scatter color=#b5702f amount=0.15 seed=9
veins color=#5a3016 scale=5 amount=0.1 seed=2
bevel light=#c8ccd250 dark=#3a3e4660
'''

EX_MODEL = '''model "stalker"
bone body pivot=(0,0,0)
  box from=(-5,0,-3) to=(5,14,3) color=#3a4a52
bone head parent=body pivot=(0,14,0)
  box from=(-4,14,-3) to=(4,20,3) color=#45575f
  box from=(-1,16,-4) to=(1,18,-3) color=#d04030
bone arm_l parent=body pivot=(5,13,0)
  box from=(5,2,-2) to=(8,14,2) color=#33424a
bone arm_r parent=body pivot=(-5,13,0)
  box from=(-8,2,-2) to=(-5,14,2) color=#33424a
bone leg_l parent=body pivot=(2,0,0)
  box from=(0,-12,-2) to=(4,0,2) color=#2d3a41
bone leg_r parent=body pivot=(-2,0,0)
  box from=(-4,-12,-2) to=(0,0,2) color=#2d3a41
animation "lunge" loop=false
  key arm_l t=0 rot=(0,0,0)
  key arm_r t=0 rot=(0,0,0)
  key arm_l t=0.2 rot=(-100,0,0)
  key arm_r t=0.2 rot=(-100,0,0)
  key arm_l t=0.5 rot=(0,0,0)
  key arm_r t=0.5 rot=(0,0,0)
'''

EX_PROP = '''design "relay_pylon" units=mm
profile post
  circle r=8
  hole circle r=5
solid shaft from=post extrude h=120 color=#5a6670
profile base
  ngon n=6 r=22
solid foot from=base extrude h=8 color=#3a4450
assembly
  part foot at=(0,0,0)
  part shaft at=(0,0,8)
'''


def _studio_example(kind):
    return {"texture": EX_TEXTURE, "model": EX_MODEL, "prop": EX_PROP}.get(kind, EX_MODEL)


def _studio_root():
    root = os.environ.get("DESIGNBRIDGE_HOME") or os.path.join(
        os.path.expanduser("~"), "designbridge_studio")
    os.makedirs(root, exist_ok=True)
    return root


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_-]", "_", (name or "asset").strip())[:60] or "asset"


def serve(port=8765):
    """DesignBridge Studio: a local workbench with a live preview, the AI
    feedback report, and a per-asset version history saved to disk."""
    import datetime
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse, parse_qs, unquote

    root = _studio_root()

    def proj_dir(p):
        d = os.path.join(root, _safe(p)); os.makedirs(d, exist_ok=True); return d

    def load_meta(p):
        f = os.path.join(proj_dir(p), "versions.json")
        return json.load(open(f)) if os.path.exists(f) else []

    def save_meta(p, m):
        json.dump(m, open(os.path.join(proj_dir(p), "versions.json"), "w"), indent=2)

    def compile_preview(source):
        kind, doc = compile_any(source)
        rep = report_any(kind, doc)
        return kind, doc, rep, (not doc.errors)

    class H(BaseHTTPRequestHandler):
        def _send(self, body, ctype="text/plain; charset=utf-8", code=200):
            if isinstance(body, str): body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj), "application/json", code)

        def _reqbody(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")

        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path == "/":
                return self._send(STUDIO_HTML, "text/html; charset=utf-8")
            if u.path == "/api/projects":
                ps = sorted([d for d in os.listdir(root)
                             if os.path.isdir(os.path.join(root, d))])
                return self._json({"projects": ps})
            if u.path.startswith("/api/project/"):
                p = unquote(u.path.split("/api/project/", 1)[1])
                return self._json({"project": p, "versions": load_meta(p)})
            if u.path.startswith("/api/version/"):
                parts = u.path.split("/")
                p = unquote(parts[3]); vid = parts[4]
                d = proj_dir(p)
                meta = {m["id"]: m for m in load_meta(p)}.get(int(vid))
                if not meta: return self._json({"error": "not found"}, 404)
                src = open(os.path.join(d, "v%s.db" % vid)).read()
                rep = open(os.path.join(d, "v%s.report.txt" % vid)).read()
                purl = ("/preview/%s/%s" % (p, vid) if os.path.exists(
                    os.path.join(d, "v%s_preview.png" % vid)) else None)
                return self._json({"source": src, "report": rep, "kind": meta["kind"],
                                   "ok": meta["ok"], "preview": purl})
            if u.path.startswith("/preview/"):
                parts = u.path.split("/")
                p = unquote(parts[2]); vid = parts[3]
                f = os.path.join(proj_dir(p), "v%s_preview.png" % vid)
                if os.path.exists(f):
                    return self._send(open(f, "rb").read(), "image/png")
                return self._send(b"", code=404)
            if u.path == "/api/example":
                return self._send(_studio_example(q.get("kind", ["model"])[0]))
            if u.path == "/api/prompt":
                return self._send(prompt_for(q.get("kind", ["model"])[0],
                                             q.get("brief", ["an asset"])[0]))
            if u.path == "/live_preview.png":
                f = os.path.join(root, "_live_preview.png")
                if os.path.exists(f): return self._send(open(f, "rb").read(), "image/png")
                return self._send(b"", code=404)
            return self._send("not found", code=404)

        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/api/compile":
                b = self._reqbody()
                src = b.get("source", "")
                kind, doc, rep, ok = compile_preview(src)
                live = os.path.join(root, "_live_preview.png")
                url = None
                if preview_any(kind, doc, live):
                    url = "/live_preview.png"
                return self._json({"kind": kind, "report": rep, "ok": ok, "preview": url})
            if u.path == "/api/save":
                b = self._reqbody()
                p = _safe(b.get("project") or "asset")
                d = proj_dir(p)
                meta = load_meta(p)
                vid = (max([m["id"] for m in meta]) + 1) if meta else 1
                src = b.get("source", "")
                kind, doc, rep, ok = compile_preview(src)
                open(os.path.join(d, "v%d.db" % vid), "w").write(src)
                open(os.path.join(d, "v%d.report.txt" % vid), "w").write(rep)
                preview_any(kind, doc, os.path.join(d, "v%d_preview.png" % vid))
                meta.append({"id": vid, "kind": kind, "ok": ok,
                             "note": b.get("note", "").strip(),
                             "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")})
                save_meta(p, meta)
                return self._json({"project": p, "id": vid, "kind": kind, "ok": ok})
            if u.path == "/api/export":
                b = self._reqbody()
                p = _safe(b["project"]); vid = int(b["id"]); d = proj_dir(p)
                src = open(os.path.join(d, "v%d.db" % vid)).read()
                kind, doc = compile_any(src)
                outdir = os.path.join(d, "export_v%d" % vid)
                files = export_any(kind, doc, outdir, p)
                return self._json({"dir": outdir, "files": files})
            return self._send("not found", code=404)

        def log_message(self, *a): pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print("DesignBridge Studio   ->  http://127.0.0.1:%d" % port)
    print("projects saved in     ->  %s" % root)
    print("ctrl-c to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0



if __name__ == "__main__":
    sys.exit(main())
