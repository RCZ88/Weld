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

---

## Round 9 — one project, many kinds; and a library of parts

Four things were in the way of using the Studio on a real set of assets rather
than one asset at a time. All four are now built.

### The track is no longer a life sentence

A project used to be welded to the track it was created on. A prop that turned
out to need bones had to be abandoned and rebuilt as a new asset, and a folder
could not hold a model, a prop and a texture that belong to the same creature.

**SETUP → TRACK** now switches an open asset between MODEL, PROP, TEXTURE and
**AUTO**, at any time, and the new-asset modal offers AUTO as a fourth choice.

AUTO is the interesting one. The grammar names its own track on the first real
line of the document — `model stalker {`, `texture rust size=16`,
`design lamp units=mm` — so an AUTO asset never has to be told what it is: the
source already says. Each round reads the declaration off the head revision and
sends that grammar. Write a different kind of document and the next round
follows it. Nothing to switch, and the prompt says which way it resolved:

```
TRACK - AUTO, resolved to PROP from the current source
  This asset is not pinned to one track. The grammar above is the prop grammar
  because that is what the current source declares. Write a prop document and
  nothing else; do not mix two grammars in one document.
```

With no source yet — a brand-new AUTO asset — the brief decides instead, by
keyword, and the starter document is written in that grammar. The one trap worth
recording: `prop` documents declare themselves with **`design`**, not `prop`, so
the first keyword of a document maps to a track rather than being one.

### A reuse library, so a part is designed once

**SETUP → REUSE LIBRARY.** Click a part in the viewport, click
`SAVE "<part>"`, and its source is lifted out of the document — braces balanced,
indentation stripped — and kept. `WHOLE` saves the entire document as one block.

Saved blocks of the current track ride along with every prompt as a new section:

```
REUSABLE BLOCKS - already built, verified, and yours to copy
  Paste a block's lines into the document when the brief calls for that part,
  then adjust its `at` position and sizes to fit. Do not redesign a part that is
  already here, and keep its proportions unless the instruction says otherwise.
```

This is the cheap half of composition: reuse without an import graph. Blocks of
another track are kept but not sent, and the card says so (`3 in this prompt ·
1 other track`), because a bone hierarchy pasted into a texture is noise. The ON
switch stops sending them without deleting anything.

### A batch is a set of siblings

The `＋` button in the asset sidebar is now joined by a **batch** button. One
line per asset; `name: description` names it, a bare line is just a brief and the
asset adopts its name from the first document that compiles. Bullets and dashes
are stripped, so a pasted list works as pasted. Every asset in the batch lands
in the same folder on the same track — usually AUTO, which is what makes a mixed
batch possible:

```
crate_small: a banded wooden crate          -> prop
floor_grate: tileable rusted steel grate    -> texture
sentry_drone: hovering drone with bone arms -> model
```

Capped at 40 a batch, each on its own `main` branch, each with a compiling
starter document.

### A selection exports as a set

The **checklist** button turns the sidebar into a selection, with a bar that
exports every chosen asset into one zip. `NATIVE` is the format that matters: a
texture has no `.obj` and a model has no `.png`, so NATIVE gives every asset the
format its own kind exports, and one click works on a folder holding all three
tracks. Anything whose head revision does not compile is skipped and **named in
`MANIFEST.txt`** rather than silently dropped.

### An asset list that shows the assets

Each card now carries the face of its asset — the thumbnail from its last good
revision, or, for a texture, the generated PNG itself — instead of a track icon
repeated nine times. Hovering a card opens a **live preview pane**: the real
compiled geometry, in its own small renderer, turning slowly. A texture shows its
PNG; a revision that does not compile says so rather than showing an empty box.
The pane runs its own scene so the two viewports never fight over one scene
graph, and its mesh fetches are cached per revision.

| State | What the pane shows |
|---|---|
| Geometry present | the mesh, orbiting, with edges |
| Texture track | the generated PNG, fitted |
| Head does not compile | `DOES NOT COMPILE` |
| No WebGL | `NO WEBGL` — the pane degrades, the list still works |
| Mesh fetch failed | `PREVIEW UNAVAILABLE` |

### New endpoints

| Route | What it does |
|---|---|
| `POST /api/projects/track` | switch an asset's track, AUTO included |
| `POST /api/projects/bulk` | create a batch from a list of descriptions |
| `POST /api/blocks` | save a reuse block |
| `POST /api/blocks/delete` | forget one |
| `GET /api/export/bulk.zip?ids=&fmt=` | one zip for a selection; `fmt=native` per asset |

`designbridge.py` is untouched, as always. Every one of these lives in
`studio_server.py` and `index.html`.

---

## Round 10 — the selection finishes, and the answer comes back whole

Two things, both about the same problem: the Studio was built for one asset at
a time, and nobody works on one asset at a time.

### INTAKE — paste the whole answer

The prompt leaves in one copy. The answer comes back carrying six props, or
three takes on one mob, and until now each document had to be pasted
separately — the slowest part of the loop by a wide margin.

The **intake** button in the asset sidebar (and `MANY` beside the response
cassette, which carries whatever is already pasted there) takes the entire
reply, prose and all, splits it into the documents it holds, compiles every one
for real, and files each.

Splitting is tried three ways, in order: fenced blocks first, because that is
what the output contract asks for; then the legacy `<<<DESIGN>>>` sentinels;
then, failing both, every top-level `model` / `design` / `texture` line starts a
new document, so a bare run of documents still separates correctly.

Routing is the interesting part:

| Mode | What happens |
|---|---|
| `AUTO` | a document whose name matches an existing asset becomes its **next version**; anything else becomes a **new asset** |
| `ALL VERSIONS` | every document becomes a successive version of the open asset, in the order pasted — three takes on one thing, ready to diff |
| `ALL NEW` | every document becomes its own asset, even when the name already exists |

A document names itself on its first line, and that is the only name that
counts — which is what makes AUTO work without asking anything.

The modal counts before it commits (`3 documents · 1 known asset · 2 new`) and
the count follows the mode, so the plan is visible while it is still editable.
Afterwards every document gets a line in a receipt: what it was, where it went,
`v6 of guard_drone` or `new asset · v1`, and whether it compiled. **A document
that fails is still filed, with its diagnostics** — a failed revision is
information, not a thing to drop. Capped at 24 a paste.

### The selection does the other four things

A selection that could only be exported was half a feature: the reason to
select eleven assets is usually to move, retrack or delete all eleven.

`ALL` · `NONE` · **EXPORT** · **MOVE** · **TRACK** · **DELETE**

MOVE reuses the folder picker, which now says `2 selected assets` instead of one
name. TRACK is a dropdown that applies on change, AUTO included. DELETE names
what it is about to destroy and refuses to empty the workspace:

> That would delete every asset in the workspace (13). Leave at least one behind.

Every asset is handled independently, so one refusal does not abandon the rest,
and anything that failed is named in the toast rather than swallowed.

### New endpoints

| Route | What it does |
|---|---|
| `POST /api/intake` | split, compile and file many documents; returns a per-document receipt |
| `POST /api/projects/action` | one action — `delete`, `move`, `track` — over a selection |

`create_project` was split into `_insert_project` (returns the new id) and a thin
wrapper, so an intake can create an asset and keep working with it. It now also
accepts a `source`, which becomes v1 itself — an intake leaves no starter stub
sitting in front of the real first revision.

### Deliberately not built

**Animation playback.** The timeline, scrubber and duration readout are UI only;
nothing poses the geometry. The compiler does emit real keyframes and the `.glb`
export carries them, so animation works *outside* the Studio. Dropped at the
operator's request as unnecessary — recorded here so nobody mistakes the
scrubber for a player.

---

## Round 11 — bulk rename, and the prompt that writes the mapping

Forty assets called `untitled_model_9f2a` is what a fast intake leaves behind,
and naming them is exactly the work a language model is good at. But a model can
only do it if it is handed the real ids and an exact output shape — a mapping
keyed on invented ids renames nothing. So the feature is two halves.

### Half one: the prompt

`RENAME` in the selection bar opens a two-step modal. Step 1 builds a prompt that
carries the actual inventory and the JSON contract, and nothing else:

```
RENAME 3 ASSET(S)
  An asset's name is a filename: lower snake_case, no spaces, no punctuation
  beyond underscores. It is what every export is called, so it should read as
  what the thing is, not as a sentence about it.

THE INVENTORY - these are the real ids. Copy them exactly.
  coffee_mug    name=coffee_mug    track=prop     folder=-   head=v1 2P / 848T
  rusted_iron   name=rusted_iron   track=texture  folder=-   head=v1 6 layers
  stalker_mob   name=stalker_mob   track=model    folder=-   head=v2 6B / 8BX
```

…then the naming guidance (say what the thing is; keep a family sorting
together; don't restate the track — `drone_model` says the same thing twice),
then the contract. An optional one-line hint rides along, so
`foundry-era STRATA props, keep the crane family together` shapes the answer.
`COPY PROMPT` for the clipboard, `SEE IT` to read it first.

### Half two: a reader that takes what the model actually sends

Models wrap JSON in prose and fences and pick their own container key, so the
reader is deliberately tolerant. It pulls the first JSON object or array out of
the text and accepts any of:

```json
{"assets":[{"id":"untitled_model_9f2a","name":"slag_crane"}]}
[{"id":"wall_tile","name":"soot_concrete_tile","folder":"STRATA / foundry"}]
{"stalker_mob":"gloom_stalker"}
```

`folder` is optional and moves the asset at the same time. The modal counts
locally before anything is applied (`3 renames parsed`, or `no JSON in that
text`), and **every entry stands or fails on its own**, with a per-entry
receipt — a mapping of forty that reports one total is not something anyone can
check:

```
✓ guard_drone        → foundry_valve_cluster
✓ rusted_iron        → soot_wall_panel · STRATA / foundry
✗ definitely_not_real → not in the selection
```

An id outside the current selection is refused rather than quietly applied,
which is what makes it safe to paste a mapping that covers more than you meant.

### Versions can be renamed now too

The modal's second scope renames **revisions** of the open asset — the thing
asked for several rounds ago and never built. A version's note is what you read
in the history and the branch map, so the prompt says it must describe what
changed, not what the asset is:

```json
{"versions":[
  {"tag":"v3","note":"wider shoulders, gold visor"},
  {"tag":"v4","note":"thinner legs","newTag":"v4-slim"}
]}
```

`newTag` is optional and only worth using when a label reads better than `vN`.
Tags stay unique inside an asset — a clash is refused by name
(`'v1' is already used in this asset`) rather than silently shadowing an
existing revision. `meta.json` on disk is rewritten so the tag and note on the
filesystem follow the rename, and exports named after the tag pick up the new
one.

### New endpoints

| Route | What it does |
|---|---|
| `POST /api/rename/prompt` | the instructing prompt, with the live inventory for the scope |
| `POST /api/rename/apply` | parse a tolerant mapping and apply it, per-entry |

`rename_commit()` is new on the storage layer: retag or re-note one revision,
with uniqueness enforced and the on-disk metadata kept in step.

---

## Round 12 — revisions become first-class, and the right mouse button works

The operator said something that reframed the whole tool: *making a version is
quicker than making an asset, so I've been making new models as versions.*

That is not a misuse. It is the faster habit, and the honest response is not to
make asset creation faster — it is to give revisions every operation an asset
already had, plus an exit for the ones that turn out to be their own thing.

### Right-click, everywhere

One context-menu component, two action sets so far, submenus that open in place
with a back row (no hover timing to fight).

**On an asset:** Open · New version · Rename… · Move to folder… · Duplicate ·
Set track ▸ · Export ▸ (head / every revision / whole repo, each by format) ·
Add to selection · Delete.

**On a revision:** Check out · New version from here · Fork a branch… ·
**Make it its own asset** · Rename… · Export ▸ · Add to selection ·
Select every revision · Select the latest only · Delete.

When several revisions are selected, the actions say so — `Make it its own
asset (3)` — and apply to all of them. The per-card hover buttons stay for
anyone who likes them; they were just never enough room.

Two bugs worth recording, both found in the browser rather than reasoned about:
a capture-phase `scroll` listener closed the menu the instant it opened, because
panels scroll themselves on focus; and the click that opened the menu also
closed it, so the close handler now ignores anything within 250 ms of opening.

### Selecting revisions

`ctrl`/`cmd`-click picks one. `shift`-click takes the whole run between it and
the last one picked. A plain click still just checks the revision out — the
thing it has always done — and clears the selection.

Selected revisions get a ring and a tick, and a bar appears above the rail:

`ALL` · `LATEST` · `✕` — then `PROMOTE` · `RENAME` · `EXPORT` · `DEL`

`ALL` and `LATEST` are the version-selection setting asked for; anything else is
custom by ctrl- and shift-clicking.

### Promote — a version becomes its own asset

`PROMOTE`, the context-menu entry, or **dragging the revision out of the rail
and dropping it on the asset list** (a drop zone appears while dragging, and the
asset list accepts the same drop, because that is the gesture people try first).

Each promoted revision keeps its document byte for byte and becomes **v1 of a
new asset**. The original is left alone — a promotion is a copy, not a move —
and the new asset inherits the parent's folder and track. A revision whose
document declares the parent's own name becomes `guard_drone_v4` rather than
`guard_drone_7f2a`, because the latter tells nobody anything.

### Renaming revisions, and exporting by version

The rename modal's `VERSIONS` scope now narrows to the selection: with three
revisions ticked, the AI is asked to name three, not forty. The modal says which
it is working on (`3 revisions of guard_drone — the selected ones`).

Export gained a version scope everywhere it appears — a dropdown beside the
format in the selection bar, and the same three choices in both context menus:

| Scope | What lands in the zip |
|---|---|
| `LATEST` | each asset's head revision — what goes into the game |
| `ALL VERSIONS` | every revision, each in its own folder — a handoff or a backup |
| `PICKED VERSIONS` | only the ticked revisions |

Choosing `PICKED VERSIONS` with nothing ticked says so rather than exporting the
wrong thing.

### Deleting revisions

A revision is not a leaf, so deleting one in the middle would orphan everything
after it. Children are **re-parented onto the deleted revision's parent**, any
branch or project head pointing at it falls back to the newest revision left on
that branch, a branch that loses its last revision is removed, and an asset
always keeps at least one revision. The confirmation says all of this before you
agree to it.

### New endpoints

| Route | What it does |
|---|---|
| `POST /api/commits/promote` | each named revision becomes v1 of its own asset |
| `POST /api/commits/delete` | delete revisions, re-parenting the history |
| `GET /api/export/bulk.zip?…&versions=head\|all\|picked&commits=…` | the version scope |

One fix worth noting: `_insert_project` makes each new asset active, so a
promotion used to dump you in the last asset it created. Promote now restores
the asset you were working in, and an intake in `ALL VERSIONS` mode stays on its
target.

---

## Round 13 — a tree, a draggable seam, one selection bar, and labels that stay editable

Five complaints, all of them about placement rather than features. Recorded in
the operator's own terms because each one names a real design mistake.

### "The split shouldn't be static"

The asset list was pinned at 190px whatever the screen or the number of assets,
which is wrong in both directions: twelve assets had to be scrolled four at a
time, and someone working inside one asset wanted that space for its revisions.

The seam between them is now a drag handle. It clamps so the revision rail keeps
a usable minimum, double-click resets it, and where you left it is remembered
per workspace.

### "Project, then folders, then models, then versions"

The sidebar grouped by one flat folder string. It is now a real tree, built from
the slash-separated path each asset already carried — so `STRATA/ashen_basin/mobs`
nests three deep with no schema change and existing data just works.

```
▾ STRATA                      12
  ▾ ashen_basin                9
    ▾ mobs                     4
      ▸ gloom_stalker    model · 6B / 8BX
      ▾ valkor_plating   prop  · 2P / 848T
          ● v3  slimmer legs, amber visor      HEAD
          ● v2  arms past knees
          ● v1  initial blockout
```

Folders default open — nothing a person filed should vanish on them — and assets
default closed, because their revisions are a detail you ask for. Only the
exceptions are stored, so the state survives a reload without growing.

A version row in the tree checks out on click, takes `ctrl`/`shift` for the same
selection the rail uses, and right-clicks to the same menu. It is a second view
of one feature, never a parallel one.

Right-clicking a folder gives: select everything inside · export everything
inside ▸ · new asset / batch / intake **here** (the modal opens pointed at that
folder) · rename folder… · move contents to… · collapse.

Renaming a folder rewrites a path prefix across every asset under it, because a
folder is only the path its assets carry. That also means two spellings of one
path would be two half-empty folders, so paths are now normalised on write —
`STRATA / foundry` and `STRATA/foundry` converge.

### "Most of the actions are the same for the top and the bottom"

They were, and the two bars had started to drift. There is now **one selection
bar component rendered twice**, once per section:

```
⌗ 3 OF 12 REVISIONS        ALL  LATEST  ✕
[NATIVE▾] [SELECTED▾]  [   EXPORT   ]  [⋯]
```

Identical shape, identical code path, different target.

### "You can't control the export of the versions"

That was the real bug behind the complaint: the revision bar borrowed the asset
bar's dropdowns, so exporting revisions meant changing the asset export first.
**Each bar now owns its own format and version scope**, remembered separately.
The version bar defaults to `SELECTED`; the asset bar to `LATEST`.

### "The UI should be clean and not so crowded"

Two cuts, both structural rather than cosmetic:

**A third row of buttons became one `⋯`.** MOVE, TRACK, RENAME and DELETE stopped
competing with EXPORT for attention and moved behind an overflow menu — the same
list the right-click menu offers, so there is one place to change them.

**Five unlabelled icons became one labelled control.** New asset, new batch and
intake all answer one question — *add something* — so they live together behind
`＋ NEW ▾`. Selection and panel-collapse stay as separate icons because they act
on what is already there. Three controls, each with a reason.

### "Intake was in the wrong place"

The sharpest note of the five. Intake sat next to the response cassette, which
said it belonged to *that reply for that version*. It does not: it creates assets
and versions across the whole workspace. The button is gone from there and lives
in `＋ NEW ▾` with the other ways assets arrive. It still carries whatever is
already pasted in the cassette, so nothing is pasted twice — the capability was
right, the placement claimed a scope it did not have.

### "Where's the comment feature?"

A revision's note was writable only in the moment it was created — the one moment
you know least about it. What a revision turned out to be is usually clear later.

**Double-click any note to edit it in place.** Enter saves, Escape restores.
Also in the revision right-click menu as `Relabel…`, and in the version bar's `⋯`.
The note field on new commits stays where it was.

### New endpoints

| Route | What it does |
|---|---|
| `POST /api/commits/label` | relabel one revision by id, at any time |
| `POST /api/projects/action` with `action:"refolder"` | rename a folder path across its whole subtree |

`normalize_folder()` is new and applied at every write, so one path has one
spelling.

---

## Round 14 — find (⌘F / ctrl+F)

The browser's own find searches what happens to be painted. In a tree with
collapsed folders, and with only one asset's revisions on screen, that is almost
nothing — so ctrl+F is taken over and searches the workspace instead.

Five filters, cycled with `Tab` or clicked:

| Filter | What it looks at |
|---|---|
| `ALL` | everything below, ranked together |
| `MODELS` | asset names and ids |
| `VERSIONS` | revision tags **and their labels** |
| `FOLDERS` | folder paths, at every level |
| `SOURCE` | inside every revision's document |

The filter matters because one word means different things in different places:
`stalker` is a model when you are looking for a model and a line of source when
you are looking for the line that mentions it.

`↑↓` moves, `⏎` opens, `esc` closes, and any text selected when you hit ctrl+F
starts in the box. Hits are ranked by prefix match first, since the thing you
typed the start of is almost always the thing you meant. `SOURCE` returns **one
hit per revision, at its first matching line** — a document that says `bone` on
forty lines is one result, not forty.

Opening a hit lands you on the thing itself: a model opens, a revision opens its
asset and checks that revision out, and a folder has every folder on the way
down expanded so the match is actually visible. A revision hit also expands its
asset in the tree, so the two views agree about where you are.

The magnifier beside `＋ NEW` is the same search — placed next to the tree it
searches, rather than in the top bar where it would be one more unexplained
icon.

---

## Round 15 — Tier 1 of the model-review plan

Two model reviews said the same thing: *I worked blind.* Round 15 is the six
hygiene items that followed the self-check kit.

### Precedence, stated once, with the substitutions pre-computed

Both reviews reported the same problem independently: the brief names colours
the lock forbids, so the model has to decide what to do and then confess. Every
prompt now carries:

```
PRECEDENCE - which rule wins when two disagree
  1. The STYLE LOCK beats everything. It is the project, not a suggestion.
  2. THE BRIEF beats the examples and your own taste.
  3. Where the brief names a colour the lock does not allow, use the nearest
     locked colour. The substitutions are worked out for you below.

  COLOUR SUBSTITUTIONS - the brief asked for these; use the right column
    #C87A45  ->  #C79A4B   (close)
    #32E6FF  ->  #1CE8FF   (very close)
```

Every hex in the brief and in the current source is matched against the locked
palette and mapped to its nearest legal neighbour. The judgment call is gone.

### Standing rules scoped by track

`symmetry none` on a texture does not mean nothing — it reads as *do not make a
symmetrical tile*. The locked-dimensions clause now names only the controls
that apply to the track, the ALSO LOCKED sentence says "layer budget" on a
texture and "form rules" on a model, and the DETAIL note is written in each
track's own terms ("readable surface at 16x16" rather than "clear silhouette").

### Several assets in one reply

Intake has split multi-document pastes for two rounds; the contract still said
"ONE fenced block". It now says the truth: one document per fence, as many
fences as you like, three fences means three assets — or three takes on one if
they share a name.

### A `deviations:` line

A model can now declare a substitution without breaking the four-line plan
rule: one line after the fence, saying what changed and why.

### Known behaviour, measured rather than guessed

A per-track block of the things models kept getting wrong, every one checked
against the compiler or measured by compiling a test document. The full write-up
is `GRAMMAR_NOTES.md`; the headline is that **`noise`, `veins`, `bevel`,
`outline` and `border` do not tile**, which is why textures looked muddy and
had harsh borders, while `brick`, `checker`, `stripes`, `scatter`, `gradient`
and `dither` do. Measured: a `noise` tile's wrap-around step is 47 against an
interior maximum of 28; `bevel` is 281 against 144.

### Three reference models

`examples/reference/` — a humanoid with thigh→shin→foot so a kneel actually
bends, a quadruped with four kneed legs and an opposed-diagonal walk, and a
machine with a slewing turret and a luffing boom. All three pass `dbcheck` with
zero failures and zero warnings, and the folder's README states the five rules
they follow.

### A real export bug, found and fixed without touching designbridge

glTF composes a child node's transform on top of its parent's, but the exporter
writes each bone's pivot verbatim as the node translation — so **every nested
rig exported to `.glb` had its children displaced by the parent's pivot**. A
two-bone test put the arm at y=22 instead of y=12.

`.boxmodel.json` is absolute throughout and was always correct, so STRATA
itself was unaffected; the damage was to the Blender and preview path.

`fix_glb_child_pivots()` walks the node tree on the way out and rewrites each
child's translation as `translation - parent_translation`. Vertex data is
untouched, because the exporter already writes verts relative to their own
bone's pivot. Every `.glb` the Studio hands out is now correct, and
`designbridge.py` is still byte-identical.

---

## Round 16 — the STRUCTURE track

A fourth track: buildings made of textured blocks. `structurebridge.py` is a
separate compiler sitting beside designbridge and importing it for the PNG
writer and the software renderer. `designbridge.py` is untouched.

### The Core 12, as geometry rather than a table

Every form is defined by its half-spaces, and the mesher derives triangles from
that definition — so a `slope_45` is a real six-vertex wedge and a
`slope_corner` is a real corner, not a cube standing in for one. The 24 proper
cube rotations are generated rather than written out, and each form declares how
many of them are meaningful (a cube has 1, a column 3, a slope 12, a corner 24).

An orientation outside a form's range is an error, and cladding on a face the
form cannot clad is an error — the two combinatorial holes the engine review
asked us to close, closed by the same table the engine reads.

### Volumes, not cells

The grammar had one hard requirement: a 24×12×24 ruin is 7,000 cells and no
model can write that. So it composes.

```
structure foundry_ruin size=(24,12,20) seed=7

material wall    soot_iron_plate      color=#3A3F45
material pillar  brushed_brass        color=#C79A4B

room (0,0,0) (23,9,19) walls=wall floor=floor ceiling=none
door  south at=(10,0) width=4 height=6
window east at=(3,3) width=2 every=5

colonnade (4,1,4) to=(20,1,4) material=pillar form=column every=4 height=8
stairs (2,1,17) to=(8,7,17) material=wall facing=east

collapse roof amount=0.55
rubble   amount=0.35 material=slag
damage   amount=0.5
```

Sixteen lines, 1,178 cells. `collapse`, `rubble` and `damage` are what make it
a ruin generator rather than a block editor: all three are seeded, so the same
document gives the same ruin every time and a new seed gives a sibling.

### What it reports

Status and cell count, the parse diagnostics, the checks, a histogram of forms
used, total mass in cell-equivalents, and **a top-down ASCII plan per floor** —
which is genuinely readable for architecture in a way it never is for a
creature. Plus a preview PNG and the four-angle contact sheet.

The standing check is provisional and says so: plain connectivity to the
ground, not the weighted BFS the engine will run. It still catches the thing
that matters — `collapse roof amount=0.55` left 114 cells floating, which would
have fallen the moment the ruin spawned.

### Output

`<name>.structure.json` carries the form table, the materials with the texture
each binds to, and every cell as the **engine's 64-bit packed record**:

```
bits  0..15 materialId   16..21 formId      22..26 orientation
bits 27..42 claddingMat  43..48 claddingMask  49..50 crackStage
```

Verified: 1,178 cells, every record inside 64 bits, unpacking back to the right
form and material.

### In the Studio

A fourth track card, a fifth button on the track switcher (`BUILD`), AUTO
detection from the `structure` keyword, intake routing, the viewport mesh with
**materials as the components** — click `wall` and every wall cell highlights —
and the structure exports in the revision zip.

### Two bugs caught while building it

**`#` is both the comment character and the hex prefix.** `color=#6E7378` was
being read as `color=` and every material came out colourless. A `#` now counts
as a comment only at the start of a line or after whitespace.

**The preview was unreadable.** STRATA's palette is soot and iron, which is
correct in the game and black in a 250px thumbnail, and three stages of damage
darkening took it the rest of the way. Damage now darkens by 6% a stage rather
than 18%, and the preview applies an exposure lift that keeps materials
distinguishable from one another — which is the only thing a preview is for.

## Round 17 — drag that actually drags, visible waits, JSON picking

**Drag and drop rewritten on pointer events.** The HTML5 version was wrong in a
way that read as right: `draggable="true"` was set, `dragstart` fired, and the
drop target's `dragover` even called `preventDefault()` — and the `drop` event
still never arrived. Chromium refuses a drop whose `dropEffect` disagrees with
`effectAllowed`, and the native drag also swallows the pointer stream it rides
on. The replacement is delegated from `document`, so it survives a re-render,
and it works the same under a finger as under a mouse. It carries assets,
folders and revisions, shows a ghost label of what you are holding, highlights
only legal targets, and still refuses a folder dropped into its own descendant.

**Assets take ctrl/shift-click.** Selecting assets used to require finding the
checklist button first, so multi-drag was impossible until you knew about a mode
you had not seen. Ctrl-click toggles, shift-click takes a range, and a plain
click sets the anchor the range runs from — the same gesture the revision rail
already had.

**Waiting is visible.** A top bar names what is running: `BUILDING STRUCTURE…`,
`ZIPPING 7 ASSETS…`, `PACKING REPOSITORY…`. Downloads go through `fetch` rather
than an `<a download>` click, so there is a real finish to report — and the
toast names the file and its size.

**Downloads are named.** `designbridge_selection_glb.zip` told you nothing and
the second one became `...(1).zip`. Now:
`STRATA-era2-foundry_12-assets_head_glb_20261128-0032.zip`.

**Pick by JSON.** Paste a list and get that set selected, or zipped and
downloaded. Entries match an asset's name or id case-insensitively, `*` is a
wildcard, `folder:PATH` takes a whole subtree and `track:structure` a whole
track. COPY JSON-PICK PROMPT hands the AI the shape *and your real asset names*,
so it cannot invent one. It sits with the set-once prompts, not beside the
per-asset prompt.

## Round 18 — one door to create, and animation that is no longer a label

**CREATE replaces three modals.** There were only ever two questions — *do I
have the AI's answer yet?* and *one or many?* — but they were spread across
START NEW ASSET (one, brief-first), BATCH (many) and INTAKE (paste output).
"I already have the output" was reachable only from an open asset's response
box, so turning five finished documents into five assets meant creating a
throwaway asset first. Both questions are now toggles on one modal behind the
`+` button, and all four combinations work. Paste source while DESCRIBE is
selected and it switches for you instead of creating one asset per line of
code. INTAKE keeps only its distinct job: adding versions to the open asset.

**The scrubber was a gimmick and now is not.** `designbridge.py` has always
compiled real keyframe animation and exported proper glTF channels — but the
viewport was never sent the keys, `animDuration` sat hardcoded at `0.5`, and
nothing read `animTime`. The payload now carries the clips; the per-bone groups
(which already had `parent` and `pivot`) are parented into a real rig, geometry
re-pivoted so a bone turns about its own joint and carries its children. The
clip name became a dropdown, the duration is the clip's own, and dragging the
scrubber poses the rig. Rotation is XYZ Euler degrees — the same convention
`_euler_quat` writes — so what you scrub is what the GLB ships.

**ANIMATE THIS REVISION.** Asking for geometry and clips in one answer makes the
model re-emit every box to add a wave: expensive, and it routinely damages a
mesh that was already approved. This asks for `animation` blocks *only*, handing
the AI the real bone names, parents and pivots, and merges what comes back into
the revision you already have. A clip name sent again replaces that clip; the
ones you leave out are kept. Tolerant of fenced blocks and chatter around them.

**Clip lists come from the brief.** A description ending `Animations: idle, run,
lunge_attack, hit, death` already said what it wants, so the clip field is
seeded from it — backticks, bullets and the trailing full stop included. The
line is matched mid-paragraph, which is where models actually put it.

**`window.__studio`** is a small read-only view of the running viewport — clips,
duration, the bone tree with live rotations, and `seek(t)`. For debugging a rig
from the browser console.

## Round 19 — setup in groups, region palettes, clips in the prompt

**The setup panel was a wall and is now four lines.** Eight unrelated cards sat
in one flat scroll — the AI briefing directly above the colour palette directly
above the block allow-list — so finding anything meant reading all of it. The
cards were fine; what was missing was any statement of what each one governs.
They are now collected by exactly that, and collapse:

| group | governs | cards |
|---|---|---|
| THE AI | set once per chat | SET UP THE AI |
| ART DIRECTION | the whole project | REGION PALETTES, STYLE LOCK, STYLE SPEC, SHAPE CONTROLS |
| THIS ASSET | switchable per asset | TRACK, BLOCKS ALLOWED |
| LIBRARY & BULK | reusable parts, picking by name | REUSE LIBRARY, PICK BY JSON |

Grouping moves the existing cards at runtime rather than rewriting the markup,
so every control keeps its id, its listeners and its render function. A group
whose cards are all hidden for the current track hides itself. Open/closed is
remembered.

**REGION PALETTES.** A project is not one palette — the briefs name regions
(Overgrown Shelf, Ashen Foundry Basin), each with its own colours off the same
locked style. A region is a named style text; the active one is layered onto the
Style Lock, so it overrides without restating the whole style. Regions are
workspace-wide, like the lock they branch off.

The point of it: a brief that says ``oxidized_copper`` (`#4E7D70`) is naming a
palette entry, but the grammar only accepts hex, so the model had to carry the
mapping in prose and drifted a shade at a time. Named colours now reach the
prompt as an explicit table:

```
THE PALETTE - these names are how the brief refers to each colour.
Write the HEX in `color=`; the grammar does not accept the names.
  shadow           #22252A
  oxidized_copper  #4E7D70
Use no colour outside this list.
```

The name is read as the identifier immediately before the hex, with any
punctuation between, so `` `shadow` (`#22252A`) ``, `shadow: #22252A` and
`shadow = #22252A` all parse — and a bare list of hexes yields no names rather
than nonsense ones. All twelve entries from the mob briefs were verified.

**ANIM, beside the other prompt options.** Asks for clips in the same answer as
the geometry, for a new asset that has no approved mesh yet. Clip names come
from the brief; keys and clips are capped by complexity (level 1: 2 clips of 3
keys; level 3: 5 of 5) because asking for a key per frame is the commonest
animation mistake. Only the model track gets it. For a mesh already approved,
ANIMATE THIS REVISION remains the cheaper path — it does not re-send the boxes.

**askText takes multiline.** A region body is many lines and a name is one; same
dialog either way, so there is still only one way to be asked for text.

## Round 20 — one PROMPTS panel, and a guide for writing briefs

**The prompts were in five places.** The briefing and self-check in SETUP, PICK
BY JSON in another SETUP group, BULK RENAME inside the rename modal, the asset
prompt in BUILD step 2, and the clips prompt inside the animate modal. "Where is
the prompt?" was the correct reaction. There is now a **PROMPTS** tab listing all
seven in the order you use them, each marked SET ONCE or PER ASSET, each with
COPY and READ (expands in place, so you can check a prompt without pasting it
somewhere to look at it). The two that depend on what is open — the asset prompt
and the clips prompt — are listed with TAKE ME THERE rather than copied blind,
because a clips prompt has to know the rig.

**COPY BOTH SETUP PROMPTS** puts the briefing and the brief guide on the
clipboard as one paste, in order, for the top of a new AI chat.

**HOW TO WRITE BRIEFS is new** — the guide that was missing. Silhouette first,
one scale number, masses and the joints they turn about, materials by palette
name (never hex in a brief), one readable detail, facing, and the `Animations:`
line last. Plus what to leave out (lore, counts you don't mean, two assets in one
brief), the shape budget to write against, the one-per-line batch form, and the
note that briefs are unnecessary if the documents already exist. Everything in it
is behaviour the Studio actually parses, so it stays in step with the app.

**Bug found while writing it:** the briefing still said "THE THREE KINDS OF
ASSET" and never mentioned `structure`, months after the building track shipped —
so any AI set up with it had no idea the track existed, and was never told that
a prop document starts with `design` rather than `prop`. Both fixed.

## Round 21 — the prompts now have a way back

**A prompt you send out has an answer that comes back, and half of them had
nowhere to put it.** PROMPTS listed every prompt and let you copy it, then
dead-ended: you copied PICK BY JSON, got JSON from the AI, and the box that
accepts it was a button on a card inside the LIBRARY & BULK group in SETUP —
collapsed by default. There was no way to get from the prompt to its own reply.

Every prompt with a return path now carries a green paste button beside COPY,
named for what it takes:

| prompt | → | takes you to |
|---|---|---|
| HOW TO WRITE BRIEFS | PASTE THE BRIEFS | CREATE, in describe + batch |
| THE ASSET PROMPT | PASTE THE DOCUMENT | BUILD, focused on the response box |
| ANIMATE THIS REVISION | PASTE THE CLIPS | the animate modal |
| PICK BY JSON | PASTE THE JSON | the JSON pick box |
| BULK RENAME JSON | PASTE THE JSON | the rename box |

THE BRIEFING and SELF-CHECK SETUP have no button, because nothing comes back
from them.

LIBRARY & BULK also opens by default now, so the card is not hidden either way.
An existing install keeps whatever you last left open — the setting is yours,
not a default — so the PROMPTS door is the reliable one.

Also removed a wasted request: THE BRIEFING was fetching the brief guide and
throwing the result away before fetching the briefing.

## Round 22 — lighting

**Default is unchanged on purpose.** Studio lighting shows an asset as clearly
as possible in its own colours, with no light physics. That is the right mode
for designing a shape, and it stays the default.

**LIT mode** (toolbar, or `L`) answers a different question — will this build be
lit in the game — and that has a numeric answer a renderer alone cannot give.
So it does both: real GPU point lights at the emitters for the look, and the
compiler's propagated 0–15 grid written into vertex colours for the truth. A
cell at level 0 renders black however close a point light happens to be, so what
you see is what the engine will compute. Turning it off restores the flat
colours exactly.

**The light model is the game's, not the renderer's.** One cell is one block is
one metre is 16 voxels. Light runs 0–15 and loses exactly one level per block —
no inverse-square, no colour bleed — so `light=14` means "lights 14 blocks of
open air". Occlusion needs no new table: a form already declares its fluid mode
and its mass in the Core 12 contract, so a grate passes light, a quarter plate
passes light, and anything half-filled or more stops it. Light does not bend
around corners, which is why an interior needs an emitter inside it.

    material lamp glowstone color=#FFD9A0 light=14

`light=` is new on `material`, defaults to 0, and is range-checked. It is packed
into **bits 51–54** of the cell record — free bits above `crackStage`, 4 bits for
the same 0–15 scale — so the engine reads a cell's emission with no side table
and the locked Core 12 layout in bits 0–50 is untouched.

**"Is it lit enough" is now a measurement.** A standable cell — empty, solid
below, headroom above — at or below level 7 counts as dark, and both the compiler
report and the LIT HUD state it:

    1 emitter(s), brightest level 14
    484 cell(s) receive light
    40 of 121 standable cells at or below level 7 (33% dark)
    darkest standable cell is level 4

**LIGHTING & UNITS** is a new prompt in the shelf: the real measurements, the
0–15 scale, what blocks light, and how to choose a brightness from the distance
you want covered (with a table from `light=15` down to `light=4`). It also says
plainly what not to write — no lightmaps, no lumens, no falloff curves.

### Round 22 addendum — you can see what is inside a folder

**Nesting was a 10px margin and nothing else**, so an asset inside a folder
looked identical to one outside it. Depth is now structural: the children of an
open folder live in a container with a guide rail down its left edge, each level
adding 21px, and a nested card draws a short elbow to the rail it belongs to. A
root asset sits at x=1; one three folders deep sits at x=64, with a rail per
level. The indent lives in the page's own stylesheet rather than in utility
classes, because the whole point of the change is that it must be visible.

**Finding something now shows you where it is.** `revealAsset` opens every
collapsed folder above an asset, scrolls it into view if it was off screen, and
flashes its outline. Opening an asset reveals it; a search hit always flashes,
even when it was already the open asset, because being shown the location was
the point of searching. Verified against an asset three levels down with its
grandparent collapsed.

## Round 23 — a texture is not always a block

Three findings before any of this was built, all measured rather than assumed:

- **A texture could only ever be a block face.** The only place anything bound a
  texture was the structure track's `material <alias> <texture>`.
- **The model and prop grammars have no texture binding at all.** Model
  statements are `model / bone / box / animation / key`; prop statements are
  `design` plus profiles and sweeps. Both carry `color=#hex` and nothing else.
- **No UVs are exported.** No `TEXCOORD`, no `baseColorTexture`, no
  `KHR_materials` anywhere in the exporter. So even a bound texture had nothing
  to map with. The viewport sampled a texture image in exactly one place: the
  texture track's own flat preview plane.

So it is built in three layers, none of which touch `designbridge.py`.

**Layer 1 — THIS TEXTURE IS FOR.** A texture declares its target, and the target
decides what *correct* means. A block face must tile in both axes; a prop wrap
tiles around the axis (or along the sweep) and is free in the other; a model
panel never repeats. This has teeth because of what `GRAMMAR_NOTES.md` measured:
`noise`, `veins`, `bevel`, `outline` and `border` do **not** wrap across a tile
edge, while `brick` does. So those layers are bugs on a block and exactly right
on a model panel — and the prompt now says which, per target.

**Layer 2 — MATERIAL SLOTS, keyed on colour.** This is the answer to "which part
of a model?". Every box and solid already carries `color=#hex`, and the region
palette already names it, so **the colour is the material slot**. A model has
many boxes but few distinct colours; a prop is one solid with one to three; a
structure already worked this way with its `material` aliases. Bind a texture to
a colour and every part made of that stuff is textured — no new grammar, no slot
per box. The prompt lists the slots and which already carry a texture.

**Layer 3 — seeing it, without touching the compiler.** There are no UVs, so the
viewport generates them: per triangle, the dominant axis picks the projection
plane and the other two world coordinates become u and v, one tile per block —
16 voxels on a model, one cell on a structure. Exact for axis-aligned voxel
faces, a reasonable projection on a swept solid.

A bone turned out to be the wrong unit for this: one bone can hold boxes of
several colours, and the payload's bone groups carry no colour at all. So
triangles are bucketed by their own vertex colour and the mesh gets one geometry
group and material per bucket. Unbinding restores the flat mesh exactly.

The binding ships as `materials.json` beside the geometry in every export, since
it can live neither in the document nor in a UV-less GLB.

## Round 24 — the building section gets its controls

The structure track had the data and almost none of the controls, and one of
them was a real bug rather than a missing feature.

**The texture name was being thrown away.** `material wall soot_iron_plate
color=#3A3F45` names a texture, the compiler parses it — and the mesh payload
dropped it. So the viewport could only ever show the colour, and a building with
every texture declared still rendered as flat paint with nothing saying so. The
payload now carries each material's texture, its light level and its cell count,
and the server resolves every declared texture against the workspace so the UI
can distinguish three states that used to look identical:

    wall   361  textured · soot_iron_plate
    floor  169  MISSING: foundry_grate          <- names a texture that does not exist
    lamp     1  colour only — no texture named

**BUILD panel**, on the viewport rather than buried in a settings tab:

- **Blocks used** — every form with its count, from the compiled build.
- **Materials** — colour swatch, cell count, texture state, and the light level
  of any emitter.
- **EXTERIOR / INTERIOR** plus a **CUT** slider. Cutting drops every triangle
  above the level rather than using a clip plane, so the interior reads as solid
  geometry from any angle instead of as a hollow shell. INTERIOR opens it at
  roughly head height.
- **LIGHT ON/OFF** and **LEVELS**, which paints the propagated 0–15 light level
  straight onto the geometry — dark blue at 0 through pale yellow at 15 — so
  "where is it dark" is something you look at rather than infer.

A structure's textures come from its own document, so they are applied from the
`material` lines with one tile per cell; the model/prop slot pass is skipped for
structures entirely, since it would strip them.

**Bug found while testing:** `loadSlotTexture` returned nothing on a cache hit,
so the second application of any texture set `material.map` to `undefined`. Every
re-render — a cut, a light toggle — silently stripped all textures. Returning the
cached texture fixes it.

## Round 25 — the model is asked what it wants to build with

The app used to pick the block list and simply hand it over, so the model had no
say in what it was given and no reason to think about it. That is how a building
ends up naming four textures that are not in the workspace — the one that was
rendering as flat paint in round 24.

**PLAN THE BLOCKS** is a round trip that happens before any geometry:

1. **The ask** names all twelve forms with the facts that decide whether you want
   one — valid orientations, support rule, how much of a cell it fills, and
   whether light passes — read out of the form table rather than from memory:

       cube           1 orientations   STD       fills 1.0   blocks light   - the default solid block
       stair         12 orientations   STD       fills 0.75  blocks light   - a step - the only form that climbs
       column         6 orientations   COLUMN    fills 0.46  light passes   - a central post - load bearing

   It also lists the textures that actually exist, the region palette as a
   name → hex table, the brief, and the json shape to answer in. It opens with
   *"do not write the building yet"*.

2. **The answer is locked in.** The forms become the project's form allow-list
   (which the compiler already enforces) and the materials are stored. Anything
   outside the twelve is dropped and named; any texture that does not exist is
   reported so you can make it before building rather than discovering flat paint
   afterwards.

3. **Every build prompt states it back**, which is what makes "use these blocks"
   true rather than hopeful:

       THE BLOCK PLAN YOU AGREED - build with exactly this
         forms: cube, slab_half, stair, girder
         material wall soot_iron_plate color=#22252A
         material lamp glowstone color=#FFD9A0 light=14
         material pipe copper_pipe_wrap color=#4E7D70   <- NOT IN THE WORKSPACE YET
         Declare these material lines verbatim at the top of the document.
         Do not rename an alias, add a material or use a form outside the list.

It is step 5 in PROMPTS, with the same COPY / PASTE THE PLAN pair as every other
prompt that has an answer coming back. The reader is tolerant of a fenced block
with chatter either side.

## Round 26 — projects, at last

This was outstanding for many rounds and it is the thing the rest depended on.

**A project is a top-level folder treated as a repository.** Every asset under
its root belongs to it. The `projects` table in the database is assets and
predates this, so the container is `workspaces` internally and "project"
everywhere a person reads it.

**The selector is in the top bar, beside the brand** — where IntelliJ and PyCharm
put it — not crammed into a sidebar card. It shows which project you are in and
how many assets it holds. Clicking it switches project, creates one from a
folder, renames, forgets (the folder and its contents are untouched), or opens
the history.

**The tree is scoped to the project you are in**, the way an IDE shows one
project's files: everything under its root and nothing else. Measured: 46 cards
across the whole workspace, 2 in `Foundry Era`. "All projects" restores the full
tree, which is what the sidebar always used to do.

**PROJECT HISTORY is the whole repository, not one asset.** Every revision so far
was only visible from inside the asset that owned it, so there was no way to ask
what the project gained this week. The feed is one line per revision across every
asset under the root, newest first, grouped by day, marked ADDED or REVISED, and
clicking a line opens that asset:

    2026-11-27 · 1
      ADDED  soot_wall_panel        v1  PASS   Starter auto — brief: a dented steel barrel
    2026-11-26 · 1
      ADDED  foundry_valve_cluster  v1  PASS   Oxidised iron plate with amber pitting

It opens as its own overlay rather than another panel in a side rail.

## Round 27 — scoped locks, and batches that stay together

**A style lock now has a SCOPE.** It was one global string, so a workspace
holding two art directions could only ever have one of them. The card gained a
selector — **ALL / THIS PROJECT / THIS FOLDER** — with a dot on each scope that
already holds a lock, and a line saying which ones reach the open asset:

    Sent with this asset: every project + project Foundry Era

Locks that cover an asset layer general to specific, the same inheritance the
region palettes use, so a project adds to the house style without restating it.
A `dir:ZD/inner` lock correctly does not reach an asset sitting in `ZD`. The ALL
scope is still the old global setting, so an existing workspace keeps working.

**A batch stays together.** Everything pasted in one go used to scatter into
whatever folder happened to be selected, mixed in with work from days ago. CREATE
now offers *"Keep this batch together in its own dated folder"* (on by default,
and only shown in BATCH mode), which puts the import in
`<chosen folder>/batch_2026-11-28_2141_mob-pack` and tags every asset with the
group, so the import is a thing you can point at afterwards.

**And the prompts come back in one copy.** A batch of briefs left you with N
assets each needing its own prompt, fetched by clicking through the list — the
slow part of the whole flow, and the thing that was missing. After a brief batch
the Studio offers to copy all of them at once, each under a header naming its
asset:

    ======================================================================
    ASSET 2 OF 3:  grp_two   [model]
    ======================================================================

Measured: three briefs in, three prompts out, 37,010 characters in one clipboard.
