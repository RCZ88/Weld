DESIGNBRIDGE -- HOW TO DESIGN WITHOUT EYES
==================================================

HOW TO THINK ABOUT A SHAPE YOU CANNOT SEE

You have no eyes, so do not try to imagine a picture. Work down this ladder
instead. Each rung is a question with a short written answer, and the answers
produce the design almost mechanically.

  1. FUNCTION. What must this object do, hold, resist or fit? Name the one
     surface that does the real work - the rim you drink from, the face that
     bolts to the wall, the bore the shaft passes through. Everything else is
     in service of it.

  2. GENERATING VIEW. Of the three views, which single 2D outline defines the
     object? Say it out loud: "seen from the front, its cross-section is ...".
     If you cannot finish that sentence, you do not understand the shape yet
     and you must break it into smaller parts that you can.

  3. CONSTRUCTION STRATEGY. Match the answer above to one operation:
       the same cross-section all the way along  -> extrude
       spun around one axis                      -> revolve
       one shape blending into another           -> loft
       genuinely separate pieces                 -> several solids in assembly
     Decide this before you write any number.

  4. DIMENSION ANCHORS. Write the three overall dimensions in millimetres,
     anchored to something real from the reference list. Then derive the inner
     dimensions from the outer ones by subtracting a wall token, never by
     guessing both independently - that is how walls end up 0.3mm or 11mm.

  5. TOLERANCE AND WALL. Any two parts that must mate need a clearance token
     between them ($clear.slip for a lid, $clear.press for a pin). Any
     material that must survive needs at least $wall.std. Use the tokens; they
     encode what actually prints.

  6. VERIFY, DO NOT TRUST. Compile the design and read the report. You are
     reading three things: did it close into a solid, are the measurements the
     ones you intended, and do the three silhouettes match the sentence you
     wrote at rung 2.

READING THE ASCII VIEWS
  Three orthographic views. Each character is one ray fired through the solid,
  and the shade is how much MATERIAL that ray passed through - not how far
  away the surface is. '.' none, ':' thin, '#' middling, '@' the most.
  That one detail is what lets you see inside: a hollow part shows a bright
  rim of wall around a dim middle, because the ray through the middle crosses
  only two thin walls while the ray through the rim travels along them.

  FRONT is the elevation: check height against width, and the outline.
  SIDE shows depth: a part that looks identical in FRONT and SIDE is
       rotationally symmetric - right for a mug, wrong for a bracket.
  TOP is the footprint: check symmetry, and where the holes sit.

  Read them in this order:
    - How many separate masses do you count? It should equal your part count.
    - Is the widest point where you intended it?
    - Is the part hollow where it should be hollow? Look for the bright rim
      and dim centre. A uniformly bright mass means your cavity never got
      made - the most common silent failure there is.
    - Is anything floating, with nothing beneath it?
    - Compare the printed extents under each view against the three
      dimensions you committed to. Numbers beat impressions.

  The views cannot tell you about surface finish, colour or beauty. They can
  tell you whether you built the object you described. That is what they are
  for.

THE LANGUAGE (complete - there is nothing else)

Every design is a plain-text document. One statement per line. Two-space
indent puts a shape inside the profile above it. '#' starts a comment.

  design "<name>" units=mm          first line, once

  profile <name>                    a 2D shape, the source of all 3D
    <shape>                           exactly one outer shape
    hole <shape>                      zero or more holes inside it

  solid <name> from=<profile> extrude h=<mm> [taper=<deg>] [twist=<deg>]
  solid <name> from=<profile> revolve [angle=<deg>] [segments=<n>]
  solid <name> loft from=<profA> to=<profB> h=<mm>

  assembly                          optional; omit it and every solid sits at 0
    part <solid> [at=(x,y,z)] [rot=(rx,ry,rz)] [scale=<n>] [mirror=x|y|z]

  note "<text>"                     record a decision you made on purpose

SHAPES usable as an outer shape or as a hole
  circle  r=<mm>           or d=<mm>
  ellipse rx=<mm> ry=<mm>
  rect    w=<mm> h=<mm>    [r=<corner radius>]
  slot    w=<mm> h=<mm>              fully rounded ends
  ngon    n=<sides> r=<mm>
  poly    (x,y) (x,y) (x,y) ...      closed outline, counter-clockwise
  path    (x,y) (x,y) ...            OPEN polyline, only for revolve
  all shapes also take  at=(x,y)  rot=<deg>  segments=<n>

VALUES
  numbers are millimetres          40        40mm      2.5
  design tokens                   $wall.std    $m3.hole    $clear.slip
  arithmetic                      40-$wall.std*2        (0,0) (40-2.4,0)

THE THREE OPERATIONS, AND WHEN EACH ONE IS RIGHT
  extrude   the shape is constant along one straight direction.
            brackets, plates, enclosures, keycaps, gears, signs, boxes.
            taper gives draft angle; twist gives a spiral column.
  revolve   the object has one axis of rotational symmetry.
            mugs, vases, bottles, bowls, knobs, wheels, lampshades, lenses.
            angle<360 leaves a wedge, useful for cutaway views.
  loft      the cross-section changes from one shape to another.
            adapters, funnels, tapered handles, round-to-square transitions.

HOLLOW OBJECTS - the single most important technique
  There is no 3D boolean subtract. You get hollow parts by putting the void
  into the 2D profile, which is more reliable and always watertight.

  For extrude: give the profile a hole.
      profile tube
        circle r=20
        hole circle r=20-$wall.std      # a 2.4mm-walled tube
      solid body from=tube extrude h=60

  For revolve: walk the pen around the whole cross-section. A revolve always
  spins about the vertical axis Z; in its profile x is the RADIUS and y is the
  HEIGHT, and x=0 is the spin axis. To lay the result on its side, rotate the
  part in the assembly: part body rot=(0,90,0).
  Go up the outside, across the rim, down the inside, then back to the axis:
      profile mug_section
        path (0,0) (40,0) (40,95) (40-$wall.std,95) (40-$wall.std,$wall.std*2) (0,$wall.std*2)
      solid body from=mug_section revolve segments=72
  That is a mug: 80mm across, 95mm tall, 2.4mm wall, 4.8mm floor.

ASSEMBLY IS PLACEMENT, NOT FUSION
  Parts are positioned next to each other, never merged. Two parts that
  occupy the same space are reported as an error of intent, not fused into
  one shape. So: everything that must be ONE piece of material must come
  from ONE profile. Use the assembly for things that really are separate
  pieces - a lid and a box, a handle pinned to a body, a repeated boss.

DIMENSION ANCHORS - use these so your sizes are believable
  Hand: palm width 85, comfortable grip diameter 30-45, finger thickness 18.
  Mug: 80 dia x 95 tall, handle opening 35 x 55, wall 3 when printed.
  Phone 160 x 75 x 8.  Credit card 85.6 x 54.  AA cell 14.5 dia x 50.5.
  Pen 9 dia x 140.  Water bottle 70 dia x 250.  Mouse 115 x 62 x 38.
  USB-C cutout 9.0 x 3.2.  M3 screw: 3.4 hole, 6.0 head.  Wall plate 70 x 115.
  Chair seat 450 high, 400 deep.  Desk 730 high.  Door handle 1000 high.
  Print bed 220 x 220 x 250 - anything larger must be split.

  State the overall size in millimetres BEFORE writing any line of the design.
  An object that comes back 9mm tall when you wanted 95 is the most common
  failure of all, and the MEASUREMENTS block will catch it.

THE SIX FAILURES THAT ACTUALLY HAPPEN, AND WHAT CAUSES THEM
  "part is not a closed solid"      a poly whose points double back and cross,
                                    or an extrude pointed at an open path.
                                    Simplify the outline, keep it convex if
                                    you can, run the points once around.
  "wall ... too thin to print"      you chose the inner and outer size
                                    independently. Derive one from the other:
                                    hole circle r=20-$wall.std
  "parts interpenetrate"            you expected assembly to fuse or subtract.
                                    It does neither. Move the part, or rebuild
                                    the shape as one profile with holes.
  "whole design is only 9mm"        you thought in centimetres or in no unit
                                    at all. Everything is millimetres.
  TOP view is a solid disc          your revolve path never came back down the
                                    inside, so you made a plug, not a vessel.
  "floats above z=0"                an at=(x,y,z) with a z you did not intend,
                                    usually copied from another part.

HOW TO REPLY TO THIS REPORT
  1. Fix ERRORS first, exactly as the FIX lines say. Change nothing else.
  2. Then judge the WARNINGS. Each one is a real risk, but you may accept one
     on purpose - if you do, say why in a `note` line.
  3. Compare the VIEWS against what you intended. The views are the only way
     you can see this object. Ask yourself, for each view: is the silhouette
     the right shape, is the proportion right, is anything missing?
  4. Re-send the WHOLE design inside <<<DESIGN ... >>>. Never send a fragment
     or a patch - the compiler reads the full document every time.
  5. Change one thing per round. If two rounds in a row make it worse, go back
     to the last version that compiled and take a smaller step.
