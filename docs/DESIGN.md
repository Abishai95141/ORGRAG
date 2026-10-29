# Strata — Design language

Strata should feel like a well-made research instrument: calm, legible, precise.
It shouldn't look like a generic AI product: no purple gradients, no sparkle icons,
no glowing orbs, no stock icon set.

## Concept: strata

Knowledge is layered: organizations, then collections, then documents, then
passages. The visual system is built from one motif, the **isometric plate**,
a rhombus with a thin side. Stacked plates are organizations or collections.
A dashed accent line going down through them is a query, and an accent diamond
marks what it found. Every illustration in the product (`web/src/components/illustrations`)
is built from that primitive, which keeps them consistent with one another.

## Colour

Warm paper neutrals with **one** accent, ember (`--accent`). The accent is kept for
things that matter: the send button, the live query, the answer marker, the focused
answer, deep mode. Everything else is ink on paper.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f5f3ee` | `#121211` | page |
| `--surface` | `#ffffff` | `#1a1a18` | cards, composer, menus |
| `--surface-2/3` | `#eeebe4` / `#e6e2d9` | `#22221f` / `#2b2a27` | wells, hovers |
| `--line` / `--line-strong` | `#e2ddd2` / `#cec8bb` | `#2d2c29` / `#3d3b37` | hairlines |
| `--ink` / `-2` / `-3` | `#1b1a17` / `#4d4b45` / `#87837a` | `#ece9e2` / `#b6b2a8` / `#7f7b72` | text tiers |
| `--accent` | `#d4501e` | `#ef6b37` | the one accent |
| `--chart` | `#d4501e` | `#e0632f` | chart marks (validated with the dataviz validator for each surface) |

**Organization identity is a hue.** Each org stores a hue (0–359) from a curated set
of eight. Lightness and saturation come from the theme (`--org-l`, `--org-s`), so
orgs stay distinct and readable in both light and dark mode. Org colour appears on
the org mark, on citation chips, and on the source groups. That way a reader can see
which organization a sentence came from without reading a label.

Dark mode has its own tuned values, not an inverted copy of light mode.

## Type

- **Instrument Serif** for display: page titles, greetings, empty states. It gives
  an editorial voice and is used only at large sizes.
- **Geist Sans** for all UI and body text. Answers are set at 15/1.7.
- **Geist Mono** for metadata: eyebrows, counts, citation numbers, timestamps,
  keyboard hints. Small caps-style tracking (`tracking-wider uppercase` at 10.5px).

## Iconography

`web/src/components/icons` is a hand-drawn set on a 20×20 grid with 1.5px strokes,
round caps and a 2.75px safe margin. Filled marks are used only for state (stop, dots).
Some icons carry product meaning:

- **Layers**: three offset plates (collections, and the logo).
- **Sources**: three feeds converging into a store.
- **Agent**: a compass rose.
- **Scope**: two overlapping circles (a question across organizations).
- **Quick / Deep**: one decisive stroke, and three chevrons going down through strata.
- **Trace steps**: plan (checklist), search (lens), read (open book), reflect (loop), answer (pen nib).

Collection and agent glyphs are a second set: 24px duotone with a 14% fill tint.

## Motion

Short and purposeful: 150–220ms, `cubic-bezier(.2,.7,.2,1)`.
- Content rises in (`animate-rise`) when it arrives.
- The live query line uses a moving dash (`dash-flow`).
- Running steps pulse. The streaming caret blinks in the accent colour.
- Everything respects `prefers-reduced-motion`.

## Key interaction: the answer

1. **Trace**: the agent's work shows as a vertical timeline (plan, then searches
   with the exact queries, then reads, then reflection). It stays open while running
   and folds into a one-line summary ("Researched · 4 searches · 2 reads · 9 sources · 6.2s")
   once the answer arrives.
2. **Answer**: streamed Markdown. `[n]` markers become small org-tinted chips.
   Hovering shows the source card; clicking opens the document with the cited passage
   highlighted.
3. **Sources**: cited sources appear as cards under the answer. The right rail lists
   every source found, grouped by organization, with cited ones emphasised.

## Layout

- Sidebar 260px: org switcher, a primary "New question" button, navigation,
  conversations grouped by day, and the user menu with the theme switch.
- Content column max 760px for reading, 1024px for management pages.
- Hairline borders (`1px var(--line)`) instead of shadows. Shadows only on popovers
  (`--shadow-pop`).
- Radii: 9px controls, 14px cards, 18px composer.
