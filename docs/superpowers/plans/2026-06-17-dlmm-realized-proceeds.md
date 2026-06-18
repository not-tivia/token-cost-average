# DLMM Realized Proceeds (Persistent) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persistently book the USDC/SOL a Meteora DLMM pool pays you when it sells your CARDS through an out-of-range bin range, so recovered proceeds (and therefore break-even) stay correct after you withdraw and swap that quote away.

**Architecture:** Today the only credit for pool-sold CARDS is `lp_quote_value` — a *live snapshot* of quote tokens still parked in open positions (`build_position_breakdown` → `calculate_summary`). When the user withdraws that quote, the snapshot drops to 0 and the credit evaporates, inflating break-even. We replace the ephemeral snapshot with a **per-pool conversion ledger** computed from transaction history: for each CARDS LbPair, net the user's CARDS delta and quote delta across all principal (non-fee-claim) liquidity txs; pools where net CARDS fell are sales (credit the quote as realized proceeds), pools where net CARDS rose are buys (add the quote as cost basis). Open positions are folded in by treating their current legs as a virtual withdrawal at current price, which unifies the open and closed cases and subsumes the old `lp_quote_value`.

**Tech Stack:** Python 3 / Flask, `requests` (Helius RPC), existing helpers `_compute_balance_deltas`, `_decode_lb_pair`, `_get_account_data`, `_is_dlmm_claim`, `get_token_price_usd`. Tests via plain `assert` scripts run with `./venv/bin/python` (mirrors `test_dlmm_quote.py`).

## Global Constraints

- No new third-party dependencies (`requirements.txt` stays: flask, requests, solders, python-dotenv).
- ASCII only in source; no non-ASCII characters.
- CARDS mint `CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp`, USDC `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`, USDT `Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB`, SOL mint via `SOL_MINT`, Meteora DLMM program `METEORA_DLMM` — all already defined in `app.py`.
- Fee-claim txs (`_is_dlmm_claim(tx) == True`) are EXCLUDED from conversion accounting — claimed CARDS already count as holdings, claimed USDC is intentionally ignored (prior user decision).
- A "sell pool" is one whose net CARDS delta `< -EPS`; a "buy pool" `> +EPS`. `EPS = 1.0` CARDS.
- Verified target: with the current 5-wallet cache, total realized proceeds ~= $29,277 and break-even ~= $0.173 (vs the bug's $0.232). Implementation must reproduce these within rounding.

---

### Task 1: Identify CARDS LbPairs referenced by DLMM transactions

**Files:**
- Modify: `app.py` (add helpers near `get_dlmm_positions`, ~line 1785)
- Test: `test_dlmm_proceeds.py` (create at repo root)

**Interfaces:**
- Consumes: `_get_account_data(pubkey)`, `_decode_lb_pair(raw)`, `METEORA_DLMM`, `_is_dlmm_claim(tx)`.
- Produces:
  - `_dlmm_ix_accounts(tx) -> list[str]` — account list of the tx's top-level Meteora DLMM instruction, or `[]`.
  - `identify_cards_lb_pairs(transactions, target_mint) -> set[str]` — addresses of LbPairs that involve `target_mint`, discovered by decoding candidate accounts of DLMM txs. Results cached in-process by address.

- [ ] **Step 1: Write the failing test**

```python
# test_dlmm_proceeds.py
import app

def test_dlmm_ix_accounts_extracts_meteora_accounts():
    tx = {'instructions': [
        {'programId': 'OtherProg', 'accounts': ['a', 'b']},
        {'programId': app.METEORA_DLMM, 'accounts': ['pair', 'pos', 'user']},
    ]}
    assert app._dlmm_ix_accounts(tx) == ['pair', 'pos', 'user']

def test_dlmm_ix_accounts_none_when_no_dlmm():
    assert app._dlmm_ix_accounts({'instructions': [{'programId': 'X', 'accounts': []}]}) == []

if __name__ == '__main__':
    test_dlmm_ix_accounts_extracts_meteora_accounts()
    test_dlmm_ix_accounts_none_when_no_dlmm()
    print('Task 1 unit tests passed')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/solana-tracker && ./venv/bin/python test_dlmm_proceeds.py`
Expected: `AttributeError: module 'app' has no attribute '_dlmm_ix_accounts'`

- [ ] **Step 3: Write minimal implementation**

```python
# in app.py, near get_dlmm_positions
_CARDS_LB_PAIR_CACHE = {}  # address -> bool (is a target-mint LbPair)

def _dlmm_ix_accounts(tx):
    """Account list of the tx's top-level Meteora DLMM instruction, or []."""
    for ins in tx.get('instructions', []) or []:
        if ins.get('programId') == METEORA_DLMM:
            return ins.get('accounts', []) or []
    return []

# accounts that are never an LbPair; skip to save RPC calls
_DLMM_NON_PAIR = {
    METEORA_DLMM, '11111111111111111111111111111111',
    'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
    'ComputeBudget111111111111111111111111111111',
    'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL',
}

def identify_cards_lb_pairs(transactions, target_mint):
    """Set of LbPair addresses (involving target_mint) referenced by DLMM txs.

    Decodes each candidate instruction account once via _decode_lb_pair and
    caches the verdict so repeated scans don't re-fetch. Accounts that fail to
    decode as an LbPair are cached as False.
    """
    candidates = set()
    for tx in transactions:
        if _is_dlmm_claim(tx):
            continue
        for acct in _dlmm_ix_accounts(tx):
            if acct and acct not in _DLMM_NON_PAIR and len(acct) >= 32:
                candidates.add(acct)
    pairs = set()
    for acct in candidates:
        cached = _CARDS_LB_PAIR_CACHE.get(acct)
        if cached is None:
            cached = False
            try:
                raw = _get_account_data(acct)
                info = _decode_lb_pair(raw) if raw else None
                if info and target_mint in (info.get('token_x_mint'), info.get('token_y_mint')):
                    cached = True
            except Exception:
                cached = False
            _CARDS_LB_PAIR_CACHE[acct] = cached
        if cached:
            pairs.add(acct)
    return pairs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/solana-tracker && ./venv/bin/python test_dlmm_proceeds.py`
Expected: `Task 1 unit tests passed`

- [ ] **Step 5: Integration check against live cache (manual, no commit yet)**

Run: `cd ~/solana-tracker && ./venv/bin/python -c "import app; ws=['HZYWwCsvH6MPfEENXxdX8gtmk9zBDHrtwCYup9HPmATs','FYLm2KfPKVtnNDShZ9UE6JTJwUatFLzu8kdxLfvL87kj','3xkr5zybajLPKui462B3ouP2cvB6YqSNjEmfHcGw1Y5p','BbLFyprTBGKJYBiXRxVYwvfYheZaLRGoBe5yqcuRtfDx','DFiXPhhrzMa58mS2Ax5rgX4Zuk2M7XHir58D6vkCc2u6']; txs=[]; [txs.extend(app._load_cache(w).get('txs',[])) for w in ws]; print(sorted(p[:8] for p in app.identify_cards_lb_pairs(txs,'CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp')))"`
Expected: `['ApFwoYkU', 'DL3WhGJR']`

- [ ] **Step 6: Commit**

```bash
cd ~/solana-tracker
git add app.py test_dlmm_proceeds.py
git commit -m "feat(dlmm): identify CARDS LbPairs referenced by DLMM txs"
```

---

### Task 2: Compute per-pool conversion ledger and realized proceeds

**Files:**
- Modify: `app.py` (add `compute_dlmm_conversions` after `identify_cards_lb_pairs`)
- Test: `test_dlmm_proceeds.py`

**Interfaces:**
- Consumes: `_compute_balance_deltas(tx, target_mint, wallet_set) -> (target_delta, sol_delta, quote_deltas_dict)`, `_is_dlmm_claim`, `_dlmm_ix_accounts`, `identify_cards_lb_pairs`, `USDC_MINT`, `USDT_MINT`.
- Produces:
  - `compute_dlmm_conversions(transactions, target_mint, wallets, sol_price_usd, open_positions=None, token_price_usd=0.0) -> dict` with keys:
    - `realized_proceeds_usd: float` (sum over sell pools of max(0, net_quote_usd))
    - `conversion_cost_usd: float` (sum over buy pools of max(0, -net_quote_usd))
    - `pools: list[dict]` each `{pair, net_cards, net_quote_usd, kind}` where `kind in ('sell','buy','flat')`.
  - `open_positions` is the `dlmm_positions` list from `get_dlmm_positions` (each has `pair_address`, `tokens`, `quote_tokens`, `quote_symbol`); when provided, each open position folds a virtual withdrawal `net_cards += tokens`, `net_quote_usd += _dlmm_quote_value_usd(quote_tokens, quote_symbol, sol_price) + tokens*token_price_usd*0` (CARDS leg credited to net_cards only, quote leg to net_quote_usd).

- [ ] **Step 1: Write the failing test**

```python
def _lp_tx(sig, pair, cards_delta, usdc_delta):
    # minimal Helius-shaped tx: one DLMM instruction naming the pair, plus a
    # tokenTransfer-free body; deltas are injected via monkeypatched helper.
    return {'signature': sig,
            'instructions': [{'programId': app.METEORA_DLMM, 'accounts': [pair, 'pos', 'user']}],
            '_test_cards': cards_delta, '_test_usdc': usdc_delta}

def test_conversions_sell_pool_credits_quote(monkeypatch):
    monkeypatch.setattr(app, 'identify_cards_lb_pairs', lambda txs, m: {'PAIRSELL', 'PAIRBUY'})
    monkeypatch.setattr(app, '_is_dlmm_claim', lambda tx: False)
    monkeypatch.setattr(app, '_compute_balance_deltas',
        lambda tx, m, ws: (tx['_test_cards'], 0.0, {app.USDC_MINT: tx['_test_usdc']}))
    txs = [
        _lp_tx('s1', 'PAIRSELL', -100000.0, 0.0),      # deposited CARDS
        _lp_tx('s2', 'PAIRSELL', 0.0, 20000.0),        # withdrew USDC, no CARDS
        _lp_tx('b1', 'PAIRBUY', 30000.0, -5000.0),     # bought CARDS via pool
    ]
    out = app.compute_dlmm_conversions(txs, 'MINT', ['user'], sol_price_usd=70.0)
    assert round(out['realized_proceeds_usd']) == 20000
    assert round(out['conversion_cost_usd']) == 5000
    kinds = {p['pair']: p['kind'] for p in out['pools']}
    assert kinds == {'PAIRSELL': 'sell', 'PAIRBUY': 'buy'}
```

Note: this project has no pytest harness; provide a tiny inline `monkeypatch` shim at the bottom of the test file:

```python
class _MP:
    def __init__(self): self._undo = []
    def setattr(self, obj, name, val):
        old = getattr(obj, name); self._undo.append((obj, name, old)); setattr(obj, name, val)
    def undo(self):
        for obj, name, old in reversed(self._undo): setattr(obj, name, old)
```

and in `__main__` run each `test_*` with `mp=_MP(); try: test(mp) finally: mp.undo()`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/solana-tracker && ./venv/bin/python test_dlmm_proceeds.py`
Expected: `AttributeError: module 'app' has no attribute 'compute_dlmm_conversions'`

- [ ] **Step 3: Write minimal implementation**

```python
def compute_dlmm_conversions(transactions, target_mint, wallets, sol_price_usd,
                             open_positions=None, token_price_usd=0.0):
    """Per-LbPair net CARDS/quote conversion ledger from tx history.

    A pool whose net CARDS delta is negative sold the user's CARDS for quote
    (USDC/USDT/SOL); that quote is realized proceeds. A pool whose net CARDS
    delta is positive bought CARDS for the user; the quote spent is added cost.
    Fee-claim txs are excluded. Open positions fold in as a virtual withdrawal
    at current value so an open position's parked quote still counts (this
    replaces the old live-snapshot lp_quote_value).
    """
    EPS = 1.0
    wset = set(wallets)
    pairs = identify_cards_lb_pairs(transactions, target_mint)
    agg = {}  # pair -> [net_cards, net_quote_usd]
    for tx in transactions:
        if _is_dlmm_claim(tx):
            continue
        accts = _dlmm_ix_accounts(tx)
        if not accts:
            continue
        pair = next((a for a in accts if a in pairs), None)
        if pair is None:
            continue
        td, sd, qd = _compute_balance_deltas(tx, target_mint, wset)
        q_usd = qd.get(USDC_MINT, 0.0) + qd.get(USDT_MINT, 0.0) + sd * sol_price_usd
        slot = agg.setdefault(pair, [0.0, 0.0])
        slot[0] += td
        slot[1] += q_usd
    for p in (open_positions or []):
        pair = p.get('pair_address')
        if not pair:
            continue
        slot = agg.setdefault(pair, [0.0, 0.0])
        slot[0] += p.get('tokens', 0.0)  # virtual CARDS withdrawal
        slot[1] += _dlmm_quote_value_usd(p.get('quote_tokens', 0.0),
                                         p.get('quote_symbol'), sol_price_usd)
    realized = cost = 0.0
    pools = []
    for pair, (net_cards, net_quote) in agg.items():
        if net_cards < -EPS:
            kind = 'sell'; realized += max(0.0, net_quote)
        elif net_cards > EPS:
            kind = 'buy'; cost += max(0.0, -net_quote)
        else:
            kind = 'flat'
        pools.append({'pair': pair, 'net_cards': net_cards,
                      'net_quote_usd': net_quote, 'kind': kind})
    return {'realized_proceeds_usd': realized, 'conversion_cost_usd': cost, 'pools': pools}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/solana-tracker && ./venv/bin/python test_dlmm_proceeds.py`
Expected: `Task 2 unit tests passed`

- [ ] **Step 5: Integration check against live cache (manual)**

Run the 5-wallet snippet calling `compute_dlmm_conversions(txs, CARDS, wallets, sol_price)` and assert `realized_proceeds_usd` rounds to ~29277.
Expected: `realized ~= 29277`, one `sell` pool `DL3WhGJR`, one `buy` pool `ApFwoYkU`.

- [ ] **Step 6: Commit**

```bash
cd ~/solana-tracker
git add app.py test_dlmm_proceeds.py
git commit -m "feat(dlmm): per-pool conversion ledger -> realized proceeds"
```

---

### Task 3: Wire realized proceeds into the summary, replacing the live snapshot

**Files:**
- Modify: `app.py` — `calculate_summary` (~lines 1459-1481, 1499-1517), `analyze()` route (~lines 2040-2085)
- Test: `test_dlmm_proceeds.py`, and re-run `test_dlmm_quote.py`

**Interfaces:**
- Consumes: `compute_dlmm_conversions(...)`, existing `position_breakdown`.
- Produces: `calculate_summary` accepts a new keyword `dlmm_conversions=None`; when present it uses `dlmm_conversions['realized_proceeds_usd']` as the recovered-proceeds credit instead of `position_breakdown['lp_quote_value_usd']`. Summary gains keys `dlmm_realized_proceeds_usd` and `dlmm_conversion_cost_usd`. `lp_quote_value_usd` is retained for display but no longer drives break-even.

- [ ] **Step 1: Write the failing test**

```python
def test_summary_uses_realized_proceeds_for_break_even():
    # holdings=500k, spread_cost=115855, no wallet sells, realized DLMM=29277
    conv = {'realized_proceeds_usd': 29277.0, 'conversion_cost_usd': 0.0, 'pools': []}
    s = app.calculate_summary(
        trades=[{'type': 'buy', 'token_amount': 500000.0, 'token_delta': 500000.0,
                 'quote_amount': 115855.0, 'quote_symbol': 'USDC', 'price_per_token': 0.231,
                 'timestamp': 1, 'normalized_quote_amount': 115855.0}],
        dca_aggregate={'order_count': 0, 'orders': [], 'source': 'none',
                       'buy_target_tokens': 0.0, 'buy_cost_usd': 0.0,
                       'sell_target_tokens': 0.0, 'sell_revenue_usd': 0.0,
                       'gross_usdc_out': 0.0, 'gross_usdc_in': 0.0, 'errors': []},
        on_chain_balance=500000.0, current_price_usd=0.20, sol_price_usd=70.0,
        auto_funding_usd=0.0, display_quote='USDC', manual_dca_cost=0.0,
        manual_airdrop_tokens=0.0, position_breakdown=None, dlmm_conversions=conv)
    assert abs(s['break_even_price'] - 0.1731) < 0.005
    assert round(s['dlmm_realized_proceeds_usd']) == 29277
```

(Adjust the `trades`/`dca_aggregate` shapes to whatever `calculate_summary` already requires; copy a known-good call from `test_dlmm_quote.py` and add `dlmm_conversions`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/solana-tracker && ./venv/bin/python test_dlmm_proceeds.py`
Expected: FAIL — `calculate_summary() got an unexpected keyword argument 'dlmm_conversions'`

- [ ] **Step 3: Write minimal implementation**

In `calculate_summary` signature add `dlmm_conversions=None`. Replace the recovered-quote source:

```python
    # Recovered proceeds from CARDS the DLMM pool sold. Prefer the persistent
    # per-pool conversion ledger (survives withdrawal); fall back to the live
    # open-position snapshot only when conversions weren't supplied.
    if dlmm_conversions is not None:
        lp_quote_value_usd = dlmm_conversions.get('realized_proceeds_usd', 0.0) or 0.0
        dlmm_conversion_cost_usd = dlmm_conversions.get('conversion_cost_usd', 0.0) or 0.0
    else:
        lp_quote_value_usd = (position_breakdown or {}).get('lp_quote_value_usd', 0.0) or 0.0
        dlmm_conversion_cost_usd = 0.0
    lp_quote_value_q = _normalize_to_quote(lp_quote_value_usd, 'USDC', display_quote, sol_price_usd)
```

Add to the returned dict: `'dlmm_realized_proceeds_usd': lp_quote_value_usd, 'dlmm_conversion_cost_usd': dlmm_conversion_cost_usd,`. Leave the existing `lp_quote_value_usd`/`break_even` math (lines 1469, 1480) unchanged — they now read the conversion-derived value.

In `analyze()` after `get_dlmm_positions(...)` and before `calculate_summary(...)`:

```python
        dlmm_conversions = compute_dlmm_conversions(
            unique, target_mint, wallets, sol_price_usd,
            open_positions=dlmm_positions, token_price_usd=token_price_usd,
        )
```

and pass `dlmm_conversions=dlmm_conversions` into the `calculate_summary(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/solana-tracker && ./venv/bin/python test_dlmm_proceeds.py && ./venv/bin/python test_dlmm_quote.py`
Expected: both print their pass lines; `test_dlmm_quote.py` still passes (open-position path still credited via the folded virtual withdrawal).

- [ ] **Step 5: End-to-end check against the running app**

Run the full `/api/analyze` scan (5 wallets, CARDS mint) and confirm `summary.break_even_price` ~= 0.173 and `summary.dlmm_realized_proceeds_usd` ~= 29277.
Expected: break-even back near the pre-withdrawal value.

- [ ] **Step 6: Commit**

```bash
cd ~/solana-tracker
git add app.py test_dlmm_proceeds.py
git commit -m "feat(dlmm): break-even uses persistent realized proceeds, not live snapshot"
```

---

### Task 4: Surface the new numbers in the frontend Position/P&L panels

**Files:**
- Modify: `index.html` (the Position Breakdown / summary rendering that currently shows `lp_quote_value_usd`)
- Test: manual visual check in browser

**Interfaces:**
- Consumes: `summary.dlmm_realized_proceeds_usd`, `summary.dlmm_conversion_cost_usd` from the API.

- [ ] **Step 1: Locate the current LP/quote display**

Run: `cd ~/solana-tracker && grep -n "lp_quote_value\|In DLMM\|Position Breakdown" index.html`
Expected: line numbers of the existing DLMM quote rendering.

- [ ] **Step 2: Add a "Recovered from DLMM (sold CARDS)" line**

Render `summary.dlmm_realized_proceeds_usd` as a recovered-proceeds line near realized proceeds, and (if `> 0`) a "Bought via DLMM" cost line. Keep the existing live-position display as "currently in DLMM" so the two are visually distinct. Match surrounding formatting helpers already used in `index.html`.

- [ ] **Step 3: Manual verification**

Reload `http://localhost:5000`, scan, confirm the recovered-proceeds line shows ~$29,277 and break-even shows ~$0.173.

- [ ] **Step 4: Commit**

```bash
cd ~/solana-tracker
git add index.html
git commit -m "feat(dlmm): show recovered DLMM proceeds in summary panel"
```

---

## Open considerations (out of scope for this plan, note for the user)

- **Buy-pool cost basis:** `conversion_cost_usd` (~$3,861 for pair `ApFwoYkU`) is computed and surfaced but NOT yet folded into `avg_buy_price`/`total_cost`. Folding it in would also require crediting the CARDS those buys added to holdings. Deferred — the user's reported issue is the proceeds side. Revisit if avg-buy looks off.
- **Open positions:** all positions are currently closed, so the folded virtual-withdrawal path is exercised only by `test_dlmm_quote.py`'s synthetic case. Re-verify when the user next has an open DLMM position with parked quote.
- **UNATTRIBUTED txs:** 99 CARDS-free quote-only DLMM txs (net CARDS = 0) are correctly excluded (no CARDS conversion). If a future Meteora instruction variant hides the pair from `instructions[].accounts`, those txs would silently drop — `identify_cards_lb_pairs` is the place to harden.
