"""Failing-first test for get_price_history_usd: parsing, pool choice,
cache short-circuit, trimming, and network-failure fallback.

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

calls = {'pools': 0, 'ohlcv': 0}


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
    raise AssertionError(f'unexpected URL {url}')


def raising_get(url, params=None, timeout=None):
    raise RuntimeError('network down')


# --- clean slate ---
for m in (MINT, MINT2):
    p = _pricehist_cache_path(m)
    if p.exists():
        p.unlink()

app.requests.get = fake_get

# 1. First call: fetches, parses closes ascending, caches
candles = get_price_history_usd(MINT, earliest_ts=TODAY - 2 * DAY)
assert candles == [[TODAY - 2 * DAY, 0.240], [TODAY - DAY, 0.245], [TODAY, 0.250]], candles
assert calls == {'pools': 1, 'ohlcv': 1}, calls
cached = json.loads(_pricehist_cache_path(MINT).read_text())
assert cached['pool'] == 'POOL_BIG', cached
assert len(cached['candles']) == 3, cached

# 2. Second call same day: cache has today's candle -> no network at all
candles2 = get_price_history_usd(MINT, earliest_ts=TODAY - 2 * DAY)
assert candles2 == candles
assert calls == {'pools': 1, 'ohlcv': 1}, f'cache should short-circuit, got {calls}'

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

# 6. force_fresh rediscovers the pool and refetches
app.requests.get = fake_get
fresh = get_price_history_usd(MINT, earliest_ts=0, force_fresh=True)
assert fresh[-1] == [TODAY, 0.250], fresh
assert calls == {'pools': 2, 'ohlcv': 2}, calls

# --- cleanup ---
for m in (MINT, MINT2):
    p = _pricehist_cache_path(m)
    if p.exists():
        p.unlink()

print('OK - all price-history assertions passed')
