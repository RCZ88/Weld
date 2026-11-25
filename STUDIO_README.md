# DesignBridge Studio — setup & what was fixed

Two files changed: `index.html` and `studio_server.py`.
**`designbridge.py` is untouched** (verified byte-identical, `selftest` still green).

---

## Run it

```bash
cd designbridge_studio
python3 studio_server.py
```

Then open **http://127.0.0.1:8765**.

Options:

```bash
python3 studio_server.py --port 9000 --workspace ~/strata_assets
python3 studio_server.py --ui /path/to/index.html     # if you move the UI
```

All four files should sit in the same folder:

```
designbridge_studio/
├── designbridge.py     the compiler (untouched)
├── studio_server.py    the backend
├── index.html          the UI
└── workspace/          created on first run — SQLite + every revision on disk
```

### Fully offline (optional)

The UI pulls Tailwind and three.js from a CDN. To cut that dependency, drop the
two files into a `vendor/` folder and they are used instead automatically:

```
vendor/tailwind.js      from https://cdn.tailwindcss.com
vendor/three.min.js     three.js r128
```

Nothing else needs changing. If three.js is missing entirely the Studio still
runs — you lose the live 3D viewport, but the compiler, the report, the 2D
blueprints and the exports all keep working.

---

## The three things that were actually broken

### 1. The prompt had collapsed to ~10 lines

`studio_server.py` already had a `/api/prompt` endpoint that routed through
`designbridge.prompt_for()` — the real prompt compiler, with the grammar, the
reasoning ladder, the failure catalogue and the output contract. **The UI never
called it.** It assembled its own short header in JavaScript instead.

That is why generated designs kept coming back in invented syntax: the model was
never shown the real language.

Now: `buildEngineeredPrompt()` calls the endpoint. Measured — **8,397 chars /
192 lines**, with the grammar block, the output contract and your instruction all
present. The local header survives only as a clearly-labelled offline fallback.

### 2. The real compiler was never running

```python
# before — the guard was backwards in effect
if "<<<DESIGN" not in dsl:
    kind, doc = db_core.compile_any(dsl)   # only for input WITHOUT sentinels
```

Every revision the UI produces *is* sentinel-wrapped, so this branch never fired.
Each commit fell through to a regex "compiler" that pattern-matched words like
`heavy|armor` and returned a hardcoded `"PASS: Compiled cleanly"` — a green tick
with nothing behind it. `bounds` and `struct` were copied from the parent commit,
so the diagnostics never changed.

`compile_any()` strips the sentinels itself (verified: 0 errors on wrapped
input), so the guard was never needed. Now every revision is really compiled and
the panel shows measured geometry:

| asset | bounds | struct |
|---|---|---|
| stalker_mob v2 | `12x32x8.5 px` | `6B / 8BX` |
| coffee_mug v1 | `93x80x95 mm` | `2P / 848T` |
| rusted_iron v1 | `16x16 px` | `6 layers` |

A broken design now fails honestly — real diagnostics, `PIPELINE BLOCKED`, and
exports refuse rather than handing you a meaningless file.

### 3. The viewport showed a fake robot

The 3D stage built a hardcoded humanoid out of regex-scraped numbers
(`torsoW`, `armLen`, `visorColor`), so it never showed your actual asset — and
`build_valid_glb()` / `build_obj_text()` / `build_stl_text()` each synthesised a
generic 8-vertex box, making every download meaningless.

Now there is a `/api/mesh/<asset>/<revision>` endpoint returning the real
triangles from `designbridge.colored_tris()`, and the viewport draws those. The
2D blueprints are real orthographic projections of the same triangles, not drawn
rectangles. Exports go through `designbridge.export_any()` — real `.glb`
(verified `glTF` magic), `.obj`, `.stl`, `.svg`, texture `.png`, and the
engine-native `.boxmodel.json` / `.recipe.json` for STRATA.

Also fixed: the three seeded assets shipped with **invented grammar**
(`<<<DESIGN model= box=(16,32,6)`, `cylinder outer r=44`, `layer base
noise=perlin(0.18)`) — none of which designbridge understands. They now use real
syntax and are compiled on first boot, so the starter projects teach the language
that actually exists.

---

## The design

`index.html` now matches `code.html`'s language exactly — checked element by
element, 0 of 37 signature items missing:

- flat frosted glass `rgba(18,20,26,0.72)`, `blur(28px) saturate(140%)`, double inset specular rim
- dual-frequency CAD workbench grid (50px major at 7%, 10px minor at 2%) on the body
- radial studio vignette, crosshair axes with coordinate labels, corner calibration marks
- **strict neutral palette**: white primary keys; emerald only for verified/watertight; amber and crimson only for warnings and errors. Saturated-accent count went **108 amber / 61 sky → 8 amber / 0 sky** (Stitch's own file has 6 amber)
- neutral `glass-card-active` — selection reads as elevation, not hue
- `remove` (—) hardware minimize buttons on every pod, tactile hover lift
- restored from the Stitch design and missing from the previous build: the
  HARDWARE BUFFER panel with its bus beam and VRAM segment meter, the
  OPERATOR INSTRUCTION bubble, the line-numbered syntax-highlighted DSL well,
  SCENE CARTRIDGES / RELAY REVISIONS decks, CAD SCALE and TOLERANCE callouts,
  and the `LOD // SUB_D` telemetry capsule

Everything the previous build added is kept: resizable spring-physics pods,
branch map DAG, command palette (⌘K), diff tab, prompt history, project
rename/duplicate/delete, settings with the LLM relay, and the export dock.

---

## Using it

1. Pick an asset in the left pod, or **NEW_ASSET**.
2. Type what you want in the bar at the bottom right, press **GEN**.
3. **COPY** the prompt into any AI — or configure the relay in Settings and press **RUN AI**.
4. Paste the reply into the Step 2 cassette, press **COMPILE & REGEN VOXEL**.
5. Pass → the viewport updates and the revision is saved. Fail → the REPORT tab
   has the real diagnostics; **FIX ERR** mode attaches them to the next prompt.
6. Export with the dock keys, or **ZIP** for the whole bundle.

Shortcuts: `⌘K` palette · `N` new · `B` branch map · `,` settings · `[` `]` pods ·
`W` wireframe · `R` spin · `Space` play · `1`–`4` camera.

---

## Known limits

- The viewport is flat-shaded and does not play the keyframe animations yet — the
  scrubber moves but the bones do not. The animation data is in the `.glb`, so
  Blender or three.js will play it; the in-Studio preview does not.
- No 3D booleans, no fillets, no organic shapes — that is designbridge's
  prismatic/voxel domain, unchanged.
- The LLM relay posts your API key from the server to the endpoint you configure.
  It is stored in the local SQLite file (and redacted in `settings.json`).
