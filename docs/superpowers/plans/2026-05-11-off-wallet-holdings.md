# Off-Wallet Holdings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Solana tracker's Holdings concept to include tokens locked in unfilled Jupiter Limit sell orders and Meteora DLMM positions, plus add a Position Breakdown UI panel.

**Architecture:** Two new backend fetchers (Jupiter Limit Orders HTTP API + Meteora DLMM positions). Each runs once per `/api/analyze`. Their token counts are summed with the existing wallet ATA balance into a new `position_breakdown` object on the summary. `holdings` switches from "wallet only" to "total across all buckets." Frontend gains a Position Breakdown panel mirroring the LP section's styling. The wallet-only reconcile banner stays unchanged as an integrity check on the tx parser.

**Tech Stack:** Python 3.10+ / Flask backend (single `app.py`), vanilla JS single-file frontend (`index.html`). No test framework in the project — verification is done via standalone diag scripts that print results (matching existing `funding_diag.py` / `keeper_diag.py` patterns) and manual UI checks. Helius RPC for on-chain reads, Jupiter `lo-api.jup.ag` for open limit orders, Meteora `dlmm-api.meteora.ag` for DLMM positions.

**Spec:** `docs/superpowers/specs/2026-05-11-off-wallet-holdings-design.md`

---

## File Structure

All changes live in two existing files plus two new verification scripts:

- **Modify:** `app.py` — new functions `get_jupiter_open_limit_orders`, `get_dlmm_positions`, `build_position_breakdown`; modify `calculate_summary` signature; modify `/api/analyze` route to wire the new pieces together.
- **Modify:** `index.html` — new CSS for `.position-section`, new `<div id="positionSection">`, new `renderPositionBreakdown` JS function, new share-modal toggle.
- **Create:** `limit_orders_diag.py` — standalone script that prints Jupiter open orders for the configured wallets.
- **Create:** `dlmm_diag.py` — standalone script that prints DLMM positions for the configured wallets.

The diag scripts serve as the test harness for the backend tasks. They are committed to the repo (matching the existing pattern).

---

## Task 1: Jupiter open limit orders fetcher

**Files:**
- Modify: `app.py` (add new function before the Routes section, ~line 895)
- Create: `limit_orders_diag.py`

- [ ] **Step 1: Confirm Jupiter Limit Orders API endpoint shape**

Use WebFetch to confirm the exact endpoint and response schema for "list open orders by wallet":

```
WebFetch: https://dev.jup.ag/docs/limit-order/get-trigger-orders
Prompt: "What is the HTTP endpoint to fetch open (untriggered) limit orders for a given wallet? List the exact URL, query parameters, and the field names in the response payload — especially anything that represents the remaining input token amount, the expected output amount, the input mint, and the output mint."
```

Write down the resolved values for this task before continuing:
- Endpoint URL: `<resolved>`
- Field for wallet param: `<resolved>` (e.g. `user`, `owner`, `maker`)
- Field for remaining input amount: `<resolved>` (e.g. `makingAmount`)
- Field for expected output amount: `<resolved>` (e.g. `takingAmount`)
- Field for input mint: `<resolved>`
- Field for output mint: `<resolved>`

If the live API uses different names than the spec assumed, use the live names in the code below — substitute throughout this task.

- [ ] **Step 2: Add `get_jupiter_open_limit_orders` to `app.py`**

Insert this function in `app.py` immediately before the `# Routes` comment block (currently around line 1206). Substitute resolved field names from Step 1 if they differ:

```python
def get_jupiter_open_limit_orders(wallets, target_mint, target_decimals):
    """Fetch open Jupiter Limit sell orders across the given wallets.

    Returns a list of dicts. Only sell orders (target_mint as input) are kept.
    Each dict:
      { 'wallet': str, 'order_pda': str,
        'tokens_remaining': float,
        'expected_proceeds_usdc': float,
        'limit_price': float,            # USDC per target token
        'setup_ts': int or None }

    Failure mode: returns ([], error_str) if any wallet's fetch fails outright.
    Partial successes (some wallets ok, others failed) return (orders, error_str).
    """
    orders = []
    errors = []
    for wallet in wallets:
        try:
            _rate_limit(0.2)
            url = f'https://api.jup.ag/limit/v2/openOrders'
            resp = requests.get(url, params={'wallet': wallet}, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
            raw_orders = payload if isinstance(payload, list) else payload.get('orders', [])
            for o in raw_orders:
                input_mint  = o.get('inputMint')  or o.get('input_mint')  or ''
                output_mint = o.get('outputMint') or o.get('output_mint') or ''
                if input_mint != target_mint:
                    continue  # not a sell of our target
                making_raw = float(o.get('makingAmount') or o.get('making_amount') or 0)
                taking_raw = float(o.get('takingAmount') or o.get('taking_amount') or 0)
                # Jupiter returns raw integer amounts in smallest units
                tokens_remaining = making_raw / (10 ** target_decimals)
                # Output is USDC (6 decimals) or USDT (6 decimals)
                quote_decimals = 6
                expected_proceeds = taking_raw / (10 ** quote_decimals)
                limit_price = (expected_proceeds / tokens_remaining) if tokens_remaining > 0 else 0
                orders.append({
                    'wallet': wallet,
                    'order_pda': o.get('orderKey') or o.get('publicKey') or o.get('order') or '',
                    'tokens_remaining': tokens_remaining,
                    'expected_proceeds_usdc': expected_proceeds,
                    'limit_price': limit_price,
                    'setup_ts': o.get('createdAt') or o.get('created_at') or None,
                })
        except Exception as e:
            errors.append(f'{wallet[:6]}...: {e}')
            print(f'[limit-api] error for {wallet}: {e}')
    err_str = '; '.join(errors) if errors else None
    print(f'[limit-api] {len(orders)} open sell orders across {len(wallets)} wallets'
          + (f' (errors: {err_str})' if err_str else ''))
    return orders, err_str
```

- [ ] **Step 3: Create `limit_orders_diag.py` verification script**

Create at `~/solana-tracker/limit_orders_diag.py`:

```python
"""Diag: print open Jupiter Limit sell orders for given wallets.

Usage:
    python limit_orders_diag.py <wallet1> [<wallet2> ...] <target_mint>
"""
import sys
from app import get_jupiter_open_limit_orders, get_token_decimals

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    *wallets, mint = sys.argv[1:]
    decimals = get_token_decimals(mint)
    print(f'Target mint: {mint}  (decimals: {decimals})')
    print(f'Wallets: {wallets}\n')
    orders, err = get_jupiter_open_limit_orders(wallets, mint, decimals)
    if err: print(f'WARN: {err}\n')
    if not orders:
        print('No open sell orders found.'); return
    total_tokens = 0
    total_proceeds = 0
    for o in sorted(orders, key=lambda x: x['limit_price']):
        total_tokens   += o['tokens_remaining']
        total_proceeds += o['expected_proceeds_usdc']
        print(f"  {o['wallet'][:6]}...{o['wallet'][-4:]}  "
              f"{o['tokens_remaining']:>12,.2f} tok  "
              f"@ ${o['limit_price']:>10.6f}  "
              f"-> ${o['expected_proceeds_usdc']:>10,.2f}")
    print(f"\nTotal: {total_tokens:,.2f} tokens, ${total_proceeds:,.2f} expected proceeds")

if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run diag against the user's wallets**

CARDS mint is `CARDsr1tQjVLeQ7Vs7P5W3ucsTHHbWiAhBvqpDLZeT4M` (verify in `index.html` defaults / user input). Run:

```bash
cd ~/solana-tracker
./venv/bin/python limit_orders_diag.py \
    HZYWwCsvH6MPfEENXxdX8gtmk9zBDHrtwCYup9HPmATs \
    FYLm2KfPKVtnNDShZ9UE6JTJwUatFLzu8kdxLfvL87kj \
    CARDsr1tQjVLeQ7Vs7P5W3ucsTHHbWiAhBvqpDLZeT4M
```

Expected: non-zero output for `FYLm…87kj` (the exit wallet) showing one or more open sell orders. If output is empty for both wallets, either (a) the user hasn't placed orders yet, (b) the API endpoint or field names are wrong — re-check Step 1 with WebFetch.

- [ ] **Step 5: Commit**

```bash
cd ~/solana-tracker
git add app.py limit_orders_diag.py
git commit -m "Add Jupiter open limit orders fetcher + diag script"
```

---

## Task 2: Meteora DLMM positions fetcher

**Files:**
- Modify: `app.py` (add new function right after `get_jupiter_open_limit_orders`)
- Create: `dlmm_diag.py`

- [ ] **Step 1: Confirm Meteora DLMM positions-by-owner API endpoint**

```
WebFetch: https://docs.meteora.ag/dlmm/api
Prompt: "What HTTP endpoint returns all DLMM positions owned by a given wallet address? Provide the exact URL, query parameters, and response schema — especially any field that gives the current token amount for token X and token Y, the pair address, and the position address. Also note token X / token Y mint addresses or which side is which."
```

Resolve:
- Endpoint URL: `<resolved>` (likely `https://dlmm-api.meteora.ag/position/{owner}` or `/position/owner/{owner}`)
- Field for token X amount: `<resolved>`
- Field for token Y amount: `<resolved>`
- Field for token X mint: `<resolved>`
- Field for token Y mint: `<resolved>`
- Field for pair address: `<resolved>`
- Field for position pubkey: `<resolved>`

- [ ] **Step 2: Add `get_dlmm_positions` to `app.py`**

Insert directly after `get_jupiter_open_limit_orders`:

```python
def get_dlmm_positions(wallets, target_mint):
    """Fetch Meteora DLMM positions across the given wallets.

    Sums the target_mint side of each position across all bins.

    Returns: (positions, error_str)
      positions: list of dicts. Each:
        { 'wallet': str, 'position_pubkey': str, 'pair_address': str,
          'tokens': float }   # target-mint amount currently in this position
    """
    positions = []
    errors = []
    for wallet in wallets:
        try:
            _rate_limit(0.2)
            url = f'https://dlmm-api.meteora.ag/position/{wallet}'
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
            raw_positions = payload if isinstance(payload, list) else payload.get('positions', [])
            for p in raw_positions:
                mint_x = p.get('mint_x') or p.get('mintX') or p.get('token_x_mint') or ''
                mint_y = p.get('mint_y') or p.get('mintY') or p.get('token_y_mint') or ''
                amount_x = float(p.get('amount_x') or p.get('amountX') or p.get('token_x_amount') or 0)
                amount_y = float(p.get('amount_y') or p.get('amountY') or p.get('token_y_amount') or 0)
                if mint_x == target_mint:
                    tokens = amount_x
                elif mint_y == target_mint:
                    tokens = amount_y
                else:
                    continue
                # Amounts may be raw units or human units depending on API — confirm in Step 1.
                # If raw, divide by 10**target_decimals (caller will pass decimals).
                positions.append({
                    'wallet': wallet,
                    'position_pubkey': p.get('address') or p.get('position') or p.get('publicKey') or '',
                    'pair_address':   p.get('pair') or p.get('pair_address') or p.get('lbPair') or '',
                    'tokens': tokens,
                })
        except Exception as e:
            errors.append(f'{wallet[:6]}...: {e}')
            print(f'[dlmm-api] error for {wallet}: {e}')
    err_str = '; '.join(errors) if errors else None
    print(f'[dlmm-api] {len(positions)} DLMM positions across {len(wallets)} wallets'
          + (f' (errors: {err_str})' if err_str else ''))
    return positions, err_str
```

**Note on amount units:** If Step 1 reveals the API returns raw integer amounts (smallest units), divide by `10 ** target_decimals` before storing. Add a `target_decimals` parameter to the function signature and update the call site in Task 3 accordingly. If amounts are already human-readable floats, the code above is correct as-is.

- [ ] **Step 3: Create `dlmm_diag.py`**

```python
"""Diag: print Meteora DLMM positions holding target_mint, for given wallets.

Usage:
    python dlmm_diag.py <wallet1> [<wallet2> ...] <target_mint>
"""
import sys
from app import get_dlmm_positions

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    *wallets, mint = sys.argv[1:]
    print(f'Target mint: {mint}')
    print(f'Wallets: {wallets}\n')
    positions, err = get_dlmm_positions(wallets, mint)
    if err: print(f'WARN: {err}\n')
    if not positions:
        print('No DLMM positions found holding the target mint.'); return
    total = 0
    for p in positions:
        total += p['tokens']
        print(f"  {p['wallet'][:6]}...{p['wallet'][-4:]}  "
              f"{p['tokens']:>14,.4f} tok  "
              f"pair={p['pair_address'][:8]}...  "
              f"pos={p['position_pubkey'][:8]}...")
    print(f"\nTotal: {total:,.4f} tokens in DLMM positions")

if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run diag and verify**

```bash
cd ~/solana-tracker
./venv/bin/python dlmm_diag.py \
    HZYWwCsvH6MPfEENXxdX8gtmk9zBDHrtwCYup9HPmATs \
    FYLm2KfPKVtnNDShZ9UE6JTJwUatFLzu8kdxLfvL87kj \
    CARDsr1tQjVLeQ7Vs7P5W3ucsTHHbWiAhBvqpDLZeT4M
```

Expected: the one-sided DLMM position the user set up on `FYLm…87kj` should show. Sanity-check: tokens shown should be roughly within the 65,000 CARDS bucket (minus whatever went to limit orders).

If the API doesn't return positions but you know one exists, recheck Step 1 — the endpoint URL may differ, or the API may need a different request shape (POST vs GET, alternate path).

- [ ] **Step 5: Commit**

```bash
cd ~/solana-tracker
git add app.py dlmm_diag.py
git commit -m "Add Meteora DLMM positions fetcher + diag script"
```

---

## Task 3: Build position breakdown, wire into `/api/analyze` and `calculate_summary`

**Files:**
- Modify: `app.py` lines ~1061 (`calculate_summary` signature), ~1111 (`holdings` formula), ~1192 (return dict), ~1285–1322 (`/api/analyze`)

- [ ] **Step 1: Add `build_position_breakdown` helper to `app.py`**

Insert immediately before `calculate_summary` (~line 1061):

```python
def build_position_breakdown(wallet_tokens, limit_orders, limit_err,
                             dlmm_positions, dlmm_err, current_price_usd):
    """Build the position_breakdown object for the summary.

    All token counts are in human units. Value is computed at current market price.
    Mutates each order/position to attach a `value_usd` field for frontend convenience.
    """
    for o in limit_orders:
        o['value_usd'] = o['tokens_remaining'] * current_price_usd
    for p in dlmm_positions:
        p['value_usd'] = p['tokens'] * current_price_usd
    limit_tokens   = sum(o['tokens_remaining'] for o in limit_orders)
    dlmm_tokens    = sum(p['tokens'] for p in dlmm_positions)
    total_tokens   = wallet_tokens + limit_tokens + dlmm_tokens
    pending_proceeds = sum(o['expected_proceeds_usdc'] for o in limit_orders)
    return {
        'wallet': {
            'tokens': wallet_tokens,
            'value_usd': wallet_tokens * current_price_usd,
        },
        'limit_orders': {
            'tokens': limit_tokens,
            'value_usd': limit_tokens * current_price_usd,
            'pending_proceeds_usd': pending_proceeds,
            'orders': limit_orders,
            'error': limit_err,
        },
        'dlmm': {
            'tokens': dlmm_tokens,
            'value_usd': dlmm_tokens * current_price_usd,
            'positions': dlmm_positions,
            'error': dlmm_err,
        },
        'total_tokens':    total_tokens,
        'total_value_usd': total_tokens * current_price_usd,
    }
```

- [ ] **Step 2: Modify `calculate_summary` signature and `holdings` line**

In `app.py`:

Change the signature at line 1061 from:

```python
def calculate_summary(trades, dca_aggregate, on_chain_balance,
                      current_price_usd, sol_price_usd,
                      auto_funding_usd, display_quote='USDC',
                      manual_dca_cost=0.0, manual_airdrop_tokens=0.0):
```

to:

```python
def calculate_summary(trades, dca_aggregate, on_chain_balance,
                      current_price_usd, sol_price_usd,
                      auto_funding_usd, display_quote='USDC',
                      manual_dca_cost=0.0, manual_airdrop_tokens=0.0,
                      position_breakdown=None):
```

Then change the `holdings` line at line 1120 from:

```python
    holdings = on_chain_balance if on_chain_balance is not None else computed_holdings
```

to:

```python
    # Wallet-only base used for reconciliation banner
    wallet_only_holdings = on_chain_balance if on_chain_balance is not None else computed_holdings
    # Total holdings includes off-wallet positions (limit orders, DLMM)
    if position_breakdown is not None:
        holdings = position_breakdown['total_tokens']
    else:
        holdings = wallet_only_holdings
```

Then in the return dict (~line 1191), add `position_breakdown` and `pending_limit_proceeds_usd`. Find this section:

```python
        'computed_holdings': computed_holdings, 'on_chain_balance': on_chain_balance,
        'reconciliation_diff': diff, 'reconciled': reconciled, 'holdings': holdings,
```

Replace with:

```python
        'computed_holdings': computed_holdings, 'on_chain_balance': on_chain_balance,
        'reconciliation_diff': diff, 'reconciled': reconciled, 'holdings': holdings,
        'wallet_only_holdings': wallet_only_holdings,
        'position_breakdown': position_breakdown,
        'pending_limit_proceeds_usd': (
            position_breakdown['limit_orders']['pending_proceeds_usd']
            if position_breakdown else 0
        ),
```

- [ ] **Step 3: Wire the new fetchers into `/api/analyze`**

In `app.py`, after the existing on-chain wallet balance loop (~line 1289) and before `analyze_limit_orders` (~line 1292), insert:

```python
        # NEW: fetch open Jupiter Limit sell orders (off-wallet bucket)
        open_limit_orders, open_limit_err = get_jupiter_open_limit_orders(
            wallets, target_mint, target_decimals
        )
        # NEW: fetch Meteora DLMM positions (off-wallet bucket)
        dlmm_positions, dlmm_err = get_dlmm_positions(wallets, target_mint)

        # NEW: build the position breakdown
        wallet_tokens_for_breakdown = on_chain if on_chain is not None else 0.0
        position_breakdown = build_position_breakdown(
            wallet_tokens_for_breakdown,
            open_limit_orders, open_limit_err,
            dlmm_positions, dlmm_err,
            token_price_usd,
        )
```

Then change the `calculate_summary` call (~line 1297) from:

```python
        summary = calculate_summary(
            trades, dca_aggregate, on_chain,
            token_price_usd, sol_price_usd,
            auto_funding_usd, display_quote,
            manual_dca_cost, manual_airdrop_tokens,
        )
```

to:

```python
        summary = calculate_summary(
            trades, dca_aggregate, on_chain,
            token_price_usd, sol_price_usd,
            auto_funding_usd, display_quote,
            manual_dca_cost, manual_airdrop_tokens,
            position_breakdown=position_breakdown,
        )
```

- [ ] **Step 4: Restart the dev server**

```bash
# Kill any existing instance, then start fresh
pkill -f "python app.py" 2>/dev/null; true
cd ~/solana-tracker && nohup ./venv/bin/python app.py > /tmp/solana-tracker.log 2>&1 &
sleep 4
tail -20 /tmp/solana-tracker.log
```

Expected: `Solana Token Tracker (v3.12) — http://localhost:5000` with no startup errors.

- [ ] **Step 5: Run an analyze via curl and inspect the new fields**

```bash
curl -s -X POST http://localhost:5000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "wallets": "HZYWwCsvH6MPfEENXxdX8gtmk9zBDHrtwCYup9HPmATs\nFYLm2KfPKVtnNDShZ9UE6JTJwUatFLzu8kdxLfvL87kj",
    "token_address": "CARDsr1tQjVLeQ7Vs7P5W3ucsTHHbWiAhBvqpDLZeT4M",
    "display_quote": "USDC"
  }' | python -c "
import sys, json
d = json.load(sys.stdin)
s = d['summary']
pb = s['position_breakdown']
print('Wallet:       ', pb['wallet']['tokens'], 'tokens, $', round(pb['wallet']['value_usd'], 2))
print('Limit orders: ', pb['limit_orders']['tokens'], 'tokens, $', round(pb['limit_orders']['value_usd'], 2),
      '/ pending proceeds $', round(pb['limit_orders']['pending_proceeds_usd'], 2))
print('DLMM:         ', pb['dlmm']['tokens'], 'tokens, $', round(pb['dlmm']['value_usd'], 2))
print('Total:        ', pb['total_tokens'], 'tokens, $', round(pb['total_value_usd'], 2))
print('Reconciled (wallet):', s['reconciled'])
"
```

Expected: non-zero rows for at least one of `limit_orders` or `dlmm` (matching whatever the user actually has set up). `total_tokens == wallet + limit_orders + dlmm` exactly.

- [ ] **Step 6: Commit**

```bash
cd ~/solana-tracker
git add app.py
git commit -m "Wire off-wallet positions into summary (position_breakdown + holdings)"
```

---

## Task 4: Frontend — Position Breakdown panel (read-only)

**Files:**
- Modify: `index.html` (CSS section ~line 120, HTML body, JS render function)

- [ ] **Step 1: Add CSS for `.position-section`**

In `index.html`, find the `.lp-section { ... }` rule (~line 120) and insert directly after it:

```css
        .position-section { background: #12121a; border: 1px solid #1e1e2e; border-radius: 14px; padding: 20px; margin-bottom: 24px; }
        .position-section h2 { font-size: 15px; margin-bottom: 16px; color: #ccc; font-weight: 600; }
        .position-row { display: grid; grid-template-columns: 1.4fr 1fr 1fr 0.6fr; gap: 12px; padding: 10px 0; border-bottom: 1px solid #1a1a26; align-items: center; font-size: 13px; }
        .position-row:last-child { border-bottom: none; }
        .position-row.total { font-weight: 700; padding-top: 14px; border-top: 2px solid #1e1e2e; }
        .position-row .loc { color: #ccc; }
        .position-row .tok, .position-row .val { color: #888; font-family: 'JetBrains Mono', monospace; font-size: 12px; text-align: right; }
        .position-row .pct { color: #6c63ff; font-weight: 600; text-align: right; }
        .position-row .badge { display: inline-block; margin-left: 8px; padding: 2px 6px; background: #1a1a26; color: #888; border-radius: 4px; font-size: 11px; font-weight: 500; }
        .position-row .err { color: #ff7676; font-size: 11px; margin-top: 4px; }
        .position-pending { margin-top: 16px; padding: 12px; background: #0a0a0f; border: 1px solid #1a1a26; border-radius: 8px; display: flex; justify-content: space-between; align-items: baseline; }
        .position-pending .k { color: #888; font-size: 12px; }
        .position-pending .v { color: #4caf50; font-weight: 700; font-family: 'JetBrains Mono', monospace; font-size: 14px; }
```

- [ ] **Step 2: Add the `<div>` placeholder in the HTML body**

In `index.html`, find the line `<div id="lpSection" class="lp-section" style="display:none;"></div>` (~line 328) and insert directly before it:

```html
            <div id="positionSection" class="position-section" style="display:none;"></div>
```

- [ ] **Step 3: Write `renderPositionBreakdown` JS function**

In `index.html`, find the function `renderLpBreakdown` (~line 791) and insert directly before it:

```javascript
        function renderPositionBreakdown(summary) {
            const pb = summary && summary.position_breakdown;
            const el = document.getElementById('positionSection');
            if (!pb || pb.total_tokens <= 0) { el.style.display = 'none'; return; }
            el.style.display = 'block';
            const total = pb.total_tokens || 0;
            const pct = (t) => total > 0 ? ((t / total) * 100).toFixed(1) : '0.0';
            const rows = [
                {
                    key: 'wallet', label: 'Wallet',
                    tokens: pb.wallet.tokens, value: pb.wallet.value_usd,
                    badge: null, error: null,
                },
                {
                    key: 'limit_orders', label: 'In Limit Orders',
                    tokens: pb.limit_orders.tokens, value: pb.limit_orders.value_usd,
                    badge: (pb.limit_orders.orders || []).length
                        ? `${pb.limit_orders.orders.length} open` : null,
                    error: pb.limit_orders.error,
                },
                {
                    key: 'dlmm', label: 'In DLMM Position',
                    tokens: pb.dlmm.tokens, value: pb.dlmm.value_usd,
                    badge: (pb.dlmm.positions || []).length
                        ? `${pb.dlmm.positions.length} position${pb.dlmm.positions.length > 1 ? 's' : ''}` : null,
                    error: pb.dlmm.error,
                },
            ];
            const rowHtml = (r) => `
                <div class="position-row">
                    <div class="loc">${r.label}${r.badge ? `<span class="badge">${r.badge}</span>` : ''}
                        ${r.error ? `<div class="err">⚠ ${r.error}</div>` : ''}
                    </div>
                    <div class="tok">${fmt(r.tokens, 2)} tok</div>
                    <div class="val">${fmtUsd(r.value)}</div>
                    <div class="pct">${pct(r.tokens)}%</div>
                </div>`;
            const pending = pb.limit_orders.pending_proceeds_usd || 0;
            el.innerHTML = `
                <h2>Position Breakdown</h2>
                ${rows.map(rowHtml).join('')}
                <div class="position-row total">
                    <div class="loc">Total</div>
                    <div class="tok">${fmt(pb.total_tokens, 2)} tok</div>
                    <div class="val">${fmtUsd(pb.total_value_usd)}</div>
                    <div class="pct">100%</div>
                </div>
                ${pending > 0 ? `
                    <div class="position-pending">
                        <div class="k">If all open limit orders fill at their limit price:</div>
                        <div class="v">${fmtUsd(pending)}</div>
                    </div>
                ` : ''}
            `;
        }
```

- [ ] **Step 4: Call `renderPositionBreakdown` in the main render flow**

Find the line `renderLpBreakdown(lp_breakdown, summary);` (~line 910) and insert directly before it:

```javascript
            renderPositionBreakdown(summary);
```

- [ ] **Step 5: Hard-reload the page and verify**

The dev server is still running from Task 3. Open http://localhost:5000 in a browser, paste the user's two wallets and the CARDS mint, click Scan, and confirm:

- The new "Position Breakdown" panel appears above the LP section.
- Three rows: Wallet / In Limit Orders / In DLMM Position, each with tokens, value, and % of bag.
- Total row at the bottom sums correctly.
- "If all open limit orders fill..." line appears below the total when there are open orders.
- The headline `Holdings Value` card (in the PnL hero) now shows the larger total.
- The reconcile banner still appears and still references wallet-only numbers.

- [ ] **Step 6: Commit**

```bash
cd ~/solana-tracker
git add index.html
git commit -m "Add Position Breakdown panel to dashboard"
```

---

## Task 5: Per-bucket expansion sub-lists

**Files:**
- Modify: `index.html` (CSS, `renderPositionBreakdown` function)

- [ ] **Step 1: Add CSS for expansion sub-lists**

In `index.html`, immediately after the `.position-pending .v {...}` rule added in Task 4, insert:

```css
        .position-row { cursor: default; }
        .position-row.expandable { cursor: pointer; }
        .position-row.expandable:hover { background: #161622; }
        .position-row .caret { display: inline-block; margin-right: 6px; color: #555; transition: transform 0.15s; font-size: 10px; }
        .position-row.open .caret { transform: rotate(90deg); color: #6c63ff; }
        .position-sublist { padding: 8px 0 12px 24px; background: #0a0a0f; border-bottom: 1px solid #1a1a26; font-size: 12px; font-family: 'JetBrains Mono', monospace; color: #aaa; display: none; }
        .position-sublist.open { display: block; }
        .position-sublist-row { padding: 4px 0; display: flex; justify-content: space-between; gap: 12px; }
        .position-sublist-row .left { color: #888; }
        .position-sublist-row .right { color: #ccc; }
```

- [ ] **Step 2: Replace `renderPositionBreakdown` with the expanded version**

In `index.html`, replace the entire `renderPositionBreakdown` function from Task 4 with:

```javascript
        function renderPositionBreakdown(summary) {
            const pb = summary && summary.position_breakdown;
            const el = document.getElementById('positionSection');
            if (!pb || pb.total_tokens <= 0) { el.style.display = 'none'; return; }
            el.style.display = 'block';
            const total = pb.total_tokens || 0;
            const pct = (t) => total > 0 ? ((t / total) * 100).toFixed(1) : '0.0';

            const limitSublist = (pb.limit_orders.orders || []).map(o => {
                const ts = o.setup_ts ? new Date(o.setup_ts * 1000).toLocaleString() : '';
                return `<div class="position-sublist-row">
                    <div class="left">${ts || o.order_pda.slice(0, 8) + '...'}  ·  ${fmt(o.tokens_remaining, 2)} tok @ $${fmt(o.limit_price, 6)}</div>
                    <div class="right">-> ${fmtUsd(o.expected_proceeds_usdc)}</div>
                </div>`;
            }).join('');

            const dlmmSublist = (pb.dlmm.positions || []).map(p => {
                const pairShort = p.pair_address ? p.pair_address.slice(0, 8) + '...' : '?';
                return `<div class="position-sublist-row">
                    <div class="left">pair ${pairShort}  ·  ${fmt(p.tokens, 4)} tok</div>
                    <div class="right">${fmtUsd(p.value_usd)}</div>
                </div>`;
            }).join('');

            const rows = [
                { key: 'wallet', label: 'Wallet',
                  tokens: pb.wallet.tokens, value: pb.wallet.value_usd,
                  badge: null, error: null, sublist: '' },
                { key: 'limit_orders', label: 'In Limit Orders',
                  tokens: pb.limit_orders.tokens, value: pb.limit_orders.value_usd,
                  badge: (pb.limit_orders.orders || []).length
                      ? `${pb.limit_orders.orders.length} open` : null,
                  error: pb.limit_orders.error, sublist: limitSublist },
                { key: 'dlmm', label: 'In DLMM Position',
                  tokens: pb.dlmm.tokens, value: pb.dlmm.value_usd,
                  badge: (pb.dlmm.positions || []).length
                      ? `${pb.dlmm.positions.length} position${pb.dlmm.positions.length > 1 ? 's' : ''}` : null,
                  error: pb.dlmm.error, sublist: dlmmSublist },
            ];

            const rowHtml = (r) => {
                const expandable = r.sublist && r.sublist.length > 0;
                return `
                <div class="position-row ${expandable ? 'expandable' : ''}" data-key="${r.key}"
                     ${expandable ? `onclick="togglePositionRow('${r.key}')"` : ''}>
                    <div class="loc">
                        ${expandable ? '<span class="caret">▶</span>' : ''}${r.label}${r.badge ? `<span class="badge">${r.badge}</span>` : ''}
                        ${r.error ? `<div class="err">⚠ ${r.error}</div>` : ''}
                    </div>
                    <div class="tok">${fmt(r.tokens, 2)} tok</div>
                    <div class="val">${fmtUsd(r.value)}</div>
                    <div class="pct">${pct(r.tokens)}%</div>
                </div>
                ${expandable ? `<div class="position-sublist" data-sublist="${r.key}">${r.sublist}</div>` : ''}`;
            };

            const pending = pb.limit_orders.pending_proceeds_usd || 0;
            el.innerHTML = `
                <h2>Position Breakdown</h2>
                ${rows.map(rowHtml).join('')}
                <div class="position-row total">
                    <div class="loc">Total</div>
                    <div class="tok">${fmt(pb.total_tokens, 2)} tok</div>
                    <div class="val">${fmtUsd(pb.total_value_usd)}</div>
                    <div class="pct">100%</div>
                </div>
                ${pending > 0 ? `
                    <div class="position-pending">
                        <div class="k">If all open limit orders fill at their limit price:</div>
                        <div class="v">${fmtUsd(pending)}</div>
                    </div>
                ` : ''}
            `;
        }

        function togglePositionRow(key) {
            const row = document.querySelector(`.position-row[data-key="${key}"]`);
            const sub = document.querySelector(`.position-sublist[data-sublist="${key}"]`);
            if (!row || !sub) return;
            row.classList.toggle('open');
            sub.classList.toggle('open');
        }
```

- [ ] **Step 3: Hard-reload and click each row to verify**

In the browser, reload http://localhost:5000 and re-run an analyze. Verify:

- The Limit Orders row has a caret. Click it: a sub-list appears listing each open order (timestamp, tokens remaining, limit price, expected proceeds).
- The DLMM row has a caret. Click it: a sub-list appears listing each position (pair short, tokens).
- The Wallet row has no caret (nothing to expand).
- Clicking again collapses the sub-list.

- [ ] **Step 4: Commit**

```bash
cd ~/solana-tracker
git add index.html
git commit -m "Add expandable sub-lists to Position Breakdown rows"
```

---

## Task 6: Share-modal toggle for "If all sell orders fill"

**Files:**
- Modify: `index.html` (share-modal markup, `openShareModal` event hookup, `renderSharePreview`)

- [ ] **Step 1: Add the share-modal checkbox**

In `index.html`, find the line `<label class="check"><input type="checkbox" id="inc-breakeven"> Break-even Price</label>` (~line 492) and insert directly after it:

```html
                <label class="check"><input type="checkbox" id="inc-pending"> If All Sell Orders Fill</label>
```

- [ ] **Step 2: Add `inc-pending` to `openShareModal` event hookup**

Find this line (~line 1403):

```javascript
            ['inc-pnl','inc-invested','inc-realized','inc-holdings','inc-avgbuy','inc-avgsell','inc-current','inc-tokens','inc-breakeven']
```

Replace with:

```javascript
            ['inc-pnl','inc-invested','inc-realized','inc-holdings','inc-avgbuy','inc-avgsell','inc-current','inc-tokens','inc-breakeven','inc-pending']
```

- [ ] **Step 3: Add the stat to `renderSharePreview`**

Find the block of `stats.push(...)` calls (~lines 1417–1424). Immediately after the `inc-breakeven` line:

```javascript
            if (inc('inc-breakeven') && s.break_even_price > 0) stats.push({ label: 'Break-even', value: fmt(s.break_even_price, 6) + ' ' + q });
```

Insert:

```javascript
            if (inc('inc-pending') && s.pending_limit_proceeds_usd > 0) stats.push({ label: 'If All Sell Orders Fill', value: fmtUsd(s.pending_limit_proceeds_usd) });
```

- [ ] **Step 4: Verify in the share modal**

In the browser, open the share modal (whichever button currently triggers it — search the HTML for `openShareModal()`), enable "If All Sell Orders Fill", and confirm the preview now shows the stat with the correct USD value (matching `summary.pending_limit_proceeds_usd`).

- [ ] **Step 5: Commit**

```bash
cd ~/solana-tracker
git add index.html
git commit -m "Add 'If all sell orders fill' opt-in stat to share card"
```

---

## Task 7: End-to-end verification + failure-mode check

- [ ] **Step 1: Golden-path verification with the user's real wallets**

The dev server is still running. In the browser at http://localhost:5000:

1. Paste both wallets:
   ```
   HZYWwCsvH6MPfEENXxdX8gtmk9zBDHrtwCYup9HPmATs
   FYLm2KfPKVtnNDShZ9UE6JTJwUatFLzu8kdxLfvL87kj
   ```
2. Paste mint: `CARDsr1tQjVLeQ7Vs7P5W3ucsTHHbWiAhBvqpDLZeT4M`
3. Click Scan.

Confirm all of the following:

- Headline `Holdings Value` reflects total (wallet + limit + DLMM), not just wallet.
- The reconcile banner is wallet-only and still says reconciled/unreconciled correctly (a discrepancy between `computed_holdings` and `on_chain_balance` would indicate a tx-parsing issue, not a feature bug).
- Position Breakdown panel shows three rows summing to the total.
- Open the Limit Orders sub-list — entries match what `limit_orders_diag.py` printed in Task 1.
- Open the DLMM sub-list — entries match what `dlmm_diag.py` printed in Task 2.
- Unrealized P/L scales with the new (larger) `holdings` — this is intended (locked tokens are still part of the unrealized position).

- [ ] **Step 2: Failure-mode check — Jupiter API unreachable**

Temporarily block Jupiter API access to verify the error path renders cleanly:

```bash
# Add a temporary fake DNS override (requires sudo)
echo '0.0.0.0 api.jup.ag' | sudo tee -a /etc/hosts
```

In the browser, click Scan again. Confirm:

- The Position Breakdown panel still renders.
- The Limit Orders row shows the ⚠ error message ("couldn't reach Jupiter API — ...") inline.
- Wallet and DLMM rows still show their numbers correctly.
- The headline Holdings Value still includes wallet + DLMM (limit_orders contribution = 0).

Then revert:

```bash
sudo sed -i '/api.jup.ag/d' /etc/hosts
```

- [ ] **Step 3: Failure-mode check — DLMM API unreachable**

```bash
echo '0.0.0.0 dlmm-api.meteora.ag' | sudo tee -a /etc/hosts
```

Re-run an analyze in the browser. Confirm the DLMM row shows the ⚠ error and the rest still works. Then:

```bash
sudo sed -i '/dlmm-api.meteora.ag/d' /etc/hosts
```

- [ ] **Step 4: Self-check the math one final time**

In the browser console after a successful scan:

```javascript
const s = lastData.summary;
const pb = s.position_breakdown;
console.log('Sum check:',
    pb.wallet.tokens + pb.limit_orders.tokens + pb.dlmm.tokens,
    '===',
    pb.total_tokens);
console.log('Value check:',
    pb.total_tokens * s.current_token_price,
    '===',
    pb.total_value_usd);
console.log('Holdings field uses total:',
    s.holdings === pb.total_tokens);
```

Expected: all three logs report equality (within floating-point noise — differences below 1e-6 are fine).

- [ ] **Step 5: Tag the verified working build**

```bash
cd ~/solana-tracker
git log --oneline -7  # confirm all 6 commits present
```

No further commit — verification produces no code changes.
