"""Diag: fetch GeckoTerminal daily price history for a mint.

Usage: ./venv/bin/python price_history_diag.py <mint> [earliest_unix_ts]

Always force-fetches (ignores cache read, but refreshes the cache file).
"""
import sys
import time

from app import get_price_history_usd, _gecko_top_pool


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    mint = sys.argv[1]
    earliest = int(sys.argv[2]) if len(sys.argv) > 2 else int(time.time()) - 365 * 86400

    pool = _gecko_top_pool(mint)
    print(f'Top pool: {pool or "(none found)"}')

    candles = get_price_history_usd(mint, earliest, force_fresh=True)
    print(f'{len(candles)} daily candles')
    if candles:
        f, l = candles[0], candles[-1]
        print(f'first: {time.strftime("%Y-%m-%d", time.gmtime(f[0]))}  ${f[1]:.6f}')
        print(f'last:  {time.strftime("%Y-%m-%d", time.gmtime(l[0]))}  ${l[1]:.6f}')


if __name__ == '__main__':
    main()
