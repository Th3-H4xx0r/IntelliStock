# Brokerage brand marks

Drop the official SVGs here with these exact filenames:

| File | Brokerage type (`brokerage_type` from the API) |
|------|------------------------------------------------|
| `alpaca.svg`    | `alpaca`    |
| `kalshi.svg`    | `kalshi`    |
| `binanceus.svg` | `binanceus` |

`BrokerageLogo` (`lib/core/widgets/brokerage_logo.dart`) picks the file by type,
so nothing else needs editing — add the file and it appears.

## What the SVG should look like

- **Glyph only, no wordmark.** These render at 16×16 next to the account name,
  so a mark with "Alpaca" spelled out beside it turns to mush. Crop to the
  symbol.
- **Square-ish viewBox.** The widget draws into a `size × size` box; a wide
  viewBox letterboxes and the glyph shrinks.
- **Colour does not matter.** Every mark is repainted with `AppColors.primary`
  through a `srcIn` filter, so all three read as one purple family regardless
  of the source colours. Gradients and multiple fills collapse to flat purple.
- **Outline vs solid is up to the asset.** The tint preserves shape, not
  weight — a solid logo becomes a solid purple silhouette, a stroked one stays
  an outline. Supply stroked/outline SVGs if you want outline glyphs to match
  the other icons in the row.

## Before the files land

`BrokerageLogo` probes the asset bundle first and falls back to the previous
Material symbol (`show_chart` for Alpaca, `savings` for the rest) when a file
is missing or malformed — so a missing mark degrades quietly instead of
throwing a red box.

## After adding a file

`flutter run` must be restarted — new asset files are bundled at build time and
are not picked up by hot reload or hot restart.
