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

---

## Round 2 fixes

**Your request is editable now.** It was captured once from the bottom bar,
cleared, then shown as read-only text — so there was no way back to it. The
`YOUR REQUEST` box is now a real textarea and the single source of truth: edit it
whenever you like, the prompt rebuilds behind you (450ms after you stop typing,
or `Ctrl/Cmd+Enter`, or `REBUILD PROMPT`). It's saved per asset, so switching
assets and back keeps your text. The bottom bar now **appends** a line instead of
replacing everything. `I` jumps to it.

**Invisible text fixed.** The Tailwind CDN injects its stylesheet *after* the
inline one, and its forms plugin sets `background-color:#fff` on every input at
the same specificity as a class — so it beat `.recessed-well` and you got white
text on a white field. All 14 fields now measure 244/255 contrast.

**Controls are predictable.** Drag was mutating the asset's own X and Y rotation,
which rolls the horizon and tilts everything diagonally with no way back to
level. It's a turntable now: the asset never moves, the camera orbits it. Pitch
clamps at ±85° so it can never flip, up is always +Y, and a vertical drag no
longer drifts the yaw. Left-drag orbits, right/middle/shift-drag pans, wheel
zooms at the target, `F` reframes. Camera presets snap to exact angles.

**The floor stopped cutting through the model.** designbridge revolves props
about **Z**, so a prop document is Z-up while a voxel model is Y-up — the viewer
assumed Y-up for everything, so props lay on their side with the grid slicing
through the middle. The mug's vertical span now reads 95mm (its real height)
instead of 80mm. Assets are seated on the floor, and the grid is a toggle (`G`).

**No more `<<< >>>`.** Models kept stripping the brackets or emitting an empty
`<<>>`. designbridge is untouched; the Studio rewrites its output contract to ask
for a plain fenced block, and rewrites the worked example to match. Extraction
accepts whatever actually arrives — tested against 10 mangling patterns including
your exact failure case:

| what the model sent | extracted? |
|---|---|
| fenced block (with or without a language tag) | yes |
| legacy `<<<DESIGN … >>>` | yes |
| **empty `<<>>`, source outside it** | yes |
| sentinel with no closing marker | yes |
| `BEGIN_DESIGN` / `END_DESIGN` | yes |
| bare source with prose above and below | yes |
| a fence nested inside sentinels | yes |

**Components are selectable.** `/api/mesh` returns per-bone/per-part groups, so
the `COMPONENTS` list shows the real structure with each part's dimensions. Click
one there or in the viewport and the rest dims, the scope chip appears, and the
prompt gains *"Change only the bone "left_arm". Leave every other bone exactly as
it is."* — so an edit stops rewriting the whole asset.

**Modes explain themselves.** Each of EDIT / NEW TAKE / FIX now shows what it
does plus a worked example for the current track, with a `USE` button to drop the
example straight into your request.

Also fixed along the way: a temporal-dead-zone crash (`showFloor` read before
initialisation) that broke the whole app on load, and a cascade where any 3D
fault made the Studio wrongly report "NO SERVER" and blank the asset list.

New shortcuts: `I` edit request · `F` reframe · `G` floor grid.

See `ROADMAP.md` for the plan on richer shapes and the variation dial.

---

## Round 3 — shape controls

You were right: the complexity dial was only planned, not built. It exists now,
as **SHAPE CONTROLS** at the top of the right-hand panel.

| Control | What it does | Enforced? |
|---|---|---|
| **Complexity 1–5** | A box/part/layer budget. 1 = 6 boxes, 2 per bone. 5 = 80 boxes, 14 per bone. | Yes — counted after compile |
| **Proportions** | `CUBIC` keeps every box within 2:1 long-to-short · `MIXED` allows 4:1 · `FREE` uncapped. This is the "how far from plain cubes" dial. | Yes — measured per box |
| **Symmetry** | none · mirror-X · mirror-Z · radial | Yes — mirrored counterparts checked |
| **Detail** | low / medium / high | No — guidance only, and the panel says so |
| **ENFORCE** | Turn the checking off and keep the prompt guidance | — |

These are not hints. The numbers go into the prompt as explicit limits, and after
each compile the Studio re-measures the result and reports anything over budget:

```
STUDIO STYLE CHECK - complexity 1/5, cubic proportions, symmetry mirror-x
  3 style issue(s) - these are budget warnings, not compiler errors:
  [WARN] bone 'b' has 3 boxes, complexity 1 allows 2 per bone
         FIX: simplify 'b' to 2 box(es) or raise complexity
  [WARN] box in 'b' is 14.0x long-to-short, 'cubic' allows 2x
         FIX: even out that box's proportions, or set proportions to elongated
  [WARN] 3 box(es) have no mirror-x counterpart
         FIX: mirror them across X, or set symmetry to none
```

Style issues are reported separately from the compiler's own diagnostics and
never flip a PASS to FAIL — they are your budget, not a geometry error.
designbridge is still untouched; the enforcement lives in the Studio.
Settings are saved per asset.

**Mode examples were there but invisible** — a 9px italic line in a cramped row.
Each mode now shows its name, what happens to your current design
(*kept and modified* / *discarded entirely* / *kept, errors repaired*), what you
get back, and a full example request with a **USE THIS** button.

---

## Round 4 — system briefing

The prompt used to drop the AI straight into a grammar reference with no idea
what the system was. There's now a `BRIEF` toggle (on by default, next to `RPT`
and `SPEC`) that puts a system briefing at the very top of every prompt:

- **what the pipeline is** — you write a document, designbridge compiles it into
  real geometry, a text report comes back
- **why it writes intent, not geometry** — it can't see, the compiler can, so a
  `box from=… to=…` line cannot produce a broken mesh because it isn't a mesh.
  Explicitly forbids vertices, triangle lists, matrices, code, JSON, SVG.
- **how it finds out what it made** — the report is its eyes: PASS/FAIL, measured
  bounds, watertightness, per-error line number + root cause + fix, ASCII views
- **the three kinds of asset** — model / prop / texture and what each is for
- **what comes out** — the actual file list per track (`.glb` with animations,
  `.boxmodel.json`, `.recipe.json`, `.stl`, `_material.png` and its channel
  packing) and that these load in Blender, three.js, Godot, Unity and custom
  Java/OpenGL engines, so the numbers it picks are real scene dimensions
- **how to work** — one change per round, keep what passes, always send the
  complete document, stay inside the shape budget

It costs about 3.3k characters. For a fresh chat, leave it on. For a long-running
chat, press **COPY SYSTEM BRIEFING ONLY**, paste it once, then untick `BRIEF` and
every later prompt is ~3.3k shorter. Also available as `CONTEXT_PROMPT.md` and
from `GET /api/context`.

---

## Round 5 — style lock

**STYLE LOCK** sits at the top of the right panel: standing art direction that
is sent with *every* prompt, for *every* asset.

```
Palette: #2d323f base, #1e222b shadow, #ef4444 accent only.
All mobs 16 wide max, flat-topped shoulders, no curves.
Eyes always emissive and 2px tall.
```

It lands in the prompt as its own section, ahead of the round's request:

```
STANDING ART DIRECTION - applies to every asset in this project
  These rules are locked. They are not part of this round's request and
  they do not expire: obey them on this asset and on every future one,
  even when the request below says nothing about them. If a rule here
  conflicts with the request below, follow this section and say so in
  your plan line.
```

Named **presets** let you keep one lock per asset family — "STRATA mobs",
"STRATA machines" — and switch between them. The lock is shared across all
assets (that's the point: cross-asset consistency), saved server-side, and
survives reloads. `ON` turns it off without losing the text.

### Why it repeats every round instead of being said once

Sending it once is cheaper, and for the system **briefing** that's a fine
trade — which is why `BRIEF` exists. But style is the opposite case:

- **Bad syntax fails loudly.** The compiler catches it, names the line, and
  `FIX ERR` feeds the error back. If the AI forgets the grammar, you find out
  immediately and the loop self-corrects.
- **Wrong style fails silently.** Nothing in the report says "the palette is
  wrong" or "this mob doesn't match the other forty". It compiles, it passes,
  it's watertight — and it's off-model. You only notice much later, by eye,
  across a pile of finished assets.

Repeat what fails silently; economise on what fails loudly.

Two further reasons it has to be per-round: attention to an instruction decays
over a long chat, so "said once, forty messages ago" is weak by the time it
matters; and every Studio prompt is deliberately self-contained, so a round can
be pasted into a brand-new chat and still come back on-style. A lock that lived
only in chat history would break that.

It costs ~200–600 characters against a ~7.6k prompt — under 5%, for the one
constraint nothing downstream can check.

---

## Your existing workspace is safe — but it needs one command

The archive contains **no database**. The `.db` files inside `examples/` are
designbridge *source* files, not SQLite. Your data lives in `workspace/`, which
is not in the archive, so extracting over your folder replaces the code and
leaves your assets untouched.

The reason your prebuilt assets throw syntax errors is different, and real:
seeding is skipped on a non-empty database (`_seed_if_empty`), so a workspace
created by an earlier build still holds that build's **fabricated grammar** —
sources like `<<<DESIGN model= box=(16,32,6)` that the real compiler rejects,
stamped with a hardcoded `PASS`.

Three commands, none of which delete your work:

```bash
# See what disagrees with the compiler. Changes nothing.
python3 studio_server.py --audit

# Rewrite every revision's verdict to the truth. Source, notes,
# branches and prompt history are all left alone.
python3 studio_server.py --repair

# Also swap the three built-in demo assets for current ones.
# Skips any demo asset you have added revisions to.
python3 studio_server.py --replace-examples --repair
```

These do their work and exit rather than starting a server. On a normal start,
the Studio now counts mismatched revisions and prints the exact command to fix
them, so this can't sit unnoticed again.

A revision that genuinely can't compile is marked FAIL with real diagnostics.
That's the honest state — it was always broken, it was just labelled PASS. Use
`FIX ERR` mode on it, or delete it.

---

## Round 6 — folders, optional names, AI-drafted style

**Examples moved to the type chooser.** I'd put them on the EDIT/NEW TAKE/FIX
switcher; you meant the NEW ASSET dialog, where you pick between the three
types. Each card now shows what the type is, what to use it for, a concrete
example brief, and which files come out:

| | Use it for | You get |
|---|---|---|
| **VOXEL MODEL** | Mobs, bosses, machines, anything that moves | `.glb` `.obj` `.boxmodel.json` |
| **CAD PROP** | Vessels, tools, furniture — turned or extruded | `.glb` `.obj` `.stl` `.svg` |
| **PBR TEXTURE** | Block faces, surfaces, tiles | `.png` `_material.png` `.recipe.json` |

**Folders.** Assets group under a folder, so one project (`STRATA / mobs`,
`STRATA / bosses`) can hold many assets of mixed types. The cartridge list is
grouped with a header per folder and an `Unfiled` group. Each card gets a
move-to-folder button, and the field autocompletes from folders you already
use. Added as a nullable column, so existing workspaces keep working.

**The name is optional now.** Leave it blank and the asset is `untitled_model_…`
until it first compiles — then it adopts the name out of the document the AI
wrote (`model "beetle_drone"` → `beetle_drone`), with a suffix if that name is
taken. Only ever applied while the name is still a placeholder, so it never
overwrites one you chose.

**Draft the style lock with an AI.** `DRAFT WITH AI` copies a prompt that turns
a loose description into a structured spec; `APPLY JSON` ingests the reply:

- **Known keys drive the controls** — `complexity`, `symmetry`, `proportions`,
  `detail` move the Shape Controls sliders directly.
- **`palette` and `rules`** become the lock text.
- **Anything else is kept, not dropped.** Keys the schema never anticipated
  (`forbidden`, `silhouette_priority`) are preserved verbatim under
  *"Additional direction"*, because a model that volunteered a field usually had
  a reason. Bad values warn rather than failing — `"detail": "banana"` leaves
  detail unchanged and tells you why.

Extraction is as tolerant as the DSL path: fenced block, bare object, or JSON
sitting in prose all work.

---

## Round 7 — prompt verifier, next-step card

**The loop no longer dead-ends.** After a compile the request text was
unchanged, so the next prompt was identical to the last one and nothing
happened until you went back and edited the textarea. A **what next** card now
appears under the compile button with the actual next move:

- pass → `NEW VERSION` (clears the request, sets EDIT, focuses the box),
  `ANOTHER TAKE` (keeps the brief, discards the geometry), `BRANCH FROM HERE`,
  `EXPORT IT`
- fail → `FIX THE ERRORS` (switches to FIX, attaches the diagnostics),
  `SEE THE REPORT`

**Prompt verifier.** A collapsible manifest above the prompt preview showing
every section that is actually present, measured against the finished text
rather than asserted — so a section that failed to assemble reads as missing
instead of claiming to be there:

```
verified  complete · 12 sections · 12,834 chars
  ✓ System briefing          4,156   what the pipeline is, what files come out
  ✓ Grammar                    510   every statement the compiler accepts
  ✓ Worked example             532   one complete document that compiles
  ✓ Shape budget               423   complexity, proportions, symmetry
  ✓ Current source           1,017   the document being edited
  ✓ Compiler report          4,141   last round's errors and measurements
  ✓ Output contract            545   how to format the reply
  ⊘ Off or empty: Standing art direction
    The model track has no: Values & tokens, Operations, Assembly rules
```

Absent sections are split three ways deliberately: **missing** (required and
genuinely gone — the header turns red), **off or empty** (you switched it off or
left it blank), and **not in this track** (the prop grammar carries far more
guidance than the texture one). Conflating those would make a normal prompt look
broken.

Composition — using a texture inside a model, and reuse generally — is designed
in full in `ROADMAP.md` rather than built: it needs a dependency table, staleness
handling and cycle rejection, and doing it badly would be worse than not having
it.

## Known limits

- The viewport is flat-shaded and does not play the keyframe animations yet — the
  scrubber moves but the bones do not. The animation data is in the `.glb`, so
  Blender or three.js will play it; the in-Studio preview does not.
- No 3D booleans, no fillets, no organic shapes — that is designbridge's
  prismatic/voxel domain, unchanged.
- `chamfer`/bevel control is not built — it needs a change inside designbridge.
- Detail low/medium/high is prompt guidance only; it cannot be measured.
- The LLM relay posts your API key from the server to the endpoint you configure.
  It is stored in the local SQLite file (and redacted in `settings.json`).

---

## Round 8 — the workbench, split into steps

The right-hand panel used to hold eleven stacked blocks in one scroll: shape
controls, style lock, mode switcher, mode explainer, request box, components,
prompt card, verifier, source well, diagnostics, cassette. Everything was
visible at once, which meant nothing was.

It is now two tabs:

**BUILD** — the round, as three numbered steps.

1. **Say what you want** — mode, the explainer for that mode, the request box.
2. **Hand it to your AI** — the prompt, the verifier, the copy button.
3. **Paste the reply back** — the cassette and COMPILE.

Component targeting moved behind a disclosure (*Aim at one part*), because it
is optional and was competing with the request box for attention. The source
well moved to SOURCE and the diagnostics strip to REPORT — the tabs that were
already about those things.

**SETUP** — the things you set once and forget: the style lock and the shape
controls. They ride along with every prompt, so they do not belong in the
middle of a round.

### A new version starts blank

`NEW VERSION` (top-right of BUILD, and on the what-next card after a compile)
now clears the canvas rather than leaving the last request in the box:

- the request, the paste box and the commit note are emptied
- the component scope is released
- the mode goes back to EDIT
- the panel scrolls to step 1 and the cursor lands in the request box
- the header says **"New version on top of v3 — blank canvas"**

The header is the part that was missing before. It tells you which revision you
are building on, and whether you have written anything yet, so a fresh round
looks different from a half-finished one.

### Empty, loading and error states

Every data-driven element now says what is going on instead of rendering blank:

| Element | Empty | Loading | Error |
|---|---|---|---|
| BUILD panel | "No asset open" + NEW ASSET | — | — |
| Components | "No parts to aim at yet — parts appear once a version compiles" | — | — |
| Prompt | "— TOK" | "BUILDING…" | "~N TOK · OFFLINE" |
| Verifier | "prompt not built yet" | — | "cannot verify — server unreachable", naming what the fallback is missing |

## Round 8b — the style lock says so

Two changes, both about the same problem: a lock is invisible, so a model
obeying one looks like a model that is stuck.

**The lock covers form, not just colour.** A palette is the easy half of a
style. The hard half is how dense the forms read, what the surface is made of,
what the silhouette has to do, and what the style never does. So:

- The standing art direction now ends with an **ALSO LOCKED** clause naming the
  Shape Control values in words — *complexity 4 of 5, high detail, cubic
  proportions, symmetry mirror-x* — and says they hold for every asset, not
  just this one. Without it a model reads those limits as this round's brief and
  drifts the moment the brief is silent.
- The style-spec generator now asks for `detail` as **both** a level and a
  sentence, plus `surface`, `silhouette`, `materials` and `forbid`. The level
  drives the slider; everything else becomes a locked rule line. `forbid`
  earns its place: drift shows up as banned things creeping back in.

**The lock is visible where you work.** An amber strip sits above the request
box whenever the lock is on. It lists the first rules, the rule count and the
form clause, and carries `EDIT` and `TURN OFF`. The inverse case gets a quieter
strip: *"style lock written but switched off"* — so an asset that drifts has a
visible cause too.

If your AI keeps returning the same design, that strip is the answer.
