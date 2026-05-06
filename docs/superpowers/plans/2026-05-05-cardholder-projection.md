# Cardholder Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a frontend-only "Cardholder Projection" section to the Solana token tracker that estimates future Collector Crypt NFT airdrop emissions over a configurable horizon, values them at current price, and derives effective cost-per-token, break-even price, and ROI.

**Architecture:** Pure frontend feature. All edits live in `index.html`. The backend (`app.py`) is unchanged — every value the projection needs is already returned by `/api/analyze` (`auto_airdrop_tokens`, `manual_airdrop_tokens`, `current_token_price`, `sol_price_usd`, `display_quote`). The math runs in JS and recomputes on every input change with no server round-trip.

**Tech Stack:** Vanilla HTML/CSS/JS (no frameworks). Reuses existing helpers `fmt(n, d)`, `fmtUsd(n)`, and CSS classes `.section`, `.summary-grid`, `.summary-card`, `.summary-card.highlight`, `.summary-card.breakeven`, `.input-row`, `.input-group`, `.positive`, `.negative`.

**Spec:** `docs/superpowers/specs/2026-05-05-cardholder-projection-design.md`

**Note on TDD:** This codebase has no JS test framework and the change is small/visual. The math is structured as a pure function `computeProjection(inputs, analysis)` so it can be exercised from the browser DevTools console with `console.assert` checks. Each task ends with manual browser verification.

---

## File Structure

All work happens in `index.html`. Five logical regions of that file are touched, each in its own task:

| Region | Approximate location | What's added |
|---|---|---|
| `<style>` block | end of styles, before `</style>` | Slider styling + a couple of layout helpers |
| `<div id="results">` block | just before its closing `</div>` (currently around line 404) | New `<section id="projection">` scaffold |
| Module-level `let` state | near line 440–447 | `lastProjectionInputs` cache + STORAGE_KEY bump |
| JS functions | between `renderResults` (ends ~line 929) and `toggleSection` (~line 931) | `getCurrentPriceUSD`, `computeProjection`, `renderProjection`, helpers |
| `renderResults` body | line 825 | One call to `renderProjection(summary, airdrop_events)` and showing the section |
| `loadPrefs` / `savePrefs` | lines 449–484 | Read/write of new input fields |

No new files. No backend changes.

---

## Task 1: Add CSS for the projection section

**Files:**
- Modify: `index.html` (inside `<style>...</style>`, immediately before the closing `</style>` tag near line 235)

- [ ] **Step 1: Insert the new CSS rules**

Insert this block at the end of the existing `<style>` element, immediately before `</style>`:

```css
.projection-section { background: #12121a; border: 1px solid #1e1e2e; border-radius: 14px; padding: 20px; margin-bottom: 24px; }
.projection-section h2 { font-size: 15px; color: #ccc; font-weight: 600; margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center; }
.projection-section .subtitle { color: #666; font-size: 12px; margin-bottom: 16px; }
.projection-inputs { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
.projection-inputs .input-group label { display: block; font-size: 11px; color: #888; margin-bottom: 6px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.projection-inputs input[type="range"] { width: 100%; accent-color: #6c63ff; }
.projection-inputs .horizon-value { font-size: 12px; color: #b8b3ff; margin-top: 4px; font-family: 'JetBrains Mono', monospace; }
.projection-price-note { color: #666; font-size: 11px; margin-bottom: 16px; line-height: 1.5; }
.projection-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 16px; }
.projection-tile { background: #0a0a0f; border: 1px solid #1a1a26; border-radius: 10px; padding: 14px; }
.projection-tile.profitable { border-color: rgba(76, 175, 80, 0.4); background: linear-gradient(135deg, #0f2517 0%, #0a0a0f 100%); }
.projection-tile.underwater { border-color: rgba(255, 107, 107, 0.4); background: linear-gradient(135deg, #2a1515 0%, #0a0a0f 100%); }
.projection-tile .label { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; margin-bottom: 6px; }
.projection-tile .value { font-size: 20px; font-weight: 700; color: #fff; line-height: 1.1; word-break: break-all; }
.projection-tile .sub { font-size: 11px; color: #888; margin-top: 6px; line-height: 1.5; }
.projection-table-wrap { overflow-x: auto; }
.projection-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.projection-table th { text-align: left; color: #666; font-size: 10px; padding: 8px 10px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; border-bottom: 1px solid #1e1e2e; }
.projection-table td { padding: 8px 10px; border-bottom: 1px solid #0e0e16; font-family: 'JetBrains Mono', monospace; }
@media (max-width: 768px) { .projection-inputs { grid-template-columns: 1fr 1fr; } }
```

- [ ] **Step 2: Verify**

Open the page in a browser. The page should still render normally (CSS additions don't affect anything yet because there's no matching markup).

- [ ] **Step 3: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Add CSS for cardholder projection section

Adds .projection-section, .projection-tile (with profitable/underwater
variants), input grid, and quarterly table styles. No markup uses
these yet — wired up in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add the HTML scaffold

**Files:**
- Modify: `index.html` (inside `<div id="results">...</div>`, immediately before its closing `</div>` — currently around line 404, just after the All Events section)

- [ ] **Step 1: Insert the projection markup**

Find the closing `</div>` of `<div id="results" style="display:none;">` (it's the last `</div>` before `<div id="loading"`). Insert the following markup directly **before** that closing `</div>`, so the projection section sits at the bottom of the dashboard:

```html
            <div id="projectionSection" class="projection-section" style="display:none;">
                <h2>Cardholder Projection
                    <span style="cursor:pointer; color:#6c63ff; font-size:12px; font-weight:400;" onclick="toggleSection('projectionBody')">show/hide</span>
                </h2>
                <p class="subtitle">Estimate future token emissions and yield from Collector Crypt NFT cardholder airdrops.</p>
                <div id="projectionBody" style="display:none;">
                    <div class="projection-inputs">
                        <div class="input-group">
                            <label>Cards held</label>
                            <input type="number" id="projCards" min="0" step="1" placeholder="0">
                        </div>
                        <div class="input-group">
                            <label>Card cost (USD)</label>
                            <input type="number" id="projCardCost" min="0" step="any" placeholder="optional">
                        </div>
                        <div class="input-group">
                            <label>Expected tokens / card / quarter</label>
                            <input type="number" id="projRate" min="0" step="any" placeholder="auto">
                        </div>
                        <div class="input-group">
                            <label>Horizon (quarters)</label>
                            <input type="range" id="projHorizon" min="1" max="20" step="1" value="4">
                            <div class="horizon-value" id="projHorizonValue">4 quarters (1.0 year)</div>
                        </div>
                    </div>
                    <div class="projection-price-note" id="projPriceNote"></div>
                    <div class="projection-tiles" id="projectionTiles"></div>
                    <div style="margin-top:12px;">
                        <span style="cursor:pointer; color:#6c63ff; font-size:12px;" onclick="toggleSection('projectionTableWrap')">show/hide quarterly breakdown</span>
                        <div id="projectionTableWrap" class="projection-table-wrap" style="display:none; margin-top:8px;">
                            <table class="projection-table">
                                <thead>
                                    <tr><th>Quarter</th><th>Tokens received</th><th>Cumulative tokens</th><th>Cumulative value</th></tr>
                                </thead>
                                <tbody id="projectionTableBody"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
```

- [ ] **Step 2: Verify**

Reload the page and run an analysis. The projection section should still be hidden (it has `display:none;`) — the section will be revealed in Task 8. There should be no visible page changes yet, and no JS console errors.

- [ ] **Step 3: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Add HTML scaffold for cardholder projection section

Adds the collapsible <section id="projectionSection"> with inputs
(cards held, card cost, expected rate, horizon slider), a tiles
container, and a quarterly breakdown table. Section is hidden until
analysis runs (wired in a later task).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add module-level state and the USD-price helper

**Files:**
- Modify: `index.html` — two regions:
  - The `let` declarations block near line 440–447
  - Insert a new function between `renderResults` (ends ~line 929) and `toggleSection` (line 931)

- [ ] **Step 1: Bump the storage key and add input fields to it**

Find the line:

```js
const STORAGE_KEY = 'solTracker.v3.14';
```

Replace it with:

```js
const STORAGE_KEY = 'solTracker.v3.15';
```

Then in `loadPrefs()` (currently around lines 449–471), find the existing fallback chain:

```js
const raw = localStorage.getItem(STORAGE_KEY)
    || localStorage.getItem('solTracker.v3.13')
    || localStorage.getItem('solTracker.v3.11')
    || localStorage.getItem('solTracker.v3.10');
```

Replace it with:

```js
const raw = localStorage.getItem(STORAGE_KEY)
    || localStorage.getItem('solTracker.v3.14')
    || localStorage.getItem('solTracker.v3.13')
    || localStorage.getItem('solTracker.v3.11')
    || localStorage.getItem('solTracker.v3.10');
```

Inside `loadPrefs()`, after the line `if (p.heliusKey) document.getElementById('heliusKey').value = p.heliusKey;`, add:

```js
                if (p.projCards !== undefined) document.getElementById('projCards').value = p.projCards;
                if (p.projCardCost !== undefined) document.getElementById('projCardCost').value = p.projCardCost;
                if (p.projRate !== undefined) document.getElementById('projRate').value = p.projRate;
                if (p.projHorizon !== undefined) document.getElementById('projHorizon').value = p.projHorizon;
```

In `savePrefs()` (currently around lines 472–484), inside the JSON.stringify object, add these fields after `quote: selectedQuote,`:

```js
                    projCards: document.getElementById('projCards').value,
                    projCardCost: document.getElementById('projCardCost').value,
                    projRate: document.getElementById('projRate').value,
                    projHorizon: document.getElementById('projHorizon').value,
```

- [ ] **Step 2: Add the USD-price helper**

Insert this function between the closing `}` of `renderResults` and the `function toggleSection` line:

```js
        function getCurrentPriceUSD(summary) {
            if (!summary || !summary.current_token_price) return 0;
            if (summary.display_quote === 'SOL') {
                return summary.current_token_price * (summary.sol_price_usd || 0);
            }
            return summary.current_token_price;
        }
```

- [ ] **Step 3: Verify**

Reload the page. Open DevTools console and confirm:
- No errors on load.
- `STORAGE_KEY` evaluates to `'solTracker.v3.15'` (type `STORAGE_KEY` in console).
- After running analysis, `getCurrentPriceUSD(lastData.summary)` returns a positive number when prices were fetched, or `0` if not.

- [ ] **Step 4: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Add USD-price helper and projection prefs persistence

Bumps STORAGE_KEY to v3.15 and persists the four new projection
inputs across reloads (with v3.14 fallback). Adds
getCurrentPriceUSD(summary) which converts SOL-quoted prices using
sol_price_usd.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Implement the pure `computeProjection` math function

**Files:**
- Modify: `index.html` — insert function between `getCurrentPriceUSD` (added in Task 3) and `toggleSection`

- [ ] **Step 1: Add the function**

Insert this function immediately after `getCurrentPriceUSD`:

```js
        function computeProjection(inputs, summary) {
            const cards    = Math.max(0, Number(inputs.cards)    || 0);
            const cardCost = Math.max(0, Number(inputs.cardCost) || 0);
            const rate     = Math.max(0, Number(inputs.rate)     || 0);
            const quarters = Math.max(0, Math.floor(Number(inputs.quarters) || 0));

            const pastTokens   = (summary.auto_airdrop_tokens || 0) + (summary.manual_airdrop_tokens || 0);
            const priceUSD     = getCurrentPriceUSD(summary);

            const futurePerQuarter = cards * rate;
            const futureTokens     = futurePerQuarter * quarters;
            const totalTokens      = pastTokens + futureTokens;

            const pastUSD   = pastTokens   * priceUSD;
            const futureUSD = futureTokens * priceUSD;
            const totalUSD  = totalTokens  * priceUSD;

            const hasCost = cardCost > 0 && totalTokens > 0;
            const effectiveCostPerToken = hasCost ? (cardCost / totalTokens) : null;
            const breakEvenPrice        = effectiveCostPerToken;
            const roiPct                = (cardCost > 0) ? ((totalUSD - cardCost) / cardCost * 100) : null;
            const profitable            = (priceUSD > 0 && breakEvenPrice !== null) ? (priceUSD > breakEvenPrice) : null;

            const quarterRows = [];
            for (let q = 1; q <= quarters; q++) {
                const cumulativeTokens = pastTokens + futurePerQuarter * q;
                quarterRows.push({
                    quarter: q,
                    tokensThisQuarter: futurePerQuarter,
                    cumulativeTokens,
                    cumulativeUSD: cumulativeTokens * priceUSD,
                });
            }

            return {
                cards, cardCost, rate, quarters,
                pastTokens, futureTokens, totalTokens,
                pastUSD, futureUSD, totalUSD,
                priceUSD,
                effectiveCostPerToken, breakEvenPrice, roiPct, profitable,
                hasCost,
                quarterRows,
            };
        }
```

- [ ] **Step 2: Verify with DevTools console assertions**

Reload the page, open DevTools console, and run:

```js
const fakeSummary = {
    auto_airdrop_tokens: 10000,
    manual_airdrop_tokens: 0,
    current_token_price: 0.12,
    sol_price_usd: 200,
    display_quote: 'USDC',
};
const r = computeProjection({cards: 10, cardCost: 5000, rate: 1000, quarters: 4}, fakeSummary);
console.assert(r.pastTokens === 10000, 'pastTokens', r.pastTokens);
console.assert(r.futureTokens === 40000, 'futureTokens', r.futureTokens);
console.assert(r.totalTokens === 50000, 'totalTokens', r.totalTokens);
console.assert(Math.abs(r.totalUSD - 6000) < 0.001, 'totalUSD', r.totalUSD);
console.assert(Math.abs(r.effectiveCostPerToken - 0.10) < 1e-9, 'effectiveCostPerToken', r.effectiveCostPerToken);
console.assert(Math.abs(r.roiPct - 20) < 1e-9, 'roiPct', r.roiPct);
console.assert(r.profitable === true, 'profitable', r.profitable);
console.assert(r.quarterRows.length === 4, 'quarterRows length', r.quarterRows.length);
console.log('all asserts passed');

// SOL-quoted summary path
const solSummary = {auto_airdrop_tokens: 10000, manual_airdrop_tokens: 0, current_token_price: 0.0006, sol_price_usd: 200, display_quote: 'SOL'};
const r2 = computeProjection({cards: 0, cardCost: 0, rate: 0, quarters: 4}, solSummary);
console.assert(Math.abs(r2.priceUSD - 0.12) < 1e-9, 'SOL conversion', r2.priceUSD);
console.assert(r2.effectiveCostPerToken === null, 'effectiveCostPerToken null when no cost');
console.assert(r2.profitable === null, 'profitable null when no cost');
console.log('edge cases passed');
```

Expected: both `console.log` lines print, no `console.assert` failures.

- [ ] **Step 3: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Add pure computeProjection() math for cardholder projection

Pure function takes (inputs, summary) and returns past/future/total
token counts, USD values, effective cost-per-token, break-even,
ROI%, and per-quarter rows. Handles SOL-quoted prices via
getCurrentPriceUSD. No DOM yet — wiring follows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Render the summary tiles (Block B)

**Files:**
- Modify: `index.html` — insert function after `computeProjection`

- [ ] **Step 1: Add the tile renderer**

Insert this function immediately after `computeProjection`:

```js
        function renderProjectionTiles(p) {
            const priceStr = p.priceUSD > 0 ? fmtUsd(p.priceUSD) : '—';
            const tiles = [];

            tiles.push(`
                <div class="projection-tile">
                    <div class="label">Emissions so far</div>
                    <div class="value">${fmt(p.pastTokens, 0)} tokens</div>
                    <div class="sub">${p.priceUSD > 0 ? fmtUsd(p.pastUSD) : '—'} @ ${priceStr}</div>
                </div>
            `);
            tiles.push(`
                <div class="projection-tile">
                    <div class="label">Projected (${p.quarters}q)</div>
                    <div class="value">${fmt(p.futureTokens, 0)} tokens</div>
                    <div class="sub">${p.priceUSD > 0 ? fmtUsd(p.futureUSD) : '—'}</div>
                </div>
            `);
            tiles.push(`
                <div class="projection-tile">
                    <div class="label">Total</div>
                    <div class="value">${fmt(p.totalTokens, 0)} tokens</div>
                    <div class="sub">${p.priceUSD > 0 ? fmtUsd(p.totalUSD) : '—'}</div>
                </div>
            `);

            if (p.hasCost) {
                const cls = p.profitable === true ? 'profitable' : (p.profitable === false ? 'underwater' : '');
                const roiStr = p.roiPct === null ? '—'
                    : (p.roiPct >= 0 ? `+${p.roiPct.toFixed(1)}%` : `${p.roiPct.toFixed(1)}%`);
                const roiCls = p.roiPct === null ? '' : (p.roiPct >= 0 ? 'positive' : 'negative');
                const beStr = p.breakEvenPrice === null ? '—' : fmtUsd(p.breakEvenPrice);
                const priceCmp = (p.priceUSD > 0 && p.breakEvenPrice !== null)
                    ? `current ${fmtUsd(p.priceUSD)} → <span class="${roiCls}">${roiStr}</span>`
                    : '';
                tiles.push(`
                    <div class="projection-tile ${cls}">
                        <div class="label">Effective cost / token</div>
                        <div class="value">${beStr}</div>
                        <div class="sub">break-even = ${beStr}<br>${priceCmp}</div>
                    </div>
                `);
            }

            document.getElementById('projectionTiles').innerHTML = tiles.join('');
        }
```

- [ ] **Step 2: Verify in DevTools**

Reload, run an analysis, then in console:

```js
const r = computeProjection({cards: 10, cardCost: 5000, rate: 1000, quarters: 4}, lastData.summary);
renderProjectionTiles(r);
document.getElementById('projectionSection').style.display = 'block';
document.getElementById('projectionBody').style.display = 'block';
```

Expected: four tiles render inside the projection section. Token counts and USD values match the Task 4 assertions for your synthetic input. The fourth tile shows green-tinted background if profitable, red if underwater.

- [ ] **Step 3: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Render projection summary tiles

renderProjectionTiles() builds the four-tile Block B (emissions so
far, projected, total, effective cost/token). Cost tile is omitted
when card cost is zero, and tinted green/red based on whether the
current price is above or below break-even.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Render the quarterly breakdown table (Block C)

**Files:**
- Modify: `index.html` — insert function after `renderProjectionTiles`

- [ ] **Step 1: Add the table renderer**

Insert this function immediately after `renderProjectionTiles`:

```js
        function renderProjectionTable(p) {
            const tbody = document.getElementById('projectionTableBody');
            if (p.quarterRows.length === 0) {
                tbody.innerHTML = `<tr><td colspan="4" style="color:#666; text-align:center;">No future quarters in projection</td></tr>`;
                return;
            }
            tbody.innerHTML = p.quarterRows.map(r => {
                const usdCell = p.priceUSD > 0 ? fmtUsd(r.cumulativeUSD) : '—';
                return `<tr>
                    <td>Q+${r.quarter}</td>
                    <td>${fmt(r.tokensThisQuarter, 0)}</td>
                    <td>${fmt(r.cumulativeTokens, 0)}</td>
                    <td>${usdCell}</td>
                </tr>`;
            }).join('');
        }
```

- [ ] **Step 2: Verify in DevTools**

Reload, run analysis, then in console:

```js
const r = computeProjection({cards: 10, cardCost: 5000, rate: 1000, quarters: 4}, lastData.summary);
renderProjectionTable(r);
document.getElementById('projectionSection').style.display = 'block';
document.getElementById('projectionBody').style.display = 'block';
document.getElementById('projectionTableWrap').style.display = 'block';
```

Expected: 4 rows labelled Q+1 through Q+4, each showing 10,000 tokens received, cumulative tokens 20,000 / 30,000 / 40,000 / 50,000, and matching USD values.

- [ ] **Step 3: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Render quarterly projection breakdown table

renderProjectionTable() fills the Block C tbody with per-quarter
rows: tokens received this quarter, cumulative tokens, cumulative
USD value. Empty state shown when horizon is 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire the orchestrator, event listeners, and auto-prefill

**Files:**
- Modify: `index.html` — insert orchestrator function after `renderProjectionTable`, and a one-time setup function

- [ ] **Step 1: Add the orchestrator and setup**

Insert these functions immediately after `renderProjectionTable`:

```js
        function recomputeProjection() {
            if (!lastData || !lastData.summary) return;
            const horizon = parseInt(document.getElementById('projHorizon').value, 10) || 0;
            document.getElementById('projHorizonValue').textContent =
                `${horizon} quarter${horizon === 1 ? '' : 's'} (${(horizon / 4).toFixed(1)} year${horizon === 4 ? '' : 's'})`;

            const inputs = {
                cards:    parseFloat(document.getElementById('projCards').value)    || 0,
                cardCost: parseFloat(document.getElementById('projCardCost').value) || 0,
                rate:     parseFloat(document.getElementById('projRate').value)     || 0,
                quarters: horizon,
            };
            const result = computeProjection(inputs, lastData.summary);

            const priceUSD = result.priceUSD;
            document.getElementById('projPriceNote').innerHTML =
                priceUSD > 0
                    ? `Future price assumed equal to current price (<b>${fmtUsd(priceUSD)}</b>).`
                    : `Current price unavailable — token amounts only.`;

            renderProjectionTiles(result);
            renderProjectionTable(result);
            savePrefs();
        }

        function maybeAutoPrefillRate() {
            const rateEl  = document.getElementById('projRate');
            const cardsEl = document.getElementById('projCards');
            if (!lastData || !lastData.summary) return;
            const cards = parseFloat(cardsEl.value) || 0;
            if (cards <= 0) return;
            if (rateEl.value && rateEl.value !== '0') return;  // user already set it
            const past = (lastData.summary.auto_airdrop_tokens || 0) + (lastData.summary.manual_airdrop_tokens || 0);
            if (past <= 0) return;
            rateEl.value = (past / cards).toFixed(2);
        }

        function setupProjectionListeners() {
            if (window.__projectionListenersInstalled) return;
            window.__projectionListenersInstalled = true;
            const ids = ['projCards', 'projCardCost', 'projRate', 'projHorizon'];
            ids.forEach(id => {
                document.getElementById(id).addEventListener('input', () => {
                    if (id === 'projCards') maybeAutoPrefillRate();
                    recomputeProjection();
                });
            });
        }
```

- [ ] **Step 2: Verify**

Reload, run analysis, then in console:

```js
setupProjectionListeners();
document.getElementById('projectionSection').style.display = 'block';
document.getElementById('projectionBody').style.display = 'block';
```

Type `10` into "Cards held" — the "Expected tokens / card / quarter" field should auto-fill with `<past_airdrop_tokens / 10>` (assuming the analyzed wallet had detected airdrops; if not, it stays empty). Drag the horizon slider — the horizon value text and tiles should update live. Type a card cost — the fourth tile should appear/update.

- [ ] **Step 3: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Wire projection inputs to live recompute + auto-prefill rate

recomputeProjection() reads the four inputs, runs the math, updates
the price note, summary tiles, and quarterly table.
maybeAutoPrefillRate() fills "expected rate" once when the user
enters a non-zero "cards held" and detected past airdrops exist.
setupProjectionListeners() installs input listeners idempotently.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Hook into `renderResults` to show the section after analysis

**Files:**
- Modify: `index.html` — inside `renderResults` (currently lines 825–929)

- [ ] **Step 1: Reveal the section and trigger initial recompute**

Find the last line of `renderResults`:

```js
            document.getElementById('results').style.display = 'block';
        }
```

Insert these lines **immediately before** `document.getElementById('results').style.display = 'block';`:

```js
            document.getElementById('projectionSection').style.display = 'block';
            setupProjectionListeners();
            maybeAutoPrefillRate();
            recomputeProjection();
```

So the end of `renderResults` becomes:

```js
            renderChart(trades, summary);
            document.getElementById('projectionSection').style.display = 'block';
            setupProjectionListeners();
            maybeAutoPrefillRate();
            recomputeProjection();
            document.getElementById('results').style.display = 'block';
        }
```

- [ ] **Step 2: Verify the end-to-end flow**

Reload the page (no DevTools tweaking this time). Run an analysis on a wallet with detected airdrops. Expected:

1. The "Cardholder Projection" header appears at the bottom of the dashboard.
2. Click "show/hide" — the inputs and tiles area expands.
3. Type a number in "Cards held" — "Expected tokens / card / quarter" auto-fills, tiles update.
4. Type a card cost — the fourth tile (effective cost / token) appears with green or red tint based on profitability.
5. Drag the horizon slider — projected/total tiles and quarterly table update live.
6. Click "show/hide quarterly breakdown" — the table appears with rows Q+1 through Q+horizon.
7. Reload the browser — the four input values persist.
8. Run analysis on a wallet with NO airdrops — section still appears, "Expected rate" does NOT auto-fill, tiles show 0 emissions so far.

- [ ] **Step 3: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Show projection section after analysis completes

Reveals #projectionSection at the end of renderResults, installs
input listeners, runs auto-prefill, and triggers an initial
recompute so the tiles populate as soon as the user opens the
collapsible.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Manual end-to-end verification of edge cases

**Files:**
- None (verification only)

This is a checklist task — no code changes. Run the Flask app and step through each case below in a browser. If anything is wrong, write a follow-up task to fix it before considering the feature done.

- [ ] **Step 1: Start the server**

```bash
cd solana-tracker
. venv/bin/activate 2>/dev/null || python3 -m venv venv && . venv/bin/activate && pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000` in a browser.

- [ ] **Step 2: Walk through edge cases**

For each case, check the listed expectations.

**Case A — wallet with detected airdrops, full inputs:**
- Run analysis on a wallet that has airdrops.
- Expand the projection section. Set Cards=10, Cost=5000, leave Rate empty (auto-fills), Horizon=4.
- Expected: 4 tiles render; quarterly table shows 4 rows; price note shows "Future price assumed equal to current price ($X)".

**Case B — wallet with no airdrops:**
- Analyze a wallet with zero detected airdrops.
- Expected: section still appears; "Emissions so far" shows 0 tokens; entering Cards does NOT auto-fill Rate (you must type it manually); after typing a Rate, Projected/Total tiles populate.

**Case C — no card cost:**
- Set Cards=10, Rate=1000, Horizon=4, leave Cost blank.
- Expected: only 3 tiles render (no "Effective cost / token" tile); no green/red coloring.

**Case D — underwater (cost > total value):**
- Set Cards=1, Cost=10000, Rate=1, Horizon=1 (so total emissions are tiny).
- Expected: "Effective cost / token" tile is red-tinted; ROI shows negative; "current → -X%" rendered in `.negative` class.

**Case E — SOL display quote:**
- Switch the quote toggle to SOL, run analysis again.
- Expected: tiles still show USD values (we always convert); price note shows USD; verify with DevTools `getCurrentPriceUSD(lastData.summary)` is positive.

**Case F — horizon slider edges:**
- Drag horizon to 1, then to 20.
- Expected: "X quarters (Y.Y years)" updates; quarterly table grows; tiles update live with each tick.

**Case G — persistence:**
- Reload the page (don't re-analyze). Re-run the same analysis.
- Expected: the four projection input values are restored from localStorage.

**Case H — current price unavailable:**
- Hard to reproduce naturally; simulate in DevTools console after a successful analysis:
  ```js
  lastData.summary.current_token_price = 0;
  recomputeProjection();
  ```
- Expected: USD columns show "—"; price note says "Current price unavailable — token amounts only."; no JS errors.

- [ ] **Step 3: If everything passes, no commit needed (verification-only task)**

If a case fails, do NOT mark the plan complete. Open a new debugging task, fix the bug, then re-verify the failing case.

---

## Self-Review Checklist (already run by the plan author)

**Spec coverage:**
- Inputs (cards, cost, rate, horizon) → Task 2 (HTML) + Task 3 (prefs) + Task 7 (listeners) ✓
- Computations → Task 4 ✓
- Edge cases (cards=0, cost=0, pastTokens=0, price=0) → covered in `computeProjection` (Task 4) and verified in Task 9 ✓
- Block A inputs row → Task 2 ✓
- Block B summary tiles → Task 5 ✓
- Block C quarterly table → Task 6 ✓
- Auto-prefill rate → Task 7 ✓
- "Future price assumed equal to current price" note → Task 7 ✓
- SOL→USD conversion → Task 3 ✓
- Profitable green / underwater red coloring → Task 1 (CSS) + Task 5 (class) ✓
- Section hidden until analysis runs → Task 2 (display:none) + Task 8 (reveal) ✓
- Backend unchanged → no backend tasks ✓

**Type/name consistency:** `computeProjection`, `renderProjectionTiles`, `renderProjectionTable`, `recomputeProjection`, `maybeAutoPrefillRate`, `setupProjectionListeners`, `getCurrentPriceUSD` are all defined and referenced consistently. Element IDs `projectionSection`, `projectionBody`, `projectionTiles`, `projectionTableBody`, `projectionTableWrap`, `projCards`, `projCardCost`, `projRate`, `projHorizon`, `projHorizonValue`, `projPriceNote` are all defined in Task 2 and referenced consistently elsewhere.

**Placeholder scan:** No TBD / TODO / "implement later" / vague language. Every code step shows the actual code.
