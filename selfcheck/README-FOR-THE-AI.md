# Check your own work before you send it

You have three files in this folder: `designbridge.py` (the compiler for
models, props and textures), `structurebridge.py` (the compiler for buildings)
and `dbcheck.py` (everything they do not check). All plain Python, standard
library only, no installation.

**Do not send a design document until it passes.** You can compile it yourself,
look at the result, and fix it, as many times as you need. Nobody sees the
rounds you throw away.

## The loop

```bash
# 1. write your document to a file
cat > doc.db <<'END'
model guard_drone
bone body pivot=(0,0,0)
  box from=(-4,0,-3) to=(4,9,3) color=#3A3F45
END

# 2. compile it and read the report
python3 designbridge.py check doc.db          # model, prop or texture
# python3 structurebridge.py check ruin.sb    # a building instead

# 3. check what the compiler does not
python3 dbcheck.py doc.db --complexity 4 --size 64x72x40 \
    --palette "#3A3F45,#22252A,#C79A4B" --sheet look.png

# 4. fix what it tells you, and run it again
```

Stop when `dbcheck` prints `READY`. Then send the document, and only the
document.

## What each step tells you

**`designbridge.py check`** — whether it compiles, every error with a fix hint,
the bone tree, the overall size, and three ASCII silhouettes (front, side,
top). The silhouettes are a real projection of the geometry: if a part is not
where you meant it, you will see it there.

**`dbcheck.py`** — the things nobody can hold in their head:

- how many boxes each bone has, against the cap for this complexity
- the total, against the budget
- bones parented to nothing, which will not move when the body does
- boxes below the ground, or floating with nothing to attach to
- colours that are not in the locked palette, **and the nearest one that is**
- the bounding box against the size the brief asked for
- two animation keys for one bone at one time
- a bone that moves while a limb hanging off it does not

**`--sheet look.png`** — a four-angle contact sheet. If you can see images,
open it. One camera is not enough: a lens and the hood over it can point
opposite ways and look perfectly fine from the front.

## Why this matters more than it sounds

Writing a model blind means guessing at box counts, at whether a limb is
attached, and at which way a part faces. Those are the mistakes that actually
show up in finished assets, and they are all mechanical — a script catches
every one of them in under a second.

The budget numbers are not advice. The studio runs the same checks when your
document arrives, so a document that fails here fails there.

## Flags worth knowing

| Flag | What it is for |
|---|---|
| `--complexity N` | 1–5, whatever the brief asked for. Decides the box budget. |
| `--size WxHxD` | the target bounding box from the brief. Flags anything off by more than 15%. |
| `--palette "#AAA,#BBB"` | the locked palette. Any other colour is reported with its nearest legal match. |
| `--sheet out.png` | write the four-angle contact sheet. |
| `--quiet` | skip the compiler's own report and views; just the checks. |
| `--tolerance N` | how close two boxes must be to count as touching. Default 0.5. |

Exit code is `0` when nothing failed and `1` when something did, so you can
loop on it.


## Buildings

A document that starts with `structure` goes through `structurebridge.py`
instead, and `dbcheck.py` routes it there for you. The report adds a top-down
ASCII floor plan per level, a histogram of the forms used, and a warning for
any cell left floating after a collapse — those would fall the moment the ruin
spawned.

```bash
python3 structurebridge.py check ruin.sb
python3 dbcheck.py ruin.sb --sheet look.png
python3 structurebridge.py build ruin.sb --out ./out
```
