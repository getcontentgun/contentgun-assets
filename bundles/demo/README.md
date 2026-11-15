# Bundle demo

A neutral bundle that shows what the renderer does: `card` and `card-image` for single cards,
`carousel-cover`, `carousel-content` and `carousel-closing` for the slides of a carousel,
addressed as `demo/<template>`. The format of a bundle is described in
[docs/bundles/README.md](../../docs/bundles/README.md).

## Design

- Dark slate `#1e2a36` with off-white text `#f3efe6` and a warm amber accent `#f2b33d`, on every card and every slide of a carousel alike.
- Headlines in Space Grotesk 700, every other text in Inter 400, the only two faces `bundle.toml` checks.
- Every template extends `_base/`: a header row on top with the handle and, on the content and closing slides, the pagination at its right end, separated from the stage by a hairline. In the stage a short bar in the accent stands above the headline; the cover puts its topic label in that place, and without a topic the bar. The cover ends in an amber swipe button at the bottom right, in a row of its own under the stage.
- `card-image` lays the image full-bleed under a slate gradient that darkens towards the bottom, where its text sits.

## Fonts

| File | Source under `https://cdn.jsdelivr.net/npm/` | Bytes |
|------|----------------------------------------------|-------|
| `SpaceGrotesk-latin.woff2` | `@fontsource-variable/space-grotesk@5.3.0/files/space-grotesk-latin-wght-normal.woff2` | 22288 |
| `SpaceGrotesk-latin-ext.woff2` | `@fontsource-variable/space-grotesk@5.3.0/files/space-grotesk-latin-ext-wght-normal.woff2` | 18940 |
| `OFL-SpaceGrotesk.txt` | `@fontsource-variable/space-grotesk@5.3.0/LICENSE` | 4401 |
| `Inter-latin.woff2` | `@fontsource-variable/inter@5.3.0/files/inter-latin-wght-normal.woff2` | 48256 |
| `Inter-latin-ext.woff2` | `@fontsource-variable/inter@5.3.0/files/inter-latin-ext-wght-normal.woff2` | 85068 |
| `OFL-Inter.txt` | `@fontsource-variable/inter@5.3.0/LICENSE` | 4477 |

Both families are under the SIL Open Font License 1.1. The files are variable fonts; their
weight axes span 300 to 700 (Space Grotesk) and 100 to 900 (Inter), which is what the faces in
`bundle.toml` declare. The latin and latin-ext subsets are the ones Google Fonts serves, and their
`unicode-range` is the one the renderer's `latin` and `latin-ext` subsets set.

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
