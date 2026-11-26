#!/usr/bin/env python3
"""
DesignBridge Studio v2 — Local-First Storage, CAD & LLM Relay Server (Stdlib Only)
==================================================================================
Zero external pip dependencies. Uses Python's built-in libraries:
  - sqlite3 (WAL mode) for ACID-safe relational storage of projects,
    Git-style branch/commit DAGs, full prompt/chat & AI response history,
    commit preview thumbnails, and UI layout + studio configuration settings.
  - Filesystem workspace/ directory for human-readable JSON snapshots,
    versioned .dsl source files, compiler reports, thumbnails, and exports.
  - Real downloadable .glb (binary glTF 2.0), .obj, .stl, .json, and .zip
    commit & full-repository archives.
  - Optional bridge to designbridge.py if placed in the same directory.
  - Optional Direct LLM relay (/api/llm/run) for OpenAI-compatible, Gemini,
    or local Ollama endpoints (avoids browser CORS issues).

Usage:
  python3 studio_server.py --port 8765 --workspace ./workspace
"""

import argparse
import base64
import datetime
import io
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent

# Studio HTML filenames to try, in order. The first two are the layouts this
# repository has shipped with; the last two are legacy names.
UI_CANDIDATES = (
    "index.html",
    "static/index.html",
    "designbridge_studio-v2.html",
    "designbridge_studio.html",
)


def resolve_ui_path(value=None, required=True):
    """Locate the Studio HTML file.

    Relative paths are checked against the current directory first and then
    against the directory holding studio_server.py, so the server starts the
    same way from the repository root, from elsewhere, or from an IDE. When no
    path is given, the usual filenames are searched in both places.
    """
    roots = list(dict.fromkeys((Path.cwd().resolve(), SCRIPT_DIR)))

    if value:
        candidate = Path(value).expanduser()
        search = [candidate] if candidate.is_absolute() else [root / candidate for root in roots]
        for path in search:
            if path.is_file():
                return path.resolve()
        if required:
            looked = "\n  ".join(str(p) for p in search)
            raise FileNotFoundError(f"Studio HTML file not found. Looked at:\n  {looked}\nPass --ui <path> to point at it.")
        return search[0]

    for name in UI_CANDIDATES:
        for root in roots:
            path = root / name
            if path.is_file():
                return path.resolve()
    if required:
        looked = "\n  ".join(str(root / name) for name in UI_CANDIDATES for root in roots)
        raise FileNotFoundError(
            "Studio HTML file not found next to studio_server.py.\nLooked at:\n  "
            + looked
            + "\nPass --ui <path> to point at it."
        )
    return SCRIPT_DIR / UI_CANDIDATES[0]


# Optional integration with designbridge.py compiler/exporter if available
HAS_DESIGNBRIDGE = False
try:
    import designbridge as db_core
    HAS_DESIGNBRIDGE = True
except Exception:
    db_core = None


DEFAULT_SETTINGS = {
    "leftWidth": 312,
    "rightWidth": 456,
    "leftCollapsed": False,
    "rightCollapsed": False,
    "orthoCollapsed": False,
    "dockCollapsed": False,
    "activeProjectId": "stalker_mob",
    "activeMode": "edit",
    "activeRightTab": "workbench",
    "autoFork": False,
    "includeReport": True,
    "fullDslSpec": False,
    "wireframe": False,
    "autoRotate": False,
    "ghostOverlay": False,
    "scaleCage": False,
    "cameraPreset": "iso",
    "llmEnabled": False,
    "llmProvider": "ollama",
    "llmEndpoint": "http://127.0.0.1:11434/v1/chat/completions",
    "llmModel": "qwen2.5-coder:7b",
    "llmApiKey": "",
    "llmAutoCompile": True,
    # Per-asset shape budgets: {projectId: {complexity, symmetry, ...}}
    "styles": {},
    # Standing art direction, shared by every asset, plus named presets
    "styleLock": "",
    "styleLockOn": True,
    "styleLockPresets": {},
}

# ----------------------------------------------------------------------
# SEED PROJECTS
# ----------------------------------------------------------------------
# Every source below is REAL designbridge grammar that compiles. The
# previous build shipped invented syntax (<<<DESIGN model= box=(16,32,6)>,
# "cylinder outer r=44", "layer base noise=perlin(0.18)"), none of which
# designbridge understands - so the three starter projects taught the
# operator, and any model reading them back, a DSL that does not exist.
# These three are the real thing: one per track, each verified by the
# compiler on first boot.
# ----------------------------------------------------------------------

SEED_MODEL_V1 = '''model "stalker"
bone torso pivot=(0,0,0)
  box from=(-4,10,-3) to=(4,24,3) color=#2d323f
  box from=(-5,22,-3) to=(5,24,3) color=#1e222b
bone head pivot=(0,24,0) parent=torso
  box from=(-4,24,-4) to=(4,32,4) color=#1e222b
bone left_arm pivot=(-4,23,0) parent=torso
  box from=(-6,9,-2) to=(-3,23,2) color=#1e222b
bone right_arm pivot=(4,23,0) parent=torso
  box from=(3,9,-2) to=(6,23,2) color=#1e222b
bone left_leg pivot=(-2,10,0) parent=torso
  box from=(-4,0,-2) to=(-1,10,2) color=#101217
bone right_leg pivot=(2,10,0) parent=torso
  box from=(1,0,-2) to=(4,10,2) color=#101217
'''

SEED_MODEL_V2 = '''model "stalker"
bone torso pivot=(0,0,0)
  box from=(-4,10,-3) to=(4,24,3) color=#2d323f
  box from=(-5,22,-3) to=(5,24,3) color=#1e222b
bone head pivot=(0,24,0) parent=torso
  box from=(-4,24,-4) to=(4,32,4) color=#1e222b
  box from=(-4,27,-4.5) to=(4,29,-4) color=#ef4444
bone left_arm pivot=(-4,23,0) parent=torso
  box from=(-6,4,-2) to=(-3,23,2) color=#1e222b
bone right_arm pivot=(4,23,0) parent=torso
  box from=(3,4,-2) to=(6,23,2) color=#1e222b
bone left_leg pivot=(-2,10,0) parent=torso
  box from=(-4,0,-2) to=(-1,10,2) color=#101217
bone right_leg pivot=(2,10,0) parent=torso
  box from=(1,0,-2) to=(4,10,2) color=#101217

animation "lunge" loop=true
  key torso t=0.0 rot=(0,0,0)
  key left_arm t=0.0 rot=(10,0,0)
  key right_arm t=0.0 rot=(10,0,0)
  key torso t=0.25 rot=(-14,0,0)
  key left_arm t=0.25 rot=(-70,0,0)
  key right_arm t=0.25 rot=(-70,0,0)
  key torso t=0.5 rot=(0,0,0)
  key left_arm t=0.5 rot=(10,0,0)
  key right_arm t=0.5 rot=(10,0,0)
'''

SEED_PROP_V1 = '''design "Coffee Mug" units=mm
note "80 dia x 95 tall, 2.4 wall - one revolve builds the whole vessel"
note "the handle overlaps the body on purpose - it is welded there, so the"
note "interpenetration warning below is an accepted decision, not a mistake"

profile body_section
  path (0,0) (40,0) (40,95) (40-$wall.std,95) (40-$wall.std,$wall.std*2) (0,$wall.std*2)

profile handle_ring
  slot w=18 h=58
  hole slot w=18-$wall.std*2 h=58-$wall.std*2

solid body from=body_section revolve segments=72
solid handle from=handle_ring extrude h=10

assembly
  part body at=(0,0,0)
  part handle at=(44,0,50) rot=(90,0,0)
'''

SEED_TEXTURE_V1 = '''texture "rusted_iron" size=16 metallic=0.6 roughness=0.7
fill color=#52525b
noise dark=#3f3f466a light=#71717a6a scale=3 amount=0.5
scatter color=#9a3412 amount=0.18 seed=4
veins color=#b45309 scale=4 amount=0.1 seed=7
bevel light=0.12 dark=0.18
outline color=#27272a
'''

# A new project starts from one of these - real, minimal, compiling source
# for its track, with __NAME__ swapped for the project id.
STARTER_SOURCES = {
    "model": '''model "__NAME__"
bone body pivot=(0,0,0)
  box from=(-4,0,-3) to=(4,14,3) color=#9aa0ac
bone head pivot=(0,14,0) parent=body
  box from=(-3,14,-3) to=(3,20,3) color=#c8ccd4
''',
    "prop": '''design "__NAME__" units=mm

profile shell
  path (0,0) (20,0) (20,40) (20-$wall.std,40) (20-$wall.std,$wall.std) (0,$wall.std)

solid body from=shell revolve segments=64

assembly
  part body at=(0,0,0)
''',
    "texture": '''texture "__NAME__" size=16 metallic=0.0 roughness=0.8
fill color=#8a8a8a
noise dark=#7070706a light=#a0a0a06a scale=3 amount=0.4
bevel light=0.1 dark=0.15
outline color=#555555
''',
}

SEED_PROJECTS = {
    "stalker_mob": {
        "id": "stalker_mob",
        "name": "stalker_mob",
        "track": "model",
        "icon": "view_in_ar",
        "activeBranch": "main",
        "headCommitId": "c2",
        "branches": {
            "main": {"name": "main", "color": "#d4d4d8", "head": "c2"},
        },
        "commits": [
            {
                "id": "c1", "tag": "v1", "branch": "main", "parentId": None, "mergeParentId": None,
                "time": "14:08",
                "note": "Initial bipedal voxel blockout",
                "dsl": SEED_MODEL_V1,
            },
            {
                "id": "c2", "tag": "v2", "branch": "main", "parentId": "c1", "mergeParentId": None,
                "time": "14:22",
                "note": "Arms past knees, crimson visor, lunge animation",
                "dsl": SEED_MODEL_V2,
            },
        ],
        "seedChat": [
            {
                "branch": "main", "commitId": "c2", "mode": "edit",
                "userPrompt": "Make the arms longer so they reach past the knees, and add a glowing red visor across the front of the head.",
                "rawAiResponse": SEED_MODEL_V2,
            }
        ],
    },
    "coffee_mug": {
        "id": "coffee_mug",
        "name": "coffee_mug",
        "track": "prop",
        "icon": "coffee",
        "activeBranch": "main",
        "headCommitId": "m1",
        "branches": {"main": {"name": "main", "color": "#d4d4d8", "head": "m1"}},
        "commits": [
            {
                "id": "m1", "tag": "v1", "branch": "main", "parentId": None, "mergeParentId": None,
                "time": "11:05",
                "note": "Revolved ceramic shell with welded loop handle",
                "dsl": SEED_PROP_V1,
            }
        ],
        "seedChat": [],
    },
    "rusted_iron": {
        "id": "rusted_iron",
        "name": "rusted_iron",
        "track": "texture",
        "icon": "texture",
        "activeBranch": "main",
        "headCommitId": "t1",
        "branches": {"main": {"name": "main", "color": "#d4d4d8", "head": "t1"}},
        "commits": [
            {
                "id": "t1", "tag": "v1", "branch": "main", "parentId": None, "mergeParentId": None,
                "time": "09:40",
                "note": "Oxidised iron plate with amber pitting and veins",
                "dsl": SEED_TEXTURE_V1,
            }
        ],
        "seedChat": [],
    },
}

def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def now_hhmm() -> str:
    return datetime.datetime.now().strftime("%H:%M")


def safe_slug(val: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", (val or "").strip().lower()).strip("_")
    return cleaned or f"design_{uuid.uuid4().hex[:6]}"


# ======================================================================
# REAL DESIGNBRIDGE BRIDGE
# ----------------------------------------------------------------------
# Everything below delegates to designbridge.py - the actual compiler,
# verifier and exporter. Studio never invents geometry, never fabricates a
# report and never synthesises a placeholder box: if designbridge cannot
# build it, the commit is honestly marked FAIL and the real diagnostics are
# shown. This is the whole point of the tool, so there is no fallback that
# quietly makes a broken design look fine.
# ======================================================================

SENTINEL_RE = re.compile(r"<<<\s*DESIGN\b(.*?)(?:>>>|$)", re.S | re.I)
FENCE_RE = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)(?:\r?\n[ \t]*```|$)", re.S)
MARKER_RE = re.compile(r"(?:^|\n)[ \t]*(?:BEGIN[_ ]DESIGN|DESIGN[_ ]START)[ \t]*\r?\n(.*?)"
                       r"(?:\n[ \t]*(?:END[_ ]DESIGN|DESIGN[_ ]END)|$)", re.S | re.I)

# A document always opens with one of these, so the body can be recovered
# from a reply that has no delimiters at all.
TOP_KEYWORDS = ("model", "texture", "design")
# Every statement the three grammars allow. Used to decide where the source
# stops and the model's closing prose begins.
BODY_KEYWORDS = (
    "model", "texture", "design", "bone", "box", "animation", "key", "note",
    "profile", "solid", "assembly", "part", "fill", "noise", "gradient",
    "brick", "planks", "checker", "stripes", "scatter", "veins", "dither",
    "bevel", "outline", "path", "slot", "hole", "circle", "ellipse", "rect",
    "ngon", "poly",
)


def _looks_like_source(block: str) -> bool:
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        return line.split()[0].lower().strip('"') in TOP_KEYWORDS
    return False


def _carve_bare_source(text: str) -> str:
    """Recover the document from a reply with no delimiters.

    Starts at the first top-level keyword and keeps going while the lines
    still look like statements, so a trailing "Hope that helps!" is dropped
    without taking any of the source with it.
    """
    lines = text.splitlines()
    start = None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line and line.split()[0].lower().strip('"') in TOP_KEYWORDS:
            start = i
            break
    if start is None:
        return ""
    out, misses = [], 0
    for raw in lines[start:]:
        line = raw.strip()
        if not line:
            out.append(raw)
            continue
        head = line.split()[0].lower().strip('",')
        if line.startswith("#") or line.startswith("//") or head in BODY_KEYWORDS:
            out.append(raw)
            misses = 0
        else:
            # Two non-statement lines in a row means the prose has resumed.
            misses += 1
            if misses >= 2:
                break
            out.append(raw)
    while out and not out[-1].strip():
        out.pop()
    if misses:
        while out and out[-1].strip() and out[-1].strip().split()[0].lower().strip('",') not in BODY_KEYWORDS \
                and not out[-1].strip().startswith(("#", "//")):
            out.pop()
    return "\n".join(out)


def strip_sentinels(text: str) -> str:
    """Pull the design document out of whatever the model actually sent.

    The old build demanded <<<DESIGN ... >>>. Models routinely swallow those
    angle brackets, emit an empty "<<>>", or drop the closing marker, which
    left nothing to compile. So no single delimiter is trusted: this tries a
    fenced block, then the legacy sentinels, then BEGIN/END markers, and
    finally recovers bare source sitting in open prose. A reply only has to
    contain the document somewhere.
    """
    if not text:
        return ""
    raw = text.replace("\r\n", "\n")

    candidates = []
    for m in FENCE_RE.finditer(raw):
        candidates.append(m.group(1))
    for m in SENTINEL_RE.finditer(raw):
        candidates.append(m.group(1))
    for m in MARKER_RE.finditer(raw):
        candidates.append(m.group(1))

    for block in candidates:
        # A fenced block may itself hold the legacy sentinels.
        inner = SENTINEL_RE.search(block)
        if inner:
            block = inner.group(1)
        block = block.strip("\n")
        if _looks_like_source(block):
            return block.rstrip() + "\n"

    if _looks_like_source(raw):
        return raw.strip("\n").rstrip() + "\n"

    carved = _carve_bare_source(raw)
    if carved:
        return carved.rstrip() + "\n"

    # Nothing recognisable. Hand back the stripped text so the compiler can
    # report a real syntax error against what the model actually said.
    for block in candidates:
        if block.strip():
            return block.strip("\n").rstrip() + "\n"
    return raw.strip()


class CompileResult:
    """The real compiler's verdict on one revision."""

    __slots__ = ("kind", "doc", "status", "report", "errors", "warnings",
                 "bounds", "struct", "anim", "ok")

    def __init__(self, kind, doc, status, report, errors, warnings, bounds, struct, anim):
        self.kind, self.doc = kind, doc
        self.status, self.report = status, report
        self.errors, self.warnings = errors, warnings
        self.bounds, self.struct, self.anim = bounds, struct, anim
        self.ok = status == "PASS"


def _diag_counts(doc) -> tuple[int, int]:
    """Count errors and warnings across both diagnostic shapes.

    The prop track carries Diag objects; the model and texture tracks carry
    plain (sev, line, msg, fix, code) tuples. Handle either.
    """
    errors = warnings = 0
    for d in getattr(doc, "diags", []) or []:
        sev = (d[0] if isinstance(d, tuple) else getattr(d, "sev", "")) or ""
        sev = str(sev).lower()
        if sev.startswith("err"):
            errors += 1
        elif sev.startswith("warn"):
            warnings += 1
    return errors, warnings


def _fmt_num(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def compile_source(dsl: str) -> CompileResult:
    """Compile a revision with designbridge and read the real facts back out."""
    if db_core is None:
        raise RuntimeError(
            "designbridge.py was not importable, so nothing can be compiled. "
            "Put designbridge.py next to studio_server.py and restart."
        )
    body = strip_sentinels(dsl)
    if not body.strip():
        raise ValueError("The revision is empty - there is no DSL to compile.")

    kind, doc = db_core.compile_any(body)
    report = db_core.report_any(kind, doc)
    errors, warnings = _diag_counts(doc)
    status = "PASS" if errors == 0 else "FAIL"

    bounds, struct, anim = "-", "-", "static"

    if kind == "model":
        bones = doc.bones.values() if isinstance(doc.bones, dict) else (doc.bones or [])
        bones = list(bones)
        nbox = sum(len(getattr(b, "boxes", []) or []) for b in bones)
        anims = doc.anims if isinstance(doc.anims, dict) else {}
        try:
            lo, hi = db_core.bbox(doc)
            bounds = "x".join(_fmt_num(round(hi[i] - lo[i], 2)) for i in range(3)) + " px"
        except Exception:
            bounds = "-"
        struct = f"{len(bones)}B / {nbox}BX"
        anim = next(iter(anims), "static") if anims else "static"

    elif kind == "prop":
        try:
            st = db_core.stats(doc)
            bb = st.get("bbox") or []
            unit = st.get("units") or ""
            if len(bb) == 3:
                bounds = "x".join(_fmt_num(round(v, 2)) for v in bb) + (f" {unit}" if unit else "")
            struct = f"{st.get('parts', 0)}P / {st.get('triangles', 0)}T"
            anim = "watertight" if st.get("watertight") else "open"
        except Exception:
            struct = f"{len(getattr(doc, 'parts', []) or [])}P"

    else:  # texture
        size = getattr(doc, "size", 0)
        bounds = f"{size}x{size} px"
        struct = f"{len(getattr(doc, 'layers', []) or [])} layers"
        anim = "tile"

    return CompileResult(kind, doc, status, report, errors, warnings, bounds, struct, anim)


def export_artifacts(result: CompileResult, outdir: Path, slug: str) -> list:
    """Write every real export format for this kind. Returns the files written."""
    outdir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in outdir.iterdir() if p.is_file()}
    db_core.export_any(result.kind, result.doc, str(outdir), slug)
    return sorted(
        p.name for p in outdir.iterdir()
        if p.is_file() and (p.name not in before or p.name.startswith(slug))
    )


def mesh_payload(result: CompileResult) -> dict:
    """Real triangles for the viewport, straight from the compiled document.

    Returns flat float arrays so the browser can drop them into a
    THREE.BufferGeometry with no parsing and no loader dependency.
    """
    # Build the triangle soup grouped by the thing the operator actually
    # thinks in: a bone for a model, a part for a prop. The groups let the UI
    # list the components, highlight one, and scope an edit to it by name.
    groups = []   # {name, kind, start, count, color, box}
    tris = []

    def _rgba_of(spec, fallback=(210, 214, 222, 255)):
        if not spec:
            return fallback
        try:
            return db_core.parse_color(spec) or fallback
        except Exception:
            return fallback

    if result.kind == "model":
        doc = result.doc
        bones = doc.bones.values() if isinstance(doc.bones, dict) else (doc.bones or [])
        for bone in bones:
            start = len(tris)
            for bx in getattr(bone, "boxes", []) or []:
                rgba = _rgba_of(getattr(bx, "color", None))
                try:
                    for t in db_core.box_tris(bx):
                        tris.append((t, rgba))
                except Exception:
                    pass
            if len(tris) > start:
                groups.append({
                    "name": getattr(bone, "name", f"bone{len(groups)}"),
                    "kind": "bone",
                    "start": start,
                    "count": len(tris) - start,
                    "boxes": len(getattr(bone, "boxes", []) or []),
                    "parent": getattr(bone, "parent", None) or None,
                    "pivot": list(getattr(bone, "pivot", ()) or ()),
                })
        if not tris:
            # Fall back to the flat soup if the per-bone walk found nothing.
            tris = db_core.colored_tris(doc) or []
            groups = []

    elif result.kind == "prop":
        for part in getattr(result.doc, "parts", []) or []:
            start = len(tris)
            rgba = _rgba_of(getattr(part, "color", None))
            for t in part.mesh.tris:
                tris.append((t, rgba))
            # A prop part is identified in the assembly by its solid's name,
            # which is what the operator writes in an edit.
            groups.append({
                "name": getattr(part, "solid", None) or f"part{len(groups)}",
                "kind": "part",
                "start": start,
                "count": len(tris) - start,
                "solid": getattr(part, "solid", None) or None,
                "at": list(getattr(part, "at", ()) or ()),
            })

    positions, colors = [], []
    for tri, rgba in tris:
        r, g, b = (rgba[0] / 255.0, rgba[1] / 255.0, rgba[2] / 255.0)
        for vx, vy, vz in tri:
            positions.extend((round(vx, 4), round(vy, 4), round(vz, 4)))
            colors.extend((round(r, 4), round(g, 4), round(b, 4)))

    def _extent(off, cnt):
        if not cnt:
            return None
        a, b = off * 9, (off + cnt) * 9
        seg = positions[a:b]
        if not seg:
            return None
        xs, ys, zs = seg[0::3], seg[1::3], seg[2::3]
        return [[min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]]

    for g in groups:
        g["box"] = _extent(g["start"], g["count"])

    lo = hi = None
    if positions:
        xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
        lo = [min(xs), min(ys), min(zs)]
        hi = [max(xs), max(ys), max(zs)]

    # Which axis points up in this document's own coordinates. designbridge
    # revolves props about Z, so a prop is Z-up while a voxel model is Y-up.
    # The viewer needs this or props lie on their side and the floor grid
    # slices straight through them.
    return {
        "ok": True,
        "kind": result.kind,
        "triangles": len(tris),
        "positions": positions,
        "colors": colors,
        "groups": groups,
        "up": "z" if result.kind == "prop" else "y",
        "units": (getattr(result.doc, "units", "") or "") if result.kind == "prop" else "px",
        "min": lo,
        "max": hi,
    }


class StudioStorage:
    """SQLite + Disk Artifact Storage Engine for DesignBridge Studio v2."""

    def __init__(self, workspace_dir: Path):
        self.root = workspace_dir.resolve()
        self.projects_dir = self.root / "projects"
        self.db_path = self.root / "studio.db"
        self.settings_json_path = self.root / "settings.json"

        self.root.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._seed_if_empty()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    track TEXT NOT NULL,
                    icon TEXT NOT NULL,
                    active_branch TEXT NOT NULL,
                    head_commit_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS branches (
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    color TEXT NOT NULL,
                    head_commit_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, name),
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS commits (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    parent_id TEXT,
                    merge_parent_id TEXT,
                    time TEXT NOT NULL,
                    status TEXT NOT NULL,
                    note TEXT NOT NULL,
                    bounds TEXT NOT NULL,
                    struct TEXT NOT NULL,
                    anim TEXT NOT NULL,
                    errors INTEGER NOT NULL DEFAULT 0,
                    params_json TEXT NOT NULL,
                    report TEXT NOT NULL,
                    dsl TEXT NOT NULL,
                    thumbnail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    commit_id TEXT,
                    mode TEXT NOT NULL,
                    user_prompt TEXT NOT NULL,
                    engineered_prompt TEXT NOT NULL,
                    raw_ai_response TEXT NOT NULL,
                    extracted_dsl TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS ui_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # Migrate v1 DBs seamlessly if thumbnail column doesn't exist
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(commits)").fetchall()]
            if "thumbnail" not in cols:
                conn.execute("ALTER TABLE commits ADD COLUMN thumbnail TEXT NOT NULL DEFAULT ''")
            # Folders arrived later; add the column to existing workspaces
            # rather than asking anyone to start over.
            pcols = [r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
            if "folder" not in pcols:
                conn.execute("ALTER TABLE projects ADD COLUMN folder TEXT NOT NULL DEFAULT ''")

    def _seed_if_empty(self, force=False, only=None):
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM projects").fetchone()
            if row["cnt"] > 0 and not force:
                self._ensure_default_settings(conn)
                return

            ts = now_iso()
            for pid, p in SEED_PROJECTS.items():
                if only is not None and pid not in only:
                    continue
                conn.execute(
                    """INSERT INTO projects (id, name, track, icon, active_branch, head_commit_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (p["id"], p["name"], p["track"], p["icon"], p["activeBranch"], p["headCommitId"], ts, ts),
                )
                for bname, b in p["branches"].items():
                    conn.execute(
                        """INSERT INTO branches (project_id, name, color, head_commit_id, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (pid, b["name"], b["color"], b["head"], ts),
                    )
                for idx, cm in enumerate(p["commits"], start=1):
                    # Compile each seed for real on first boot, so its status,
                    # report, bounds and structure are measured rather than
                    # asserted. If a seed ever stops compiling, the operator
                    # sees the real diagnostics instead of a stale "PASS".
                    try:
                        res = compile_source(cm["dsl"])
                        status, report = res.status, res.report
                        errors, bounds, struct, anim = res.errors, res.bounds, res.struct, res.anim
                    except Exception as exc:
                        status, errors = "FAIL", 1
                        bounds, struct, anim = "-", "-", "static"
                        report = f"STATUS: FAIL\n\nERRORS\n  [ERROR] input: {exc}\n"
                    full = dict(cm, status=status, report=report, errors=errors,
                                bounds=bounds, struct=struct, anim=anim)
                    conn.execute(
                        """INSERT INTO commits (id, project_id, seq, tag, branch, parent_id, merge_parent_id,
                                                time, status, note, bounds, struct, anim, errors, params_json, report, dsl, thumbnail, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, '', ?)""",
                        (
                            cm["id"], pid, idx, cm["tag"], cm["branch"], cm.get("parentId"), cm.get("mergeParentId"),
                            cm["time"], status, cm["note"], bounds, struct, anim, errors, report, cm["dsl"], ts,
                        ),
                    )
                    self._write_commit_artifacts(pid, full)

                for msg in p.get("seedChat", []):
                    conn.execute(
                        """INSERT INTO chat_history (project_id, branch, commit_id, mode, user_prompt, engineered_prompt, raw_ai_response, extracted_dsl, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            pid, msg["branch"], msg["commitId"], msg["mode"],
                            msg["userPrompt"], msg.get("engineeredPrompt", ""), msg["rawAiResponse"],
                            self.extract_dsl(msg["rawAiResponse"]), ts,
                        ),
                    )

            self._ensure_default_settings(conn)

        for pid in SEED_PROJECTS:
            self._write_project_manifest(pid)

    # ------------------------------------------------------------------
    # REPAIR / MIGRATION
    # ------------------------------------------------------------------
    def audit(self) -> dict:
        """Recompile every stored revision and report which ones are lying.

        A workspace created by an earlier build keeps whatever that build
        wrote, because seeding is skipped on a non-empty database. Earlier
        builds stamped a hardcoded "PASS: Compiled cleanly" on revisions whose
        source the real compiler rejects outright. This finds them without
        changing anything.
        """
        bad, ok = [], 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, project_id, tag, status, dsl FROM commits ORDER BY project_id, seq"
            ).fetchall()
        for r in rows:
            try:
                res = compile_source(r["dsl"])
                truth, errs = res.status, res.errors
            except Exception as exc:
                truth, errs = "FAIL", 1
                res = None
            if truth != r["status"] or (truth == "FAIL" and r["status"] == "PASS"):
                bad.append({"project": r["project_id"], "commit": r["id"], "tag": r["tag"],
                            "stored": r["status"], "actual": truth, "errors": errs})
            else:
                ok += 1
        return {"total": len(rows), "agree": ok, "mismatched": bad}

    def repair(self, verbose=True) -> dict:
        """Rewrite every revision's verdict to what the compiler actually says.

        Only the derived columns change - status, errors, report, bounds,
        struct, anim. The source text, the notes, the branch graph and the
        prompt history are never touched, so no work is lost. A revision that
        genuinely does not compile is marked FAIL with its real diagnostics,
        which is the honest state: it was always broken, it was just labelled
        PASS.
        """
        fixed, failed, intact = 0, 0, 0
        with self._connect() as conn:
            rows = conn.execute("SELECT id, project_id, tag, status, dsl FROM commits").fetchall()
            for r in rows:
                try:
                    res = compile_source(r["dsl"])
                    status, report = res.status, res.report
                    errors, bounds, struct, anim = res.errors, res.bounds, res.struct, res.anim
                except Exception as exc:
                    status, errors = "FAIL", 1
                    bounds, struct, anim = "-", "-", "static"
                    report = ("STATUS: FAIL   1 error(s)\n\nERRORS\n"
                              f"  [ERROR] input: {exc}\n"
                              "          FIX: this revision predates the current grammar. Open the\n"
                              "               SOURCE tab, or use FIX ERR mode to have it rewritten.\n")
                changed = (status != r["status"])
                conn.execute(
                    """UPDATE commits SET status=?, errors=?, report=?, bounds=?, struct=?, anim=?
                       WHERE id=?""",
                    (status, errors, report, bounds, struct, anim, r["id"]),
                )
                if status == "FAIL":
                    failed += 1
                    if verbose and changed:
                        print(f"  {r['project_id']}/{r['tag']}: PASS -> FAIL (was mislabelled)")
                elif changed:
                    fixed += 1
                else:
                    intact += 1
        return {"checked": len(rows), "now_failing": failed, "corrected": fixed, "unchanged": intact}

    def replace_examples(self, verbose=True) -> dict:
        """Swap only the built-in demo assets for current, compiling ones.

        Matches on the seed ids alone, so anything the operator made is left
        completely alone. Their revisions are deleted and rebuilt because they
        are demo content, not work.
        """
        replaced, skipped = [], []
        seed_ids = set(SEED_PROJECTS.keys())
        with self._connect() as conn:
            present = {r["id"] for r in conn.execute("SELECT id FROM projects").fetchall()}
            for pid in seed_ids:
                if pid not in present:
                    continue
                # Only replace if it still looks like untouched demo content:
                # a seed asset nobody has added revisions to beyond the seeds.
                n = conn.execute("SELECT COUNT(*) c FROM commits WHERE project_id=?", (pid,)).fetchone()["c"]
                expected = len(SEED_PROJECTS[pid]["commits"])
                if n > expected:
                    skipped.append(f"{pid} (has {n} revisions, you've worked on it)")
                    continue
                conn.execute("DELETE FROM projects WHERE id=?", (pid,))
                replaced.append(pid)
        if replaced:
            pdir_root = self.projects_dir
            for pid in replaced:
                shutil.rmtree(pdir_root / pid, ignore_errors=True)
            self._seed_if_empty(force=True, only=set(replaced))
        if verbose:
            for p in replaced:
                print(f"  replaced demo asset: {p}")
            for s in skipped:
                print(f"  kept: {s}")
        return {"replaced": replaced, "skipped": skipped}

    def _ensure_default_settings(self, conn: sqlite3.Connection):
        ts = now_iso()
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO ui_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                (k, json.dumps(v), ts),
            )
        self._flush_settings_json(conn)

    def _flush_settings_json(self, conn: sqlite3.Connection):
        rows = conn.execute("SELECT key, value_json FROM ui_settings").fetchall()
        merged = dict(DEFAULT_SETTINGS)
        for r in rows:
            try:
                merged[r["key"]] = json.loads(r["value_json"])
            except Exception:
                pass
        # Redact API key from plain-text settings.json mirror if desired, keep in SQLite
        disk_copy = dict(merged)
        if disk_copy.get("llmApiKey"):
            disk_copy["llmApiKey"] = "***STORED_IN_SQLITE***"
        self.settings_json_path.write_text(json.dumps(disk_copy, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # SETTINGS & LAYOUT PERSISTENCE
    # ------------------------------------------------------------------
    def get_settings(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value_json FROM ui_settings").fetchall()
            out = dict(DEFAULT_SETTINGS)
            for r in rows:
                try:
                    out[r["key"]] = json.loads(r["value_json"])
                except Exception:
                    pass
            return out

    def update_settings(self, patch: dict) -> dict:
        ts = now_iso()
        with self._connect() as conn:
            for k, v in patch.items():
                conn.execute(
                    """INSERT INTO ui_settings (key, value_json, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                    (str(k), json.dumps(v), ts),
                )
            self._flush_settings_json(conn)
        return self.get_settings()

    # ------------------------------------------------------------------
    # BOOTSTRAP / FULL WORKSPACE HYDRATION
    # ------------------------------------------------------------------
    def get_bootstrap_state(self) -> dict:
        settings = self.get_settings()
        projects = {}
        with self._connect() as conn:
            p_rows = conn.execute("SELECT * FROM projects ORDER BY created_at ASC").fetchall()
            for p in p_rows:
                pid = p["id"]
                b_rows = conn.execute("SELECT * FROM branches WHERE project_id=? ORDER BY created_at ASC", (pid,)).fetchall()
                branches = {
                    b["name"]: {"name": b["name"], "color": b["color"], "head": b["head_commit_id"]}
                    for b in b_rows
                }
                c_rows = conn.execute("SELECT * FROM commits WHERE project_id=? ORDER BY seq ASC", (pid,)).fetchall()
                commits = []
                for c in c_rows:
                    commits.append({
                        "id": c["id"],
                        "tag": c["tag"],
                        "branch": c["branch"],
                        "parentId": c["parent_id"],
                        "mergeParentId": c["merge_parent_id"],
                        "time": c["time"],
                        "status": c["status"],
                        "note": c["note"],
                        "bounds": c["bounds"],
                        "struct": c["struct"],
                        "anim": c["anim"],
                        "errors": c["errors"],
                        "params": json.loads(c["params_json"] or "{}"),
                        "report": c["report"],
                        "dsl": c["dsl"],
                        "thumbnail": c["thumbnail"] or "",
                    })
                h_rows = conn.execute(
                    "SELECT * FROM chat_history WHERE project_id=? ORDER BY id DESC LIMIT 150", (pid,)
                ).fetchall()
                chat_history = [
                    {
                        "id": h["id"],
                        "branch": h["branch"],
                        "commitId": h["commit_id"],
                        "mode": h["mode"],
                        "userPrompt": h["user_prompt"],
                        "engineeredPrompt": h["engineered_prompt"],
                        "rawAiResponse": h["raw_ai_response"],
                        "extractedDsl": h["extracted_dsl"],
                        "createdAt": h["created_at"],
                    }
                    for h in h_rows
                ]
                projects[pid] = {
                    "id": pid,
                    "name": p["name"],
                    "track": p["track"],
                    "icon": p["icon"],
                    "folder": (p["folder"] if "folder" in p.keys() else "") or "",
                    "activeBranch": p["active_branch"],
                    "headCommitId": p["head_commit_id"],
                    "branches": branches,
                    "commits": commits,
                    "chatHistory": chat_history,
                }

        return {
            "ok": True,
            "engine": "DESIGNBRIDGE_CORE" if HAS_DESIGNBRIDGE else "SQLITE_DISK_ENGINE",
            "dbPath": str(self.db_path),
            "workspaceDir": str(self.root),
            "settings": settings,
            "projects": projects,
        }

    # ------------------------------------------------------------------
    # DSL EXTRACTION, COMPILATION & ARTIFACT WRITER
    # ------------------------------------------------------------------
    @staticmethod
    def extract_dsl(raw_text: str) -> str:
        if not raw_text:
            return ""
        return strip_sentinels(raw_text).rstrip()

    def _compile_dsl(self, track: str, dsl: str, parent_commit: dict) -> CompileResult:
        """Compile a revision for real. No guessing, no fabricated PASS.

        The previous build only reached designbridge when the source did NOT
        carry the <<<DESIGN>>> sentinels - which is every revision the UI
        produces - so in practice the compiler never ran and every commit was
        stamped with a hardcoded "compiled cleanly" report. compile_source()
        strips the sentinels itself and always runs the real compiler, so a
        broken design now says so and carries the real diagnostics.
        """
        return compile_source(dsl)

    def _save_thumbnail_file(self, project_id: str, commit_id: str, data_url: str) -> str:
        if not data_url or not data_url.startswith("data:image/png;base64,"):
            return ""
        try:
            b64 = data_url.split(",", 1)[1]
            raw = base64.b64decode(b64)
            cdir = self.projects_dir / project_id / "commits" / commit_id
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "thumb.png").write_bytes(raw)
            return f"/api/thumb/{project_id}/{commit_id}"
        except Exception:
            return ""

    def _write_commit_artifacts(self, project_id: str, commit: dict):
        """Persist the revision and every real export designbridge can make.

        The exports are the genuine article - a real binary glTF, a real OBJ,
        a real texture PNG and the engine-native JSON - because they come
        from designbridge.export_any() on the compiled document. Nothing here
        synthesises a placeholder.
        """
        cdir = self.projects_dir / project_id / "commits" / commit["id"]
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "source.dsl").write_text(commit["dsl"], encoding="utf-8")
        (cdir / "report.txt").write_text(commit["report"], encoding="utf-8")
        (cdir / "meta.json").write_text(json.dumps(commit, indent=2), encoding="utf-8")

        if not commit.get("dsl", "").strip() or db_core is None:
            return
        try:
            result = compile_source(commit["dsl"])
        except Exception as exc:
            (cdir / "export_error.txt").write_text(str(exc), encoding="utf-8")
            return
        if not result.ok:
            # A failing design still keeps its source and report; there is just
            # nothing valid to export, and saying so beats writing a fake file.
            (cdir / "export_error.txt").write_text(
                "This revision did not compile, so no asset was exported.\n"
                "See report.txt for the diagnostics.\n",
                encoding="utf-8",
            )
            return
        try:
            export_artifacts(result, cdir / "exports", self._slug_for(project_id, commit))
            payload = mesh_payload(result)
            (cdir / "mesh.json").write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:
            (cdir / "export_error.txt").write_text(f"Export failed: {exc}\n", encoding="utf-8")

    @staticmethod
    def _slug_for(project_id: str, commit: dict) -> str:
        return safe_slug(f"{project_id}_{commit.get('tag') or commit.get('id')}")

    def _write_project_manifest(self, project_id: str):
        boot = self.get_bootstrap_state()
        proj = boot["projects"].get(project_id)
        if not proj:
            return
        pdir = self.projects_dir / project_id
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "manifest.json").write_text(json.dumps(proj, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # PROJECT CRUD, BRANCH, COMMIT & CHAT HISTORY MUTATIONS
    # ------------------------------------------------------------------
    def create_project(self, payload: dict) -> dict:
        # The name is optional. designbridge documents carry their own name
        # (model "stalker"), so an unnamed asset gets a placeholder now and
        # adopts the real name from the document on first successful compile.
        raw_name = (payload.get("name") or "").strip()
        track = payload.get("track") or "model"
        auto_named = not raw_name
        if auto_named:
            raw_name = f"untitled_{track}_{uuid.uuid4().hex[:4]}"
        pid = safe_slug(raw_name)
        folder = (payload.get("folder") or "").strip()
        brief = (payload.get("brief") or f"Create a clean {track} asset named {pid}").strip()
        icon_map = {"model": "view_in_ar", "prop": "precision_manufacturing", "texture": "texture"}
        icon = icon_map.get(track, "view_in_ar")
        cid = "c" + uuid.uuid4().hex[:6]
        ts = now_iso()

        with self._connect() as conn:
            exists = conn.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
            if exists:
                pid = f"{pid}_{uuid.uuid4().hex[:4]}"

            conn.execute(
                """INSERT INTO projects (id, name, track, icon, active_branch, head_commit_id, created_at, updated_at, folder)
                   VALUES (?, ?, ?, ?, 'main', ?, ?, ?, ?)""",
                (pid, pid, track, icon, cid, ts, ts, folder),
            )
            conn.execute(
                "INSERT INTO branches (project_id, name, color, head_commit_id, created_at) VALUES (?, 'main', '#d4d4d8', ?, ?)",
                (pid, cid, ts),
            )
            # A new project opens on a real, compiling starter document rather
            # than a stub, so the viewport has something to show and the
            # operator can see valid grammar before the first AI round.
            starter = STARTER_SOURCES.get(track, STARTER_SOURCES["model"]).replace("__NAME__", pid)
            try:
                res = compile_source(starter)
                status, report = res.status, res.report
                errors, bounds, struct, anim = res.errors, res.bounds, res.struct, res.anim
            except Exception as exc:
                status, errors = "FAIL", 1
                bounds, struct, anim = "-", "-", "static"
                report = f"STATUS: FAIL\n\nERRORS\n  [ERROR] input: {exc}\n"

            init_commit = {
                "id": cid, "tag": "v1", "branch": "main", "parentId": None, "mergeParentId": None,
                "time": now_hhmm(), "status": status,
                "note": f"Starter {track} - brief: {brief[:80]}",
                "bounds": bounds, "struct": struct, "anim": anim, "errors": errors,
                "params": {},
                "report": report,
                "dsl": starter,
                "thumbnail": "",
            }
            conn.execute(
                """INSERT INTO commits (id, project_id, seq, tag, branch, parent_id, merge_parent_id,
                                        time, status, note, bounds, struct, anim, errors, params_json, report, dsl, thumbnail, created_at)
                   VALUES (?, ?, 1, ?, 'main', NULL, NULL, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, '', ?)""",
                (
                    cid, pid, init_commit["tag"], init_commit["time"], status,
                    init_commit["note"], bounds, struct, anim, errors,
                    report, starter, ts,
                ),
            )
            conn.execute(
                """INSERT INTO chat_history (project_id, branch, commit_id, mode, user_prompt, engineered_prompt, raw_ai_response, extracted_dsl, created_at)
                   VALUES (?, 'main', ?, 'new_take', ?, ?, '', ?, ?)""",
                (pid, cid, brief, f"[NEW DESIGN BRIEF] {brief}", init_commit["dsl"], ts),
            )
            self._write_commit_artifacts(pid, init_commit)

        self.update_settings({"activeProjectId": pid, "activeMode": "new_take"})
        self._write_project_manifest(pid)
        return self.get_bootstrap_state()

    def rename_project(self, project_id: str, new_name: str, folder=None) -> dict:
        ts = now_iso()
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("Project not found")
            if new_name:
                conn.execute("UPDATE projects SET name=?, updated_at=? WHERE id=?",
                             (safe_slug(new_name), ts, project_id))
            if folder is not None:
                conn.execute("UPDATE projects SET folder=?, updated_at=? WHERE id=?",
                             (str(folder).strip(), ts, project_id))
        self._write_project_manifest(project_id)
        return self.get_bootstrap_state()

    def duplicate_project(self, project_id: str) -> dict:
        ts = now_iso()
        with self._connect() as conn:
            p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not p:
                raise ValueError("Project not found")
            new_pid = f"{project_id}_copy_{uuid.uuid4().hex[:4]}"
            commits = conn.execute("SELECT * FROM commits WHERE project_id=? ORDER BY seq ASC", (project_id,)).fetchall()
            branches = conn.execute("SELECT * FROM branches WHERE project_id=?", (project_id,)).fetchall()
            chats = conn.execute("SELECT * FROM chat_history WHERE project_id=? ORDER BY id ASC", (project_id,)).fetchall()

            id_map = {c["id"]: "c" + uuid.uuid4().hex[:6] for c in commits}
            new_head = id_map.get(p["head_commit_id"], p["head_commit_id"])
            conn.execute(
                """INSERT INTO projects (id, name, track, icon, active_branch, head_commit_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_pid, f"{p['name']}_copy", p["track"], p["icon"], p["active_branch"], new_head, ts, ts),
            )
            for b in branches:
                conn.execute(
                    "INSERT INTO branches (project_id, name, color, head_commit_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (new_pid, b["name"], b["color"], id_map.get(b["head_commit_id"], new_head), ts),
                )
            for c in commits:
                new_cid = id_map[c["id"]]
                conn.execute(
                    """INSERT INTO commits (id, project_id, seq, tag, branch, parent_id, merge_parent_id,
                                            time, status, note, bounds, struct, anim, errors, params_json, report, dsl, thumbnail, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)""",
                    (
                        new_cid, new_pid, c["seq"], c["tag"], c["branch"],
                        id_map.get(c["parent_id"]), id_map.get(c["merge_parent_id"]),
                        c["time"], c["status"], c["note"], c["bounds"], c["struct"], c["anim"],
                        c["errors"], c["params_json"], c["report"], c["dsl"], ts,
                    ),
                )
                self._write_commit_artifacts(new_pid, {
                    "id": new_cid, "tag": c["tag"], "branch": c["branch"], "params": json.loads(c["params_json"] or "{}"),
                    "report": c["report"], "dsl": c["dsl"],
                })
            for h in chats:
                conn.execute(
                    """INSERT INTO chat_history (project_id, branch, commit_id, mode, user_prompt, engineered_prompt, raw_ai_response, extracted_dsl, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        new_pid, h["branch"], id_map.get(h["commit_id"]), h["mode"],
                        h["user_prompt"], h["engineered_prompt"], h["raw_ai_response"], h["extracted_dsl"], ts,
                    ),
                )

        self.update_settings({"activeProjectId": new_pid})
        self._write_project_manifest(new_pid)
        return self.get_bootstrap_state()

    def delete_project(self, project_id: str) -> dict:
        with self._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) AS cnt FROM projects").fetchone()["cnt"]
            if cnt <= 1:
                raise ValueError("Cannot delete the last remaining project")
            conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            next_p = conn.execute("SELECT id FROM projects ORDER BY updated_at DESC LIMIT 1").fetchone()
            next_pid = next_p["id"] if next_p else ""

        pdir = self.projects_dir / project_id
        if pdir.exists():
            shutil.rmtree(pdir, ignore_errors=True)
        if next_pid:
            self.update_settings({"activeProjectId": next_pid})
        return self.get_bootstrap_state()

    def checkout_commit(self, project_id: str, commit_id: str) -> dict:
        ts = now_iso()
        with self._connect() as conn:
            c = conn.execute("SELECT * FROM commits WHERE project_id=? AND id=?", (project_id, commit_id)).fetchone()
            if not c:
                raise ValueError("Commit not found")
            conn.execute(
                "UPDATE projects SET active_branch=?, head_commit_id=?, updated_at=? WHERE id=?",
                (c["branch"], c["id"], ts, project_id),
            )
        self.update_settings({"activeProjectId": project_id})
        self._write_project_manifest(project_id)
        return self.get_bootstrap_state()

    def fork_branch(self, project_id: str, source_commit_id: str, branch_name: str) -> dict:
        clean_branch = re.sub(r"\s+", "-", (branch_name or "").strip().lower())
        if not clean_branch:
            raise ValueError("Branch name required")
        palette = ["#10b981", "#38bdf8", "#f59e0b", "#e879f9", "#fb7185"]
        ts = now_iso()

        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM branches WHERE project_id=? AND name=?", (project_id, clean_branch)).fetchone():
                raise ValueError(f"Branch '{clean_branch}' already exists")
            src = conn.execute("SELECT * FROM commits WHERE project_id=? AND id=?", (project_id, source_commit_id)).fetchone()
            if not src:
                raise ValueError("Source commit not found")
            b_cnt = conn.execute("SELECT COUNT(*) AS cnt FROM branches WHERE project_id=?", (project_id,)).fetchone()["cnt"]
            seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS s FROM commits WHERE project_id=?", (project_id,)).fetchone()["s"]
            color = palette[b_cnt % len(palette)]
            new_id = "c" + uuid.uuid4().hex[:6]
            new_tag = f"{src['tag']}-{clean_branch.split('/')[-1][:4]}1"

            commit_obj = {
                "id": new_id, "tag": new_tag, "branch": clean_branch,
                "parentId": src["id"], "mergeParentId": None,
                "time": now_hhmm(), "status": src["status"],
                "note": f"Branched '{clean_branch}' from {src['tag']}",
                "bounds": src["bounds"], "struct": src["struct"], "anim": src["anim"],
                "errors": src["errors"], "params": json.loads(src["params_json"] or "{}"),
                "report": src["report"], "dsl": src["dsl"], "thumbnail": src["thumbnail"] or "",
            }
            conn.execute(
                """INSERT INTO commits (id, project_id, seq, tag, branch, parent_id, merge_parent_id,
                                        time, status, note, bounds, struct, anim, errors, params_json, report, dsl, thumbnail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id, project_id, seq, new_tag, clean_branch, src["id"],
                    commit_obj["time"], commit_obj["status"], commit_obj["note"],
                    commit_obj["bounds"], commit_obj["struct"], commit_obj["anim"],
                    commit_obj["errors"], json.dumps(commit_obj["params"]), commit_obj["report"],
                    commit_obj["dsl"], commit_obj["thumbnail"], ts,
                ),
            )
            conn.execute(
                "INSERT INTO branches (project_id, name, color, head_commit_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, clean_branch, color, new_id, ts),
            )
            conn.execute(
                "UPDATE projects SET active_branch=?, head_commit_id=?, updated_at=? WHERE id=?",
                (clean_branch, new_id, ts, project_id),
            )
            self._write_commit_artifacts(project_id, commit_obj)

        self._write_project_manifest(project_id)
        return self.get_bootstrap_state()

    def merge_branch(self, project_id: str, source_commit_id: str) -> dict:
        ts = now_iso()
        with self._connect() as conn:
            src = conn.execute("SELECT * FROM commits WHERE project_id=? AND id=?", (project_id, source_commit_id)).fetchone()
            main_br = conn.execute("SELECT * FROM branches WHERE project_id=? AND name='main'", (project_id,)).fetchone()
            if not src or not main_br:
                raise ValueError("Source commit or main branch missing")
            if src["branch"] == "main":
                raise ValueError("Commit is already on main")

            main_count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM commits WHERE project_id=? AND branch='main'", (project_id,)
            ).fetchone()["cnt"]
            seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS s FROM commits WHERE project_id=?", (project_id,)).fetchone()["s"]
            new_id = "m" + uuid.uuid4().hex[:6]
            new_tag = f"v{main_count + 1}"

            merged = {
                "id": new_id, "tag": new_tag, "branch": "main",
                "parentId": main_br["head_commit_id"], "mergeParentId": src["id"],
                "time": now_hhmm(), "status": src["status"],
                "note": f"Merged {src['branch']} ({src['tag']}) into main",
                "bounds": src["bounds"], "struct": src["struct"], "anim": src["anim"],
                "errors": src["errors"], "params": json.loads(src["params_json"] or "{}"),
                "report": src["report"], "dsl": src["dsl"], "thumbnail": src["thumbnail"] or "",
            }
            conn.execute(
                """INSERT INTO commits (id, project_id, seq, tag, branch, parent_id, merge_parent_id,
                                        time, status, note, bounds, struct, anim, errors, params_json, report, dsl, thumbnail, created_at)
                   VALUES (?, ?, ?, ?, 'main', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id, project_id, seq, new_tag, merged["parentId"], merged["mergeParentId"],
                    merged["time"], merged["status"], merged["note"], merged["bounds"],
                    merged["struct"], merged["anim"], merged["errors"],
                    json.dumps(merged["params"]), merged["report"], merged["dsl"], merged["thumbnail"], ts,
                ),
            )
            conn.execute("UPDATE branches SET head_commit_id=? WHERE project_id=? AND name='main'", (new_id, project_id))
            conn.execute("UPDATE projects SET active_branch='main', head_commit_id=?, updated_at=? WHERE id=?", (new_id, ts, project_id))
            self._write_commit_artifacts(project_id, merged)

        self._write_project_manifest(project_id)
        return self.get_bootstrap_state()

    def compile_and_save(self, payload: dict) -> dict:
        project_id = payload["projectId"]
        raw_response = payload.get("rawAiResponse", "")
        user_prompt = payload.get("userPrompt", "")
        engineered_prompt = payload.get("engineeredPrompt", "")
        mode = payload.get("mode", "edit")
        style_lock_at_compile = (payload.get("styleLock") or "").strip()
        note = (payload.get("note") or user_prompt[:52] or "Compiled AI DSL revision").strip()
        auto_fork = bool(payload.get("autoFork", False))
        thumb_data_url = payload.get("thumbnail", "")

        dsl = self.extract_dsl(raw_response)
        if not dsl:
            raise ValueError("No DSL content found in AI response")

        ts = now_iso()
        with self._connect() as conn:
            proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not proj:
                raise ValueError("Project not found")
            seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS s FROM commits WHERE project_id=?", (project_id,)).fetchone()["s"]
            target_branch = proj["active_branch"]
            if auto_fork:
                palette = ["#10b981", "#38bdf8", "#f59e0b", "#e879f9", "#fb7185"]
                b_cnt = conn.execute("SELECT COUNT(*) AS cnt FROM branches WHERE project_id=?", (project_id,)).fetchone()["cnt"]
                target_branch = f"iter/{seq}"
                conn.execute(
                    "INSERT INTO branches (project_id, name, color, head_commit_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (project_id, target_branch, palette[b_cnt % len(palette)], proj["head_commit_id"], ts),
                )

            # Real compile. Bounds, structure and status all come from the
            # compiled document, so the diagnostics panel reports measured
            # geometry instead of copying the parent commit's numbers forward.
            try:
                result = self._compile_dsl(proj["track"], dsl, None)
                status, report = result.status, result.report
                errors, bounds, struct, anim = result.errors, result.bounds, result.struct, result.anim
                # Re-measure against the declared shape budget and append the
                # findings to the report. Style issues never change PASS/FAIL -
                # they are the operator's own budget, not a compiler error.
                style = normalize_style(payload.get("style"))
                try:
                    issues = check_style(result, style)
                    # Attributes the compiler quietly dropped. Reported even
                    # when style enforcement is off, because this is not a
                    # budget preference - it is work that silently did nothing.
                    ghosts = check_unknown_keys(dsl)
                    report = report.rstrip() + "\n" + style_report_block(issues, style)
                    if ghosts:
                        report += ("\nIGNORED ATTRIBUTES - the parser accepted these and "
                                   "then discarded them:\n")
                        for sev, msg, fix in ghosts:
                            report += f"  [{sev}] {msg}\n         WHY: {fix}\n"
                except Exception as exc:
                    report = report.rstrip() + f"\n\nSTUDIO STYLE CHECK\n  could not run: {exc}\n"
            except Exception as exc:
                status, errors = "FAIL", 1
                bounds, struct, anim = "-", "-", "static"
                report = (
                    "STATUS: FAIL   1 error(s)\n\nERRORS\n"
                    f"  [ERROR] input: {exc}\n"
                    "          FIX: check the document against the grammar in the Step 1 prompt\n"
                )

            br_commits = conn.execute(
                "SELECT COUNT(*) AS cnt FROM commits WHERE project_id=? AND branch=?", (project_id, target_branch)
            ).fetchone()["cnt"]
            tag = f"v{br_commits + 1}" if target_branch == "main" else f"{target_branch.split('/')[-1][:4]}-{br_commits + 1}"
            new_id = "c" + uuid.uuid4().hex[:6]
            thumb_url = self._save_thumbnail_file(project_id, new_id, thumb_data_url)

            commit_obj = {
                "id": new_id, "tag": tag, "branch": target_branch,
                "parentId": proj["head_commit_id"], "mergeParentId": None,
                "time": now_hhmm(), "status": status, "note": note,
                "bounds": bounds, "struct": struct,
                "anim": anim, "errors": errors,
                "params": {}, "report": report, "dsl": dsl, "thumbnail": thumb_url,
            }

            conn.execute(
                """INSERT INTO commits (id, project_id, seq, tag, branch, parent_id, merge_parent_id,
                                        time, status, note, bounds, struct, anim, errors, params_json, report, dsl, thumbnail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id, project_id, seq, tag, target_branch, proj["head_commit_id"],
                    commit_obj["time"], status, note, commit_obj["bounds"], commit_obj["struct"],
                    commit_obj["anim"], errors, "{}", report, dsl, thumb_url, ts,
                ),
            )
            conn.execute(
                "UPDATE branches SET head_commit_id=? WHERE project_id=? AND name=?",
                (new_id, project_id, target_branch),
            )
            conn.execute(
                "UPDATE projects SET active_branch=?, head_commit_id=?, updated_at=? WHERE id=?",
                (target_branch, new_id, ts, project_id),
            )
            conn.execute(
                """INSERT INTO chat_history (project_id, branch, commit_id, mode, user_prompt, engineered_prompt, raw_ai_response, extracted_dsl, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (project_id, target_branch, new_id, mode, user_prompt, engineered_prompt, raw_response, dsl, ts),
            )
            self._write_commit_artifacts(project_id, commit_obj)

            # An asset created without a name adopts the one the AI wrote into
            # the document, so nobody has to invent a name before the thing
            # exists. Only ever applied while the name is still a placeholder.
            if status == "PASS" and str(proj["name"]).startswith("untitled_"):
                try:
                    doc_name = (getattr(result.doc, "name", "") or "").strip()
                except Exception:
                    doc_name = ""
                if doc_name:
                    nice = safe_slug(doc_name)
                    taken = {r["name"] for r in conn.execute(
                        "SELECT name FROM projects WHERE id != ?", (project_id,)).fetchall()}
                    if nice in taken:
                        nice = f"{nice}_{uuid.uuid4().hex[:3]}"
                    conn.execute("UPDATE projects SET name=?, updated_at=? WHERE id=?",
                                 (nice, ts, project_id))

        self._write_project_manifest(project_id)
        return self.get_bootstrap_state()

    def get_thumbnail_bytes(self, project_id: str, commit_id: str) -> bytes | None:
        f = self.projects_dir / project_id / "commits" / commit_id / "thumb.png"
        return f.read_bytes() if f.exists() else None

    MIME = {
        "glb": "model/gltf-binary",
        "obj": "text/plain; charset=utf-8",
        "stl": "model/stl",
        "png": "image/png",
        "json": "application/json",
        "svg": "image/svg+xml",
        "db": "text/plain; charset=utf-8",
    }

    def _commit_row(self, project_id: str, commit_id: str) -> dict:
        with self._connect() as conn:
            c = conn.execute(
                "SELECT * FROM commits WHERE project_id=? AND id=?", (project_id, commit_id)
            ).fetchone()
        if not c:
            raise ValueError("Commit not found")
        return dict(c)

    def real_exports(self, project_id: str, commit: dict) -> dict:
        """Build this revision's real exports and return {suffix: (name, bytes)}.

        Compiled fresh from the stored source every time, so the download can
        never drift from what the report says. Which formats exist depends on
        the kind: a texture has no .glb, a prop has no .boxmodel.json.
        """
        result = compile_source(commit["dsl"])
        if not result.ok:
            raise ValueError(
                "This revision does not compile, so there is no asset to export. "
                "Open the REPORT tab, fix the errors, and compile again."
            )
        slug = self._slug_for(project_id, commit)
        cdir = self.projects_dir / project_id / "commits" / commit["id"] / "exports"
        if cdir.exists():
            shutil.rmtree(cdir, ignore_errors=True)
        export_artifacts(result, cdir, slug)
        return {p.name: p.read_bytes() for p in sorted(cdir.iterdir()) if p.is_file()}

    def build_export_bytes(self, project_id: str, commit_id: str, fmt: str) -> tuple[bytes, str, str]:
        commit = self._commit_row(project_id, commit_id)
        fmt = (fmt or "").lower()
        base = self._slug_for(project_id, commit)

        if fmt == "dsl":
            return commit["dsl"].encode("utf-8"), "text/plain; charset=utf-8", f"{base}.db"
        if fmt == "report":
            return commit["report"].encode("utf-8"), "text/plain; charset=utf-8", f"{base}_report.txt"

        files = self.real_exports(project_id, commit)

        if fmt == "zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(f"{base}/source.db", commit["dsl"])
                zf.writestr(f"{base}/report.txt", commit["report"])
                zf.writestr(f"{base}/commit.json", json.dumps(commit, indent=2))
                for name, blob in files.items():
                    zf.writestr(f"{base}/{name}", blob)
                thumb = self.get_thumbnail_bytes(project_id, commit_id)
                if thumb:
                    zf.writestr(f"{base}/viewport.png", thumb)
            return buf.getvalue(), "application/zip", f"{base}_bundle.zip"

        # Pick the file whose extension the caller asked for. "json" prefers the
        # engine-native export (boxmodel/recipe) over the preview metadata.
        wanted = [n for n in files if n.lower().endswith("." + fmt)]
        if fmt == "json":
            wanted.sort(key=lambda n: ("boxmodel" not in n and "recipe" not in n, n))
        if not wanted:
            have = ", ".join(sorted(files)) or "none"
            raise ValueError(
                f"This {compile_source(commit['dsl']).kind} revision has no .{fmt} export. "
                f"Available: {have}"
            )
        name = wanted[0]
        return files[name], self.MIME.get(fmt, "application/octet-stream"), name

    def mesh_json(self, project_id: str, commit_id: str) -> dict:
        """Real triangles for the viewport, or an honest reason there are none."""
        commit = self._commit_row(project_id, commit_id)
        if not (commit["dsl"] or "").strip():
            return {"ok": False, "error": "This revision has no source yet.", "kind": "none"}
        try:
            result = compile_source(commit["dsl"])
        except Exception as exc:
            return {"ok": False, "error": str(exc), "kind": "none"}
        if not result.ok:
            return {
                "ok": False,
                "kind": result.kind,
                "error": f"{result.errors} compile error(s) - nothing to display.",
            }
        if result.kind == "texture":
            return {"ok": True, "kind": "texture", "triangles": 0,
                    "size": getattr(result.doc, "size", 0),
                    "preview": f"/api/preview/{project_id}/{commit_id}"}
        return mesh_payload(result)

    def preview_png(self, project_id: str, commit_id: str) -> bytes | None:
        """designbridge's own rendered preview for this revision."""
        commit = self._commit_row(project_id, commit_id)
        try:
            result = compile_source(commit["dsl"])
            if not result.ok:
                return None
            cdir = self.projects_dir / project_id / "commits" / commit["id"] / "preview"
            cdir.mkdir(parents=True, exist_ok=True)
            slug = self._slug_for(project_id, commit)
            db_core.preview_any(result.kind, result.doc, str(cdir / f"{slug}.png"))
            f = cdir / f"{slug}.png"
            return f.read_bytes() if f.exists() else None
        except Exception:
            return None

    def build_repo_zip(self, project_id: str) -> tuple[bytes, str, str]:
        boot = self.get_bootstrap_state()
        proj = boot["projects"].get(project_id)
        if not proj:
            raise ValueError("Project not found")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{project_id}/manifest.json", json.dumps(proj, indent=2))
            zf.writestr(f"{project_id}/chat_history.json", json.dumps(proj.get("chatHistory", []), indent=2))
            for cm in proj["commits"]:
                cbase = f"{project_id}/commits/{cm['tag']}_{cm['id']}"
                zf.writestr(f"{cbase}/source.db", cm["dsl"])
                zf.writestr(f"{cbase}/report.txt", cm["report"])
                # Real exports per revision. A revision that never compiled
                # contributes a note instead of a fabricated mesh.
                try:
                    for name, blob in self.real_exports(project_id, cm).items():
                        zf.writestr(f"{cbase}/{name}", blob)
                except Exception as exc:
                    zf.writestr(f"{cbase}/NO_EXPORT.txt", f"{exc}\n")
                thumb = self.get_thumbnail_bytes(project_id, cm["id"])
                if thumb:
                    zf.writestr(f"{cbase}/viewport.png", thumb)
        return buf.getvalue(), "application/zip", f"{project_id}_full_repo.zip"

    # ------------------------------------------------------------------
    # OPTIONAL DIRECT LLM RELAY (OpenAI-Compatible / Ollama / Gemini)
    # ------------------------------------------------------------------
    def run_direct_llm(self, prompt_text: str) -> dict:
        settings = self.get_settings()
        provider = (settings.get("llmProvider") or "ollama").lower()
        endpoint = (settings.get("llmEndpoint") or "").strip()
        model = (settings.get("llmModel") or "").strip()
        api_key = (settings.get("llmApiKey") or "").strip()

        if not endpoint:
            raise ValueError("LLM endpoint URL is not configured in Studio Settings.")

        headers = {"Content-Type": "application/json"}
        if provider == "gemini":
            url = endpoint
            if "{model}" in url:
                url = url.replace("{model}", model or "gemini-2.5-flash")
            if api_key and "key=" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}key={api_key}"
            body = json.dumps({"contents": [{"parts": [{"text": prompt_text}]}]}).encode("utf-8")
        else:
            url = endpoint
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            body = json.dumps({
                "model": model or "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "You are a precision CAD DSL generator. Return valid <<<DESIGN ...>>> blocks."},
                    {"role": "user", "content": prompt_text},
                ],
                "temperature": 0.35,
            }).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")[:400]
            raise ValueError(f"LLM HTTP {exc.code}: {err_body}")
        except Exception as exc:
            raise ValueError(f"LLM connection failed: {exc}")

        if provider == "gemini":
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                text = json.dumps(data)
        else:
            try:
                text = data["choices"][0]["message"]["content"]
            except Exception:
                text = data.get("response") or json.dumps(data)

        return {"ok": True, "rawAiResponse": text}


# ======================================================================
# SHAPE STYLE CONTROLS
# ----------------------------------------------------------------------
# A declared budget on how elaborate an asset may be, and how varied its
# forms are allowed to get. These are not hints: the numbers go into the
# prompt as hard limits, and check_style() re-measures the compiled
# document afterwards and reports anything that broke them. designbridge
# is untouched - the enforcement lives here, in Studio's own lint layer,
# and is reported separately from the compiler's own diagnostics.
# ======================================================================

STYLE_DEFAULTS = {
    "complexity": 3,        # 1..5  overall budget
    "symmetry": "none",     # none | mirror-x | mirror-z | radial
    "proportions": "mixed",  # cubic | mixed | elongated
    "detail": "medium",     # low | medium | high  (guidance only)
    "enforce": True,        # report violations after compile
}

# Budgets per complexity step, per track.
# (total, per-bone). The model ceiling was far too low for a boss: a 15-bone
# boss at complexity 5 could use 14 boxes on one bone but only 80 overall, so
# the per-bone figure was unreachable and the total was the real cap. Totals
# now scale with the bone count (see model_budget), and the ceiling is high
# enough for a genuine boss.
BUDGET = {
    "model":   {1: (8, 2),  2: (20, 4), 3: (48, 7), 4: (120, 12), 5: (320, 20)},
    "prop":    {1: (1, 1),  2: (2, 1),  3: (3, 2),  4: (5, 3),  5: (8, 4)},
    "texture": {1: (3, 0),  2: (4, 0),  3: (6, 0),  4: (9, 0),  5: (14, 0)},
}
# Largest allowed long:short ratio on a single box.
ASPECT_CAP = {"cubic": 2.0, "mixed": 4.0, "elongated": 0.0}  # 0 = uncapped

DETAIL_NOTE = {
    "low": "Read as a silhouette from ten blocks away. Big forms only, no trim.",
    "medium": "Clear silhouette plus a few reading details. No fine filigree.",
    "high": "Dense surface detail is welcome, as long as the budget above allows it.",
}


def normalize_style(raw) -> dict:
    s = dict(STYLE_DEFAULTS)
    if isinstance(raw, dict):
        for k in s:
            if k in raw and raw[k] is not None:
                s[k] = raw[k]
    try:
        s["complexity"] = max(1, min(5, int(s["complexity"])))
    except Exception:
        s["complexity"] = 3
    if s["symmetry"] not in ("none", "mirror-x", "mirror-z", "radial"):
        s["symmetry"] = "none"
    if s["proportions"] not in ASPECT_CAP:
        s["proportions"] = "mixed"
    if s["detail"] not in DETAIL_NOTE:
        s["detail"] = "medium"
    s["enforce"] = bool(s["enforce"])
    return s


def model_budget(style: dict, bones: int = 0) -> tuple:
    """Box budget for a model, scaled by how many bones it actually has.

    A flat total punishes articulation: a 15-bone boss needs more boxes than a
    4-bone prop at the same level of detail per part. The per-bone figure is
    the real control; the total is a ceiling derived from it.
    """
    lvl = normalize_style(style)["complexity"]
    base_total, per = BUDGET["model"][lvl]
    if bones > 0:
        derived = per * bones
        return max(base_total, derived), per
    return base_total, per


def size_budget_note(kind: str, style: dict, brief: str) -> str:
    """If the brief names a voxel size, say what that implies for the budget.

    A target like 144x152x192 is a volume the boxes have to cover. Stating the
    relationship stops the model from either pebble-dashing the shape with
    tiny boxes or trying to fill it with four.
    """
    if kind != "model" or not brief:
        return ""
    m = re.search(r"(\d{2,4})\s*[x\u00d7]\s*(\d{2,4})\s*[x\u00d7]\s*(\d{2,4})", brief)
    if not m:
        return ""
    dx, dy, dz = (int(g) for g in m.groups())
    lvl = normalize_style(style)["complexity"]
    total, per = BUDGET["model"][lvl]
    longest = max(dx, dy, dz)
    return (
        f"  SIZE TARGET: the brief says {dx}x{dy}x{dz} voxels. That is the envelope the\n"
        f"  boxes must fill, not a box count. With about {total} boxes to spend, each one\n"
        f"  averages roughly {max(1, longest // max(4, int(total ** (1/3)) * 2))} voxels on its longest side - so build with large\n"
        "  masses and add detail only where it reads. If you cannot cover the envelope\n"
        "  within the budget, say so in your plan line and cover it with fewer, bigger\n"
        "  boxes rather than silently going over.\n"
    )


def style_constraints_text(kind: str, style: dict, bones: int = 0, brief: str = "") -> str:
    """The style, written as limits a model can actually obey."""
    s = normalize_style(style)
    lvl = s["complexity"]
    if kind == "model":
        total, per = model_budget(style, bones)
    else:
        total, per = BUDGET.get(kind, BUDGET["model"])[lvl]
    out = [f"SHAPE BUDGET - complexity {lvl} of 5. These are limits, not suggestions."]

    if kind == "model":
        out.append(f"  At most {per} boxes per bone. Across the whole model, at most {total}.")
        if bones:
            out.append(f"  (Counted against the {bones} bone(s) the current design has. Adding bones")
            out.append("  raises the total, so articulate first and detail second.)")
        out.append("  Spend them on masses that read, not on filler.")
    elif kind == "prop":
        out.append(f"  Use at most {total} solids and at most {per} profiles beyond the first.")
    else:
        out.append(f"  Use at most {total} layers.")

    cap = ASPECT_CAP[s["proportions"]]
    if kind in ("model", "prop"):
        if s["proportions"] == "cubic":
            out.append("  PROPORTIONS: cubic. Keep every box roughly chunky - its longest side")
            out.append(f"  may be at most {cap:g}x its shortest. No thin plates, no long rods.")
        elif s["proportions"] == "mixed":
            out.append("  PROPORTIONS: mixed. Boxes may be elongated or slab-like up to")
            out.append(f"  {cap:g}x long-to-short. Vary them; do not make everything a cube.")
        else:
            out.append("  PROPORTIONS: elongated. Any aspect ratio is allowed - thin plates,")
            out.append("  long rods and wide slabs are all encouraged where they read well.")

    if s["symmetry"] != "none" and kind in ("model", "prop"):
        if s["symmetry"] == "mirror-x":
            out.append("  SYMMETRY: mirror across X. Every shape placed left of centre must have")
            out.append("  a matching shape the same distance right of centre. Centred shapes must")
            out.append("  straddle x=0 evenly.")
        elif s["symmetry"] == "mirror-z":
            out.append("  SYMMETRY: mirror across Z, front to back, by the same rule.")
        else:
            out.append("  SYMMETRY: radial. Repeat the motif evenly around the vertical axis.")

    out.append(f"  DETAIL: {s['detail']}. {DETAIL_NOTE[s['detail']]}")
    return "\n".join(out)


def _boxes_of(result) -> list:
    """Every box as (bone_name, (x0,y0,z0), (x1,y1,z1))."""
    out = []
    doc = result.doc
    if result.kind == "model":
        bones = doc.bones.values() if isinstance(doc.bones, dict) else (doc.bones or [])
        for bone in bones:
            nm = getattr(bone, "name", "?")
            for bx in getattr(bone, "boxes", []) or []:
                a = tuple(getattr(bx, "lo", ()) or ())
                b = tuple(getattr(bx, "hi", ()) or ())
                if len(a) == 3 and len(b) == 3:
                    lo = tuple(min(a[i], b[i]) for i in range(3))
                    hi = tuple(max(a[i], b[i]) for i in range(3))
                    out.append((nm, lo, hi))
    return out


# Keys designbridge's box statement actually honours. Anything else is
# parsed and then dropped on the floor with no diagnostic, which is how a
# model can write rot=(0,45,0) or material_id=iron, be told nothing, and
# ship an axis-aligned untextured box believing it worked.
BOX_KEYS_HONOURED = {"from", "to", "color"}
BONE_KEYS_HONOURED = {"parent", "pivot"}
SILENT_KEY_HINT = {
    "rot": "designbridge has no rotated boxes. Build the shape from axis-aligned "
           "boxes, or rotate the whole bone with an animation key.",
    "rotation": "same as rot - not supported on a box.",
    "material_id": "materials are not part of the model grammar. Colour the box with "
                   "color=#rrggbb, and bind a texture at the Studio level.",
    "material": "see material_id.",
    "emissive": "there is no emissive channel. A bright saturated colour is the only "
                "way to read as glowing, and it is only a visual approximation.",
    "emissive_channel": "see emissive.",
    "glow": "see emissive.",
    "socket": "sockets are not a statement. An empty bone at the socket position is "
              "the closest thing, and it carries no semantics for the engine.",
    "size": "boxes are defined by two corners: use from= and to=.",
    "origin": "boxes are defined by two corners: use from= and to=.",
    "uv": "UVs are not expressible here.",
    "texture": "a box cannot name a texture in this grammar.",
}


def check_unknown_keys(dsl: str) -> list:
    """Flag attributes the compiler will silently discard.

    This is a Studio-side lint because designbridge does not warn: it parses
    the key, finds no handler, and moves on. Silence is the dangerous part -
    the author has no way to learn their instruction did nothing.
    """
    issues = []
    seen = set()
    for n, raw in enumerate(dsl.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        head = line.split()[0].lower()
        if head == "box":
            honoured, what = BOX_KEYS_HONOURED, "box"
        elif head == "bone":
            honoured, what = BONE_KEYS_HONOURED, "bone"
        else:
            continue
        for key in re.findall(r"(?:^|\s)([A-Za-z_][\w.]*)\s*=", line):
            k = key.lower()
            if k in honoured or k in seen:
                continue
            seen.add(k)
            hint = SILENT_KEY_HINT.get(k, f"the {what} statement has no '{key}' attribute.")
            issues.append((
                "WARN",
                f"line {n}: '{key}=' on a {what} is accepted by the parser and then "
                f"DISCARDED - it has no effect",
                hint,
            ))
    return issues


def check_style(result, style: dict) -> list:
    """Re-measure the compiled document against the declared style.

    Returns a list of (severity, message, fix) for anything that broke the
    budget. This is what makes the controls binding rather than decorative:
    the model is told the limits, and then the limits are actually checked.
    """
    s = normalize_style(style)
    if not s["enforce"]:
        return []
    kind = result.kind
    lvl = s["complexity"]
    issues = []

    if kind == "model":
        boxes = _boxes_of(result)
        nbones = len({nm for nm, _, _ in boxes}) or 0
        total_cap, per_cap = model_budget(style, nbones)
    else:
        total_cap, per_cap = BUDGET.get(kind, BUDGET["model"])[lvl]

    if kind == "model":
        if len(boxes) > total_cap:
            issues.append(("WARN",
                f"complexity {lvl} allows {total_cap} boxes, this model has {len(boxes)}",
                f"merge or remove {len(boxes) - total_cap} box(es), or raise complexity"))
        per_bone = {}
        for nm, _, _ in boxes:
            per_bone[nm] = per_bone.get(nm, 0) + 1
        for nm, n in sorted(per_bone.items()):
            if n > per_cap:
                issues.append(("WARN",
                    f"bone '{nm}' has {n} boxes, complexity {lvl} allows {per_cap} per bone",
                    f"simplify '{nm}' to {per_cap} box(es) or raise complexity"))

        cap = ASPECT_CAP[s["proportions"]]
        if cap:
            for nm, lo, hi in boxes:
                d = sorted(max(1e-6, hi[i] - lo[i]) for i in range(3))
                ratio = d[2] / d[0]
                if ratio > cap + 1e-6:
                    issues.append(("WARN",
                        f"box in '{nm}' is {ratio:.1f}x long-to-short, '{s['proportions']}' allows {cap:g}x",
                        "even out that box's proportions, or set proportions to elongated"))
                    break   # one is enough; the model gets the point

        if s["symmetry"] in ("mirror-x", "mirror-z"):
            ax = 0 if s["symmetry"] == "mirror-x" else 2
            def key(lo, hi):
                return tuple(round(v, 2) for v in (lo + hi))
            have = {}
            for nm, lo, hi in boxes:
                have[key(lo, hi)] = have.get(key(lo, hi), 0) + 1
            unmatched = 0
            for nm, lo, hi in boxes:
                mlo = list(lo); mhi = list(hi)
                mlo[ax], mhi[ax] = -hi[ax], -lo[ax]
                if have.get(key(tuple(mlo), tuple(mhi)), 0) == 0:
                    unmatched += 1
            if unmatched:
                issues.append(("WARN",
                    f"{unmatched} box(es) have no {s['symmetry']} counterpart",
                    f"mirror them across {'X' if ax == 0 else 'Z'}, or set symmetry to none"))

    elif kind == "prop":
        n = len(getattr(result.doc, "parts", []) or [])
        if n > total_cap:
            issues.append(("WARN",
                f"complexity {lvl} allows {total_cap} part(s), this design has {n}",
                f"combine parts, or raise complexity"))

    else:  # texture
        n = len(getattr(result.doc, "layers", []) or [])
        if n > total_cap:
            issues.append(("WARN",
                f"complexity {lvl} allows {total_cap} layers, this texture has {n}",
                "merge layers, or raise complexity"))

    return issues


def style_report_block(issues: list, style: dict) -> str:
    """Render the style findings to append to the compiler's own report."""
    s = normalize_style(style)
    head = (f"STUDIO STYLE CHECK - complexity {s['complexity']}/5, "
            f"{s['proportions']} proportions, symmetry {s['symmetry']}, {s['detail']} detail")
    if not s["enforce"]:
        return f"\n{head}\n  (enforcement off - not checked)\n"
    if not issues:
        return f"\n{head}\n  PASS: within budget.\n"
    lines = [f"\n{head}", f"  {len(issues)} style issue(s) - these are budget warnings, not compiler errors:"]
    for sev, msg, fix in issues:
        lines.append(f"  [{sev}] {msg}")
        lines.append(f"         FIX: {fix}")
    return "\n".join(lines) + "\n"


SYSTEM_CONTEXT = """ABOUT THIS SYSTEM - read this before anything else

You are the design half of a two-part pipeline that produces real, usable game
assets. You write a short text document. A compiler called designbridge turns
that document into actual geometry and image files.

      you write a document  ->  designbridge compiles it  ->  real asset files
             ^                                                      |
             |            a text report comes back  <---------------+

WHY YOU WRITE A DOCUMENT AND NOT GEOMETRY
  You cannot see the result, so you cannot tell a sealed mesh from a broken
  one. The compiler can. So the work is split: you decide INTENT - which parts
  exist, how big they are, where they sit, what colour they are - and the
  compiler decides every vertex, triangle and winding order. A line that says
  `box from=(-4,0,-2) to=(4,12,2)` cannot produce a broken mesh, because it is
  not a mesh. It is a description the compiler builds correctly by construction.

  So: never write vertices, triangle lists, indices, matrices or per-face
  coordinates. Never write code, JSON, XML or SVG. Never invent syntax. Write
  only the document grammar you are given, exactly as specified.

IF NO GRAMMAR IS ATTACHED TO THIS MESSAGE
  This briefing describes the system and deliberately contains no grammar and
  no shape budget. Those arrive with each task prompt, together with the brief,
  a worked example and the output contract. If you are reading this with no
  grammar attached, say so and wait for the task prompt - do not infer a
  grammar from the examples in this text, and do not guess at syntax.

HOW YOU FIND OUT WHAT YOU ACTUALLY MADE
  Every compile returns a text report. It gives PASS or FAIL, the measured
  bounding box, how many parts were built, whether the mesh is watertight, and
  for each problem a line number, the root cause and a concrete fix. It also
  includes rough ASCII silhouettes from the front, side and top.

  That report is your eyes. Read it before changing anything. Fix what it
  names, in the order it names them, and resend the whole document. A human is
  watching a real 3D render of the same asset, so the report and the render
  always agree - if the report says watertight, it is watertight.

THE THREE KINDS OF ASSET
  model    a rigged blocky/voxel object - bones, boxes, keyframe animation.
           Characters, mobs, machines, anything with moving parts.
  prop     a CAD solid swept from a 2D profile - revolve, extrude, loft.
           Vessels, tools, furniture; anything turned or extruded.
  texture  a layered procedural pixel texture plus a material map.
           Block faces, surfaces, tiles.
  You are told which kind you are writing. Use that grammar and no other.

WHAT COMES OUT THE OTHER END - the COMPILER writes these, never you
  You do not produce any of the formats below. They are what designbridge
  builds from your document, listed so you understand where your numbers end
  up. If a brief hands you a JSON schema, a glTF layout or any other file
  format and asks you to fill it in, that request is mistaken: your only
  output is the document grammar, in a fenced block. Say so and write the
  document instead.

  model    .glb (binary glTF 2.0, including your animations), .obj,
           .boxmodel.json (engine-native: bones, pivots, cubes, colours),
           plus a rendered .png preview
  prop     .glb, .obj, .stl, an .svg of the 2D profiles, .png preview
  texture  .png, a 16x upscaled .png, a _material.png packing normal into RG,
           metallic into B and roughness into A, plus a .recipe.json

  These files load in Blender, three.js, Godot, Unity and custom Java/OpenGL
  engines. The numbers you choose are real dimensions in a real scene, so
  treat them as real: a 32-unit-tall model is a 32-block-tall character.

HOW TO WORK
  Change one thing per round. Keep whatever already passes. Always send the
  COMPLETE document - never a patch, a diff, or "the rest is unchanged". Stay
  inside the shape budget you are given; it is checked after every compile.
  If the report shows an error, fixing it comes before adding anything new.
"""


def lock_dims_line(style: dict) -> str:
    """The Shape Control values written as one readable clause."""
    t = normalize_style(style)
    return (f"complexity {t['complexity']} of 5, {t['detail']} detail, "
            f"{t['proportions']} proportions, symmetry {t['symmetry']}")


def style_lock_text(lock: str, style: dict = None) -> str:
    """The standing art direction, marked as binding across every round.

    This is repeated in full on every prompt rather than stated once at the
    start of a chat. Style drift is silent - no compiler error catches "the
    palette is wrong" or "this mob does not match the other forty" - so the
    one instruction that cannot be checked afterwards is the one that has to
    be restated every time. It also keeps each prompt self-contained, so a
    round can be pasted into a brand-new chat and still come back on-style.
    """
    body = (lock or "").strip()
    if not body:
        return ""
    lines = [
        "STANDING ART DIRECTION - applies to every asset in this project",
        "  These rules are locked. They are not part of this round's request and",
        "  they do not expire: obey them on this asset and on every future one,",
        "  even when the request below says nothing about them. If a rule here",
        "  conflicts with the request below, follow this section and say so in",
        "  your plan line.",
        "",
    ]
    lines += ["  " + l.rstrip() for l in body.splitlines()]
    # The palette and the written rules are only half of a style. The form
    # rules - how dense, how chunky, how symmetrical - are set by the Shape
    # Controls and spelled out under SHAPE BUDGET below. Without this line a
    # model reads those as this round's brief rather than as standing law,
    # and quietly drifts denser or chunkier the moment the brief is silent.
    if style:
        lines += [
            "",
            "  ALSO LOCKED - the form rules under SHAPE BUDGET are part of this lock:",
            "    " + lock_dims_line(style) + ".",
            "  Those are not this round's preference. They hold for every asset in",
            "  the project, and the budget there is a hard ceiling, not a target.",
        ]
    return "\n".join(lines)


# ======================================================================
# STYLE SPEC GENERATOR
# ----------------------------------------------------------------------
# A round trip: ask a model to turn a loose description of an art
# direction into a structured spec, then ingest that spec. The known keys
# drive the Shape Controls; everything else - extra rules, notes, keys the
# schema never anticipated - is folded into the style lock text rather
# than discarded, so nothing the model thought worth saying is lost.
# ======================================================================

STYLE_SPEC_PROMPT = """Turn the art direction below into a style specification for a
game-asset pipeline.

THE ART DIRECTION
  {brief}

Answer with ONE fenced JSON block and nothing else:

```json
{{
  "name": "short name for this style, e.g. STRATA industrial mobs",
  "palette": {{
    "base":   "#rrggbb",
    "shadow": "#rrggbb",
    "accent": "#rrggbb",
    "<any other role you need>": "#rrggbb"
  }},
  "complexity": 3,
  "symmetry": "none | mirror-x | mirror-z | radial",
  "proportions": "cubic | mixed | elongated",
  "detail": {{
    "level": "low | medium | high",
    "notes": "what detail means here in words - what gets detailed and what stays plain"
  }},
  "surface": "how surfaces are treated: flat blocks, panel seams, rivets, wear...",
  "silhouette": "what the outline has to do to be recognisable at a distance",
  "materials": "what the thing reads as made of",
  "forbid": ["things this style never does"],
  "rules": [
    "One hard rule per line. Be concrete and checkable by eye.",
    "Name real numbers where they matter, e.g. 'mobs 16 units wide max'.",
    "Say what is forbidden as well as what is required."
  ],
  "notes": "Anything that matters but does not fit a field above."
}}
```

WHAT THE FIELDS MEAN
  complexity   1-5. A budget on how many parts an asset may use.
               1 is a bold silhouette, 3 is a normal game asset, 5 is a boss.
  symmetry     Whether assets must mirror, and about which axis.
  proportions  cubic keeps every box chunky (max 2:1 long-to-short);
               mixed allows slabs and elongated forms up to 4:1;
               elongated removes the cap entirely.
  detail       Both a level and a sentence. The level sets the budget;
               the sentence is restated to the model every round, so it is
               where "detailed" actually gets defined for this project.
  surface      Surface treatment. A palette says what colour a thing is;
               this says what it looks made of and how worked it is.
  silhouette   What the outline must achieve. The one rule that survives
               being viewed from across the map.
  forbid       Hard bans. These are the most useful lines in a style lock,
               because drift shows up as things creeping back in.
  rules        The part that does the real work. These are restated to the
               generating model on every single asset, so write them as
               standing law, not as a description of one asset.
  palette      Hex colours with a role name each. Roles are free-form; use
               whatever the art direction actually needs.

RULES FOR YOUR ANSWER
  Valid JSON only - double quotes, no trailing commas, no comments.
  Add extra top-level keys if the art direction needs them; they will be
  kept as notes rather than dropped, so do not force detail into a field
  where it does not belong.
  Do not invent an art direction that was not asked for. If the brief is
  thin, keep the spec small and say what you assumed in "notes".
"""

STYLE_KNOWN_KEYS = {"name", "palette", "complexity", "symmetry", "proportions",
                    "detail", "rules", "notes",
                    # A style is more than a palette. These carry the part a
                    # colour list cannot: how dense the forms read, how the
                    # surface is treated, what the silhouette must do, and what
                    # is banned outright. Each becomes a locked rule line.
                    "surface", "silhouette", "forbid", "materials"}


def extract_json(text: str):
    """Pull the first JSON object out of whatever the model sent.

    Same tolerance as the DSL extractor: a fenced block, a bare object, or
    an object sitting in prose. Returns (obj, error_message).
    """
    if not text or not text.strip():
        return None, "nothing to parse"
    raw = text.replace("\r\n", "\n")
    candidates = []
    for m in FENCE_RE.finditer(raw):
        candidates.append(m.group(1))
    candidates.append(raw)
    # Also try the outermost {...} span, for a reply with prose either side.
    i, j = raw.find("{"), raw.rfind("}")
    if i >= 0 and j > i:
        candidates.append(raw[i:j + 1])
    last = "no JSON object found"
    for c in candidates:
        c = c.strip()
        if not c.startswith("{"):
            k, l = c.find("{"), c.rfind("}")
            if k < 0 or l <= k:
                continue
            c = c[k:l + 1]
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj, None
        except Exception as exc:
            last = str(exc)
    return None, last


def style_spec_to_lock(spec: dict) -> dict:
    """Turn a parsed spec into lock text plus Shape Control values.

    Unknown keys are not errors. They are rendered into the lock text under
    their own heading, because a model that volunteered a field the schema
    did not ask for usually had a reason.
    """
    warn = []
    lines = []

    name = str(spec.get("name") or "").strip()

    pal = spec.get("palette")
    if isinstance(pal, dict) and pal:
        bits = []
        for role, val in pal.items():
            v = str(val).strip()
            if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", v):
                warn.append(f"palette.{role} is not a hex colour ({v!r}) - kept as written")
            bits.append(f"{role} {v}")
        lines.append("Palette: " + ", ".join(bits) + ". Use these and no others.")
    elif pal is not None:
        warn.append("palette was not an object - ignored")

    # detail/surface/silhouette/materials are the "and also the detail" half
    # of a style lock. Each may be a string, a list of lines, or an object
    # with prose attached; all three shapes become locked lines.
    def _prose(key, lead):
        v = spec.get(key)
        if v is None:
            return
        if isinstance(v, dict):
            bits = [f"{k} {vv}" for k, vv in v.items()]
            txt = ", ".join(bits)
        elif isinstance(v, (list, tuple)):
            txt = "; ".join(str(x).strip() for x in v if str(x).strip())
        else:
            txt = str(v).strip()
        if txt:
            lines.append(f"{lead}: {txt}")

    det = spec.get("detail")
    if isinstance(det, dict):
        # the enum feeds the slider, the prose feeds the lock
        lvl = str(det.get("level") or det.get("value") or "").strip().lower()
        if lvl in DETAIL_NOTE:
            spec = dict(spec); spec["detail"] = lvl
        note = str(det.get("notes") or det.get("note") or "").strip()
        if note:
            lines.append("Detail: " + note)
        if lvl and lvl not in DETAIL_NOTE:
            warn.append(f"detail.level {lvl!r} is not low/medium/high - prose kept, slider unchanged")
    elif isinstance(det, str) and det.strip().lower() not in DETAIL_NOTE and det.strip():
        # free prose in the detail field: keep it as a rule rather than drop it
        lines.append("Detail: " + det.strip())

    _prose("surface", "Surface")
    _prose("silhouette", "Silhouette")
    _prose("materials", "Materials")
    forb = spec.get("forbid")
    if forb is not None:
        if isinstance(forb, (list, tuple)):
            for f in forb:
                t = str(f).strip()
                if t:
                    lines.append("Never: " + t)
        else:
            lines.append("Never: " + str(forb).strip())

    rules = spec.get("rules")
    if isinstance(rules, str):
        rules = [rules]
    if isinstance(rules, list):
        for r in rules:
            t = str(r).strip()
            if t:
                lines.append(t)
    elif rules is not None:
        warn.append("rules was not a list - ignored")

    notes = str(spec.get("notes") or "").strip()
    if notes:
        lines.append("")
        lines.append("Notes: " + notes)

    # Anything the schema did not anticipate, preserved verbatim.
    extras = {k: v for k, v in spec.items() if k not in STYLE_KNOWN_KEYS}
    if extras:
        lines.append("")
        lines.append("Additional direction:")
        for k, v in extras.items():
            if isinstance(v, (dict, list)):
                lines.append(f"  {k}: " + json.dumps(v, separators=(", ", ": ")))
            else:
                lines.append(f"  {k}: {v}")

    style = {}
    if "complexity" in spec:
        try:
            style["complexity"] = max(1, min(5, int(spec["complexity"])))
        except Exception:
            warn.append(f"complexity {spec['complexity']!r} is not 1-5 - left unchanged")
    for key, allowed in (("symmetry", ("none", "mirror-x", "mirror-z", "radial")),
                         ("proportions", ("cubic", "mixed", "elongated")),
                         ("detail", ("low", "medium", "high"))):
        if key in spec:
            v = str(spec[key]).strip().lower()
            if v in allowed:
                style[key] = v
            else:
                warn.append(f"{key} {spec[key]!r} is not one of {'/'.join(allowed)} - left unchanged")

    return {
        "ok": True,
        "name": name,
        "lock": "\n".join(lines).strip(),
        "style": style,
        "extras": sorted(extras.keys()),
        "warnings": warn,
    }


def build_engineered_prompt(payload):
    """Build the full engineered prompt with the real compiler in designbridge.py.

    Studio v2 used to assemble a short header in JavaScript, which skipped the
    role, reasoning ladder, complete grammar, anchors, failure catalogue, worked
    example, output contract, and self-check. This routes the workbench through
    the same prompt compiler as `designbridge.py prompt`, then adds the round
    context (current source, compiler report, edit/fix/new-take instruction).
    """
    kind = (payload.get("kind") or payload.get("track") or "model").lower()
    if kind not in ("texture", "model", "prop"):
        kind = "prop"

    mode = (payload.get("mode") or "edit").lower()
    if mode not in ("edit", "new_take", "fix"):
        mode = "edit"

    instruction = (payload.get("instruction") or "").strip()
    dsl = (payload.get("dsl") or "").strip()
    report = (payload.get("report") or "").strip()
    name = (payload.get("name") or "").strip()
    tag = (payload.get("tag") or "").strip()
    branch = (payload.get("branch") or "").strip()
    include_report = bool(payload.get("includeReport", True))
    full_dsl = bool(payload.get("fullDsl", False))
    want_context = payload.get("context", True) is not False
    style_lock = payload.get("styleLock", "") if payload.get("styleLockOn", True) is not False else ""
    runnable = bool(payload.get("runnable", False))

    if mode == "fix":
        brief = "Repair this design: " + (instruction or "make it compile cleanly without changing its intent.")
    elif mode == "new_take":
        brief = instruction or "Create a fresh design for this project."
    else:
        brief = instruction or "Refine the current design while keeping what already works."

    contract = ""
    marker = "OUTPUT CONTRACT"
    # designbridge's own contract asks for <<<DESIGN ... >>>. Models mangle
    # those brackets - stripping them, or emitting an empty "<<>>" - so the
    # contract is rewritten here to ask for a plain fenced block instead.
    # designbridge itself is left untouched; only the prompt text is adjusted,
    # and the extractor accepts either form on the way back in.
    NEW_CONTRACT = (
        "OUTPUT CONTRACT - follow exactly\n"
        "  1. First, in at most four short lines, state your plan: what the asset is,\n"
        "     its palette or parts, and its overall size.\n"
        "  2. Then the whole source in ONE fenced code block and nothing else:\n"
        "        ```\n"
        "        <the complete document, starting with its first keyword>\n"
        "        ```\n"
        "  3. Nothing after the closing fence. Send the WHOLE source every round -\n"
        "     never a patch, never a fragment, never a diff.\n"
        "  Do not wrap the source in angle brackets or any other marker. The fence\n"
        "  alone is the delimiter.\n"
    )
    if HAS_DESIGNBRIDGE and db_core is not None:
        try:
            base = db_core.prompt_for(kind, brief, runnable=runnable)
            contract = getattr(db_core, "_OUTPUT_CONTRACT", "") or ""
        except Exception as exc:
            base = f"Design a game {kind}.\n\nTHE BRIEF\n  {brief}\n\n[prompt compiler unavailable: {exc}]"
    else:
        base = f"Design a game {kind}.\n\nTHE BRIEF\n  {brief}"

    where = " ".join(part for part in (name, f"({tag})" if tag else "", f"on {branch}" if branch else "") if part)
    extra = []

    # Standing art direction leads the round context: it governs everything
    # that follows, including a NEW TAKE that otherwise inherits nothing.
    lock_block = style_lock_text(style_lock, payload.get("style"))
    if lock_block:
        extra.append(lock_block)

    if mode == "new_take":
        extra.append(
            "THIS ROUND - NEW TAKE" + (f" - {where}" if where else "") + "\n"
            "  Write a brand-new document from scratch; do not inherit the previous geometry.\n"
            "  Follow the brief above exactly. Send the whole document between the sentinels."
        )
    else:
        if dsl:
            extra.append("CURRENT DESIGN SOURCE" + (f" - {where}" if where else "") + "\n" + dsl)
        if include_report and report:
            extra.append(
                "CURRENT COMPILER REPORT\n"
                + report
                + "\n  Treat every ERROR as a blocking defect and every WARNING as a decision to judge."
            )
        if mode == "fix":
            extra.append(
                "THIS ROUND - FIX\n"
                "  Repair every error first, then judge the warnings. Change one thing per round,\n"
                "  keep what already works, and resend the whole document - never a patch."
            )
        else:
            extra.append(
                "THIS ROUND - EDIT\n"
                "  Apply the instruction to the CURRENT DESIGN SOURCE. Keep the parts that already\n"
                "  work, change one thing per round, and resend the whole document - never a patch."
            )

    # The shape budget goes in as hard limits, and is re-measured after the
    # compile, so it binds rather than merely suggesting.
    bones_now = len(re.findall(r"(?m)^\s*bone\s+\S", dsl or ""))
    extra.append(style_constraints_text(kind, payload.get("style"), bones_now, brief))
    size_note = size_budget_note(kind, payload.get("style"), brief)
    if size_note:
        extra.append(size_note.rstrip())
    if kind == "model":
        extra.append(
            "WHAT THIS GRAMMAR CANNOT DO - do not attempt these\n"
            "  There are no rotated boxes, no wedges, cylinders or slopes, no material\n"
            "  ids, no emissive or glow channel, and no sockets. A box has exactly three\n"
            "  attributes: from, to and color. Any other attribute is parsed and then\n"
            "  silently discarded, so writing rot= or material_id= does nothing at all\n"
            "  and you will not be told.\n"
            "  Work within that: build slopes as stepped boxes, read glow as a bright\n"
            "  saturated colour, and mark an attachment point as an empty bone. If the\n"
            "  brief asks for something this grammar cannot express, say so plainly in\n"
            "  your plan line rather than inventing syntax for it."
        )

    if full_dsl:
        extra.append(
            "REMINDER\n"
            "  Use only the grammar above. Every profile must be used, every inner dimension\n"
            "  must derive from a token, and the three extents must match the size you intend."
        )

    addition = "\n\n".join(extra)
    # Insert the round context just before the output contract so the contract
    # stays the last thing the model reads, but preserve everything after it
    # (texture/model prompts add a "WHAT HAPPENS NEXT" or runnable section).
    # The system briefing goes first: a model that understands what the
    # pipeline does with its text writes better documents than one dropped
    # straight into a grammar reference.
    if want_context:
        base = SYSTEM_CONTEXT.rstrip() + "\n\n" + ("=" * 70) + "\n\n" + base.lstrip()

    if addition and marker in base:
        head, tail = base.split(marker, 1)
        text = head.rstrip() + "\n\n" + addition + "\n\n" + marker + tail
    elif addition:
        text = base.rstrip() + "\n\n" + addition
    else:
        text = base

    # Swap designbridge's sentinel contract for the fenced-block one. The
    # original runs from "OUTPUT CONTRACT" to the next blank-line-separated
    # heading, so replace exactly that span and keep whatever follows.
    # The worked example inside designbridge's prompt also wraps its source in
    # the sentinels. Convert those to fences too, so every example the model
    # reads demonstrates the format it is being asked to produce.
    text = re.sub(r"(?m)^([ \t]*)<<<[ \t]*DESIGN[ \t]*$", r"\1```", text)
    text = re.sub(r"(?m)^([ \t]*)>>>[ \t]*$", r"\1```", text)

    if marker in text:
        head, rest = text.split(marker, 1)
        after = ""
        m = re.search(r"\n\n(?=[A-Z][A-Z0-9 _/&-]{3,}\n)", rest)
        if m:
            after = rest[m.start():]
        text = head + NEW_CONTRACT.rstrip() + after
    else:
        text = text.rstrip() + "\n\n" + NEW_CONTRACT.rstrip()

    # A manifest of what actually ended up in the prompt, so the UI can show
    # the operator the whole thing is present before they copy it. Measured
    # against the finished text rather than asserted, so a section that
    # silently failed to assemble shows as missing instead of claiming to be
    # there.
    # A manifest of what actually ended up in the prompt, so the operator can
    # see the whole thing is present before copying. Measured against the
    # finished text rather than asserted, so a section that silently failed to
    # assemble shows as absent instead of claiming to be there.
    #
    # "required" means this prompt is incomplete without it. The rest are
    # track-specific: the prop grammar carries far more guidance than the
    # texture one, so their absence is normal and must not read as an error.
    probes = [
        ("System briefing", "ABOUT THIS SYSTEM", want_context, True,
         "what the pipeline is, why it writes a document, what files come out"),
        ("Role & how to think", "HOW TO THINK ABOUT", True, False,
         "the reasoning ladder for this kind of asset"),
        ("Grammar", "THE LANGUAGE", True, False,
         "every statement the compiler accepts"),
        ("Worked example", "WORKED EXAMPLE", True, False,
         "one complete document that compiles"),
        ("Reading the views", "READING THE", False, False,
         "how to interpret the ASCII silhouettes"),
        ("Values & tokens", "VALUES", False, False,
         "named constants like $wall.std"),
        ("Operations", "THE THREE OPERATIONS", False, False,
         "revolve / extrude / loft and when each is right"),
        ("Assembly rules", "ASSEMBLY IS PLACEMENT", False, False,
         "why parts are placed, never fused"),
        ("Known failures", "FAILURES THAT ACTUALLY HAPPEN", False, False,
         "the mistakes this track invites, and their causes"),
        ("Standing art direction", "STANDING ART DIRECTION", bool(lock_block), True,
         "your style lock, repeated every round"),
        ("Shape budget", "SHAPE BUDGET", True, False,
         "complexity, proportions, symmetry - checked after compile"),
        ("Your request", "THE BRIEF", True, False,
         "what you asked for this round"),
        ("Current source", "CURRENT DESIGN SOURCE", bool(dsl and mode != "new_take"), True,
         "the document being edited"),
        ("Compiler report", "CURRENT COMPILER REPORT", bool(include_report and report and mode != "new_take"), True,
         "last round's errors and measurements"),
        ("This round's job", "THIS ROUND", True, False,
         "edit / new take / fix instruction"),
        ("Output contract", "OUTPUT CONTRACT", True, False,
         "how to format the reply"),
        ("What happens next", "WHAT HAPPENS NEXT", False, False,
         "the loop the model is about to enter"),
    ]
    found, order = {}, []
    for label, needle, req, toggleable, why in probes:
        at = text.find(needle)
        found[label] = at
        if at >= 0:
            order.append((at, label, why, req))
    order.sort()
    sections = []
    for i, (at, label, why, req) in enumerate(order):
        end = order[i + 1][0] if i + 1 < len(order) else len(text)
        sections.append({"name": label, "chars": end - at, "why": why, "required": req})
    missing = [l for l, n, req, t, _ in probes if req and found[l] < 0]
    # Absent for two different reasons, and conflating them misleads: a
    # section this track simply does not have, versus one the operator
    # switched off or left empty.
    switched_off = [l for l, n, req, t, _ in probes if t and not req and found[l] < 0]
    optional_absent = [l for l, n, req, t, _ in probes if not t and not req and found[l] < 0]

    return {"ok": True, "kind": kind, "mode": mode, "prompt": text, "length": len(text),
            "sections": sections, "missing": missing,
            "notApplicable": optional_absent,
            "switchedOff": switched_off,
            "complete": not missing,
            # What is locked, so the UI can say so plainly. A model that keeps
            # returning the same design is usually obeying a lock the operator
            # has forgotten is on; this is the data that warning needs.
            "lock": {
                "active": bool(lock_block),
                "chars": len(style_lock),
                "lines": len([l for l in style_lock.splitlines() if l.strip()]),
                "dims": lock_dims_line(payload.get("style")),
                "body": style_lock,
            },
            "lines": text.count("\n") + 1}


def make_handler(storage: StudioStorage, html_path: Path):
    class StudioHandler(BaseHTTPRequestHandler):
        def _send_bytes(self, body: bytes, ctype: str, code: int = 200, extra_headers: dict = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, obj: dict, code: int = 200):
            self._send_bytes(json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", code)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            return json.loads(raw or "{}")

        def do_OPTIONS(self):
            self._send_bytes(b"", "text/plain", 204)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            try:

                if path in ("/", "/index.html", "./index.html"):
                    target = html_path
                    if not target.is_file():
                        target = resolve_ui_path(required=False)
                    if not target.is_file():
                        message = (
                            "Studio HTML file not found next to studio_server.py. Looked for:\n  "
                            + "\n  ".join(UI_CANDIDATES)
                            + "\nPass --ui <path> to point at it."
                        )
                        return self._send_bytes(message.encode("utf-8"), "text/plain; charset=utf-8", 404)
                    html = target.read_text(encoding="utf-8")
                    # Point the two library tags at vendor/ when the operator
                    # has put copies there, so the Studio needs no internet.
                    for marker, local in (("<!--DB_TAILWIND-->", "tailwind.js"),
                                          ("<!--DB_THREE-->", "three.min.js")):
                        if marker not in html:
                            continue
                        if not any((root / "vendor" / local).is_file()
                                   for root in (Path.cwd(), SCRIPT_DIR, target.parent)):
                            continue
                        html = re.sub(
                            r'<script src="https://[^"]+"></script>(?=' + re.escape(marker) + ')',
                            f'<script src="/vendor/{local}"></script>',
                            html, count=1)
                    return self._send_bytes(
                        html.encode("utf-8"),
                        "text/html; charset=utf-8",
                        200,
                        {"Cache-Control": "no-store"},
                    )

                if path == "/api/health":
                    return self._send_json({
                        "ok": True,
                        "engine": "DESIGNBRIDGE_CORE" if HAS_DESIGNBRIDGE else "SQLITE_DISK_ENGINE",
                        "dbPath": str(storage.db_path),
                    })

                if path == "/api/bootstrap":
                    return self._send_json(storage.get_bootstrap_state())

                if path == "/api/settings":
                    return self._send_json({"ok": True, "settings": storage.get_settings()})

                if path == "/api/style-prompt":
                    brief = (urlparse(self.path).query or "")
                    from urllib.parse import parse_qs
                    b = (parse_qs(brief).get("brief", [""])[0] or
                         "(none given - ask the operator what look they want, "
                         "or propose a sensible default and say so in notes)")
                    txt = STYLE_SPEC_PROMPT.format(brief=b)
                    return self._send_json({"ok": True, "prompt": txt, "length": len(txt)})

                if path == "/api/context":
                    return self._send_json({"ok": True, "context": SYSTEM_CONTEXT,
                                             "length": len(SYSTEM_CONTEXT)})

                if path.startswith("/vendor/"):
                    # Optional offline libraries. If the operator has dropped
                    # three.min.js / tailwind.js into vendor/ they are served
                    # from here; otherwise an empty 200 keeps the console clean
                    # and index.html falls through to its CDN <script>.
                    name = os.path.basename(unquote(path))
                    if name and re.fullmatch(r"[A-Za-z0-9._-]+", name):
                        for root in (Path.cwd(), SCRIPT_DIR, html_path.parent):
                            f = root / "vendor" / name
                            if f.is_file():
                                ctype = "application/javascript" if name.endswith(".js") else "text/css"
                                return self._send_bytes(f.read_bytes(), ctype, 200,
                                                        {"Cache-Control": "max-age=86400"})
                    return self._send_bytes(
                        b"/* no vendored copy present - falling back to the CDN */",
                        "application/javascript", 200)

                if path.startswith("/api/mesh/"):
                    parts = [unquote(x) for x in path.split("/") if x]
                    if len(parts) == 4:
                        _, _, pid, cid = parts
                        return self._send_json(storage.mesh_json(pid, cid))
                    return self._send_json({"ok": False, "error": "Invalid mesh route"}, 400)

                if path.startswith("/api/preview/"):
                    parts = [unquote(x) for x in path.split("/") if x]
                    if len(parts) == 4:
                        _, _, pid, cid = parts
                        raw = storage.preview_png(pid, cid)
                        if raw:
                            return self._send_bytes(raw, "image/png", 200, {"Cache-Control": "no-store"})
                    return self._send_bytes(b"", "image/png", 404)

                if path.startswith("/api/thumb/"):
                    parts = [unquote(x) for x in path.split("/") if x]
                    if len(parts) == 4:
                        _, _, pid, cid = parts
                        raw = storage.get_thumbnail_bytes(pid, cid)
                        if raw:
                            return self._send_bytes(raw, "image/png", 200)
                    return self._send_bytes(b"", "image/png", 404)

                if path.startswith("/api/export/"):
                    parts = [unquote(x) for x in path.split("/") if x]
                    # /api/export/<project_id>/repo.zip
                    if len(parts) == 4 and parts[3] == "repo.zip":
                        data, ctype, fname = storage.build_repo_zip(parts[2])
                        return self._send_bytes(data, ctype, 200, {"Content-Disposition": f'attachment; filename="{fname}"'})
                    # /api/export/<project_id>/<commit_id>/<fmt>
                    if len(parts) == 5:
                        _, _, pid, cid, fmt = parts
                        data, ctype, fname = storage.build_export_bytes(pid, cid, fmt)
                        return self._send_bytes(data, ctype, 200, {"Content-Disposition": f'attachment; filename="{fname}"'})
                    return self._send_json({"ok": False, "error": "Invalid export route"}, 400)

                return self._send_json({"ok": False, "error": f"Unknown route {path}"}, 404)
            except Exception as exc:
                return self._send_json({"ok": False, "error": str(exc)}, 500)

        def do_PUT(self):
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/settings":
                    patch = self._read_json()
                    saved = storage.update_settings(patch)
                    return self._send_json({"ok": True, "settings": saved})
                return self._send_json({"ok": False, "error": "Not found"}, 404)
            except Exception as exc:
                return self._send_json({"ok": False, "error": str(exc)}, 500)

        def do_PATCH(self):
            parsed = urlparse(self.path)
            parts = [unquote(x) for x in parsed.path.split("/") if x]
            try:
                if len(parts) == 3 and parts[0] == "api" and parts[1] == "projects":
                    body = self._read_json()
                    return self._send_json(storage.rename_project(
                        parts[2], body.get("name", ""), body.get("folder")))
                return self._send_json({"ok": False, "error": "Not found"}, 404)
            except Exception as exc:
                return self._send_json({"ok": False, "error": str(exc)}, 400)

        def do_DELETE(self):
            parsed = urlparse(self.path)
            parts = [unquote(x) for x in parsed.path.split("/") if x]
            try:
                if len(parts) == 3 and parts[0] == "api" and parts[1] == "projects":
                    return self._send_json(storage.delete_project(parts[2]))
                return self._send_json({"ok": False, "error": "Not found"}, 404)
            except Exception as exc:
                return self._send_json({"ok": False, "error": str(exc)}, 400)

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path
            parts = [unquote(x) for x in path.split("/") if x]
            try:
                body = self._read_json()
                if path == "/api/projects":
                    return self._send_json(storage.create_project(body))
                if len(parts) == 4 and parts[0] == "api" and parts[1] == "projects" and parts[3] == "duplicate":
                    return self._send_json(storage.duplicate_project(parts[2]))
                if path == "/api/checkout":
                    return self._send_json(storage.checkout_commit(body["projectId"], body["commitId"]))
                if path == "/api/branches/fork":
                    return self._send_json(storage.fork_branch(body["projectId"], body["sourceCommitId"], body["branchName"]))
                if path == "/api/branches/merge":
                    return self._send_json(storage.merge_branch(body["projectId"], body["sourceCommitId"]))
                if path == "/api/compile":
                    return self._send_json(storage.compile_and_save(body))
                if path == "/api/llm/run":
                    return self._send_json(storage.run_direct_llm(body.get("prompt", "")))
                if path == "/api/prompt":
                    return self._send_json(build_engineered_prompt(body))
                if path == "/api/style-parse":
                    spec, err = extract_json(body.get("text", ""))
                    if spec is None:
                        return self._send_json(
                            {"ok": False,
                             "error": f"Could not read a JSON style spec: {err}"}, 400)
                    return self._send_json(style_spec_to_lock(spec))
                return self._send_json({"ok": False, "error": f"Unknown POST route {path}"}, 404)
            except Exception as exc:
                return self._send_json({"ok": False, "error": str(exc)}, 400)

        def log_message(self, fmt, *args):
            pass

    return StudioHandler


def main():
    parser = argparse.ArgumentParser(description="DesignBridge Studio v2 Local SQLite + Disk Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port number (default: 8765)")
    parser.add_argument("--workspace", default="./workspace", help="Directory for studio.db & project files")
    parser.add_argument("--repair", action="store_true",
                        help="Recompile every stored revision and write its true status. "
                             "Never deletes anything; fixes verdicts left by older builds.")
    parser.add_argument("--replace-examples", action="store_true",
                        help="Swap only the three built-in demo assets for current ones. "
                             "Skips any you have added revisions to.")
    parser.add_argument("--audit", action="store_true",
                        help="Report which stored revisions disagree with the compiler, and change nothing.")
    parser.add_argument(
        "--ui",
        default=None,
        help="Path to Studio HTML file (default: auto-detect index.html next to studio_server.py)",
    )
    args = parser.parse_args()

    try:
        html_path = resolve_ui_path(args.ui)
    except FileNotFoundError as exc:
        parser.error(str(exc))

    storage = StudioStorage(Path(args.workspace))

    if args.audit:
        a = storage.audit()
        print(f"Audited {a['total']} revision(s): {a['agree']} agree with the compiler, "
              f"{len(a['mismatched'])} do not.")
        for m in a["mismatched"]:
            print(f"  {m['project']}/{m['tag']}: stored {m['stored']}, actually {m['actual']} "
                  f"({m['errors']} error(s))")
        if a["mismatched"]:
            print("\nRun with --repair to write the true status, or --replace-examples "
                  "to refresh just the demo assets.")
        return

    if args.replace_examples:
        print("Replacing built-in demo assets...")
        r = storage.replace_examples()
        print(f"Replaced {len(r['replaced'])}, kept {len(r['skipped'])}.")

    if args.repair:
        print("Recompiling every stored revision...")
        r = storage.repair()
        print(f"Checked {r['checked']}: {r['corrected']} verdict(s) corrected, "
              f"{r['now_failing']} genuinely failing, {r['unchanged']} already right.")
        print("Source, notes, branches and prompt history were not touched.")

    # Maintenance flags do their work and stop, so they never leave a server
    # running unexpectedly. Start it the normal way afterwards.
    if args.repair or args.replace_examples:
        print("\nDone. Start the Studio normally:")
        print(f"  python3 studio_server.py --workspace {args.workspace}")
        return

    # Quietly check for revisions an older build mislabelled, and say so once.
    try:
        a = storage.audit()
        stale = len(a["mismatched"])
    except Exception:
        stale = 0

    handler = make_handler(storage, html_path)

    server = ThreadingHTTPServer((args.host, args.port), handler)
    print("============================================================")
    print(f"DesignBridge Studio v2 -> http://{args.host}:{args.port}")
    print(f"Studio UI              -> {html_path}")
    print(f"SQLite Database        -> {storage.db_path}")
    print(f"UI Settings JSON       -> {storage.settings_json_path}")
    print(f"Project Artifacts      -> {storage.projects_dir}")
    print(f"CAD Compiler Bridge    -> {'ACTIVE (designbridge.py)' if HAS_DESIGNBRIDGE else 'BUILT-IN VALIDATOR'}")
    if stale:
        print("")
        print(f"!! {stale} stored revision(s) carry a status the compiler disagrees with.")
        print("   They were written by an older build and cannot compile as they stand.")
        print("   Your work is safe - nothing has been changed. To fix the labels:")
        print(f"     python3 studio_server.py --repair --workspace {args.workspace}")
        print("   To also refresh the three built-in demo assets:")
        print(f"     python3 studio_server.py --replace-examples --repair --workspace {args.workspace}")
    print("Press Ctrl+C to stop.")
    print("============================================================", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
