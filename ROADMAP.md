# Beyond boxes — a plan for richer shapes

You asked what happens when boxes and cubes aren't enough, and whether the AI
could have a dial for "how much variation" to allow. Here's how I'd build that,
cheapest and most useful first. Nothing below is implemented yet.

---

## The constraint worth keeping

The reason the current tool works at all is that the AI never touches geometry.
It writes *intent* — a bone, a box, a profile — and the compiler produces
watertight triangles. The moment the AI emits vertices, everything breaks: it
cannot see, so it cannot tell a sealed mesh from a hole.

So every addition below is a **new primitive the compiler knows how to build**,
never a new way for the AI to describe raw geometry. That's the line to hold.

---

## Stage 1 — Shape vocabulary for the voxel track (highest value, lowest risk)

Today a model is boxes only. These four additions cover most of what a
Minecraft-style mob or machine actually needs, and each is a small, bounded
change to the compiler:

| Primitive | Why it earns its place | Build cost |
|---|---|---|
| `wedge` | ramps, roofs, beaks, claws, angled armour | low — it's a box with two verts merged |
| `slab from=… to=… inset=…` | panels, trims, plating without stacking boxes | low |
| `cylinder axis=y segments=8` | barrels, pipes, legs, cables, gun muzzles | medium — reuse the prop revolve |
| `stairs steps=4 dir=+x` | staircases, terraced detail, mechanical ribs | low — repeated slabs |

Each one is declared the same way a box is, so the prompt grammar grows by four
lines and the AI's mental model doesn't change.

**Why this first:** it multiplies what's expressible without touching the
architecture, and the ASCII/preview feedback loop already handles it.

## Stage 2 — The variation dial ✅ BUILT (see SHAPE CONTROLS in the Studio)

**Implemented.** It landed as a `SHAPE CONTROLS` panel rather than a `style`
line in the document, which keeps designbridge untouched: the settings are
written into the prompt as hard numeric limits, and the Studio re-measures the
compiled result against them and reports anything over budget. Chamfer is the
one piece not built — it needs a compiler change.

What shipped:

- **`complexity` 1–5** — a budget, not a suggestion. The compiler *enforces* it:
  at 1 it rejects a document with more than ~6 boxes per bone; at 5 it allows
  40+. This is the "how much alternation from a bunch of boxes" setting, and
  making it a hard limit is what makes it meaningful — otherwise the model
  ignores it.
- **`symmetry=mirror-x|mirror-z|radial-N|none`** — declare it once and the
  compiler mirrors for you. Halves the tokens the AI spends and removes the most
  common asymmetry bug (a left arm that doesn't match the right).
- **`detail=low|medium|high`** — a hint the prompt generator turns into concrete
  guidance ("at low, read as a silhouette from 10 blocks away").
- **`chamfer`** — NOT built. A uniform edge cut has to happen inside the
  compiler, so it needs designbridge to change. Still on the list.

Settings are saved per asset, so each one keeps its own budget.

## Stage 3 — Orientation and placement sugar

The friction right now is that everything is absolute coordinates. These make
describing a shape match how people actually talk:

```
box ... orient=horizontal        # or vertical / along=x
box ... anchor=top-centre        # place by a feature, not a corner
box ... relative-to=torso offset=(0,2,0)
repeat count=5 step=(0,3,0)      # ribs, teeth, treads, slats
```

`relative-to` is the big one: it's how you say "the pauldron sits on the
shoulder" without recomputing coordinates every time the torso changes. It also
makes **scoped edits** (the component selection you now have) far more powerful —
change the torso and the attached parts follow.

## Stage 4 — Smooth and organic, properly

Only once the above is solid, and only through the compiler:

- **`taper` / `twist` on a swept profile** — already in designbridge for props,
  just not exposed to the model track.
- **`subdiv=1|2`** on a box group — Catmull-Clark-ish rounding for creatures,
  giving organic silhouettes from blocky input. Expensive to implement well.
- **`loft between=(profileA, profileB)`** — already in the prop track.
- **Metaballs / implicit surfaces** — the honest answer is this is a different
  engine. It needs marching cubes, a normals pipeline, and a watertightness
  proof. I'd do it as a fourth track (`blob`) rather than bolting it onto models.

## Stage 5 — The thing that would actually close the quality gap

A **vision critic**: render the asset, show that image to a multimodal model,
feed its critique back as text into the next round. This is worth more than any
new primitive, because the real limit isn't what the compiler can build — it's
that the AI writing the document cannot see what it made. Everything else on
this list makes the vocabulary richer; this makes the *judgement* better.

Roughly: `render → describe → critique → revise`, with the critique appended to
the FIX-mode prompt. The Studio already has every piece except the critic call.

---

## Suggested order

1. ~~**Stage 2 style controls** — the variation dial, enforced~~ ✅ done
2. **Stage 1 primitives** — immediate, visible payoff for STRATA mobs
3. **Stage 5 vision critic** — the real quality jump
4. **Stage 3 placement sugar** — quality-of-life, big token savings
5. **Stage 4 smooth shapes** — only if you genuinely need organic forms

Stages 1–3 are each a day or two of work. Stage 4 is a project. Stage 5 depends
on which multimodal model you point it at.

---

## Composition — reusing assets inside other assets

Your idea: a texture you already made should be usable on a model; more
generally, anything already built should be referenceable instead of rebuilt.
This is the highest-leverage feature left, because it changes the unit of work
from "an asset" to "a kit", and it's the only one that gets *better* the more
you've made. Here's how I'd build it.

### What a reference actually is

The key decision: a reference is a **link, not a copy**. The referencing
document names the other asset; it never inlines its source. That means fixing
the palette on `rusted_iron` fixes every model that uses it, and it means the
dependency is a real fact the Studio can show you rather than a comment.

Three kinds are worth supporting, in this order:

| Reference | Means | Why first/last |
|---|---|---|
| **model → texture** | this model's faces use that texture | Your actual ask, and the cheapest: no geometry involved |
| **model → model** | mount this asset onto a bone (a turret on a chassis) | Where kits really pay off; needs transform handling |
| **prop → profile** | reuse a 2D profile across solids | Narrow but nearly free — profiles are already named |

### How it rides along in the prompt

designbridge has no `use` statement and I'm not changing it, so the reference
lives in **Studio's layer** and is resolved before the compiler ever sees it.
Two halves:

**1. The prompt gains a resolved dependency section.** Not a file path — the
actual facts the model needs, so it can make decisions without seeing an image:

```
AVAILABLE COMPONENTS - already built, reference them by name
  texture "rusted_iron"   16x16, metallic 0.6, roughness 0.7
      palette: #52525b base, #3f3f46 shadow, #b45309 accent
      Use it by naming it in a comment on the box it applies to:
          box from=(-4,0,-2) to=(4,12,2) color=#52525b   # texture: rusted_iron
      Match the box colour to that palette so the preview reads right.

  model "chassis_mk2"      24x8x16, 6 bones, 11 boxes
      mount points: turret_ring (0,8,0), hitch (0,2,-8)
      Do not redraw it. Name the mount point you are attaching to.
```

That's the whole trick for the texture case: the model's *colours* come from the
referenced palette, and the Studio records the binding. The AI never has to
invent a palette, and consistency is structural rather than hoped-for.

**2. The Studio resolves and records.** On compile it parses the
`# texture: name` trailers, strips them (designbridge treats them as comments
anyway, so nothing breaks), and stores the edges in a new `asset_refs` table:
`(from_asset, from_part, to_asset, kind)`. Export then packs the referenced
texture PNG alongside the GLB and writes the material binding into the
`.boxmodel.json`, so STRATA gets a model that actually points at its texture.

### How the UI shows it

A dependency needs to be visible from both ends, or it's a trap:

- **On the asset card**: a small chip, `uses 2`, and `used by 3`. One glance
  tells you whether something is a leaf or load-bearing.
- **In the COMPONENTS list**: each bone shows its bound texture as a swatch
  beside the name. Clicking the swatch jumps to that texture asset.
- **A new USES panel**: the outgoing references with live thumbnails, each with
  unbind / swap / open. Swap is the payoff — point a model at
  `mossy_cobble` instead of `rusted_iron` and recompile.
- **A warning on delete**: deleting an asset that three models reference must
  say so and offer to unbind rather than silently breaking them.
- **In the branch map**: an optional second edge style for "references", so a
  kit's shape is visible at a glance.

### The hard parts, honestly

- **Staleness.** If `rusted_iron` changes, every model referencing it is now
  rendering an old palette. Needs a "3 assets depend on this, recompile them?"
  prompt after a referenced asset compiles. This is the real work.
- **Cycles.** A references B references A. Must be rejected at bind time with a
  clear message, not discovered at compile.
- **Transforms for model→model.** Mounting needs a position and rotation, which
  means mount points have to be a real concept rather than a convention. I'd
  punt this: ship texture refs first, learn from them, then do mounting.
- **Export semantics.** A GLB with an external texture is either a bundle (zip
  with both) or an embedded copy. Probably both, as an export option.

### Suggested order

1. **model → texture binding** — the ask, and self-contained
2. **UI both ways** (`uses` / `used by`, swap, delete guard) — without this the
   feature is a hidden trap
3. **Staleness prompts** — the thing that makes it trustworthy
4. **model → model mounting** — once the above is proven

---

## The Prompt Library — one menu for every prompt the system emits

**Status: designed, not built. Captured 2026-11-26 so it isn't lost.**

Your idea: a single menu that shows *all* the prompts, split into **static** (set
something up, no per-round input) and **dynamic** (needs your input to work),
where each dynamic one shows what it requires and gives you the UI to supply it
— pick a project, pick a revision, and so on.

### Why this is the right shape

Prompts are currently scattered by accident of where they were added: the task
prompt lives in BUILD step 2, the style-spec generator is a button in SETUP, the
system briefing is a checkbox, and the grammar reference isn't surfaced at all.
Nothing tells you the full set exists. Worse, the only way to discover what a
prompt *needs* is to generate it and notice something missing.

A library inverts that: the prompt is the thing you pick, and its requirements
are stated up front as slots you fill. You were right that most are dynamic —
by the count below it's 4 of 7, and the dynamic ones are the ones you use every
round.

### The catalogue

**Static — set things up, nothing to bind**

| Prompt | What it's for | Needs |
|---|---|---|
| **System briefing** | Explains the whole pipeline to a fresh AI: what it writes, what the compiler does, what comes back. Paste once at the top of a new chat. | nothing |
| **Grammar reference** | The exact grammar for one track, plus the `WHAT THIS GRAMMAR CANNOT DO` limits. For when the AI keeps inventing syntax. | track (model/prop/texture) |
| **Workflow primer** | How the paste-back loop works, so the AI stops sending patches and diffs. | nothing |

**Dynamic — needs you to bind something**

| Prompt | What it's for | Required slots | Optional |
|---|---|---|---|
| **Task: EDIT** | Change the current design | project, revision, instruction | component scope, compiler report |
| **Task: NEW TAKE** | Start over within the same asset | project, instruction | — |
| **Task: FIX** | Repair the errors from last round | project, revision *(report comes with it)* | hint |
| **Style spec** | Turn a loose art direction into the structured JSON that fills the lock | brief | existing lock (to extend rather than replace) |
| **Composition** *(needs Composition built first)* | Build an asset that references other assets | project, instruction, ≥1 referenced asset | — |

Note that **style lock** and **shape controls** are not slots on any of these.
They're project-level settings that ride along automatically — which is exactly
why the lock warning strip had to exist. The library should show them as
"attached from SETUP" rather than as something to fill in per prompt.

### Slot types, and the UI each one needs

The whole feature hinges on slots being typed, because the type determines the
control:

| Slot | Control | Resolves to |
|---|---|---|
| `project` | asset picker, grouped by folder | `projectId` |
| `revision` | commit picker for that project, defaulting to HEAD | `commitId`, and with it the source and the report |
| `instruction` | textarea, with the mode explainer beside it | free text |
| `component` | part picker, populated from the revision's compiled mesh | bone/part name |
| `track` | three-way chooser with the worked example for each | `model` / `prop` / `texture` |
| `brief` | textarea | free text |
| `asset-ref` | multi-select over compiled assets, filtered by what the reference kind allows | list of `projectId` |

Two rules make it coherent:

1. **Slots cascade.** `revision` is meaningless until `project` is bound, and
   `component` is meaningless until `revision` is. An unbound parent disables
   its children rather than showing an empty dropdown.
2. **Binding is sticky and shared.** Picking `project` here is the same act as
   selecting it in the sidebar. The library is a different view onto one state,
   not a second place to track which asset you're on.

### Readiness, and how it differs from the verifier

Each prompt card shows one of three states:

- **Ready** — every required slot bound. Shows the character/token estimate and
  a COPY button.
- **Needs N things** — lists exactly which slots are empty, each with its
  control inline. Copy is disabled, because a prompt with an unbound slot is
  the failure mode this feature exists to prevent.
- **Unavailable** — the prompt can't apply yet, with the reason: *Composition
  needs the Composition feature*; *FIX needs a revision that actually failed*.

This is the mirror image of the existing prompt verifier, and both are worth
having. The verifier answers *"did everything make it into the text I'm about
to copy?"* — after the fact, by probing the built string. The library answers
*"what does this prompt need before it can be built at all?"* — before the fact,
from the slot manifest. One catches a lost section; the other catches a prompt
you were never able to build correctly.

### Implementation sketch

Server: a `PROMPT_CATALOGUE` list of descriptors — `id`, `title`, `category`,
`purpose`, `slots` (each with `name`, `type`, `required`, `dependsOn`), and a
`build` reference to the function that renders it. `GET /api/prompts` returns the
catalogue with no prompt text; `POST /api/prompts/<id>` takes the bound slots and
returns the text plus the existing verifier manifest. `build_engineered_prompt`
becomes the builder behind three catalogue entries (EDIT / NEW TAKE / FIX) rather
than a function the UI calls directly, so the three stop being a `mode` string
that the caller has to get right.

UI: a new left-rail entry or a `PROMPTS` tab, two sections, a card per prompt.
The slot controls are the ones that already exist in BUILD and SETUP, reused
rather than rebuilt — which is the test of whether the slot typing is right.

### What it quietly fixes

- The system briefing stops being a checkbox nobody notices.
- The grammar reference becomes reachable, which is the actual answer to an AI
  inventing `wedge` and `rot=`.
- FIX stops being a mode you have to know to switch into; it's a prompt you pick
  when a compile fails.
- Every prompt gets one honest statement of what it needs, in one place.

### Order, when you want it built

1. `PROMPT_CATALOGUE` + `GET /api/prompts`, with the three task prompts and the
   briefing. Proves the descriptor shape against prompts that already exist.
2. The menu UI with `project` / `revision` / `instruction` slots and the three
   readiness states.
3. Grammar reference and workflow primer — cheap once the frame exists.
4. Style spec moved in from SETUP.
5. Composition, whenever Composition itself lands.

---

# Acting on the model reviews (captured 2026-11-27)

Two independent reviews from models that produced assets for STRATA. Both rank
the same thing first: *"I worked blind."* Before planning around their claims, I
checked each against the source. **Four of their top asks already exist.** That
changes what the plan should be.

## What the reviews got wrong, and why it matters

| Claim | Reality |
|---|---|
| "I never saw a compile report or the ASCII views" | `designbridge.py check doc.db` prints status, error count with fix hints, the bone tree, size, and **three orthographic ASCII views**. It is a CLI, pure stdlib, one file. |
| "Fractional coordinates may not be accepted" | They are. `_tuple()` parses with `float()`. |
| "No rotated boxes, no mirror" | True on the **model** track only. The **prop** track's `part` accepts `at`, `rot`, `rotate`, `scale`, `mirror`. Animation keys accept `rot` too. |
| "The workflow says fix-and-resend but I only got one shot" | The Studio's FIX prompt already embeds the current compiler report. |
| "Ambiguous whether multiple textures in one block are valid" | Intake splits a paste into N documents and files each. The *prompt* still says "ONE fenced block". |
| "Hard cap of 6 layers is too tight" | That is our own style budget, not a grammar limit — `BUDGET["texture"]` is 6 at complexity 3 and **14 at complexity 5**. |

So the headline is not *build an evaluation loop*. It is **ship the loop that
already exists to the model that needs it**. Everything these reviewers wanted
was two metres away behind a copy-paste boundary.

## Tier 1 — reachable now, no grammar change

1. **The self-check kit.** Package `designbridge.py` plus a one-page
   `README-FOR-THE-AI.md` as a drop-in for the model's own sandbox, and add a
   Studio button that copies the exact instruction text: *here is the file, run
   `python3 designbridge.py check doc.db`, read the views, fix, repeat, and paste
   only the document that passes.* This is the #1 ask and it is mostly packaging.
   Where a model has no sandbox, nothing changes; where it has one — Claude,
   most agent harnesses — it stops working blind entirely.

2. **`dbcheck.py`, for the checks designbridge does not do.** A standalone
   wrapper that imports designbridge and adds the studio-side lints the model
   cannot otherwise see: palette membership against the locked palette, per-bone
   and total box budget for the declared complexity, ground contact at `y=0`,
   orphan bones, duplicate animation keys at one time, bounding box against the
   brief's target size. `check_style()` and `check_unknown_keys()` already exist
   server-side; this lifts them out. designbridge stays untouched.

3. **State precedence once.** Lock beats brief. Brief colours snap to the
   nearest locked palette entry, and the prompt carries the **computed mapping
   table** (`#C87A45 → #C79A4B brushed_brass`) so the model is not making a
   judgment call it then has to confess to. Both reviews raised this
   independently; it is the cheapest real fix on the list.

4. **Scope the standing rules by track.** A texture prompt should carry palette,
   PBR, luminance and tiling rules — not bone budgets, silhouette hierarchy,
   smokestacks or boss complexity. Roughly half of the texture prompt is
   currently about 3D. This is a straight bug in `build_engineered_prompt`.

5. **Fix the single-asset contract.** Say that several documents in one reply are
   welcome and will each be filed, because intake already does exactly that.
   Removes the ambiguity without any new code.

6. **A `deviations:` line in the output contract.** So a model can declare a
   substitution without breaking the four-line plan rule — and the Studio stores
   it on the revision, using the label mechanism that now exists.

7. **Behavioural one-liners per command.** Does `noise` wrap at tile edges? Does
   `brick` stagger by default? Is `bevel` the tile border or the previous layer?
   Are pivots world-space for child bones? Is `pos` in voxels or pixels? **Which
   way is left?** Most of these are answerable from the source in an afternoon,
   and every one of them is currently guessed.

8. **Three reference models.** A humanoid with hips, knees and ankles that
   actually bends; a quadruped; a machine. Shipped in `examples/`, cited by the
   prompt, and pre-loaded into the reuse library so they can be copied rather
   than reinvented. The first review's exact ask, and the block feature to carry
   it is already built.

## Tier 2 — Studio work

9. **Animation extreme-pose report.** Print the bounding box at each animation's
   extreme keys. Catches arms through the floor, parts leaving the model, and
   limbs that do not follow the body — the kneel bug the first review caught in
   its own output.

10. **Texture contact sheet.** 8× nearest-neighbour upscale, a 3×3 tiling to
    expose seams, edge-delta seamlessness score, palette linter, and the top five
    hex percentages to catch "90% flat fill". designbridge already renders the
    PNG; this is composition and measurement around it.

11. **Re-tune the budgets.** The per-bone cap of 20 pushes a model to split
    bones for no reason. Consider a soft per-bone warning with a hard total, and
    raise the texture layer allowance at middle complexity.

## Tier 3 — the grammar decision, which is yours

The model track has no `rot`, no `mirror`, no `repeat`, no emissive flag; the
texture track has no grid/hex/rivet primitive, no per-layer PBR, no seam mask.
Two independent reviews now name this as the ceiling on quality. Extending it
means editing `designbridge.py`, which you asked to keep intact.

There is a third option worth more than it sounds:

- **A macro pass that never touches designbridge.** `mirror=x` and `repeat` are
  pure source-to-source transforms — expand them into the plain `box` statements
  the grammar already accepts, before compiling. The model writes one arm; the
  expander writes both. `designbridge.py` stays byte-identical, and the two
  features the review blamed for most of its hand-copy mistakes both arrive.
  Emissive and rotated voxel boxes cannot be faked this way; they need the
  kernel.

So: macro pass now, kernel fork only if the texture primitives turn out to
matter more than the workarounds.

## If only one thing gets built

Tier 1 items 1 and 2. A model that can run `check` on its own work before
sending it catches the box counts, the orphan bones, the wrong-facing lens and
the fractional-coordinate worry by itself — every concrete error the first review
listed about its own output. Nothing else on this page comes close.

---

# The STRUCTURE track — buildings out of textured blocks

**Status: designed, not built. Captured 2026-11-27.**

A fourth track alongside model / prop / texture. A structure is a volume of
block placements; each block type binds to textures that already exist in the
workspace. The output is a piece the game can spawn — a ruin, a foundry hall, a
collapsed spire — not a single mesh.

This composes with what is already here rather than extending it: textures are
assets with names, and a structure references them the way the reuse library
references parts.

## The decision you asked about: block shapes

Each texture should **not** be one block. A texture is a surface; a block is a
shape wearing surfaces. Binding them one-to-one would mean a new texture for
every shape of the same material, which is how a palette of 10 materials turns
into 60 near-duplicate tiles.

Instead: **a small shape set, each shape wearing a material.**

| Shape | Why it earns its place |
|---|---|
| `full` | the mass of everything |
| `slab` (bottom/top) | floors, ledges, thin roofs, half-height steps |
| `stair` (4 rotations) | roofs, rubble slopes, seating, anything that reads as built |
| `post` | columns, pipes, railings — a square prism thinner than a block |
| `panel` | a thin vertical face: walls one block thin, signage, plating |
| `frame` | an open square — windows, grates, scaffolding |

Six shapes is the point where architecture starts reading as architecture. The
instinct to add wedges, corner stairs, fences, half-slabs and twenty variants
should be resisted until a real building is blocked out and the gap is felt —
**what makes a ruin read as a ruin is massing and material, not shape count.**

One modifier earns its place immediately, because the whole use case is ruins:

- **`damage=0..3`** on any placement. Not a new shape — a deterministic
  erosion that chips corners and punches holes at a seed. A ruined wall is the
  same wall with damage 2.

## Material binding

Per block type, not per placement:

```
block rusted_plate
  all   soot_iron_plate          # one texture everywhere
block foundry_floor
  top   foundry_grate
  side  weathered_iron_band
  bottom soot_basalt
```

Top/side/bottom covers almost everything; a six-face override exists for the
cases it does not. Both texture names resolve against assets in the workspace,
so a structure breaks loudly if someone renames a texture out from under it —
which the rename feature now makes easy to do.

## Authoring: volumes, not voxels

The critical design constraint. A 20×20×20 ruin is 8,000 cells; a model cannot
write that and should never try. The grammar composes volumes:

```
structure foundry_ruin size=(24,18,24) seed=7

palette
  wall    rusted_plate
  floor   foundry_floor
  pillar  brass_column

room (0,0,0) (24,10,24) walls=wall floor=floor ceiling=none
  door  south at=(12,0)  width=4 height=6
  window east  at=(6,4)  width=3 height=3 every=6

colonnade along=north from=(2,0,2) to=(22,0,2) block=pillar shape=post every=4
stairs from=(4,0,20) to=(10,6,20) facing=east block=wall

collapse  roof  amount=0.6   seed=7
collapse  wall  north amount=0.3
rubble    floor amount=0.4 from=wall
```

Ten lines, a building. `collapse` and `rubble` are what make this a ruin
generator rather than a block editor: they take an intact structure and erode
it deterministically, so the same seed gives the same ruin and a different seed
gives a sibling.

## Verified standing, the way props are verified watertight

DesignBridge's real contribution is not geometry, it is **guarantees by
construction** — a prop cannot compile non-watertight. The structure track's
equivalent is support: your own spec already runs a Structural Integrity BFS,
so a ruin that cannot stand would collapse the moment it spawned.

The compiler should run that same BFS and refuse to pass a structure with
floating islands. `collapse amount=0.6` then means *remove 60% of the roof and
keep what remains standable*, which is a much stronger primitive than random
deletion.

## Spawning: emit jigsaw pieces, not finished buildings

Your spec already specifies WFC/jigsaw ruin generation, so a structure should
compile to a **piece with connectors**, not a finished building:

```
connector north at=(12,0,0) width=4 height=6 kind=corridor
connector up    at=(12,10,12) width=6 height=6 kind=shaft
```

Then the game assembles pieces, and the tool's job is to produce a library of
pieces that fit each other — which is exactly what a jigsaw system needs and
exactly what hand-authoring a whole building does not give you.

## Output

- `structure.json` — block palette, placements run-length encoded by layer,
  connectors, seed, and the support-check result
- the same four-angle contact sheet, so a model can see the massing
- an OBJ/GLB merge for viewing, with the real textures applied
- an ASCII plan per floor, which is genuinely readable for buildings in a way
  it is not for creatures

## Where it lives

`designbridge.py` cannot do this and must stay intact, so the structure track
is a **separate compiler** — `structurebridge.py` — sitting beside it and
importing it for texture compilation and PNG rendering. No fork, no conflict,
and the Studio gains a fourth track with the same report-and-fix loop.

## Build order

1. Block shape set + material binding + a flat `place` primitive. Renders.
2. `room`, `door`, `window`, `colonnade`, `stairs` — the composing volumes.
3. Support BFS, and `collapse` / `rubble` built on top of it.
4. Connectors and the jigsaw export.
5. The structure track in the Studio: prompt, report, preview, intake.

Stop after 2 and look at a real building before doing 3 — the shape set is a
guess until something is built with it.

---

# Evaluating the Architectural Form Kernel proposal (2026-11-27)

A second model proposed an engine-side form system: ~24–32 canonical shapes at
24 orientations, plus a 6-face cladding layer, with a `FormLUT` SSBO carrying
analytical ray-intersection primitives so `voxTex` stays at one texel per block.

**Verdict: the core idea is right and one part of it is better than anything we
had. The scope is roughly three times what should be committed to, and the
whole document is runtime — there is no content pipeline in it at all, which is
precisely the half we are building.**

## What is genuinely good

**The FormLUT is the correct answer to a real problem, and we should adopt it.**
Representing a 45° slope in `voxTex` as a solid cube gives black shadow acne and
SVGF ghosting; raising `voxTex` to sub-voxel resolution kills DDA speed and VRAM.
Storing `formId + orientation` per texel and doing exact analytical
sub-voxel intersection inside the DDA cell avoids both. That is the right
shape of solution and it is not obvious.

**Upgrading `Blocks.occluder()` from boolean to a 6-bit per-face mask is
necessary, not optional.** Without it, vertical slabs and sloped walls either
leave void seams or waste fills. This is a small change with a large payoff.

**Cladding that detaches into debris and leaves a Reconstruction Ghost** ties
three existing systems together for almost no new machinery.

**A 64-bit packed cell in a sparse per-chunk ShapeMap** is the right storage
decision — plain terrain pays nothing.

## Where it fails

**1. The shape count is a second engine, not a kit.** Each form needs a mesher,
an analytical RT primitive, a collision shape, a support-transfer rule, a fluid
capacity and seal mask, an occlusion mask, verified variants at every legal
orientation, and art per material. That is eight subsystems multiplied by thirty
forms. The document's own Phase 8 quietly shrinks to "the Core 12", which is an
admission that the thirty is aspiration rather than plan.
**Fix: commit to the Core 12 in writing and make the rest a backlog with
evidence gates — a form gets built when a real building needed it and the
workaround was ugly.**

**2. Cushion / pillow blocks should be cut entirely.** They need a beveled AABB
in the ray path, normal perturbation in the fragment shader, and their own
collision response — to serve one mechanic, impact trauma in boss arenas.
**Fix: "padded" becomes a cladding material with a damage-reduction property,
not a geometric form. Same mechanic, none of the pipeline.**

**3. `fluidCapacity = 0.5` is a much larger promise than it looks.** Partial-cell
volumes with per-form geometry is a different fluid solver from full/empty
cells, and "real head pressure" is asserted in passing.
**Fix: ship channels as full cells carrying a flow-direction hint and a visual
trough. Make capacity analytic only once the solver demonstrably handles it.**

**4. The two-layer model has a combinatorial hole: cladding on a non-cube
form.** A 2px face panel on a 45° slope — does it follow the slope, cap the end,
or is it illegal? With thirty forms and six faces that is 180 undefined cases.
**Fix: one extra field. `FormLUT` carries a 6-bit `claddableFaces` mask; cladding
is legal only on faces that are full squares on the host form. Closes the hole
deterministically and the placement UI reads the same mask.**

**5. Twenty-four orientations is wrong for most forms.** A slab has 6 meaningful
orientations, a stair 8, a column 3. A uniform 24 wastes index space and admits
states that are invalid or duplicate.
**Fix: a per-form valid-orientation mask in the LUT, read by the placement UI,
the serializer and the ruin generator alike.**

**6. The support rules change what the BFS is, and the document does not say
so.** "Arches transfer load downward-and-inward" turns a connectivity flood into
a directional load router — a different algorithm with different failure modes.
**Fix: name it, and define the cheapest version that works: per-form support
vectors with integer budgets, not real statics. Say out loud that it is a
believable approximation, so nobody later mistakes it for engineering.**

**7. Era-gating by shape fights the one-material-plus-radial UX.** If the radial
must grey out forms per material per era, that is a lot of state to explain.
**Fix: gate by material. Concrete arrives in Era 2 and brings every form with
it. Same progression feel, far less to communicate.**

**8. The unnamed risk is chunk remesh time.** Thirty forms with per-face
occlusion masks mean the greedy mesher can no longer merge across cells cheaply,
and remesh cost is what will actually hurt on a builder's base.
**Fix: measure a worst-case chunk of entirely shaped cells in Phase 8, before
the form count is locked.**

**9. There is no content pipeline anywhere in the document.** No authoring
format, no way to write a WFC template, no tool. It closes by asking for more
spec to lock bit layouts. That is the engine's half of the problem, and it is
the half we are *not* building.

## What this changes in our plan

The six-shape set above was sized for a tool authoring ruins by hand. If the
engine is committing to a Core 12, the tool should emit exactly those twelve,
or it produces buildings the engine cannot represent and the engine gets ruins
nobody can author.

**Revised structure-track shape set — track the engine's Core 12:**

| Our shape | Engine form |
|---|---|
| `full` | Full Cube |
| `slab` | Horizontal Half-Slab |
| `vslab` | Vertical Half-Slab |
| `plate` | Quarter-Plate (4 / 8 / 12 px) |
| `stair` | Stairs |
| `slope` | 45° Slope |
| `chamfer` | Chamfered / Beveled |
| `column` | Fluted / Octagonal Column |
| `girder` | I-Beam Girder |
| `arch` | 1×1 and 2×2 Arch Segment |
| `grate` | Grate / Louver |
| `clad` | 6-Face Cladding (material + face mask) |

Dropped from their list for now: cushions (becomes a material), diagonal braces
and corbels (arch and girder cover the spans worth building first), embrasure
slits (a grate variant), aqueduct channels and culverts (until the fluid
solver is real), geodesic segments and aether struts (Era 3–4, not Phase 8).

`damage=0..3` stays, as our own erosion modifier — it is an authoring concept,
not an engine form, and it compiles down to ordinary placements.

The structure compiler must therefore also emit, per cell, the fields the engine
reads: `formId`, `orientation` (validated against the per-form mask),
`material`, `claddingMaterial`, `claddingMask`. That is the 64-bit record, and
producing it is the whole point of the tool.

## The one thing to say back to that model

Ask it for the **per-form table** before any of it is built: formId, valid
orientations, analytical primitive, support-transfer rule, fluid capacity,
seal mask, claddable-faces mask, and mass fraction. Twelve rows. If a form
cannot be filled in on one line across all eight columns, it is not ready to
be in the Core 12 — and that table is also exactly the file our compiler needs
to validate against.

---

# LOCKED: the Core 12 form contract (received 2026-11-27)

Agreed with the engine side. Both the engine's `FormLUT` and the structure
compiler read these same numbers, so the two cannot drift.

**Conventions.** Coordinate space is normalised `[0,1]³` where 16 px = 1.0.
The 6-bit face mask order is `bit 0 = +X, 1 = -X, 2 = +Y, 3 = -Y, 4 = +Z,
5 = -Z`, written in canonical orientation before `orientation ∈ [0..23]` is
applied. Cladding is an in-cell 2 px (0.125) boundary layer, valid only on bits
set in `claddableFaces`; on an empty host cell all six faces are claddable, so
a standalone 2 px panel, louver or grate costs no Core 12 slot.

**Support rule — the cheapest honest version.** The existing BFS stays; edge
traversal becomes an integer weighted BFS (Dial's bucket queue, no FEA):
`STD` vertical 0 / horizontal 2; `COLUMN` as STD with a 1.5× vertical cap along
its axis; `SPAN` horizontal 1 along the beam axis; `BRACKET` horizontal 0 into
its backed face; `NON_LOAD` passes mass down and no horizontal support.

| ID | Form | Ori | Analytical primitive | Support | Fluid | Seal / occl. | Claddable | Mass |
|---|---|---|---|---|---|---|---|---|
| 0 | `cube` | 1 | AABB `[0,0,0]→[1,1,1]` | STD | SOLID | ALL | ALL | 1.00 |
| 1 | `slab_half` | 6 | AABB `[0,0,0]→[1,0.5,1]` | STD | SOLID | −Y | −Y | 0.50 |
| 2 | `plate_quarter` | 6 | AABB `[0,0,0]→[1,0.25,1]` | NON_LOAD | SOLID | −Y | −Y | 0.25 |
| 3 | `stair` | 12 | 2 AABBs | STD | SOLID | −Y, +Z | −Y, +Z | 0.75 |
| 4 | `slope_45` | 12 | AABB ∩ plane `y ≤ z` | STD | SOLID | −Y, +Z | −Y, +Z | 0.50 |
| 5 | `slope_corner` | 24 | AABB ∩ `y ≤ x` ∩ `y ≤ z` | STD | SOLID | −Y | −Y | 0.33 |
| 6 | `chamfer_edge` | 12 | AABB ∩ `y − z ≤ 0.75` | STD | SOLID | −Y, +Z | −Y, +Z | 0.97 |
| 7 | `column` | 3 | AABB `[.125,0,.125]→[.875,1,.875]` | COLUMN | SOLID | NONE | NONE | 0.56 |
| 8 | `girder` | 3 | AABB `[.3125,0,.3125]→[.6875,1,.6875]` | SPAN | PASS | NONE | NONE | 0.14 |
| 9 | `corbel_arch` | 12 | 3 AABBs | BRACKET | PASS | +Y, +Z | +Y, +Z | 0.50 |
| 10 | `channel_trough` | 12 | 3 AABBs | STD | FLOW_Z | −Y, ±X | −Y, ±X | 0.63 |
| 11 | `trim_edge` | 12 | AABB `[0,0,0.75]→[1,0.25,1]` | NON_LOAD | PASS | NONE | NONE | 0.06 |

Fluid modes: `SOLID` blocks flow; `PASS` is full-cell pass-through; `FLOW_Z` is
full-cell pass-through with sealed −Y and ±X walls and a flow bias along local
±Z. No partial-cell volumes anywhere — that was the point of the critique.

## The 64-bit sparse cell record

```
bits  0..15 : materialId        uint16   0 = empty, cladding-only host
bits 16..21 : formId            uint6    0..11 Core 12, 12..63 reserved
bits 22..26 : orientation       uint5    0..23, validated against FormLUT
bits 27..42 : claddingMaterial  uint16   0 = none
bits 43..48 : claddingMask      uint6    world-space +X,-X,+Y,-Y,+Z,-Z
bits 49..50 : crackStage        uint2    0..3 structural stress overlay
bits 51..63 : reserved          uint13   zero
```

One `claddingMaterial` is shared across every set bit of `claddingMask`. The
structure compiler emits exactly this record, and validates `orientation`
against the per-form count and `claddingMask` against the rotated
`claddableFaces` before it will pass a document.

**Our shape names map 1:1 onto these ids**, so the earlier six-shape table is
superseded: use the form names above.
