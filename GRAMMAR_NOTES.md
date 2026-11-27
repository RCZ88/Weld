# Grammar behaviour — the answers, measured

Every statement here was checked against `designbridge.py` or measured by
compiling a test document, on 2026-11-27. Nothing is inferred from the docs.
These are the questions models actually asked after working blind.

## Numbers and axes

| Question | Answer |
|---|---|
| Are fractional coordinates allowed? | **Yes.** `_tuple()` parses with `float()`. `-1.5` is legal everywhere a number is. |
| What unit are model coordinates in? | **Voxels.** 16 voxels = 1 metre = 1 STRATA block. |
| What unit is an animation `pos=`? | The same voxels, as an offset from the bone's rest position. |
| Which way is **+X**? | **+X is the model's left**, looking at it from the front. Y is up, −Z is the facing direction. |
| Where is the ground? | `y = 0`. A model should touch it exactly. |

## Bone pivots, and one export bug worth knowing

A bone's `pivot=` is written in **absolute coordinates**, not relative to its
parent. Box corners are absolute too. The two agree, and the engine-native
`.boxmodel.json` export is consistent throughout.

**The `.glb` export is not.** glTF composes a child node's transform on top of
its parent's, but the exporter writes each bone's pivot verbatim as the node
translation. A bone parented under a bone whose pivot is not at the origin
therefore lands displaced by the parent's pivot in the `.glb`:

```
bone body pivot=(0,10,0)
bone arm  pivot=(4,12,0) parent=body     ->  in the .glb, arm sits at y=22
```

`.boxmodel.json` is correct, so STRATA itself is unaffected; this hurts the
Blender and preview path. **Keep the root bone's pivot at `(0,0,0)` and the
problem disappears entirely.** `dbcheck.py` warns when it does not.

## Keyframes

- Two keys for the same bone at the same `t` are **not merged**. Write one key
  carrying both `rot=` and `pos=`.
- Keys interpolate linearly between the times given; there is no easing.
- A bone with no keys does not move — but it still follows its parent, which
  is the whole reason to parent limbs.

## Texture layers — which ones tile, and which do not

Measured by comparing the wrap-around column step against the largest step
inside the tile. A layer "seams" when the wrap is an outlier:

| Layer | Tiles seamlessly? | Notes |
|---|---|---|
| `fill` | yes | flat |
| `brick` | **yes** | staggers rows by default (`off = w/2` on odd rows); wraps with `% 16` |
| `checker` | yes | |
| `stripes` | yes | |
| `scatter` | yes | |
| `gradient` | yes | runs top-to-bottom, so the left and right edges match |
| `dither` | yes | |
| **`noise`** | **NO** | `_vnoise` uses unwrapped lattice coordinates. Measured wrap step 47 against an interior maximum of 28 |
| **`veins`** | **NO** | built on the same noise |
| **`bevel`** | **NO** | draws light on the top and left edges and dark on the bottom and right **of the tile itself**. Measured wrap step 281 against an interior maximum of 144 |
| **`outline` / `border`** | **NO** | same: a border on the tile, not on the shape inside it |

**This is the single most useful thing on this page.** A tile that looked
"muddy and procedural" was usually a `noise` layer seaming at every edge, and a
tile with "harsh borders" was `bevel` doing exactly what it says.

Workarounds that need no grammar change:

- For surface variation that tiles: `scatter`, `dither`, or `brick` with
  `jitter=`, instead of `noise`.
- `bevel` and `outline` are for a tile that is **meant** to read as a single
  panel with edges — a plate, a sign, a hatch. Never on ground, wall or any
  tile that repeats.

## Argument traps

- `veins amount=` is a **threshold**, not a density. The default is `0.12`.
  At `0.25` it is already a wash, and at `0.5` it floods the entire tile one
  colour. Measured.
- `brick h=` is the row height in pixels, so on a 16-pixel tile `h=4` gives
  four courses. `w=8` gives two bricks per row with a half-brick stagger.
- `bevel width=` insets by that many pixels on all four sides.
- `scale=` on `noise` is a feature size in pixels, not a frequency — larger is
  blobbier.

## Shape budget

The layer and box caps are the **Studio's** budget, not a grammar limit. At
complexity 5 a texture may use 14 layers, not 6. If six is too tight, the
answer is to raise the complexity, not to fight the grammar.

## What the model track genuinely cannot do

No rotated boxes, no mirroring, no repeat, no emissive flag. Every one of these
exists on the **prop** track (`part` takes `at`, `rot`, `rotate`, `scale`,
`mirror`), and `rot` exists on animation keys — the gap is specific to voxel
boxes. Symmetric parts must be written out twice, which is where hand-copy
mistakes come from. A macro pass that expands `mirror=` and `repeat` into plain
boxes before compiling is planned; see `ROADMAP.md`.
