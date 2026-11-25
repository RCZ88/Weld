#!/usr/bin/env python3
"""
DesignBridge Human Studio Server (FastAPI).
Wraps designbridge.py without modifying it, providing:
  - Chat-driven session persistence & version timeline
  - Initial Prompt & Contextual Edit/Comment Prompt builders
  - Live GLB (3D WebGL + animations) & PNG/Material Map rendering
  - Structured diagnostics + one-click file / ZIP exports
"""
import io
import json
import os
import re
import struct
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

import designbridge as db

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STUDIO_HOME = Path(
    os.environ.get("DESIGNBRIDGE_STUDIO_HOME")
    or (Path.home() / "designbridge_studio_v2")
)
STUDIO_HOME.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="DesignBridge Studio", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- helpers
def safe_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "project").strip().lower()).strip("_")
    return (slug[:60] or "project")


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def proj_path(project_id: str) -> Path:
    pid = safe_slug(project_id)
    p = STUDIO_HOME / pid
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_file(project_id: str) -> Path:
    return proj_path(project_id) / "studio_state.json"


def load_state(project_id: str) -> Dict[str, Any]:
    sf = state_file(project_id)
    if sf.exists():
        return json.loads(sf.read_text(encoding="utf-8"))
    return {
        "id": safe_slug(project_id),
        "name": project_id,
        "kind": "model",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "messages": [],
        "versions": [],
    }


def save_state(project_id: str, state: Dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    state_file(project_id).write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def extract_diagnostics(kind: str, doc: Any) -> List[Dict[str, Any]]:
    out = []
    if kind == "prop":
        for d in doc.diags:
            out.append({
                "severity": d.sev,
                "line": d.line,
                "message": d.msg,
                "fix": d.fix,
                "code": d.code,
            })
    else:
        for sev, line, msg, fix, code in doc.diags:
            out.append({
                "severity": sev,
                "line": line,
                "message": msg,
                "fix": fix,
                "code": code,
            })
    return out


def extract_metadata(kind: str, doc: Any) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "kind": kind,
        "name": getattr(doc, "name", "asset"),
        "ok": len(doc.errors) == 0,
        "error_count": len(doc.errors),
        "warning_count": len(getattr(doc, "warnings", [])),
        "repairs": list(dict.fromkeys(getattr(doc, "repairs", []))),
    }
    if kind == "prop":
        s = db.stats(doc)
        meta.update({
            "units": doc.units,
            "bbox": s["bbox"],
            "triangles": s["triangles"],
            "volume_cm3": round(s["volume_mm3"] / 1000.0, 2),
            "surface_cm2": round(s["surface_mm2"] / 100.0, 2),
            "watertight": s["watertight"],
            "parts": s["per_part"],
        })
    elif kind == "model":
        lo, hi = db.bbox(doc)
        size = [round(hi[i] - lo[i], 1) for i in range(3)]
        nboxes = sum(len(doc.bones[n].boxes) for n in doc.order)
        meta.update({
            "bbox": size,
            "blocks": [round(v / 16.0, 2) for v in size],
            "bone_count": len(doc.order),
            "box_count": nboxes,
            "bones": [
                {
                    "name": n,
                    "parent": doc.bones[n].parent,
                    "pivot": list(doc.bones[n].pivot),
                    "boxes": len(doc.bones[n].boxes),
                }
                for n in doc.order
            ],
            "animations": [
                {
                    "name": a.name,
                    "loop": a.loop,
                    "duration": max([k.t for k in a.keys], default=0.0),
                    "keys": len(a.keys),
                }
                for a in doc.anims.values()
            ],
        })
    elif kind == "texture":
        pal = []
        if not doc.errors:
            img = db.paint(doc)
            total = max(1, doc.size * doc.size)
            for c, cnt in db.palette_of(img)[:16]:
                pal.append({
                    "hex": db.hex_of(c),
                    "pct": round(100.0 * cnt / total, 1),
                    "count": cnt,
                })
        meta.update({
            "size": doc.size,
            "metallic": doc.metallic,
            "roughness": doc.roughness,
            "layers": [{"kind": l.kind, "args": l.args} for l in doc.layers],
            "palette": pal,
        })
    return meta


def export_model_glb_fixed_hierarchy(doc: db.ModelDoc, path: Path) -> int:
    """
    Exports a model GLB with proper parent-relative bone translations so nested
    bones with non-zero parent pivots render accurately in Three.js GLTFLoader.
    """
    prims = db._pack_mesh(doc)
    bin_parts: List[bytes] = []
    offset = 0
    accessors: List[Dict[str, Any]] = []
    buffer_views: List[Dict[str, Any]] = []

    def add_view(data: bytes, target: Optional[int] = None) -> int:
        nonlocal offset
        pad = (4 - (len(data) % 4)) % 4
        padded = data + b"\x00" * pad
        bv: Dict[str, Any] = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(data),
        }
        if target:
            bv["target"] = target
        buffer_views.append(bv)
        bin_parts.append(padded)
        offset += len(padded)
        return len(buffer_views) - 1

    nodes: List[Dict[str, Any]] = []
    meshes: List[Dict[str, Any]] = []
    name_to_node: Dict[str, int] = {}

    for i, (name, bone, pos, nrm, col, idx) in enumerate(prims):
        parent_pivot = (0.0, 0.0, 0.0)
        if bone.parent and bone.parent in doc.bones:
            parent_pivot = doc.bones[bone.parent].pivot
        rel_trans = [
            float(bone.pivot[k] - parent_pivot[k]) for k in range(3)
        ]
        node: Dict[str, Any] = {"name": name, "translation": rel_trans}
        if pos:
            pv = add_view(b"".join(struct.pack("<3f", *p) for p in pos), 34962)
            mn = [min(p[k] for p in pos) for k in range(3)]
            mx = [max(p[k] for p in pos) for k in range(3)]
            pa = len(accessors)
            accessors.append({
                "bufferView": pv, "componentType": 5126, "count": len(pos),
                "type": "VEC3", "min": mn, "max": mx,
            })
            nv = add_view(b"".join(struct.pack("<3f", *v) for v in nrm), 34962)
            na = len(accessors)
            accessors.append({
                "bufferView": nv, "componentType": 5126, "count": len(nrm),
                "type": "VEC3",
            })
            cv = add_view(b"".join(struct.pack("<4f", *c) for c in col), 34962)
            ca = len(accessors)
            accessors.append({
                "bufferView": cv, "componentType": 5126, "count": len(col),
                "type": "VEC4",
            })
            iv = add_view(b"".join(struct.pack("<I", v) for v in idx), 34963)
            ia = len(accessors)
            accessors.append({
                "bufferView": iv, "componentType": 5125, "count": len(idx),
                "type": "SCALAR",
            })
            meshes.append({
                "name": name,
                "primitives": [{
                    "attributes": {"POSITION": pa, "NORMAL": na, "COLOR_0": ca},
                    "indices": ia,
                    "material": 0,
                }],
            })
            node["mesh"] = len(meshes) - 1
        nodes.append(node)
        name_to_node[name] = i

    children_of: Dict[int, List[int]] = {i: [] for i in range(len(nodes))}
    roots: List[int] = []
    for name, bone, *_ in prims:
        i = name_to_node[name]
        if bone.parent and bone.parent in name_to_node:
            children_of[name_to_node[bone.parent]].append(i)
        else:
            roots.append(i)
    for i, kids in children_of.items():
        if kids:
            nodes[i]["children"] = kids

    gltf: Dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "DesignBridge Studio"},
        "scene": 0,
        "scenes": [{"nodes": roots}],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "materials": [{
            "name": "vertexcolor",
            "pbrMetallicRoughness": {
                "baseColorFactor": [1, 1, 1, 1],
                "metallicFactor": 0.05,
                "roughnessFactor": 0.85,
            },
        }],
    }

    animations = []
    for aname, anim in doc.anims.items():
        by_bone: Dict[str, List[Any]] = {}
        for k in anim.keys:
            by_bone.setdefault(k.bone, []).append(k)
        channels, samplers = [], []
        for bname, keys in by_bone.items():
            if bname not in name_to_node:
                continue
            node_idx = name_to_node[bname]
            bone = doc.bones[bname]
            parent_pivot = (
                doc.bones[bone.parent].pivot
                if bone.parent and bone.parent in doc.bones
                else (0.0, 0.0, 0.0)
            )
            base_t = [bone.pivot[c] - parent_pivot[c] for c in range(3)]
            keys = sorted(keys, key=lambda k: k.t)

            for chan_path, is_rot in (("rotation", True), ("translation", False)):
                kk = [k for k in keys if (k.rot if is_rot else k.pos) is not None]
                if not kk:
                    continue
                t_list = [float(k.t) for k in kk]
                tv = add_view(b"".join(struct.pack("<f", v) for v in t_list))
                ta = len(accessors)
                accessors.append({
                    "bufferView": tv, "componentType": 5126, "count": len(t_list),
                    "type": "SCALAR", "min": [min(t_list)], "max": [max(t_list)],
                })
                if is_rot:
                    vals = [db._euler_quat(k.rot) for k in kk]
                    vv = add_view(b"".join(struct.pack("<4f", *v) for v in vals))
                    typ = "VEC4"
                else:
                    vals = [
                        (base_t[0] + k.pos[0], base_t[1] + k.pos[1], base_t[2] + k.pos[2])
                        for k in kk
                    ]
                    vv = add_view(b"".join(struct.pack("<3f", *v) for v in vals))
                    typ = "VEC3"
                va = len(accessors)
                accessors.append({
                    "bufferView": vv, "componentType": 5126, "count": len(vals),
                    "type": typ,
                })
                samplers.append({"input": ta, "output": va, "interpolation": "LINEAR"})
                channels.append({
                    "sampler": len(samplers) - 1,
                    "target": {"node": node_idx, "path": chan_path},
                })
        if channels:
            animations.append({"name": aname, "channels": channels, "samplers": samplers})
    if animations:
        gltf["animations"] = animations

    buf = b"".join(bin_parts)
    gltf["buffers"] = [{"byteLength": len(buf)}]
    jtext = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    jtext += b" " * ((4 - len(jtext) % 4) % 4)
    bpad = buf + b"\x00" * ((4 - len(buf) % 4) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(jtext) + 8 + len(bpad))
    glb += struct.pack("<II", len(jtext), 0x4E4F534A) + jtext
    glb += struct.pack("<II", len(bpad), 0x004E4942) + bpad
    path.write_bytes(glb)
    return len(glb)


def build_artifacts(project_id: str, vid: int, kind: str, doc: Any) -> Dict[str, Optional[str]]:
    """Build live viewable artifacts for a version and return their API URLs."""
    art_dir = proj_path(project_id) / "artifacts"
    art_dir.mkdir(exist_ok=True)
    base_url = f"/api/projects/{safe_slug(project_id)}/artifact/{vid}"
    urls: Dict[str, Optional[str]] = {
        "glb": None,
        "albedo": None,
        "material": None,
        "preview": None,
    }

    if kind == "texture":
        if not doc.errors:
            alb = db.paint(doc)
            alb_path = art_dir / f"v{vid}_albedo.png"
            mat_path = art_dir / f"v{vid}_material.png"
            prev_path = art_dir / f"v{vid}_preview.png"
            alb.save_png(str(alb_path))
            db.material_map(doc, alb).save_png(str(mat_path))
            alb.scale_nn(max(8, 256 // max(1, doc.size))).save_png(str(prev_path))
            urls["albedo"] = f"{base_url}/v{vid}_albedo.png"
            urls["material"] = f"{base_url}/v{vid}_material.png"
            urls["preview"] = f"{base_url}/v{vid}_preview.png"
    elif kind == "model":
        if not doc.errors and db.colored_tris(doc):
            glb_path = art_dir / f"v{vid}.glb"
            export_model_glb_fixed_hierarchy(doc, glb_path)
            if glb_path.exists() and glb_path.stat().st_size > 0:
                urls["glb"] = f"{base_url}/v{vid}.glb"
            prev_path = art_dir / f"v{vid}_preview.png"
            if db.preview_any(kind, doc, str(prev_path)):
                urls["preview"] = f"{base_url}/v{vid}_preview.png"
    else:  # prop
        if db.merged(doc).tris:
            glb_path = art_dir / f"v{vid}.glb"
            nbytes = db.export_mesh_glb(doc, str(glb_path))
            if nbytes and glb_path.exists():
                urls["glb"] = f"{base_url}/v{vid}.glb"
            prev_path = art_dir / f"v{vid}_preview.png"
            if db.preview_any(kind, doc, str(prev_path)):
                urls["preview"] = f"{base_url}/v{vid}_preview.png"
    return urls


def build_edit_prompt(
    kind: str,
    comment: str,
    current_source: str,
    report_text: str,
    include_report: bool = True,
    include_grammar: bool = False,
) -> str:
    """Build a crisp, self-contained refinement prompt for the AI."""
    parts = [
        f"You are iterating on an existing DesignBridge {kind.upper()} asset.",
        "The human reviewer inspected the rendered 3D/2D preview and left feedback below.",
        "",
        "HUMAN FEEDBACK / REQUESTED EDITS",
        "  " + comment.strip().replace("\n", "\n  "),
        "",
        "CURRENT SOURCE (the version the human just viewed)",
        "<<<DESIGN",
        current_source.strip(),
        ">>>",
    ]
    if include_report and report_text.strip():
        parts += [
            "",
            "COMPILER REPORT & VIEWS FOR THIS VERSION",
            report_text.strip(),
        ]
    if include_grammar:
        parts += [
            "",
            "DSL REFERENCE REMINDER",
            db.prompt_for(kind, "(follow the edit feedback above)", runnable=False),
        ]
    parts += [
        "",
        "INSTRUCTIONS FOR THIS EDIT ROUND",
        "  1. Fix any compiler ERRORS first (if present in the report).",
        "  2. Apply every requested change from the HUMAN FEEDBACK accurately while keeping",
        "     the rest of the design clean, watertight, and well-proportioned.",
        "  3. Reply with 2-4 short lines explaining what you changed, followed immediately by",
        "     the COMPLETE updated source inside <<<DESIGN ... >>> (no markdown fences, never a partial patch).",
        "  4. Output nothing after >>>.",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------- schemas
class CreateProjectReq(BaseModel):
    name: str
    kind: str = "model"


class CreatePromptReq(BaseModel):
    brief: str
    kind: str = "model"
    runnable: bool = False


class EditPromptReq(BaseModel):
    comment: str
    version_id: Optional[int] = None
    include_report: bool = True
    include_grammar: bool = False


class CompileReq(BaseModel):
    source: str
    note: str = ""
    append_chat: bool = True


# ---------------------------------------------------------------- routes
@app.get("/", response_class=HTMLResponse)
def index():
    html_file = STATIC_DIR / "index.html"
    if not html_file.exists():
        raise HTTPException(status_code=500, detail="static/index.html not found")
    return HTMLResponse(html_file.read_text(encoding="utf-8"))


@app.get("/api/tokens")
def get_tokens():
    return {"tokens": db.TOKENS, "build_volume": list(db.BUILD_VOLUME)}


@app.get("/api/example/{kind}")
def get_example(kind: str):
    if kind not in ("texture", "model", "prop"):
        raise HTTPException(status_code=400, detail="kind must be texture, model, or prop")
    return {"kind": kind, "source": db._studio_example(kind)}


@app.get("/api/projects")
def list_projects():
    items = []
    for entry in sorted(STUDIO_HOME.iterdir()):
        if entry.is_dir() and (entry / "studio_state.json").exists():
            st = json.loads((entry / "studio_state.json").read_text(encoding="utf-8"))
            last_v = st["versions"][-1] if st.get("versions") else None
            items.append({
                "id": st["id"],
                "name": st["name"],
                "kind": st.get("kind", "model"),
                "version_count": len(st.get("versions", [])),
                "last_ok": last_v["ok"] if last_v else None,
                "updated_at": st.get("updated_at", ""),
            })
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return {"projects": items}


@app.post("/api/projects")
def create_project(req: CreateProjectReq):
    pid = safe_slug(req.name)
    # avoid overwriting an existing slug silently
    base = pid
    n = 2
    while state_file(pid).exists():
        pid = f"{base}_{n}"
        n += 1
    st = {
        "id": pid,
        "name": req.name.strip() or pid,
        "kind": req.kind if req.kind in ("texture", "model", "prop") else "model",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "messages": [],
        "versions": [],
    }
    save_state(pid, st)
    return st


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    import shutil
    p = STUDIO_HOME / safe_slug(project_id)
    if p.exists() and p.is_dir():
        shutil.rmtree(p)
    return {"deleted": project_id}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    sf = state_file(project_id)
    if not sf.exists():
        raise HTTPException(status_code=404, detail="project not found")
    return load_state(project_id)


@app.post("/api/projects/{project_id}/prompt/create")
def generate_create_prompt(project_id: str, req: CreatePromptReq):
    st = load_state(project_id)
    kind = req.kind if req.kind in ("texture", "model", "prop") else st.get("kind", "model")
    st["kind"] = kind
    prompt_text = db.prompt_for(kind, req.brief, runnable=req.runnable)

    msg_id = len(st["messages"]) + 1
    st["messages"].append({
        "id": msg_id,
        "type": "prompt",
        "mode": "create",
        "kind": kind,
        "brief": req.brief.strip(),
        "prompt": prompt_text,
        "time": now_iso(),
    })
    save_state(project_id, st)
    return {"message": st["messages"][-1], "state": st}


@app.post("/api/projects/{project_id}/prompt/edit")
def generate_edit_prompt(project_id: str, req: EditPromptReq):
    st = load_state(project_id)
    if not st["versions"]:
        raise HTTPException(status_code=400, detail="Compile at least one version before using Edit Mode.")

    target = None
    if req.version_id is not None:
        target = next((v for v in st["versions"] if v["id"] == req.version_id), None)
    if target is None:
        target = st["versions"][-1]

    vdir = proj_path(project_id) / "versions"
    src_text = (vdir / f"v{target['id']}.db").read_text(encoding="utf-8")
    rep_text = (vdir / f"v{target['id']}.report.txt").read_text(encoding="utf-8")
    kind = target["kind"]

    prompt_text = build_edit_prompt(
        kind=kind,
        comment=req.comment,
        current_source=src_text,
        report_text=rep_text,
        include_report=req.include_report,
        include_grammar=req.include_grammar,
    )

    msg_id = len(st["messages"]) + 1
    st["messages"].append({
        "id": msg_id,
        "type": "prompt",
        "mode": "edit",
        "kind": kind,
        "target_version": target["id"],
        "brief": req.comment.strip(),
        "prompt": prompt_text,
        "include_report": req.include_report,
        "include_grammar": req.include_grammar,
        "time": now_iso(),
    })
    save_state(project_id, st)
    return {"message": st["messages"][-1], "state": st}


@app.post("/api/projects/{project_id}/compile")
def compile_version(project_id: str, req: CompileReq):
    if not req.source.strip():
        raise HTTPException(status_code=400, detail="Empty source pasted.")

    st = load_state(project_id)
    kind, doc = db.compile_any(req.source)
    rep = db.report_any(kind, doc)
    diags = extract_diagnostics(kind, doc)
    meta = extract_metadata(kind, doc)

    vid = (st["versions"][-1]["id"] + 1) if st["versions"] else 1
    vdir = proj_path(project_id) / "versions"
    vdir.mkdir(exist_ok=True)
    (vdir / f"v{vid}.db").write_text(req.source, encoding="utf-8")
    (vdir / f"v{vid}.report.txt").write_text(rep, encoding="utf-8")

    urls = build_artifacts(project_id, vid, kind, doc)

    version_rec = {
        "id": vid,
        "kind": kind,
        "ok": meta["ok"],
        "note": req.note.strip() or f"{kind} build v{vid}",
        "time": now_iso(),
        "meta": meta,
        "diagnostics": diags,
        "urls": urls,
    }
    st["kind"] = kind
    st["versions"].append(version_rec)

    if req.append_chat:
        msg_id = len(st["messages"]) + 1
        st["messages"].append({
            "id": msg_id,
            "type": "compile",
            "version_id": vid,
            "kind": kind,
            "ok": meta["ok"],
            "note": version_rec["note"],
            "meta": meta,
            "diagnostics": diags,
            "urls": urls,
            "time": version_rec["time"],
        })

    save_state(project_id, st)
    return {
        "version": version_rec,
        "source": req.source,
        "report": rep,
        "state": st,
    }


@app.get("/api/projects/{project_id}/versions/{vid}")
def get_version(project_id: str, vid: int):
    st = load_state(project_id)
    v = next((x for x in st["versions"] if x["id"] == vid), None)
    if not v:
        raise HTTPException(status_code=404, detail="version not found")
    vdir = proj_path(project_id) / "versions"
    src = (vdir / f"v{vid}.db").read_text(encoding="utf-8")
    rep = (vdir / f"v{vid}.report.txt").read_text(encoding="utf-8")
    return {"version": v, "source": src, "report": rep}


@app.get("/api/projects/{project_id}/artifact/{vid}/{filename}")
def get_artifact(project_id: str, vid: int, filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    f = proj_path(project_id) / "artifacts" / filename
    if not f.exists():
        raise HTTPException(status_code=404, detail="artifact not found")
    ctype = "model/gltf-binary" if filename.endswith(".glb") else "image/png"
    return FileResponse(f, media_type=ctype, headers={"Cache-Control": "no-store"})


@app.get("/api/projects/{project_id}/export/{vid}/{fmt}")
def export_asset(project_id: str, vid: int, fmt: str):
    st = load_state(project_id)
    vdir = proj_path(project_id) / "versions"
    src_file = vdir / f"v{vid}.db"
    if not src_file.exists():
        raise HTTPException(status_code=404, detail="version not found")

    src = src_file.read_text(encoding="utf-8")
    kind, doc = db.compile_any(src)
    if doc.errors:
        raise HTTPException(status_code=400, detail="Cannot export a version with compiler errors.")

    slug = safe_slug(getattr(doc, "name", st["name"]))
    exp_dir = proj_path(project_id) / "exports" / f"v{vid}"
    made = db.export_any(kind, doc, str(exp_dir), slug)

    # For voxel models, also replace the GLB with the fixed-hierarchy GLB
    if kind == "model":
        export_model_glb_fixed_hierarchy(doc, exp_dir / f"{slug}.glb")

    if fmt == "zip":
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in made:
                zf.write(p, arcname=os.path.basename(p))
            zf.writestr(f"{slug}.db", src)
            zf.writestr(f"{slug}_report.txt", db.report_any(kind, doc))
        mem.seek(0)
        return StreamingResponse(
            mem,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{slug}_v{vid}.zip"'},
        )

    suffix_map = {
        "glb": f"{slug}.glb",
        "stl": f"{slug}.stl",
        "obj": f"{slug}.obj",
        "svg": f"{slug}_profiles.svg",
        "png": f"{slug}.png",
        "png16x": f"{slug}@16x.png",
        "material": f"{slug}_material.png",
        "recipe": f"{slug}.recipe.json",
        "boxmodel": f"{slug}.boxmodel.json",
    }
    target_name = suffix_map.get(fmt)
    if not target_name:
        raise HTTPException(status_code=400, detail=f"Unknown export format: {fmt}")
    target_path = exp_dir / target_name
    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"Format '{fmt}' not available for {kind}")
    return FileResponse(
        target_path,
        filename=target_name,
        media_type="application/octet-stream",
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8765"))
    print(f"\n  DesignBridge Human Studio -> http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port)
