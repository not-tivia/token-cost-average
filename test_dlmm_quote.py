"""Failing-first test: DLMM quote-side value must flow into holdings/net_pnl/break-even.

Scenario mirrors the user's real position: one-sided CARDS deposit that the pool
partially converted to USDC. The quote (USDC) leg is genuine recovered proceeds.

Run: ./venv/bin/python test_dlmm_quote.py
"""
from app import build_position_breakdown, calculate_summary, USDC_MINT

SOL_PRICE = 150.0
CARDS_PRICE = 0.19  # current USD

# 150k CARDS bought for $30k (avg $0.20), all deposited one-sided into the LP.
trades = [{
    'type': 'buy', 'token_amount': 150_000.0,
    'quote_amount': 30_000.0, 'quote_symbol': 'USDC',
    'token_delta': 150_000.0,
}]
dca_aggregate = {
    'buy_target_tokens': 0, 'buy_cost_usd': 0, 'sell_target_tokens': 0,
    'sell_revenue_usd': 0, 'order_count': 0, 'errors': [], 'orders': [],
    'gross_usdc_out': 0, 'gross_usdc_in': 0,
}
# Position as decoded on-chain: 86,262 CARDS + 62,430 USDC remaining.
dlmm_positions = [{
    'wallet': 'W', 'position_pubkey': 'P', 'pair_address': 'PAIR',
    'tokens': 86_262.0,
    'quote_tokens': 62_430.0, 'quote_mint': USDC_MINT, 'quote_symbol': 'USDC',
}]

pb = build_position_breakdown(
    wallet_tokens=0.0, limit_orders=[], limit_err=None,
    dlmm_positions=dlmm_positions, dlmm_err=None,
    current_price_usd=CARDS_PRICE, sol_price_usd=SOL_PRICE,
)

# --- Position breakdown must value the quote leg ---
assert abs(pb['dlmm']['quote_value_usd'] - 62_430.0) < 1.0, \
    f"dlmm quote_value_usd should be ~62430, got {pb['dlmm']['quote_value_usd']}"
expected_dlmm_total = 86_262.0 * CARDS_PRICE + 62_430.0
assert abs(pb['dlmm']['value_usd'] - expected_dlmm_total) < 1.0, \
    f"dlmm value_usd should include both legs (~{expected_dlmm_total:.0f}), got {pb['dlmm']['value_usd']}"

summary = calculate_summary(
    trades, dca_aggregate, on_chain_balance=0.0,
    current_price_usd=CARDS_PRICE, sol_price_usd=SOL_PRICE,
    auto_funding_usd=0.0, display_quote='USDC',
    position_breakdown=pb,
)

# --- Net PnL must include the recovered USDC ---
expected_net = 0.0 + 86_262.0 * CARDS_PRICE + 62_430.0 - 30_000.0
assert abs(summary['net_pnl'] - expected_net) < 5.0, \
    f"net_pnl should be ~{expected_net:.0f}, got {summary['net_pnl']:.2f}"

# --- Break-even: recovered USDC ($62.4k) exceeds remaining cost ($30k) -> 0 ---
assert summary['break_even_price'] < 0.01, \
    f"break_even_price should be ~0 (past break-even), got {summary['break_even_price']:.4f}"

print("PASS: DLMM quote-side value flows into holdings, net_pnl, and break-even.")
print(f"  net_pnl       = ${summary['net_pnl']:,.2f}")
print(f"  break_even    = ${summary['break_even_price']:.4f}")
print(f"  dlmm value    = ${pb['dlmm']['value_usd']:,.2f} "
      f"(CARDS ${86_262.0*CARDS_PRICE:,.0f} + USDC ${pb['dlmm']['quote_value_usd']:,.0f})")
