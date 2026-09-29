# Neon Dusk --- Pixel Modern UI Design System

> **Purpose:** Authoritative visual and implementation reference for the
> **Demo 4 --- Neon Dusk** theme. Use this document when asking an AI
> coding assistant to build pages, dashboards, SaaS interfaces, AI-agent
> applications, landing pages, or reusable components.

> **Core aesthetic:** premium futuristic dark UI + restrained pixel
> typography + violet/cyan signal colors + glass surfaces + subtle
> technical depth.

> **Important:** This is **retro-modern**, not cyberpunk. Pixel details
> must feel intentional and functional, never gimmicky.

------------------------------------------------------------------------

## 1. Design Direction

### Theme

**Neon Dusk**

### One-line description

A premium dark interface combining modern SaaS/product design with
restrained pixel typography, violet/cyan glow, glass surfaces, and
futuristic depth.

### Personality

**Premium · Futuristic · Technical · AI-native · Minimal · Calm ·
Precise · Slightly experimental · Production-ready**

### Avoid

Arcade-game styling, aggressive cyberpunk, excessive neon, excessive
animation, visual noise, random pixel art, generic dark dashboards.

The product should look like a modern application that happens to use a
pixel-inspired visual language.

------------------------------------------------------------------------

# 2. Core Visual Principles

## 2.1 Dark foundation

Prefer deep navy/indigo-black over pure black.

``` text
Background       #0E0E20
Deep background  #090918
Panel            #1B1B39
Elevated panel   #222247
Input            rgba(5,5,16,.55)
```

The blue/purple tint gives violet and cyan accents more depth.

## 2.2 Violet = action / identity

``` text
Primary Violet   #8B5CF6
Bright Violet    #A78BFA
Deep Violet      #6D5DFC
```

Use for primary buttons, active navigation, selected states, focus
rings, important actions, AI/agent identity, and occasional headline
emphasis.

Do not make every element violet.

## 2.3 Cyan = signal / information

``` text
Cyan             #67E8F9
Bright Cyan      #22D3EE
Soft Cyan        #A5F3FC
```

Use for healthy/live states, metrics, positive changes, system signals,
data visualization highlights, and AI processing indicators.

Mental model:

``` text
Violet = ACTION / IDENTITY
Cyan   = SIGNAL / INFORMATION
```

------------------------------------------------------------------------

# 3. Color System

  Token                      Value                      Use
  -------------------------- -------------------------- -----------------------------
  `--background`             `#0E0E20`                  Main application background
  `--background-deep`        `#090918`                  Deep areas / overlays
  `--surface`                `#1B1B39`                  Main cards
  `--surface-elevated`       `#222247`                  Elevated cards
  `--surface-soft`           `rgba(255,255,255,.025)`   Secondary surfaces
  `--input`                  `rgba(5,5,16,.55)`         Form controls
  `--foreground`             `#F6F3FF`                  Primary text
  `--foreground-secondary`   `#D8D5E8`                  Secondary text
  `--muted`                  `#AAA8C9`                  Muted text
  `--subtle`                 `#74718E`                  Metadata
  `--violet`                 `#8B5CF6`                  Primary action
  `--violet-bright`          `#A78BFA`                  Highlight
  `--violet-deep`            `#6D5DFC`                  Gradient depth
  `--cyan`                   `#67E8F9`                  Signal
  `--cyan-bright`            `#22D3EE`                  Strong signal

### Borders

Prefer 1px low-opacity borders:

``` text
Default  rgba(167,139,250,.22)
Hover    rgba(167,139,250,.35)
Active   rgba(167,139,250,.50)
Subtle   rgba(255,255,255,.06)
```

Avoid thick borders except for deliberate pixel accents.

------------------------------------------------------------------------

# 4. Typography

## Display: Geist Pixel

Use for: - Hero headings - Page titles - Section headings - Large
statistics - Product names - Short important labels

``` css
font-family: "Geist Pixel", "Geist Mono", monospace;
```

It is a display language, not the body font.

## UI/body: Inter

Use for: - Body text - Navigation - Buttons - Forms - Descriptions -
Tables - General interface content

``` css
font-family: Inter, ui-sans-serif, system-ui, sans-serif;
```

## Technical: Geist Mono

Use for: - Status labels - IDs - Timestamps - System information -
Keyboard shortcuts - API/tool names - Logs - Small uppercase labels

``` css
font-family: "Geist Mono", ui-monospace, monospace;
```

### Typography rule

``` text
HEADINGS          Geist Pixel
BODY / UI         Inter
TECHNICAL DATA    Geist Mono
```

Never use the pixel font for long paragraphs.

### Suggested scale

``` text
Hero              64–80px
Page title        48–64px
Section heading   28–40px
Card heading      18–24px
Body              14–16px
Small UI          12–13px
Technical         9–11px
```

Hero line-height should be around `1.03`.

------------------------------------------------------------------------

# 5. Layout

Use a modern SaaS layout.

``` text
Content width:    1120–1200px
Large desktop:    up to 1280px
Mobile padding:   16px
Tablet padding:   24px
Desktop padding:  32–48px
```

Spacing scale:

``` text
4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64 / 80 / 96
```

Avoid arbitrary spacing values.

### Border radius

``` text
Small controls   8px
Buttons          8–10px
Cards            14–20px
App shells       20–24px
```

Do not overuse pill shapes. Reserve pills for statuses, tags, and
compact metadata.

------------------------------------------------------------------------

# 6. Glass Surfaces

Use restrained glass, not full glassmorphism.

``` css
background: rgba(27,27,57,.80);
border: 1px solid rgba(167,139,250,.22);
backdrop-filter: blur(16px);
box-shadow: inset 0 1px 0 rgba(255,255,255,.04);
```

Use primarily for: - Main cards - Navigation shells - Floating panels -
Command palettes - Modals - Important dashboard surfaces

Do not make every element transparent.

------------------------------------------------------------------------

# 7. Glow

Glow must be subtle.

### Violet

``` css
box-shadow: 0 0 28px rgba(139,92,246,.20);
```

### Cyan

``` css
box-shadow: 0 0 20px rgba(103,232,249,.14);
```

### Strong glow --- rare

``` css
box-shadow: 0 0 50px rgba(139,92,246,.18);
```

Glow communicates **active, live, important, AI processing, or primary
action**.

Never give every card/button a glow.

------------------------------------------------------------------------

# 8. Pixel Grid

Use a low-opacity grid behind the interface:

``` css
background-image:
  linear-gradient(rgba(167,139,250,.025) 1px, transparent 1px),
  linear-gradient(90deg, rgba(167,139,250,.025) 1px, transparent 1px);
background-size: 24px 24px;
```

A 16px grid is acceptable for denser layouts.

The grid must remain subordinate to the UI and must never reduce
readability.

------------------------------------------------------------------------

# 9. Pixel Accents

Good small accents:

``` text
■  ▣  +  01  //  ::
```

Use them around: - Section labels - Card corners - Active navigation -
Hero edges - Status indicators - Data labels - Background details

Do not scatter pixel art throughout the interface.

------------------------------------------------------------------------

# 10. Navigation

Recommended desktop structure:

``` text
┌─────────────────────────────────────────────────────────────┐
│ Logo        Dashboard  Automations  Insights       ⌘ K      │
└─────────────────────────────────────────────────────────────┘
```

Navigation should be compact, minimal, glassy/transparent, 1px bordered,
and small.

``` text
Active    #F6F3FF
Inactive  #AAA8C9
```

Use a violet active indicator when helpful.

------------------------------------------------------------------------

# 11. Hero

Preferred hierarchy:

``` text
TECHNICAL EYEBROW
        ↓
LARGE PIXEL HEADING
        ↓
SHORT DESCRIPTION
        ↓
PRIMARY + SECONDARY CTA
        ↓
SUPPORTING METRICS
```

Example:

``` text
CREATIVE INTELLIGENCE

Signal in
the noise.

Monitor campaigns, content velocity,
and audience signals from one quiet command surface.

[ Open workspace → ]   [ Read report ]
```

The heading should be the strongest visual element.

------------------------------------------------------------------------

# 12. Buttons

## Primary

``` css
background: linear-gradient(135deg,#8B5CF6,#6D5DFC);
color: white;
box-shadow: 0 0 28px rgba(139,92,246,.22);
```

## Secondary

``` text
background: rgba(255,255,255,.025)
border: 1px solid rgba(167,139,250,.24)
```

### Interaction

Hover: - translateY(-1px) - slightly brighter background - slightly
stronger border

Active: - translateY(0) - scale(.98)

Use:

``` css
transition:
  transform .18s ease,
  box-shadow .18s ease,
  background .18s ease,
  border-color .18s ease;
```

------------------------------------------------------------------------

# 13. Inputs

``` css
background: rgba(5,5,16,.55);
border: 1px solid rgba(167,139,250,.28);
color: #F6F3FF;
```

Placeholder should use `#74718E`.

Focus:

``` css
border-color: #8B5CF6;
box-shadow: 0 0 0 3px rgba(139,92,246,.14);
```

Inputs should feel like part of the dark surface rather than white forms
dropped onto it.

------------------------------------------------------------------------

# 14. Cards

Recommended card language:

``` text
Dark glass surface
1px subtle border
14–20px radius
Small internal highlight
Optional shadow
```

Example:

``` css
.card {
  background: rgba(27,27,57,.80);
  border: 1px solid rgba(167,139,250,.22);
  border-radius: 18px;
  box-shadow:
    0 24px 80px rgba(0,0,0,.25),
    inset 0 1px 0 rgba(255,255,255,.04);
}
```

Internal hierarchy:

``` text
Eyebrow
Heading
Description
Data/action
```

------------------------------------------------------------------------

# 15. Statistics

Use Geist Pixel for prominent values.

``` text
┌────────────┐
│ REACH      │
│            │
│ 2.4M       │
│ +18.2%     │
└────────────┘
```

Recommended: - Label → Geist Mono, 9--10px - Number → Geist Pixel,
24--32px - Change → Cyan/violet

------------------------------------------------------------------------

# 16. Status System

``` text
LIVE / HEALTHY
Cyan indicator

PROCESSING
Violet indicator

ATTENTION
Amber indicator

ERROR
Red indicator
```

Do not use color alone to communicate status.

------------------------------------------------------------------------

# 17. AI / Agent Interface

Neon Dusk is especially suited to AI command centers.

### Agent activity

``` text
Agent
├── Planning
├── Tool call
├── Searching
├── Processing
└── Complete
```

Use violet for agent identity and cyan for successful tool/system
output.

### Task card

``` text
┌─────────────────────────────────────────┐
│ AGENT / RESEARCH                        │
│                                         │
│ Searching 14 sources...                 │
│                                         │
│ ████████████████░░░░  78%              │
│                                         │
│ ● 2.4s                                  │
└─────────────────────────────────────────┘
```

### Tool call

``` text
TOOL CALL

search_web
status: complete
latency: 482ms
results: 14
```

Use Geist Mono for technical information.

### Approval

``` text
ACTION REQUIRES APPROVAL

Create calendar event
Tomorrow · 09:00–09:30

[ Reject ] [ Approve ]
```

Use violet for the primary approval action.

------------------------------------------------------------------------

# 18. Command Palette

Recommended structure:

``` text
┌─────────────────────────────────────┐
│ ⌕  Search commands...               │
├─────────────────────────────────────┤
│ → Create agent                      │
│ → Search conversations              │
│ → Open integrations                 │
│ → View activity                     │
├─────────────────────────────────────┤
│ ESC close                           │
└─────────────────────────────────────┘
```

Use: - Glass panel - Backdrop blur - Violet active row - Geist Mono
shortcuts - Keyboard navigation - Accessible focus

------------------------------------------------------------------------

# 19. Data Visualization

Prefer minimal charts: - Thin lines - Small glowing points - Violet
primary series - Cyan secondary series - Very subtle grid - No
unnecessary decoration

Suggested:

``` text
Primary  #A78BFA
Secondary #67E8F9
Grid     rgba(167,139,250,.08)
```

Avoid rainbow charts.

------------------------------------------------------------------------

# 20. Micro-interactions

Use short Framer-Motion-style transitions:

``` text
120–220ms
cubic-bezier(.2,.8,.2,1)
```

Good: - Cards lift 1--2px - Borders brighten - Buttons translate 1px -
Active navigation fades/slides - Status dots pulse subtly - Progress
bars animate - Dialogs fade + translate upward - Tabs use a sliding
active indicator

Avoid: - Large bounces - Spinning everything - Constant floating -
Excessive particles - Long cinematic transitions

Respect `prefers-reduced-motion`.

------------------------------------------------------------------------

# 21. Accessibility

The aesthetic must never compromise usability.

Requirements: - Strong readable contrast - Visible keyboard focus -
Semantic HTML - Real buttons for actions - Real labels for inputs -
`aria-label` for icon-only controls - Keyboard-accessible dialogs - Do
not rely only on color - Respect reduced motion

Focus example:

``` css
:focus-visible {
  outline: 3px solid rgba(139,92,246,.45);
  outline-offset: 3px;
}
```

------------------------------------------------------------------------

# 22. Responsive Behavior

Support mobile, tablet, desktop, and large desktop.

### Mobile

-   Compact navigation
-   Stack cards vertically
-   Scale hero typography
-   Convert two-column layouts to one column
-   Use 2-column or stacked stats

### Desktop

-   1120--1200px content width
-   Comfortable whitespace
-   Multi-column layouts where useful

------------------------------------------------------------------------

# 23. Recommended React/Next.js Component Architecture

``` text
components/
├── layout/
│   ├── Navbar
│   ├── Sidebar
│   └── PageShell
│
├── ui/
│   ├── Button
│   ├── Card
│   ├── Input
│   ├── Badge
│   ├── Tabs
│   ├── Dialog
│   ├── Tooltip
│   └── CommandPalette
│
├── data/
│   ├── StatCard
│   ├── DataTable
│   ├── Activity
│   └── Chart
│
├── ai/
│   ├── AgentCard
│   ├── ToolCall
│   ├── AgentStatus
│   ├── ApprovalCard
│   └── TaskProgress
│
└── effects/
    ├── PixelGrid
    ├── Glow
    └── Noise
```

Prefer composable shadcn/ui-style components.

------------------------------------------------------------------------

# 24. Tailwind Direction

Use Tailwind utilities where practical.

Example:

``` html
<div class="
  rounded-2xl
  border border-violet-400/20
  bg-[#1B1B39]/80
  shadow-[0_24px_80px_rgba(0,0,0,.25)]
  backdrop-blur-xl
">
```

For repeated values, establish design tokens/CSS variables instead of
scattering arbitrary values.

------------------------------------------------------------------------

# 25. Design Tokens

``` css
:root {
  --background: #0E0E20;
  --background-deep: #090918;

  --surface: #1B1B39;
  --surface-elevated: #222247;

  --foreground: #F6F3FF;
  --foreground-secondary: #D8D5E8;
  --muted: #AAA8C9;
  --subtle: #74718E;

  --violet: #8B5CF6;
  --violet-bright: #A78BFA;
  --violet-deep: #6D5DFC;

  --cyan: #67E8F9;
  --cyan-bright: #22D3EE;

  --border: rgba(167,139,250,.22);
  --border-hover: rgba(167,139,250,.35);

  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 18px;
  --radius-xl: 24px;
}
```

------------------------------------------------------------------------

# 26. Do / Don't

## DO

-   Use generous whitespace.
-   Keep the background slightly blue/purple.
-   Use violet as the primary action/identity color.
-   Use cyan as the signal/information color.
-   Use Geist Pixel mainly for headings.
-   Use Inter for normal UI.
-   Use Geist Mono for technical metadata.
-   Use 1px borders.
-   Use subtle glass.
-   Use restrained glow.
-   Use low-opacity pixel grids.
-   Keep interfaces clean and production-ready.
-   Make AI states visually understandable.
-   Use consistent spacing.
-   Make responsive behavior intentional.

## DON'T

-   Don't turn everything neon.
-   Don't use pure black everywhere.
-   Don't use pixel font for body paragraphs.
-   Don't add random pixel art.
-   Don't use thick borders everywhere.
-   Don't make every card glow.
-   Don't use excessive gradients.
-   Don't use rainbow colors.
-   Don't make every component glassmorphic.
-   Don't use constant animations.
-   Don't sacrifice contrast for aesthetics.
-   Don't make the interface look like a retro arcade game.
-   Don't copy the reference screenshot literally; reproduce its
    principles.

------------------------------------------------------------------------

# 27. Visual Priority

When deciding what should stand out:

``` text
1. Primary action / main heading
        ↓
2. Important information
        ↓
3. Supporting metrics
        ↓
4. Navigation
        ↓
5. Metadata
        ↓
6. Decorative pixel details
```

Decorative details always have the lowest visual priority.

------------------------------------------------------------------------

# 28. AI Implementation Instructions

When using this document as a coding reference:

1.  Read the complete design system before writing code.
2.  Establish color and typography tokens first.
3.  Build reusable components instead of styling every section
    independently.
4.  Use Geist Pixel only where display typography benefits from it.
5.  Keep body/UI text highly readable.
6.  Use violet as the primary action/identity color.
7.  Use cyan as the signal/information color.
8.  Keep the background dark navy/indigo rather than pure black.
9.  Add pixel-grid/noise details only after the core UI works.
10. Add micro-interactions after layout and accessibility are correct.
11. Make responsive behavior intentional from the start.
12. Do not introduce unrelated colors without semantic purpose.
13. Do not overuse glow or glass effects.
14. Preserve strong information hierarchy.
15. For AI products, apply the same language to agents, tools,
    approvals, logs, tasks, and metrics.
16. Prefer simple composable components similar to shadcn/ui.
17. Use semantic HTML and accessible focus behavior.
18. Respect `prefers-reduced-motion`.
19. Keep the implementation production-quality, not merely a visual
    mockup.
20. When choosing between a flashy and restrained effect, choose the
    restrained effect.

------------------------------------------------------------------------

# 29. Reference Keywords

``` text
premium dark SaaS
AI-native interface
modern developer tool
futuristic but calm
pixel-modern
Geist Pixel
Geist Mono
Inter
midnight violet
electric cyan
glass surface
subtle glow
technical metadata
command center
agent dashboard
minimal data visualization
shadcn-style components
Vercel-inspired restraint
Linear-inspired spacing
Framer-style micro-interactions
```

------------------------------------------------------------------------

# 30. Final Art Direction

The final interface should feel like:

> **"A futuristic AI product designed in a modern SaaS design system,
> with a subtle pixel-grid soul."**

The pixel aesthetic is a **design language**, not the entire visual
identity.

The interface should still look believable as a real product that could
ship today.

Priority:

``` text
Usability
    ↓
Information hierarchy
    ↓
Typography
    ↓
Spacing
    ↓
Component consistency
    ↓
Color
    ↓
Pixel details
    ↓
Glow / decoration
```

If pixel effects ever compete with usability, reduce the effects.

------------------------------------------------------------------------

# Quick AI Prompt

Use the attached **Neon Dusk --- Pixel Modern UI Design System** as the
authoritative visual reference for this implementation.

Do not simply copy a screenshot. Build a reusable, production-quality
interface following its typography, colors, spacing, component patterns,
pixel treatment, glow behavior, accessibility rules, and interaction
principles.

Preserve the restrained premium futuristic aesthetic.

The result should feel like a modern AI/SaaS product with a subtle
pixel-grid soul --- **not a retro arcade UI and not generic cyberpunk**.
