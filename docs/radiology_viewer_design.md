# Radiology Core — UI/UX Design System

> **Purpose of this document:** capture the visual language, layout structure, component patterns, and interaction conventions used in the "MedSense" radiology viewer (this project) so that they can be reproduced consistently in another viewer/app. Every token, color, and structural detail below was pulled directly from the current source (`frontend/src/`), not guessed.
>
> **Provenance note:** `frontend/src/index.css:7` carries the comment *"Design Tokens (Dark Radiology — Blue-first, aligned with frontend_new)"* — the tokens were intentionally ported from a sibling project (`frontend_new`) to keep a consistent visual identity across products. Treat this file as the canonical source for that shared identity going forward.

---

## 1. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Framework | **React 18.3 + TypeScript** | bootstrapped with Vite (package name literally left as `vite-react-typescript-starter`) |
| Build tool | **Vite 5** | `vite.config.ts`, dev server on `:5173` |
| Routing | **react-router-dom v7** | `BrowserRouter` + nested `<Route>` + `<Outlet/>` |
| UI component kit | **Ant Design v6 (`antd`)** | used as raw primitives (`Button`, `Select`, `Slider`, `Tabs`, `Alert`, `Spin`, `message`), then reskinned wholesale via CSS `!important` overrides rather than AntD's `ConfigProvider` theme API |
| CSS | **Tailwind CSS 3.4** | utility classes mixed with inline `style={}` objects — a hybrid, not pure-Tailwind codebase. `tailwind.config.js` is left at defaults (no theme.extend) — all design tokens live in plain CSS custom properties instead |
| Icons | **lucide-react** (custom components) + **@ant-design/icons** (paired with AntD controls) | |
| Imaging | **itk-wasm** + **@itk-wasm/image-io** (primary rendering engine, canvas-based) · `dicom-parser` (manual DICOM header parsing) · `nifti-reader-js` + `pako` (NIfTI / `.nii.gz`) · `cornerstone-core`/`cornerstone-wado-image-loader` (installed, legacy/unused in the primary viewer) | |
| State management | **No Redux/Zustand.** Plain `useState` + custom hooks + a single React Context for theme | see §8 |
| Backend | Flask (Python), SQLite dev DB, Blueprint routes | not UI-relevant but shapes API shape used by hooks |

Reproduce this stack as-is (React + TS + Vite + AntD + Tailwind hybrid + CSS-custom-property tokens) to guarantee pixel/behavior parity in a new viewer, or port just the token system (§3) if the new app uses a different framework.

---

## 2. Overall Composition — "PACS Workstation" Metaphor

The app is deliberately shaped like a radiology workstation, not a generic dashboard:

```
┌─ App Shell (MainLayout) ───────────────────────────────────────────────┐
│ Sidebar (220/64px) │ Topbar (60px): page title + theme toggle + avatar │
│  - Logo/wordmark   ├─────────────────────────────────────────────────┤│
│  - Nav (Modules)   │ Page content (<Outlet/>)                        ││
│  - Collapse toggle │  ┌─ ViewerLayout (nested, per viewer page) ────┐ ││
│                    │  │ Patient header strip (compact PatientCard)  │ ││
│                    │  │ Menu bar: File/Segmentation/Tools/View/Help │ ││
│                    │  ├──────────────────────────────┬──────────────┤ ││
│                    │  │ Tool status strip (36px)     │ Right         │ ││
│                    │  ├───────────────────────────────┤ sidebar      │ ││
│                    │  │ Canvas viewport               │ (320px,      │ ││
│                    │  │  + toolbar overlay (top)       │ tabbed       │ ││
│                    │  │  + patient/series HUD corners  │ panels)     │ ││
│                    │  │  + W/L + slice sliders (bottom)│              │ ││
│                    │  ├───────────────────────────────┴──────────────┤ ││
│                    │  │ Footer status bar (26px): pulse dot + HIPAA  │ ││
│                    │  └───────────────────────────────────────────────┘ ││
└──────────────────────────────────────────────────────────────────────┘
```

Two layout components, two levels:

1. **`MainLayout.tsx`** — the app-wide chrome (sidebar nav + topbar). Every route renders inside it via `<Outlet/>`.
2. **`ViewerLayout.tsx`** — nested inside individual viewer pages, wraps the actual image canvas with PACS-style furniture (patient strip, fake menu bar, tool status strip, right info sidebar, compliance footer).

This two-tier split is worth keeping in a new viewer: **app chrome** (identity, navigation, account) stays separate from **viewer chrome** (clinical context, tools, status), and the viewer chrome is reusable across every page that shows an image (segmentation, VLM analysis, plain viewing) via a `sidebarContent`-style prop.

---

## 3. Design Tokens

All tokens are plain CSS custom properties defined in `frontend/src/index.css`, toggled by a `data-theme` attribute on `<html>`. **Do not** encode these in `tailwind.config.js` `theme.extend` — the project deliberately keeps Tailwind vanilla and drives everything through CSS variables so both Tailwind utilities and inline styles and AntD overrides can all reference the same source of truth.

### 3.1 Color — Dark (default)

```css
--radio-bg:      #08090B;   /* page background, near-black */
--radio-surface: #0F1117;   /* sidebars, headers, menu bars */
--radio-card:    #14161E;   /* cards, panels, dropdowns */
--radio-border:  rgba(255,255,255,0.07);
--radio-border-hover: rgba(26,86,232,0.4);

--accent:        #1A56E8;   /* single primary brand color — blue */
--accent-dim:    rgba(26,86,232,0.10);
--accent-mid:    rgba(26,86,232,0.4);
--accent-hover:  #2563EB;

--white:  #FFFFFF;
--ink-90: rgba(255,255,255,0.92);  /* primary text */
--ink-60: rgba(255,255,255,0.55);  /* secondary text */
--ink-30: rgba(255,255,255,0.28);  /* tertiary / labels */
--ink-10: rgba(255,255,255,0.06);  /* hover fills */

/* Status colors — muted, explicitly NOT used as brand identity */
--teal:  #00C6AF;  --teal-dim:  rgba(0,198,175,0.10);
--green: #22C55E;  --green-dim: rgba(34,197,94,0.10);
--red:   #EF4444;  --red-dim:   rgba(239,68,68,0.10);
--amber: #F59E0B;  --amber-dim: rgba(245,158,11,0.10);
```

### 3.2 Color — Light (`[data-theme="light"]`)

```css
--radio-bg: #F5F7FA;  --radio-surface: #FFFFFF;  --radio-card: #FFFFFF;
--radio-border: rgba(0,0,0,0.09);
--accent: #1A56E8;  /* same accent — brand stays constant across themes */
--white: #111827;   /* inverted "white" = near-black ink for text-on-light */
--ink-90: rgba(0,0,0,0.87); --ink-60: rgba(0,0,0,0.55);
--ink-30: rgba(0,0,0,0.35); --ink-10: rgba(0,0,0,0.04);
--teal:#059387; --green:#16a34a; --red:#dc2626; --amber:#d97706; /* darkened for contrast on white */
```

Design rule: **one accent color** (`#1A56E8` blue) carries all brand/interactive identity. Status colors exist only for semantic state (error/warning/success), never for navigation or emphasis — this keeps the UI visually calm despite dense clinical data.

### 3.3 Typography

```css
--font-serif: 'Instrument Serif', Georgia, serif;   /* display: wordmark, page titles */
--font-sans:  'Geist', -apple-system, BlinkMacSystemFont, sans-serif; /* everything else */
--font-mono:  'Courier New', monospace;             /* HUD/overlay numeric readouts */
```

Loaded via Google Fonts: `@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@300;400;500;600&display=swap');`

Pairing convention:
- **Instrument Serif**, regular weight, `italic` for a single accent word — used for the sidebar wordmark (`Med` + italic accent-colored `Sense`) and the topbar page title. This serif-for-display / sans-for-UI split is the signature typographic move — reuse it for the new viewer's brand mark and page headers only, never for body/UI text.
- **Geist** sans for all UI chrome, labels, buttons, tables.
- **Courier New monospace** exclusively for numeric HUD overlays on the image canvas (zoom %, slice n/total, W/L values, ITK image-info) — gives them a "instrument readout" feel distinct from UI chrome.

Common micro-label pattern used throughout (section titles, stat labels, badges):
```css
font-size: 9px–11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em–0.12em; color: var(--ink-30);
```

### 3.4 Motion, radius, shadow

```css
--ease-out: cubic-bezier(0.16, 1, 0.3, 1);
--ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);
--dur-fast: 120ms; --dur-base: 220ms; --dur-slow: 450ms;

--r-sm: 4px; --r-md: 8px; --r-lg: 12px; --r-xl: 16px;

--shadow-sm: 0 1px 4px rgba(0,0,0,0.5);
--shadow-md: 0 4px 20px rgba(0,0,0,0.6);
--shadow-lg: 0 12px 48px rgba(0,0,0,0.7);
--shadow-blue: 0 0 20px rgba(26,86,232,0.18);   /* accent glow, used sparingly */
```

Sidebar width and margin-left transitions use `220ms cubic-bezier(0.16,1,0.3,1)` specifically (not the CSS var, hardcoded inline in `MainLayout.tsx` — worth centralizing in a rebuild).

### 3.5 Theme switching mechanism

`frontend/src/hooks/useTheme.tsx` — a single React Context, no external lib:

```tsx
const [mode, setMode] = useState<ThemeMode>(() => {
  const saved = localStorage.getItem('medsense-theme');
  return (saved === 'light' || saved === 'dark') ? saved : 'dark';
});
useEffect(() => {
  localStorage.setItem('medsense-theme', mode);
  document.documentElement.setAttribute('data-theme', mode);
}, [mode]);
```
Default is **dark**. Toggle button lives top-right of the topbar (Sun/Moon `lucide-react` icon, amber when dark/showing "switch to light", accent-blue when light/showing "switch to dark").

### 3.6 Reusable utility classes (`index.css`)

| Class | Purpose |
|---|---|
| `.rr-card` | base panel: `var(--radio-card)` bg, `1px` border, `r-lg` radius, `16px` padding, border brightens to accent on hover |
| `.rr-badge` + `-blue/-green/-red/-amber/-white` | pill badge, `9px` uppercase, `700` weight, `0.08em` tracking |
| `.rr-stat` / `.rr-stat-value` / `.rr-stat-label` | stat block: big bold value (`20px`) + tiny uppercase label (`9px`, `ink-30`) |
| `.glow-line` | 1px gradient divider, `linear-gradient(90deg, transparent, accent@30%, transparent)` |
| `.medical-viewer` | `image-rendering: pixelated` for DICOM canvases — critical for correct pixel display, don't anti-alias medical images |
| `input[type=range]` custom skin | 4px track, 14px circular thumb, `box-shadow: 0 0 8px accent@50%` glow |
| `.animate-fade-up` / `.page-transition` | `fadeUp` 0.3–0.45s ease-out entrance animation for page/panel content |
| `.blink` | 2s opacity pulse, used for "live/ready" status dots |

### 3.7 Ant Design override strategy

AntD is used as raw component primitives, then every visual property is forced to the token system with `!important` (not AntD's `ConfigProvider`/`theme` API). E.g.:
```css
.ant-btn-primary { background: var(--accent) !important; border-color: var(--accent) !important; font-weight: 600 !important; }
.ant-card { background: var(--radio-card) !important; border: 1px solid var(--radio-border) !important; border-radius: var(--r-lg) !important; }
.ant-tabs-tab-active .ant-tabs-tab-btn { color: white !important; }
.ant-tabs-ink-bar { background: var(--accent) !important; }
```
**Recommendation for the new viewer:** if starting fresh, prefer AntD's `ConfigProvider theme={{ token: {...} }}` API over blanket `!important` overrides — same visual result, far less brittle against AntD version upgrades. Keep this file's *values* as the source of truth for whichever mechanism you choose.

---

## 4. App Shell — `MainLayout.tsx`

- **Sidebar**: fixed position, `220px` expanded / `64px` collapsed, animated via `transition: width 220ms cubic-bezier(0.16,1,0.3,1)`.
  - `60px` logo header, serif wordmark: `Med` (white) + italic `Sense` (accent blue) — collapses to icon-only.
  - `.glow-line` divider directly below.
  - Nav section labeled `MODULES` (9px uppercase, `ink-30`), one button per route:
    ```tsx
    { key: '/', icon: <MonitorPlay size={16}/>, label: 'Image Viewer' },
    { key: '/segmentation', icon: <Layers size={16}/>, label: 'Segmentation', badge: 'AI' },
    { key: '/vlm', icon: <BrainCircuit size={16}/>, label: 'VLM Analysis', badge: 'AI' },
    { key: '/report', icon: <FileText size={16}/>, label: 'Report Generator' },
    ```
  - Active nav item: `background: var(--accent-dim)`, `color: var(--accent)`, `boxShadow: inset 0 0 0 1px accent@25%` (a ring, not a border, so it doesn't shift layout).
  - `AI` feature badges use `.rr-badge-accent` at 8px font.
  - Collapse toggle pinned to sidebar bottom, `ChevronLeft`/`ChevronRight`, hover color `ink-30 → accent`.
- **Main area**: offset by `margin-left` equal to sidebar width, same transition timing (keeps sidebar and content moving in lockstep).
  - **Topbar** (`60px`, sticky, `zIndex: 50`): left side = serif page title (`22px`, derived from matching current route to `navItems`, fallback `"Radiology Core AI"`) + `10.5px` subtitle `"Clinical Intelligence Platform"`. Right side = theme toggle button (34×34, rounded-8) then a `32×32` rounded-8 gradient avatar (`linear-gradient(135deg, #1A56E8, #60a5fa)`) with a white `User` icon.
  - `<main>` renders `<Outlet/>`, `overflow: auto`.

**Reusable takeaway:** page title is *derived from the route*, not hardcoded per page — one `navItems` array drives both the sidebar nav and the topbar title. Reproduce this pattern to avoid title/nav drift.

---

## 5. Viewer Shell — `ViewerLayout.tsx`

Wraps the actual image canvas (`children`) with PACS-workstation furniture. Props: `sidebar`, `patientInfo`, `studyId`, `footerText`, `activeTool`, `modelName`, `onNewUpload`.

1. **Patient header strip** — `PatientCard` in `compact` mode (see §7) directly inside the viewer shell, not the app shell, so it only appears on pages with an active study.
2. **Menu bar** (34px, below patient strip) — `File / Segmentation / Tools / View / Help` buttons with `ChevronDown` (mostly decorative — a PACS-authenticity cue more than functional menus), right-aligned Study ID badge (`rr-badge-accent`, showing last 8 chars of study id) or a neutral "No Study Loaded" badge when empty.
3. **Content row** = flex row:
   - **Viewer column** (flex: 1):
     - **Tool status strip** (36px, `background: rgba(26,86,232,0.05)` — a *very* faint accent wash, not a hard color): `Cpu` icon + `TOOL:` label + active tool name in accent color; optional `Layers` icon + `MODEL:` + model name when an AI model is active, separated by a 1px vertical divider; right-aligned "New Upload" button (accent-dim pill that inverts to solid accent on hover).
     - **Canvas viewport** (flex: 1, `position: relative`) — the actual `children` (DicomViewer/ITKDicomViewer) render here with their own absolutely-positioned overlay HUD (§6).
   - **Right sidebar** (fixed `320px`, `overflowY: auto`) — arbitrary `sidebar` content, typically a tabbed panel (§8/§9).
4. **Footer status bar** (26px) — left: 5px pulsing accent dot (`opacity: 0.7`) + `footerText` (default `"ITK-SNAP Viewer · ITK-wasm · HuggingFace Models"`); right: static compliance string `"HIPAA Compliant · End-to-End Encrypted"`. Always present, regardless of app state — a persistent trust signal.

**Reusable takeaway:** the tool-status strip + compliance footer pairing is cheap to add and immediately reads as "clinical-grade software" — worth carrying into any new medical-imaging UI even if the underlying tech differs.

---

## 6. Image Viewport — Toolbar & HUD Overlays

Implemented in `ITKDicomViewer.tsx` (the production viewer; a legacy `DicomViewer.tsx` also exists using `<img>` + CSS filters instead of real pixel windowing — don't carry that one forward).

### 6.1 Floating toolbar
`position: absolute; top: 8px; left: 8px; right: 8px; zIndex: 20`, glassy surface:
```css
background: var(--radio-surface); backdrop-filter: blur(8px);
border-radius: 8px; padding: 8px; border: 1px solid var(--radio-border);
display: flex; align-items: center; justify-content: space-between;
```
Grouped left→right using AntD `Space.Compact` (segmented-button look) with 1px vertical dividers between groups:
1. **View mode** — `2D` (Slice icon) / `MIP` (Layers icon) / `3D Volume` (Box icon)
2. **Tools** — Pointer / Pan / Ruler (measure) / ROI-circle / ROI-square
3. **View toggles** — grid overlay, crosshair, patient-info overlay, mask-overlay eye (NIfTI only)
4. **Transform** — zoom out/in with live `%` chip, reset W/L, rotate L/R, flip H/V
5. **Right-aligned**: cine controls (First/Play-Pause/Last + speed `Select` 0.5×/1×/2×/4×, only shown for 3D volumes), global reset, fullscreen

### 6.2 Corner HUD overlays
All corner-pinned, glass surfaces, `font-family: var(--font-mono)` for numeric readouts:
- **Top-left**: patient info card — name in accent color, ID, age/sex, study date, modality, body part.
- **Top-right**: series info + "ITK Image Info" (dimensions, component type, spacing) + live state (view mode, slice `n/total`, zoom %, W/L).
- **Bottom bar** (`position: absolute; bottom: 8px; left: 8px; right: 8px`): dual `Slider`s for Window Width / Window Level; NIfTI volume/channel `Select` for multi-volume 4D data (labeled e.g. T1/T1ce/T2/FLAIR); Slice Navigation `Slider` + `n/total` chip; footer row with pulsing green "ITK-wasm Ready" dot.
- **Segmentation legend** (when mask overlay is on): color swatches per label, e.g. red = Tumor, blue = Edema.
- **Empty state**: centered dashed drop-zone, circular upload icon, format chips `DICOM` / `NIfTI` / `NRRD` — supports drag-and-drop directly onto the canvas as a fallback to the dedicated upload zone.

### 6.3 Interaction/state model
All viewer state is local `useState` (no store): `currentSlice`, `windowWidth`, `windowLevel`, `viewMode`, `isPlaying`, `playbackSpeed`, `zoom`, etc. Cine playback is a plain `setInterval(..., 1000 / playbackSpeed)` loop advancing `currentSlice`. No keyboard shortcuts or context menus exist anywhere in the app currently — if the new viewer wants either, they're a clean addition, not a refactor.

**Reusable takeaway:** the "glassy floating toolbar + corner HUD overlays + bottom W/L & slice sliders" composition is the visual signature of the viewport itself, independent of which rendering engine sits underneath. This is the part most worth copying verbatim into a differently-implemented viewer.

---

## 7. `PatientCard` Component

Two variants, controlled by a `compact` prop — reuse this exact pattern (one component, two densities) rather than building separate compact/full components.

- **Compact** (used in `ViewerLayout`'s header strip): 32×32 gradient-initials avatar (`linear-gradient(135deg, #1A56E8, #60a5fa)`) + name (13px, 600) + inline meta row (`ID · gender, age yo · MRN`) + right-aligned modality pill and priority badge.
- **Full** (standalone card, e.g. worklist/report context): 44×44 avatar, name at 15px, `.rr-card`-style container, divider, then a details grid (Study Date / Modality+BodyPart / Description / Physician) each with a micro-icon + uppercase label + value.

Priority badge coloring (shared helper `getPriorityStyle`):
```ts
STAT   → red-dim bg,   red text
URGENT → amber-dim bg, amber text
default→ accent-dim bg, #93c5fd text
```

---

## 8. Navigation & Page Structure

`frontend/src/App.tsx` — flat route table, everything nested in one `MainLayout`:

```tsx
<Route path="/" element={<MainLayout />}>
  <Route index element={<MedicalImageViewer />} />
  <Route path="segmentation" element={<Segmentation />} />
  <Route path="vlm" element={<VLMAnalysis />} />
  <Route path="report" element={<Report />} />
</Route>
```

4 pages, no login/worklist/admin routes wired up (backend has `models/auth.py` but no auth UI is mounted). This is a **single-study, single-viewer workflow**: upload → view → analyze → report — not a multi-patient PACS worklist. If the new viewer needs a worklist, that's new surface area, not something to port from here.

Each page composes:
- 3–5 feature hooks (`useImageData`, `useApi`, `useAIAnalysis`/`useVLMChat`/`useReport`) for its own data/async state
- `UnifiedMedicalImageViewer` (wraps `ITKDicomViewer` + `ViewerLayout` + patient/study wiring), passing `customImageData`/`customPatientInfo` overrides and a `sidebarContent` node — so every page reuses the exact same viewer chrome while injecting page-specific side panels

**Right-sidebar tab pattern** repeated across pages (e.g. `Info / Analysis / Report`): underline active-tab style, `borderBottom: 2px solid var(--accent)`.

---

## 9. State Management Pattern

No Redux/Zustand/MobX. Composition over global store:

- `useTheme.tsx` — the **only** Context (dark/light), see §3.5.
- `useImageData.ts` — upload state, backend connection, current study id, image data.
- `useApi.ts` — generic hooks: `useModels`, `useFileUpload`, `useJobStatus`.
- `useAIAnalysis.ts`, `useVLMChat.ts`, `useReport.ts` — per-feature async state + actions.
- Pages compose these hooks directly; derived values are passed as props down into shared viewer/sidebar components (prop-drilling by design, not by accident, since the tree is shallow).

**Recommendation:** keep this pattern for a similarly-scoped viewer (small number of pages, one shared viewer component). Only introduce a store (Zustand is the lightest fit given the existing hook style) if cross-page state sharing grows beyond what prop composition can handle cleanly.

---

## 10. Shared Small Components (locally redeclared, not extracted)

There is **no** `components/ui/` library. Each page currently redeclares near-identical tiny presentational components — a known gap worth fixing when building the new viewer (extract these once into a real shared module instead of copy-pasting per page):

- `SectionTitle` — icon + 11px uppercase tracked label
- `Label` — 9.5px uppercase micro-label for form fields
- `PrimaryBtn` — full-width 36px accent button; swaps label text to "Running…/Processing…/Analyzing…" while `loading` (no spinner — a deliberate stylistic choice, keep it)
- `GhostBtn` — outlined transparent button
- `SideCard` — `var(--radio-card)` bordered container, the base unit of the whole right sidebar

Truly shared (in `components/`): `PatientCard.tsx`, `FileUploadZone.tsx` (drag-and-drop with per-file validation/error list — note it uses cyan accents, a slight inconsistency with the app's blue identity; **normalize to `--accent` in the new build**).

---

## 11. UX Patterns to Carry Forward

- **Chat bubble UI** (`VLMAnalysis.tsx`): asymmetric corner radius per role — user `10px 10px 3px 10px` (solid accent fill, right-aligned), assistant `10px 10px 10px 3px` (bordered card, left-aligned).
- **Loading states**: AntD `<Spin>` over a `bg-black/50` backdrop for image fetches; inline button label swaps for actions (not spinners inside buttons).
- **Error states**: red-tinted bordered panels (`--red-dim`/`--red`) for load failures; `message.error()` toasts for async action failures.
- **Empty states**: centered icon + heading + subtext, reused verbatim for "no image loaded", canvas drop-zone, and empty chat.
- **Drag-and-drop**: both a dedicated dropzone component and a drop-anywhere-on-canvas fallback.
- **Collapsible sidebar** with icon-only compact mode, cubic-bezier width animation.
- **Persistent compliance footer** ("HIPAA Compliant · End-to-End Encrypted") — cheap trust signal, keep even in non-production builds.
- **Mobile concession only**: `@media (max-width: 768px) { button, input, select, textarea { min-height: 44px; } }` — layouts are otherwise fixed-pixel (220px/320px sidebars) and **not genuinely responsive**. If the new viewer needs to work on tablets, this needs real breakpoint work, not a port.
- **No keyboard shortcuts, no context menus** anywhere currently — clean slate if the new viewer wants them.

---

## 12. Checklist for Building a New Viewer with the Same Design Language

1. Pull in the font imports + full `:root` / `[data-theme="light"]` token block from §3.1–3.4 verbatim.
2. Implement `useTheme` context exactly as in §3.5 (same localStorage key convention, adapted name).
3. Build the two-tier shell: app `MainLayout` (sidebar nav + topbar, route-driven title) + nested `ViewerLayout` (patient strip + menu bar + tool strip + canvas + right sidebar + compliance footer).
4. On the canvas itself: floating glass toolbar (top), corner HUD overlays (patient/series info, mono font), bottom W/L + slice sliders, empty-state drop-zone.
5. Reuse `PatientCard`'s compact/full dual-mode pattern and its priority-color helper.
6. If using AntD, prefer `ConfigProvider` theme tokens over blanket `!important` CSS (see §3.7) — this repo's approach works but is brittle; improve on it rather than copy it exactly.
7. Extract the small presentational components (`SectionTitle`, `Label`, `PrimaryBtn`, `GhostBtn`, `SideCard`) into one real shared module up front instead of letting them drift per-page as they have here.
8. Normalize any stray accent colors (e.g. `FileUploadZone`'s cyan) to the single `--accent` blue for a consistent identity.
9. Decide deliberately whether the new viewer needs true responsive breakpoints or keyboard shortcuts — neither exists in this codebase today.
