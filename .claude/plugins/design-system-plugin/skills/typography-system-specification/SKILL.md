---
name: typography-system-specification
description: Serves as the absolute visual and technical source of truth for the product's typography system. It details the Dual-Font Track System (Exo and Gabarito) and composite application criteria. Use this skill when an agent needs to retrieve global typographic principles, variant token configurations, atomic property blueprints (fontFamily, size, weight, line-height), layout context assignments, or WCAG compliance parameters.
metadata:
  version: "1.0"
---

# 📐 Typography System Specification

This markdown document serves as the absolute visual and technical source of truth for our product's typography system. It catalogs the atomic typographic properties established in `Mode 1.tokens 2.json` and details their composite application criteria found in `Neurealm-Typography.json`.

Our design language relies on a **Dual-Font Track System** designed to establish clean reading scales, structural visual hierarchies, and context-aware reading lanes across screen orientations.

---

## 🏗️ Global Typographic Principles & Core Guardrails

### 🟩 The Do's
* **DO** select text styles exclusively from the `Primary-EXO` track for consumer-facing features, product overviews, marketing layouts, and dashboard screens.
* **DO** swap smoothly to the `Secondary-Gabarito` track inside technical side panes, data grids, tabular layouts, and deep operational metrics lists.
* **DO** pair header typography with body and label styles that yield a clear size distribution step of at least 4px to maintain strong reading hierarchy.

### 🟥 The Don't's
* **DON'T** use `Display` or `Headline` text variant classes for long-form paragraph body blocks, as high-character density in large sizes causes severe reader eye strain.
* **DON'T** apply custom tracking modifications or force manual line-height overrides onto standard canvas frames; components must dynamically consume the exact pixel dimensions locked in this specification file.
* **DON'T** use `Label Small` styles or sizes below 11px for major instructions or validation alert messages.

---

## 📱 Typographic Variant Specifications

---

### 🌟 1. Display Variants
Highly expressive, extra-large type sets reserved for hero metrics, splash landing introductions, and numerical dashboard tallies.

#### 🔹 Variant A: Display Large
* **System Definition:** The maximum typographic scale element in the system architecture, optimized strictly for short, high-impact alphanumeric callouts where visual prominence is the absolute priority.
* **Core Token Path:** `Primary-EXO/Display/Display Large` \| `Secondary-Gabarito/Display/Display Large`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 58px` | `lineHeight: 64px` | `letterSpacing: -0.25px` | `fontWeight: 600`
* **Intended Layout System Purpose:** Used for singular visual focal points, single-digit key performance data points, impact titles on onboarding frames, and empty-state illustrations.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** restrict usage to text sequences with a maximum threshold of 3 words.
  * **DON'T** wrap this structural type tier onto multi-line paragraphs or subheadings.
* **Web Accessibility (WCAG) Rationale:** Must maintain an absolute strict contrast threshold of 4.5:1 against page background fills. 

#### 🔹 Variant B: Display Medium
* **System Definition:** A high-scale decorative structural text tier designed to break layout monotony and announce large-format numeric expressions or single-sentence milestone achievements.
* **Core Token Path:** `Primary-EXO/Display/Display Medium` \| `Secondary-Gabarito/Display/Display Medium`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 48px` | `lineHeight: 52px` | `letterSpacing: 0px` | `fontWeight: 500`
* **Intended Layout System Purpose:** Profile summary title values, welcome statements, block charts metrics headers, and primary checkout balances.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** lock your text alignment parameter to centralized or left orientations based cleanly on viewport bounds.
  * **DON'T** pair this tier closely with small body text components without a title block separator.
* **Web Accessibility (WCAG) Rationale:** Qualifies as large scale text under WCAG compliance models, lowering the strict minimum color contrast threshold rule to a 3.0:1 ratio.

#### 🔹 Variant C: Display Small
* **System Definition:** The entry-level display style used to highlight major system states, financial data points, or localized layout banners without completely dominating the screen real estate.
* **Core Token Path:** `Primary-EXO/Display/Display Small` \| `Secondary-Gabarito/Display/Display Small`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 36px` | `lineHeight: 44px` | `letterSpacing: 0px` | `fontWeight: 400`
* **Intended Layout System Purpose:** Secondary dashboard chart values, main module section landing greetings, and large empty state banners.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** utilize regular font styles (`fontWeight: 400`) to maximize curve smoothing on low-resolution displays.
  * **DON'T** apply thin styles or light font weights over high-vibrancy colored primary containers.
* **Web Accessibility (WCAG) Rationale:** Minimum line spacing of 1.2x font size is programmatically preserved via the 44px layout boundary lock to enable safe assistive screen reader pacing.

---

### 📛 2. Heading Variants
Structural layout anchors positioned at the root entry intersections of core sections, pages, and components.

#### 🔹 Variant A: Headline Large
* **System Definition:** The primary page-level navigation anchor used as the foundational title marker to declare a user's absolute structural location within an application view or feature routing directory.
* **Core Token Path:** `Primary-EXO/Heading/Headline Large` \| `Secondary-Gabarito/Heading/Headline Large`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 32px` | `lineHeight: 40px` | `letterSpacing: 0px` | `fontWeight: 600`
* **Intended Layout System Purpose:** Root title settings header on major full-screen app viewports, main settings category headers, and drawer title cards.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** reserve this explicitly as an `<h1>` semantic code mapping tag across web targets.
  * **DON'T** introduce this structural tier inside side drawer menus or modal prompt popups.
* **Web Accessibility (WCAG) Rationale:** Ensures clear macro-reading anchors for visually impaired users scanning spatial boundaries.

#### 🔹 Variant B: Headline Medium
* **System Definition:** A secondary page header used to divide long layout flows into clear sub-sections, serving as the master entry title for distinct operational clusters or component grids.
* **Core Token Path:** `Primary-EXO/Heading/Headline Medium` \| `Secondary-Gabarito/Heading/Headline Medium`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 28px` | `lineHeight: 36px` | `letterSpacing: 0px` | `fontWeight: 400`
* **Intended Layout System Purpose:** Mid-tier content category subheadings, nested detail panel block titles, and container card title elements.
* **Applied Local Constraints (Do's and Don't's):**
  * **DO** use this to mark clear secondary context steps down standard long-form layouts.
  * **DON'T** position this element below low-prominence subtitles.
* **Web Accessibility (WCAG) Rationale:** Formally maps to semantic `<h2>` component layers to ensure clean screen reader navigation trails.

#### 🔹 Variant C: Headline Small
* **Core Token Path:** `Primary-EXO/Heading/Headline Small` \| `Secondary-Gabarito/Heading/Headline Small`
* **System Definition:** The lowest heading hierarchy tier, engineered to anchor independent card wrappers, layout tiles, modular dashboard cards, and action group titles.
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 24px` | `lineHeight: 32px` | `letterSpacing: 0px` | `fontWeight: 400`
* **Intended Layout System Purpose:** Small card headings, system item list headers, and core dialog option container callouts.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** ensure the layer constraints provide room for horizontal wrapping to clear overlapping text risks.
  * **DON'T** use tight bounding box containers that truncate character loops.
* **System Correction Node:** *Note that under the legacy `Secondary-Gabarito` file, this element was erroneously registered at `32px`. This breaks scale sequence since it is larger than Headline Medium (`28px`). Code synthesis should enforce the corrected `24px` parameter.*

---

### 🏷️ 3. Title Variants
Medium-prominence typographic styles designed to identify form clusters, button labels, and secondary interface modules.

#### 🔹 Variant A: Title Large
* **System Definition:** A high-contrast layout sub-anchor designed to emphasize component grouping metrics, overlay window headers, or transaction sheet details without cluttering screen hierarchy.
* **Core Token Path:** `Primary-EXO/Title/Title Large` \| `Secondary-Gabarito/Title/Title Large`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 24px` | `lineHeight: 36px` | `letterSpacing: 0px` | `fontWeight: 700`
* **Intended Layout System Purpose:** Standard overlay labels for modal cards, card deck headers, profile titles, and confirmation dialog titles.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** use bold weights (`700`) to separate these block headers from body text blocks.
  * **DON'T** use light weights that render with low contrast on high-density displays.
* **System Correction Node:** *The primary track layout maps a `24px` text size into a small `20px` line height. This will cause multiple text lines to overlap vertically. The unified system correction forces a `36px` layout boundary rule to match the Gabarito model.*

#### 🔹 Variant B: Title Medium
* **System Definition:** The default structural style for identifying prominent form variables, interactive text lists, tab layouts, and navigation menu selections.
* **Core Token Path:** `Primary-EXO/Title/Title Medium` \| `Secondary-Gabarito/Title/Title Medium`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 16px` | `lineHeight: 28px` | `letterSpacing: 0px` | `fontWeight: 500`
* **Intended Layout System Purpose:** High-prominence input form labels, transaction card entries, data grid headers, and system menus text.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** use this as the primary typography option for clickable link elements and active menus.
  * **DON'T** mix this weight format with body copy rows inside unbordered layouts.
* **Web Accessibility (WCAG) Rationale:** The 28px line-height configuration creates clear spacing around elements, making text easy to tap on mobile touch devices.

#### 🔹 Variant C: Title Small
* **System Definition:** The baseline structural descriptor used to supply tight component headers, sub-panel data definitions, and secondary card descriptions.
* **Core Token Path:** `Primary-EXO/Title/Title Small` \| `Secondary-Gabarito/Title/Title Small`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 14px` | `lineHeight: 20px` | `letterSpacing: 0.10000000149011612px` | `fontWeight: 400`
* **Intended Layout System Purpose:** Collapsible panel labels, inner component subtitles, secondary navigation links, and small card descriptors.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** use the tracking property to maximize readability when rendering with small font sizes.
  * **DON'T** apply bold text styles, as tight letter loops will fill in and degrade text clarity.
* **Web Accessibility (WCAG) Rationale:** Must maintain an absolute contrast ratio of 4.5:1 against the canvas, as the small font size makes it sensitive to contrast loss.

---

### 📝 4. Label Variants
Functional, high-weight technical text styles optimized for interface badges, input captions, and button element tags.

#### 🔹 Variant A: Label Large
* **System Definition:** An action-driven typographic token specifically engineered to style button triggers, chip options, interactive toggle states, and tag items.
* **Core Token Path:** `Primary-EXO/Label/Label Large` \| `Secondary-Gabarito/Label/Label Large`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 14px` | `lineHeight: 20px` | `letterSpacing: 0.10000000149011612px` | `fontWeight: 600`
* **Intended Layout System Purpose:** Standard button actions text, active badge filters, segment toggle row descriptions, and chip options text.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** use heavy semi-bold text treatments (`600`) to highlight interactive click points.
  * **DON'T** confuse this utility tracking layer with regular paragraph body layouts.
* **Web Accessibility (WCAG) Rationale:** Clear character weight helps users scan and identify interactive controls quickly.

#### 🔹 Variant B: Label Medium
* **System Definition:** A secondary status-driven text class reserved for component caption helpers, structural chart legend lines, form validations, and secondary badge info.
* **Core Token Path:** `Primary-EXO/Label/Label Medium` \| `Secondary-Gabarito/Label/Label Medium`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 12px` | `lineHeight: 16px` | `letterSpacing: 0.5px` | `fontWeight: 500` / `400`
* **Intended Layout System Purpose:** Form field helper captions, metadata categories, graph axis markings, and secondary badge states.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** take advantage of the `0.5px` letter spacing to preserve legibility when converting text strings to uppercase.
  * **DON'T** wrap this functional style across complex multi-line instructional blocks.
* **Web Accessibility (WCAG) Rationale:** The explicit `0.5px` horizontal tracking value ensures readable spacing between small characters.

#### 🔹 Variant C: Label Small
* **System Definition:** The entry-level micro-caption style used exclusively for low-priority system timestamps, legal block footnotes, or minor chart data points.
* **Core Token Path:** `Primary-EXO/Label/Label Small` \| `Secondary-Gabarito/Label/Label Small`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 10px` | `lineHeight: 16px` | `letterSpacing: 0.5px` | `fontWeight: 400`
* **Intended Layout System Purpose:** Micro-captions, system timeline timestamps, data point charts indicators, and legal table footnotes.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** restrict usage to short strings (under 20 characters) like data labels or numbers.
  * **DON'T** rely on this variant to communicate critical data errors or validation warnings.
* **Web Accessibility (WCAG) Rationale:** **This is the absolute smallest text size allowed in the system.** It requires strict solid black or pure white pairings to ensure readability at a small scale.

---

### 🗪 5. Body Variants
Low-emphasis text styles optimized for readability across multi-line descriptions, help copy, and documentation blocks.

#### 🔹 Variant A: Body Large
* **System Definition:** The primary typographic setting for handling heavy multi-line reading, ideal for documentation guides, feature text blocks, and instructional screens.
* **Core Token Path:** `Primary-EXO/Body/Body Large` \| `Secondary-Gabarito/Body/Body Large`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 16px` | `lineHeight: 24px` | `letterSpacing: 0.5px` | `fontWeight: 500`
* **Intended Layout System Purpose:** Primary product description blocks, text messages chat rows, and instructional documentation panels.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** use medium text weights (`500`) to guarantee smooth character paths on anti-aliased screens.
  * **DON'T** use extreme paragraph block widths exceeding 80 characters per line.
* **Web Accessibility (WCAG) Rationale:** Provides an ideal reading rhythm for long-form content, meeting standard accessibility requirements out of the box.

#### 🔹 Variant B: Body Medium
* **System Definition:** The system's standard typographic body choice, optimized to handle description cards, list entries, summary lines, and help content.
* **Core Token Path:** `Primary-EXO/Body/Body Medium` \| `Secondary-Gabarito/Body/Body Medium`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 14px` | `lineHeight: 24px` | `letterSpacing: 0.25px` | `fontWeight: 500`
* **Intended Layout System Purpose:** Secondary feature descriptions, card info paragraphs, dashboard tables row data, and tooltips text copy.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** apply this variant as the system standard for multi-line user interfaces.
  * **DON'T** mix this style with low-contrast label fonts within the same block.
* **Web Accessibility (WCAG) Rationale:** Re-evaluates a balanced `0.25px` tracking offset to ensure clean letter shapes and maintain legibility at smaller scales.

#### 🔹 Variant C: Body Small
* **System Definition:** The lowest hierarchy tier for paragraph text, configured exclusively for secondary item definitions, disclaimers, and tooltips metadata.
* **Core Token Path:** `Primary-EXO/Body/Body Small` \| `Secondary-Gabarito/Body/Body Small`
* **Atomic Property Blueprint:** `fontFamily: "Exo" / "Gabarito"` | `fontSize: 12px` | `lineHeight: 12px` | `letterSpacing: 0.4000000059604645px` | `fontWeight: 400`
* **Intended Layout System Purpose:** Disclaimers copy, input help captions, system cookie notes, and secondary chart legends.
* **Applied Local Constraints (Do's and Don'ts):**
  * **DO** use the wide tracking profile to prevent character crowding in dense blocks.
  * **DON'T** use this style for legal terms or lengthy reading paths.
* **Web Accessibility (WCAG) Rationale:** The compact `12px` line height is optimized for single-line interface captions. Avoid wrapping this text variant across more than two lines to prevent vertical crowding.