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
    # Last parsed style spec, rendered visually in the SETUP panel.
    "styleSpec": {},
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
    "structure": '''structure "__NAME__" size=(16,8,16) seed=1

material wall   stone_block     color=#6E7378
material floor  timber_plank    color=#4E422F
material post   weathered_iron  color=#3A3F45

room (0,0,0) (15,5,15) walls=wall floor=floor ceiling=none
door south at=(6,0) width=3 height=4
window east at=(2,2) width=2 every=5

colonnade (3,1,3) to=(12,1,3) material=post form=column every=4 height=4
fill (0,6,0) (15,6,15) material=wall form=slab_half
''',
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
        if _is_source(block):
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


try:
    import structurebridge as sb_core
except Exception:  # the Studio still runs without the structure track
    sb_core = None


def compile_source(dsl: str, style: dict = None) -> CompileResult:
    """Compile a revision with designbridge and read the real facts back out."""
    if db_core is None:
        raise RuntimeError(
            "designbridge.py was not importable, so nothing can be compiled. "
            "Put designbridge.py next to studio_server.py and restart."
        )
    body = strip_sentinels(dsl)
    if not body.strip():
        raise ValueError("The revision is empty - there is no DSL to compile.")

    # The structure track is a separate compiler sitting beside designbridge,
    # not a fork of it: a document that declares itself a structure is routed
    # there and comes back wearing the same CompileResult the rest of the
    # Studio already understands.
    if detect_kind_from_source(body) == "structure":
        if sb_core is None:
            raise RuntimeError(
                "This is a structure document, but structurebridge.py was not "
                "importable. Put it next to studio_server.py and restart.")
        doc = sb_core.parse(body)
        allowed = (style or {}).get("forms") if isinstance(style, dict) else None
        report = sb_core.report(doc, allowed)
        checks = sb_core.validate(doc, allowed)
        errors = len(doc.errors) + len([c for c in checks if c[0] == "error"])
        warnings = len([d for d in doc.diags if d[0] == "warn"]) \
            + len([c for c in checks if c[0] == "warn"])
        sx, sy, sz = doc.size
        forms = len({c.form for c in doc.cells.values()})
        return CompileResult(
            "structure", doc, "PASS" if errors == 0 else "FAIL", report,
            errors, warnings, f"{sx}x{sy}x{sz} cells",
            f"{len(doc.cells)}C / {forms}F",
            f"{len(doc.materials)} materials")

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
    if result.kind == "structure":
        if sb_core is not None:
            sb_core.export_json(result.doc, str(outdir / f"{slug}.structure.json"))
            sb_core.preview_png(result.doc, str(outdir / f"{slug}_preview.png"))
            sb_core.contact_sheet(result.doc, str(outdir / f"{slug}_sheet.png"))
    else:
        db_core.export_any(result.kind, result.doc, str(outdir), slug)
    return sorted(
        p.name for p in outdir.iterdir()
        if p.is_file() and (p.name not in before or p.name.startswith(slug))
    )


def _slot_payload(storage, project_id, kind):
    """Bound textures for the viewport, as hex -> {name, url}.

    There are no UVs in the exported geometry and no texture binding in the
    model or prop grammar, so this is the only route from "this colour is
    oxidized copper" to a textured surface on screen. The client generates
    planar UVs from world position, which is exact for axis-aligned voxel
    faces and good enough for a swept solid.
    """
    try:
        binds = storage.get_meta(project_id, "slot_bindings") or {}
        if not binds:
            return {}
        by_name = {t["name"]: t for t in storage.list_textures()}
        out = {}
        for hx, name in binds.items():
            t = by_name.get(name)
            if not t:
                continue
            out[hx.upper()] = {
                "name": name,
                "url": f"/api/preview/{t['id']}/{t['head']}",
                # One tile per block: a model measures in voxels (16 to the
                # block), a structure measures in cells (one to the block).
                "tile": 16.0 if kind in ("model", "prop") else 1.0,
            }
        return {"slotTextures": out}
    except Exception:
        return {}


def _light_payload(doc):
    """Emitters and the propagated grid, for the viewport's LIT mode.

    The grid is the game's own light model, not the renderer's: point lights
    give the look, these levels give the truth, and LIT mode shows both so
    what you see matches what the engine will compute.
    """
    try:
        import structurebridge as sb_core
        grid = sb_core.propagate_light(doc)
        survey = sb_core.light_survey(doc, grid)
    except Exception:
        return {"light": None}
    by_mat = {}
    for m in doc.materials.values():
        by_mat[m["id"]] = {"light": int(m.get("light", 0) or 0),
                           "color": m.get("color", "#9AA0A8")}
    emitters = []
    for (x, y, z), cell in doc.cells.items():
        lvl = int(getattr(cell, "light", 0) or 0)
        if lvl > 0:
            emitters.append({"pos": [x, y, z], "level": lvl,
                             "color": by_mat.get(cell.mat, {}).get("color", "#FFD9A0")})
    emitters.sort(key=lambda e: -e["level"])
    # A sparse level-per-cell list; the client reads it into a lookup.
    cells = [[x, y, z, lvl] for (x, y, z), lvl in grid.items() if lvl > 0]
    return {"light": {"max": sb_core.LIGHT_MAX, "threshold": sb_core.DARK_THRESHOLD,
                      "emitters": emitters[:64], "emitterCount": len(emitters),
                      "cells": cells, "survey": survey}}


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
    extra = {}    # track-specific additions, e.g. a model's animation clips

    def _rgba_of(spec, fallback=(210, 214, 222, 255)):
        if not spec:
            return fallback
        try:
            return db_core.parse_color(spec) or fallback
        except Exception:
            return fallback

    # A structure's components are its materials: highlighting "wall" and
    # watching every wall cell light up is the question an operator actually
    # asks of a building.
    if result.kind == "structure" and sb_core is not None:
        doc = result.doc
        bymat = {}
        for (x, y, z), c in doc.cells.items():
            bymat.setdefault(c.mat, []).append((x, y, z, c))
        alias = {m["id"]: a for a, m in doc.materials.items()}
        colour = {m["id"]: m.get("color", "#9AA0A8") for m in doc.materials.values()}
        # `material wall soot_iron_plate color=#3A3F45` names a texture, and
        # the payload used to drop it - so the viewport could only ever show
        # the colour, and a building with every texture declared still
        # rendered as flat paint with no way to tell.
        texname = {m["id"]: (m.get("texture") or "") for m in doc.materials.values()}
        litlvl = {m["id"]: int(m.get("light", 0) or 0) for m in doc.materials.values()}
        colors = []
        for mid, cells in sorted(bymat.items()):
            lo, hi, n0 = [1e9] * 3, [-1e9] * 3, len(tris)
            for (x, y, z, c) in cells:
                for t in sb_core.form_tris(c.form, c.ori):
                    for p in t:
                        wp = (p[0] + x, p[1] + y, p[2] + z)
                        tris.extend(wp)
                        for i in range(3):
                            lo[i] = min(lo[i], wp[i])
                            hi[i] = max(hi[i], wp[i])
            nvert = (len(tris) - n0) // 3
            rgba = _rgba_of(colour.get(mid, "#9AA0A8"))
            colors.extend([rgba[0] / 255.0, rgba[1] / 255.0, rgba[2] / 255.0] * nvert)
            # start and count are in TRIANGLES: the viewport slices the flat
            # arrays with start*9 and count*9. Counting vertices here made
            # every slice three times too long, the colour attribute was
            # dropped as too short, and the whole building rendered white.
            groups.append({
                "name": alias.get(mid, f"material_{mid}"), "kind": "material",
                "start": n0 // 9, "count": nvert // 3,
                "color": colour.get(mid, "#9AA0A8"), "box": [lo, hi],
                "texture": texname.get(mid, ""),
                "light": litlvl.get(mid, 0),
                "cells": len(cells),
            })
        mn = [min(tris[i::3]) for i in range(3)] if tris else [0, 0, 0]
        mx = [max(tris[i::3]) for i in range(3)] if tris else [0, 0, 0]
        # Which blocks this building actually uses, so the UI can show it
        # beside the allow-list instead of making someone read the report.
        counts = {}
        for c in doc.cells.values():
            nm = sb_core.FORMS[c.form][0]
            counts[nm] = counts.get(nm, 0) + 1
        return {"ok": True, "kind": "structure", "up": "Y",
                "positions": tris, "colors": colors, "groups": groups,
                "min": mn, "max": mx, "formCounts": counts,
                "cellCount": len(doc.cells),
                **_light_payload(doc)}

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

        # The clips themselves. The compiler has had real keyframe animation
        # all along - it exports proper glTF channels - but the viewport was
        # never sent them, so the scrubber scrubbed a number that drove
        # nothing. Per-bone groups above already carry parent and pivot, which
        # is everything needed to pose them here.
        clips = {}
        for aname, anim in (getattr(doc, "anims", None) or {}).items():
            keys = []
            for k in anim.keys:
                keys.append({
                    "t": float(getattr(k, "t", 0.0) or 0.0),
                    "bone": getattr(k, "bone", "") or "",
                    "rot": [float(x) for x in (getattr(k, "rot", None) or ())],
                    "pos": [float(x) for x in (getattr(k, "pos", None) or ())],
                })
            keys.sort(key=lambda d: d["t"])
            clips[aname] = {
                "loop": bool(getattr(anim, "loop", False)),
                "duration": max([d["t"] for d in keys], default=0.0),
                "keys": keys,
            }
        extra["animations"] = clips

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
        **extra,
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
                CREATE TABLE IF NOT EXISTS folders (
                    path TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                -- A PROJECT is a top-level folder treated as a repository:
                -- every asset under its root belongs to it. The `projects`
                -- table above is assets (it predates this and is not worth
                -- renaming), so the container is `workspaces` internally and
                -- "project" everywhere a person reads it.
                -- A style lock has a SCOPE: '' is the whole workspace,
                -- prj:<id> one project, dir:<path> one folder and everything
                -- under it. They layer general to specific, so a region can
                -- override the house style without restating it.
                CREATE TABLE IF NOT EXISTS style_locks (
                    scope TEXT PRIMARY KEY,
                    body TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    root TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS asset_meta (
                    project_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, key)
                );
                CREATE TABLE IF NOT EXISTS palettes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    inherit INTEGER NOT NULL DEFAULT 1,
                    active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS blocks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    origin TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
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
                    # Sorting by name is the wrong only option: the asset you
                    # made ten minutes ago is found by time, not spelling.
                    "createdAt": p["created_at"] if "created_at" in p.keys() else "",
                    "updatedAt": p["updated_at"] if "updated_at" in p.keys() else "",
                }

        return {
            "ok": True,
            "engine": "DESIGNBRIDGE_CORE" if HAS_DESIGNBRIDGE else "SQLITE_DISK_ENGINE",
            "dbPath": str(self.db_path),
            "workspaceDir": str(self.root),
            "settings": settings,
            "projects": projects,
            "blocks": self.list_blocks(),
            # Folders a person made that hold nothing yet. Derived folders -
            # the ones implied by an asset's path - are still derived; these
            # are only the ones that would otherwise vanish for being empty.
            "folders": self.list_folders(),
            "workspaces": self.list_workspaces(),
            "batches": self.list_batches(),
        }

    # ------------------------------------------------------------------
    # DSL EXTRACTION, COMPILATION & ARTIFACT WRITER
    # ------------------------------------------------------------------
    @staticmethod
    def extract_dsl(raw_text: str) -> str:
        if not raw_text:
            return ""
        return strip_sentinels(raw_text).rstrip()

    def _compile_dsl(self, track: str, dsl: str, parent_commit: dict,
                     style: dict = None) -> CompileResult:
        """Compile a revision for real. No guessing, no fabricated PASS.

        The previous build only reached designbridge when the source did NOT
        carry the <<<DESIGN>>> sentinels - which is every revision the UI
        produces - so in practice the compiler never ran and every commit was
        stamped with a hardcoded "compiled cleanly" report. compile_source()
        strips the sentinels itself and always runs the real compiler, so a
        broken design now says so and carries the real diagnostics.
        """
        return compile_source(dsl, style)

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
    # ------------------------------------------------------------------
    # REUSE LIBRARY, TRACK SWITCHING, BULK CREATE & BULK EXPORT
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # BULK RENAME - a JSON mapping, and the prompt that produces one
    # ------------------------------------------------------------------
    def selfcheck_brief(self, payload: dict) -> dict:
        """The text that turns a blind model into one that checks its own work.

        Everything the reviews asked for already existed; it was simply on the
        wrong side of a copy-paste boundary. This hands the model the compiler
        and tells it how to run it, with the brief's own complexity, palette
        and target size filled in so the checks are the real ones.
        """
        style = normalize_style(payload.get("style"))
        lock = payload.get("palette") or []
        if isinstance(lock, str):
            lock = [c.strip() for c in lock.split(",") if c.strip()]
        size = (payload.get("size") or "").strip()
        pid = (payload.get("projectId") or "").strip()
        kind = (payload.get("kind") or payload.get("track") or "model").lower()
        if kind not in TRACK_KINDS:
            kind = "model"

        flags = [f"--complexity {style['complexity']}"]
        if lock:
            flags.append('--palette "' + ",".join(lock[:24]) + '"')
        if size:
            flags.append(f"--size {size}")
        flags.append("--sheet look.png")
        flag_line = " \\\n    ".join(flags)

        name = (pid or "doc")
        ext = ".sb" if kind == "structure" else ".db"
        files = ("three files: designbridge.py (the compiler), structurebridge.py "
                 "(the building compiler) and dbcheck.py (everything they do not\ncheck)"
                 if kind == "structure" else
                 "two files: designbridge.py (the compiler this project uses) and\n"
                 "dbcheck.py (everything the compiler does not check)")
        return {"ok": True, "prompt": f"""BEFORE YOU ANSWER: CHECK YOUR OWN WORK

You have {files}. Plain Python, standard
library only, nothing to install. Put them in your working folder.

Write your document to a file, compile it, read what comes back, fix it, and
repeat. Nobody sees the rounds you throw away - only the document that passes.

    # write the document
    cat > {name}{ext} <<'END'
    ...your design document...
    END

    # does it compile? what does it look like?
    python3 {"structurebridge.py check" if kind == "structure" else "designbridge.py check"} {name}{ext}

    # the things nobody can hold in their head
    python3 dbcheck.py {name}{ext} \\
    {flag_line}

The first command prints every error with a fix hint, the bone tree, the size,
and three ASCII silhouettes - a real projection of the geometry, so a part in
the wrong place shows up there.

The second checks what it cannot: boxes per bone against the cap, the total
against the budget, bones parented to nothing that will not move when the body
does, boxes below the ground or floating unattached, colours outside the locked
palette (and the nearest one that is allowed), the bounding box against the
brief, and two animation keys for one bone at one time.

look.png is four camera angles in one image. If you can see images, open it.
One view is not enough - a lens and the hood over it can point opposite ways
and look perfectly fine from the front.

Stop when dbcheck prints READY. Then send the document, and only the document,
in one fenced block.

The budget figures are not advice. The studio runs these same checks when your
document arrives, so a document that fails here fails there too.
"""}

    def rename_brief(self, payload: dict) -> dict:
        """The prompt that teaches a text AI to emit the rename mapping.

        Naming forty assets by hand is the kind of work a language model is
        actually good at, but only if it is told the exact shape to answer in
        and given the real ids - a mapping keyed on invented ids renames
        nothing. So the prompt carries the current inventory and the contract,
        and nothing else.
        """
        scope = (payload.get("scope") or "assets").lower()
        ids = [str(x) for x in (payload.get("ids") or []) if str(x).strip()]
        target = (payload.get("projectId") or "").strip()
        hint = (payload.get("hint") or "").strip()

        with self._connect() as conn:
            if scope == "versions":
                pid = target or (ids[0] if ids else "")
                p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
                if not p:
                    raise ValueError("Open the asset whose versions you want renamed first.")
                rows = conn.execute(
                    "SELECT id, tag, branch, status, note, struct FROM commits WHERE project_id=? ORDER BY seq",
                    (pid,)).fetchall()
                # A version selection narrows the inventory: asking a model to
                # name forty revisions when three are selected wastes the
                # answer and invites it to rename the wrong ones.
                picked = {str(x) for x in (payload.get("commitIds") or [])}
                if picked:
                    rows = [r for r in rows if r["id"] in picked] or rows
                inv = [f"  {r['tag']:<6} {r['branch']:<10} {r['status']:<5} {r['struct'] or '-':<14} {r['note'] or ''}"
                       for r in rows]
                head = (f"RENAME THE VERSIONS OF {p['name']}\n"
                        f"  {len(rows)} revision(s). A version's note is what the operator reads in the\n"
                        "  history and the branch map, so it must say what changed, not what the asset is.")
                contract = (
                    'ANSWER WITH JSON AND NOTHING ELSE, in one fenced block:\n'
                    '```json\n'
                    '{\n'
                    '  "versions": [\n'
                    '    { "tag": "v3", "note": "wider shoulders, gold visor" },\n'
                    '    { "tag": "v4", "note": "thinner legs", "newTag": "v4-slim" }\n'
                    '  ]\n'
                    '}\n'
                    '```\n'
                    "  tag      which revision to rename - copy it exactly from the list above\n"
                    "  note     the new note. One line, under 60 characters, plain words\n"
                    "  newTag   optional. Only when a label reads better than vN; must stay unique\n"
                    "  Leave out any revision you would not change. Never invent a tag."
                )
            else:
                if ids:
                    rows = conn.execute(
                        f"SELECT * FROM projects WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
                if not rows:
                    raise ValueError("Nothing selected to rename.")
                inv = []
                for r in rows:
                    c = conn.execute(
                        "SELECT tag, struct FROM commits WHERE project_id=? ORDER BY seq DESC LIMIT 1",
                        (r["id"],)).fetchone()
                    folder = (r["folder"] if "folder" in r.keys() else "") or "-"
                    inv.append(f"  {r['id']:<22} name={r['name']:<22} track={r['track']:<8} "
                               f"folder={folder:<18} head={c['tag'] if c else '-'} {c['struct'] if c else ''}")
                head = (f"RENAME {len(rows)} ASSET(S)\n"
                        "  An asset's name is a filename: lower snake_case, no spaces, no punctuation\n"
                        "  beyond underscores. It is what every export is called, so it should read as\n"
                        "  what the thing is, not as a sentence about it.")
                contract = (
                    'ANSWER WITH JSON AND NOTHING ELSE, in one fenced block:\n'
                    '```json\n'
                    '{\n'
                    '  "assets": [\n'
                    '    { "id": "untitled_model_9f2a", "name": "slag_crane" },\n'
                    '    { "id": "wall_tile", "name": "soot_concrete_tile", "folder": "STRATA / foundry" }\n'
                    '  ]\n'
                    '}\n'
                    '```\n'
                    "  id      which asset to rename - copy it exactly from the list above\n"
                    "  name    the new name. lower snake_case, 2-4 words, no extension\n"
                    "  folder  optional. Include only when the asset belongs somewhere else\n"
                    "  Leave out any asset you would not change. Never invent an id."
                )

        parts = [
            "You are naming the contents of a game asset repository.",
            "",
            head,
            "",
            "THE INVENTORY - these are the real ids. Copy them exactly.",
            "\n".join(inv),
        ]
        if hint:
            parts += ["", "WHAT THE OPERATOR WANTS", "  " + hint]
        parts += [
            "",
            "HOW TO NAME",
            "  Say what the thing is, at the level someone browsing a folder needs.",
            "  Keep a family consistent: if two assets are parts of one machine, their",
            "  names should sort next to each other.",
            "  Do not number things that are not a sequence, and do not restate the",
            "  track - a model called `drone_model` is saying the same thing twice.",
            "",
            contract,
        ]
        return {"ok": True, "prompt": "\n".join(parts), "scope": scope}

    @staticmethod
    def _parse_rename_json(raw, scope: str) -> list:
        """Read the mapping out of whatever the model actually sent.

        Models wrap JSON in prose and fences, and they pick their own container
        key. Rather than reject that, pull the first JSON object or array out of
        the text and accept any of the shapes a reasonable model would choose,
        including a bare `{"old": "new"}` map.
        """
        text = (raw or "").strip()
        if not text:
            raise ValueError("Nothing pasted.")
        m = re.search(r"```[a-zA-Z0-9_\-]*\n(.*?)```", text, re.S)
        if m:
            text = m.group(1).strip()
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise ValueError("No JSON found in that text.")
        end = max(text.rfind("}"), text.rfind("]"))
        try:
            data = json.loads(text[start:end + 1])
        except Exception as exc:
            raise ValueError(f"That is not valid JSON: {exc}")

        items = None
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("versions", "assets", "renames", "items", "mapping", "projects"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
            if items is None:
                nested = data.get("versions") or data.get("assets") or data.get("mapping")
                if isinstance(nested, dict):
                    data = nested
                # A flat {"old": "new"} map.
                items = [{"id": k, "name": v} if scope != "versions" else {"tag": k, "note": v}
                         for k, v in data.items() if isinstance(v, str)]
        if not items:
            raise ValueError("The JSON held no rename entries.")

        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            if scope == "versions":
                tag = str(it.get("tag") or it.get("version") or it.get("id") or "").strip()
                if not tag:
                    continue
                out.append({
                    "tag": tag,
                    "note": (str(it["note"]).strip() if it.get("note") is not None else None),
                    "newTag": (str(it["newTag"]).strip() if it.get("newTag") else None),
                    "asset": str(it.get("asset") or it.get("projectId") or "").strip(),
                })
            else:
                pid = str(it.get("id") or it.get("projectId") or it.get("asset") or "").strip()
                if not pid:
                    continue
                out.append({
                    "id": pid,
                    "name": (str(it["name"]).strip() if it.get("name") else None),
                    "folder": (str(it["folder"]).strip() if it.get("folder") is not None else None),
                })
        if not out:
            raise ValueError("No usable entries - every one was missing its id or tag.")
        return out

    def rename_commit(self, project_id: str, tag: str, new_tag=None, new_note=None) -> None:
        """Retag or re-note one revision. Tags stay unique inside a project."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM commits WHERE project_id=? AND tag=?", (project_id, tag)).fetchone()
            if not row:
                raise ValueError(f"No revision tagged {tag!r}")
            if new_tag and new_tag != tag:
                clash = conn.execute(
                    "SELECT 1 FROM commits WHERE project_id=? AND tag=? AND id<>?",
                    (project_id, new_tag, row["id"])).fetchone()
                if clash:
                    raise ValueError(f"{new_tag!r} is already used in this asset")
                conn.execute("UPDATE commits SET tag=? WHERE id=?", (new_tag, row["id"]))
            if new_note is not None:
                conn.execute("UPDATE commits SET note=? WHERE id=?", (new_note, row["id"]))
            fresh = conn.execute("SELECT * FROM commits WHERE id=?", (row["id"],)).fetchone()
        # meta.json on disk carries the tag and note, so it follows the rename.
        try:
            cdir = self.projects_dir / project_id / "commits" / row["id"]
            if (cdir / "meta.json").is_file():
                meta = json.loads((cdir / "meta.json").read_text(encoding="utf-8"))
                meta["tag"] = fresh["tag"]
                meta["note"] = fresh["note"]
                (cdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except Exception:
            pass

    def set_commit_label(self, payload: dict) -> dict:
        """Relabel one revision, by id, at any time.

        A note used to be writable only in the moment a revision was created,
        which is the one moment you know least about it. What a revision turned
        out to be is usually clear later, so the label has to stay editable.
        """
        pid = (payload.get("projectId") or "").strip()
        cid = (payload.get("commitId") or "").strip()
        note = payload.get("note")
        tag = (payload.get("tag") or "").strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM commits WHERE project_id=? AND id=?", (pid, cid)).fetchone()
            if not row:
                raise ValueError("No such revision")
        self.rename_commit(pid, row["tag"],
                           tag or None,
                           None if note is None else str(note).strip())
        try:
            self._write_project_manifest(pid)
        except Exception:
            pass
        return self.get_bootstrap_state()

    def apply_rename(self, payload: dict) -> dict:
        """Apply a parsed mapping. Every entry stands or fails on its own."""
        scope = (payload.get("scope") or "assets").lower()
        allowed = {str(x) for x in (payload.get("ids") or []) if str(x).strip()}
        target = (payload.get("projectId") or "").strip()
        ops = self._parse_rename_json(payload.get("json") or payload.get("text"), scope)

        results = []
        touched = set()
        for op in ops:
            try:
                if scope == "versions":
                    pid = op["asset"] or target
                    if not pid:
                        raise ValueError("no target asset")
                    self.rename_commit(pid, op["tag"], op["newTag"], op["note"])
                    touched.add(pid)
                    results.append({
                        "key": f"{pid} {op['tag']}",
                        "to": " · ".join(x for x in (op["newTag"], op["note"]) if x) or "(nothing to change)",
                        "ok": True, "error": "",
                    })
                else:
                    pid = op["id"]
                    if allowed and pid not in allowed:
                        raise ValueError("not in the selection")
                    self.rename_project(pid, op["name"] or "", folder=op["folder"])
                    touched.add(pid)
                    with self._connect() as conn:
                        r = conn.execute("SELECT name, folder FROM projects WHERE id=?", (pid,)).fetchone()
                    results.append({
                        "key": pid,
                        "to": (r["name"] if r else op["name"] or "")
                             + (f" · {op['folder'] or 'unfiled'}" if op["folder"] is not None else ""),
                        "ok": True, "error": "",
                    })
            except Exception as exc:
                results.append({
                    "key": op.get("id") or op.get("tag") or "?",
                    "to": "", "ok": False, "error": str(exc),
                })
        for pid in touched:
            try:
                self._write_project_manifest(pid)
            except Exception:
                pass
        boot = self.get_bootstrap_state()
        boot["renames"] = results
        return boot

    def bulk_project_action(self, payload: dict) -> dict:
        """Apply one action to a whole selection.

        A selection that can only be exported is half a feature: the reason to
        select eleven assets is usually to move, retrack or delete all eleven.
        Each asset is handled independently so one failure does not abandon the
        rest, and what failed is named.
        """
        ids = [str(x) for x in (payload.get("ids") or []) if str(x).strip()]
        action = (payload.get("action") or "").lower()

        # Renaming a folder is renaming a path prefix: every asset at that
        # path, and everything nested under it, moves together. Folders are
        # not rows in a table - they exist only as the paths assets carry -
        # so this is the one place that has to know about the separator.
        if action == "refolder":
            src = (payload.get("from") or "").strip().strip("/")
            dst = (payload.get("to") or "").strip().strip("/")
            if not src:
                raise ValueError("No folder to rename.")
            if not dst:
                raise ValueError("A folder needs a name. Move assets to Unfiled instead.")
            moved, clashes = [], []
            with self._connect() as conn:
                rows = conn.execute("SELECT id, folder FROM projects").fetchall()
            for r in rows:
                cur = (r["folder"] or "").strip().strip("/")
                if cur != src and not cur.startswith(src + "/"):
                    continue
                nxt = dst + cur[len(src):]
                try:
                    self.rename_project(r["id"], "", folder=nxt)
                    moved.append(r["id"])
                except Exception as exc:
                    clashes.append(f"{r['id']}: {exc}")
            with self._connect() as conn:
                rows = conn.execute("SELECT path FROM folders").fetchall()
                for r in rows:
                    cur = r["path"]
                    if cur != src and not cur.startswith(src + "/"):
                        continue
                    conn.execute("DELETE FROM folders WHERE path=?", (cur,))
                    conn.execute(
                        "INSERT OR IGNORE INTO folders (path, created_at) VALUES (?, ?)",
                        (dst + cur[len(src):], now_iso()))
            boot = self.get_bootstrap_state()
            boot["done"] = moved
            boot["failed"] = clashes
            return boot

        if not ids:
            raise ValueError("Nothing selected.")
        done, failed = [], []
        if action == "delete":
            with self._connect() as conn:
                total = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
            if len(ids) >= total:
                raise ValueError(
                    f"That would delete every asset in the workspace ({total}). "
                    "Leave at least one behind."
                )
        for pid in ids:
            try:
                if action == "delete":
                    self.delete_project(pid)
                elif action == "move":
                    self.rename_project(pid, "", folder=(payload.get("folder") or ""))
                elif action == "track":
                    self.set_project_track({"projectId": pid, "track": payload.get("track")})
                else:
                    raise ValueError(f"Unknown action {action!r}")
                done.append(pid)
            except Exception as exc:
                failed.append(f"{pid}: {exc}")
        boot = self.get_bootstrap_state()
        boot["done"] = done
        boot["failed"] = failed
        return boot

    def batch_folder(self, base: str, label: str = "") -> tuple:
        """A dated subfolder for one import, so a batch stays together.

        Everything pasted in one go used to scatter into whatever folder was
        selected, mixed in with work from days ago. A batch now gets its own
        folder named for when it arrived, which is also the grouping the
        sidebar shows.
        """
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        slug = "".join(ch if (ch.isalnum() or ch in "-_") else "-"
                       for ch in (label or "")).strip("-")
        name = f"batch_{stamp}" + (f"_{slug[:24]}" if slug else "")
        path = normalize_folder(f"{base}/{name}" if base else name)
        self._remember_folder(path)
        return path, f"bat_{uuid.uuid4().hex[:8]}"

    def stamp_batch(self, ids: list, batch_id: str, folder: str, label: str = "") -> None:
        info = {"id": batch_id, "folder": folder, "label": label, "at": now_iso(),
                "size": len(ids)}
        for pid in ids:
            self.set_meta(pid, "batch", info)

    def list_batches(self) -> list:
        """Every import group, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id, value_json FROM asset_meta WHERE key='batch'").fetchall()
        groups = {}
        for r in rows:
            try:
                d = json.loads(r["value_json"])
            except Exception:
                continue
            g = groups.setdefault(d.get("id", "?"), {
                "id": d.get("id", "?"), "folder": d.get("folder", ""),
                "label": d.get("label", ""), "at": d.get("at", ""), "assets": []})
            g["assets"].append(r["project_id"])
        out = sorted(groups.values(), key=lambda g: g["at"], reverse=True)
        for g in out:
            g["size"] = len(g["assets"])
        return out

    def intake_documents(self, payload: dict) -> dict:
        """Take a pasted blob of AI output and file every document in it.

        This is the other half of the round trip. The prompt goes out in one
        copy; the answer can come back carrying six assets or three takes on
        one, and until now each had to be pasted separately. Every document is
        compiled for real, and a document that fails is still filed with its
        diagnostics rather than dropped - a failed revision is information.

        Routing, by `mode`:
          auto     - a document whose name matches an existing asset becomes a
                     new version of it; anything else becomes a new asset.
          versions - every document becomes a successive version of one asset,
                     in the order pasted. Several takes on the same thing.
          assets   - every document becomes its own new asset.
        """
        text = payload.get("text") or ""
        mode = (payload.get("mode") or "auto").lower()
        if mode not in ("auto", "versions", "assets"):
            mode = "auto"
        folder = normalize_folder(payload.get("folder"))
        # One import, one dated folder, so a batch stays together instead of
        # scattering into whatever folder happened to be selected.
        batch_id = ""
        if payload.get("groupBatch"):
            folder, batch_id = self.batch_folder(folder, payload.get("batchLabel") or "")
        track = (payload.get("track") or "auto").lower()
        if track not in TRACK_KINDS and track != "auto":
            track = "auto"
        target = (payload.get("projectId") or "").strip()
        note = (payload.get("note") or "").strip()

        docs = split_documents(text)
        if not docs:
            raise ValueError(
                "No design documents found in that text. A document starts with "
                "`model`, `design` or `texture`, ideally inside a fenced block."
            )
        if len(docs) > 24:
            raise ValueError(f"{len(docs)} documents is more than one intake should carry. Keep it under 24.")

        with self._connect() as conn:
            rows = conn.execute("SELECT id, name FROM projects").fetchall()
        by_name = {}
        for r in rows:
            by_name[r["name"]] = r["id"]
            by_name[r["id"]] = r["id"]

        results = []
        for i, doc in enumerate(docs, 1):
            name = document_name(doc)
            kind = detect_kind_from_source(doc) or "model"
            try:
                if mode == "versions":
                    pid = target or by_name.get(name) or ""
                    if not pid:
                        raise ValueError("No target asset for a versions intake")
                    route = "version"
                elif mode == "assets":
                    pid, route = "", "asset"
                else:
                    pid = by_name.get(name, "")
                    route = "version" if pid else "asset"

                if route == "asset":
                    pid = self._insert_project({
                        "name": name,
                        "track": track,
                        "brief": f"Intake: pasted {kind} document" + (f" {name}" if name else ""),
                        "folder": folder,
                        "source": doc,
                        "note": note or f"Intake {i} of {len(docs)}",
                    })
                    by_name.setdefault(name or pid, pid)
                    with self._connect() as conn:
                        c = conn.execute(
                            "SELECT tag, status, errors FROM commits WHERE project_id=? ORDER BY seq DESC LIMIT 1",
                            (pid,)).fetchone()
                else:
                    self.compile_and_save({
                        "projectId": pid,
                        "rawAiResponse": doc,
                        "userPrompt": note or f"Intake {i} of {len(docs)}",
                        "note": note or f"Intake {i} of {len(docs)}",
                        "mode": "new_take" if mode == "versions" else "edit",
                        "style": payload.get("style"),
                    })
                    with self._connect() as conn:
                        c = conn.execute(
                            "SELECT tag, status, errors FROM commits WHERE project_id=? ORDER BY seq DESC LIMIT 1",
                            (pid,)).fetchone()
                results.append({
                    "index": i, "name": name or pid, "projectId": pid, "kind": kind,
                    "route": route, "tag": c["tag"] if c else "", 
                    "status": c["status"] if c else "FAIL",
                    "errors": c["errors"] if c else 1, "error": "",
                })
            except Exception as exc:
                results.append({
                    "index": i, "name": name or f"document {i}", "projectId": "", "kind": kind,
                    "route": "skipped", "tag": "", "status": "FAIL", "errors": 1, "error": str(exc),
                })

        if mode == "versions" and target:
            self.update_settings({"activeProjectId": target})
        if batch_id:
            made = [r["projectId"] for r in results if r.get("projectId")]
            if made:
                self.stamp_batch(made, batch_id, folder, payload.get("batchLabel") or "")
        boot = self.get_bootstrap_state()
        boot["intake"] = results
        boot["batchFolder"] = folder if batch_id else ""
        return boot

    MAX_FOLDER_DEPTH = 5

    def list_textures(self) -> list:
        """Texture assets a structure's materials can bind to.

        Only the ones whose head revision compiles: offering a broken texture
        as a binding target wastes a round when the building references it.
        """
        out = []
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
            for p in rows:
                c = conn.execute(
                    "SELECT dsl, status FROM commits WHERE id=?", (p["head_commit_id"],)
                ).fetchone()
                if not c or c["status"] != "PASS":
                    continue
                # What the document says it is beats what the project is
                # filed as: a model sitting under track=texture is a model,
                # and offering it as a binding target would be a lie.
                kind = detect_kind_from_source(c["dsl"] or "") or p["track"]
                if kind != "texture":
                    continue
                hexes = palette_of(c["dsl"] or "")
                out.append({"name": p["name"],
                            "folder": (p["folder"] if "folder" in p.keys() else "") or "",
                            # id and head let a caller read this texture's
                            # intent and fetch its PNG for the viewport.
                            "id": p["id"],
                            "head": p["head_commit_id"],
                            "colors": hexes[:4]})
        return out

    def list_folders(self) -> list:
        with self._connect() as conn:
            try:
                rows = conn.execute("SELECT path FROM folders ORDER BY path").fetchall()
            except Exception:
                return []
        return [r["path"] for r in rows]

    def create_folder(self, payload: dict) -> dict:
        """Make a folder that exists before anything is in it.

        Folders used to be nothing but the path string each asset carried, so
        an empty one could not exist and there was no way to make a sub-folder
        before you had something to put in it. They are rows now; the tree
        still merges them with the paths assets imply.
        """
        path = normalize_folder(payload.get("path"))
        if not path:
            raise ValueError("A folder needs a name.")
        depth = len(path.split("/"))
        if depth > self.MAX_FOLDER_DEPTH:
            raise ValueError(
                f"{depth} levels deep - the limit is {self.MAX_FOLDER_DEPTH}. "
                "Past that a tree is harder to read than a flat list.")
        with self._connect() as conn:
            # Every parent on the way down exists too, so a path typed in one
            # go does not leave gaps in the tree.
            segs = path.split("/")
            for i in range(len(segs)):
                p = "/".join(segs[:i + 1])
                conn.execute(
                    "INSERT OR IGNORE INTO folders (path, created_at) VALUES (?, ?)",
                    (p, now_iso()))
        return self.get_bootstrap_state()

    def delete_folder(self, payload: dict) -> dict:
        """Forget a folder row. Assets filed under it are not touched - they
        keep their path, so the folder simply goes back to being derived."""
        path = normalize_folder(payload.get("path"))
        with self._connect() as conn:
            conn.execute("DELETE FROM folders WHERE path=? OR path LIKE ?",
                         (path, path + "/%"))
        return self.get_bootstrap_state()

    def list_blocks(self) -> list:
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM blocks ORDER BY created_at DESC"
                ).fetchall()
            except Exception:
                return []
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "kind": r["kind"],
                "source": r["source"],
                "note": r["note"],
                "origin": r["origin"],
                "createdAt": r["created_at"],
            }
            for r in rows
        ]

    def save_block(self, payload: dict) -> dict:
        source = (payload.get("source") or "").strip()
        if not source:
            raise ValueError("A block needs source to reuse. Nothing was selected.")
        name = (payload.get("name") or "").strip() or f"block_{uuid.uuid4().hex[:4]}"
        kind = (payload.get("kind") or "").lower()
        if kind not in TRACK_KINDS:
            kind = detect_kind_from_source(source) or ""
        bid = "b" + uuid.uuid4().hex[:8]
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO blocks (id, name, kind, source, note, origin, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (bid, name, kind, source, (payload.get("note") or "").strip(),
                 (payload.get("origin") or "").strip(), now_iso()),
            )
        return self.get_bootstrap_state()

    def delete_block(self, payload: dict) -> dict:
        bid = (payload.get("id") or "").strip()
        with self._connect() as conn:
            conn.execute("DELETE FROM blocks WHERE id=?", (bid,))
        return self.get_bootstrap_state()

    def retarget_texture(self, old_name: str, new_name: str) -> list:
        """Follow a renamed texture into every building that binds to it.

        A structure's materials name textures by the asset name they live
        under, so renaming a texture breaks every building that referenced it
        - silently, because the document still compiles. The reference is
        rewritten and committed as its own revision, which is auditable and
        reversible, rather than edited in place behind the operator's back.
        """
        if not old_name or not new_name or old_name == new_name:
            return []
        pat = re.compile(r"^(\s*material\s+\S+\s+)" + re.escape(old_name) + r"\b",
                         re.M)
        touched = []
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name, head_commit_id FROM projects").fetchall()
            heads = {r["id"]: conn.execute(
                "SELECT id, dsl FROM commits WHERE id=?", (r["head_commit_id"],)).fetchone()
                for r in rows}
        for r in rows:
            c = heads.get(r["id"])
            if not c or not c["dsl"]:
                continue
            if detect_kind_from_source(c["dsl"]) != "structure":
                continue
            if not pat.search(c["dsl"]):
                continue
            fixed = pat.sub(lambda m: m.group(1) + new_name, c["dsl"])
            try:
                self.compile_and_save({
                    "projectId": r["id"], "rawAiResponse": fixed, "mode": "edit",
                    "note": f"Texture {old_name} renamed to {new_name}",
                    "userPrompt": f"Reference update: {old_name} -> {new_name}",
                })
                touched.append(r["name"])
            except Exception:
                pass
        return touched

    def set_project_track(self, payload: dict) -> dict:
        """Change an asset's track after the fact, AUTO included.

        A project is not a commitment to one kind of asset for life: a prop
        that turns out to need bones becomes a model without being rebuilt,
        and AUTO lets one project hold all three.
        """
        pid = (payload.get("projectId") or payload.get("project_id") or "").strip()
        track = (payload.get("track") or "").lower()
        if track not in TRACK_KINDS and track != "auto":
            raise ValueError(f"Unknown track {track!r}")
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
            if not row:
                raise ValueError(f"No such asset {pid!r}")
            conn.execute(
                "UPDATE projects SET track=?, icon=?, updated_at=? WHERE id=?",
                (track, TRACK_ICONS.get(track, "view_in_ar"), now_iso(), pid),
            )
        return self.get_bootstrap_state()

    def create_projects_bulk(self, payload: dict) -> dict:
        """Create many assets from one pasted list.

        Each line is one asset. `name: brief` names it; a bare line is a brief
        and the asset adopts its name from the document it compiles. Every
        asset lands in the same folder on the same track, which is the whole
        point - a batch is a set of siblings.
        """
        raw = payload.get("items")
        if isinstance(raw, str):
            raw = [raw]
        lines = []
        for chunk in (raw or []):
            for line in str(chunk).splitlines():
                line = line.strip().lstrip("-*\u2022 ").strip()
                if line:
                    lines.append(line)
        if not lines:
            raise ValueError("Nothing to create - the list was empty.")
        if len(lines) > 40:
            raise ValueError(f"{len(lines)} items is more than one batch should carry. Keep it under 40.")
        track = (payload.get("track") or "auto").lower()
        folder = normalize_folder(payload.get("folder"))
        batch_id = ""
        if payload.get("groupBatch"):
            folder, batch_id = self.batch_folder(folder, payload.get("batchLabel") or "")
        made = []
        for line in lines:
            name, brief = "", line
            m = re.match(r"^([A-Za-z0-9 _\-]{1,40}?)\s*[:\u2013\u2014]\s*(.+)$", line)
            if m:
                name, brief = m.group(1).strip(), m.group(2).strip()
            pid = self._insert_project({"name": name, "track": track, "brief": brief, "folder": folder})
            made.append(pid)
        if batch_id and made:
            self.stamp_batch(made, batch_id, folder, payload.get("batchLabel") or "")
        boot = self.get_bootstrap_state()
        boot["created"] = made
        return boot

    # ------------------------------------------------------------------
    # REVISIONS AS FIRST-CLASS THINGS: promote, delete, export by version
    # ------------------------------------------------------------------
    def promote_commits(self, payload: dict) -> dict:
        """Lift revisions out of an asset and make each one its own asset.

        Making a new version is two clicks and making a new asset is a modal,
        so operators do the sane thing and explore separate designs as versions
        of whatever happened to be open. That is good practice, not a mistake -
        it just needs an exit. Each promoted revision keeps its document exactly
        and becomes v1 of a new asset; the original is left untouched, because a
        promotion is a copy, not a move.
        """
        pid = (payload.get("projectId") or "").strip()
        cids = [str(x) for x in (payload.get("commitIds") or []) if str(x).strip()]
        folder = payload.get("folder")
        track = (payload.get("track") or "").lower()
        if not cids:
            raise ValueError("No revisions selected.")

        with self._connect() as conn:
            proj = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if not proj:
                raise ValueError("Project not found")
            rows = conn.execute(
                f"SELECT * FROM commits WHERE project_id=? AND id IN ({','.join('?' * len(cids))}) ORDER BY seq",
                [pid] + cids).fetchall()
        if not rows:
            raise ValueError("Those revisions are not in this asset.")

        if folder is None:
            folder = (proj["folder"] if "folder" in proj.keys() else "") or ""
        if track not in TRACK_KINDS and track != "auto":
            track = proj["track"]

        # A promotion must not steal the operator's place: _insert_project
        # makes each new asset active, so the asset they were working in is
        # restored once the batch is done.
        was_active = (self.get_settings() or {}).get("activeProjectId") or pid
        results = []
        for r in rows:
            try:
                # A promoted revision usually carries the parent's own name in
                # its document, and `guard_drone_7f2a` tells nobody anything -
                # so a name that would collide becomes `guard_drone_v4`, which
                # says exactly where it came from.
                declared = document_name(r["dsl"] or "")
                name = declared if declared and declared != proj["name"] else f"{proj['name']}_{r['tag']}"
                new_pid = self._insert_project({
                    "name": name,
                    "track": track,
                    "brief": f"Promoted from {proj['name']} {r['tag']}",
                    "folder": folder,
                    "source": r["dsl"] or "",
                    "note": f"Promoted from {proj['name']} {r['tag']} - {(r['note'] or '')[:48]}",
                })
                results.append({"from": r["tag"], "to": new_pid, "ok": True, "error": ""})
            except Exception as exc:
                results.append({"from": r["tag"], "to": "", "ok": False, "error": str(exc)})

        self.update_settings({"activeProjectId": was_active})
        boot = self.get_bootstrap_state()
        boot["promoted"] = results
        return boot

    def delete_commits(self, payload: dict) -> dict:
        """Remove revisions, keeping the history connected.

        A revision is not a leaf: deleting one in the middle would orphan
        everything after it, so its children are re-parented onto its parent
        and any branch or project head pointing at it falls back to the newest
        revision left on that branch. An asset always keeps at least one.
        """
        pid = (payload.get("projectId") or "").strip()
        cids = [str(x) for x in (payload.get("commitIds") or []) if str(x).strip()]
        if not cids:
            raise ValueError("No revisions selected.")
        results = []
        with self._connect() as conn:
            proj = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if not proj:
                raise ValueError("Project not found")
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM commits WHERE project_id=?", (pid,)).fetchone()["n"]
            if len(cids) >= total:
                raise ValueError(
                    f"That would delete every revision of {proj['name']} ({total}). "
                    "An asset has to keep one."
                )
            for cid in cids:
                row = conn.execute(
                    "SELECT * FROM commits WHERE project_id=? AND id=?", (pid, cid)).fetchone()
                if not row:
                    results.append({"tag": cid, "ok": False, "error": "not in this asset"})
                    continue
                conn.execute(
                    "UPDATE commits SET parent_id=? WHERE project_id=? AND parent_id=?",
                    (row["parent_id"], pid, cid))
                conn.execute(
                    "UPDATE commits SET merge_parent_id=? WHERE project_id=? AND merge_parent_id=?",
                    (row["parent_id"], pid, cid))
                conn.execute("DELETE FROM commits WHERE project_id=? AND id=?", (pid, cid))
                conn.execute("DELETE FROM chat_history WHERE project_id=? AND commit_id=?", (pid, cid))
                fallback = conn.execute(
                    "SELECT id FROM commits WHERE project_id=? AND branch=? ORDER BY seq DESC LIMIT 1",
                    (pid, row["branch"])).fetchone()
                if not fallback:
                    fallback = conn.execute(
                        "SELECT id FROM commits WHERE project_id=? ORDER BY seq DESC LIMIT 1",
                        (pid,)).fetchone()
                    conn.execute("DELETE FROM branches WHERE project_id=? AND name=?", (pid, row["branch"]))
                else:
                    conn.execute(
                        "UPDATE branches SET head_commit_id=? WHERE project_id=? AND name=? AND head_commit_id=?",
                        (fallback["id"], pid, row["branch"], cid))
                if proj["head_commit_id"] == cid and fallback:
                    nb = conn.execute("SELECT branch FROM commits WHERE id=?", (fallback["id"],)).fetchone()
                    conn.execute(
                        "UPDATE projects SET head_commit_id=?, active_branch=?, updated_at=? WHERE id=?",
                        (fallback["id"], nb["branch"] if nb else "main", now_iso(), pid))
                results.append({"tag": row["tag"], "ok": True, "error": ""})
                try:
                    shutil.rmtree(self.projects_dir / pid / "commits" / cid, ignore_errors=True)
                except Exception:
                    pass
        try:
            self._write_project_manifest(pid)
        except Exception:
            pass
        boot = self.get_bootstrap_state()
        boot["deleted"] = results
        return boot

    def materials_sidecar(self, project_id: str) -> str:
        """The slot bindings, as a file that ships beside the geometry.

        The binding cannot live in the document - the model and prop
        grammars have no texture syntax - and it cannot live in the GLB,
        which carries no UVs. So it travels as its own small file and the
        engine applies it.
        """
        binds = self.get_meta(project_id, "slot_bindings") or {}
        if not binds:
            return ""
        by_name = {t["name"]: t for t in self.list_textures()}
        slots = []
        for hx, name in sorted(binds.items()):
            t = by_name.get(name) or {}
            slots.append({
                "color": hx,
                "texture": name,
                "textureAsset": t.get("id", ""),
                "target": (self.texture_intent(t["id"]) if t.get("id") else {}).get("target", ""),
            })
        proj = self.get_bootstrap_state()["projects"].get(project_id) or {}
        return json.dumps({
            "format": "strata.materials.v1",
            "asset": proj.get("name", project_id),
            "note": "Bind by vertex colour: every face of this colour uses that texture. "
                    "One tile per block (16 voxels on a model, one cell on a structure).",
            "tileVoxels": 16,
            "slots": slots,
        }, indent=2)

    def zip_name(self, project_ids: list, fmt: str, scope: str = "head",
                 override: str = "") -> str:
        """A download whose name says what is inside it.

        `designbridge_selection_glb.zip` told you nothing and the browser
        turned the second one into `...(1).zip`. The name now carries where
        the assets came from, how many, which revisions and when, so a
        downloads folder stays legible and two exports never collide.
        """
        def slug(t):
            out = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in str(t))
            while "--" in out:
                out = out.replace("--", "-")
            return out.strip("-_")[:48]

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        if override:
            return f"{slug(override)}_{stamp}.zip"

        allp = self.get_bootstrap_state()["projects"]
        rows = [allp[p] for p in project_ids if p in allp]
        folders = {(r.get("folder") or "") for r in rows}
        where = slug(folders.pop().replace("/", "-")) if len(folders) == 1 else ""

        if len(rows) == 1:
            what = slug(rows[0].get("name") or project_ids[0])
        else:
            what = f"{len(rows) or len(project_ids)}-assets"

        bits = [b for b in (where, what, scope, fmt, stamp) if b]
        return "_".join(bits) + ".zip"

    def build_bulk_zip(self, project_ids: list, fmt: str, scope: str = "head",
                       commit_ids: list = None, name: str = "") -> tuple:
        """One zip holding whichever revisions of the selection were asked for.

        Exporting eleven assets one click at a time is not a workflow, so the
        selection exports as a set. `scope` decides how much of each asset's
        history comes along, because both answers are right at different times:
        the latest revision is what goes into the game, and the whole history is
        what you hand to someone else or keep as a backup.

          head      the current head of each asset (the default)
          all       every revision of each asset, each in its own folder
          picked    only the revisions named in commit_ids

        An asset whose revision does not compile is skipped and named in
        MANIFEST.txt rather than silently dropped.
        """
        import io
        import zipfile

        fmt = (fmt or "obj").lower()
        scope = (scope or "head").lower()
        if scope not in ("head", "all", "picked"):
            scope = "head"
        picked = {str(x) for x in (commit_ids or [])}
        buf = io.BytesIO()
        written, skipped = [], []
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for pid in project_ids:
                try:
                    with self._connect() as conn:
                        p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
                        if not p:
                            skipped.append(f"{pid} - no such asset")
                            continue
                        if scope == "all":
                            rows = conn.execute(
                                "SELECT * FROM commits WHERE project_id=? ORDER BY seq", (pid,)).fetchall()
                        elif scope == "picked":
                            rows = [r for r in conn.execute(
                                "SELECT * FROM commits WHERE project_id=? ORDER BY seq", (pid,)).fetchall()
                                if r["id"] in picked]
                        else:
                            rows = [r for r in [conn.execute(
                                "SELECT * FROM commits WHERE id=?", (p["head_commit_id"],)).fetchone()] if r]
                    if not rows:
                        skipped.append(f"{pid} - no revisions matched the version scope")
                        continue
                    many = len(rows) > 1
                    for c in rows:
                        if c["status"] != "PASS":
                            skipped.append(f"{pid} {c['tag']} - does not compile")
                            continue
                        # A mixed selection has no single right format: a texture
                        # has no .obj and a model has no .png. NATIVE gives every
                        # asset the format its own kind actually exports, so one
                        # click works on a folder holding all three tracks.
                        want = fmt
                        if fmt in ("native", "auto"):
                            k = detect_kind_from_source(c["dsl"] or "")
                            want = "png" if k == "texture" else "obj"
                        data, _ctype, fname = self.build_export_bytes(pid, c["id"], want)
                        inner = f"{pid}/{c['tag']}/{fname}" if many else f"{pid}/{fname}"
                        zf.writestr(inner, data)
                        written.append(f"{pid} - {fname} ({c['tag']})")
                        # The texture bindings travel with the geometry.
                        side = self.materials_sidecar(pid)
                        if side:
                            zf.writestr(f"{pid}/materials.json", side)
                except Exception as exc:
                    skipped.append(f"{pid} - {exc}")
            manifest = ["DESIGNBRIDGE STUDIO - BULK EXPORT",
                        f"format: {fmt}   versions: {scope}",
                        f"written: {len(written)}   skipped: {len(skipped)}",
                        ""]
            manifest += ["EXPORTED"] + [f"  {w}" for w in written] if written else ["EXPORTED", "  (none)"]
            if skipped:
                manifest += ["", "SKIPPED"] + [f"  {s}" for s in skipped]
            zf.writestr("MANIFEST.txt", "\n".join(manifest) + "\n")
        return (buf.getvalue(), "application/zip",
                self.zip_name(project_ids, fmt, scope, name))

    def _remember_folder(self, folder: str) -> None:
        folder = normalize_folder(folder)
        if not folder:
            return
        try:
            self.create_folder({"path": folder})
        except Exception:
            pass

    def _insert_project(self, payload: dict) -> str:
        # The name is optional. designbridge documents carry their own name
        # (model "stalker"), so an unnamed asset gets a placeholder now and
        # adopts the real name from the document on first successful compile.
        raw_name = (payload.get("name") or "").strip()
        track = (payload.get("track") or "model").lower()
        if track not in TRACK_KINDS and track != "auto":
            track = "model"
        # An AUTO asset still needs a concrete grammar for its starter
        # document; the brief decides which, and the asset stays AUTO.
        seed_kind = track if track in TRACK_KINDS else (
            guess_kind_from_text(payload.get("brief") or raw_name) or "model")
        auto_named = not raw_name
        if auto_named:
            raw_name = f"untitled_{seed_kind}_{uuid.uuid4().hex[:4]}"
        pid = safe_slug(raw_name)
        folder = normalize_folder(payload.get("folder"))
        self._remember_folder(folder)
        brief = (payload.get("brief") or f"Create a clean {seed_kind} asset named {pid}").strip()
        icon = TRACK_ICONS.get(track, "view_in_ar")
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
            # A document handed in by the caller - a parsed AI output, say -
            # becomes v1 itself, so an intake does not leave a starter stub
            # sitting in front of the real first revision.
            given = (payload.get("source") or "").strip()
            starter = given + "\n" if given else \
                STARTER_SOURCES.get(seed_kind, STARTER_SOURCES["model"]).replace("__NAME__", pid)
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
                "note": (payload.get("note") or f"Intake - {brief[:72]}") if given
                        else f"Starter {track} - brief: {brief[:80]}",
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
        return pid

    def create_project(self, payload: dict) -> dict:
        self._insert_project(payload)
        return self.get_bootstrap_state()

    def rename_project(self, project_id: str, new_name: str, folder=None) -> dict:
        ts = now_iso()
        remember = ""
        was_name, was_texture = "", False
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT name, head_commit_id FROM projects WHERE id=?", (project_id,)).fetchone()
                if row:
                    was_name = row["name"]
                    hc = conn.execute("SELECT dsl FROM commits WHERE id=?",
                                      (row["head_commit_id"],)).fetchone()
                    was_texture = bool(hc) and detect_kind_from_source(hc["dsl"] or "") == "texture"
        except Exception:
            pass
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("Project not found")
            if new_name:
                conn.execute("UPDATE projects SET name=?, updated_at=? WHERE id=?",
                             (safe_slug(new_name), ts, project_id))
            if folder is not None:
                conn.execute("UPDATE projects SET folder=?, updated_at=? WHERE id=?",
                             (normalize_folder(folder), ts, project_id))
                remember = normalize_folder(folder)
        if folder is not None and remember:
            self._remember_folder(remember)
        self._write_project_manifest(project_id)
        # A renamed texture is followed into every building that binds to it,
        # so a rename cannot leave a dangling reference that still compiles.
        retargeted = []
        if was_texture and new_name and safe_slug(new_name) != was_name:
            retargeted = self.retarget_texture(was_name, safe_slug(new_name))
        boot = self.get_bootstrap_state()
        if retargeted:
            boot["retargeted"] = retargeted
        return boot

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

    # ---- animating a revision that already exists -----------------------
    #
    # Asking for geometry and animation in one answer means the model
    # re-emits every box to add a wave, which is expensive and routinely
    # corrupts a mesh that was already approved. These two let the clips
    # arrive on their own and be merged into the revision you already have.

    @staticmethod
    def _anim_block_name(line: str) -> str:
        rest = line.strip()[len("animation"):].strip()
        if rest.startswith('"') and '"' in rest[1:]:
            return rest[1:].split('"', 1)[0]
        return rest.split()[0].strip('"') if rest.split() else ""

    @classmethod
    def extract_animation_blocks(cls, text: str) -> list:
        """Every `animation` block in a reply, each as its own chunk.

        Tolerant on purpose: a model may wrap the answer in a fence, explain
        itself first, or hand back a whole document when only clips were
        asked for.
        """
        body = text or ""
        fence = re.search(r"```[A-Za-z]*\s*(.*?)```", body, re.S)
        if fence:
            body = fence.group(1)
        body = body.replace("<<<DESIGN", "").replace(">>>", "")
        blocks, cur = [], None
        for raw in body.splitlines():
            head = raw.strip().split()[0].lower() if raw.strip().split() else ""
            if head == "animation":
                if cur:
                    blocks.append("\n".join(cur).rstrip())
                cur = [raw.rstrip()]
                continue
            if head in ("model", "bone", "design", "texture", "structure"):
                if cur:
                    blocks.append("\n".join(cur).rstrip())
                cur = None
                continue
            if cur is not None:
                cur.append(raw.rstrip())
        if cur:
            blocks.append("\n".join(cur).rstrip())
        return [b for b in blocks if b.strip()]

    @classmethod
    def merge_animations(cls, dsl: str, blocks: list) -> str:
        """Put `blocks` into `dsl`, replacing any clip of the same name.

        A clip the new blocks do not mention is left alone, so animating a
        lunge does not silently drop the walk cycle.
        """
        incoming = {cls._anim_block_name(b.splitlines()[0]) for b in blocks}
        kept, skipping = [], False
        for raw in (dsl or "").splitlines():
            head = raw.strip().split()[0].lower() if raw.strip().split() else ""
            if head == "animation":
                skipping = cls._anim_block_name(raw) in incoming
                if skipping:
                    continue
            elif head in ("model", "bone", "design", "texture", "structure"):
                skipping = False
            if not skipping:
                kept.append(raw.rstrip())
        while kept and not kept[-1].strip():
            kept.pop()
        return "\n".join(kept) + "\n\n" + "\n\n".join(b.strip() for b in blocks) + "\n"

    # ---- per-project art-style variants ---------------------------------
    #
    # One project holds several regions, and each has its own palette off the
    # same locked art style - Overgrown Shelf is not Ashen Foundry Basin.
    # A variant is a named style text; the active one is appended to the
    # project-wide lock, so a region overrides without restating everything.

    # ---- per-asset metadata ---------------------------------------------
    #
    # Two things live here, and they are the same shape: what a texture is
    # FOR, and which texture fills a model or prop's material slots. Neither
    # can go in the document, because the model and prop grammars have no
    # texture binding and designbridge.py is not ours to change.

    TEXTURE_TARGETS = ("block", "prop", "model")

    def get_meta(self, project_id: str, key: str, default=None):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM asset_meta WHERE project_id=? AND key=?",
                (project_id, key)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except Exception:
            return default

    def set_meta(self, project_id: str, key: str, value) -> dict:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO asset_meta (project_id, key, value_json, updated_at)"
                " VALUES (?,?,?,?) ON CONFLICT(project_id, key)"
                " DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (project_id, key, json.dumps(value), now_iso()))
        return {"ok": True, "value": value}

    def apply_blockplan(self, payload: dict) -> dict:
        """Take the model's answer and make it the actual constraint.

        Without this the plan is just a chat message. Stored, it narrows the
        form allow-list and gets stated back in the build prompt, which is
        what makes "use these blocks" true rather than hopeful.
        """
        project_id = (payload.get("projectId") or "").strip()
        text = payload.get("text") or ""
        body = text
        fence = re.search(r"```[A-Za-z]*\s*(.*?)```", body, re.S)
        if fence:
            body = fence.group(1)
        i, j = body.find("{"), body.rfind("}")
        if i < 0 or j <= i:
            raise ValueError("No json object in that text. It should start with { and "
                             'contain "forms" and "materials".')
        try:
            plan = json.loads(body[i:j + 1])
        except Exception as exc:
            raise ValueError(f"That json does not parse: {exc}")

        forms = [str(f).strip() for f in (plan.get("forms") or []) if str(f).strip()]
        bad_forms = [f for f in forms if f not in CORE_12]
        forms = [f for f in forms if f in CORE_12]

        mats, seen = [], set()
        for m in (plan.get("materials") or []):
            if not isinstance(m, dict):
                continue
            alias = str(m.get("alias") or "").strip()
            if not alias or alias in seen:
                continue
            seen.add(alias)
            try:
                lvl = int(m.get("light", 0) or 0)
            except Exception:
                lvl = 0
            mats.append({
                "alias": alias,
                "texture": str(m.get("texture") or "").strip(),
                "color": str(m.get("color") or "").strip() or "#9AA0A8",
                "light": max(0, min(15, lvl)),
            })
        if not forms and not mats:
            raise ValueError('That json has neither "forms" nor "materials".')

        known = {t["name"] for t in self.list_textures()}
        missing = sorted({m["texture"] for m in mats if m["texture"] and m["texture"] not in known})

        self.set_meta(project_id, "block_plan", {"forms": forms, "materials": mats})
        # The form allow-list lives in the project style, which the client
        # owns, so the forms go back for it to apply - one source of truth.
        return {"ok": True, "forms": forms, "materials": mats,
                "missingTextures": missing, "rejectedForms": bad_forms}

    def block_plan(self, project_id: str) -> dict:
        return self.get_meta(project_id, "block_plan") or {"forms": [], "materials": []}

    def texture_intent(self, project_id: str) -> dict:
        """What a texture is for, and therefore how it must be built.

        A block face has to tile in both axes; a wrap round a revolved prop
        only has to tile around it; a panel on one box face of a model need
        not tile at all. That changes which layers are even legal, so it is
        not a label - it is a constraint.
        """
        got = self.get_meta(project_id, "texture_intent") or {}
        return {"target": got.get("target", "block"),
                "wrap": got.get("wrap", "around"),
                "note": got.get("note", "")}

    def asset_slots(self, project_id: str) -> dict:
        """The material slots of a model or prop, keyed on colour.

        A box or a swept solid already carries `color=#hex`, and the region
        palette already names those colours - so the colour IS the slot. A
        model has many boxes but few distinct colours, which is why binding
        per colour answers "which part of the model" without needing a new
        grammar or a slot per box.
        """
        boot = self.get_bootstrap_state()
        proj = boot["projects"].get(project_id)
        if not proj:
            raise ValueError("Project not found")
        commit = self._commit_row(project_id, proj["headCommitId"])
        dsl = (commit["dsl"] or "").strip()
        if not dsl:
            return {"ok": True, "kind": "", "slots": [], "bindings": {}}
        result = compile_source(dsl)
        if not result.ok:
            return {"ok": True, "kind": result.kind or "", "slots": [], "bindings": {},
                    "error": "This revision does not compile, so it has no slots yet."}
        doc, kind = result.doc, result.kind
        counts = {}

        def bump(col, where):
            if not col:
                return
            hx = col if isinstance(col, str) else None
            if hx is None:
                try:
                    hx = "#%02X%02X%02X" % tuple(int(c) for c in col[:3])
                except Exception:
                    return
            hx = hx.upper()
            if not hx.startswith("#"):
                hx = "#" + hx
            e = counts.setdefault(hx, {"hex": hx, "count": 0, "where": []})
            e["count"] += 1
            if where and where not in e["where"]:
                e["where"].append(where)

        if kind == "model":
            for name, bone in (doc.bones or {}).items():
                for bx in (getattr(bone, "boxes", None) or []):
                    bump(getattr(bx, "colorspec", None) or getattr(bx, "color", None), name)
        elif kind == "prop":
            for sol in (getattr(doc, "solids", None) or {}).values() if isinstance(
                    getattr(doc, "solids", None), dict) else (getattr(doc, "solids", None) or []):
                bump(getattr(sol, "colorspec", None) or getattr(sol, "color", None),
                     getattr(sol, "name", ""))
        elif kind == "structure":
            for alias, m in (getattr(doc, "materials", None) or {}).items():
                bump(m.get("color"), alias)

        slots = sorted(counts.values(), key=lambda d: -d["count"])
        names = {c["hex"]: c["name"] for c in
                 [{"hex": h, "name": n} for n, h in named_palette(
                     self.effective_lock("", self.get_settings().get("styleLock", "")))]}
        for sl in slots:
            sl["name"] = names.get(sl["hex"], "")
        return {"ok": True, "kind": kind, "slots": slots,
                "bindings": self.get_meta(project_id, "slot_bindings") or {}}

    def usable_textures(self, target: str = "") -> list:
        """Textures whose declared target fits what is asking for them."""
        out = []
        for t in self.list_textures():
            pid = t.get("id") or t.get("projectId") or ""
            intent = self.texture_intent(pid) if pid else {"target": "block"}
            if target and intent.get("target") != target:
                continue
            row = dict(t)
            row["intent"] = intent
            out.append(row)
        return out

    # ---- projects (workspaces) ------------------------------------------

    def list_workspaces(self) -> list:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM workspaces ORDER BY name").fetchall()
            assets = conn.execute("SELECT id, folder FROM projects").fetchall()
        out = []
        for r in rows:
            root = r["root"] or ""
            n = sum(1 for a in assets
                    if (a["folder"] or "") == root or (a["folder"] or "").startswith(root + "/"))
            out.append({"id": r["id"], "name": r["name"], "root": root,
                        "note": r["note"], "assets": n})
        return out

    def save_workspace(self, payload: dict) -> dict:
        name = (payload.get("name") or "").strip()
        root = normalize_folder(payload.get("root") or "")
        if not name:
            raise ValueError("A project needs a name.")
        if not root:
            raise ValueError("A project is a top-level folder, so it needs a root folder.")
        wid = (payload.get("id") or "").strip() or f"prj_{uuid.uuid4().hex[:8]}"
        with self._connect() as conn:
            if conn.execute("SELECT id FROM workspaces WHERE id=?", (wid,)).fetchone():
                conn.execute("UPDATE workspaces SET name=?, root=?, note=? WHERE id=?",
                             (name, root, payload.get("note") or "", wid))
            else:
                conn.execute("INSERT INTO workspaces (id, name, root, note, created_at)"
                             " VALUES (?,?,?,?,?)",
                             (wid, name, root, payload.get("note") or "", now_iso()))
        self._remember_folder(root)      # the root exists even while empty
        return {"ok": True, "id": wid, "workspaces": self.list_workspaces()}

    def delete_workspace(self, payload: dict) -> dict:
        """Forget the project. The folders and assets under it are untouched."""
        wid = (payload.get("id") or "").strip()
        with self._connect() as conn:
            conn.execute("DELETE FROM workspaces WHERE id=?", (wid,))
        return {"ok": True, "workspaces": self.list_workspaces()}

    def workspace_history(self, workspace_id: str, limit: int = 120) -> dict:
        """What has happened to the whole project, not to one asset.

        Every revision so far was only visible from inside the asset that
        owned it, so there was no way to see what the project gained this
        week. This is that feed: one line per revision across every asset
        under the project root, newest first.
        """
        wss = {w["id"]: w for w in self.list_workspaces()}
        ws = wss.get(workspace_id)
        if not ws:
            raise ValueError("Project not found")
        root = ws["root"]
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT c.id, c.project_id, c.tag, c.status, c.errors, c.note,"
                "       c.created_at, c.seq, p.name AS asset, p.folder AS folder,"
                "       p.track AS track"
                "  FROM commits c JOIN projects p ON p.id = c.project_id"
                " ORDER BY c.created_at DESC").fetchall()
        feed, seen_first = [], {}
        for r in rows:
            fol = r["folder"] or ""
            if not (fol == root or fol.startswith(root + "/")):
                continue
            feed.append({
                "commitId": r["id"], "assetId": r["project_id"], "asset": r["asset"],
                "folder": fol, "track": r["track"], "tag": r["tag"],
                "status": r["status"], "errors": r["errors"], "note": r["note"] or "",
                "at": r["created_at"], "seq": r["seq"],
                "kind": "added" if r["seq"] == 1 else "revised",
            })
        for e in feed:
            seen_first.setdefault(e["assetId"], 0)
            seen_first[e["assetId"]] += 1
        return {"ok": True, "project": ws, "events": feed[:limit],
                "total": len(feed), "assets": len(seen_first)}

    def list_palettes(self, project_id: str = "") -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM palettes WHERE project_id=? ORDER BY created_at",
                (project_id or "",)).fetchall()
        return [{"id": r["id"], "projectId": r["project_id"], "name": r["name"],
                 "body": r["body"], "inherit": bool(r["inherit"]),
                 "active": bool(r["active"]),
                 "colors": [{"name": n, "hex": h} for n, h in named_palette(r["body"])]}
                for r in rows]

    def save_palette(self, payload: dict) -> dict:
        pid = (payload.get("projectId") or "").strip()
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("A variant needs a name.")
        vid = (payload.get("id") or "").strip() or f"pal_{uuid.uuid4().hex[:8]}"
        body = payload.get("body") or ""
        inherit = 1 if payload.get("inherit", True) else 0
        with self._connect() as conn:
            exists = conn.execute("SELECT id FROM palettes WHERE id=?", (vid,)).fetchone()
            if exists:
                conn.execute("UPDATE palettes SET name=?, body=?, inherit=? WHERE id=?",
                             (name, body, inherit, vid))
            else:
                conn.execute(
                    "INSERT INTO palettes (id, project_id, name, body, inherit, active, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (vid, pid, name, body, inherit, 0, now_iso()))
        if payload.get("activate"):
            self.activate_palette({"projectId": pid, "id": vid})
        return {"ok": True, "id": vid, "palettes": self.list_palettes(pid)}

    def activate_palette(self, payload: dict) -> dict:
        pid = (payload.get("projectId") or "").strip()
        vid = (payload.get("id") or "").strip()
        with self._connect() as conn:
            conn.execute("UPDATE palettes SET active=0 WHERE project_id=?", (pid,))
            if vid:
                conn.execute("UPDATE palettes SET active=1 WHERE id=? AND project_id=?", (vid, pid))
        return {"ok": True, "palettes": self.list_palettes(pid)}

    def delete_palette(self, payload: dict) -> dict:
        vid = (payload.get("id") or "").strip()
        pid = (payload.get("projectId") or "").strip()
        with self._connect() as conn:
            conn.execute("DELETE FROM palettes WHERE id=?", (vid,))
        return {"ok": True, "palettes": self.list_palettes(pid)}

    # ---- scoped style locks ---------------------------------------------

    SCOPE_ALL = ""

    def list_locks(self) -> list:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM style_locks ORDER BY scope").fetchall()
        return [{"scope": r["scope"], "body": r["body"], "at": r["updated_at"]}
                for r in rows if (r["body"] or "").strip()]

    def set_lock(self, scope: str, body: str) -> dict:
        scope = (scope or "").strip()
        with self._connect() as conn:
            if (body or "").strip():
                conn.execute(
                    "INSERT INTO style_locks (scope, body, updated_at) VALUES (?,?,?)"
                    " ON CONFLICT(scope) DO UPDATE SET body=excluded.body,"
                    " updated_at=excluded.updated_at",
                    (scope, body, now_iso()))
            else:
                conn.execute("DELETE FROM style_locks WHERE scope=?", (scope,))
        return {"ok": True, "locks": self.list_locks()}

    def scopes_for_asset(self, project_id: str) -> list:
        """Which lock scopes cover this asset, general to specific."""
        boot = self.get_bootstrap_state()
        proj = boot["projects"].get(project_id) or {}
        folder = (proj.get("folder") or "").strip("/")
        out = [self.SCOPE_ALL]
        for w in self.list_workspaces():
            root = w["root"]
            if folder == root or folder.startswith(root + "/"):
                out.append(f"prj:{w['id']}")
        if folder:
            segs = folder.split("/")
            for i in range(len(segs)):
                out.append("dir:" + "/".join(segs[:i + 1]))
        return out

    def lock_label(self, scope: str) -> str:
        if not scope:
            return "every project"
        if scope.startswith("prj:"):
            w = next((x for x in self.list_workspaces() if x["id"] == scope[4:]), None)
            return f"project {w['name']}" if w else "a project that no longer exists"
        if scope.startswith("dir:"):
            return f"folder {scope[4:]}"
        return scope

    def resolved_lock(self, project_id: str, fallback: str = "") -> dict:
        """Every lock that covers this asset, layered general to specific.

        The lock used to be one global string, so a workspace holding two
        art directions could only have one of them. Scoped, the house style
        stays at the top and a project or a folder adds to it - which is the
        same inheritance the region palettes already use.
        """
        stored = {l["scope"]: l["body"] for l in self.list_locks()}
        if self.SCOPE_ALL not in stored and (fallback or "").strip():
            stored[self.SCOPE_ALL] = fallback      # the old global setting
        parts, used = [], []
        for sc in self.scopes_for_asset(project_id):
            body = (stored.get(sc) or "").strip()
            if not body:
                continue
            used.append({"scope": sc, "label": self.lock_label(sc)})
            parts.append(body if sc == self.SCOPE_ALL
                         else f"# {self.lock_label(sc).upper()}\n{body}")
        return {"text": "\n\n".join(parts), "applied": used}

    def effective_lock(self, project_id: str, base_lock: str) -> str:
        """The project lock with the active variant layered on top."""
        for v in self.list_palettes(project_id):
            if v["active"]:
                if v["inherit"] and (base_lock or "").strip():
                    return (base_lock.rstrip() + "\n\n"
                            + f"REGION / VARIANT: {v['name']}\n" + (v["body"] or ""))
                return f"REGION / VARIANT: {v['name']}\n" + (v["body"] or "")
        return base_lock or ""

    def animation_brief(self, project_id: str, clips: str = "") -> dict:
        """The prompt that asks for clips only, for the rig as it stands."""
        boot = self.get_bootstrap_state()
        proj = boot["projects"].get(project_id)
        if not proj:
            raise ValueError("Project not found")
        commit = self._commit_row(project_id, proj["headCommitId"])
        dsl = (commit["dsl"] or "").strip()
        if not dsl:
            raise ValueError("This revision has no source to animate yet.")
        result = compile_source(dsl)
        if not result.ok or result.kind != "model":
            raise ValueError(
                "Only the model track animates - a prop or a texture has no bones."
                if result.ok else "This revision does not compile, so there is no rig to animate."
            )
        doc = result.doc
        rows = []
        for name in (getattr(doc, "order", None) or list(doc.bones.keys())):
            b = doc.bones.get(name)
            if not b:
                continue
            pv = getattr(b, "pivot", (0, 0, 0)) or (0, 0, 0)
            par = getattr(b, "parent", None)
            rows.append(f"  {name:<16} pivot=({pv[0]:g},{pv[1]:g},{pv[2]:g})"
                        + (f"  parent={par}" if par else "  (root)"))
        existing = sorted((getattr(doc, "anims", None) or {}).keys())
        wanted = [c.strip() for c in re.split(r"[,\n]", clips or "") if c.strip()]
        if not wanted:
            wanted = ["idle", "walk", "attack", "hit", "death"]

        P = [
            f'Write keyframe animation for an existing rig named "{doc.name}".',
            "",
            "Return ONLY `animation` blocks. Do NOT repeat the model, the bones or the",
            "boxes - the geometry is already approved and will be reused exactly as it is.",
            "Anything else you send for it will be ignored.",
            "",
            "THE RIG YOU ARE ANIMATING - these bone names are the only legal targets:",
            *rows,
            "",
            "A bone rotates about its own pivot and carries its children with it.",
            "",
            "SYNTAX",
            '  animation "<name>" [loop=true|false]',
            "    key <bone> t=<seconds> rot=(rx,ry,rz)     # degrees, XYZ, about the pivot",
            "    key <bone> t=<seconds> pos=(dx,dy,dz)     # offset in pixels",
            "",
            "RULES",
            "  - t starts at 0 and increases. Give every clip a key at t=0.",
            "  - A looping clip must end in the same pose it started, or it will jump.",
            "  - One bone per key line. Several bones at the same t is several lines.",
            "  - 3 to 5 keys per clip. Interpolation between them is linear and free,",
            "    so do not write a key per frame.",
            "  - Rotate rather than translate wherever a joint would really rotate.",
            "",
            "CLIPS WANTED: " + ", ".join(wanted),
        ]
        if existing:
            P.append("")
            P.append("This rig already has: " + ", ".join(existing)
                     + ". Re-sending one of those names replaces it; any you leave out are kept.")
        P.append("")
        P.append("WORKED EXAMPLE")
        P.append('animation "lunge" loop=false')
        P.append("  key arm_r t=0 rot=(0,0,0)")
        P.append("  key arm_r t=0.25 rot=(-110,0,0)")
        P.append("  key arm_r t=0.6 rot=(0,0,0)")
        return {"ok": True, "prompt": "\n".join(P),
                "bones": [r.strip().split()[0] for r in rows], "existing": existing}

    def apply_animation(self, payload: dict) -> dict:
        """Merge pasted clips into the head revision and compile it."""
        project_id = (payload.get("projectId") or "").strip()
        boot = self.get_bootstrap_state()
        proj = boot["projects"].get(project_id)
        if not proj:
            raise ValueError("Project not found")
        commit = self._commit_row(project_id, proj["headCommitId"])
        head_dsl = (commit["dsl"] or "").strip()
        if not head_dsl:
            raise ValueError("This revision has no source to animate yet.")
        text = payload.get("text") or ""
        blocks = self.extract_animation_blocks(text)
        if not blocks:
            raise ValueError(
                'No animation blocks in that text. Each clip starts with '
                'animation "name" and its key lines follow it.'
            )
        merged = self.merge_animations(head_dsl, blocks)
        names = [self._anim_block_name(b.splitlines()[0]) for b in blocks]
        out = dict(payload)
        out["rawAiResponse"] = merged
        out["note"] = (payload.get("note") or "").strip() or (
            "Animation: " + ", ".join(n for n in names if n))
        return self.compile_and_save(out)

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
                result = self._compile_dsl(proj["track"], dsl, None, payload.get("style"))
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
        out = {}
        for p in sorted(cdir.iterdir()):
            if not p.is_file():
                continue
            blob = p.read_bytes()
            # Every nested rig exports with its children displaced; repaired
            # here on the way out so a download is correct in Blender and in
            # the engine alike. See fix_glb_child_pivots.
            if p.suffix.lower() == ".glb":
                blob = fix_glb_child_pivots(blob)
            out[p.name] = blob
        return out

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
                side = self.materials_sidecar(project_id)
                if side:
                    zf.writestr(f"{base}/materials.json", side)
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
        out = mesh_payload(result)
        out.update(_slot_payload(self, project_id, result.kind))
        # A structure declares its textures in the document itself, so resolve
        # each one to a real asset: the UI must be able to say "this material
        # names a texture that does not exist" rather than quietly painting it.
        if result.kind == "structure":
            known = {t["name"]: t for t in self.list_textures()}
            for g in out.get("groups", []):
                nm = g.get("texture") or ""
                if not nm:
                    g["texState"] = "none"
                    continue
                t = known.get(nm)
                if not t:
                    g["texState"] = "missing"
                    continue
                g["texState"] = "ok"
                g["texUrl"] = f"/api/preview/{t['id']}/{t['head']}"
                g["texAsset"] = t["id"]
                g["texTarget"] = self.texture_intent(t["id"]).get("target", "block")
        return out

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
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        safe = "".join(ch if (ch.isalnum() or ch in "-_") else "-"
                       for ch in str(proj.get("name") or project_id))
        return (buf.getvalue(), "application/zip",
                f"{safe.strip('-')}_full-repo_{stamp}.zip")

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
    # A structure is budgeted in statements, not cells: the whole point of the
    # grammar is that ten lines make a building, so the limit that matters is
    # how many volumes it takes to describe, not how many cells come out.
    "structure": {1: (6, 2), 2: (12, 3), 3: (24, 5), 4: (40, 8), 5: (72, 12)},
}
# Clips and keys per clip, by complexity. Asking for a key per frame is the
# commonest animation mistake: interpolation is linear and free, so poses are
# what is wanted, not frames.
CLIP_BUDGET = {1: (2, 3), 2: (3, 4), 3: (5, 5), 4: (6, 6), 5: (8, 8)}


# Measured in GRAMMAR_NOTES.md: these layers do not wrap across the tile edge,
# so they are bugs on a block face and perfectly good on a panel that never
# repeats. The target decides which list applies.
NON_TILING_LAYERS = ("noise", "veins", "bevel", "outline", "border")


def texture_target_block(intent: dict) -> str:
    """What this texture is for, and therefore what correct means.

    Not every texture is a block. A cube face has to tile in both axes; a
    wrap round a revolved prop only has to tile around it; a panel on one
    box face of a model need not tile at all. Authoring all three as if
    they were cubes is why decals and labels came back unusable.
    """
    target = (intent or {}).get("target", "block")
    wrap = (intent or {}).get("wrap", "around")
    L = ["WHAT THIS TEXTURE IS FOR"]
    if target == "block":
        L += [
            "  A BLOCK face - a cube in a structure, repeated edge to edge in every",
            "  direction. It must tile seamlessly in BOTH axes.",
            "",
            "  So: " + ", ".join(NON_TILING_LAYERS) + " do NOT wrap across the tile",
            "  edge. Keep them away from the outer 2 pixels, or leave them out. `brick`",
            "  staggers and wraps correctly, so it is safe anywhere.",
            "  Do not draw a border, a frame or a single centred motif: you will see",
            "  the seam repeat as a grid the moment two blocks sit side by side.",
        ]
    elif target == "prop":
        L += [
            "  A PROP surface - wrapped round a solid that was revolved or extruded.",
            f"  It repeats {'around the axis only' if wrap == 'around' else 'along the sweep only'}.",
            "",
            "  So it must tile in ONE axis and is free in the other: horizontal bands,",
            "  rivet lines and wear that runs round the body all work. A vertical",
            "  gradient, a rim at the top or a different base is fine and will not",
            "  seam, because that edge is never repeated.",
        ]
    else:
        L += [
            "  A MODEL panel - painted on one box face of a rigged model. It does not",
            "  repeat, so it does not have to tile at all.",
            "",
            "  This is the one target where " + ", ".join(NON_TILING_LAYERS),
            "  are all legal and useful: a border reads as a panel edge, an outline as",
            "  a stencil, a single centred motif as an insignia. Compose it as one",
            "  picture rather than as a repeating field.",
        ]
    note = (intent or {}).get("note", "").strip()
    if note:
        L += ["", "  " + note]
    return "\n".join(L)


def slot_block(slots: list, bindings: dict, kind: str) -> str:
    """The asset's material slots, so the AI knows which colours are textured.

    A box or a solid already carries `color=#hex` and the palette already
    names those colours, so the colour IS the slot - which is how "which
    part of the model" gets an answer without a slot per box.
    """
    if not slots:
        return ""
    L = ["MATERIAL SLOTS - this asset's colours, and the textures on them",
         "  Keep using these colours. A colour with a texture bound to it is",
         "  painted with that texture in the engine; the colour stays as its tint."]
    for sl in slots[:12]:
        hx = sl.get("hex", "")
        nm = sl.get("name") or ""
        tex = bindings.get(hx, "")
        where = ", ".join(sl.get("where", [])[:3])
        line = f"  {hx}"
        if nm:
            line += f"  {nm}"
        line += f"   x{sl.get('count', 0)}"
        if where:
            line += f"   on {where}"
        line += f"   -> {tex if tex else 'no texture (flat colour)'}"
        L.append(line)
    if kind == "model":
        L.append("  Reuse a colour for every part made of the same stuff - one slot for all")
        L.append("  the plating beats a different shade per box, and it is what gets textured.")
    return "\n".join(L)


def clips_block(wanted: list, lvl: int = 3) -> str:
    """Ask for animation alongside the geometry, in the same answer."""
    max_clips, max_keys = CLIP_BUDGET.get(lvl, CLIP_BUDGET[3])
    names = [w for w in (wanted or []) if w][:max_clips]
    L = ["ANIMATION - required this round",
         f"  Write {'the clips below' if names else 'the clips the brief asks for'}"
         f" as well as the geometry, at most {max_keys} keys each.",
         "",
         '  animation "<name>" [loop=true|false]',
         "    key <bone> t=<seconds> rot=(rx,ry,rz)     # degrees, XYZ, about the pivot",
         "    key <bone> t=<seconds> pos=(dx,dy,dz)     # offset in pixels",
         "",
         "  - Put the animation blocks after every bone, at the end of the document.",
         "  - t starts at 0 and increases. Every clip needs a key at t=0.",
         "  - A looping clip ends in the pose it started, or it visibly jumps.",
         "  - One bone per key line.",
         "  - Interpolation between keys is linear and free: write poses, not frames.",
         "  - Give a bone a pivot at the joint it turns about, or it will rotate",
         "    about the origin and swing through the body."]
    if names:
        L.append("")
        L.append("  CLIPS WANTED: " + ", ".join(names))
    return "\n".join(L)


# Largest allowed long:short ratio on a single box.
ASPECT_CAP = {"cubic": 2.0, "mixed": 4.0, "elongated": 0.0}  # 0 = uncapped

DETAIL_NOTE = {
    "low": "Read as a silhouette from ten blocks away. Big forms only, no trim.",
    "medium": "Clear silhouette plus a few reading details. No fine filigree.",
    "high": "Dense surface detail is welcome, as long as the budget above allows it.",
}

# A tile has no silhouette, so the same three levels are said in the terms
# that track actually has: how busy the surface is at 16x16.
DETAIL_NOTE_TEXTURE = {
    "low": "Flat and quiet. A base tone plus one variation; no fine speckle.",
    "medium": "Readable surface at 16x16 - grain or wear, nothing per-pixel.",
    "high": "Dense surface detail is welcome, as long as the layer budget allows it.",
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

    notes = DETAIL_NOTE_TEXTURE if kind == "texture" else DETAIL_NOTE
    out.append(f"  DETAIL: {s['detail']}. {notes[s['detail']]}")
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

THE FOUR KINDS OF ASSET
  model      a rigged blocky/voxel object - bones, boxes, keyframe animation.
             Characters, mobs, machines, anything with moving parts.
             The document starts with `model`.
  prop       a CAD solid swept from a 2D profile - revolve, extrude, loft.
             Vessels, tools, furniture; anything turned or extruded.
             The document starts with `design`, not `prop`.
  texture    a layered procedural pixel texture plus a material map.
             Block faces, surfaces, tiles. Starts with `texture`.
  structure  a building, assembled from block forms on a cell grid - walls,
             ruins, rooms, towers. You write volumes, never single cells.
             Starts with `structure`.
  You are told which kind you are writing. Use that grammar and no other.
  The first keyword of your document is what decides the kind, so it must
  match the track you were asked for.

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
  structure a cell grid with a form, orientation and material per cell, plus
           a .png preview and an ASCII floor plan per level

  These files load in Blender, three.js, Godot, Unity and custom Java/OpenGL
  engines. The numbers you choose are real dimensions in a real scene, so
  treat them as real: a 32-unit-tall model is a 32-block-tall character.

HOW TO WORK
  Change one thing per round. Keep whatever already passes. Always send the
  COMPLETE document - never a patch, a diff, or "the rest is unchanged". Stay
  inside the shape budget you are given; it is checked after every compile.
  If the report shows an error, fixing it comes before adding anything new.
"""


def lock_dims_line(style: dict, kind: str = "model") -> str:
    """The Shape Control values written as one readable clause.

    Scoped by track, because proportions and symmetry are box controls: on a
    texture they are not merely irrelevant, they actively mislead - a locked
    'symmetry none' reads as an instruction not to make a symmetrical tile.
    """
    t = normalize_style(style)
    bits = [f"complexity {t['complexity']} of 5", f"{t['detail']} detail"]
    if kind in ("model", "prop"):
        bits += [f"{t['proportions']} proportions", f"symmetry {t['symmetry']}"]
    return ", ".join(bits)


def style_lock_text(lock: str, style: dict = None, kind: str = "model") -> str:
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
    # A brief that writes `oxidized_copper` (#4E7D70) is naming a palette
    # entry. The grammar only accepts hex, so without the mapping stated the
    # model carries it in prose and drifts a shade at a time.
    table = palette_table(body)
    if table:
        lines.append("")
        lines += ["  " + l for l in table.splitlines()]
    # The palette and the written rules are only half of a style. The form
    # rules - how dense, how chunky, how symmetrical - are set by the Shape
    # Controls and spelled out under SHAPE BUDGET below. Without this line a
    # model reads those as this round's brief rather than as standing law,
    # and quietly drifts denser or chunkier the moment the brief is silent.
    if style:
        lines += [
            "",
            ("  ALSO LOCKED - the layer budget under SHAPE BUDGET is part of this lock:"
             if kind == "texture" else
             "  ALSO LOCKED - the form rules under SHAPE BUDGET are part of this lock:"),
            "    " + lock_dims_line(style, kind) + ".",
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
    raw_spec = spec

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
        # The parsed spec, so the client can render the palette and the prose
        # visually instead of asking anyone to read the JSON. detail is
        # normalised to {level, notes} here because the lock-building pass
        # flattens it to its enum.
        "spec": _display_spec(raw_spec),
        "extras": sorted(extras.keys()),
        "warnings": warn,
    }


def _display_spec(spec: dict) -> dict:
    out = dict(spec)
    det = spec.get("detail")
    if isinstance(det, dict):
        lvl = str(det.get("level") or det.get("value") or "").strip().lower()
        out["detail"] = {
            "level": lvl if lvl in DETAIL_NOTE else "",
            "notes": str(det.get("notes") or det.get("note") or "").strip(),
        }
    return out


_DOC_START_RE = re.compile(r"^(model|texture|design|structure)\s+[\"\w]")
_DOC_NAME_RE = re.compile(r"^(?:model|texture|design|structure)\s+\"?([A-Za-z0-9_\-]+)\"?")


def _is_source(block: str) -> bool:
    """designbridge's own sniffer does not know the structure track, so the
    splitter asks this instead: does any line open a document?"""
    for raw in (block or "").splitlines():
        if _DOC_START_RE.match(raw.strip()):
            return True
    return _looks_like_source(block)


def document_name(dsl: str) -> str:
    """The name the document gives itself, which is the only name that counts."""
    for raw in (dsl or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        m = _DOC_NAME_RE.match(line)
        return m.group(1) if m else ""
    return ""


def split_documents(text: str) -> list:
    """Cut one pasted blob into the separate documents it holds.

    A session with a text AI produces several documents at once - six props in
    one answer, or three takes on the same mob - and pasting them back one at a
    time is the slowest part of the loop. Fenced blocks are preferred because
    that is what the output contract asks for; failing that, each top-level
    declaration (`model x`, `design y`, `texture z`) starts a new document, so
    a bare run of documents still separates correctly.
    """
    text = (text or "").replace("\r\n", "\n")
    if not text.strip():
        return []

    docs = []
    fenced = re.findall(r"```[a-zA-Z0-9_\-]*\n(.*?)```", text, re.S)
    for block in fenced:
        inner = SENTINEL_RE.search(block)
        if inner:
            block = inner.group(1)
        if _is_source(block):
            docs.append(block.strip("\n").rstrip())
    if docs:
        return docs

    # No fences. Legacy sentinels next, then raw declarations.
    sent = re.findall(r"<<<\s*DESIGN.*?>>>(.*?)<<<\s*/?\s*DESIGN\s*>>>", text, re.S)
    for block in sent:
        if _is_source(block):
            docs.append(block.strip("\n").rstrip())
    if docs:
        return docs

    current = []
    for raw in text.split("\n"):
        if _DOC_START_RE.match(raw.strip()) and current and any(l.strip() for l in current):
            docs.append("\n".join(current).strip("\n").rstrip())
            current = [raw]
        else:
            current.append(raw)
    if current and any(l.strip() for l in current):
        docs.append("\n".join(current).strip("\n").rstrip())
    return [d for d in docs if _is_source(d)]


TRACK_KINDS = ("model", "prop", "texture", "structure")

TRACK_ICONS = {
    "model": "view_in_ar",
    "prop": "precision_manufacturing",
    "texture": "texture",
    "structure": "domain",
    "auto": "auto_awesome",
}

# The grammar names its track with the first keyword of the document, and the
# prop grammar spells that keyword `design`, not `prop` - so the map is not
# the identity it looks like it should be.
_KIND_DECL_RE = re.compile(r"^(model|prop|texture|design|structure)\b")
_DECL_TO_KIND = {"model": "model", "prop": "prop", "texture": "texture",
                 "design": "prop", "structure": "structure"}

_KIND_HINTS = {
    "texture": ("texture", "tileable", "seamless", "albedo", "swatch", "pattern",
                "wallpaper", "fabric", "noise", "gradient", "decal", "tile"),
    "prop": ("prop", "lathe", "turned", "revolve", "sweep", "profile", "vase",
             "bottle", "barrel", "pillar", "column", "goblet", "cup", "bollard",
             "canister", "flask", "spindle", "finial"),
    "model": ("character", "creature", "rig", "rigged", "bone", "skeleton",
              "animation", "walk cycle", "vehicle", "robot", "mech", "humanoid",
              "voxel", "blockout", "weapon", "turret", "drone"),
    "structure": ("building", "structure", "ruin", "ruins", "hall", "tower",
                  "bunker", "bridge", "room", "architecture", "wall section",
                  "foundry hall", "shrine", "outpost", "station", "aqueduct",
                  "jigsaw", "wfc", "floorplan", "colonnade"),
}


_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")


def _rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def palette_of(lock_text: str) -> list:
    """Every hex colour named in the style lock, in the order it appears."""
    seen, out = set(), []
    for h in _HEX_RE.findall(lock_text or ""):
        u = h.upper()
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# The name is an identifier immediately before the hex; between them a brief
# may put any punctuation: `shadow` (`#22252A`), shadow = #22252A, shadow:
# #22252A. The gap class excludes letters and digits, so a match can never
# reach back across a preceding word such as "and".
_NAMED_RE = re.compile(r"([A-Za-z][A-Za-z0-9_\-]{1,31})[^A-Za-z0-9#]{0,12}(#[0-9a-fA-F]{6})")
_PAL_STOP = ("color", "colour", "colors", "colours", "hex", "and", "or", "the",
             "is", "to", "in", "of", "with", "rgb")


def named_palette(lock_text: str) -> list:
    """(name, hex) pairs named in a style text, in the order written.

    A brief that says `oxidized_copper (#4E7D70)` is naming a palette entry,
    not just a colour, and the model has to be told both halves: the name is
    how the brief refers to it, the hex is what the grammar accepts. Without
    the table the model carries the mapping in prose and drifts.
    """
    out, seen = [], set()
    for raw_name, hx in _NAMED_RE.findall(lock_text or ""):
        nm = raw_name.strip("-_").lower()
        u = hx.upper()
        if not nm or u in seen or nm in _PAL_STOP:
            continue
        seen.add(u)
        out.append((nm, u))
    return out


def palette_table(lock_text: str) -> str:
    """The named palette, as a block for the prompt."""
    pairs = named_palette(lock_text)
    if not pairs:
        return ""
    L = ["THE PALETTE - these names are how the brief refers to each colour.",
         "Write the HEX in `color=`; the grammar does not accept the names."]
    width = max(len(n) for n, _ in pairs)
    for nm, hx in pairs:
        L.append(f"  {nm.ljust(width)}  {hx}")
    L.append("Use no colour outside this list.")
    return "\n".join(L)


def nearest_in_palette(h: str, palette: list) -> tuple:
    """(nearest allowed hex, distance). Plain RGB distance is enough here -
    the palettes are small and well separated, and a perceptual metric would
    only move the answer on ties nobody can see."""
    target = _rgb(h)
    best, bd = "", 1e9
    for p in palette:
        d = sum((target[i] - _rgb(p)[i]) ** 2 for i in range(3)) ** 0.5
        if d < bd:
            best, bd = p, d
    return best, bd


# Behaviour that is not in the grammar reference and that models have
# repeatedly guessed wrong. Measured against the compiler, not inferred.
# Kept short on purpose: this is the shortlist of things that actually bite,
# not a second manual.
STRUCTURE_GRAMMAR = """THE STRUCTURE GRAMMAR - write volumes, never cells

  structure <name> size=(x,y,z) seed=<n>
      One per document, first line. size is the bounding volume in cells.
      seed makes every collapse and rubble pass repeatable.

  material <alias> <texture_name> color=#RRGGBB
      Bind a name you will use below to a texture asset in the workspace.
      color is for the preview only; the texture is what the game shows.
      Declare every material before you use it.

  room (x0,y0,z0) (x1,y1,z1) walls=<alias> floor=<alias> [ceiling=<alias>|none]
       [wallform=<form>] [floorform=<form>]
      A hollow box: four walls, a floor, optionally a ceiling. This is the
      main tool - most buildings are two or three rooms.

  door   <north|south|east|west> at=(u,v) width=<n> height=<n>
  window <north|south|east|west> at=(u,v) width=<n> height=<n> [every=<n>]
      Carve an opening out of the room declared immediately above. every=
      repeats the opening along that wall, which is how you get a row of
      windows in one line.

  fill (x0,y0,z0) (x1,y1,z1) material=<alias> [form=<form>] [ori=<n>] [hollow=true]
      A solid or hollow block of cells. Roofs, platforms, plinths, buttresses.

  colonnade (x,y,z) to=(x,y,z) material=<alias> [form=column] [every=<n>] [height=<n>]
      Evenly spaced uprights between two points.

  stairs (x,y,z) to=(x,y,z) material=<alias> [facing=<north|east|south|west>]
      A run of stair cells climbing from one point to another.

  place (x,y,z) form=<form> material=<alias> [ori=<n>] [clad=<alias>]
        [cladmask=+X,-Z] [damage=0..3]
      One cell, when nothing else fits.

  collapse <roof|walls|all> amount=0..1 [seed=<n>]
  rubble   amount=0..1 [material=<alias>] [y=<n>]
  damage   amount=0..1
      What turns an intact building into a ruin. collapse removes cells,
      biased toward the top; rubble scatters broken plates on the floor;
      damage raises the crack stage on what is left. All deterministic.

THE TWELVE FORMS - every cell is one of these, rotated

  cube           a full block                       ori 0 only
  slab_half      half height                        ori 0-5
  plate_quarter  quarter height, carries no load    ori 0-5
  stair          a step                             ori 0-11
  slope_45       a 45-degree wedge                  ori 0-11
  slope_corner   a corner wedge                     ori 0-23
  chamfer_edge   a cube with one edge cut           ori 0-11
  column         a square pillar, strong vertically ori 0-2
  girder         a thin beam, spans horizontally    ori 0-2
  corbel_arch    an arch haunch, extends a span     ori 0-11
  channel_trough a carved water channel             ori 0-11
  trim_edge      a thin edge moulding               ori 0-11

  An orientation outside a form's range is rejected. Cladding is only legal
  on faces that are full squares on the host form.

WHAT MAKES A BUILDING READ

  Massing before detail. Get the volumes right, then add a colonnade.
  A ruin reads as a ruin because of what is missing, not what is added:
  collapse the roof before you scatter rubble.
  Keep collapse amount at or under 0.5 - beyond that, pieces float with
  nothing holding them up, and the report will say so.
"""


GOTCHAS = {
    "texture": (
        "KNOWN BEHAVIOUR - measured, not guessed\n"
        "  These layers DO tile seamlessly: fill, brick, checker, stripes, scatter,\n"
        "  gradient, dither. brick staggers its rows by half a brick by default.\n"
        "  These DO NOT tile: noise, veins (both use unwrapped lattice noise), and\n"
        "  bevel, outline, border (they draw on the edges of the tile itself).\n"
        "  A muddy repeating texture is almost always a noise layer seaming at every\n"
        "  edge. For variation that tiles, use scatter, dither, or brick jitter=.\n"
        "  Use bevel or outline only on a tile meant to read as one panel - a plate,\n"
        "  a sign, a hatch - never on ground or wall.\n"
        "  veins amount= is a THRESHOLD, not a density: 0.12 is the default, 0.25 is\n"
        "  already a wash, 0.5 floods the whole tile one colour."
    ),
    "model": (
        "KNOWN BEHAVIOUR - measured, not guessed\n"
        "  Coordinates are voxels; 16 voxels = 1 metre = 1 block. Fractions are legal.\n"
        "  +X is the model's left seen from the front, Y is up, -Z is its facing.\n"
        "  The ground is y=0 and the model should touch it exactly.\n"
        "  Bone pivots are ABSOLUTE, not relative to the parent. Keep the root bone's\n"
        "  pivot at (0,0,0): a child under a non-zero parent pivot comes out displaced\n"
        "  in the .glb export (the engine .boxmodel.json is fine either way).\n"
        "  Two keys for one bone at one time are NOT merged - write a single key\n"
        "  carrying both rot= and pos=. Keys interpolate linearly, with no easing.\n"
        "  A bone with no keys still follows its parent, which is why limbs must be\n"
        "  parented: an unparented leg stays standing when the body kneels."
    ),
    "prop": (
        "KNOWN BEHAVIOUR - measured, not guessed\n"
        "  A part accepts at=, rot=, rotate=, scale= and mirror= - rotation and\n"
        "  mirroring are available on this track, unlike the voxel model track.\n"
        "  Fractional values are legal everywhere a number is."
    ),
    "structure": (
        "KNOWN BEHAVIOUR - measured, not guessed\n"
        "  Write volumes, never cells. A 24x12x24 building is 7000 cells; ten\n"
        "  statements describe it. room, fill, colonnade and stairs are the tools.\n"
        "  door and window carve out of the room declared immediately above them.\n"
        "  collapse and rubble turn an intact building into a ruin, and they are\n"
        "  deterministic: the same seed gives the same ruin every time.\n"
        "  Cells not connected to the ground are reported - a collapse amount much\n"
        "  over 0.5 leaves islands that would fall the moment it spawned.\n"
        "  Declare every material before use, with a color= for the preview and the\n"
        "  name of the texture asset it binds to in the game."
    ),
}


CORE_12 = ["cube", "slab_half", "plate_quarter", "stair", "slope_45",
           "slope_corner", "chamfer_edge", "column", "girder", "corbel_arch",
           "channel_trough", "trim_edge"]


def textures_block(textures: list) -> str:
    """The texture assets a structure may bind its materials to.

    A building names textures by the asset name they live under, so a model
    writing `material wall soot_iron_plate` is making a claim about the
    workspace. Listing what is actually there turns that from a guess into a
    choice - and a material bound to a name that does not exist is a broken
    building nobody notices until it spawns.
    """
    if not textures:
        return ("TEXTURES AVAILABLE - none yet\n"
                "  This workspace has no compiling texture assets, so invent material\n"
                "  names that describe the surface you want and give each a color=.\n"
                "  The textures can be made afterwards under the names you chose.")
    lines = ["TEXTURES AVAILABLE - bind materials to these names and no others"]
    for t in textures[:40]:
        where = f"  [{t['folder']}]" if t.get("folder") else ""
        cols = ("  " + " ".join(t.get("colors") or [])) if t.get("colors") else ""
        lines.append(f"  {t['name']}{where}{cols}")
    if len(textures) > 40:
        lines.append(f"  ... and {len(textures) - 40} more")
    lines.append("")
    lines.append("  material <alias> <one of the names above> color=#RRGGBB")
    lines.append("  The color is for the preview only; the texture is what the game shows.")
    return "\n".join(lines)


def forms_block(allowed: list) -> str:
    """The subset of the Core 12 this project permits."""
    allowed = [f for f in (allowed or []) if f in CORE_12]
    if not allowed or len(allowed) == len(CORE_12):
        return ""
    banned = [f for f in CORE_12 if f not in allowed]
    return ("FORMS ALLOWED - this project uses a subset of the twelve\n"
            "  Use only:  " + ", ".join(allowed) + "\n"
            "  Never use: " + ", ".join(banned) + "\n"
            "  A document using a form outside the list is rejected, not corrected.")


# What each of the twelve actually is, straight out of the form table rather
# than from memory: orientations, support rule, whether light and fluid pass,
# and how much of a cell it fills.
_FORM_NOTE = {
    "cube": "the default solid block",
    "slab_half": "half height - floors, ledges, table tops",
    "plate_quarter": "quarter height - thin panels, catwalks, trim",
    "stair": "a step - the only form that climbs",
    "slope_45": "a 45 degree ramp - roofs, buttresses",
    "slope_corner": "the corner piece that turns a slope",
    "chamfer_edge": "a cut corner - softens a hard edge",
    "column": "a central post - load bearing, light passes around it",
    "girder": "a horizontal beam - spans a gap",
    "corbel_arch": "a stepped bracket - arches, overhangs",
    "channel_trough": "an open channel - gutters, pipes, fluid runs",
    "trim_edge": "decorative edging - never load bearing",
}


def forms_catalogue() -> str:
    """Every form with the facts that decide whether you want it."""
    try:
        import structurebridge as sb_core
        rows = []
        for spec in sb_core.FORMS:
            name, oris, _half, support, fluid, _seal, _clad, mass = spec[:8]
            rows.append((name, int(oris), support, fluid, float(mass)))
    except Exception:
        return "\n".join(f"  {f}" for f in CORE_12)
    w = max(len(r[0]) for r in rows)
    out = []
    for name, oris, support, fluid, mass in rows:
        note = _FORM_NOTE.get(name, "")
        out.append(f"  {name.ljust(w)}  {oris:2} orientations   {support:<9} "
                   f"fills {mass:<4}  {'light passes' if fluid == 'PASS' else 'blocks light'}"
                   + (f"   - {note}" if note else ""))
    return "\n".join(out)


def agreed_plan_block(plan: dict, known: set) -> str:
    """Say the plan back, so the build is held to what it asked for."""
    mats = (plan or {}).get("materials") or []
    forms = (plan or {}).get("forms") or []
    if not mats and not forms:
        return ""
    L = ["THE BLOCK PLAN YOU AGREED - build with exactly this"]
    if forms:
        L.append("  forms: " + ", ".join(forms))
    for m in mats:
        tex = m.get("texture") or ""
        mark = "" if (not tex or tex in known) else "   <- NOT IN THE WORKSPACE YET"
        lit = f" light={m['light']}" if m.get("light") else ""
        L.append(f"  material {m['alias']} {tex} color={m.get('color', '')}{lit}{mark}")
    L.append("  Declare these material lines verbatim at the top of the document.")
    L.append("  Do not rename an alias, add a material or use a form outside the list.")
    return "\n".join(L)


def blockplan_prompt(brief: str, textures: list, lock_text: str, allowed: list) -> str:
    """Ask the model what it wants to build WITH, before it builds.

    The app used to decide the block list and simply hand it over, so a
    model had no say in what it was given and no reason to think about it.
    Asked first, it commits to a palette of forms and materials - and that
    answer becomes the constraint the build prompt states back, which is
    what stops it inventing a material or naming a texture that is not
    there.
    """
    P = ["PLAN THE BLOCKS FIRST - do not write the building yet.",
         "",
         "You are about to build a structure. Before any geometry, decide what you",
         "need to build it WITH and answer with one json block and nothing else.",
         ""]
    if brief.strip():
        P += ["WHAT IS BEING BUILT", "  " + brief.strip(), ""]
    P += ["THE TWELVE FORMS - every block shape the engine has",
          forms_catalogue(),
          "",
          "  Pick the fewest that do the job. A build that only needs cube, slab_half",
          "  and stair is easier to read and cheaper to place than one using all",
          "  twelve. Only `stair` climbs; only `column`, `girder` and `corbel_arch`",
          "  carry a span; `trim_edge` never bears load.",
          ""]
    P.append(textures_block(textures))
    P.append("")
    pal = palette_table(lock_text)
    if pal:
        P += [pal, ""]
    P += ["ANSWER IN THIS SHAPE",
          "{",
          '  "forms": ["cube", "slab_half", "stair", "column"],',
          '  "materials": [',
          '    {"alias": "wall",  "texture": "soot_iron_plate", "color": "#3A3F45"},',
          '    {"alias": "floor", "texture": "foundry_grate",   "color": "#22252A"},',
          '    {"alias": "lamp",  "texture": "glowstone",        "color": "#FFD9A0", "light": 14}',
          "  ]",
          "}",
          "",
          "RULES",
          "  forms     only names from the twelve above, spelled exactly.",
          "  alias     a short lowercase name you will use in the building, e.g. `wall`.",
          "  texture   a name from TEXTURES AVAILABLE, or a new name you want created.",
          "            Say in a sentence after the json which ones do not exist yet.",
          "  color     hex, from the palette above when there is one.",
          "  light     0-15, only on a material that glows. Leave it out otherwise.",
          "            One level per block of reach: 14 lights 14 blocks of open air.",
          "  Four to eight materials is usually right. One per distinct surface, not",
          "  one per part: all the plating is one material.",
          "",
          "I will hold you to this list. The build prompt will state it back and a",
          "document using anything outside it is rejected, not corrected."]
    if allowed and len(allowed) < len(CORE_12):
        P += ["", "This project is currently limited to: " + ", ".join(allowed),
              "Say so if you need a form outside that."]
    return "\n".join(P)


def precedence_block(brief: str, lock_text: str, dsl: str = "") -> str:
    """Say once which rule wins, and pre-compute the substitutions.

    Both model reviews independently reported the same problem: the brief
    names colours the lock forbids, and the model has to decide what to do
    about it, then confess. That is a judgment call it should never have been
    handed. The order is stated plainly, and every offending colour arrives
    already mapped to the nearest legal one, so the substitution is a lookup
    rather than a decision.
    """
    palette = palette_of(lock_text)
    if not palette:
        return ""
    lines = [
        "PRECEDENCE - which rule wins when two disagree",
        "  1. The STYLE LOCK beats everything. It is the project, not a suggestion.",
        "  2. THE BRIEF beats the examples and your own taste.",
        "  3. Where the brief names a colour the lock does not allow, use the nearest",
        "     locked colour. The substitutions are worked out for you below - do not",
        "     improvise a different one, and do not use the brief's hex.",
    ]
    strays = []
    for h in palette_of(brief) + palette_of(dsl):
        if h in palette:
            continue
        near, d = nearest_in_palette(h, palette)
        if near and not any(x[0] == h for x in strays):
            strays.append((h, near, d))
    if strays:
        lines.append("")
        lines.append("  COLOUR SUBSTITUTIONS - the brief asked for these; use the right column")
        for h, near, d in strays:
            how = "very close" if d < 24 else ("close" if d < 64 else "the nearest there is")
            lines.append(f"    {h}  ->  {near}   ({how})")
    else:
        lines.append("")
        lines.append("  Every colour mentioned this round is already in the palette.")
    return "\n".join(lines)


def fix_glb_child_pivots(data: bytes) -> bytes:
    """Make child bone translations parent-relative in an exported .glb.

    designbridge writes each bone's pivot verbatim as its glTF node
    translation, but glTF composes a child's transform on top of its parent's,
    so every nested bone comes out displaced by its parent's pivot. The
    engine-native .boxmodel.json is absolute throughout and is correct; only
    the .glb is affected, and only for rigs more than one bone deep - which is
    every rig worth having.

    Rather than edit designbridge, the fix is applied to the bytes on the way
    out: walk the node tree and replace each child's translation with
    translation - parent_translation. Vertex data is untouched, because the
    exporter already writes verts relative to their own bone's pivot.
    """
    try:
        if data[:4] != b"glTF":
            return data
        json_len = struct.unpack("<I", data[12:16])[0]
        head, body = data[:20], data[20:]
        doc = json.loads(body[:json_len].decode("utf-8"))
        nodes = doc.get("nodes") or []
        if not nodes:
            return data

        original = [list(n.get("translation") or [0.0, 0.0, 0.0]) for n in nodes]
        changed = False

        def walk(i, parent_world):
            nonlocal changed
            mine = original[i]
            rel = [mine[k] - parent_world[k] for k in range(3)]
            if any(abs(rel[k] - mine[k]) > 1e-9 for k in range(3)):
                nodes[i]["translation"] = rel
                changed = True
            for c in nodes[i].get("children") or []:
                walk(c, mine)

        for scene in doc.get("scenes") or []:
            for root in scene.get("nodes") or []:
                walk(root, [0.0, 0.0, 0.0])
        if not changed:
            return data

        new_json = json.dumps(doc, separators=(",", ":")).encode("utf-8")
        new_json += b" " * ((4 - len(new_json) % 4) % 4)
        rest = body[json_len:]
        out = bytearray()
        out += b"glTF" + struct.pack("<II", 2, 12 + 8 + len(new_json) + len(rest))
        out += struct.pack("<II", len(new_json), 0x4E4F534A) + new_json
        out += rest
        return bytes(out)
    except Exception:
        # A fix that cannot be applied must never cost the user their export.
        return data


def normalize_folder(folder) -> str:
    """One canonical spelling for a folder path.

    A folder is only the slash-separated path its assets carry, so two
    spellings of the same path - `STRATA / foundry` and `STRATA/foundry` -
    would otherwise show up as two different folders holding half the assets
    each. Segments are trimmed and rejoined on a bare slash.
    """
    segs = [seg.strip() for seg in str(folder or "").split("/")]
    return "/".join(seg for seg in segs if seg)


def detect_kind_from_source(dsl: str) -> str:
    """Read the kind straight off the document's own declaration.

    The grammar names its own track on the first real line - `model stalker {`
    - so an AUTO asset never has to be told what it is; the source already
    says. Comments and blank lines are skipped, nothing else is guessed.
    """
    for raw in (dsl or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        m = _KIND_DECL_RE.match(line)
        return _DECL_TO_KIND.get(m.group(1), "") if m else ""
    return ""


def guess_kind_from_text(text: str) -> str:
    """Guess a track from a brief, for an AUTO asset with no source yet."""
    t = (text or "").lower()
    best, score = "", 0
    for kind, words in _KIND_HINTS.items():
        hits = sum(1 for w in words if w in t)
        if hits > score:
            best, score = kind, hits
    return best


def resolve_kind(track: str, dsl: str = "", text: str = "") -> tuple:
    """(kind, was_auto). A fixed track is obeyed; AUTO reads the source first."""
    track = (track or "model").lower()
    if track in TRACK_KINDS:
        return track, False
    kind = detect_kind_from_source(dsl) or guess_kind_from_text(text) or "model"
    return kind, True


def reuse_blocks_text(blocks: list, kind: str) -> str:
    """The REUSABLE BLOCKS section: parts already built, ready to be reused.

    Reuse is cheaper than redesign and keeps a project visually consistent, so
    the blocks go in as source the model may copy verbatim rather than as prose
    describing them.
    """
    usable = [b for b in (blocks or []) if (b.get("source") or "").strip()
              and (not b.get("kind") or b.get("kind") == kind)]
    if not usable:
        return ""
    out = ["REUSABLE BLOCKS - already built, verified, and yours to copy",
           "  Paste a block's lines into the document when the brief calls for that part,",
           "  then adjust its `at` position and sizes to fit. Do not redesign a part that is",
           "  already here, and keep its proportions unless the instruction says otherwise."]
    for b in usable[:8]:
        name = (b.get("name") or "block").strip()
        note = (b.get("note") or "").strip()
        src = (b.get("source") or "").strip()
        out.append(f"\n  BLOCK {name}" + (f" - {note}" if note else ""))
        for line in src.splitlines():
            out.append("    " + line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# HOW TO WRITE A BRIEF
# Handed to the AI once, so the descriptions it writes land cleanly in the
# Studio. A brief is not geometry and never becomes geometry: it is the
# request line of a later prompt. Everything in here is behaviour the Studio
# actually parses - the `Animations:` line, the `name: description` form, the
# palette names - so it is kept in step with the app, not aspirational.
# ---------------------------------------------------------------------------
BRIEF_GUIDE = """HOW TO WRITE ASSET BRIEFS FOR THIS PIPELINE

WHAT A BRIEF IS
  A brief is one paragraph of plain English describing ONE asset. It is not
  the design document and it is not geometry. It becomes the request line of
  a prompt that the Studio builds later, with the grammar, the palette, the
  shape budget and the output contract attached automatically.

  So in a brief you never write: grammar, `box`, `bone`, coordinates,
  vertices, code, JSON, or file formats. You write intent.

THE SHAPE OF ONE BRIEF
  <snake_case_name>: <one paragraph>. Animations: <clip>, <clip>, <clip>.

  Example:
    slag_hound: compact barrel-chested quadruped furnace hound, about 1.1m
    tall, heavy soot-basalt plating over a darker underbelly. Wide bull neck
    with overlapping angled collar plates, two dorsal louver vents leaking
    recessed heat glow, hinged vertical-barred jaw facing +Z that opens on a
    glowing inner throat, piston-braced legs with brass hydraulic rods and
    broad clamped iron paws. Animations: idle, run, bite_charge, hit, death.

  The leading `name:` sets the asset name. Without it the asset is named
  after the first document that compiles. Use snake_case: it becomes a
  filename and an id.

WHAT TO PUT IN, IN ROUGHLY THIS ORDER
  1. SILHOUETTE FIRST. The overall read at a glance - "compact barrel-chested
     quadruped", "tall gaunt asymmetric biped". This matters more than detail:
     an asset is recognised by its outline before anything else.
  2. SCALE. Give one real number - "about 2.2m tall", "roughly 11 blocks
     wide". One is enough; the rest is inferred from it.
  3. MASSES AND HOW THEY JOIN. Name the parts and say where they attach:
     "reverse-joint hind legs", "left arm hangs longer as a segmented whip".
     For anything that will animate, say which joint a part turns about -
     that becomes its pivot, and a wrong pivot makes a limb swing through
     the body.
  4. MATERIALS BY PALETTE NAME. Use the names from the active region palette
     (`shadow`, `oxidized_copper`, `emissive_thermal`). Do not invent colour
     names and do not write hex in a brief - the palette table is attached to
     the prompt automatically and the names are resolved there.
  5. THE ONE READABLE DETAIL. One feature that makes it identifiable: a
     recessed optic slit, a rear radiator drum behind grate bars, a cracked
     ceramic shoulder. One, not six.
  6. FACING. Say which way is forward (`+Z` is forward in this pipeline) when
     the asset has a front.
  7. ANIMATIONS, LAST, ON ITS OWN SENTENCE. Write exactly:
         Animations: idle, run, lunge_attack, hit, death.
     The Studio reads that line and pre-fills the clip list, so the names
     must be snake_case and comma-separated. Leave the line out entirely for
     anything that does not move.

WHAT TO LEAVE OUT
  - Lore, mood and backstory. "Haunted by the old war" compiles to nothing.
    Describe what is VISIBLE.
  - Counts you do not mean literally. "Dozens of pipes" becomes a shape
    budget violation; say "three ribbed bands" if that is what you want.
  - Hex colours, coordinates, and any syntax. Those belong to the document,
    not the brief.
  - Two assets in one brief. One brief, one asset. A turret and its base are
    one asset; a turret and a wall are two.

GAMEPLAY-READABLE DESIGN
  If the asset is a threat, say what the player is meant to read. A weak point
  belongs in the brief - "exposed radiator drum strapped across the upper back
  behind grate bars, glowing hot, so the flank is the punish target" - because
  it changes the geometry. Silhouette distinctions between two enemies in the
  same region are worth stating explicitly.

BUDGET AWARENESS
  Each asset is built under a shape budget set by its complexity level, and
  the budget is enforced by the compiler. At the default (3) a model gets
  about 48 boxes total and 7 per bone, and animation is capped at 5 clips of
  5 keys. Write a brief that fits that: "offset carapace plates" is buildable,
  "individually modelled scales" is not.

SEVERAL AT ONCE
  Write one brief per line, no blank lines between, no bullets or dashes:

    moss_stalker: low predatory quadruped... Animations: idle, run, hit.
    gloom_lurker: tall gaunt biped... Animations: idle, walk, slash_attack.
    camp_sentry_turret: grounded three-legged tripod... Animations: idle_scan.

  That block goes straight into CREATE with "A DESCRIPTION" and "BATCH"
  selected, and becomes one asset per line.

IF YOU ALREADY HAVE THE DOCUMENTS
  Briefs are only needed when the design document does not exist yet. If you
  have already written the documents, hand those over instead and say so -
  they go into CREATE with "THE OUTPUT ALREADY" selected, and are compiled on
  arrival with no prompt round trip.
"""


# ---------------------------------------------------------------------------
# LIGHTING
# The units are the ones the rest of the pipeline measures in, so a brief can
# say "lit for 6 metres" and mean something the compiler can check.
# ---------------------------------------------------------------------------
LIGHTING_GUIDE = """LIGHTING IN THIS PIPELINE

THE UNITS - these are the real ones, not an analogy
  1 structure cell  =  1 STRATA block  =  1 metre  =  16 voxels
  Model and prop coordinates are in voxels, so a 32-tall model is 2 m tall.
  Light is measured per block, so a structure's light numbers and a model's
  size numbers are on the same scale: 16 to 1.

THE LIGHT SCALE
  Light level runs 0 to 15, whole numbers only, and loses exactly ONE level
  per block it travels. There is no inverse-square falloff and no colour
  bleed: the level is a count of blocks remaining.

      material lamp glowstone color=#FFD9A0 light=14

  That lamp is level 14 at its own cell, 13 one block away, 12 two blocks
  away, and reaches 0 fourteen blocks out. So `light=N` means "lights N
  blocks of open air". Pick the number from the distance you want covered:

      light=15   floods a 15 m radius. A forge mouth, a rift, a reactor.
      light=14   the standard bright lamp. A room up to ~28 m across.
      light=10   a wall sconce. Comfortable for a 20 m hall.
      light=7    a dim ember. Exactly at the dark threshold - see below.
      light=4    barely visible glow. Decorative, not useful.
      light=0    emits nothing. This is the default for every material.

WHAT BLOCKS LIGHT
  Light is stopped by a cell whose form fills half the cell or more - a cube,
  a slab, a chamfered block. It passes through a form declared open (grates
  and bars) and through thin forms like a quarter plate, losing the usual one
  level per block. It does not bend around corners: light that leaves a room
  through a doorway continues down the corridor, one level per block, and a
  room with no opening and no lamp inside it is pitch black however bright
  the room next door is.

  So lighting an interior means putting an emitter INSIDE it. A window does
  not let your own lamps in.

THE DARK THRESHOLD
  A standable cell - empty, with something solid under it and headroom above
  - at or below level 7 counts as dark. The compiler reports this directly:

      40 of 121 standable cells at or below level 7 (33% dark)
      darkest standable cell is level 4

  Read that line. It is the one measurement that says whether a build is
  actually lit. If a brief asks for a lit interior, aim for 0 dark standable
  cells; if it asks for a half-ruined hall where something could lurk, some
  dark cells are the point - say so rather than lighting it by accident.

HOW TO LIGHT A BUILD
  1. Count the span first. A hall 20 blocks across needs an emitter within
     10 blocks of its far corners, so one level-14 lamp in the middle covers
     it and two level-10 sconces on opposite walls also do.
  2. Put emitters where they would really be: sconces on walls at head height
     (y of the floor plus 2 or 3), furnace mouths at floor level, ceiling
     fixtures under the roof slab.
  3. An emitter is a normal cell with an emitting material, so it still needs
     to obey support rules and still occupies its cell.
  4. Light the stairs and the doorways. They are where dark cells hide.
  5. Then read the LIGHT block in the report and fix what it says. Adding one
     lamp and recompiling is cheap.

COLOUR
  `color=` on the material is the block's own colour and also the colour of
  the light it casts. A warm lamp is `#FFD9A0`, a furnace `#FF6A1A`, a gloom
  crystal `#4A1C6B`. Keep these inside the region palette like any other
  colour; an emitter is not an excuse for a new hue.

WHAT YOU DO NOT WRITE
  No light probes, no lightmaps, no shadow settings, no intensity in lumens
  or watts, no falloff curves, no ambient or exposure values. The only
  lighting control is `light=0..15` on a material. The renderer and the
  engine derive everything else from the propagated levels.
"""


def build_engineered_prompt(payload):
    """Build the full engineered prompt with the real compiler in designbridge.py.

    Studio v2 used to assemble a short header in JavaScript, which skipped the
    role, reasoning ladder, complete grammar, anchors, failure catalogue, worked
    example, output contract, and self-check. This routes the workbench through
    the same prompt compiler as `designbridge.py prompt`, then adds the round
    context (current source, compiler report, edit/fix/new-take instruction).
    """
    raw_track = (payload.get("kind") or payload.get("track") or "model").lower()
    kind = raw_track if raw_track in TRACK_KINDS else ""

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
    # A region variant overrides the project lock without restating it.
    if payload.get("variantLock"):
        base = style_lock.rstrip()
        style_lock = (base + "\n\n" + payload["variantLock"]) if base else payload["variantLock"]
    runnable = bool(payload.get("runnable", False))
    reuse = payload.get("blocks") or []

    # AUTO: the track is read off the document that exists, and only guessed
    # from the brief when there is no document yet. One project can therefore
    # hold a model, a prop and a texture without the operator switching a
    # setting before every round.
    auto_track = not kind
    if auto_track:
        kind, _ = resolve_kind("auto", dsl, instruction or name)

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
        "  3. If you had to depart from the brief - a colour substituted, a part\n"
        "     dropped to stay inside the budget - add one line after the fence:\n"
        "        deviations: <what you changed, and why, in one sentence each>\n"
        "     Say nothing there if you changed nothing. This line does not count\n"
        "     against the four-line plan.\n"
        "  4. Nothing else after the fence. Send the WHOLE source every round -\n"
        "     never a patch, never a fragment, never a diff.\n"
        "  Do not wrap the source in angle brackets or any other marker. The fence\n"
        "  alone is the delimiter.\n"
        "\n"
        "  SEVERAL ASSETS IN ONE REPLY are welcome. Give each its own fenced block,\n"
        "  one complete document per block. They are split apart and filed\n"
        "  separately, so three fences means three assets - or three takes on one,\n"
        "  if they share a name. Do not put two documents inside one fence.\n"
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

    prec = precedence_block(instruction or "", style_lock, dsl)
    if prec:
        extra.append(prec)
    if kind == "structure":
        # designbridge knows three tracks; the structure grammar is ours, so
        # the prompt has to carry it or the model is guessing at the syntax.
        extra.append(STRUCTURE_GRAMMAR)
        fb = forms_block((payload.get("style") or {}).get("forms"))
        if fb:
            extra.append(fb)
        extra.append(textures_block(payload.get("textures") or []))
    if GOTCHAS.get(kind):
        extra.append(GOTCHAS[kind])

    if auto_track:
        src = "the current source" if detect_kind_from_source(dsl) else "the brief"
        extra.append(
            f"TRACK - AUTO, resolved to {kind.upper()} from {src}\n"
            f"  This asset is not pinned to one track. The grammar above is the {kind} grammar\n"
            f"  because that is what {src} declares. Write a {kind} document and nothing else;\n"
            "  do not mix two grammars in one document."
        )

    # Standing art direction leads the round context: it governs everything
    # that follows, including a NEW TAKE that otherwise inherits nothing.
    lock_block = style_lock_text(style_lock, payload.get("style"), kind)
    if lock_block:
        extra.append(lock_block)

    # The plan the model agreed to, stated back as the constraint it is.
    if kind == "structure" and payload.get("blockPlan"):
        known = {t["name"] for t in (payload.get("textures") or [])}
        blk = agreed_plan_block(payload.get("blockPlan"), known)
        if blk:
            extra.append(blk)

    # What this texture is for decides what a correct texture even is.
    if kind == "texture" and payload.get("textureIntent"):
        extra.append(texture_target_block(payload.get("textureIntent")))

    # A model or prop's colour slots, and which already carry a texture.
    if kind in ("model", "prop") and payload.get("slots"):
        extra.append(slot_block(payload.get("slots") or [],
                                payload.get("slotBindings") or {}, kind))

    # Clips alongside the geometry. Only the model track has bones to move.
    if payload.get("wantClips") and kind == "model":
        lvl = int(payload.get("complexity") or (payload.get("style") or {}).get("complexity") or 3)
        extra.append(clips_block(payload.get("clips") or [], lvl))

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
    reuse_block = reuse_blocks_text(reuse, kind)
    if reuse_block:
        extra.append(reuse_block)

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
                "dims": lock_dims_line(payload.get("style"), kind),
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

                if path == "/api/lightguide":
                    return self._send_json({"ok": True, "prompt": LIGHTING_GUIDE,
                                             "length": len(LIGHTING_GUIDE)})

                if path == "/api/briefguide":
                    return self._send_json({"ok": True, "prompt": BRIEF_GUIDE,
                                             "length": len(BRIEF_GUIDE)})

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

                if path == "/api/export/bulk.zip":
                    from urllib.parse import parse_qs as _pq
                    q = _pq(parsed.query)
                    ids = [x for x in (q.get("ids", [""])[0] or "").split(",") if x]
                    fmt = (q.get("fmt", ["obj"])[0] or "obj")
                    scope = (q.get("versions", ["head"])[0] or "head")
                    cids = [x for x in (q.get("commits", [""])[0] or "").split(",") if x]
                    if not ids:
                        return self._send_json({"ok": False, "error": "No assets selected"}, 400)
                    data, ctype, fname = storage.build_bulk_zip(
                        ids, fmt, scope, cids, (q.get("name", [""])[0] or "").strip())
                    return self._send_bytes(data, ctype, 200,
                                            {"Content-Disposition": f'attachment; filename="{fname}"'})

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
                if path == "/api/commits/label":
                    return self._send_json(storage.set_commit_label(body))

                if path == "/api/commits/promote":
                    return self._send_json(storage.promote_commits(body))

                if path == "/api/commits/delete":
                    return self._send_json(storage.delete_commits(body))

                if path == "/api/selfcheck/prompt":
                    return self._send_json(storage.selfcheck_brief(body))

                if path == "/api/folders/create":
                    return self._send_json(storage.create_folder(body))

                if path == "/api/folders/delete":
                    return self._send_json(storage.delete_folder(body))

                if path == "/api/rename/prompt":
                    return self._send_json(storage.rename_brief(body))

                if path == "/api/rename/apply":
                    return self._send_json(storage.apply_rename(body))

                if path == "/api/projects/action":
                    return self._send_json(storage.bulk_project_action(body))

                if path == "/api/intake":
                    return self._send_json(storage.intake_documents(body))

                if path == "/api/projects/bulk":
                    return self._send_json(storage.create_projects_bulk(body))

                if path == "/api/projects/track":
                    return self._send_json(storage.set_project_track(body))

                if path == "/api/blocks":
                    return self._send_json(storage.save_block(body))

                if path == "/api/blocks/delete":
                    return self._send_json(storage.delete_block(body))

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
                if path == "/api/locks":
                    return self._send_json({"ok": True, "locks": storage.list_locks(),
                                             "scopes": storage.scopes_for_asset(
                                                 (body.get("projectId") or "").strip()),
                                             "resolved": storage.resolved_lock(
                                                 (body.get("projectId") or "").strip(),
                                                 body.get("fallback") or "")})
                if path == "/api/locks/set":
                    return self._send_json(storage.set_lock(
                        body.get("scope") or "", body.get("body") or ""))

                if path == "/api/workspaces":
                    return self._send_json({"ok": True, "workspaces": storage.list_workspaces()})
                if path == "/api/workspaces/save":
                    return self._send_json(storage.save_workspace(body))
                if path == "/api/workspaces/delete":
                    return self._send_json(storage.delete_workspace(body))
                if path == "/api/workspaces/history":
                    return self._send_json(storage.workspace_history(
                        (body.get("id") or "").strip(), int(body.get("limit") or 120)))

                if path == "/api/blockplan/prompt":
                    pid = (body.get("projectId") or "").strip()
                    return self._send_json({"ok": True, "prompt": blockplan_prompt(
                        body.get("instruction") or "",
                        storage.list_textures(),
                        storage.effective_lock("", (body.get("styleLock") or "")),
                        (body.get("style") or {}).get("forms") or [])})

                if path == "/api/blockplan/apply":
                    return self._send_json(storage.apply_blockplan(body))

                if path == "/api/blockplan":
                    return self._send_json({"ok": True, "plan": storage.block_plan(
                        (body.get("projectId") or "").strip())})

                if path == "/api/texture/intent":
                    pid = (body.get("projectId") or "").strip()
                    if "target" in body:
                        tgt = str(body.get("target") or "block").lower()
                        if tgt not in storage.TEXTURE_TARGETS:
                            raise ValueError(f"target must be one of {', '.join(storage.TEXTURE_TARGETS)}")
                        storage.set_meta(pid, "texture_intent", {
                            "target": tgt,
                            "wrap": str(body.get("wrap") or "around"),
                            "note": str(body.get("note") or ""),
                        })
                    return self._send_json({"ok": True, "intent": storage.texture_intent(pid)})

                if path == "/api/slots":
                    return self._send_json(storage.asset_slots(
                        (body.get("projectId") or "").strip()))

                if path == "/api/slots/bind":
                    pid = (body.get("projectId") or "").strip()
                    binds = storage.get_meta(pid, "slot_bindings") or {}
                    hx = str(body.get("hex") or "").upper()
                    tex = str(body.get("texture") or "")
                    if tex:
                        binds[hx] = tex
                    else:
                        binds.pop(hx, None)
                    storage.set_meta(pid, "slot_bindings", binds)
                    return self._send_json(storage.asset_slots(pid))

                if path == "/api/textures/usable":
                    return self._send_json({"ok": True, "textures": storage.usable_textures(
                        (body.get("target") or "").strip())})

                if path == "/api/palettes":
                    return self._send_json({"ok": True, "palettes": storage.list_palettes(
                        (body.get("projectId") or "").strip())})
                if path == "/api/palettes/save":
                    return self._send_json(storage.save_palette(body))
                if path == "/api/palettes/activate":
                    return self._send_json(storage.activate_palette(body))
                if path == "/api/palettes/delete":
                    return self._send_json(storage.delete_palette(body))

                if path == "/api/animate/prompt":
                    return self._send_json(storage.animation_brief(
                        (body.get("projectId") or "").strip(), body.get("clips") or ""))

                if path == "/api/animate/apply":
                    return self._send_json(storage.apply_animation(body))

                if path == "/api/compile":
                    return self._send_json(storage.compile_and_save(body))
                if path == "/api/llm/run":
                    return self._send_json(storage.run_direct_llm(body.get("prompt", "")))
                if path == "/api/prompt":
                    # A building binds its materials to textures that exist.
                    # The model cannot know their names unless it is told, so
                    # the workspace's own texture assets ride along.
                    if "textures" not in body:
                        body["textures"] = storage.list_textures()
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
