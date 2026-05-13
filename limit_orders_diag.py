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
