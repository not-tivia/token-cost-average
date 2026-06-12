"""Failing-first test for get_price_history_usd: parsing, pool choice,
cache short-circuit, trimming, network-failure fallback, and DefiLlama
backfill of days older than GeckoTerminal's 180-day public-API window.

Run: ./venv/bin/python test_price_history.py
"""
import json
import time

import app
from app import get_price_history_usd, _pricehist_cache_path

MINT = 'TESTMINTPriceHistoryxxxxxxxxxxxxxxxxxxxxxxx'
MINT2 = 'TESTMINTPriceHistoryNoCachexxxxxxxxxxxxxxxx'
DAY = 86400
TODAY = int(time.time()) // DAY * DAY

calls = {'pools': 0, 'ohlcv': 0, 'llama': 0}
LLAMA = {'rows': [], 'raise': False}


class FakeResp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200

    def json(self):
        return self._p


def fake_get(url, params=None, timeout=None):
    if '/tokens/' in url and url.endswith('/pools'):
        calls['pools'] += 1
        return FakeResp({'data': [
            {'attributes': {'address': 'POOL_SMALL', 'reserve_in_usd': '1000'}},
            {'attributes': {'address': 'POOL_BIG', 'reserve_in_usd': '50000'}},
        ]})
    if '/ohlcv/day' in url:
        calls['ohlcv'] += 1
        assert 'POOL_BIG' in url, f'should query deepest-liquidity pool, got {url}'
        return FakeResp({'data': {'attributes': {'ohlcv_list': [
            [TODAY,           0.25, 0.26, 0.24, 0.250, 1234.0],
            [TODAY - DAY,     0.24, 0.25, 0.23, 0.245, 2345.0],
            [TODAY - 2 * DAY, 0.23, 0.24, 0.22, 0.240, 3456.0],
        ]}}})
    if 'coins.llama.fi' in url:
        calls['llama'] += 1
        LLAMA['last'] = dict(params or {})
        if LLAMA['raise']:
            raise RuntimeError('llama down')
        key = 'solana:' + url.rsplit('solana:', 1)[1]
        return FakeResp({'coins': {key: {'prices': LLAMA['rows']}}})
    raise AssertionError(f'unexpected URL {url}')


def raising_get(url, params=None, timeout=None):
    raise RuntimeError('network down')


# --- clean slate ---
for m in (MINT, MINT2):
    p = _pricehist_cache_path(m)
    if p.exists():
        p.unlink()

app.requests.get = fake_get

# 1. First call: fetches, parses closes ascending, caches. The GT data starts
#    after cutoff, so a DefiLlama backfill is attempted (empty -> no flag set).
candles = get_price_history_usd(MINT, earliest_ts=TODAY - 2 * DAY)
assert candles == [[TODAY - 2 * DAY, 0.240], [TODAY - DAY, 0.245], [TODAY, 0.250]], candles
assert calls == {'pools': 1, 'ohlcv': 1, 'llama': 1}, calls
cached = json.loads(_pricehist_cache_path(MINT).read_text())
assert cached['pool'] == 'POOL_BIG', cached
assert len(cached['candles']) == 3, cached
assert cached.get('backfilled') is False, cached

# 2. Second call same day: cache has today's candle -> no network at all
candles2 = get_price_history_usd(MINT, earliest_ts=TODAY - 2 * DAY)
assert candles2 == candles
assert calls == {'pools': 1, 'ohlcv': 1, 'llama': 1}, f'cache should short-circuit, got {calls}'

# 3. Trimming: cutoff is earliest_ts - 7 days
trimmed = get_price_history_usd(MINT, earliest_ts=TODAY + 6 * DAY)  # cutoff = TODAY - DAY
assert trimmed == [[TODAY - DAY, 0.245], [TODAY, 0.250]], trimmed

# 4. Stale cache + network failure -> serves cached candles, never raises
stale = {'pool': 'POOL_BIG', 'updated_ts': 0,
         'candles': [[TODAY - 5 * DAY, 0.2], [TODAY - 4 * DAY, 0.21]]}
_pricehist_cache_path(MINT).write_text(json.dumps(stale))
app.requests.get = raising_get
fallback = get_price_history_usd(MINT, earliest_ts=0)
assert fallback == [[TODAY - 5 * DAY, 0.2], [TODAY - 4 * DAY, 0.21]], fallback

# 5. No cache + network failure -> empty list, never raises
empty = get_price_history_usd(MINT2, earliest_ts=0)
assert empty == [], empty

# 5b. Corrupted cache shapes -> degrade gracefully, never raise
for bad in ('null', '[]', '"junk"', '{"pool": "POOL_BIG", "candles": [[42], ["abc", "x"]]}'):
    _pricehist_cache_path(MINT2).write_text(bad)
    got = get_price_history_usd(MINT2, earliest_ts=0)
    assert got == [], (bad, got)

# 6. force_fresh rediscovers the pool, refetches, and re-attempts backfill
app.requests.get = fake_get
fresh = get_price_history_usd(MINT, earliest_ts=0, force_fresh=True)
assert fresh[-1] == [TODAY, 0.250], fresh
assert calls == {'pools': 2, 'ohlcv': 2, 'llama': 2}, calls

# 7. DefiLlama backfill fills days older than GeckoTerminal's window;
#    GT wins on overlapping days; flag set once backfill data arrives.
p = _pricehist_cache_path(MINT)
if p.exists():
    p.unlink()
LLAMA['rows'] = [
    {'timestamp': TODAY - 12 * DAY + 3600, 'price': 0.10},   # old day -> kept
    {'timestamp': TODAY - 11 * DAY + 7200, 'price': 0.11},   # old day -> kept
    {'timestamp': TODAY - 2 * DAY + 60,    'price': 0.99},   # overlaps GT -> GT wins
]
merged = get_price_history_usd(MINT, earliest_ts=TODAY - 5 * DAY)  # cutoff = TODAY-12d
assert merged == [
    [TODAY - 12 * DAY, 0.10], [TODAY - 11 * DAY, 0.11],
    [TODAY - 2 * DAY, 0.240], [TODAY - DAY, 0.245], [TODAY, 0.250],
], merged
cached = json.loads(p.read_text())
assert cached.get('backfilled') is True, cached

# 8. Backfilled flag prevents repeat DefiLlama calls on later incremental updates
n_llama = calls['llama']
stale = dict(cached)
stale['candles'] = [c for c in cached['candles'] if c[0] < TODAY]  # drop today -> stale
p.write_text(json.dumps(stale))
again = get_price_history_usd(MINT, earliest_ts=TODAY - 5 * DAY)
assert again[-1] == [TODAY, 0.250], again
assert again[0] == [TODAY - 12 * DAY, 0.10], again
assert calls['llama'] == n_llama, 'backfilled cache must not re-call DefiLlama'

# 9. DefiLlama failure: GT data still returned, flag stays False for retry,
#    no exception
if p.exists():
    p.unlink()
LLAMA['raise'] = True
gt_only = get_price_history_usd(MINT, earliest_ts=TODAY - 5 * DAY)
assert gt_only == [[TODAY - 2 * DAY, 0.240], [TODAY - DAY, 0.245], [TODAY, 0.250]], gt_only
assert json.loads(p.read_text()).get('backfilled') is False
LLAMA['raise'] = False

# 10. DefiLlama requests respect the 500-point API cap: when the gap is wider
#     than 500 days, the window is anchored to end at the first GT candle
if p.exists():
    p.unlink()
LLAMA['rows'] = [{'timestamp': TODAY - 12 * DAY, 'price': 0.10}]
get_price_history_usd(MINT, earliest_ts=TODAY - 600 * DAY)
lp = LLAMA['last']
assert lp.get('span', 9999) <= 500, lp
assert lp.get('start') == (TODAY - 2 * DAY) - 498 * DAY, lp

# --- cleanup ---
for m in (MINT, MINT2):
    p = _pricehist_cache_path(m)
    if p.exists():
        p.unlink()

print('OK - all price-history assertions passed')
