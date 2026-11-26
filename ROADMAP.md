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
