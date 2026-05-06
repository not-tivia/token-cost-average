# Cardholder Projection — Design Spec

**Date:** 2026-05-05
**Status:** Approved, awaiting implementation plan

## Summary

Add a forward-looking projection feature to the Solana token tracker that estimates total token emissions and yield for Collector Crypt NFT cardholders. The user inputs how many cards they hold, what they paid for them, and an expected per-card per-quarter token rate; the app projects future emissions over a configurable horizon, values them at the current token price, and shows effective cost-per-token, break-even price, and ROI.

The feature lives entirely in the frontend (`index.html`). The backend (`app.py`) is unchanged — the existing `/api/analyze` response already exposes every value needed (`auto_airdrop_tokens`, `manual_airdrop_tokens`, `current_token_price`, `sol_price_usd`, `display_quote`).

## Motivation

The tracker currently reports past airdrops in detail but says nothing about future emissions. Collector Crypt NFTs are expected to receive quarterly community airdrops, and the user wants to plan around them — specifically to see emissions so far, projected emissions, and whether the cards have paid for themselves at current token prices.

The user expects future emissions to favor cardholders more than the first airdrop did, so the model must support setting a per-card-per-quarter rate higher than the empirically observed one.

## User-facing inputs

A new collapsible section titled **"Cardholder Projection"** sits at the bottom of the dashboard, hidden until analysis runs, collapsed by default once visible.

| Input | Type | Default |
|---|---|---|
| Cards held | integer ≥ 0 | empty |
| Card cost (USD) | number ≥ 0 | empty (optional — only needed for ROI/break-even) |
| Expected tokens per card per quarter | number ≥ 0 | auto-prefilled to `total_past_airdrop_tokens / cards_held` once cards held > 0; freely editable |
| Horizon (quarters) | slider 1–20 | 4 |

Auto-prefill rationale: `total_past_airdrop_tokens` is `auto_airdrop_tokens + manual_airdrop_tokens` from the analysis response. Treating it as "one round" gives a reasonable empirical baseline matching the user's stated "last airdrop ~1000 tokens each." The user is expected to override upward to model larger future airdrops.

Every input change recomputes the output live — no submit button, no server round-trip.

## Computations

All math runs in JS. Inputs: `cards`, `cardCost`, `rate` (per card per quarter), `quarters`. Pulled from the existing analysis response: `pastTokens` (auto + manual airdrop tokens), `currentPrice` (USD; converted from quoted price using `sol_price_usd` when `display_quote === 'SOL'`).

```
futurePerQuarter = cards × rate
futureTokens     = futurePerQuarter × quarters
totalTokens      = pastTokens + futureTokens

pastUSD    = pastTokens   × currentPrice
futureUSD  = futureTokens × currentPrice
totalUSD   = totalTokens  × currentPrice

# Only when cardCost > 0 and totalTokens > 0:
effectiveCostPerToken = cardCost / totalTokens
breakEvenPrice        = cardCost / totalTokens
roiPct                = (totalUSD - cardCost) / cardCost × 100
profitable            = currentPrice > breakEvenPrice
```

Quarter-by-quarter rows for the breakdown table:

```
for q in 1..quarters:
  tokensThisQuarter = futurePerQuarter
  cumulativeTokens  = pastTokens + futurePerQuarter × q
  cumulativeUSD     = cumulativeTokens × currentPrice
```

### Edge cases

- `cards = 0` or `rate = 0` → future numbers are 0; total = past only.
- `cardCost = 0` (or empty) → hide ROI / break-even / effective-cost tile; show only emission amounts.
- `pastTokens = 0` (no airdrops detected) → don't prefill rate; user enters manually.
- `currentPrice = 0` (price fetch failed) → show token amounts only, USD columns shown as "—".
- `quarters = 0` not possible (slider min is 1), but defensively treat as 0 future.

## Display layout

Three blocks inside the collapsible section.

### Block A — Inputs row

Four fields side-by-side, wrapping on narrow screens:

```
[Cards held: 10]  [Card cost ($): 5000]  [Expected tokens/card/qtr: 1000]  [Horizon: 4 qtrs ━●━━━━━]
```

Beneath the row, a small note: *"Future price assumed equal to current price ($X.XX)."*

### Block B — Summary tiles

Four stat tiles, mirroring the visual style of the existing Summary section:

```
┌─ Emissions so far ─┐ ┌─ Projected (4q) ─┐ ┌─ Total ─────────┐ ┌─ Effective cost/token ─┐
│ 10,000 tokens      │ │ 40,000 tokens    │ │ 50,000 tokens   │ │ $0.10                  │
│ $1,200 @ $0.12     │ │ $4,800           │ │ $6,000          │ │ break-even = $0.10     │
└────────────────────┘ └──────────────────┘ └─────────────────┘ │ current $0.12 → +20%   │
                                                                └────────────────────────┘
```

The fourth tile only renders when `cardCost > 0`. Profitable state shown in the existing "green" theme color, underwater shown in the existing "red" theme color (reuse classes already defined in `index.html`).

### Block C — Quarterly breakdown table

Collapsed-within-collapsed (closed by default):

```
Quarter | Tokens received | Cumulative tokens | Cumulative value
Q+1     | 10,000          | 20,000            | $2,400
Q+2     | 10,000          | 30,000            | $3,600
Q+3     | 10,000          | 40,000            | $4,800
Q+4     | 10,000          | 50,000            | $6,000
```

## Architecture

### Backend changes

**None.** The `/api/analyze` response already includes:

- `auto_airdrop_tokens` — past tokens from auto-detected airdrop transactions
- `manual_airdrop_tokens` — user-declared past airdrop amount
- `current_token_price` — current price in `display_quote`
- `sol_price_usd` — for SOL→USD conversion when `display_quote === 'SOL'`
- `display_quote` — `'USDC'`, `'USD'`, or `'SOL'`

USD price derivation in JS:
```
currentPriceUSD = (display_quote === 'SOL')
  ? current_token_price × sol_price_usd
  : current_token_price
```

### Frontend changes (`index.html`)

1. New `<section id="projection">` element appended to the dashboard, with a collapsible header. Hidden via CSS until analysis completes successfully.
2. Inputs (cards, card cost, expected rate, horizon) bound to a single `recomputeProjection()` function via `input` event listeners. Slider also fires `input` events.
3. Render functions for Block B (summary tiles) and Block C (quarterly table) read from the latest analysis response, which is already stashed in a module-level JS variable in the existing render flow.
4. Number formatting reuses the existing `formatNumber` / `formatCurrency` helpers already defined in `index.html`.
5. The auto-prefill of "expected rate" fires once when the user enters a non-zero `cards held` value AND the rate field is currently empty. It does not overwrite a user-edited rate.

## Out of scope

- Auto-detecting NFT count from on-chain wallet contents (deferred — user prefers manual entry).
- Per-quarter variable rates (deferred — single rate suffices for now).
- Price forecasting beyond "current price." User explicitly asked for current-price-as-future-price.
- Cluster-detection of past airdrops into discrete "rounds" — total past tokens treated as one lump for the prefill heuristic.

## Testing

This is a frontend-only feature with simple arithmetic. Verification:

1. Run the existing tracker against a wallet that has detected airdrops; confirm the new section appears.
2. Enter cards/cost/rate/horizon combinations and verify computed values by hand.
3. Edge cases: `cards=0`, `cardCost=0` (empty), `pastTokens=0` (use a wallet with no airdrop history), `display_quote='SOL'` (USD conversion path).
4. Visual: confirm collapsed-by-default, profitable green / underwater red coloring, mobile-narrow-screen wrap behavior.
