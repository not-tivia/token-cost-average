# Cardholder Projection — Refinement Spec

**Date:** 2026-05-06
**Status:** Approved
**Builds on:** `2026-05-05-cardholder-projection-design.md`

## Summary

Two refinements to the just-shipped Cardholder Projection feature, based on usage feedback:

1. **Move the section** from the bottom of the dashboard to immediately before the "All Events" table, so it's visible without scrolling past the entire trade history.
2. **Replace auto-summed past airdrops with a manual input.** The user reports that not every detected airdrop was cardholder-targeted — the first was a platform-usage airdrop, the second was small for cardholders alongside other recipient classes, the third was meaningfully cardholder-focused. A single auto-summed total mis-represents "cardholder emissions so far" and the auto-prefilled rate is wrong as a result. A dedicated manual input is more accurate and matches the user's mental model.

Both changes live entirely in the frontend. Backend (`app.py`) remains untouched.

## Motivation

The shipped feature uses `auto_airdrop_tokens + manual_airdrop_tokens` from `/api/analyze` as the past-emissions baseline. Three issues:

- The auto-detected count includes airdrops that were not for cardholders (platform-usage airdrop, gacha-user airdrop, etc.). Lumping them all into "cardholder emissions so far" distorts every downstream number.
- The auto-prefilled rate (past total / cards held) inherits the same distortion.
- Placing the section at the very bottom of the dashboard means the user never sees it without intentionally scrolling past the long trade table.

## Changes

### 1. Placement

Move the `<div id="projectionSection">` markup block from immediately before the closing `</div>` of `<div id="results">` to immediately before the "All Events" `<div class="section">` (the one containing the trade history table — currently around line 386 in the post-feature file).

New visual order inside `#results`:

1. Reconcile banner
2. PnL hero
3. Summary cards
4. Breakdown card
5. Impact section
6. LP section
7. Insights section
8. Auto-Detected Airdrops section
9. Auto-Detected Funding section
10. Trade History (chart) section
11. **Cardholder Projection section** ← moved here
12. All Events section

No logic change. Pure DOM reordering.

### 2. Manual past-emissions input

Replace the rate auto-prefill mechanism with a new dedicated input.

#### Input row (5 fields)

| Order | Input | Type | Default |
|---|---|---|---|
| 1 | Cards held | integer ≥ 0 | empty |
| 2 | Cardholder emissions so far (tokens) | number ≥ 0 | empty (treated as 0) |
| 3 | Card cost (USD) | number ≥ 0 | empty |
| 4 | Expected tokens / card / quarter | number ≥ 0 | empty |
| 5 | Horizon (quarters) | slider 1–20 | 4 |

The new input ID is `projPastEmissions`. Its placeholder text is `"e.g. 10000"`.

The "Expected tokens / card / quarter" placeholder changes from `"auto"` to `""` (or `"e.g. 1500"`) since there is no longer any auto-prefill.

#### `computeProjection` signature change

Currently:
```js
const pastTokens = (summary.auto_airdrop_tokens || 0) + (summary.manual_airdrop_tokens || 0);
```

After:
```js
const pastTokens = Math.max(0, Number(inputs.pastTokens) || 0);
```

`pastTokens` is now an input field, treated identically to the other clamped numeric inputs. The `summary` parameter is still needed for `getCurrentPriceUSD` (price + display_quote + sol_price_usd) but is no longer the source of past tokens.

#### Removed: `maybeAutoPrefillRate`

The function `maybeAutoPrefillRate` and all its call sites are deleted entirely. The rate field is now pure manual input. The deletion includes:

- The function definition itself
- The call from `setupProjectionListeners`'s `projCards` listener branch
- The call from `renderResults`

After this change, `setupProjectionListeners`'s `forEach` simply attaches `recomputeProjection` to every input's `input` event with no per-input branching.

#### Persistence

Bump `STORAGE_KEY` from `'solTracker.v3.15'` to `'solTracker.v3.16'`. Add `'solTracker.v3.15'` as the first fallback in `loadPrefs`. Persist a new field `projPastEmissions` in `loadPrefs` (with `!== undefined` guard) and `savePrefs`.

#### CSS

The existing `.projection-inputs` rule is `grid-template-columns: repeat(4, 1fr)` (desktop) and `1fr 1fr` (mobile). Update desktop to `repeat(5, 1fr)`. Mobile breakpoint stays `1fr 1fr` (a 5-column grid wraps to two rows of 2 + 1, which is acceptable on narrow screens).

## Out of scope

- The existing "Auto-Detected Airdrops" section (a separate collapsible showing all detected airdrops with timestamps and signatures) is **not** removed. It remains as a useful on-chain record. It just no longer feeds the projection.
- No backend changes.
- No change to `getCurrentPriceUSD`, `renderProjectionTiles`, `renderProjectionTable`, `recomputeProjection`'s structure (only the input shape it builds), or the quarterly breakdown table.

## Testing

Manual browser verification only (consistent with the original feature). Cases to walk:

- **A** — set Cards Held = 10, Past Emissions = 5000, Cost = 4000, Rate = 1500, Horizon = 4. Verify "Emissions so far: 5000 tokens", projected = 60000, total = 65000, cost-per-token math correct.
- **B** — set Past Emissions = 0 (or empty). Verify "Emissions so far: 0 tokens" and that total = future only.
- **C** — clear Cards Held. Verify future = 0 and rate auto-prefill no longer fires (the field stays at whatever the user typed).
- **D** — change Cards Held while Rate is non-empty. Verify Rate is NOT silently overwritten (since `maybeAutoPrefillRate` is gone).
- **E** — reload page. Verify the new `projPastEmissions` value persists from localStorage.
- **F** — visual: confirm the projection section now appears above the All Events table, not below it.
