<<<DESIGN prop="coffee_mug" unit=mm
cylinder outer r=44 h=96 chamfer_top=1.5
subtract cylinder inner r=40 h=90 offset=(0,6,0)
torus handle r=25 tube=6.5 offset=(44,48,0)
material ceramic color=#27272a roughness=0.35
>>>