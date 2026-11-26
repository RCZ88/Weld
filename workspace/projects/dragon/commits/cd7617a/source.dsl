model "azrakul_the_hollow_crown"

bone body pivot=(0,39,0)
  box from=(-13,27,-19) to=(13,51,19) color=#211b25
  box from=(-11,25,-14) to=(11,48,17) color=#34212a
  box from=(-10,39,-17) to=(10,55,13) color=#4b2028
  box from=(-12,47,-12) to=(12,53,12) color=#17151d
  box from=(-9,51,-10) to=(9,56,10) color=#9b292c
  box from=(-7,48,-13) to=(7,59,10) color=#d49a43
  box from=(-4,51,-15) to=(4,62,8) color=#f0c56b

bone chest_plate parent=body pivot=(0,43,14)
  box from=(-11,29,13) to=(11,48,20) color=#8c282c
  box from=(-9,34,17) to=(9,50,22) color=#c34432
  box from=(-7,35,19) to=(7,45,23) color=#d69a48
  box from=(-5,31,20) to=(5,37,24) color=#f0bc61
  box from=(-2,38,21) to=(2,48,24) color=#f8d78a

bone neck parent=body pivot=(0,44,15)
  box from=(-8,40,13) to=(8,54,28) color=#34212a
  box from=(-7,44,19) to=(7,58,32) color=#57242b
  box from=(-6,48,24) to=(6,61,36) color=#70272c
  box from=(-8,54,23) to=(-5,62,31) color=#d39b48
  box from=(5,54,23) to=(8,62,31) color=#d39b48
  box from=(-4,57,29) to=(4,65,37) color=#9e3430

bone head parent=neck pivot=(0,59,34)
  box from=(-9,53,30) to=(9,66,44) color=#392027
  box from=(-8,55,35) to=(8,67,48) color=#65262c
  box from=(-7,53,41) to=(7,61,51) color=#8e302c
  box from=(-7,50,42) to=(7,57,53) color=#bd4431
  box from=(-5,49,47) to=(5,54,58) color=#30202a
  box from=(-6,56,49) to=(6,61,55) color=#d29b4a
  box from=(-3,54,52) to=(3,57,59) color=#17151d
  box from=(-8,60,39) to=(-5,66,48) color=#d2a44e
  box from=(5,60,39) to=(8,66,48) color=#d2a44e
  box from=(-4,58,54) to=(4,62,60) color=#f1c96e
  box from=(-2,51,57) to=(2,53,64) color=#eee0c1
  box from=(-2,48,56) to=(2,51,61) color=#c9b28e

bone left_horn parent=head pivot=(-6,63,39)
  box from=(-9,62,35) to=(-5,68,42) color=#d4b36e
  box from=(-12,65,32) to=(-8,73,39) color=#e2c88b
  box from=(-15,69,28) to=(-11,77,35) color=#a88d61
  box from=(-17,74,24) to=(-13,80,31) color=#eee0c1

bone right_horn parent=head pivot=(6,63,39)
  box from=(5,62,35) to=(9,68,42) color=#d4b36e
  box from=(8,65,32) to=(12,73,39) color=#e2c88b
  box from=(11,69,28) to=(15,77,35) color=#a88d61
  box from=(13,74,24) to=(17,80,31) color=#eee0c1

bone left_eye parent=head pivot=(-8,58,43)
  box from=(-9,58,43) to=(-7,61,47) color=#ff8b24
  box from=(-8,59,46) to=(-7,60,48) color=#fff0a1

bone right_eye parent=head pivot=(8,58,43)
  box from=(7,58,43) to=(9,61,47) color=#ff8b24
  box from=(7,59,46) to=(8,60,48) color=#fff0a1

bone left_cheek_spike parent=head pivot=(-8,56,38)
  box from=(-12,52,34) to=(-8,58,40) color=#a33a30
  box from=(-15,50,32) to=(-11,54,37) color=#e0b86e
  box from=(-17,48,30) to=(-13,52,34) color=#eee0c1

bone right_cheek_spike parent=head pivot=(8,56,38)
  box from=(8,52,34) to=(12,58,40) color=#a33a30
  box from=(11,50,32) to=(15,54,37) color=#e0b86e
  box from=(13,48,30) to=(17,52,34) color=#eee0c1

bone left_wing parent=body pivot=(-11,47,-5)
  box from=(-13,44,-12) to=(-23,48,11) color=#39202a
  box from=(-18,43,-8) to=(-42,46,7) color=#70252c
  box from=(-25,40,-8) to=(-47,43,5) color=#a32e32
  box from=(-39,36,-6) to=(-54,40,3) color=#d19b4c
  box from=(-19,38,-10) to=(-24,65,-7) color=#bd8c4a
  box from=(-24,50,-8) to=(-48,69,-5) color=#d2aa66
  box from=(-42,64,-6) to=(-55,70,-3) color=#eee0c1
  box from=(-52,34,-5) to=(-56,40,0) color=#eee0c1
  box from=(-25,33,-9) to=(-28,45,-6) color=#d1a35c
  box from=(-32,31,-8) to=(-35,41,-5) color=#d1a35c
  box from=(-39,30,-7) to=(-42,38,-4) color=#d1a35c
  box from=(-46,32,-6) to=(-49,38,-3) color=#d1a35c
  box from=(-47,39,-5) to=(-54,44,-2) color=#e4c27c
  box from=(-53,39,-4) to=(-59,42,0) color=#eee0c1

bone right_wing parent=body pivot=(11,47,-5)
  box from=(13,44,-12) to=(23,48,11) color=#39202a
  box from=(18,43,-8) to=(42,46,7) color=#70252c
  box from=(25,40,-8) to=(47,43,5) color=#a32e32
  box from=(39,36,-6) to=(54,40,3) color=#d19b4c
  box from=(19,38,-10) to=(24,65,-7) color=#bd8c4a
  box from=(24,50,-8) to=(48,69,-5) color=#d2aa66
  box from=(42,64,-6) to=(55,70,-3) color=#eee0c1
  box from=(52,34,-5) to=(56,40,0) color=#eee0c1
  box from=(25,33,-9) to=(28,45,-6) color=#d1a35c
  box from=(32,31,-8) to=(35,41,-5) color=#d1a35c
  box from=(39,30,-7) to=(42,38,-4) color=#d1a35c
  box from=(46,32,-6) to=(49,38,-3) color=#d1a35c
  box from=(47,39,-5) to=(54,44,-2) color=#e4c27c
  box from=(53,39,-4) to=(59,42,0) color=#eee0c1

bone left_front_leg parent=body pivot=(-9,34,11)
  box from=(-13,18,8) to=(-6,37,17) color=#35212a
  box from=(-14,13,10) to=(-5,25,18) color=#68262c
  box from=(-13,6,11) to=(-6,18,19) color=#9c3832
  box from=(-14,2,8) to=(-4,8,20) color=#29202a
  box from=(-15,0,10) to=(-3,5,22) color=#bd913e
  box from=(-14,1,18) to=(-10,3,25) color=#eee0c1
  box from=(-9,1,18) to=(-5,3,25) color=#eee0c1
  box from=(-14,8,13) to=(-11,19,17) color=#d1a35c

bone right_front_leg parent=body pivot=(9,34,11)
  box from=(6,18,8) to=(13,37,17) color=#35212a
  box from=(5,13,10) to=(14,25,18) color=#68262c
  box from=(6,6,11) to=(13,18,19) color=#9c3832
  box from=(4,2,8) to=(14,8,20) color=#29202a
  box from=(3,0,10) to=(15,5,22) color=#bd913e
  box from=(10,1,18) to=(14,3,25) color=#eee0c1
  box from=(5,1,18) to=(9,3,25) color=#eee0c1
  box from=(11,8,13) to=(14,19,17) color=#d1a35c

bone left_back_leg parent=body pivot=(-9,34,-12)
  box from=(-14,16,-19) to=(-6,38,-8) color=#35212a
  box from=(-16,10,-20) to=(-5,25,-10) color=#68262c
  box from=(-14,5,-21) to=(-6,16,-11) color=#9c3832
  box from=(-15,2,-23) to=(-4,8,-12) color=#29202a
  box from=(-16,0,-24) to=(-3,5,-10) color=#bd913e
  box from=(-15,1,-23) to=(-11,3,-27) color=#eee0c1
  box from=(-9,1,-23) to=(-5,3,-27) color=#eee0c1
  box from=(-15,8,-19) to=(-12,18,-15) color=#d1a35c

bone right_back_leg parent=body pivot=(9,34,-12)
  box from=(6,16,-19) to=(14,38,-8) color=#35212a
  box from=(5,10,-20) to=(16,25,-10) color=#68262c
  box from=(6,5,-21) to=(14,16,-11) color=#9c3832
  box from=(4,2,-23) to=(15,8,-12) color=#29202a
  box from=(3,0,-24) to=(16,5,-10) color=#bd913e
  box from=(11,1,-23) to=(15,3,-27) color=#eee0c1
  box from=(5,1,-23) to=(9,3,-27) color=#eee0c1
  box from=(12,8,-19) to=(15,18,-15) color=#d1a35c

bone tail_base parent=body pivot=(0,38,-16)
  box from=(-8,33,-19) to=(8,44,-34) color=#442129
  box from=(-7,31,-29) to=(7,42,-43) color=#77272d
  box from=(-6,32,-39) to=(6,41,-52) color=#a33a30
  box from=(-5,33,-48) to=(5,40,-60) color=#c18c43
  box from=(-4,35,-56) to=(4,39,-65) color=#eee0c1

bone tail_mid parent=tail_base pivot=(0,37,-37)
  box from=(-6,32,-38) to=(6,41,-52) color=#77272d
  box from=(-5,33,-49) to=(5,40,-62) color=#a33a30
  box from=(-4,34,-60) to=(4,39,-71) color=#d0a052
  box from=(-3,35,-69) to=(3,38,-78) color=#eee0c1
  box from=(-8,36,-43) to=(-4,44,-48) color=#d6aa61
  box from=(4,36,-43) to=(8,44,-48) color=#d6aa61

bone tail_tip parent=tail_mid pivot=(0,36,-65)
  box from=(-4,33,-62) to=(4,40,-72) color=#8e302c
  box from=(-5,34,-70) to=(5,39,-78) color=#b28c55
  box from=(-4,35,-76) to=(4,38,-85) color=#eee0c1
  box from=(-3,35,-83) to=(3,38,-91) color=#d4b36e
  box from=(-2,35,-89) to=(2,37,-97) color=#eee0c1

bone dorsal_spikes parent=body pivot=(0,52,-5)
  box from=(-3,51,-17) to=(3,60,-11) color=#d4b36e
  box from=(-3,53,-10) to=(3,63,-4) color=#eee0c1
  box from=(-3,52,-2) to=(3,62,4) color=#d4b36e
  box from=(-3,52,6) to=(3,62,12) color=#eee0c1
  box from=(-3,51,13) to=(3,59,18) color=#d4b36e
  box from=(-2,58,-16) to=(2,66,-12) color=#eee0c1
  box from=(-2,61,-8) to=(2,69,-4) color=#eee0c1
  box from=(-2,60,0) to=(2,68,4) color=#eee0c1
  box from=(-2,59,8) to=(2,66,12) color=#eee0c1

bone left_shoulder_spikes parent=body pivot=(-11,45,2)
  box from=(-17,44,-4) to=(-11,52,5) color=#a33a30
  box from=(-20,48,-3) to=(-14,57,4) color=#d4b36e
  box from=(-22,54,-2) to=(-17,61,3) color=#eee0c1
  box from=(-16,39,5) to=(-11,47,11) color=#8e302c
  box from=(-19,43,7) to=(-14,51,12) color=#eee0c1

bone right_shoulder_spikes parent=body pivot=(11,45,2)
  box from=(11,44,-4) to=(17,52,5) color=#a33a30
  box from=(14,48,-3) to=(20,57,4) color=#d4b36e
  box from=(17,54,-2) to=(22,61,3) color=#eee0c1
  box from=(11,39,5) to=(16,47,11) color=#8e302c
  box from=(14,43,7) to=(19,51,12) color=#eee0c1

bone left_fang parent=head pivot=(-5,53,51)
  box from=(-6,47,49) to=(-3,53,53) color=#eee0c1
  box from=(-5,44,50) to=(-3,49,53) color=#d8c9a8

bone right_fang parent=head pivot=(5,53,51)
  box from=(3,47,49) to=(6,53,53) color=#eee0c1
  box from=(3,44,50) to=(5,49,53) color=#d8c9a8

bone left_forearm_blade parent=left_front_leg pivot=(-10,17,13)
  box from=(-16,13,12) to=(-12,26,16) color=#8e302c
  box from=(-19,19,11) to=(-15,29,15) color=#d4b36e
  box from=(-21,25,10) to=(-17,33,14) color=#eee0c1

bone right_forearm_blade parent=right_front_leg pivot=(10,17,13)
  box from=(12,13,12) to=(16,26,16) color=#8e302c
  box from=(15,19,11) to=(19,29,15) color=#d4b36e
  box from=(17,25,10) to=(21,33,14) color=#eee0c1

animation "ancient_awakening" loop=false
  key body t=0 rot=(0,0,0)
  key body t=0.8 rot=(-5,0,0)
  key body t=1.5 rot=(0,0,0)
  key neck t=0 rot=(0,0,0)
  key neck t=0.8 rot=(18,0,0)
  key neck t=1.5 rot=(0,0,0)
  key head t=0 rot=(0,0,0)
  key head t=0.8 rot=(-20,0,0)
  key head t=1.5 rot=(0,0,0)
  key left_wing t=0 rot=(0,0,0)
  key left_wing t=0.7 rot=(0,0,-65)
  key left_wing t=1.5 rot=(0,0,0)
  key right_wing t=0 rot=(0,0,0)
  key right_wing t=0.7 rot=(0,0,65)
  key right_wing t=1.5 rot=(0,0,0)
  key tail_base t=0 rot=(0,0,0)
  key tail_base t=0.5 rot=(0,-20,0)
  key tail_base t=1 rot=(0,20,0)
  key tail_base t=1.5 rot=(0,0,0)

animation "crown_of_ruin" loop=true
  key body t=0 rot=(0,0,0)
  key body t=1.2 rot=(2,0,0)
  key body t=2.4 rot=(0,0,0)
  key head t=0 rot=(0,0,0)
  key head t=1.2 rot=(0,4,0)
  key head t=2.4 rot=(0,-4,0)
  key left_wing t=0 rot=(0,0,-5)
  key left_wing t=1.2 rot=(0,0,-18)
  key left_wing t=2.4 rot=(0,0,-5)
  key right_wing t=0 rot=(0,0,5)
  key right_wing t=1.2 rot=(0,0,18)
  key right_wing t=2.4 rot=(0,0,5)
  key tail_base t=0 rot=(0,-8,0)
  key tail_base t=1.2 rot=(0,8,0)
  key tail_base t=2.4 rot=(0,-8,0)
  key tail_mid t=0 rot=(0,8,0)
  key tail_mid t=1.2 rot=(0,-8,0)
  key tail_mid t=2.4 rot=(0,8,0)