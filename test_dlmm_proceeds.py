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
