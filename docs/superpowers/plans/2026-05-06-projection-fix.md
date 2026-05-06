# Cardholder Projection — Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Apply two refinements to the Cardholder Projection section: move it above the All Events table, and replace the auto-summed past-airdrops baseline with a dedicated manual input.

**Architecture:** Frontend-only. All edits in `solana-tracker/index.html`. Backend untouched.

**Spec:** `solana-tracker/docs/superpowers/specs/2026-05-06-projection-fix-design.md`

**Tech Stack:** Vanilla HTML/CSS/JS. Reuses existing helpers `fmt`, `fmtUsd`, `getCurrentPriceUSD`, and CSS classes from the prior feature.

**Note on line numbers:** several prior tasks have shifted line numbers in `index.html`. Tasks below give text anchors (use `grep -n` to locate) rather than absolute line numbers.

---

## Task 1: Move the projection section above the All Events table

**Files:**
- Modify: `index.html`

The current location of `<div id="projectionSection" class="projection-section" style="display:none;">` is immediately before the closing `</div>` of `<div id="results">`. The destination is immediately before the All Events `<div class="section">` (the one whose `<h2>` contains `"All Events"`).

- [ ] **Step 1: Locate both anchor regions**

```bash
grep -n 'id="projectionSection"' index.html
grep -n '<h2>All Events' index.html
grep -n '<div id="loading"' index.html
```

The first command gives the start of the section to move. The All Events section is a `<div class="section">` whose immediate child is the `<h2>All Events` line; you want to insert before its opening `<div class="section">` line. The third gives the line where `#results` ends — the section currently sits just before there.

- [ ] **Step 2: Cut and paste the markup**

Move the entire `<div id="projectionSection" ...>...</div>` block (40+ lines, runs from `<div id="projectionSection"` to its matching closing `</div>`) so that it appears immediately before the `<div class="section">` that contains `<h2>All Events`. Indentation should match the surrounding sibling divs (12 spaces).

- [ ] **Step 3: Verify**

```bash
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('index.html').read()); print('parse OK')"
grep -n 'id="projectionSection"' index.html
grep -n '<h2>All Events' index.html
```

`projectionSection` should now appear at a smaller line number than `<h2>All Events`. The order in the file should be: Trade History chart section → projectionSection → All Events section.

- [ ] **Step 4: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Move Cardholder Projection above All Events

The section now sits immediately before the trade history table
instead of below it, so it's visible without scrolling past every
transaction. Pure DOM reorder; no logic change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add the "Past emissions" input + adjust CSS grid

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Add the new input as the second `.input-group` in the projection-inputs row**

Find the projection input markup (search for `projCards`):

```bash
grep -n 'id="projCards"' index.html
```

The current first three inputs render in this order:

```html
<div class="input-group"><label>Cards held</label><input ... id="projCards" ...></div>
<div class="input-group"><label>Card cost (USD)</label><input ... id="projCardCost" ...></div>
<div class="input-group"><label>Expected tokens / card / quarter</label><input ... id="projRate" ...></div>
```

Insert a new `.input-group` between "Cards held" and "Card cost" so the row reads: Cards held → Past emissions → Card cost → Expected rate → Horizon. The new block:

```html
                        <div class="input-group">
                            <label>Cardholder emissions so far (tokens)</label>
                            <input type="number" id="projPastEmissions" min="0" step="any" placeholder="e.g. 10000">
                        </div>
```

- [ ] **Step 2: Update the rate input's placeholder**

Find the existing rate input:

```bash
grep -n 'id="projRate"' index.html
```

Change `placeholder="auto"` to `placeholder="e.g. 1500"`. The rate is no longer auto-derived, so the placeholder should not promise auto behavior.

- [ ] **Step 3: Update the CSS grid from 4 to 5 columns**

Find the CSS rule:

```bash
grep -n 'projection-inputs.*grid-template-columns' index.html
```

Change `grid-template-columns: repeat(4, 1fr);` to `grid-template-columns: repeat(5, 1fr);`. Leave the mobile breakpoint at `1fr 1fr` — it will wrap a 5-column grid to a 2+2+1 layout, which is acceptable.

- [ ] **Step 4: Verify**

```bash
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('index.html').read()); print('parse OK')"
grep -c 'id="projPastEmissions"' index.html  # → 1
grep -c 'placeholder="auto"' index.html      # → 0
grep -c 'placeholder="e.g. 1500"' index.html # → 1
grep -c 'repeat(5, 1fr)' index.html          # → 1
grep -c 'repeat(4, 1fr)' index.html          # depends on other selectors; the projection-inputs rule should not match
```

Inspect the projection-inputs rule directly to confirm it now uses 5 columns.

- [ ] **Step 5: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Add Cardholder emissions input and widen grid to 5 columns

Adds a dedicated #projPastEmissions input as the second field in
the projection input row, between Cards Held and Card Cost. CSS
grid bumped from 4 to 5 columns. Rate input placeholder updated
from "auto" to "e.g. 1500" since auto-prefill is being removed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Update `computeProjection` to read `pastTokens` from inputs

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Replace the `pastTokens` derivation**

Find the function:

```bash
grep -n 'function computeProjection' index.html
```

The current line:

```js
            const pastTokens   = (summary.auto_airdrop_tokens || 0) + (summary.manual_airdrop_tokens || 0);
```

Replace with:

```js
            const pastTokens   = Math.max(0, Number(inputs.pastTokens) || 0);
```

Place the new declaration in the same input-clamping block, immediately after the existing `quarters` clamp, so all input clamps live together. The declaration block becomes:

```js
            const cards    = Math.max(0, Number(inputs.cards)    || 0);
            const cardCost = Math.max(0, Number(inputs.cardCost) || 0);
            const rate     = Math.max(0, Number(inputs.rate)     || 0);
            const quarters = Math.min(1000, Math.max(0, Math.floor(Number(inputs.quarters) || 0)));
            const pastTokens = Math.max(0, Number(inputs.pastTokens) || 0);
```

Then delete the OLD `pastTokens` line that derived from `summary.auto_airdrop_tokens`. The `summary` parameter is still used by `getCurrentPriceUSD(summary)` further down — leave that unchanged.

- [ ] **Step 2: Verify with assertions**

Open a Node REPL or write a tiny script that extracts both functions from `index.html` and runs:

```js
const fakeSummary = {current_token_price: 0.12, sol_price_usd: 200, display_quote: 'USDC'};
// New shape: pastTokens is a user input
const r = computeProjection({cards: 10, cardCost: 5000, rate: 1000, quarters: 4, pastTokens: 10000}, fakeSummary);
console.assert(r.pastTokens === 10000, 'pastTokens from inputs', r.pastTokens);
console.assert(r.futureTokens === 40000);
console.assert(r.totalTokens === 50000);
console.assert(Math.abs(r.totalUSD - 6000) < 0.001);

// Past tokens default to 0 when missing
const r2 = computeProjection({cards: 1, rate: 100, quarters: 1}, fakeSummary);
console.assert(r2.pastTokens === 0);

// summary's airdrop fields are no longer consulted
const r3 = computeProjection({cards: 0, pastTokens: 500}, {auto_airdrop_tokens: 99999, manual_airdrop_tokens: 99999, current_token_price: 0.12, sol_price_usd: 200, display_quote: 'USDC'});
console.assert(r3.pastTokens === 500, 'summary airdrop fields ignored', r3.pastTokens);
console.log('all asserts passed');
```

- [ ] **Step 3: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Read pastTokens from inputs in computeProjection

The function no longer derives past emissions from
summary.auto_airdrop_tokens + summary.manual_airdrop_tokens.
pastTokens is now a clamped user input alongside cards/cost/rate/
quarters. summary is still consulted for price (display_quote and
sol_price_usd via getCurrentPriceUSD).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire the new input through `recomputeProjection` and delete `maybeAutoPrefillRate`

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Add `pastTokens` to the inputs object in `recomputeProjection`**

Find:

```bash
grep -n 'function recomputeProjection' index.html
```

The current `inputs` object literal:

```js
            const inputs = {
                cards:    parseFloat(document.getElementById('projCards').value)    || 0,
                cardCost: parseFloat(document.getElementById('projCardCost').value) || 0,
                rate:     parseFloat(document.getElementById('projRate').value)     || 0,
                quarters: horizon,
            };
```

Insert a new `pastTokens` field immediately after `cards`:

```js
            const inputs = {
                cards:       parseFloat(document.getElementById('projCards').value)         || 0,
                pastTokens:  parseFloat(document.getElementById('projPastEmissions').value) || 0,
                cardCost:    parseFloat(document.getElementById('projCardCost').value)      || 0,
                rate:        parseFloat(document.getElementById('projRate').value)          || 0,
                quarters:    horizon,
            };
```

- [ ] **Step 2: Delete `maybeAutoPrefillRate` entirely**

Find and remove the function definition. Search:

```bash
grep -n 'function maybeAutoPrefillRate' index.html
```

Delete from the line `function maybeAutoPrefillRate() {` through and including its closing `}` (about 12 lines).

- [ ] **Step 3: Remove the call site inside `setupProjectionListeners`**

The current listener installer has a per-input branch:

```js
            ids.forEach(id => {
                document.getElementById(id).addEventListener('input', () => {
                    if (id === 'projCards') maybeAutoPrefillRate();
                    recomputeProjection();
                });
            });
```

Simplify to:

```js
            ids.forEach(id => {
                document.getElementById(id).addEventListener('input', () => {
                    recomputeProjection();
                });
            });
```

- [ ] **Step 4: Add the new input ID to the listener list**

In `setupProjectionListeners`, the `ids` array currently is:

```js
            const ids = ['projCards', 'projCardCost', 'projRate', 'projHorizon'];
```

Update to include the new input:

```js
            const ids = ['projCards', 'projPastEmissions', 'projCardCost', 'projRate', 'projHorizon'];
```

- [ ] **Step 5: Remove the `maybeAutoPrefillRate()` call from `renderResults`**

Find:

```bash
grep -n 'maybeAutoPrefillRate' index.html
```

The result of Step 2 should leave only the call site inside `renderResults` (since the listener call site was removed in Step 3 and the function definition was removed in Step 2). Delete the line that calls it. The end of `renderResults` should now read:

```js
            renderChart(trades, summary);
            document.getElementById('projectionSection').style.display = 'block';
            setupProjectionListeners();
            recomputeProjection();
            document.getElementById('results').style.display = 'block';
        }
```

- [ ] **Step 6: Verify**

```bash
grep -c 'maybeAutoPrefillRate' index.html       # → 0 (function gone, all calls gone)
grep -c "projPastEmissions" index.html          # → 4 or 5 (HTML id, listener array, recompute input, persistence)
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('index.html').read()); print('parse OK')"
```

Then run the app, open the page, do an analysis, and confirm:
- Typing in any of the 5 inputs triggers a recompute (tiles update live)
- Changing Cards Held does NOT silently overwrite the Rate field
- Past emissions input value is reflected in "Emissions so far" tile

- [ ] **Step 7: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Wire projPastEmissions through recomputeProjection; delete auto-prefill

recomputeProjection now reads the new projPastEmissions field and
passes it as inputs.pastTokens. maybeAutoPrefillRate is deleted
entirely; the rate field is pure manual input. setupProjectionListeners
includes the new input ID and no longer per-branches on projCards.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Persist `projPastEmissions` and bump STORAGE_KEY

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Bump STORAGE_KEY**

Find:

```bash
grep -n "STORAGE_KEY = 'solTracker" index.html
```

Change `'solTracker.v3.15'` to `'solTracker.v3.16'`.

- [ ] **Step 2: Add v3.15 to the loadPrefs fallback chain**

Find the chain:

```bash
grep -n "localStorage.getItem('solTracker.v3.14')" index.html
```

Insert a new fallback line for v3.15 above v3.14:

```js
            const raw = localStorage.getItem(STORAGE_KEY)
                || localStorage.getItem('solTracker.v3.15')
                || localStorage.getItem('solTracker.v3.14')
                || localStorage.getItem('solTracker.v3.13')
                || localStorage.getItem('solTracker.v3.11')
                || localStorage.getItem('solTracker.v3.10');
```

- [ ] **Step 3: Restore `projPastEmissions` in loadPrefs**

Find the existing `projCards` restore line (search `p.projCards !== undefined`). Add immediately after it:

```js
                if (p.projPastEmissions !== undefined) document.getElementById('projPastEmissions').value = p.projPastEmissions;
```

- [ ] **Step 4: Save `projPastEmissions` in savePrefs**

Find the existing `projCards` save line. Add immediately after it:

```js
                    projPastEmissions: document.getElementById('projPastEmissions').value,
```

- [ ] **Step 5: Verify**

```bash
grep -c "STORAGE_KEY = 'solTracker.v3.16'" index.html  # → 1
grep -c "STORAGE_KEY = 'solTracker.v3.15'" index.html  # → 0
grep -c "'solTracker.v3.15'" index.html                # → 1 (in fallback chain)
grep -c 'p.projPastEmissions !== undefined' index.html # → 1
grep -c 'projPastEmissions: document.getElementById' index.html  # → 1
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('index.html').read()); print('parse OK')"
```

- [ ] **Step 6: Commit**

```bash
cd solana-tracker
git add index.html
git commit -m "$(cat <<'EOF'
Persist projPastEmissions; bump STORAGE_KEY to v3.16

New past-emissions input is now persisted in localStorage. v3.15
added to the fallback chain so prior projection prefs migrate
forward.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Manual end-to-end verification

**Files:**
- None (verification only)

- [ ] **Step 1: Restart the Flask server (if not already running)**

```bash
cd solana-tracker
./venv/bin/python app.py
```

Or if a port-5555 instance is already running from earlier in the session, it has the new code reloaded automatically (Flask debug mode reloads on file changes).

- [ ] **Step 2: Walk through the cases from the spec**

Run an analysis on a real wallet. Then verify:

- **A** — Cards = 10, Past Emissions = 5000, Cost = 4000, Rate = 1500, Horizon = 4. Tiles show: Emissions so far = 5000, Projected = 60000, Total = 65000, Effective cost / token = $4000/65000 ≈ $0.0615.
- **B** — Past Emissions = 0 or empty. Emissions so far tile reads 0; Total = future only.
- **C** — Cards Held cleared. Future and Total reset to 0; Rate field unaffected.
- **D** — Type a value in Rate, then change Cards Held to a different number. Rate field is NOT overwritten (the previous auto-prefill behavior is gone).
- **E** — Reload the page. All five projection inputs restore from localStorage.
- **F** — Visual: scroll the page after analysis. The Cardholder Projection section should sit between the Trade History chart and the All Events table.

- [ ] **Step 3: If everything passes, no commit needed (verification only)**

If a case fails, write a follow-up debugging task before considering the refinement done.

---

## Self-Review

**Spec coverage:**
- Placement → Task 1 ✓
- New `projPastEmissions` input → Task 2 ✓
- Rate placeholder updated → Task 2 ✓
- CSS grid widened to 5 columns → Task 2 ✓
- `computeProjection` reads `pastTokens` from inputs → Task 3 ✓
- `recomputeProjection` passes through new input → Task 4 ✓
- `maybeAutoPrefillRate` deleted (function + listener call + renderResults call) → Task 4 ✓
- Listener IDs array updated → Task 4 ✓
- STORAGE_KEY bump + persistence → Task 5 ✓

**Placeholder scan:** No TBD/TODO/vague language. Every step shows the actual code.

**Type/name consistency:** `projPastEmissions` is the new ID, used consistently across HTML, listener array, recompute input shape, loadPrefs/savePrefs.
