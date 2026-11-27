# Reference models — copy the skeleton, change the boxes

Three finished rigs, each one passing `dbcheck` with zero failures and zero
warnings. They exist so nobody has to invent a skeleton from scratch, which is
where most rigging mistakes come from.

| File | What it demonstrates |
|---|---|
| `humanoid_vanguard.db` | hips → chest → head, arms with hands, and legs with **thigh → shin → foot** so a kneel actually bends. Carries an `idle` and a working `kneel`. |
| `quadruped_stalker.db` | a spine that bends, a neck and head, four legs each with a knee, and a `walk` with the diagonal pairs in opposition. |
| `machine_crane.db` | a fixed base with a slewing turret, a boom that luffs and a hook that drops — the pattern for any machine with a part that swings. |

## The rules they all follow

- **A `root` bone at `(0,0,0)`.** Everything hangs off it.
- **Every bone is parented.** An unparented limb stays standing when the body
  kneels — the single most common rigging bug.
- **Pivots sit at the joint**, not at the centre of the box. A knee pivot goes
  at the knee.
- **The lowest face is exactly `y = 0`.** The model stands on the ground.
- **One key per bone per time**, carrying both `rot=` and `pos=` if it needs
  both.

## Using one

Copy the file, rename the model, and replace the boxes bone by bone. Keep the
bone names and the parenting — they are the part that is hard to get right, and
the part animations depend on. Then:

```bash
python3 ../../selfcheck/dbcheck.py your_model.db --complexity 4 --sheet look.png
```
