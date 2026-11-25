---
name: DesignBridge CAD·OS
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#554336'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#887364'
  outline-variant: '#dbc2b0'
  surface-tint: '#904d00'
  primary: '#8d4b00'
  on-primary: '#ffffff'
  primary-container: '#b15f00'
  on-primary-container: '#fffbff'
  inverse-primary: '#ffb77d'
  secondary: '#5b5e68'
  on-secondary: '#ffffff'
  secondary-container: '#dedfeb'
  on-secondary-container: '#60626c'
  tertiary: '#006948'
  on-tertiary: '#ffffff'
  tertiary-container: '#00855d'
  on-tertiary-container: '#f5fff7'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffdcc3'
  primary-fixed-dim: '#ffb77d'
  on-primary-fixed: '#2f1500'
  on-primary-fixed-variant: '#6e3900'
  secondary-fixed: '#e0e2ed'
  secondary-fixed-dim: '#c4c6d1'
  on-secondary-fixed: '#181b24'
  on-secondary-fixed-variant: '#444650'
  tertiary-fixed: '#85f8c4'
  tertiary-fixed-dim: '#68dba9'
  on-tertiary-fixed: '#002114'
  on-tertiary-fixed-variant: '#005137'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
  amber-core: '#d97706'
  amber-rich: '#b45309'
  amber-glow: '#f59e0b'
  amber-subtle: '#fef3c7'
  emerald-core: '#059669'
  emerald-glow: '#10b981'
  emerald-subtle: '#d1fae5'
  stage-bg: '#0a0b0e'
  ceramic-surface: rgba(255, 255, 255, 0.92)
  ceramic-border: rgba(226, 230, 240, 0.95)
  well-bg: '#edf1f7'
  visor-crimson: '#dc2626'
typography:
  headline-lg:
    fontFamily: Geist
    fontSize: 1.25rem
    fontWeight: '700'
    lineHeight: 1.5rem
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Geist
    fontSize: 0.875rem
    fontWeight: '700'
    lineHeight: 1.25rem
    letterSpacing: -0.01em
  body-md:
    fontFamily: Geist
    fontSize: 0.75rem
    fontWeight: '400'
    lineHeight: 1.125rem
  label-lg:
    fontFamily: JetBrains Mono
    fontSize: 0.75rem
    fontWeight: '700'
    lineHeight: 1rem
    letterSpacing: 0.02em
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 0.6875rem
    fontWeight: '600'
    lineHeight: 0.875rem
    letterSpacing: 0.04em
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 0.625rem
    fontWeight: '600'
    lineHeight: 0.75rem
    letterSpacing: 0.06em
  label-xs:
    fontFamily: JetBrains Mono
    fontSize: 0.5rem
    fontWeight: '700'
    lineHeight: 0.625rem
    letterSpacing: 0.08em
rounded:
  sm: 0.5rem
  DEFAULT: 1rem
  md: 1.5rem
  lg: 2rem
  xl: 3rem
  full: 9999px
spacing:
  gutter: 1rem
  margin: 1.5rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.25rem
---

## Brand & Style

DesignBridge CAD·OS combines **Tactile Skeuomorphism** with precision **Developer Glassmorphism**. Inspired by high-end physical synthesizers, CNC milling consoles, and ceramic aerospace instrumentation, the interface presents functional software controls as machined physical artifacts: floating ceramic pods, recessed debossed wells, extruded tumbler keys, and glowing LED bead telemetry.

The aesthetic balances clean, hyper-refined ceramic white surfaces with precise technical amber instrumentation and emerald state emitters. It evokes extreme engineering fidelity, tactile satisfaction, and spatial depth, transforming abstract CAD compiler loops into tangible physical deck controls.

## Colors

The palette establishes high tactile contrast between a deep, atmospheric dark 3D canvas backdrop (`#0a0b0e`) and ultra-clean, high-density floating ceramic modules (`rgba(255, 255, 255, 0.92)`).

- **Primary (Amber Instrumentation)**: `#d97706` is the central action, telemetry, and syntax highlight hue. Augmented by `#f59e0b` for luminescent glows and `#fef3c7` for recessed amber chip containers.
- **Secondary (Machined Obsidian / Slate)**: `#14171f` serves as the primary high-legibility text tone, paired with `#64748b` for monospaced technical metadata and label tiers.
- **Tertiary (Verification Emerald)**: `#059669` and `#10b981` communicate watertight geometry status, successful compiler loops, and active live states.
- **Mechanical Wells**: `#edf1f7` forms the debossed interior cavity for inputs and syntax terminal beds.

## Typography

The typography reinforces precision instrumentation:
- **Geist** delivers crisp structural branding, headers, and tool identifiers.
- **JetBrains Mono** governs all operative feedback, code matrices, coordinate feeds, telemetry readouts, and button caps.
- Strict uppercase formatting with exaggerated tracking (`0.06em` to `0.08em`) is applied to micro-telemetry and sub-labels at sizes from `8px` to `10px` (`label-xs` to `label-sm`), guaranteeing extreme legibility inside tight hardware capsules.

## Layout & Spacing

The interface employs a **Viewport Peripheral Overlay Model** rather than a rigid document grid:
- **Canvas Backdrop**: A 100vw × 100vh spatial viewport anchors 3D modeling work, overlaid with center crosshairs and peripheral radial vignetting.
- **HUD Shell Anchors**: Fixed floating capsules anchor along four cardinal quadrants:
  - **Top Bar**: Master telemetry capsule and status pill (fixed at `top: 1.25rem`).
  - **Left Satellite**: Cartridge bank and revision timeline (`w: 240px`, pinned at `left: 1.5rem`).
  - **Right Control Slab**: CAD prompt synthesizer and code compiler terminal (`w: 420px`, pinned at `right: 1.5rem`).
  - **Bottom Dock**: Scrubber jog-wheel, camera discs, and file export console (`h: 56px`, pinned at `bottom: 1.25rem`).
- Internal component rhythm relies on an ultra-compact `4px` / `8px` metric ladder to preserve maximum central 3D viewport territory.

## Elevation & Depth

Visual hierarchy uses physical debossed wells, frosted backdrops, and multi-tier directional drop shadows:

1. **Floating Ceramic Pod Level**:
   - `box-shadow: 0 20px 40px -10px rgba(15, 23, 42, 0.12), 0 8px 16px -4px rgba(15, 23, 42, 0.08), inset 0 1.5px 1px 0 rgba(255, 255, 255, 0.95), inset 0 -1.5px 1px 0 rgba(0, 0, 0, 0.04);`
   - Paired with `backdrop-filter: blur(20px)` and an opaque porcelain border (`border: 1px solid rgba(226, 230, 240, 0.95)`).
2. **Tactile Extruded Discs & Keycaps**:
   - Raised keys utilize top specular highlights and bottom micro-shading:
   - `box-shadow: 0 4px 10px rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.04), inset 0 1px 1px rgba(255, 255, 255, 0.95), inset 0 -1.5px 0.5px rgba(0, 0, 0, 0.12);`
   - Active click state: `transform: translateY(1.5px)` with `box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.22)`.
3. **Debossed Cavities (Wells)**:
   - Recessed terminal containers, progress bars, and token bays use inverse inner bevels:
   - `box-shadow: inset 0 2px 5px rgba(0, 0, 0, 0.09), inset 0 1px 2px rgba(0, 0, 0, 0.06), inset 0 -1px 0 rgba(255, 255, 255, 0.9);`
4. **Luminescent Amber Accents**:
   - Primary buttons and indicator diodes emit amber glow cascades:
   - `box-shadow: 0 6px 16px -2px rgba(217, 119, 6, 0.4), inset 0 1.5px 1px rgba(255, 255, 255, 0.6);`

## Shapes

The design system utilizes continuous pill curvatures and generous rounded radii to emulate machined medical and audio hardware:
- **Master Outer Slabs**: Sculpted rounded capsules at `rounded-[32px]`.
- **Primary Buttons, Toolcaps, and HUD Pills**: Full pill geometry (`rounded-full` / `9999px`).
- **Tactile Key Discs**: Uniform circular buttons (`w-7 h-7` to `w-9 h-9`, `rounded-full`).
- **Internal Card Enclosures & Diagnostic Pucks**: Continuous smooth radii (`rounded-2xl` / `16px`).
- **Code & Syntax Wells**: Contained inner rounded corners (`rounded-xl` / `12px`).

## Components

### Buttons & Keycaps
- **Tactile Primary (Amber Extrusion)**: Pill-shaped, gradient from `#f59e0b` to `#b45309`, white bold monospace text, inset white top-light bevel, and glowing amber ambient shadow. On press, depresses by `1.5px`.
- **Machined Ceramic Discs**: Circular (`w-8 h-8` or `w-7 h-7`), gradient from white to neutral-200 with border, housing Material Symbol icons (`15px` to `18px`).
- **Segmented Tumbler Switches**: Pill housing containing a recessed track with white tactile thumb buttons switching between states (e.g. LIGHT / DARK).

### Recessed Wells & Input Fields
- **Syntax & DSL Wells**: Recessed `#edf1f7` background, debossed inner drop shadow, monospaced `10px` code styling with amber syntax highlights (`//`, `<<<`, `>>>`).
- **Text Inputs & Areas**: Debossed well background, rounded pill or `rounded-xl` bounds, no default outline, focused with a crisp `border-amber-core` halo.

### Telemetry Pills & Chips
- **Status Capsules**: Micro pill badges (`text-[9px]`, uppercase JetBrains Mono, border with low opacity color tint).
- **LED Diodes**: `w-2 h-2` to `w-2.5 h-2.5` circular elements with radial box-shadow bloom (Emerald `#10b981` or Amber `#f59e0b`), accompanied by CSS pulse animations.
- **Segmented Token Gauges**: Micro pill columns (`w-1.5 h-3`) filling sequentially from dark amber to glow amber to represent budget usage.

### Hardware Scrubber & Camera Controls
- **Continuous Timeline Bar**: Recessed debossed track with gradient fill and a circular white bead knob with amber ring border (`group-hover:scale-125`).
- **Camera Disc Cluster**: Segmented recessed capsule housing ISO, FRONT, SIDE, and TOP camera selectors with tactile active states.