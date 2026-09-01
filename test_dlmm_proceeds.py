import app

def test_dlmm_ix_accounts_extracts_meteora_accounts():
    tx = {'instructions': [
        {'programId': 'OtherProg', 'accounts': ['a', 'b']},
        {'programId': app.METEORA_DLMM, 'accounts': ['pair', 'pos', 'user']},
    ]}
    assert app._dlmm_ix_accounts(tx) == ['pair', 'pos', 'user']

def test_dlmm_ix_accounts_none_when_no_dlmm():
    assert app._dlmm_ix_accounts({'instructions': [{'programId': 'X', 'accounts': []}]}) == []


class _MP:
    def __init__(self): self._undo = []
    def setattr(self, obj, name, val):
        old = getattr(obj, name); self._undo.append((obj, name, old)); setattr(obj, name, val)
    def undo(self):
        for obj, name, old in reversed(self._undo): setattr(obj, name, old)


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
    assert round(out['conversion_tokens']) == 30000, out['conversion_tokens']
    assert out['fee_income_usd'] == 0.0
    kinds = {p['pair']: p['kind'] for p in out['pools']}
    assert kinds == {'PAIRSELL': 'sell', 'PAIRBUY': 'buy'}
    assert out['tx_signatures'] == {'s1', 's2', 'b1'}


def test_conversions_fee_claims_are_income_not_principal(monkeypatch):
    # Pure fee claims on a CARDS pool: quote is cash income, CARDS fees are
    # NOT netted into the pool's principal ledger (they already sit in wallet).
    monkeypatch.setattr(app, 'identify_cards_lb_pairs', lambda txs, m: {'PAIRSELL', 'PAIROTHER'})
    monkeypatch.setattr(app, '_is_dlmm_claim', lambda tx: tx['signature'].startswith('c'))
    monkeypatch.setattr(app, '_compute_balance_deltas',
        lambda tx, m, ws: (tx['_test_cards'], tx.get('_test_sol', 0.0), {app.USDC_MINT: tx['_test_usdc']}))
    txs = [
        _lp_tx('s1', 'PAIRSELL', -100000.0, 0.0),
        _lp_tx('s2', 'PAIRSELL', 0.0, 20000.0),
        _lp_tx('c1', 'PAIRSELL', 500.0, 1200.0),        # claim: 500 CARDS + 1200 USDC
        _lp_tx('c2', 'PAIRSELL', 100.0, 300.0),         # claim
        _lp_tx('c3', 'PAIROTHER', 0.0, 0.0),            # claim, SOL only
    ]
    txs[-1]['_test_sol'] = 2.0
    out = app.compute_dlmm_conversions(txs, 'MINT', ['user'], sol_price_usd=100.0)
    assert round(out['realized_proceeds_usd']) == 20000      # claims not in principal
    assert round(out['fee_income_usd']) == 1500 + 200         # USDC + 2 SOL @ $100
    assert round(out['fee_target_tokens']) == 600
    sell = next(p for p in out['pools'] if p['pair'] == 'PAIRSELL')
    assert round(sell['net_cards']) == -100000 and round(sell['fees_target']) == 600


def test_summary_folds_dlmm_buys_and_fee_income():
    # 500k bought for $115,855 + 38,670 bought via a DLMM pool for $5,422;
    # $29,277 pool-sold proceeds + $17,326 claimed USDC fees.
    conv = {'realized_proceeds_usd': 29277.0, 'conversion_cost_usd': 5422.0,
            'conversion_tokens': 38670.0, 'fee_income_usd': 17326.0, 'pools': []}
    trades = [{
        'type': 'buy', 'token_amount': 500000.0, 'token_delta': 500000.0,
        'quote_amount': 115855.0, 'quote_symbol': 'USDC', 'price_per_token': 0.231,
        'timestamp': 1, 'normalized_quote_amount': 115855.0,
    }]
    dca_aggregate = {
        'order_count': 0, 'orders': [], 'source': 'none',
        'buy_target_tokens': 0.0, 'buy_cost_usd': 0.0,
        'sell_target_tokens': 0.0, 'sell_revenue_usd': 0.0,
        'gross_usdc_out': 0.0, 'gross_usdc_in': 0.0, 'errors': [],
    }
    s = app.calculate_summary(
        trades=trades, dca_aggregate=dca_aggregate, on_chain_balance=538670.0,
        current_price_usd=0.20, sol_price_usd=70.0, auto_funding_usd=0.0,
        display_quote='USDC', manual_dca_cost=0.0, manual_airdrop_tokens=0.0,
        position_breakdown=None, dlmm_conversions=conv,
    )
    cost = 115855.0 + 5422.0
    assert abs(s['total_invested'] - cost) < 0.01, s['total_invested']
    assert abs(s['total_bought_tokens'] - 538670.0) < 0.01
    assert abs(s['avg_buy_price'] - cost / 538670.0) < 1e-9
    assert abs(s['cost_breakdown']['dlmm_buys']['cost'] - 5422.0) < 0.01
    # net P/L = holdings*price + proceeds + fees - cost
    expect_pnl = 538670.0 * 0.20 + 29277.0 + 17326.0 - cost
    assert abs(s['net_pnl'] - expect_pnl) < 0.01, (s['net_pnl'], expect_pnl)
    expect_be = (cost - 29277.0 - 17326.0) / 538670.0
    assert abs(s['break_even_price'] - expect_be) < 1e-9, (s['break_even_price'], expect_be)
    assert round(s['dlmm_fee_income_usd']) == 17326
    assert round(s['dlmm_recovered_usd']) == 29277 + 17326


def test_summary_uses_realized_proceeds_for_break_even():
    # holdings=500k, spread_cost=115855, no wallet sells, realized DLMM=29277
    conv = {'realized_proceeds_usd': 29277.0, 'conversion_cost_usd': 0.0, 'pools': []}
    trades = [{
        'type': 'buy', 'token_amount': 500000.0, 'token_delta': 500000.0,
        'quote_amount': 115855.0, 'quote_symbol': 'USDC', 'price_per_token': 0.231,
        'timestamp': 1, 'normalized_quote_amount': 115855.0,
    }]
    dca_aggregate = {
        'order_count': 0, 'orders': [], 'source': 'none',
        'buy_target_tokens': 0.0, 'buy_cost_usd': 0.0,
        'sell_target_tokens': 0.0, 'sell_revenue_usd': 0.0,
        'gross_usdc_out': 0.0, 'gross_usdc_in': 0.0, 'errors': [],
    }
    s = app.calculate_summary(
        trades=trades,
        dca_aggregate=dca_aggregate,
        on_chain_balance=500000.0,
        current_price_usd=0.20,
        sol_price_usd=70.0,
        auto_funding_usd=0.0,
        display_quote='USDC',
        manual_dca_cost=0.0,
        manual_airdrop_tokens=0.0,
        position_breakdown=None,
        dlmm_conversions=conv,
    )
    assert abs(s['break_even_price'] - 0.1731) < 0.005, \
        f"break_even_price should be ~0.173, got {s['break_even_price']:.4f}"
    assert round(s['dlmm_realized_proceeds_usd']) == 29277, \
        f"dlmm_realized_proceeds_usd should be 29277, got {s['dlmm_realized_proceeds_usd']}"


def test_transient_fetch_failure_not_cached(monkeypatch):
    """I-1 regression: a transient _get_account_data exception must NOT poison
    the (acct, mint) cache entry so the next scan can retry the account.
    M-1: cache key is (acct, target_mint), not acct alone.
    """
    ACCT = 'POOLXaaaaaaaaaaaaaaaaaaaaaaaaaaaa'  # 33-char dummy, passes len>=32 guard
    MINT = 'MINT'

    # Build a minimal DLMM tx that names POOLX in its instruction accounts.
    tx = {
        'signature': 'sig-regr',
        'instructions': [
            {'programId': app.METEORA_DLMM, 'accounts': [ACCT, 'pos', 'user']},
        ],
    }

    sentinel = object()
    call_count = [0]

    def fake_get_account_data(acct):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError('transient RPC failure')
        return sentinel

    def fake_decode_lb_pair(raw):
        if raw is sentinel:
            return {'token_x_mint': MINT, 'token_y_mint': 'OTHER'}
        raise ValueError('unexpected raw')

    monkeypatch.setattr(app, '_get_account_data', fake_get_account_data)
    monkeypatch.setattr(app, '_decode_lb_pair', fake_decode_lb_pair)
    monkeypatch.setattr(app, '_is_dlmm_claim', lambda tx: False)

    # Clear any pre-existing cache entries that might mask the bug.
    app._CARDS_LB_PAIR_CACHE.clear()

    # First call: fetch raises, pool unresolvable this scan.
    result1 = app.identify_cards_lb_pairs([tx], MINT)
    assert result1 == set(), f"expected empty set on first call, got {result1}"

    # The failure must NOT have been written to the cache.
    assert (ACCT, MINT) not in app._CARDS_LB_PAIR_CACHE, \
        "transient failure incorrectly written to cache (I-1 bug)"

    # Second call: fetch succeeds, pool is now resolved.
    result2 = app.identify_cards_lb_pairs([tx], MINT)
    assert result2 == {ACCT}, f"expected {{{ACCT!r}}} on second call, got {result2}"

    # The successful verdict IS now in the cache.
    assert app._CARDS_LB_PAIR_CACHE.get((ACCT, MINT)) is True, \
        "successful verdict should be cached after second call"


if __name__ == '__main__':
    test_dlmm_ix_accounts_extracts_meteora_accounts()
    test_dlmm_ix_accounts_none_when_no_dlmm()
    print('Task 1 unit tests passed')

    mp = _MP()
    try:
        test_conversions_sell_pool_credits_quote(mp)
    finally:
        mp.undo()
    print('Task 2 unit tests passed')

    test_summary_uses_realized_proceeds_for_break_even()
    print('Task 3 unit tests passed')

    mp = _MP()
    try:
        test_conversions_fee_claims_are_income_not_principal(mp)
    finally:
        mp.undo()
    test_summary_folds_dlmm_buys_and_fee_income()
    print('Fee-income / DLMM-buy cost-basis unit tests passed')

    mp2 = _MP()
    try:
        test_transient_fetch_failure_not_cached(mp2)
    finally:
        mp2.undo()
    print('Task 4 (I-1/M-1 regression) unit tests passed')
