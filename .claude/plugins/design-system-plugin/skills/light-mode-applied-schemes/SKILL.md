---
name: light-mode-applied-schemes
description: Provides the active semantic color application tier for the application layout interface under Light Mode. It maps structural properties and contextual design intent directly to end-user components using verified tokens. Use this skill when an agent needs to apply, audit, or reference light theme semantic M3 tokens, color hex values, intended layout systems purposes, or structural content guardrails.
metadata:
  version: "1.0"
---

# ☀️ Light Mode Applied Schemes

This markdown document serves as the active semantic color application tier for our application layout interface under Light Mode. It maps structural properties and contextual design intent directly to end-user components using the verified tokens extracted from our design repository.

---

## 1. Master Scheme Application Table (Tier 1 Core Schemes)

| Applied M3 Semantic Token | Primitive Alias Source Target | Hex Output Value | Intended System Purpose (Where to Use) | System Restrictions (Where NOT to Use) |
| :--- | :--- | :--- | :--- | :--- |
| **Schemes/Primary** | `Colors/Primary/Primary-900` | `#081160` | Primary brand visibility. High-prominence components like filled buttons, active state highlights, and primary headers. | Do not use for large area backdrops or paragraph body copy as it strains legibility. |
| **Schemes/On Primary** | `Colors/Primary/Primary-100` | `#F1F0FF` | Clear text labels or iconography nesting directly inside components colored with Schemes/Primary. | Do not use as a standalone container outline or page structural canvas backing. |
| **Schemes/Primary Container** | `Colors/Primary/Primary-900` | `#081160` | Solid block container foundations requiring strong structural emphasis or layout anchoring. | Do not use interchangeable with neutral context container blocks or subtle menu list fills. |
| **Schemes/On Primary Container** | `Colors/Primary/Primary-200` | `#DDE1FF` | Secondary text labels, subheadings, or structural details nested cleanly within Primary Container fields. | Do not use over neutral cards or default surface variants. |
| **Schemes/Secondary** | `Colors/Secondary/Secondary-500` | `#4EA0EF` | Secondary component structures, less prominent text selections, accent badges, filter chips, and toggle track outlines. | Do not use for standard critical alerts or error notifications. |
| **Schemes/On Secondary** | `Colors/Secondary/Secondary-50` | `#F1F7FF` | Labels or graphics placed explicitly inside active fields colored with Schemes/Secondary. | Do not use directly on grey backgrounds as it will fail accessibility checks. |
| **Schemes/Secondary Container** | `Colors/Secondary/Secondary-200` | `#D1E9FF` | Fills or backgrounds for lower-prominence screen sections like selected tabs or secondary filter buttons. | Do not use for the primary layout grid container. |
| **Schemes/On Secondary Container** | `Colors/Secondary/Secondary-900` | `#00335F` | High-contrast copy or metadata inside secondary active containers. | Do not use as standard system main headings. |
| **Schemes/Tertiary** | `Colors/Light/Ivory-50` | `#FFFFEF` | Soft, distinct stylistic accents, alternative item group highlights, and specialized visual components. | Do not use where a stark, neutral background treatment is required. |
| **Schemes/On Tertiary** | `Colors/Grey/grey-800` | `#212936` | High-visibility typography or active markings embedded over Ivory tertiary sections. | Do not use as global light-mode canvas body text. |
| **Schemes/Tertiary Container** | `Colors/Light/Ivory-50` | `#FFFFEF` | Container background cards designed to offset typical neutral page structures. | Do not use for error zones or alert banners. |
| **Schemes/On Tertiary Container** | `Colors/Grey/grey-800` | `#212936` | Content overlays inside non-standard tertiary system groupings. | Do not use on dark or secondary background elements. |
| **Schemes/Error** | `Colors/Error/500` | `#EB1414` | Critical diagnostic alerts, invalid inputs, failure banners, or highly destructive action states (e.g., Delete actions). | Do not use for decorative items or standard app navigation elements. |
| **Schemes/On Error** | `Colors/Error/100` | `#F2E9E9` | Highly readable warning text or iconography placed directly inside solid Schemes/Error banners. | Do not use as standard card outlines or general body text. |
| **Schemes/Error Container** | `Colors/Error/700` | `#931616` | Distinct container sheets displaying transaction fail messages or system errors. | Do not use for success toasts or positive feedback states. |
| **Schemes/On Error Container** | `Colors/Error/200` | `#ECCBCB` | Accents or structural captions appearing inside the system error container spaces. | Do not use across primary layout paths. |
| **Schemes/Background** | `Colors/Primary/Primary-100` | `#F1F0FF` | The true global window background backing for viewports in the application interface. | Do not use on floating context menus, dropdown pickers, or modular dialogs. |
| **Schemes/On Background** | `Colors/Grey/grey-900` | `#131927` | Global text fields, standard paragraph body fonts, and base headers sitting over the canvas workspace. | Do not use as an outline frame color or inner surface fill. |
| **Schemes/Surface** | `Colors/Grey/grey-100` | `#F3F4F6` | Base canvas container tier for default application surfaces like menus, navigation bars, and structural sheets. | Do not use interchangeably with layouts meant to explicitly overlay other cards. |
| **Schemes/On Surface** | `Colors/Black/black-900` | `#000000` | Titles, main text values, and dominant labels resting safely on top of standard Surface layers. | Do not use inside dark themed container nodes. |
| **Schemes/Surface Variant** | `Colors/Grey/grey-200` | `#E5E7EA` | Inactive states, search input backgrounds, empty states, or table row headers. | Do not use for main text or high-contrast graphics. |
| **Schemes/On Surface Variant** | `Colors/Grey/grey-800` | `#212936` | Lower-priority structural descriptors, caption footnotes, or unselected navigation indicators. | Do not use for major interface commands or call-to-actions. |
| **Schemes/Outline** | `Colors/Grey/grey-600` | `#4D5461` | Prominent input field outlines, structural dividers, accordion frames, and high-visibility component borders. | Do not use for solid area fills or layout text paths. |
| **Schemes/Outline Variant** | `Colors/Grey/grey-500` | `#6D717F` | Subtle text partition rules, inner divider hairs, grid line matrices, and low-contrast borders. | Do not use where structural clarity is legally needed for accessibility. |
| **Schemes/Surface Tint** | `Colors/Tertiary/Tertiary-600` | `#00B3B3` | Overlaid on surfaces when dynamic color accent elevation transformations occur. | Do not use directly for user-facing layout typography blocks. |
| **Schemes/Shadow** | `Colors/Black/black-900` | `#000000` | Applied to drop-shadow effects, elevation filters, or structural layout multi-layer definitions. | Do not use directly for text strings or button component borders. |
| **Schemes/Scrim** | `Colors/Black/black-900` | `#000000` | Backdrop overlay shading behind modal view sheets, screen dialogs, or temporary popup panes. | Do not use inside components for interactive active borders. |
| **Schemes/Inverse Surface** | `Colors/Light/Ivory-50` | `#FFFFEF` | Used on snackbars or toast blocks that need to contrast completely with the default light UI landscape. | Do not use as a primary layout container backdrop. |
| **Schemes/Inverse On Surface** | `Colors/Light/Ivory-900` | `#4A4938` | Labels or text strings sitting directly over highly contrasted Inverse Surface modules. | Do not use on default layout backgrounds or cards. |
| **Schemes/Inverse Primary** | `Colors/Primary/Primary-500` | `#6F74D2` | High-prominence actionable elements nested over inverted landscape zones. | Do not use for base light mode buttons. |
| **Schemes/Primary Fixed** | `Colors/Primary/Primary-100` | `#F1F0FF` | Containers or regions that must keep this exact light brand coloration across theme switches. | Do not use if the region is expected to drop into deep dark mode themes. |
| **Schemes/On Primary Fixed** | `Colors/Primary/Primary-900` | `#081160` | Content running over fixed primary structural blocks. | Do not use on standard semantic variables. |
| **Schemes/Primary Fixed Dim** | `Colors/Primary/Primary-100` | `#F1F0FF` | A lower-contrast, softer option for fixed primary elements. | Do not use for crucial interactive focus pathways. |
| **Schemes/On Primary Fixed Variant**| `Colors/Primary/Primary-600` | `#555BB6` | Alternate typography color choices resting on top of fixed primary structures. | Do not use for legal or body level reading. |
| **Schemes/Secondary Fixed** | `Colors/Secondary/Secondary-100`| `#EBF4FF` | Fixed containers mapping light accent structures across global responsive viewport states. | Do not use where a stark neutral color theme belongs. |
| **Schemes/On Secondary Fixed** | `Colors/Secondary/Secondary-900`| `#00335F` | Read-only structural elements anchored inside fixed secondary panels. | Do not use over warning scales. |
| **Schemes/Secondary Fixed Dim** | `Colors/Secondary/Secondary-50` | `#F1F7FF` | Shaded variant alternative layouts for localized theme-exempt layouts. | Do not use for standard context labels. |
| **Schemes/On Secondary Fixed Variant**| `Colors/Secondary/Secondary-800`| `#004D8C` | Accent iconography sets running over theme-locked secondary elements. | Do not use for long paragraphs. |
| **Schemes/Tertiary Fixed** | `Colors/Light/Ivory-50` | `#FFFFEF` | Fixed background highlights tracking specialized promotional modules. | Do not use inside technical operational tables. |
| **Schemes/On Tertiary Fixed** | `Colors/Tertiary/Tertiary-900` | `#005050` | Contrasting layout headers positioned on specialized locked frames. | Do not use over primary background tiers. |
| **Schemes/Tertiary Fixed Dim** | `Colors/Tertiary/Tertiary-100` | `#E0FBFB` | Lower-intensity locked visual zones for non-critical interface paths. | Do not use for baseline menu texts. |
| **Schemes/On Tertiary Fixed Variant**| `Colors/Highlight/Turquoise-800`| `#008181` | Alternate decorative accents on top of theme-exempt structures. | Do not use for essential reading paths. |
| **Surface Dim** | `Colors/Primary/Primary-50` | `#F8F8FF` | Slightly deepened baseline surface to create variation without adding a border line. | Do not use on floating elements like context tooltips. |
| **Surface Bright** | `Colors/Secondary/Secondary-50`| `#F1F7FF` | A crisp, high-luminance surface tier ideal for structural headers or dashboard workspace tiles. | Do not use inside recessed content wells. |
| **Surface Container Lowest** | `ColorsWhite/White/-900` | `#FFFFFF` | The absolute lowest visual structural container, used for standard canvas cards or view containers. | Do not use as a default border outline parameter. |
| **Surface Container Low** | `Colors/Light/Ivory-50` | `#FFFFEF` | A slightly warmer tier container asset for simple nested layout elements. | Do not use for components requiring high neutral contrast. |
| **Surface Container** | `Colors/Grey/grey-100` | `#F3F4F6` | Standard containment layout panel block for general component groups and lists. | Do not use for floating window blocks or sheets. |
| **Surface Container High** | `ColorsWhite/White/-900` | `#FFFFFF` | Elevates nested blocks (like picker lists or multi-option widgets) above base containers. | Do not use as a fallback window wrapper. |
| **Surface Container Highest**| `Colors/Tertiary/Tertiary-50` | `#ECFDFD` | The maximum structural component casing, reserving intense focal attention for dialog choices. | Do not use for repeating row elements or common templates. |

---

## 2. Structural Content Guardrails (Design Constraints)

* **✔️ DO** confirm that all layouts consume these context-specific aliases explicitly rather than picking rough matches directly out of raw primitive files.
* **✔️ DO** utilize the progressive step scales of `Surface Container Low` through `Highest` to form distinct dimensional stack elevation layers without introducing redundant borders.
* **❌ DON'T** attempt to force semantic properties from this list to run custom opacity modifiers on UI mockups—interaction transparencies are modularly separated into the state overlay files.
* **❌ DON'T** drop `Schemes/Primary` or container definitions down onto wide structural page surfaces where pure neutral backings belong.