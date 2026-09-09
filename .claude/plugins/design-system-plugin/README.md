---
name: design-token-migration-readme
description: Serves as the master entry point and architectural guide for the Neurealm brand migration design tokens. It explains the purpose (What) and exact usage (How to) of the three core design token assets (PRIMITIVE.md, LIGHT_SCHEMES.md, and TYPOGRAPHY.md). Use this skill when an agent needs to onboard teams, understand file relations, troubleshoot brownfield code generation, or execute a non-destructive theme layer mutation.
metadata:
  version: "1.0"
---

# 🚀 Neurealm Brand Migration Core Specification

Welcome to the **Neurealm Brand Migration Registry**. This workspace serves as the framework-agnostic source of truth for updating existing ("brownfield") applications to align with the new Neurealm visual identity[cite: 1]. 

This document details the architectural purpose (**What you will get**) and development execution rules (**How to use them**) for the foundational specification files provided by our design team[cite: 1].

---

## 🏗️ Core Architecture Overview (The What)

The design system is split into three decoupled specification tracks, moving from raw values up to contextual intent[cite: 1]:

| Design Input File | Design Intent & Purpose | How it Helps Development |
| :--- | :--- | :--- |
| **`PRIMITIVE.md`** | Establishes the absolute visual source-of-truth palette[cite: 1]. It catalogs raw color swatches, mathematical RGB steps, and alpha chains separate from UI layout bounds[cite: 1]. | Acts as a centralized data registry to establish global variables (e.g., `:root` CSS or Tailwind config tokens)[cite: 1]. |
| **`LIGHT_SCHEMES.md`** | Translates raw primitives into contextual interaction meaning for Light Mode UI layouts[cite: 1]. Maps roles (surfaces, headers, outlines, errors) to strict purposes[cite: 1]. | Serves as the ultimate semantic mapping rulebook, dictating exactly which tokens/classes apply to specific component states[cite: 1]. |
| **`TYPOGRAPHY.md`** | Outlines a strict context-aware hierarchy based on a **Dual-Font Track System** (`Exo` vs `Gabarito`) to protect micro and macro layout scanning[cite: 1]. | Establishes explicit font maps, tracking offsets, scale boundaries, and line heights to eliminate legacy alignment defects[cite: 1]. |

---

## 🛠️ Step-by-Step Implementation Guide (The How To)

Follow this step-by-step roadmap to ingest tokens into a brownfield project safely without breaking existing component logic[cite: 1].

### 🔹 Step 1: Initialize Primitives to Global Variables
1. Open your project's root style configuration layer (e.g., `theme.css`, `tailwind.config.js`, or theme initialization file).
2. Read the global scales inside **`PRIMITIVE.md`**.
3. Map raw values into centralized tokens:
   * **CSS Variable Strategy:** Map `Colors/Primary/Primary-900` to `--nr-color-primitive-primary-900: #081160;`.
   * **Tailwind System Strategy:** Extend your configuration file inside the theme block by declaring primitive namespaces (e.g., `colors: { nr: { primary: { 900: '#081160' } } }`).

### 🔹 Step 2: Establish Semantic Schemes
1. Open your active semantic application mapping stylesheet or configuration logic.
2. Read the **`LIGHT_SCHEMES.md`** Master Application table[cite: 1].
3. Direct your components to read semantic variables instead of raw hex values:
   * **Correct Usage Rule:** Match functional elements directly to their semantic intent. For instance, high-prominence triggers like primary buttons or active canvas states must exclusively map to `Schemes/Primary`[cite: 1].
   * **Component Isolation Example:** Ensure text nested directly inside a `Schemes/Primary` element utilizes `Schemes/On Primary` to fulfill accessibility parameters[cite: 1].

### 🔹 Step 3: Configure the Dual-Font Typography Lanes
1. Verify that your environment imports the required brand typeface engines (`Exo` and `Gabarito`) via your bundler, `@import`, or local font buffers.
2. Build your typographic utility maps using the atomic blueprints defined in **`TYPOGRAPHY.md`**:
   * Use the **`Primary-EXO`** track for consumer-facing features, product descriptions, marketing layouts, and primary dashboard summaries.
   * Swap smoothly to the **`Secondary-Gabarito`** track within data grids, technical side panes, tabular code arrays, and high-density performance listings.
3. Enforce the exact line-height configurations explicitly defined under the *Atomic Property Blueprints* to automatically eliminate vertical overlaps and text truncation bugs from legacy files.

---

## ⚠️ Core Engineering Guardrails (Hard Rules)

To maintain synchronization sanity and avoid compilation parsing errors, all automation tools and engineers must strictly adhere to these design pipeline rules[cite: 1]:

* **🛑 Token Preservation Rule:** Every single styling parameter written to the codebase must come exclusively from these provided design specification files[cite: 1]. Code compilation engines and development teams are strictly prohibited from inventing arbitrary hex variations or pulling external overrides[cite: 1].
* **🔄 Case Sensitivity Precision:** Automated parsers handle token strings with strict case-sensitivity. Verify that case distributions across files match your target setups exactly (e.g., Title-case `Colors/Grey/Grey-50` vs lowercase `Colors/Grey/grey-100`) to prevent path duplications.
* **🛡️ Structural Non-Destruction:** Token upgrades must execute strictly as an overlay theme adjustment tier. The integration script or manual updates must update value bindings but **never** modify component-level layout markup structures, delete existing non-brand custom configurations, or clear legacy developer comments[cite: 1].