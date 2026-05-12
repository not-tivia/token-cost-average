"""Diag: print Meteora DLMM positions holding target_mint, for given wallets.

Uses on-chain RPC (Helius) via getProgramAccounts + BinArray decoding.
No public REST API exists for per-owner DLMM positions (as of May 2026).

Usage:
    python dlmm_diag.py <wallet1> [<wallet2> ...] <target_mint>
"""
import sys
from app import get_dlmm_positions, get_token_decimals


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    *wallets, mint = sys.argv[1:]
    print(f'Target mint: {mint}')
    print(f'Wallets: {wallets}\n')

    decimals = get_token_decimals(mint)
    print(f'Token decimals: {decimals}\n')

    positions, err = get_dlmm_positions(wallets, mint, target_decimals=decimals)
    if err:
        print(f'WARN: {err}\n')
    if not positions:
        print('No DLMM positions found holding the target mint.')
        return
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
