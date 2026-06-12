# Market Price History on the Trade Chart — Design

**Date:** 2026-06-11
**Status:** Approved by user

## Problem

The trade-history chart plots the user's buys/sells, a running-avg line, and two
flat reference lines (current price, break-even). There is no actual market
price history, so the dots float in space — you can't see whether a fill was
above or below the market trend, the way a Dexscreener chart shows it.

## Decision summary (user-confirmed)

- **Form:** real price history drawn as a line *behind* the existing buy/sell
  dots in the same Chart.js chart — not a separate candlestick panel, not a
  Dexscreener iframe embed.
- **Granularity:** daily closes covering the full trading history.
- **Quote mode:** renders only when `display_quote` is USDC/USD; hidden in SOL
  mode (no historical SOL conversion).
- **Data source:** GeckoTerminal public API (free, no key), fetched
  server-side and cached — consistent with the app's cache-everything pattern.
- **Cleanup:** the flat dashed "Current Price" line is removed — the market
  line's right-hand end is the current price.

## Backend (`app.py`)

New fetcher `get_price_history_usd(mint, earliest_ts)`:

1. **Pool discovery:** `GET {GECKOTERMINAL}/networks/solana/tokens/{mint}/pools`
   → choose the pool with the highest `reserve_in_usd` (deepest liquidity =
   real market price, NOT the user's own one-sided DLMM pool). Pool address is
   cached alongside the candles.
2. **OHLCV fetch:** `GET {GECKOTERMINAL}/networks/solana/pools/{pool}/ohlcv/day`
   with `limit=1000` (1000 daily candles ≈ 2.7 years — covers full history in
   one call). Keep `[timestamp, close]` pairs only. Trim to candles from
   ~7 days before `earliest_ts`.
3. **Cache:** `cache/price_history_<mint>.json` →
   `{ "pool": str, "updated_ts": int, "candles": [[ts, close_usd], ...] }`.
   - Cache hit (skip network): latest cached candle is today's (UTC).
   - Incremental: otherwise fetch and merge only candles after the last cached
     timestamp.
   - `force_fresh=True` (UI "Force Refresh") refetches the full series and
     rediscovers the pool.
4. **Error handling:** any network/HTTP/parse failure → return cached candles
   if present, else `[]`. Price-history failures must NEVER fail or delay the
   P/L analysis (wrap entirely; log a `[price-history]` console line).

## API

`/api/analyze` response gains one field:

```json
"price_history": [[1746000000, 0.291], [1746086400, 0.288], ...]
```

`[]` when unavailable. No other response changes.

## Frontend (`index.html`, `renderChart`)

- New dataset `Market Price`: `type: 'line'`, data mapped to
  `{x: new Date(ts*1000), y: close}`, thin muted line (e.g.
  `rgba(120,144,176,0.55)`, width 1.5), `pointRadius: 0`, subtle area fill,
  highest `order` so it draws behind dots/avg/break-even.
- Rendered only when `summary.display_quote` is `USDC`/`USD` and
  `price_history.length > 0`; otherwise the dataset is omitted and the chart
  looks exactly as it does today.
- Remove the flat "Current Price" dashed-line dataset. Break-even line stays.
- Tooltip: existing fallback branch already prints `label: price` for
  non-trade datasets; verify it reads well for the market line.
- Legend gains the "Market Price" entry.

## Testing

- `price_history_diag.py` (repo root, same pattern as `dlmm_diag.py`): fetches
  pool + candles for the CARDS mint, prints pool address, candle count,
  first/last candles. Verifies the data source independently of Flask.
- Manual: run app, analyze, compare the line's shape against Dexscreener's
  daily chart for the same pool; confirm dots sit on the curve, SOL mode hides
  the line, and a cold run with GeckoTerminal unreachable still analyzes.

## Addendum (2026-06-11, post-ship): DefiLlama backfill

GeckoTerminal's public API turned out to serve only the last **180 days** of
OHLCV (HTTP 401 beyond that), leaving trades older than ~6 months without a
market line. Fix: when the earliest candle is newer than the cutoff and the
cache isn't flagged `backfilled`, fetch older daily closes once from
DefiLlama (`coins.llama.fi/chart/solana:<mint>`, token-level, keyless,
**max 500 points per request** — window anchored to end at the first
GeckoTerminal candle). GeckoTerminal wins on overlapping days. The
`backfilled` flag in the cache prevents repeat DefiLlama calls; a failed
backfill leaves the flag unset so it retries on the next stale day.

## Out of scope

- Hourly/intraday candles (free APIs only keep recent months).
- Historical SOL-denominated conversion.
- Candlestick rendering.
