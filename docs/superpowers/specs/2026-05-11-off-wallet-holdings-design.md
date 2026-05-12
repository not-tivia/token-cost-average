# Off-Wallet Holdings — Design Spec

**Date:** 2026-05-11
**Status:** Approved

## Summary

Extend the tracker's "Holdings" concept so it counts tokens that are not in a wallet ATA but are still owned by the user: tokens locked in unfilled Jupiter Limit sell orders, and tokens held inside Meteora DLMM positions. The headline Holdings Value number becomes a total-exposure figure, and a new Position Breakdown panel shows the per-bucket split.

## Motivation

The user runs exit strategies that pull tokens out of the wallet — most recently a 65,000 CARDS transfer to a secondary wallet, which then seeded Jupiter Limit sell orders and a one-sided Meteora DLMM position. Those tokens are still the user's bag, but the current dashboard reports only on-wallet ATA balances. As soon as tokens get listed, "Holdings" understates reality, and unrealized P/L drops by the listed amount — even though no sale has happened. The dashboard should reflect the full owned position.

## Scope (in)

- Track tokens in **unfilled Jupiter Limit sell orders** (across all configured wallets).
- Track tokens in **Meteora DLMM positions** (across all configured wallets).
- Keep the existing wallet-only ATA reconciliation banner intact.
- Add a Position Breakdown UI panel and an opt-in "If all sell orders fill" stat chip.

## Scope (out)

- Jupiter DCA leftover tokens. DCA is an entry mechanism for this user; mid-DCA balances are negligible and not worth the extra path.
- Limit **buy** orders (those lock USDC, not target tokens).
- Non-Meteora LPs (no other LP programs are currently tracked).
- Real-time push updates. Off-wallet positions are refreshed on each `/api/analyze` call, same as the rest of the dashboard.

## Data sources

Each analyze produces three independent queries that are summed:

| Bucket | Source | Returned data |
|---|---|---|
| Wallet | Existing `get_token_balance_on_chain(wallet, mint)` per wallet | tokens in ATAs |
| Limit-order reserves | `GET https://lo-api.jup.ag/openOrders?wallet=<W>` (one call per wallet). Returns open orders with `makingAmount` (target remaining) and `takingAmount` (USDC expected); limit price = `takingAmount / makingAmount`. Filter to side == sell and `inputMint == target_mint`. | tokens locked + per-order limit price |
| DLMM positions | Helius RPC `getProgramAccounts` filtered to `LBUZkhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo` (METEORA_DLMM) with a memcmp on the position account's `owner` field. Decode each position's per-bin amounts and sum the side whose mint equals `target_mint`. Fallback to `https://dlmm-api.meteora.ag/position/<owner>` if the on-chain decode proves brittle during implementation. | tokens currently held across all bins, per position |

Wallet reconciliation banner stays as-is — it still compares tx-derived `computed_holdings` against the wallet ATA balance, which is a useful integrity check on the tx parser. Reserves & DLMM are authoritative on-chain reads, so they don't get a second reconcile.

### Refresh policy

Off-wallet queries are **not** cached on disk. They run live each analyze. Cost per analyze: 1 HTTP call per wallet to Jupiter, 1 RPC per wallet to Helius for the DLMM enumerate. Negligible vs. the existing per-wallet tx-history sweep.

### Failure handling

If Jupiter API or the DLMM enumerate fails for a wallet, log it, return `tokens: 0` for that bucket, and attach an `error: '<message>'` field. The UI surfaces a small inline warning ("couldn't reach Jupiter API — limit-order bucket may be incomplete") next to the affected breakdown row. We do **not** fall back to tx-history-derived approximation, because the entire point of this feature is to reflect what is actually still ours; a stale approximation would silently understate fills and drift past the value of having the feature at all.

## Backend changes (`app.py`)

### New functions

```
get_jupiter_open_limit_orders(wallets: list[str], target_mint: str) -> list[dict]
    # one HTTP call per wallet to lo-api.jup.ag
    # filters: side == 'sell', inputMint == target_mint
    # returns: [{wallet, order_pda, tokens_remaining, limit_price,
    #           taking_mint, setup_ts (if available)}]

get_dlmm_positions(wallets: list[str], target_mint: str) -> list[dict]
    # getProgramAccounts(METEORA_DLMM, filters=[memcmp(owner_offset, wallet)])
    # decode position layout, sum the target-mint side across all bins
    # returns: [{wallet, position_pubkey, pair_address, tokens}]
```

### New aggregation

Built once per analyze, attached to the summary as `position_breakdown`:

```python
position_breakdown = {
    'wallet': {
        'tokens': wallet_tokens,
        'value_usd': wallet_tokens * current_price_usd,
    },
    'limit_orders': {
        'tokens': limit_tokens,
        'value_usd': limit_tokens * current_price_usd,
        'pending_proceeds_usd': sum(o['tokens_remaining'] * o['limit_price']
                                    for o in open_sell_orders),
        'orders': open_sell_orders,
        'error': None,  # or string if Jupiter API failed
    },
    'dlmm': {
        'tokens': dlmm_tokens,
        'value_usd': dlmm_tokens * current_price_usd,
        'positions': dlmm_positions,
        'error': None,  # or string if DLMM enumerate failed
    },
    'total_tokens':    wallet_tokens + limit_tokens + dlmm_tokens,
    'total_value_usd': (wallet_tokens + limit_tokens + dlmm_tokens) * current_price_usd,
}
```

### `compute_summary` changes

- `holdings` now equals `wallet_tokens + limit_tokens + dlmm_tokens` (was: `on_chain_balance`).
- New field `pending_limit_proceeds_usd` mirrored from `position_breakdown.limit_orders.pending_proceeds_usd`.
- New field `position_breakdown` carrying the full structure above.
- Existing `on_chain_balance` field is preserved unchanged — the reconcile banner still uses it. (Renaming would ripple through too much frontend; not worth the churn.)
- Unrealized P/L formula (`(current_token_price - spread_avg) * holdings`) automatically picks up the larger `holdings`, which is the intended behavior — locked tokens are still part of the unrealized position.

## Frontend changes (`index.html`)

### Position Breakdown panel

New `<div class="section">` inserted directly after the Holdings Value summary card, styled to match the existing LP / DCA breakdown panels. Structure:

```
Position Breakdown
─────────────────────────────────────────────────
Location              Tokens      Value      % of bag
Wallet                X           $X         X%
In Limit Orders       Y           $Y         Y%   (N open orders)
In DLMM Position      Z           $Z         Z%   (N positions)
─────────────────────────────────────────────────
Total                 X+Y+Z       $...       100%
```

Each non-zero subrow has an expand toggle that reveals a sub-list:

- **Limit Orders sub-list:** one row per open order — `<setup_ts>  <tokens_remaining> @ $<limit_price>  -> $<expected_proceeds>`
- **DLMM sub-list:** one row per position — `<pair_short>  <tokens>  ($<value>)`

Rows with `error` set show a small inline warning instead of (or alongside) their numbers.

### "If all sell orders fill" stat chip

A new optional stat in the toggleable stats row, alongside `inc-holdings`, `inc-pnl`, etc.:

- Toggle id: `inc-pending`
- Label: `If all sell orders fill`
- Value: `summary.pending_limit_proceeds_usd` formatted as USD
- **Default: off** (opt-in; user enables via the checkbox row)

### Tokens Held / Holdings Value stats

No JS changes needed. These already render `summary.holdings` and `summary.current_value_usd`, which now reflect the new total via the backend change.

### Reconcile banner

Unchanged. Still compares wallet-only `computed_holdings` against wallet-only `on_chain_balance`.

## Testing notes

- Verify with the user's actual wallets: confirm the 65k CARDS that moved to `FYLm…87kj` shows up in either the limit-orders or DLMM bucket (depending on what was set up).
- Confirm wallet reconciliation banner still matches when off-wallet positions exist (the banner is wallet-only and should be unaffected).
- Confirm fallback path: kill network to lo-api.jup.ag, confirm the limit-orders row shows the error message and the wallet/DLMM rows still render.
- Sanity-check `total_tokens == wallet + limit + dlmm` and `total_value_usd == total_tokens * current_price`.

## Open implementation questions

These get resolved during the implementation plan, not now:

- Exact field names from `lo-api.jup.ag/openOrders`. May not be `makingAmount`/`takingAmount` — confirm against a live response.
- Offset of the `owner` field in the Meteora DLMM `Position` account layout. May need to consult Meteora's SDK source or do a getAccountInfo on a known position and inspect.
- Whether the Meteora DLMM API fallback endpoint is named `/position/<owner>` or something else (will verify against their docs at implementation time).
