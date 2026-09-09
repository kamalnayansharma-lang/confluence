---
name: core-color-primitives
description: Provides the absolute visual source-of-truth palette for the application architecture, cataloging raw color swatches, tint step distributions, and mathematical alpha channels. Use this skill when an agent needs to retrieve global primitive tokens, root palette step scales (Primary, Secondary, Tertiary, Neutrals), operational status colors (Success, Warning, Error), or contrast guardrails to build downstream semantic aliases.
metadata:
  version: "1.0"
---

# 🎨 Core Color Primitives

This markdown document serves as the absolute visual source-of-truth palette for the application architecture. It catalogs the raw color swatches, tint step distributions, and mathematical alpha channels established within the `Primitive.json` collection file. 

All entries in this layer represent **Global / Primitive Tokens**. They are structurally independent of product application contexts or specific execution intents; they exist solely to establish raw color values and contrast availability scales.

---

## 1. Root Palette Step Scales

### 🟦 Brand Core Accent Systems
These scales represent the definitive mathematical distributions of primary visual energy across the design system layout frames.

#### Primary Scale (Deep Sapphire Blue)
* **Visual Intent:** Rich, stable sapphire scales optimized for core layout structural weighting and strong visual hierarchy anchoring.
* **Values:**
  * `Colors/Primary/Primary-50`: `#F8F7FF` | RGB: `0.973, 0.971, 1.000` | Alpha: `1.0`
  * `Colors/Primary/Primary-100`: `#F1F0FF` | RGB: `0.945, 0.941, 1.000` | Alpha: `1.0`
  * `Colors/Primary/Primary-200`: `#DDE1FF` | RGB: `0.867, 0.882, 1.000` | Alpha: `1.0`
  * `Colors/Primary/Primary-300`: `#A4A9FF` | RGB: `0.643, 0.663, 1.000` | Alpha: `1.0`
  * `Colors/Primary/Primary-400`: `#898EED` | RGB: `0.537, 0.557, 0.933` | Alpha: `1.0`
  * `Colors/Primary/Primary-500`: `#6F74D2` | RGB: `0.435, 0.455, 0.824` | Alpha: `1.0`
  * `Colors/Primary/Primary-600`: `#555BB6` | RGB: `0.333, 0.357, 0.714` | Alpha: `1.0`
  * `Colors/Primary/Primary-700`: `#3C439B` | RGB: `0.235, 0.263, 0.608` | Alpha: `1.0`
  * `Colors/Primary/Primary-800`: `#202B81` | RGB: `0.125, 0.169, 0.506` | Alpha: `1.0`
  * `Colors/Primary/Primary-900`: `#081160` | RGB: `0.031, 0.067, 0.376` | Alpha: `1.0`

#### Secondary Scale (Sky Blue)
* **Visual Intent:** High-luminance aerial cyan-blue variants designed to emphasize action components without overpowering main content layouts.
* **Values:**
  * `Colors/Secondary/Secondary-50`: `#F1F7FF` | RGB: `0.945, 0.970, 1.000` | Alpha: `1.0`
  * `Colors/Secondary/Secondary-100`: `#EBF4FF` | RGB: `0.922, 0.957, 1.000` | Alpha: `1.0`
  * `Colors/Secondary/Secondary-200`: `#D1E9FF` | RGB: `0.820, 0.914, 1.000` | Alpha: `1.0`
  * `Colors/Secondary/Secondary-300`: `#A3D1FF` | RGB: `0.639, 0.820, 1.000` | Alpha: `1.0`
  * `Colors/Secondary/Secondary-400`: `#74BEFF` | RGB: `0.455, 0.745, 1.000` | Alpha: `1.0`
  * `Colors/Secondary/Secondary-500`: `#4EA0EF` | RGB: `0.306, 0.627, 0.937` | Alpha: `1.0`
  * `Colors/Secondary/Secondary-600`: `#3182CE` | RGB: `0.192, 0.510, 0.808` | Alpha: `1.0`
  * `Colors/Secondary/Secondary-700`: `#1E68AD` | RGB: `0.118, 0.408, 0.678` | Alpha: `1.0`
  * `Colors/Secondary/Secondary-800`: `#004D8C` | RGB: `0.000, 0.302, 0.549` | Alpha: `1.0`
  * `Colors/Secondary/Secondary-900`: `#00335F` | RGB: `0.000, 0.200, 0.373` | Alpha: `1.0`

#### Tertiary Scale (Teal & Turquoise Matrix)
* **Visual Intent:** Vivid emerald-infused blues that add premium structural flair and highlight non-standard visual modules.
* **Values:**
  * `Colors/Tertiary/Tertiary-50`: `#ECFDFD` | RGB: `0.925, 0.990, 0.990` | Alpha: `1.0`
  * `Colors/Tertiary/Tertiary-100`: `#E0FBFB` | RGB: `0.878, 0.984, 0.984` | Alpha: `1.0`
  * `Colors/Tertiary/Tertiary-200`: `#BEF7F7` | RGB: `0.745, 0.969, 0.969` | Alpha: `1.0`
  * `Colors/Tertiary/Tertiary-300`: `#90F1F1` | RGB: `0.564, 0.945, 0.945` | Alpha: `1.0`
  * `Colors/Tertiary/Tertiary-400`: `#5EE8E8` | RGB: `0.369, 0.910, 0.910` | Alpha: `1.0`
  * `Colors/Tertiary/Tertiary-500`: `#0DD3D3` | RGB: `0.051, 0.827, 0.827` | Alpha: `1.0`
  * `Colors/Tertiary/Tertiary-600`: `#00B3B3` | RGB: `0.000, 0.702, 0.702` | Alpha: `1.0`
  * `Colors/Tertiary/Tertiary-700`: `#009494` | RGB: `0.000, 0.580, 0.580` | Alpha: `1.0`
  * `Colors/Tertiary/Tertiary-800`: `#007373` | RGB: `0.000, 0.451, 0.451` | Alpha: `1.0`
  * `Colors/Tertiary/Tertiary-900`: `#005050` | RGB: `0.000, 0.314, 0.314` | Alpha: `1.0`

---

### 🧱 Core Architectural Neutral Systems
These variables form the foundational layers for screens, boxes, line segments, frames, and type configurations.

#### Light Warm Neutrals (Ivory)
* **Visual Intent:** Sophisticated, low-saturation cream white profiles to reduce screen glare and add warmth to light layouts.
* **Values:**
  * `Colors/Light/Ivory-50`: `#FFFFEF` | RGB: `1.000, 1.000, 0.937` | Alpha: `1.0`
  * `Colors/Light/Ivory-100`: `#FFFFE6` | RGB: `1.000, 0.996, 0.902` | Alpha: `1.0`
  * `Colors/Light/Ivory-200`: `#E8E7CD` | RGB: `0.910, 0.906, 0.804` | Alpha: `1.0`
  * `Colors/Light/Ivory-300`: `#D2D1B6` | RGB: `0.824, 0.820, 0.714` | Alpha: `1.0`
  * `Colors/Light/Ivory-400`: `#BBBA9F` | RGB: `0.733, 0.729, 0.624` | Alpha: `1.0`
  * `Colors/Light/Ivory-500`: `#A4A389` | RGB: `0.643, 0.639, 0.537` | Alpha: `1.0`
  * `Colors/Light/Ivory-600`: `#8E8D73` | RGB: `0.557, 0.553, 0.451` | Alpha: `1.0`
  * `Colors/Light/Ivory-700`: `#77765F` | RGB: `0.467, 0.463, 0.373` | Alpha: `1.0`
  * `Colors/Light/Ivory-800`: `#605E4B` | RGB: `0.376, 0.373, 0.294` | Alpha: `1.0`
  * `Colors/Light/Ivory-900`: `#4A4938` | RGB: `0.290, 0.286, 0.220` | Alpha: `1.0`

#### Universal Cool Neutrals (Grey Palette)
* **Visual Intent:** Balanced slate greys configured to cleanly anchor borders, inputs, and dark type configurations.
* **Values:**
  * `Colors/Grey/Grey-50`: `#F9FAFC` | RGB: `0.976, 0.980, 0.984` | Alpha: `1.0`
  * `Colors/Grey/grey-100`: `#F3F4F6` | RGB: `0.953, 0.957, 0.965` | Alpha: `1.0`
  * `Colors/Grey/grey-200`: `#E5E7EA` | RGB: `0.898, 0.906, 0.918` | Alpha: `1.0`
  * `Colors/Grey/grey-300`: `#D2D5DB` | RGB: `0.823, 0.835, 0.859` | Alpha: `1.0`
  * `Colors/Grey/grey-400`: `#9EA2AE` | RGB: `0.620, 0.635, 0.682` | Alpha: `1.0`
  * `Colors/Grey/grey-500`: `#6D717F` | RGB: `0.427, 0.443, 0.498` | Alpha: `1.0`
  * `Colors/Grey/grey-600`: `#4D5461` | RGB: `0.302, 0.329, 0.380` | Alpha: `1.0`
  * `Colors/Grey/grey-700`: `#394050` | RGB: `0.224, 0.251, 0.314` | Alpha: `1.0`
  * `Colors/Grey/grey-800`: `#212936` | RGB: `0.129, 0.161, 0.212` | Alpha: `1.0`
  * `Colors/Grey/grey-900`: `#131927` | RGB: `0.075, 0.098, 0.153` | Alpha: `1.0`

#### Material Framework Neutrals
* **Visual Intent:** Flat, fallback structural greys to map un-themed layout spaces.
* **Values:**
  * `Colors/Neutrals/50`: `#F8F8F8` | RGB: `0.973, 0.973, 0.973` | Alpha: `1.0`
  * `Colors/Neutrals/100`: `#E4DFDF` | RGB: `0.894, 0.878, 0.878` | Alpha: `1.0`
  * `Colors/Neutrals/200`: `#CFC9C9` | RGB: `0.812, 0.788, 0.788` | Alpha: `1.0`
  * `Colors/Neutrals/300`: `#BBB2B2` | RGB: `0.733, 0.698, 0.698` | Alpha: `1.0`
  * `Colors/Neutrals/400`: `#A69C9C` | RGB: `0.651, 0.612, 0.612` | Alpha: `1.0`
  * `Colors/Neutrals/500`: `#928686` | RGB: `0.573, 0.525, 0.525` | Alpha: `1.0`
  * `Colors/Neutrals/600`: `#7E7272` | RGB: `0.494, 0.447, 0.447` | Alpha: `1.0`
  * `Colors/Neutrals/700`: `#695D5D` | RGB: `0.412, 0.365, 0.365` | Alpha: `1.0`
  * `Colors/Neutrals/800`: `#554A4A` | RGB: `0.333, 0.290, 0.290` | Alpha: `1.0`
  * `Colors/Neutrals/900`: `#403737` | RGB: `0.251, 0.216, 0.216` | Alpha: `1.0`

---

### 🔘 Absolute Alpha Transparency Chains
These paths define solid templates mixed with exact mathematical alpha variations. They ensure reliable scrim shadows and element hover states across variables.

#### Pure White Transparency Scale (`#FFFFFF`)
* `Colors/White/White-50`: RGB: `1.000, 1.000, 1.000` | Alpha: `0.10`
* `Colors/White/White-100`: RGB: `1.000, 1.000, 1.000` | Alpha: `0.20`
* `Colors/White/White-200`: RGB: `1.000, 1.000, 1.000` | Alpha: `0.30`
* `Colors/White/White-300`: RGB: `1.000, 1.000, 1.000` | Alpha: `0.40`
* `Colors/White/White-400`: RGB: `1.000, 1.000, 1.000` | Alpha: `0.50`
* `Colors/White/White-500`: RGB: `1.000, 1.000, 1.000` | Alpha: `0.60`
* `Colors/White/White-600`: RGB: `1.000, 1.000, 1.000` | Alpha: `0.70`
* `Colors/White/White-700`: RGB: `1.000, 1.000, 1.000` | Alpha: `0.80`
* `Colors/White/White-800`: RGB: `1.000, 1.000, 1.000` | Alpha: `0.90`
* `Colors/White/White-900`: RGB: `1.000, 1.000, 1.000` | Alpha: `1.00` *(Solid White)*

#### Pure Black Transparency Scale (`#000000`)
* `Colors/Black/Black-50`: RGB: `0.000, 0.000, 0.000` | Alpha: `0.10`
* `Colors/Black/black-100`: RGB: `0.000, 0.000, 0.000` | Alpha: `0.20`
* `Colors/Black/black-200`: RGB: `0.000, 0.000, 0.000` | Alpha: `0.30`
* `Colors/Black/black-300`: RGB: `0.000, 0.000, 0.000` | Alpha: `0.40`
* `Colors/Black/black-400`: RGB: `0.000, 0.000, 0.000` | Alpha: `0.50`
* `Colors/Black/black-500`: RGB: `0.000, 0.000, 0.000` | Alpha: `0.60`
* `Colors/Black/black-600`: RGB: `0.000, 0.000, 0.000` | Alpha: `0.70`
* `Colors/Black/black-700`: RGB: `0.000, 0.000, 0.000` | Alpha: `0.80`
* `Colors/Black/black-800`: RGB: `0.000, 0.000, 0.000` | Alpha: `0.90`
* `Colors/Black/black-900`: RGB: `0.000, 0.000, 0.000` | Alpha: `1.00` *(Solid Pure Black)*

---

### ⚠️ Functional Contextual Systems
These scales are strictly reserved for communicating operational status, data validations, and diagnostic alerts.

#### Success Scale (Green)
* **Visual Intent:** Fresh, high-signal green variants optimized for verified completions, active status items, and validation checkpoints.
* **Values:**
  * `Colors/Success/Green-50`: `#F7FAF7` | `100`: `#E9F1EC` | `200`: `#CDEDE6` | `300`: `#A1E2B9` | `400`: `#65DD91` | `500`: `#26D968` | `600`: `#22AF56` | `700`: `#208846` | `800`: `#1D6336` | `900`: `#183A24`

#### Warning Scale (Amber Gold)
* **Visual Intent:** Warning scales designed for intermediate validation messages and low-priority system status tags.
* **Values:**
  * `Colors/Warning/50`: `#FAF9F6` | `100`: `#F2EEDF` | `200`: `#EDE0C9` | `300`: `#EC9898` | `400`: `#EFB552` | `500`: `#F59E0A` | `600`: `#C4800D` | `700`: `#986610` | `800`: `#6E4B12` | `900`: `#3F2F12`

#### Error Scale (Crimson Red)
* **Visual Intent:** Alert variables indicating severe errors, invalid states, or destructive actions.
* **Values:**
  * `Colors/Error/50`: `#FAF6F6` | `100`: `#F2E9E9` | `200`: `#ECCBCB` | `300`: `#E89B9B` | `400`: `#E85959` | `500`: `#EB1414` | `600`: `#BC1515` | `700`: `#931616` | `800`: `#6A1616` | `900`: `#3D1414`

---

## 2. Dynamic State Overlays Baseline
This scale forms the middle-tier interaction baseline for default primitive overlays inside the source file:
* `Colors/State Layer/Opacity/primary/Opacity-8`: RGB: `0.031, 0.067, 0.376` | Alpha: `0.08` *(Hover Baseline Input)*
* `Colors/State Layer/Opacity/primary/Opacity-10`: RGB: `0.031, 0.067, 0.376` | Alpha: `0.10` *(Focus Baseline Input)*

---

## 3. Core Operational Guardrails & Guidelines

Derived from Google Stitch open-source documentation parameters, these rules establish the proper usage context for primitive files.

* **✔️ DO** ensure your automated build pipelines (like Style Dictionary) ingest the exact spelling, nested directories, and formatting of path names to avoid parsing breaks.
* **✔️ DO** use the pure alpha scales (`White-` and `Black-`) for custom element lighting overlays or overlay curtains. This allows content below to show through naturally.
* **❌ DON'T** map primitive tokens directly to component shapes, template mockups, or source application code. These entries serve exclusively as the raw color engine for downstream files.
* **❌ DON'T** introduce arbitrary hex updates directly inside downstream mapping files. If a brand color shift is required, it must be added to this source primitive file first.

---

## 4. Web Accessibility (WCAG) Contrast Rationale

When referencing these tokens to build downstream semantic aliases, keep these contrast constraints in mind:
* **Typography Elements:** Any type or label token must ensure a 4.5:1 ratio over light fields. Use step indicators below step `300` for backgrounds, and step indicators above step `700` for type components.
* **Functional Line Divisions:** Grid lines, checkboxes, text fields, and borders require a minimum 3.0:1 contrast difference relative to their surrounding backgrounds to ensure clean visibility.
* **Pipeline Formatting Rule:** Automated parsers treat token strings with strict case-sensitivity. Verify that case distributions (e.g., title-case `Colors/Grey/Grey-50` vs lowercase `Colors/Grey/grey-100`) match exactly to prevent duplicate paths in code compilation.