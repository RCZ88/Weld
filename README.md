# DesignBridge

**A text-only asset bridge: let any AI that can only write text produce real game
assets — textures, 3D models with animation, and parametric props — in formats
that load anywhere (PNG, glTF/GLB) and in your own engine's JSON.**

Text AIs can't do graphics. Not for lack of taste — they have no eyes, they're
bad at coordinates, and they can't check their own work. Ask one for an image or
a mesh and you get broken syntax and invented geometry with nothing to debug.

DesignBridge fixes all three. The AI never emits pixels or vertices. It writes a
short declarative description. This tool compiles it into real assets, renders a
real picture, and reports back in plain text the AI can read — so it can see what
it made and fix it. You get files that open in Blender, a browser, Godot, or your
own Java engine.

```
brief ─▶ prompt ─▶ [ any AI ] ─▶ source text ─▶ compile ─▶ check ─▶ feedback
                        ▲                                              │
                        └────── palette / bone tree / ASCII views ◀────┘
                                   + a real PNG preview for you         │
                                                                        ▼
                      PNG · material map · glTF/GLB · engine JSON · STL · OBJ · SVG
```

One file. Pure Python 3.8+, standard library only — so it pastes straight into
any AI's code sandbox and runs on a bare Fedora box with nothing installed.

## Quick start

```bash
python3 designbridge.py selftest                       # verify everything works
python3 designbridge.py serve                           # open the Studio (below)

python3 designbridge.py prompt --kind texture --brief "mossy stone"   # prompt for an AI
python3 designbridge.py check  examples/mossy_cobble.tex.db            # the feedback report
python3 designbridge.py build  examples/husk_king.model.db -o out/     # export real files
```

## Three asset kinds, one language

The first word of a source file picks the track. The language is tolerant — it
repairs smart quotes, markdown fences, `key: value`, and pulls the design out of
any chatter the AI wraps around it.

**`texture` — 2D procedural tiles.** A stack of layers (`fill`, `noise`, `brick`,
`planks`, `scatter`, `veins`, `bevel`, `outline`, …) painted into a real tile.
Exports **albedo PNG + material PNG** (R,G = tangent normal, B = metallic,
A = roughness — the layout a procedural-atlas engine uses) **+ a recipe JSON**.

**`model` — blocky 3D models with animation.** Coloured boxes grouped into named
bones with pivots, plus keyframe animation. Exports **glTF/GLB** (Blender,
three.js, Godot, Unity, any website) **+ a box-model JSON** for a voxel engine
**+ OBJ**. This is where mobs, bosses and their attack animations come from.

**`prop` — smooth parametric 3D.** `extrude` / `revolve` / `loft` of 2D profiles,
with colours and holes, for pipes, machines and ruin details. Exports **GLB +
STL + OBJ + SVG**. Watertight geometry, verified.

## The Studio

`python3 designbridge.py serve` opens a local workbench at `http://127.0.0.1:8765`:

- **Left** — your assets and, under each, a **version history** saved to disk.
  Every compile-and-save is a version with a note, a timestamp and a pass/fail
  dot. Click any version to restore its source, report and preview. Nothing is
  lost; you can always go back.
- **Centre** — the source editor, a note field, and Compile / Save. It also
  copies the right **AI prompt** for the current kind, and loads examples.
- **Right** — a **real colour preview** (rendered by a software rasterizer, no
  GPU needed) and the **feedback report** the AI reads.

Projects live in `~/designbridge_studio/` (override with `DESIGNBRIDGE_HOME`).
Each version keeps its source, report and preview; **Export** writes every file
format to `export_v<n>/`.

## Why it's built this way

- **2D in, 3D out; no mesh math for the AI.** Models reason about flat outlines
  and boxes far better than volumes. The compiler does the geometry.
- **No 3D booleans.** Hollow props put the hole in the 2D profile, so parts are
  watertight by construction, not by luck.
- **The feedback is the product.** Errors carry a line number and a concrete fix.
  Every character of an ASCII view is a ray fired through the solid, shaded by how
  much *material* it crossed — so a hollow part reads as a bright rim round a dim
  middle. That's how a blind AI debugs geometry. The colour PNG is for you.
- **The AI is taught how to think.** Each `prompt` carries a reasoning ladder for
  that kind (a texture's material→palette→depth; a model's parts→sizes→pivots),
  the full grammar, a worked example, and an output contract — so weak models
  produce valid, sensible assets, not guesses.

## Standard formats and your engine, both

Every asset emits a universal format **and** an engine-native JSON:

| Kind | Universal | Engine-native |
|---|---|---|
| texture | `.png` albedo + `_material.png` | `.recipe.json` (re-paint in your atlas) |
| model | `.glb` (glTF 2.0) + `.obj` | `.boxmodel.json` (bones + cubes + pivots + anim) |
| prop | `.glb` + `.stl` + `.obj` + `.svg` | — |

The GLB is validated: correct header, in-bounds accessors, animations as glTF
channels. The box-model JSON is a flat list of bones with pivots and coloured
cubes, trivially loaded by a blocky-mob renderer.

## Verified

`selftest` builds 11 reference solids against known volumes, rejects 9 malformed
designs with the right diagnostic, tessellates 200 random outlines with exact
area conservation, paints a texture to a valid PNG, and exports a model to a GLB
whose every accessor is in-bounds with its animation intact. All green.

## Honest limits

- No 3D booleans; assembled prop parts are placed, not fused (overlaps are
  reported). No 3D fillets. Nothing organic — this is the blocky/prismatic/
  axisymmetric world, which is most of a voxel game.
- Texture generators are pattern-based; there's no freehand pixel editing.
- The preview renderer is flat-shaded software — fine for judging form and
  colour, not a beauty render. For that, open the GLB in Blender.

## Files

| | |
|---|---|
| `designbridge.py` | the whole engine, CLI and Studio — one file |
| `GEOMETRY_GUIDE.md` | how-to-think for the parametric prop track |
| `examples/*.tex.db` | textures (mossy cobble, gold ore) |
| `examples/*.model.db` | models (husk king boss, stalker mob, crate) |
| `examples/*.db` | props (mug, bracket, box, vase) |

## Where to take it next

1. **A vision critic** — render the GLB, show the PNG to a multimodal model, feed
   its critique back as text. This is what closes the aesthetic gap.
2. **More texture generators** — cellular stone, gradients along normals, tiling
   checks.
3. **Skeletal weights** for smooth-skinned models (box models already animate).
4. **An MCP server** wrapping `check`/`build`, for AIs that support tools.
5. **STRATA glue** — a tiny Java loader for `.boxmodel.json` and the texture
   recipe, so assets drop straight into the engine.
