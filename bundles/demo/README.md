# Bundle demo

A neutral bundle that shows what the renderer does: `card` and `card-image` for single cards,
`quote` for quotes and testimonials, `code` for source code, and `carousel-cover`,
`carousel-content` and `carousel-closing` for the slides of a carousel, addressed as
`demo/<template>`. The format of a bundle is described in
[docs/bundles/README.md](../../docs/bundles/README.md).

## Design

- Dark slate `#1e2a36` with off-white text `#f3efe6` and a warm amber accent `#f2b33d`, on every card and every slide of a carousel alike.
- Headlines and the author of a quote in Space Grotesk 700, code and its file name in JetBrains Mono 400, every other text in Inter 400, the only three faces `bundle.toml` checks.
- Every template extends `_base/`: a header row on top with the handle and, on the content and closing slides, the pagination at its right end, separated from the stage by a hairline. In the stage a short bar in the accent stands above the headline; the cover puts its topic label in that place, and without a topic the bar. The cover ends in an amber swipe button at the bottom right, in a row of its own under the stage.
- `card-image` lays the image full-bleed under a slate gradient that darkens towards the bottom, where its text sits.
- `quote` puts an amber quotation mark where the bar stands, `assets/quote.svg`, drawn for the bundle and inlined, then the optional headline, the quote in large Inter, and the author and role under it, next to a round portrait when the request carries an image.
- `code` sets the code in a window darker than the card, with three dots and the file name in its bar. The token colors are the `--color-code-*` variables of `_base/base.css`: keywords in the amber accent, strings green, numbers orange, function names blue, types teal, builtins violet, comments muted. A highlighted line gets a faint amber band across the window and an amber line number; ligatures are off, so `!=` stays two characters.

## Fonts

| File | Source under `https://cdn.jsdelivr.net/npm/` | Bytes |
|------|----------------------------------------------|-------|
| `SpaceGrotesk-latin.woff2` | `@fontsource-variable/space-grotesk@5.3.0/files/space-grotesk-latin-wght-normal.woff2` | 22288 |
| `SpaceGrotesk-latin-ext.woff2` | `@fontsource-variable/space-grotesk@5.3.0/files/space-grotesk-latin-ext-wght-normal.woff2` | 18940 |
| `OFL-SpaceGrotesk.txt` | `@fontsource-variable/space-grotesk@5.3.0/LICENSE` | 4401 |
| `Inter-latin.woff2` | `@fontsource-variable/inter@5.3.0/files/inter-latin-wght-normal.woff2` | 48256 |
| `Inter-latin-ext.woff2` | `@fontsource-variable/inter@5.3.0/files/inter-latin-ext-wght-normal.woff2` | 85068 |
| `OFL-Inter.txt` | `@fontsource-variable/inter@5.3.0/LICENSE` | 4477 |
| `JetBrainsMono-latin.woff2` | `@fontsource/jetbrains-mono@5.3.0/files/jetbrains-mono-latin-400-normal.woff2` | 21168 |
| `JetBrainsMono-latin-ext.woff2` | `@fontsource/jetbrains-mono@5.3.0/files/jetbrains-mono-latin-ext-400-normal.woff2` | 7336 |
| `OFL-JetBrainsMono.txt` | `@fontsource/jetbrains-mono@5.3.0/LICENSE` | 4524 |

All three families are under the SIL Open Font License 1.1. Space Grotesk and Inter are variable
fonts; their weight axes span 300 to 700 (Space Grotesk) and 100 to 900 (Inter), which is what the
faces in `bundle.toml` declare. JetBrains Mono is drawn in one weight only, so it comes as the
static files of weight 400. The latin and latin-ext subsets are the ones Google Fonts serves, and
their `unicode-range` is the one the renderer's `latin` and `latin-ext` subsets set.

Space Grotesk's inline box is 1.27em tall while the headline sets `line-height: 1.08`, so the
headline carries `padding: 0.12em 0`; with less, the first and last line spill out of their box
and the fit-text search stops at its minimum.

## Fallback image

`assets/backdrop.jpg` is drawn by `scripts/make_demo_backdrop.py`: a slate blue gradient with a
warm glow, computed pixel by pixel, so the bundle carries no photo whose rights would need
clearing. Run the script again after changing it:

```
.venv/bin/python scripts/make_demo_backdrop.py
```

## Text limits

The boxes and floors are set so that the service-wide character limits never clip, measured in
the renderer on 2026-11-14 with `tests/test_text_limits.py` and the numbers in
`docs/render.md`:

- The headline fits between its maximum and 40px, every second field (`subline`, `body`, `cta`) between 40px and 24px.
- At 41px and below, two 12-character chunks of `W` share a line of Space Grotesk 700, so a headline of 140 × `W` takes six lines at the floor, 269px. At 24px three chunks share a line of Inter, so 200 × `W` take six lines, 214px. Every headline box and every copy box leaves at least that much room, also on the content slide with an image or icon.
- `1:1` has the least room, so there the header row's gap shrinks from 48px to 40px and the gaps in the stage from 40px to 32px, and the cover's swipe button from 104px to 88px.
- `quote` caps its headline at 80 characters and fits it between 64px and 40px, the quote between 56px and 24px. Author and role are not fitted, so their worst case, a run of `W` at 60 and 80 characters next to the portrait, is measured as part of the quote's room: at `1:1` with a headline, everything at its limit as `W` puts the headline at 41px and the quote exactly on its floor. Measured on 2026-11-22.
- `code` caps its headline at 60 characters and fits it between 72px and 40px, the code between 36px and 24px. A character of JetBrains Mono is 0.6em wide, so 48 columns with the gutter of the line numbers fit the window from 26px down; at `1:1` the height decides, and 14 lines next to a headline at its limit land at 25px. Measured on 2026-11-22.
