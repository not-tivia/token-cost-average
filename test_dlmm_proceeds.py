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
