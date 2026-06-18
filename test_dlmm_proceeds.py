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
    kinds = {p['pair']: p['kind'] for p in out['pools']}
    assert kinds == {'PAIRSELL': 'sell', 'PAIRBUY': 'buy'}


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
