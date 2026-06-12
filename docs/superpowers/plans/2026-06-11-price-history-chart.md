# Market Price History on the Trade Chart — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw the real daily CARDS market price as a line behind the buy/sell dots on the trade chart, sourced from GeckoTerminal and cached server-side.

**Architecture:** A new fetcher in `app.py` discovers the deepest-liquidity pool for the mint via GeckoTerminal's free API, pulls daily OHLCV closes, and caches `[ts, close]` pairs in `cache/price_history_<mint>.json`. `/api/analyze` returns them as `price_history`. The frontend adds one Chart.js line dataset drawn behind everything else and removes the now-redundant flat "Current Price" line.

**Tech Stack:** Flask + `requests` (existing), GeckoTerminal public API (no key), Chart.js 4 (existing). Tests are plain assert scripts run with `./venv/bin/python` (project convention, see `test_dlmm_quote.py`).

**Spec:** `docs/superpowers/specs/2026-06-11-price-history-chart-design.md`

All paths below are relative to `/home/deez/solana-tracker/` (its own git repo, branch `main`). The Flask dev server auto-reloads on `.py` edits (debug=True), so no restarts are needed between tasks.

All `git commit` commands below should end the message with the trailer:
`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Backend price-history fetcher with cache

**Files:**
- Modify: `app.py` (insert after `_price_from_coingecko_sol`, around line 318)
- Test: `test_price_history.py` (create, repo root — project convention puts test scripts there)

- [ ] **Step 1: Write the failing test**

Create `test_price_history.py`:

```python
"""Failing-first test for get_price_history_usd: parsing, pool choice,
cache short-circuit, trimming, and network-failure fallback.

Run: ./venv/bin/python test_price_history.py
"""
import json
import time

import app
from app import get_price_history_usd, _pricehist_cache_path

MINT = 'TESTMINTPriceHistoryxxxxxxxxxxxxxxxxxxxxxxx'
MINT2 = 'TESTMINTPriceHistoryNoCachexxxxxxxxxxxxxxxx'
DAY = 86400
TODAY = int(time.time()) // DAY * DAY

calls = {'pools': 0, 'ohlcv': 0}


class FakeResp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200

    def json(self):
        return self._p


def fake_get(url, params=None, timeout=None):
    if '/tokens/' in url and url.endswith('/pools'):
        calls['pools'] += 1
        return FakeResp({'data': [
            {'attributes': {'address': 'POOL_SMALL', 'reserve_in_usd': '1000'}},
            {'attributes': {'address': 'POOL_BIG', 'reserve_in_usd': '50000'}},
        ]})
    if '/ohlcv/day' in url:
        calls['ohlcv'] += 1
        assert 'POOL_BIG' in url, f'should query deepest-liquidity pool, got {url}'
        return FakeResp({'data': {'attributes': {'ohlcv_list': [
            [TODAY,           0.25, 0.26, 0.24, 0.250, 1234.0],
            [TODAY - DAY,     0.24, 0.25, 0.23, 0.245, 2345.0],
            [TODAY - 2 * DAY, 0.23, 0.24, 0.22, 0.240, 3456.0],
        ]}}})
    raise AssertionError(f'unexpected URL {url}')


def raising_get(url, params=None, timeout=None):
    raise RuntimeError('network down')


# --- clean slate ---
for m in (MINT, MINT2):
    p = _pricehist_cache_path(m)
    if p.exists():
        p.unlink()

app.requests.get = fake_get

# 1. First call: fetches, parses closes ascending, caches
candles = get_price_history_usd(MINT, earliest_ts=TODAY - 2 * DAY)
assert candles == [[TODAY - 2 * DAY, 0.240], [TODAY - DAY, 0.245], [TODAY, 0.250]], candles
assert calls == {'pools': 1, 'ohlcv': 1}, calls
cached = json.loads(_pricehist_cache_path(MINT).read_text())
assert cached['pool'] == 'POOL_BIG', cached
assert len(cached['candles']) == 3, cached

# 2. Second call same day: cache has today's candle -> no network at all
candles2 = get_price_history_usd(MINT, earliest_ts=TODAY - 2 * DAY)
assert candles2 == candles
assert calls == {'pools': 1, 'ohlcv': 1}, f'cache should short-circuit, got {calls}'

# 3. Trimming: cutoff is earliest_ts - 7 days
trimmed = get_price_history_usd(MINT, earliest_ts=TODAY + 6 * DAY)  # cutoff = TODAY - DAY
assert trimmed == [[TODAY - DAY, 0.245], [TODAY, 0.250]], trimmed

# 4. Stale cache + network failure -> serves cached candles, never raises
stale = {'pool': 'POOL_BIG', 'updated_ts': 0,
         'candles': [[TODAY - 5 * DAY, 0.2], [TODAY - 4 * DAY, 0.21]]}
_pricehist_cache_path(MINT).write_text(json.dumps(stale))
app.requests.get = raising_get
fallback = get_price_history_usd(MINT, earliest_ts=0)
assert fallback == [[TODAY - 5 * DAY, 0.2], [TODAY - 4 * DAY, 0.21]], fallback

# 5. No cache + network failure -> empty list, never raises
empty = get_price_history_usd(MINT2, earliest_ts=0)
assert empty == [], empty

# 6. force_fresh rediscovers the pool and refetches
app.requests.get = fake_get
fresh = get_price_history_usd(MINT, earliest_ts=0, force_fresh=True)
assert fresh[-1] == [TODAY, 0.250], fresh
assert calls == {'pools': 2, 'ohlcv': 2}, calls

# --- cleanup ---
for m in (MINT, MINT2):
    p = _pricehist_cache_path(m)
    if p.exists():
        p.unlink()

print('OK - all price-history assertions passed')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/deez/solana-tracker && ./venv/bin/python test_price_history.py`
Expected: FAIL with `ImportError: cannot import name 'get_price_history_usd' from 'app'`

- [ ] **Step 3: Write the implementation**

In `app.py`, insert after `_price_from_coingecko_sol` (after line 317, before `get_token_price_usd`):

```python
# =========================================================================
# v3.13: GeckoTerminal daily price history (market line on the trade chart)
# =========================================================================
GECKOTERMINAL_BASE = 'https://api.geckoterminal.com/api/v2'


def _pricehist_cache_path(mint):
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f'price_history_{mint}.json'


def _gecko_top_pool(mint):
    """Address of the deepest-liquidity Solana pool trading `mint`, or ''."""
    try:
        resp = requests.get(
            f'{GECKOTERMINAL_BASE}/networks/solana/tokens/{mint}/pools',
            params={'page': 1}, timeout=10,
        )
        if resp.status_code != 200: return ''
        pools = resp.json().get('data', []) or []
        if not pools: return ''
        best = max(pools, key=lambda p: float(p.get('attributes', {}).get('reserve_in_usd', 0) or 0))
        return best.get('attributes', {}).get('address', '') or ''
    except Exception:
        return ''


def _gecko_daily_closes(pool, limit=1000):
    """[[unix_ts, close_usd], ...] ascending, or [] on any failure."""
    try:
        resp = requests.get(
            f'{GECKOTERMINAL_BASE}/networks/solana/pools/{pool}/ohlcv/day',
            params={'aggregate': 1, 'limit': limit}, timeout=15,
        )
        if resp.status_code != 200: return []
        rows = resp.json().get('data', {}).get('attributes', {}).get('ohlcv_list', []) or []
        out = [[int(r[0]), float(r[4])] for r in rows if r and len(r) >= 5 and r[4]]
        out.sort(key=lambda c: c[0])
        return out
    except Exception:
        return []


def get_price_history_usd(mint, earliest_ts, force_fresh=False):
    """Daily [ts, close_usd] pairs from ~7 days before earliest_ts to now.

    Cached in cache/price_history_<mint>.json. If the cache already holds
    today's (UTC) candle, no network call is made. On any failure the cached
    candles (or []) are returned — this function must never raise, so price
    problems can't break the P/L analysis.
    """
    path = _pricehist_cache_path(mint)
    cached = {'pool': '', 'updated_ts': 0, 'candles': []}
    if path.exists() and not force_fresh:
        try:
            cached = json.loads(path.read_text())
        except Exception:
            pass
    candles = {int(c[0]): float(c[1]) for c in cached.get('candles', [])}

    today_utc = int(time.time()) // 86400 * 86400
    last_ts = max(candles) if candles else 0
    if last_ts < today_utc:
        pool = '' if force_fresh else (cached.get('pool', '') or '')
        if not pool:
            pool = _gecko_top_pool(mint)
        if pool:
            missing_days = ((today_utc - last_ts) // 86400 + 2) if last_ts else 1000
            fresh = _gecko_daily_closes(pool, limit=min(1000, missing_days))
            for ts, close in fresh:
                candles[ts] = close
            if fresh:
                try:
                    path.write_text(json.dumps({
                        'pool': pool, 'updated_ts': int(time.time()),
                        'candles': sorted(candles.items()),
                    }))
                except Exception:
                    pass

    cutoff = (earliest_ts or 0) - 7 * 86400
    out = [[ts, c] for ts, c in sorted(candles.items()) if ts >= cutoff]
    print(f'[price-history] {len(out)} daily candles for {mint[:8]}…')
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/deez/solana-tracker && ./venv/bin/python test_price_history.py`
Expected: `OK - all price-history assertions passed`

- [ ] **Step 5: Run the existing regression test (imports app.py, catches syntax/name breakage)**

Run: `cd /home/deez/solana-tracker && ./venv/bin/python test_dlmm_quote.py`
Expected: exits 0 with its usual PASS output, no tracebacks.

- [ ] **Step 6: Commit**

```bash
cd /home/deez/solana-tracker
git add app.py test_price_history.py
git commit -m "Add GeckoTerminal daily price-history fetcher with cache"
```

---

### Task 2: Wire price history into /api/analyze + version bump

**Files:**
- Modify: `app.py:1-16` (module docstring), `app.py:~1958` (analyze route, after `calculate_summary` / before `jsonify`), `app.py:1998` (startup print)

- [ ] **Step 1: Add the fetch + response field in `analyze()`**

In `analyze()`, directly after the `best_worst = surface_best_worst_events(...)` line (~1959), add:

```python
        # v3.13: daily market price history for the chart line (never fatal)
        earliest_ts = min((t['timestamp'] for t in trades if t.get('timestamp')), default=0)
        price_history = get_price_history_usd(target_mint, earliest_ts, force_fresh=force_fresh)
```

In the `return jsonify({...})` dict, add one line after `'best_worst': best_worst,`:

```python
            'price_history':  price_history,
```

- [ ] **Step 2: Bump version strings**

Replace the module docstring (lines 1–16) with:

```python
"""
Solana Token Tracker — v3.13.

Changes from v3.12:
- Market price history: /api/analyze now returns `price_history` — daily
  [ts, close_usd] pairs for the target token from GeckoTerminal (deepest-
  liquidity pool, no API key), cached in cache/price_history_<mint>.json.
  Failures degrade to cached/empty data and never break the analysis.
- index.html: trade chart draws the market price as a muted line behind the
  buy/sell dots (USDC display mode only); the flat dashed "Current Price"
  line is removed as redundant — the market line ends at the current price.
"""
```

At line 1998, change the startup print:

```python
    print('Solana Token Tracker (v3.13) — http://localhost:5000')
```

- [ ] **Step 3: Verify end-to-end against the running server**

The dev server auto-reloads. Run:

```bash
curl -s -X POST http://localhost:5000/api/analyze -H "Content-Type: application/json" -d '{
  "wallets": "3xkr5zybajLPKui462B3ouP2cvB6YqSNjEmfHcGw1Y5p\nBbLFyprTBGKJYBiXRxVYwvfYheZaLRGoBe5yqcuRtfDx\nDFiXPhhrzMa58mS2Ax5rgX4Zuk2M7XHir58D6vkCc2u6\nFYLm2KfPKVtnNDShZ9UE6JTJwUatFLzu8kdxLfvL87kj\nHZYWwCsvH6MPfEENXxdX8gtmk9zBDHrtwCYup9HPmATs",
  "token_address": "CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp",
  "display_quote": "USDC"
}' | ./venv/bin/python -c "
import json, sys, datetime
d = json.load(sys.stdin)
ph = d.get('price_history', [])
assert len(ph) > 30, f'expected months of daily candles, got {len(ph)}'
print(len(ph), 'candles,',
      datetime.date.fromtimestamp(ph[0][0]), '->', datetime.date.fromtimestamp(ph[-1][0]),
      'last close $%.4f' % ph[-1][1])
"
```

Expected: a line like `200+ candles, 2025-XX-XX -> 2026-06-11 last close $0.24XX` (last close within ~10% of the dashboard's current price), and `cache/price_history_CARDScc….json` now exists.

- [ ] **Step 4: Commit**

```bash
cd /home/deez/solana-tracker
git add app.py
git commit -m "Return price_history from /api/analyze; bump to v3.13"
```

---

### Task 3: Diag script

**Files:**
- Create: `price_history_diag.py` (repo root, same pattern as `dlmm_diag.py`)

- [ ] **Step 1: Write the script**

```python
"""Diag: fetch GeckoTerminal daily price history for a mint.

Usage: ./venv/bin/python price_history_diag.py <mint> [earliest_unix_ts]

Always force-fetches (ignores cache read, but refreshes the cache file).
"""
import sys
import time

from app import get_price_history_usd, _gecko_top_pool


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    mint = sys.argv[1]
    earliest = int(sys.argv[2]) if len(sys.argv) > 2 else int(time.time()) - 365 * 86400

    pool = _gecko_top_pool(mint)
    print(f'Top pool: {pool or "(none found)"}')

    candles = get_price_history_usd(mint, earliest, force_fresh=True)
    print(f'{len(candles)} daily candles')
    if candles:
        f, l = candles[0], candles[-1]
        print(f'first: {time.strftime("%Y-%m-%d", time.gmtime(f[0]))}  ${f[1]:.6f}')
        print(f'last:  {time.strftime("%Y-%m-%d", time.gmtime(l[0]))}  ${l[1]:.6f}')


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run it against CARDS**

Run: `cd /home/deez/solana-tracker && ./venv/bin/python price_history_diag.py CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp`
Expected: a real pool address, 300+ candles, last close ≈ today's price (~$0.24), dates ending today.

- [ ] **Step 3: Commit**

```bash
cd /home/deez/solana-tracker
git add price_history_diag.py
git commit -m "Add price-history diag script"
```

---

### Task 4: Frontend market-price line

**Files:**
- Modify: `index.html:1123` (renderChart call site), `index.html:1404-1474` (renderChart: signature, remove flat Current Price line, add Market Price dataset)

- [ ] **Step 1: Pass price_history into renderChart**

Line 1123, change:

```js
            renderChart(trades, summary);
```
to:
```js
            renderChart(trades, summary, lastData.price_history || []);
```

(`lastData` is already set to the full response at line 643.)

- [ ] **Step 2: Update the renderChart signature**

Line 1404, change:

```js
        function renderChart(trades, summary) {
```
to:
```js
        function renderChart(trades, summary, priceHistory) {
```

- [ ] **Step 3: Replace the flat Current Price line with the market line**

Remove lines 1430–1433:

```js
            const currentPriceLine = summary.current_token_price > 0 ? [
                { x: new Date(earliest * 1000), y: summary.current_token_price },
                { x: new Date(), y: summary.current_token_price }
            ] : [];
```

and in their place add:

```js
            // Market price history (USD ~ USDC). Only drawn in USDC/USD display
            // mode — no historical SOL conversion (see 2026-06-11 design spec).
            const showMarket = (q === 'USDC' || q === 'USD') && priceHistory && priceHistory.length > 0;
            const marketLine = showMarket
                ? priceHistory.map(c => ({ x: new Date(c[0] * 1000), y: c[1] }))
                : [];
            if (showMarket && summary.current_token_price > 0) {
                marketLine.push({ x: new Date(), y: summary.current_token_price });
            }
```

- [ ] **Step 4: Swap the datasets**

Remove the Current Price dataset (lines 1468–1470):

```js
                        { label: 'Current Price', type: 'line', data: currentPriceLine,
                            borderColor: 'rgba(255, 215, 0, 0.5)', borderWidth: 1.5, borderDash: [4, 4],
                            pointRadius: 0, pointHoverRadius: 0, fill: false, order: 3 },
```

and in its place add (higher `order` = drawn first = behind the dots):

```js
                        { label: 'Market Price', type: 'line', data: marketLine,
                            borderColor: 'rgba(120, 144, 176, 0.55)', backgroundColor: 'rgba(120, 144, 176, 0.05)',
                            borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 0,
                            fill: true, tension: 0.2, order: 4 },
```

- [ ] **Step 5: Verify in the browser**

1. Open http://localhost:5000 (hard-refresh: Ctrl+Shift+R), run an analysis with the full 5-wallet list.
2. Expected: a muted blue-grey line tracing the real CARDS price under the green/red dots; dots sit on or near the curve; the right end of the line touches today's price; the gold dashed "Current Price" line is gone; "Break-even" line still present; legend shows "Market Price".
3. Hover the line: tooltip reads `Market Price: 0.24XXXXXX USDC`.
4. Switch display quote to SOL and re-analyze: the market line disappears, chart otherwise normal.
5. Compare line shape against the Dexscreener/GeckoTerminal daily chart for the CARDS pool — peaks and troughs must line up by date.

- [ ] **Step 6: Commit**

```bash
cd /home/deez/solana-tracker
git add index.html
git commit -m "Draw market price history behind trades; drop flat current-price line"
```

---

### Task 5: Final verification + failure-mode check

**Files:** none (verification only)

- [ ] **Step 1: Re-run both test scripts**

```bash
cd /home/deez/solana-tracker
./venv/bin/python test_price_history.py && ./venv/bin/python test_dlmm_quote.py
```
Expected: both pass.

- [ ] **Step 2: Failure-mode check (GeckoTerminal unreachable must not break analyze)**

```bash
cd /home/deez/solana-tracker
mv cache/price_history_CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp.json /tmp/ph_backup.json
./venv/bin/python - <<'EOF'
import app
app.GECKOTERMINAL_BASE = 'http://127.0.0.1:9'   # unroutable
ph = app.get_price_history_usd('CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp', 0)
assert ph == [], ph
print('OK - empty history, no exception')
EOF
mv /tmp/ph_backup.json cache/price_history_CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp.json
```
Expected: `OK - empty history, no exception`.

- [ ] **Step 3: Force Refresh path**

In the UI, click "Force Refresh" and confirm the chart still shows the market line afterwards (cache file's `updated_ts` changes).

- [ ] **Step 4: Final commit check**

```bash
cd /home/deez/solana-tracker && git status --short && git log --oneline -5
```
Expected: clean tree (only unrelated pre-existing modifications, if any), 4 new commits from Tasks 1–4.
