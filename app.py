"""
Solana Token Tracker — v3.12.

Changes from v3.11:
- Limit-order pairing: each Jupiter Limit V1/V2 setup tx is now matched with
  its fills (and any cancellation refunds) via the shared Reserve token
  account. Each ORDER becomes one priced event with: USDC deposited, USDC
  refunded, USDC actually spent, tokens received, and the resulting avg
  fill price. Same logic mirrored for sell limit orders (CARDS Reserve).
- surface_best_worst's keeper_buys/keeper_sells now populate from these
  paired orders. Trade Insights "Keeper Buys (Best/Worst)" finally shows
  the real per-order entry prices ($0.28, $0.26, $0.24, etc).
- Limit-order ledger printed to console with full breakdown per order.

Backend-only change, index.html unchanged.
"""

import os
import json
import math
import base64
import struct as _struct
import hashlib as _hashlib
import time
import requests
from collections import Counter, defaultdict
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
HELIUS_API_KEY = os.getenv('HELIUS_API_KEY')
HELIUS_BASE = 'https://api.helius.xyz/v0'
HELIUS_RPC = f'https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}'

USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
USDT_MINT = 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'
SOL_MINT  = 'So11111111111111111111111111111111111111112'
QUOTE_MINTS = {USDC_MINT, USDT_MINT}
QUOTE_INFO = {
    SOL_MINT:  ('SOL',  9),
    USDC_MINT: ('USDC', 6),
    USDT_MINT: ('USDT', 6),
}

JUP_DCA_PROGRAM     = 'DCA265Vj8a9CEuX1eb1LWRnDT7uK6q1xMipnNyatn23M'
JUP_LIMIT_V1        = 'j1o2qRpjcyUwEvwtcfhEQefh773ZgjxcVRry7LDqg5X'
JUP_LIMIT_V2        = 'jupoNjAxXgZ4rjzxzPMP4oxduvQsQtZzyknqvzYNrNu'
JUP_AGGREGATOR_V6   = 'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4'
KEEPER_PROGRAMS     = {JUP_DCA_PROGRAM, JUP_LIMIT_V1, JUP_LIMIT_V2}
KEEPER_NAMES = {
    JUP_DCA_PROGRAM:   'Jupiter DCA',
    JUP_LIMIT_V1:      'Jupiter Limit V1',
    JUP_LIMIT_V2:      'Jupiter Limit V2',
    JUP_AGGREGATOR_V6: 'Jupiter Recurring/Aggregator',
}

METEORA_DLMM    = 'LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo'
LP_PROGRAMS     = {METEORA_DLMM}
LP_NAMES        = {METEORA_DLMM: 'Meteora DLMM'}

METAPLEX_DISTRO  = 'D1STRoZTUiEa6r8TLg2aAbG4nSRT5cDBmgG7jDqCZvU8'
METAPLEX_GUMDROP = 'gdrpGjVffourzkdDRrQmySw4aTHr8a3xmQzzxSwFD1a'
DEFAULT_AIRDROP_PROGRAMS = {METAPLEX_DISTRO, METAPLEX_GUMDROP}

INFRA_PROGRAMS = {
    '11111111111111111111111111111111',
    'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
    'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb',
    'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL',
    'ComputeBudget111111111111111111111111111111',
}

SOL_QUOTE_THRESHOLD = 0.05
USDC_QUOTE_THRESHOLD = 0.50
FUND_MIN_SOL     = 0.1
FUND_MIN_USDC    = 5.0
FUND_MIN_INSTRS  = 5
SELF_TRANSFER_WINDOW_HOURS = 72
SELF_TRANSFER_TOL_PCT      = 0.5

DCA_SETUP_LOOKBACK_DAYS  = 60
DCA_SETUP_LOOKAHEAD_DAYS = 30
DCA_SETUP_MIN_USDC       = 5.0
DCA_SETUP_MIN_SOL        = 0.05

ROUTE_USDC_MIN = 1.0
REFUND_MIN_USDC = 1.0
REFUND_MIN_SOL  = 0.01


# =========================================================================
# Disk cache
# =========================================================================
CACHE_DIR = Path('cache')

def _cache_path(wallet):
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f'{wallet}.json'

def _load_cache(wallet):
    p = _cache_path(wallet)
    if p.exists():
        try:
            with p.open() as f: return json.load(f)
        except Exception as e: print(f'[cache] load error {wallet}: {e}')
    return {'txs': [], 'last_updated': 0}

def _save_cache(wallet, data):
    try:
        with _cache_path(wallet).open('w') as f: json.dump(data, f)
    except Exception as e: print(f'[cache] save error {wallet}: {e}')


# =========================================================================
# Rate limiting + retry
# =========================================================================
_last_request_time = 0.0
def _raw_remaining(o, primary, fallback):
    """Return remaining raw amount as float. Treats key-missing as fallback,
    but a literal 0 in the primary is respected (do not fall through)."""
    v = o.get(primary)
    if v is not None:
        try: return float(v)
        except (TypeError, ValueError): pass
    v = o.get(fallback)
    if v is not None:
        try: return float(v)
        except (TypeError, ValueError): pass
    return 0.0


def _rate_limit(min_gap=0.5):
    global _last_request_time
    now = time.time()
    gap = now - _last_request_time
    if gap < min_gap: time.sleep(min_gap - gap)
    _last_request_time = time.time()


def _request_with_retry(method, url, *, max_retries=6, **kwargs):
    delay = 1.0
    for attempt in range(max_retries):
        _rate_limit()
        resp = requests.request(method, url, **kwargs)
        rate_limited = resp.status_code == 429
        if not rate_limited and resp.status_code == 200:
            try:
                body = resp.json()
                if isinstance(body, dict) and body.get('error', {}).get('code') == -32429:
                    rate_limited = True
            except Exception: pass
        if rate_limited:
            if attempt == max_retries - 1: resp.raise_for_status()
            print(f'[rate-limit] backing off {delay:.1f}s')
            time.sleep(delay); delay = min(delay * 2, 30); continue
        return resp
    return resp


# =========================================================================
# Helius / RPC
# =========================================================================
def get_parsed_transactions_page(wallet, before=None, limit=100):
    url = f'{HELIUS_BASE}/addresses/{wallet}/transactions'
    params = {'api-key': HELIUS_API_KEY, 'limit': limit}
    if before: params['before'] = before
    resp = _request_with_retry('GET', url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_all_transactions_cached(wallet, max_pages=100, force_fresh=False):
    cache = {'txs': [], 'last_updated': 0} if force_fresh else _load_cache(wallet)
    cached_txs = cache.get('txs', [])
    disk_cached_sigs = frozenset(tx.get('signature') for tx in cached_txs if tx.get('signature'))
    print(f'[cache] {wallet}: starting with {len(cached_txs)} cached txs')

    new_txs = []; new_sigs = set(); before = None; hit_cached = False
    for page_num in range(max_pages):
        page = get_parsed_transactions_page(wallet, before=before)
        if not page: break
        for tx in page:
            sig = tx.get('signature')
            if not sig: continue
            if sig in disk_cached_sigs: hit_cached = True; break
            if sig in new_sigs: continue
            new_sigs.add(sig); new_txs.append(tx)
        if hit_cached:
            print(f'[cache] {wallet}: hit cached at page {page_num + 1}; +{len(new_txs)} new')
            break
        if len(page) < 100: break
        before = page[-1].get('signature')
        if not before: break

    if new_txs:
        all_txs = new_txs + cached_txs
        _save_cache(wallet, {'txs': all_txs, 'last_updated': int(time.time())})
        print(f'[cache] {wallet}: +{len(new_txs)} new, {len(all_txs)} total')
        return all_txs
    return cached_txs


def get_token_balance_on_chain(wallet, mint):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
               "params": [wallet, {"mint": mint}, {"encoding": "jsonParsed"}]}
    try:
        resp = _request_with_retry('POST', HELIUS_RPC, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        total = 0.0
        for acct in data.get('result', {}).get('value', []) or []:
            info = acct.get('account', {}).get('data', {}).get('parsed', {}).get('info', {})
            ui_amt = info.get('tokenAmount', {}).get('uiAmount')
            if ui_amt is not None: total += float(ui_amt)
        return total
    except Exception as e:
        print(f'[balance] error: {e}'); return None


def get_token_decimals(mint):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
               "params": [mint, {"encoding": "jsonParsed"}]}
    try:
        resp = _request_with_retry('POST', HELIUS_RPC, json=payload, timeout=15)
        info = resp.json().get('result', {}).get('value', {}).get('data', {}).get('parsed', {}).get('info', {})
        return int(info.get('decimals', 9))
    except Exception:
        return 9


def get_token_symbol(mint):
    try:
        resp = requests.get(f'https://api.dexscreener.com/latest/dex/tokens/{mint}', timeout=8)
        if resp.status_code != 200: return ''
        pairs = resp.json().get('pairs', []) or []
        if not pairs: return ''
        return pairs[0].get('baseToken', {}).get('symbol', '') or ''
    except Exception: return ''


def _price_from_jupiter(mint):
    try:
        resp = requests.get(f'https://api.jup.ag/price/v2?ids={mint}', timeout=10)
        if resp.status_code != 200: return 0.0
        return float(resp.json().get('data', {}).get(mint, {}).get('price', 0) or 0)
    except Exception: return 0.0


def _price_from_dexscreener(mint):
    try:
        resp = requests.get(f'https://api.dexscreener.com/latest/dex/tokens/{mint}', timeout=10)
        if resp.status_code != 200: return 0.0
        pairs = resp.json().get('pairs', []) or []
        usd_pairs = [p for p in pairs if (p.get('quoteToken', {}).get('symbol') in ('USDC', 'USDT', 'USD'))]
        if not usd_pairs: usd_pairs = pairs
        if not usd_pairs: return 0.0
        best = max(usd_pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))
        price = best.get('priceUsd')
        return float(price) if price else 0.0
    except Exception: return 0.0


def _price_from_coingecko_sol():
    try:
        resp = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd', timeout=10)
        if resp.status_code != 200: return 0.0
        return float(resp.json().get('solana', {}).get('usd', 0) or 0)
    except Exception: return 0.0


def get_token_price_usd(mint):
    p = _price_from_jupiter(mint)
    if p > 0: return p
    p = _price_from_dexscreener(mint)
    if p > 0: return p
    if mint == SOL_MINT:
        p = _price_from_coingecko_sol()
        if p > 0: return p
    return 0.0


# =========================================================================
# Jupiter DCA API + tx-based aggregator
# =========================================================================
def get_jupiter_dca_aggregate_api(wallets, target_mint, target_decimals):
    out = {'orders': [], 'order_count': 0, 'source': 'api',
           'buy_target_tokens': 0.0, 'buy_cost_usd': 0.0,
           'sell_target_tokens': 0.0, 'sell_revenue_usd': 0.0, 'errors': [],
           'gross_usdc_out': 0.0, 'gross_usdc_in': 0.0}
    sol_price = get_token_price_usd(SOL_MINT)
    for wallet in wallets:
        try:
            _rate_limit(0.2)
            resp = requests.get(f'https://dca-api.jup.ag/dca/{wallet}', timeout=20)
            if resp.status_code != 200: continue
            payload = resp.json()
        except Exception: continue
        orders = None
        if isinstance(payload, list): orders = payload
        elif isinstance(payload, dict):
            for key in ('data', 'dcaAccounts', 'orders', 'positions', 'fills', 'result'):
                v = payload.get(key)
                if isinstance(v, list): orders = v; break
        if not isinstance(orders, list) or not orders: continue
        for o in orders:
            if not isinstance(o, dict): continue
            input_mint  = o.get('inputMint')  or o.get('input_mint')
            output_mint = o.get('outputMint') or o.get('output_mint')
            if input_mint != target_mint and output_mint != target_mint: continue
            def _atom(*keys):
                for k in keys:
                    v = o.get(k)
                    if v is not None:
                        try: return float(v)
                        except (TypeError, ValueError): pass
                return 0.0
            in_used      = _atom('inUsed', 'in_used', 'amountUsed')
            out_received = _atom('outReceived', 'out_received', 'amountReceived')
            def _dec(m):
                if m == target_mint: return target_decimals
                if m in QUOTE_INFO: return QUOTE_INFO[m][1]
                return 9
            in_used_ui      = in_used      / (10 ** _dec(input_mint))
            out_received_ui = out_received / (10 ** _dec(output_mint))
            qpu = 1.0 if input_mint in (USDC_MINT, USDT_MINT) else (sol_price if input_mint == SOL_MINT else 0.0)
            out['orders'].append({
                'wallet': wallet, 'dcaKey': o.get('dcaKey') or o.get('dca_key'),
                'input_mint': input_mint, 'output_mint': output_mint,
                'in_used_ui': in_used_ui, 'out_received_ui': out_received_ui,
                'direction': 'buy' if output_mint == target_mint else 'sell',
                'status': o.get('status'),
            })
            out['order_count'] += 1
            if output_mint == target_mint:
                out['buy_target_tokens'] += out_received_ui
                out['buy_cost_usd']      += in_used_ui * qpu
            else:
                out['sell_target_tokens'] += in_used_ui
                opu = 1.0 if output_mint in (USDC_MINT, USDT_MINT) else (sol_price if output_mint == SOL_MINT else 0.0)
                out['sell_revenue_usd']  += out_received_ui * opu
    return out


def aggregate_dca_from_txs(events, sol_price_usd):
    out = {'orders': [], 'order_count': 0, 'source': 'tx-based',
           'buy_target_tokens': 0.0, 'buy_cost_usd': 0.0,
           'sell_target_tokens': 0.0, 'sell_revenue_usd': 0.0, 'errors': [],
           'gross_usdc_out': 0.0, 'gross_usdc_in': 0.0}
    target_in = target_out = usdc_out = usdc_in = sol_out = sol_in = 0.0
    n_events = n_refunds = 0
    for e in events:
        if e.get('type') != 'dca_tx': continue
        n_events += 1
        td = e.get('token_delta', 0) or 0
        sd = e.get('sol_delta', 0) or 0
        ud = (e.get('usdc_delta', 0) or 0) + (e.get('usdt_delta', 0) or 0)
        if e.get('is_refund'): n_refunds += 1
        if td > 0:   target_in  += td
        elif td < 0: target_out += -td
        if ud < 0:   usdc_out += -ud
        elif ud > 0: usdc_in  += ud
        if sd < 0:   sol_out += -sd
        elif sd > 0: sol_in  += sd

    out['order_count'] = n_events
    out['gross_usdc_out'] = usdc_out
    out['gross_usdc_in']  = usdc_in
    net_target = target_in - target_out
    net_usdc   = usdc_out - usdc_in
    net_sol    = sol_out  - sol_in
    if net_target > 0:
        out['buy_target_tokens'] = net_target
        out['buy_cost_usd']      = max(net_usdc, 0) + max(net_sol, 0) * sol_price_usd
    elif net_target < 0:
        out['sell_target_tokens'] = -net_target
        out['sell_revenue_usd']   = max(-net_usdc, 0) + max(-net_sol, 0) * sol_price_usd
    print(f'[dca-tx] {n_events} keeper events ({n_refunds} refunds): net_target={net_target:+,.0f}, cost=${out["buy_cost_usd"]:.2f}')
    return out


# =========================================================================
# Tx parsing helpers
# =========================================================================
def _program_ids(tx):
    top, every = set(), set()
    for instr in tx.get('instructions', []) or []:
        pid = instr.get('programId') or instr.get('program_id')
        if pid: top.add(pid); every.add(pid)
        for inner in instr.get('innerInstructions', []) or []:
            ipid = inner.get('programId') or inner.get('program_id')
            if ipid: every.add(ipid)
    return top, every


def _tx_references_mint(tx, mint):
    if not mint: return False
    for tt in tx.get('tokenTransfers', []) or []:
        if (tt.get('mint', '') or '') == mint: return True
    for instr in tx.get('instructions', []) or []:
        if mint in (instr.get('accounts', []) or []): return True
        for inner in instr.get('innerInstructions', []) or []:
            if mint in (inner.get('accounts', []) or []): return True
    return False


def _identify_keeper_program(tx):
    top_ids, _ = _program_ids(tx)
    matches = top_ids & KEEPER_PROGRAMS
    return next(iter(matches)) if matches else None


def _identify_lp_program(tx):
    top_ids, _ = _program_ids(tx)
    matches = top_ids & LP_PROGRAMS
    return next(iter(matches)) if matches else None


def _has_jupiter_aggregator(tx):
    _, every = _program_ids(tx)
    return JUP_AGGREGATOR_V6 in every


def _scan_tx_max_usdc_route(tx):
    max_amt = 0.0
    for tt in tx.get('tokenTransfers', []) or []:
        mint = tt.get('mint', '') or ''
        if mint not in (USDC_MINT, USDT_MINT): continue
        amt = float(tt.get('tokenAmount', 0) or 0)
        if amt > max_amt: max_amt = amt
    return max_amt


def _compute_balance_deltas(tx, target_mint, our_wallets):
    target_delta = 0.0
    sol_delta = 0.0
    quote_deltas = {m: 0.0 for m in QUOTE_MINTS}
    for ad in tx.get('accountData', []) or []:
        if ad.get('account') in our_wallets:
            sol_delta += (ad.get('nativeBalanceChange', 0) or 0) / 1e9
        for tbc in ad.get('tokenBalanceChanges', []) or []:
            if tbc.get('userAccount') not in our_wallets: continue
            mint = tbc.get('mint', '')
            raw = tbc.get('rawTokenAmount', {}) or {}
            try:
                dec = int(raw.get('decimals', 0))
                amt = float(raw.get('tokenAmount', '0')) / (10 ** dec) if dec else float(raw.get('tokenAmount', '0'))
            except (TypeError, ValueError): continue
            if mint == target_mint: target_delta += amt
            elif mint == SOL_MINT: sol_delta += amt
            elif mint in quote_deltas: quote_deltas[mint] += amt
    if tx.get('feePayer') in our_wallets:
        sol_delta += (tx.get('fee', 0) or 0) / 1e9
    return target_delta, sol_delta, quote_deltas


def _native_outflow_to_others(tx, our_wallets):
    total = 0.0
    for nt in tx.get('nativeTransfers', []) or []:
        from_addr = nt.get('fromUserAccount', '') or ''
        to_addr   = nt.get('toUserAccount', '') or ''
        amt = (nt.get('amount', 0) or 0) / 1e9
        if from_addr in our_wallets and to_addr not in our_wallets and amt > 0: total += amt
    return total


def _stable_outflow_to_others(tx, our_wallets):
    total = 0.0
    for tt in tx.get('tokenTransfers', []) or []:
        mint = tt.get('mint', '') or ''
        if mint not in (USDC_MINT, USDT_MINT): continue
        from_addr = tt.get('fromUserAccount', '') or ''
        to_addr   = tt.get('toUserAccount', '') or ''
        amt = float(tt.get('tokenAmount', 0) or 0)
        if from_addr in our_wallets and to_addr not in our_wallets and amt > 0: total += amt
    return total


def _find_receiving_wallet(tx, target_mint, our_wallets):
    for ad in tx.get('accountData', []) or []:
        for tbc in ad.get('tokenBalanceChanges', []) or []:
            if tbc.get('mint') != target_mint: continue
            if tbc.get('userAccount') not in our_wallets: continue
            raw = tbc.get('rawTokenAmount', {}) or {}
            try:
                dec = int(raw.get('decimals', 0))
                amt = float(raw.get('tokenAmount', '0')) / (10 ** dec) if dec else float(raw.get('tokenAmount', '0'))
                if amt > 0: return tbc.get('userAccount')
            except (TypeError, ValueError): pass
    return None


def _pick_quote(sol_delta, quote_deltas):
    candidates = []
    if abs(sol_delta) > SOL_QUOTE_THRESHOLD: candidates.append((sol_delta, 'SOL'))
    for m, d in quote_deltas.items():
        if abs(d) > USDC_QUOTE_THRESHOLD: candidates.append((d, QUOTE_INFO[m][0]))
    if not candidates: return 0.0, ''
    candidates.sort(key=lambda x: abs(x[0]), reverse=True)
    return candidates[0]


def parse_tx(tx, target_mint, our_wallets, airdrop_programs):
    target_delta, sol_delta, quote_deltas = _compute_balance_deltas(tx, target_mint, our_wallets)
    top_ids, _ = _program_ids(tx)
    keeper_pid = _identify_keeper_program(tx)
    lp_pid     = _identify_lp_program(tx)
    is_keeper  = keeper_pid is not None
    is_lp      = lp_pid is not None
    is_airdrop = bool(top_ids & airdrop_programs)
    fee_payer  = tx.get('feePayer', '') or ''
    user_signed = fee_payer in our_wallets

    is_refund = False
    if is_keeper and not _tx_references_mint(tx, target_mint):
        usdc_in_amt = (quote_deltas.get(USDC_MINT, 0.0) + quote_deltas.get(USDT_MINT, 0.0))
        sol_in_amt  = sol_delta
        looks_like_refund = (
            user_signed and abs(target_delta) < 1e-9
            and (usdc_in_amt > REFUND_MIN_USDC or sol_in_amt > REFUND_MIN_SOL)
        )
        if not looks_like_refund: return None
        is_refund = True

    if abs(target_delta) < 1e-9 and not (is_keeper or is_lp or is_airdrop):
        return None

    base = {
        'signature': tx.get('signature', ''), 'timestamp': tx.get('timestamp', 0),
        'source':    tx.get('source', '') or 'UNKNOWN',
        'token_amount': abs(target_delta), 'token_delta': target_delta,
        'quote_amount': 0.0, 'quote_symbol': '', 'price_per_token': 0.0,
    }

    if is_airdrop:
        base['type']   = 'airdrop'
        base['source'] = base['source'] + ' (airdrop — excluded from cost)'
        base['wallet'] = _find_receiving_wallet(tx, target_mint, our_wallets)
        return base

    if is_keeper:
        keeper_label = KEEPER_NAMES.get(keeper_pid, 'Keeper')
        suffix = f' ({keeper_label} cancellation refund)' if is_refund else f' ({keeper_label} fill)'
        base['type'] = 'dca_tx'
        base['source'] = base['source'] + suffix
        base['sol_delta']  = sol_delta
        base['usdc_delta'] = quote_deltas.get(USDC_MINT, 0.0)
        base['usdt_delta'] = quote_deltas.get(USDT_MINT, 0.0)
        base['keeper_program'] = keeper_label
        base['is_refund']  = is_refund
        return base

    if is_lp:
        lp_label = LP_NAMES.get(lp_pid, 'LP')
        base['type']   = 'lp_op'
        base['source'] = base['source'] + f' ({lp_label})'
        base['sol_delta']  = sol_delta
        base['usdc_delta'] = quote_deltas.get(USDC_MINT, 0.0)
        base['usdt_delta'] = quote_deltas.get(USDT_MINT, 0.0)
        base['lp_program'] = lp_label
        return base

    if (target_delta > 0 and not user_signed and _has_jupiter_aggregator(tx)):
        base['type']     = 'dca_tx'
        base['source']   = base['source'] + ' (External keeper fill — bot signed)'
        base['sol_delta']  = sol_delta
        base['usdc_delta'] = quote_deltas.get(USDC_MINT, 0.0)
        base['usdt_delta'] = quote_deltas.get(USDT_MINT, 0.0)
        base['keeper_program'] = 'Bot keeper (non-user signed)'
        return base

    quote_signed, quote_sym = _pick_quote(sol_delta, quote_deltas)
    quote_amount = abs(quote_signed)

    if target_delta > 0:
        if quote_signed < 0 and quote_amount > 0:
            base.update({'type': 'buy', 'quote_amount': quote_amount,
                         'quote_symbol': quote_sym,
                         'price_per_token': quote_amount / target_delta})
        else:
            if user_signed and _has_jupiter_aggregator(tx):
                route_usdc = _scan_tx_max_usdc_route(tx)
                if route_usdc >= ROUTE_USDC_MIN:
                    base.update({
                        'type': 'buy', 'quote_amount': route_usdc, 'quote_symbol': 'USDC',
                        'price_per_token': route_usdc / target_delta,
                        'source': base['source'] + ' (non-stable input — USDC route inferred)',
                    })
                    return base
            base['type'] = 'unpriced_in'
    else:
        if quote_signed > 0 and quote_amount > 0:
            base.update({'type': 'sell', 'quote_amount': quote_amount,
                         'quote_symbol': quote_sym,
                         'price_per_token': quote_amount / abs(target_delta)})
        else:
            base['type'] = 'transfer_out'
    return base


def _find_keeper_setups(transactions, target_mint, our_wallets, keeper_fill_events, processed_sigs):
    if not keeper_fill_events: return []
    fill_times = [e['timestamp'] for e in keeper_fill_events]
    earliest = min(fill_times) - DCA_SETUP_LOOKBACK_DAYS  * 86400
    latest   = max(fill_times) + DCA_SETUP_LOOKAHEAD_DAYS * 86400

    setups = []
    for tx in transactions:
        sig = tx.get('signature', '')
        if not sig or sig in processed_sigs: continue
        ts = tx.get('timestamp', 0)
        if ts < earliest or ts > latest: continue
        if (tx.get('feePayer') or '') not in our_wallets: continue
        keeper_pid = _identify_keeper_program(tx)
        if keeper_pid is None: continue
        target_delta, sol_delta, quote_deltas = _compute_balance_deltas(tx, target_mint, our_wallets)
        if abs(target_delta) > 1e-9: continue
        usdc_d = quote_deltas.get(USDC_MINT, 0.0) + quote_deltas.get(USDT_MINT, 0.0)
        usdc_out = -usdc_d if usdc_d < 0 else 0.0
        sol_out  = -sol_delta if sol_delta < 0 else 0.0
        if usdc_out < DCA_SETUP_MIN_USDC and sol_out < DCA_SETUP_MIN_SOL: continue
        keeper_label = KEEPER_NAMES.get(keeper_pid, 'Keeper')
        setups.append({
            'signature': sig, 'timestamp': ts,
            'source': (tx.get('source') or 'UNKNOWN') + f' ({keeper_label} setup deposit)',
            'token_amount': 0.0, 'token_delta': 0.0, 'quote_amount': 0.0,
            'quote_symbol': '', 'price_per_token': 0.0, 'type': 'dca_tx',
            'sol_delta': sol_delta,
            'usdc_delta': quote_deltas.get(USDC_MINT, 0.0),
            'usdt_delta': quote_deltas.get(USDT_MINT, 0.0),
            'keeper_program': keeper_label,
        })
    return setups


def _cancel_self_transfers(events,
                           window_hours=SELF_TRANSFER_WINDOW_HOURS,
                           tolerance_pct=SELF_TRANSFER_TOL_PCT):
    outs = [(i, e) for i, e in enumerate(events) if e['type'] == 'transfer_out']
    ins  = [(i, e) for i, e in enumerate(events) if e['type'] == 'unpriced_in']
    cancelled = set()
    window_sec = window_hours * 3600
    for out_idx, out in outs:
        if out_idx in cancelled: continue
        out_amt = out['token_amount']
        out_t   = out['timestamp']
        if out_amt < 0.001: continue
        best_idx = None; best_dt = None
        for in_idx, inn in ins:
            if in_idx in cancelled: continue
            dt = abs(inn['timestamp'] - out_t)
            if dt > window_sec: continue
            in_amt = inn['token_amount']
            if abs(in_amt - out_amt) / max(out_amt, 0.001) > tolerance_pct / 100: continue
            if best_dt is None or dt < best_dt: best_dt = dt; best_idx = in_idx
        if best_idx is not None:
            cancelled.add(out_idx); cancelled.add(best_idx)
    return [e for i, e in enumerate(events) if i not in cancelled]


def analyze_token_trades(transactions, target_mint, wallet_addresses, airdrop_programs):
    wallet_set = {w.strip() for w in wallet_addresses if w.strip()}
    seen, events = set(), []
    refund_count = 0
    for tx in transactions:
        sig = tx.get('signature', '')
        if not sig or sig in seen: continue
        seen.add(sig)
        ev = parse_tx(tx, target_mint, wallet_set, airdrop_programs)
        if ev:
            events.append(ev)
            if ev.get('is_refund'): refund_count += 1
    if refund_count: print(f'[refunds] captured {refund_count} cancellation refund txs')

    keeper_fills = [e for e in events if e['type'] == 'dca_tx' and (e.get('token_delta', 0) or 0) > 0]
    setup_events = _find_keeper_setups(transactions, target_mint, wallet_set, keeper_fills, processed_sigs=seen)
    if setup_events: events.extend(setup_events)

    events.sort(key=lambda e: e['timestamp'])
    events = _cancel_self_transfers(events)
    by_type = {}
    for e in events: by_type[e['type']] = by_type.get(e['type'], 0) + 1
    print(f'\n=== PARSED {len(events)} EVENTS — {by_type} ===')
    return events


# =========================================================================
# v3.12: Limit-order pairing
# =========================================================================
def analyze_limit_orders(transactions, target_mint, our_wallets, sol_price_usd):
    """Match Jupiter Limit Order V1/V2 setups with their fills via shared
    Reserve token accounts. Each order = one setup + N fills + 0..1 refunds.

    Returns:
      buy_orders: list of priced buy orders
      sell_orders: list of priced sell orders

    Pairing logic:
      - Buy order setup: USDC moves from user's USDC ATA → Reserve_USDC.
        We tag Reserve_USDC as a "buy reserve" for this setup.
      - Buy order fill: USDC drains FROM Reserve_USDC; user receives target token.
        Sum fills per Reserve_USDC.
      - Buy order cancellation: USDC drains FROM Reserve_USDC back to user's USDC ATA.

      - Sell order setup: target token moves from user's target ATA → Reserve_target.
      - Sell order fill: target drains from Reserve_target; user receives USDC.
      - Sell order cancellation: target drains from Reserve_target back to user.
    """
    buy_setups   = {}  # reserve_token_acct → setup info
    sell_setups  = {}  # reserve_token_acct → setup info
    user_usdc_atas = set()
    user_target_atas = set()

    # Helper: get user's token accounts (ATAs) so we can recognize "back to user"
    for tx in transactions:
        for tt in tx.get('tokenTransfers', []) or []:
            mint = tt.get('mint', '') or ''
            from_user = tt.get('fromUserAccount', '') or ''
            to_user   = tt.get('toUserAccount', '') or ''
            from_acct = tt.get('fromTokenAccount', '') or ''
            to_acct   = tt.get('toTokenAccount', '') or ''
            if mint == USDC_MINT or mint == USDT_MINT:
                if from_user in our_wallets and from_acct: user_usdc_atas.add(from_acct)
                if to_user   in our_wallets and to_acct:   user_usdc_atas.add(to_acct)
            if mint == target_mint:
                if from_user in our_wallets and from_acct: user_target_atas.add(from_acct)
                if to_user   in our_wallets and to_acct:   user_target_atas.add(to_acct)

    print(f'[limit-orders] discovered {len(user_usdc_atas)} user USDC ATAs, {len(user_target_atas)} user target ATAs')

    # Pass 1: identify setup txs (user-signed, keeper-program, reserve-bound transfer)
    for tx in transactions:
        keeper_pid = _identify_keeper_program(tx)
        if keeper_pid is None: continue
        if (tx.get('feePayer') or '') not in our_wallets: continue
        td, sd, qd = _compute_balance_deltas(tx, target_mint, our_wallets)
        if abs(td) > 1e-9 and not (qd.get(USDC_MINT, 0) + qd.get(USDT_MINT, 0) < 0):
            # Has both target movement + USDC out — that's a fill, not a setup.
            # But for a SELL setup, target leaves user. We need to check.
            pass

        ud = qd.get(USDC_MINT, 0) + qd.get(USDT_MINT, 0)
        keeper_label = KEEPER_NAMES.get(keeper_pid, 'Keeper')
        sig = tx.get('signature', '')
        ts  = tx.get('timestamp', 0)

        # BUY setup: target_delta == 0, USDC out
        if abs(td) < 1e-9 and ud < -0.5:
            for tt in tx.get('tokenTransfers', []) or []:
                mint = tt.get('mint', '') or ''
                if mint not in (USDC_MINT, USDT_MINT): continue
                from_user = tt.get('fromUserAccount', '') or ''
                if from_user not in our_wallets: continue
                amt = float(tt.get('tokenAmount', 0) or 0)
                if amt < DCA_SETUP_MIN_USDC: continue
                to_acct = tt.get('toTokenAccount', '') or ''
                if to_acct and to_acct not in user_usdc_atas:
                    buy_setups[to_acct] = {
                        'sig': sig, 'ts': ts,
                        'usdc_deposited': amt, 'reserve': to_acct,
                        'keeper': keeper_label, 'wallet': from_user,
                    }
                    break

        # SELL setup: target_delta < 0, USDC unchanged
        elif td < -0.5 and abs(ud) < 0.5:
            for tt in tx.get('tokenTransfers', []) or []:
                mint = tt.get('mint', '') or ''
                if mint != target_mint: continue
                from_user = tt.get('fromUserAccount', '') or ''
                if from_user not in our_wallets: continue
                amt = float(tt.get('tokenAmount', 0) or 0)
                if amt < 0.001: continue
                to_acct = tt.get('toTokenAccount', '') or ''
                if to_acct and to_acct not in user_target_atas:
                    sell_setups[to_acct] = {
                        'sig': sig, 'ts': ts,
                        'target_deposited': amt, 'reserve': to_acct,
                        'keeper': keeper_label, 'wallet': from_user,
                    }
                    break

    print(f'[limit-orders] found {len(buy_setups)} buy setups, {len(sell_setups)} sell setups')

    # Pass 2: find fills + refunds matching each setup's Reserve
    buy_orders = {r: {'setup': s, 'fills': [], 'cancellations': []} for r, s in buy_setups.items()}
    sell_orders = {r: {'setup': s, 'fills': [], 'cancellations': []} for r, s in sell_setups.items()}

    for tx in transactions:
        keeper_pid = _identify_keeper_program(tx)
        if keeper_pid is None: continue
        sig = tx.get('signature', '')
        ts  = tx.get('timestamp', 0)
        td, sd, qd = _compute_balance_deltas(tx, target_mint, our_wallets)
        ud = qd.get(USDC_MINT, 0) + qd.get(USDT_MINT, 0)

        for tt in tx.get('tokenTransfers', []) or []:
            mint = tt.get('mint', '') or ''
            from_acct = tt.get('fromTokenAccount', '') or ''
            to_acct   = tt.get('toTokenAccount', '') or ''
            amt = float(tt.get('tokenAmount', 0) or 0)
            if amt <= 0: continue

            # BUY ORDER FILL/CANCEL: USDC drains from a known Reserve_USDC
            if mint in (USDC_MINT, USDT_MINT) and from_acct in buy_orders:
                if to_acct in user_usdc_atas:
                    # Refund: USDC came back to user
                    buy_orders[from_acct]['cancellations'].append({
                        'sig': sig, 'ts': ts, 'usdc_refunded': amt,
                    })
                else:
                    # Fill: USDC went to a swap. The user should be receiving target
                    # in the same tx. Use td > 0 as the matched amount.
                    if td > 0:
                        buy_orders[from_acct]['fills'].append({
                            'sig': sig, 'ts': ts,
                            'usdc_drained': amt, 'tokens_received': td,
                        })

            # SELL ORDER FILL/CANCEL: target drains from a known Reserve_target
            if mint == target_mint and from_acct in sell_orders:
                if to_acct in user_target_atas:
                    # Refund: tokens came back
                    sell_orders[from_acct]['cancellations'].append({
                        'sig': sig, 'ts': ts, 'tokens_refunded': amt,
                    })
                else:
                    # Fill: tokens went to taker. User should receive USDC.
                    if ud > 0:
                        sell_orders[from_acct]['fills'].append({
                            'sig': sig, 'ts': ts,
                            'tokens_drained': amt, 'usdc_received': ud,
                        })

    # Pass 3: compute per-order summaries
    buy_summaries = []
    for r, data in buy_orders.items():
        s = data['setup']
        fills = data['fills']
        cans = data['cancellations']
        total_tokens = sum(f['tokens_received'] for f in fills)
        total_usdc_filled = sum(f['usdc_drained'] for f in fills)
        total_refunded    = sum(c['usdc_refunded'] for c in cans)
        net_cost = max(s['usdc_deposited'] - total_refunded, 0)
        # If we have fills and the math doesn't balance, prefer the fill-derived figure
        effective_cost = total_usdc_filled if total_tokens > 0 else net_cost
        avg_price = effective_cost / total_tokens if total_tokens > 0 else 0
        buy_summaries.append({
            'setup_sig': s['sig'], 'setup_ts': s['ts'],
            'reserve': r, 'wallet': s['wallet'], 'keeper': s['keeper'],
            'usdc_deposited': s['usdc_deposited'],
            'usdc_refunded':  total_refunded,
            'usdc_net_cost':  net_cost,
            'usdc_actually_spent': effective_cost,
            'tokens_received': total_tokens,
            'fill_count': len(fills),
            'cancellation_count': len(cans),
            'avg_fill_price': avg_price,
            'side': 'buy',
        })

    sell_summaries = []
    for r, data in sell_orders.items():
        s = data['setup']
        fills = data['fills']
        cans = data['cancellations']
        total_tokens_sold = sum(f['tokens_drained'] for f in fills)
        total_usdc_received = sum(f['usdc_received'] for f in fills)
        total_refunded_tokens = sum(c['tokens_refunded'] for c in cans)
        net_tokens_sold = max(s['target_deposited'] - total_refunded_tokens, 0)
        avg_price = total_usdc_received / total_tokens_sold if total_tokens_sold > 0 else 0
        sell_summaries.append({
            'setup_sig': s['sig'], 'setup_ts': s['ts'],
            'reserve': r, 'wallet': s['wallet'], 'keeper': s['keeper'],
            'tokens_deposited': s['target_deposited'],
            'tokens_refunded':  total_refunded_tokens,
            'tokens_net_sold':  net_tokens_sold,
            'tokens_actually_sold': total_tokens_sold,
            'usdc_received':   total_usdc_received,
            'fill_count':      len(fills),
            'cancellation_count': len(cans),
            'avg_fill_price':  avg_price,
            'side': 'sell',
        })

    # Diagnostic print
    print(f'\n[limit-orders] BUY ORDERS ({len(buy_summaries)}):')
    for o in sorted(buy_summaries, key=lambda x: x['setup_ts']):
        ts_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(o['setup_ts']))
        if o['avg_fill_price'] > 0:
            print(f'  {ts_str}  ${o["usdc_deposited"]:>10,.2f} dep, ${o["usdc_refunded"]:>10,.2f} ref → '
                  f'{o["tokens_received"]:>12,.0f} tokens @ ${o["avg_fill_price"]:.6f}/tok '
                  f'({o["fill_count"]} fills, {o["cancellation_count"]} refunds)')
        else:
            print(f'  {ts_str}  ${o["usdc_deposited"]:>10,.2f} dep, ${o["usdc_refunded"]:>10,.2f} ref → '
                  f'no fills detected ({o["cancellation_count"]} refunds)')

    if sell_summaries:
        print(f'\n[limit-orders] SELL ORDERS ({len(sell_summaries)}):')
        for o in sorted(sell_summaries, key=lambda x: x['setup_ts']):
            ts_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(o['setup_ts']))
            if o['avg_fill_price'] > 0:
                print(f'  {ts_str}  {o["tokens_deposited"]:>12,.0f} tok dep, {o["tokens_refunded"]:>12,.0f} ref → '
                      f'${o["usdc_received"]:>10,.2f} @ ${o["avg_fill_price"]:.6f}/tok '
                      f'({o["fill_count"]} fills, {o["cancellation_count"]} refunds)')
            else:
                print(f'  {ts_str}  {o["tokens_deposited"]:>12,.0f} tok dep, {o["tokens_refunded"]:>12,.0f} ref → '
                      f'no fills detected ({o["cancellation_count"]} refunds)')

    return buy_summaries, sell_summaries


# =========================================================================
# v3.11/v3.12: LP breakdown
# =========================================================================
def analyze_lp_activity(events, sol_price_usd, current_token_price_usd):
    lp_events = [e for e in events if e['type'] == 'lp_op']
    if not lp_events: return None
    target_in  = sum(e['token_delta'] for e in lp_events if e['token_delta'] > 0)
    target_out = -sum(e['token_delta'] for e in lp_events if e['token_delta'] < 0)
    usdc_in    = sum((e.get('usdc_delta', 0) + e.get('usdt_delta', 0)) for e in lp_events
                     if (e.get('usdc_delta', 0) + e.get('usdt_delta', 0)) > 0)
    usdc_out   = sum(-(e.get('usdc_delta', 0) + e.get('usdt_delta', 0)) for e in lp_events
                     if (e.get('usdc_delta', 0) + e.get('usdt_delta', 0)) < 0)
    sol_in     = sum(e.get('sol_delta', 0) for e in lp_events if e.get('sol_delta', 0) > 0)
    sol_out    = sum(-e.get('sol_delta', 0) for e in lp_events if e.get('sol_delta', 0) < 0)
    deposit_value_usd  = target_out * current_token_price_usd + usdc_out + sol_out * sol_price_usd
    withdraw_value_usd = target_in  * current_token_price_usd + usdc_in  + sol_in  * sol_price_usd
    pnl_usd = withdraw_value_usd - deposit_value_usd
    by_program = Counter(e.get('lp_program', 'LP') for e in lp_events)
    print(f'[lp] {len(lp_events)} events: target net={target_in - target_out:+,.2f}, usdc net=${usdc_in - usdc_out:+,.2f}, P/L=${pnl_usd:+,.2f}')
    return {
        'count': len(lp_events), 'by_program': dict(by_program),
        'target_in': target_in, 'target_out': target_out, 'target_net': target_in - target_out,
        'usdc_in': usdc_in, 'usdc_out': usdc_out, 'usdc_net': usdc_in - usdc_out,
        'sol_in': sol_in, 'sol_out': sol_out, 'sol_net': sol_in - sol_out,
        'deposit_value_usd': deposit_value_usd, 'withdraw_value_usd': withdraw_value_usd,
        'lp_pnl_usd': pnl_usd,
    }


# =========================================================================
# v3.12: Best/worst surfacing — now uses paired limit orders for keeper insights
# =========================================================================
def surface_best_worst_events(events, dca_aggregate, limit_buy_orders, limit_sell_orders, top_n=15):
    out = {'reg_buys_best': [], 'reg_buys_worst': [],
           'reg_sells_best': [], 'reg_sells_worst': [],
           'keeper_buys_best': [], 'keeper_buys_worst': [],
           'keeper_sells_best': [], 'keeper_sells_worst': []}

    def _entry(e, side):
        return {
            'signature': e['signature'], 'timestamp': e['timestamp'],
            'token_amount': e.get('token_amount', 0),
            'quote_amount': e.get('quote_amount_q', e.get('quote_amount', 0)),
            'price_per_token': e.get('price_per_token_q', e.get('price_per_token', 0)),
            'source': e.get('source', ''), 'side': side,
        }

    # Regular buys/sells
    reg_buys = [e for e in events if e['type'] == 'buy' and (e.get('price_per_token_q') or 0) > 0]
    reg_sells = [e for e in events if e['type'] == 'sell' and (e.get('price_per_token_q') or 0) > 0]

    out['reg_buys_best']   = [_entry(e, 'buy')  for e in sorted(reg_buys,  key=lambda x: x['price_per_token_q'])[:top_n]]
    out['reg_buys_worst']  = [_entry(e, 'buy')  for e in sorted(reg_buys,  key=lambda x: -x['price_per_token_q'])[:top_n]]
    out['reg_sells_best']  = [_entry(e, 'sell') for e in sorted(reg_sells, key=lambda x: -x['price_per_token_q'])[:top_n]]
    out['reg_sells_worst'] = [_entry(e, 'sell') for e in sorted(reg_sells, key=lambda x: x['price_per_token_q'])[:top_n]]

    # Keeper buys/sells — now from paired limit orders
    def _korder_buy_entry(o):
        return {
            'signature': o['setup_sig'], 'timestamp': o['setup_ts'],
            'token_amount': o['tokens_received'],
            'quote_amount': o['usdc_actually_spent'],
            'price_per_token': o['avg_fill_price'],
            'source': f"{o['keeper']} order  ·  {o['fill_count']} fills"
                      + (f", {o['cancellation_count']} refunds" if o['cancellation_count'] else ''),
            'side': 'buy',
            # Extras for richer UI
            'usdc_deposited': o['usdc_deposited'],
            'usdc_refunded':  o['usdc_refunded'],
            'fill_count':     o['fill_count'],
            'cancellation_count': o['cancellation_count'],
        }

    def _korder_sell_entry(o):
        return {
            'signature': o['setup_sig'], 'timestamp': o['setup_ts'],
            'token_amount': o['tokens_actually_sold'],
            'quote_amount': o['usdc_received'],
            'price_per_token': o['avg_fill_price'],
            'source': f"{o['keeper']} order  ·  {o['fill_count']} fills"
                      + (f", {o['cancellation_count']} refunds" if o['cancellation_count'] else ''),
            'side': 'sell',
            'tokens_deposited':   o['tokens_deposited'],
            'tokens_refunded':    o['tokens_refunded'],
            'fill_count':         o['fill_count'],
            'cancellation_count': o['cancellation_count'],
        }

    priced_buy_orders  = [o for o in limit_buy_orders  if o['avg_fill_price'] > 0 and o['tokens_received'] > 0]
    priced_sell_orders = [o for o in limit_sell_orders if o['avg_fill_price'] > 0 and o['tokens_actually_sold'] > 0]

    out['keeper_buys_best']   = [_korder_buy_entry(o) for o in sorted(priced_buy_orders,  key=lambda x: x['avg_fill_price'])[:top_n]]
    out['keeper_buys_worst']  = [_korder_buy_entry(o) for o in sorted(priced_buy_orders,  key=lambda x: -x['avg_fill_price'])[:top_n]]
    out['keeper_sells_best']  = [_korder_sell_entry(o) for o in sorted(priced_sell_orders, key=lambda x: -x['avg_fill_price'])[:top_n]]
    out['keeper_sells_worst'] = [_korder_sell_entry(o) for o in sorted(priced_sell_orders, key=lambda x: x['avg_fill_price'])[:top_n]]

    out['_meta'] = {
        'reg_buys_total':    len(reg_buys),
        'reg_sells_total':   len(reg_sells),
        'keeper_buys_priced':  len(priced_buy_orders),
        'keeper_sells_priced': len(priced_sell_orders),
        'keeper_buys_total_orders':  len(limit_buy_orders),
        'keeper_sells_total_orders': len(limit_sell_orders),
    }
    return out


# =========================================================================
# Funding detector
# =========================================================================
def detect_funding_txs(transactions, target_mint, our_wallets, sol_price_usd):
    funding_events = []
    for tx in transactions:
        sig = tx.get('signature', '')
        fee_payer = tx.get('feePayer', '') or ''
        if fee_payer not in our_wallets: continue
        top_ids, _ = _program_ids(tx)
        if not top_ids or not top_ids.issubset(INFRA_PROGRAMS): continue
        instrs = tx.get('instructions', []) or []
        if len(instrs) < FUND_MIN_INSTRS: continue
        has_target = any((tt.get('mint', '') or '') == target_mint for tt in (tx.get('tokenTransfers', []) or []))
        if has_target: continue
        sol_out  = _native_outflow_to_others(tx, our_wallets)
        usdc_out = _stable_outflow_to_others(tx, our_wallets)
        if sol_out < FUND_MIN_SOL and usdc_out < FUND_MIN_USDC: continue
        funding_usd = sol_out * sol_price_usd + usdc_out
        funding_events.append({
            'signature': sig, 'timestamp': tx.get('timestamp', 0),
            'wallet': fee_payer, 'sol_out': sol_out, 'usdc_out': usdc_out,
            'funding_usd': funding_usd, 'instr_count': len(instrs),
        })
    funding_events.sort(key=lambda e: e['timestamp'])
    print(f'[funding] {len(funding_events)} funding txs, total ${sum(e["funding_usd"] for e in funding_events):.2f}')
    return funding_events


# =========================================================================
# Summary
# =========================================================================
def _normalize_to_quote(amount, from_symbol, display_quote, sol_price_usd):
    if amount == 0 or not from_symbol: return 0.0
    if display_quote in ('USD', 'USDC'):
        if from_symbol in ('USDC', 'USDT'): return amount
        if from_symbol == 'SOL': return amount * sol_price_usd
        return 0.0
    if display_quote == 'SOL':
        if from_symbol == 'SOL': return amount
        if from_symbol in ('USDC', 'USDT') and sol_price_usd > 0: return amount / sol_price_usd
        return 0.0
    return 0.0


def normalize_trade_prices(trades, display_quote, sol_price_usd):
    for t in trades:
        if t.get('quote_amount', 0) > 0 and t.get('quote_symbol'):
            qa_q = _normalize_to_quote(t['quote_amount'], t['quote_symbol'], display_quote, sol_price_usd)
            t['quote_amount_q']    = qa_q
            t['price_per_token_q'] = qa_q / t['token_amount'] if t['token_amount'] > 0 else 0
        else:
            t['quote_amount_q']    = 0
            t['price_per_token_q'] = 0
    return trades


def build_position_breakdown(wallet_tokens, limit_orders, limit_err,
                             dlmm_positions, dlmm_err, current_price_usd):
    """Build the position_breakdown object for the summary.

    All token counts are in human units. Value is computed at current market price.
    Mutates each order/position to attach a `value_usd` field for frontend convenience.
    """
    for o in limit_orders:
        o['value_usd'] = o['tokens_remaining'] * current_price_usd
    for p in dlmm_positions:
        p['value_usd'] = p['tokens'] * current_price_usd
    limit_tokens   = sum(o['tokens_remaining'] for o in limit_orders)
    dlmm_tokens    = sum(p['tokens'] for p in dlmm_positions)
    total_tokens   = wallet_tokens + limit_tokens + dlmm_tokens
    pending_proceeds = sum(o['expected_proceeds_usdc'] for o in limit_orders)
    return {
        'wallet': {
            'tokens': wallet_tokens,
            'value_usd': wallet_tokens * current_price_usd,
        },
        'limit_orders': {
            'tokens': limit_tokens,
            'value_usd': limit_tokens * current_price_usd,
            'pending_proceeds_usd': pending_proceeds,
            'orders': limit_orders,
            'error': limit_err,
        },
        'dlmm': {
            'tokens': dlmm_tokens,
            'value_usd': dlmm_tokens * current_price_usd,
            'positions': dlmm_positions,
            'error': dlmm_err,
        },
        'total_tokens':    total_tokens,
        'total_value_usd': total_tokens * current_price_usd,
    }


def calculate_summary(trades, dca_aggregate, on_chain_balance,
                      current_price_usd, sol_price_usd,
                      auto_funding_usd, display_quote='USDC',
                      manual_dca_cost=0.0, manual_airdrop_tokens=0.0,
                      position_breakdown=None):
    regular  = [t for t in trades if t['type'] in ('buy', 'sell', 'unpriced_in', 'transfer_out')]
    dca_txs  = [t for t in trades if t['type'] == 'dca_tx']
    lp_ops   = [t for t in trades if t['type'] == 'lp_op']
    airdrops = [t for t in trades if t['type'] == 'airdrop']

    total_bought_reg = total_buy_cost_reg = 0.0
    total_sold_reg   = total_sell_revenue_reg = 0.0
    total_unpriced_in = total_transfer_out = 0.0
    for t in regular:
        norm = _normalize_to_quote(t['quote_amount'], t['quote_symbol'], display_quote, sol_price_usd)
        if t['type'] == 'buy':
            total_bought_reg += t['token_amount']; total_buy_cost_reg += norm
        elif t['type'] == 'sell':
            total_sold_reg += t['token_amount']; total_sell_revenue_reg += norm
        elif t['type'] == 'unpriced_in':
            total_unpriced_in += t['token_amount']
        elif t['type'] == 'transfer_out':
            total_transfer_out += t['token_amount']

    dca_buy_tokens   = dca_aggregate['buy_target_tokens']
    dca_buy_cost_q   = _normalize_to_quote(dca_aggregate['buy_cost_usd'],     'USDC', display_quote, sol_price_usd)
    dca_sell_tokens  = dca_aggregate['sell_target_tokens']
    dca_sell_rev_q   = _normalize_to_quote(dca_aggregate['sell_revenue_usd'], 'USDC', display_quote, sol_price_usd)
    dca_net_delta     = sum(t['token_delta'] for t in dca_txs)
    lp_net_delta      = sum(t['token_delta'] for t in lp_ops)
    airdrop_net_delta = sum(t['token_delta'] for t in airdrops)

    auto_airdrop_tokens   = sum(t['token_amount'] for t in airdrops if t['token_delta'] > 0)
    manual_airdrop_tokens = max(0.0, min(manual_airdrop_tokens, total_unpriced_in))
    priceable_unpriced = max(total_unpriced_in - manual_airdrop_tokens, 0.0)

    auto_funding_q = _normalize_to_quote(auto_funding_usd, 'USDC', display_quote, sol_price_usd)
    if manual_dca_cost > 0: extra_cost = manual_dca_cost; cost_source = 'manual'
    else: extra_cost = auto_funding_q; cost_source = 'auto-detected'
    extra_tokens_priced = priceable_unpriced if (extra_cost > 0 and priceable_unpriced > 0) else 0.0

    spread_tokens = total_bought_reg + dca_buy_tokens + extra_tokens_priced
    spread_cost   = total_buy_cost_reg + dca_buy_cost_q + extra_cost
    spread_avg    = (spread_cost / spread_tokens) if spread_tokens > 0 else 0
    strict_tokens = total_bought_reg + dca_buy_tokens
    strict_cost   = total_buy_cost_reg + dca_buy_cost_q
    strict_avg    = (strict_cost / strict_tokens) if strict_tokens > 0 else 0
    total_sold = total_sold_reg + dca_sell_tokens
    total_sell_revenue = total_sell_revenue_reg + dca_sell_rev_q
    avg_sell_price = (total_sell_revenue / total_sold) if total_sold > 0 else 0

    computed_holdings = (
        total_bought_reg + total_unpriced_in - total_sold_reg - total_transfer_out
        + dca_net_delta + lp_net_delta + airdrop_net_delta
    )
    diff = reconciled = None
    if on_chain_balance is not None:
        diff = on_chain_balance - computed_holdings
        tolerance = max(abs(on_chain_balance) * 0.005, 0.001)
        reconciled = abs(diff) <= tolerance
    # Wallet-only base used for reconciliation banner
    wallet_only_holdings = on_chain_balance if on_chain_balance is not None else computed_holdings
    # Total holdings includes off-wallet positions (limit orders, DLMM)
    if position_breakdown is not None:
        holdings = position_breakdown['total_tokens']
    else:
        holdings = wallet_only_holdings

    if display_quote == 'SOL':
        current_token_price = (current_price_usd / sol_price_usd) if sol_price_usd > 0 else 0
    else:
        current_token_price = current_price_usd
    current_value = holdings * current_token_price

    total_invested    = spread_cost
    realized_proceeds = total_sell_revenue
    holdings_value    = current_value
    net_pnl           = realized_proceeds + holdings_value - total_invested
    net_pnl_pct       = (net_pnl / total_invested * 100) if total_invested > 0 else 0
    realized_pnl   = (total_sell_revenue - spread_avg * total_sold) if (total_sold > 0 and spread_avg > 0) else 0
    unrealized_pnl = ((current_token_price - spread_avg) * holdings) if (holdings > 0 and spread_avg > 0) else 0
    total_pnl = realized_pnl + unrealized_pnl
    usd_mult = 1.0 if display_quote in ('USD', 'USDC') else sol_price_usd

    # Break-even on remaining holdings: price the bag must reach for net P/L = 0.
    # If realized proceeds already exceed total cost, you're past break-even (clamp to 0).
    unrecovered_cost = max(0.0, spread_cost - total_sell_revenue)
    break_even_price = (unrecovered_cost / holdings) if holdings > 0 else 0
    break_even_pct_above_current = (
        (break_even_price - current_token_price) / current_token_price * 100
    ) if current_token_price > 0 else 0

    breakdown = {
        'reg_buys': {'tokens': total_bought_reg, 'cost': total_buy_cost_reg,
            'avg': total_buy_cost_reg / total_bought_reg if total_bought_reg > 0 else 0,
            'pct_of_cost': (total_buy_cost_reg / spread_cost * 100) if spread_cost > 0 else 0},
        'keeper': {'tokens': dca_buy_tokens, 'cost': dca_buy_cost_q,
            'avg': dca_buy_cost_q / dca_buy_tokens if dca_buy_tokens > 0 else 0,
            'pct_of_cost': (dca_buy_cost_q / spread_cost * 100) if spread_cost > 0 else 0},
        'funding': {'tokens': extra_tokens_priced, 'cost': extra_cost,
            'avg': extra_cost / extra_tokens_priced if extra_tokens_priced > 0 else 0,
            'pct_of_cost': (extra_cost / spread_cost * 100) if spread_cost > 0 else 0,
            'source': cost_source},
    }

    return {
        'display_quote': display_quote,
        'avg_buy_price': spread_avg, 'spread_avg_buy_price': spread_avg,
        'strict_avg_buy_price': strict_avg, 'avg_sell_price': avg_sell_price,
        'total_bought_tokens': spread_tokens, 'strict_bought_tokens': strict_tokens,
        'total_sold': total_sold, 'total_cost': spread_cost, 'strict_cost': strict_cost,
        'total_sell_revenue': total_sell_revenue,
        'total_unpriced_in': total_unpriced_in, 'priceable_unpriced': priceable_unpriced,
        'auto_airdrop_count': len(airdrops),
        'auto_airdrop_tokens': auto_airdrop_tokens,
        'manual_airdrop_tokens': manual_airdrop_tokens,
        'total_airdrop_tokens': auto_airdrop_tokens + manual_airdrop_tokens,
        'extra_tokens_priced': extra_tokens_priced, 'extra_cost_used': extra_cost,
        'extra_cost_source': cost_source,
        'auto_funding_q': auto_funding_q, 'auto_funding_usd': auto_funding_usd,
        'total_invested': total_invested, 'realized_proceeds': realized_proceeds,
        'holdings_value': holdings_value, 'net_pnl': net_pnl, 'net_pnl_pct': net_pnl_pct,
        'cost_breakdown': breakdown,
        'num_buys': sum(1 for t in regular if t['type'] == 'buy'),
        'num_sells': sum(1 for t in regular if t['type'] == 'sell'),
        'num_unpriced': sum(1 for t in regular if t['type'] == 'unpriced_in'),
        'num_transfers': sum(1 for t in regular if t['type'] == 'transfer_out'),
        'reg_buy_cost': total_buy_cost_reg, 'reg_buy_tokens': total_bought_reg,
        'dca_orders': dca_aggregate['order_count'], 'dca_source': dca_aggregate.get('source', 'unknown'),
        'dca_buy_tokens': dca_buy_tokens, 'dca_buy_cost': dca_buy_cost_q,
        'dca_sell_tokens': dca_sell_tokens, 'dca_sell_revenue': dca_sell_rev_q,
        'dca_gross_usdc_out': dca_aggregate.get('gross_usdc_out', 0),
        'dca_gross_usdc_in':  dca_aggregate.get('gross_usdc_in', 0),
        'dca_errors': dca_aggregate['errors'], 'manual_dca_cost': manual_dca_cost,
        'lp_ops_count': len(lp_ops),
        'lp_in':  sum(t['token_amount'] for t in lp_ops if t['token_delta'] > 0),
        'lp_out': sum(t['token_amount'] for t in lp_ops if t['token_delta'] < 0),
        'computed_holdings': computed_holdings, 'on_chain_balance': on_chain_balance,
        'reconciliation_diff': diff, 'reconciled': reconciled, 'holdings': holdings,
        'wallet_only_holdings': wallet_only_holdings,
        'position_breakdown': position_breakdown,
        'pending_limit_proceeds_usd': (
            position_breakdown['limit_orders']['pending_proceeds_usd']
            if position_breakdown else 0
        ),
        'current_token_price': current_token_price, 'current_value': current_value,
        'break_even_price': break_even_price,
        'break_even_pct_above_current': break_even_pct_above_current,
        'realized_pnl': realized_pnl, 'unrealized_pnl': unrealized_pnl,
        'total_pnl': total_pnl, 'sol_price_usd': sol_price_usd,
        'total_cost_usd': spread_cost * usd_mult,
        'current_value_usd': current_value * usd_mult,
        'total_pnl_usd': total_pnl * usd_mult, 'net_pnl_usd': net_pnl * usd_mult,
        'total_invested_usd': total_invested * usd_mult,
        'realized_proceeds_usd': realized_proceeds * usd_mult,
    }


def get_jupiter_open_limit_orders(wallets, target_mint, target_decimals):
    """Fetch open Jupiter Limit sell orders across the given wallets.

    Returns a list of dicts. Only sell orders (target_mint as input) are kept.
    Each dict:
      { 'wallet': str, 'order_pda': str,
        'tokens_remaining': float,
        'expected_proceeds_usdc': float,
        'limit_price': float,            # USDC per target token
        'setup_ts': int or None }

    Failure mode: returns ([], error_str) if any wallet's fetch fails outright.
    Partial successes (some wallets ok, others failed) return (orders, error_str).

    API: GET https://api.jup.ag/trigger/v1/getTriggerOrders
    Params: user=<wallet>&orderStatus=active
    Key fields: orderKey, inputMint, outputMint, rawRemainingMakingAmount,
                rawRemainingTakingAmount, createdAt
    """
    orders = []
    errors = []
    for wallet in wallets:
        try:
            _rate_limit(0.2)
            url = 'https://api.jup.ag/trigger/v1/getTriggerOrders'
            resp = requests.get(url, params={'user': wallet, 'orderStatus': 'active'}, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
            raw_orders = payload if isinstance(payload, list) else payload.get('orders', [])
            for o in raw_orders:
                input_mint  = o.get('inputMint')  or ''
                if input_mint != target_mint:
                    continue  # not a sell of our target
                # rawRemainingMakingAmount / rawRemainingTakingAmount are in smallest units
                making_raw = _raw_remaining(o, 'rawRemainingMakingAmount', 'makingAmount')
                taking_raw = _raw_remaining(o, 'rawRemainingTakingAmount', 'takingAmount')
                # Convert from smallest units
                tokens_remaining = making_raw / (10 ** target_decimals)
                # Output is USDC (6 decimals) or USDT (6 decimals)
                quote_decimals = 6
                expected_proceeds = taking_raw / (10 ** quote_decimals)
                limit_price = (expected_proceeds / tokens_remaining) if tokens_remaining > 0 else 0
                orders.append({
                    'wallet': wallet,
                    'order_pda': o.get('orderKey') or '',
                    'tokens_remaining': tokens_remaining,
                    'expected_proceeds_usdc': expected_proceeds,
                    'limit_price': limit_price,
                    'setup_ts': o.get('createdAt') if o.get('createdAt') is not None else o.get('created_at'),
                })
        except Exception as e:
            errors.append(f'{wallet[:6]}...: {e}')
            print(f'[limit-api] error for {wallet}: {e}')
    err_str = '; '.join(errors) if errors else None
    print(f'[limit-api] {len(orders)} open sell orders across {len(wallets)} wallets'
          + (f' (errors: {err_str})' if err_str else ''))
    return orders, err_str


# =========================================================================
# Meteora DLMM positions (on-chain RPC, no HTTP API available)
# =========================================================================
# PositionV2 account layout (Anchor IDL):
#   [0:8]    discriminator  (sha256("account:PositionV2")[0:8] = 75b0d4c7f5b485b6)
#   [8:40]   lb_pair        (pubkey, 32 bytes)
#   [40:72]  owner          (pubkey, 32 bytes)
#   [72:1192] liquidity_shares  (u128[70], 1120 bytes)
#   [1192:4552] reward_infos    (UserRewardInfo[70], 70*48=3360 bytes)
#   [4552:7912] fee_infos       (FeeInfo[70], 70*48=3360 bytes)
#   [7912:7916] lower_bin_id    (i32)
#   [7916:7920] upper_bin_id    (i32)
#   ...
# LbPair account layout (offsets confirmed by scan):
#   [88:120]  token_x_mint  (pubkey)
#   [120:152] token_y_mint  (pubkey)
#   [152:184] reserve_x     (pubkey, token account)
#   [184:216] reserve_y     (pubkey, token account)
# BinArray account layout:
#   [8:16]  index  (i64)
#   [24:56] lb_pair (pubkey)
#   [56+]   bins (Bin[70]), each Bin = 144 bytes:
#             [0:8]   amount_x   (u64, raw)
#             [8:16]  amount_y   (u64, raw)
#             [32:48] liquidity_supply (u128)

def _b58encode(b):
    """Encode bytes to base58 (Bitcoin alphabet)."""
    ALPHA = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    n = int.from_bytes(b, 'big')
    res = ''
    while n > 0:
        n, r = divmod(n, 58)
        res = ALPHA[r] + res
    for byte in b:
        if byte == 0:
            res = '1' + res
        else:
            break
    return res

_DLMM_POSITION_V2_DISCR_B58 = _b58encode(
    _hashlib.sha256(b'account:PositionV2').digest()[:8]
)  # = 'LgkNAEYaVX3' confirmed against 75b0d4c7f5b485b6

_BIN_ARRAY_DISCR_B58 = _b58encode(
    _hashlib.sha256(b'account:BinArray').digest()[:8]
)  # = 'GUunkrC2gRJ' confirmed against 5c8e5cdc059446b5

_BINS_PER_ARRAY = 70
_BIN_SIZE = 144          # bytes per Bin struct
_BINARRAY_HEADER = 56    # bytes before bins[] in BinArray account
_POSV2_LIQ_OFFSET = 72  # bytes before liquidityShares in PositionV2
_POSV2_LB_PAIR_OFFSET = 8
_POSV2_OWNER_OFFSET = 40
_POSV2_LOWER_BIN_OFFSET = 7912  # after 8+32+32+70*16+70*48+70*48
_POSV2_UPPER_BIN_OFFSET = 7916
_LBPAIR_TOKEN_X_OFFSET = 88
_LBPAIR_TOKEN_Y_OFFSET = 120
_LBPAIR_RESERVE_X_OFFSET = 152
_LBPAIR_RESERVE_Y_OFFSET = 184


def _rpc_post(payload):
    """Fire a JSON-RPC POST against HELIUS_RPC with 429 retry/backoff.

    Raises RuntimeError on JSON-level RPC errors (e.g. invalid params).
    """
    delay = 1.0
    max_retries = 5
    for attempt in range(max_retries):
        _rate_limit()
        resp = requests.post(HELIUS_RPC, json=payload, timeout=20)
        if resp.status_code == 429:
            if attempt == max_retries - 1:
                resp.raise_for_status()
            print(f'[dlmm][rate-limit] backing off {delay:.1f}s')
            time.sleep(delay)
            delay = min(delay * 2, 16)
            continue
        resp.raise_for_status()
        data = resp.json()
        if 'error' in data:
            raise RuntimeError(f'RPC error: {data["error"]}')
        return data
    return resp.json()  # unreachable but satisfies linter


def _get_program_accounts_pubkeys(program, filters):
    """Return list of pubkeys for program accounts matching filters (dataSlice=0)."""
    payload = {
        'jsonrpc': '2.0', 'id': 1,
        'method': 'getProgramAccounts',
        'params': [program, {
            'encoding': 'base64',
            'dataSlice': {'offset': 0, 'length': 0},
            'filters': filters,
            'withContext': False,
        }],
    }
    result = _rpc_post(payload).get('result', [])
    return [a['pubkey'] for a in result] if isinstance(result, list) else []


def _get_account_data(pubkey):
    """Return raw bytes for a single account, or None if missing."""
    payload = {
        'jsonrpc': '2.0', 'id': 1,
        'method': 'getAccountInfo',
        'params': [pubkey, {'encoding': 'base64'}],
    }
    d = _rpc_post(payload).get('result', {}).get('value')
    if not d:
        return None
    return base64.b64decode(d['data'][0])


def _decode_position_v2(raw):
    """Parse a PositionV2 account's raw bytes into a minimal dict."""
    if len(raw) < _POSV2_UPPER_BIN_OFFSET + 4:
        return None
    lb_pair = _b58encode(raw[_POSV2_LB_PAIR_OFFSET:_POSV2_LB_PAIR_OFFSET + 32])
    owner   = _b58encode(raw[_POSV2_OWNER_OFFSET:_POSV2_OWNER_OFFSET + 32])
    lower   = _struct.unpack_from('<i', raw, _POSV2_LOWER_BIN_OFFSET)[0]
    upper   = _struct.unpack_from('<i', raw, _POSV2_UPPER_BIN_OFFSET)[0]
    liq_shares = [
        int.from_bytes(raw[_POSV2_LIQ_OFFSET + i*16 : _POSV2_LIQ_OFFSET + (i+1)*16], 'little')
        for i in range(_BINS_PER_ARRAY)
    ]
    return {
        'lb_pair': lb_pair, 'owner': owner,
        'lower_bin_id': lower, 'upper_bin_id': upper,
        'liq_shares': liq_shares,
    }


def _decode_lb_pair(raw):
    """Extract token_x_mint, token_y_mint from an LbPair account."""
    if len(raw) < _LBPAIR_RESERVE_Y_OFFSET + 32:
        return None
    return {
        'token_x_mint':   _b58encode(raw[_LBPAIR_TOKEN_X_OFFSET:_LBPAIR_TOKEN_X_OFFSET + 32]),
        'token_y_mint':   _b58encode(raw[_LBPAIR_TOKEN_Y_OFFSET:_LBPAIR_TOKEN_Y_OFFSET + 32]),
    }


def _decode_bin_array(raw):
    """Return (index, dict of bin_id -> (amount_x_raw, amount_y_raw, liq_supply))."""
    if len(raw) < _BINARRAY_HEADER:
        return None, {}
    index = _struct.unpack_from('<q', raw, 8)[0]
    bins = {}
    for slot in range(_BINS_PER_ARRAY):
        bin_id = index * _BINS_PER_ARRAY + slot
        off = _BINARRAY_HEADER + slot * _BIN_SIZE
        if off + _BIN_SIZE > len(raw):
            break
        amount_x   = _struct.unpack_from('<Q', raw, off)[0]
        amount_y   = _struct.unpack_from('<Q', raw, off + 8)[0]
        liq_supply = int.from_bytes(raw[off + 32 : off + 48], 'little')
        bins[bin_id] = (amount_x, amount_y, liq_supply)
    return index, bins


def _find_bin_array_accounts(lb_pair_key):
    """Return all BinArray pubkeys for a given lb_pair via getProgramAccounts."""
    filters = [
        {'memcmp': {'bytes': _BIN_ARRAY_DISCR_B58, 'offset': 0}},
        {'memcmp': {'bytes': lb_pair_key, 'offset': 24}},
    ]
    return _get_program_accounts_pubkeys(METEORA_DLMM, filters)


def get_dlmm_positions(wallets, target_mint, target_decimals=None):
    """Fetch Meteora DLMM PositionV2 positions across wallets holding target_mint.

    Uses on-chain RPC (Helius) via getProgramAccounts with PositionV2 discriminator
    and per-owner memcmp filter. Computes token amounts from BinArray data.

    No public REST API exists for per-owner DLMM positions as of May 2026:
    - dlmm-api.meteora.ag/* returns 404 on all paths
    - dlmm.datapi.meteora.ag/portfolio returns empty (not indexed in real time)

    Returns: (positions, error_str)
      positions: list of dicts, each:
        { 'wallet': str, 'position_pubkey': str, 'pair_address': str,
          'tokens': float }   # target_mint amount in this position (human units)
    """
    if target_decimals is None:
        target_decimals = get_token_decimals(target_mint)

    positions = []
    errors = []

    for wallet in wallets:
        try:
            _rate_limit(0.2)
            # 1. Find all PositionV2 accounts owned by this wallet
            filters = [
                {'memcmp': {'bytes': _DLMM_POSITION_V2_DISCR_B58, 'offset': 0}},
                {'memcmp': {'bytes': wallet, 'offset': _POSV2_OWNER_OFFSET}},
            ]
            pos_pubkeys = _get_program_accounts_pubkeys(METEORA_DLMM, filters)
            print(f'[dlmm] {wallet[:6]}...: {len(pos_pubkeys)} PositionV2 accounts')

            for pos_pubkey in pos_pubkeys:
                try:
                    _rate_limit(0.1)
                    pos_raw = _get_account_data(pos_pubkey)
                    if not pos_raw:
                        continue
                    pos = _decode_position_v2(pos_raw)
                    if not pos:
                        continue

                    # 2. Check if this pool involves target_mint
                    _rate_limit(0.1)
                    pair_raw = _get_account_data(pos['lb_pair'])
                    if not pair_raw:
                        continue
                    pair_info = _decode_lb_pair(pair_raw)
                    if not pair_info:
                        continue

                    is_x = pair_info['token_x_mint'] == target_mint
                    is_y = pair_info['token_y_mint'] == target_mint
                    if not is_x and not is_y:
                        continue  # this pool doesn't involve target_mint

                    # 3. Load BinArray accounts covering this position's bin range
                    lower = pos['lower_bin_id']
                    upper = pos['upper_bin_id']
                    liq_shares = pos['liq_shares']

                    # Determine which BinArray indices cover [lower, upper]
                    ba_indices_needed = set(
                        math.floor(b / _BINS_PER_ARRAY)
                        for b in range(lower, upper + 1)
                    )

                    _rate_limit(0.1)
                    ba_pubkeys = _find_bin_array_accounts(pos['lb_pair'])
                    bin_data = {}  # bin_id -> (amount_x_raw, amount_y_raw, liq_supply)
                    for ba_pk in ba_pubkeys:
                        _rate_limit(0.05)
                        ba_raw = _get_account_data(ba_pk)
                        if not ba_raw:
                            continue
                        ba_index, ba_bins = _decode_bin_array(ba_raw)
                        if ba_index in ba_indices_needed:
                            bin_data.update(ba_bins)

                    # 4. Compute user's share per bin
                    total_target_raw = 0
                    for i, bin_id in enumerate(range(lower, upper + 1)):
                        if i >= len(liq_shares):
                            break
                        user_share = liq_shares[i]
                        if user_share == 0:
                            continue
                        bin_entry = bin_data.get(bin_id)
                        if not bin_entry:
                            continue
                        amt_x_raw, amt_y_raw, liq_supply = bin_entry
                        if liq_supply == 0:
                            continue
                        if is_x:
                            total_target_raw += (amt_x_raw * user_share) // liq_supply
                        else:
                            total_target_raw += (amt_y_raw * user_share) // liq_supply

                    tokens = total_target_raw / (10 ** target_decimals)
                    if tokens <= 0:
                        continue  # empty position

                    positions.append({
                        'wallet':           wallet,
                        'position_pubkey':  pos_pubkey,
                        'pair_address':     pos['lb_pair'],
                        'tokens':           tokens,
                    })

                except Exception as e:
                    errors.append(f'{wallet[:6]}...pos: {e}')
                    print(f'[dlmm] error decoding position {pos_pubkey}: {e}')

        except Exception as e:
            errors.append(f'{wallet[:6]}...: {e}')
            print(f'[dlmm] error for wallet {wallet}: {e}')

    err_str = '; '.join(errors) if errors else None
    print(f'[dlmm] {len(positions)} DLMM positions across {len(wallets)} wallets'
          + (f' (errors: {err_str})' if err_str else ''))
    return positions, err_str


# =========================================================================
# Routes
# =========================================================================
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


def _normalize_wallet_input(raw):
    raw_lines = (raw or '').split('\n')
    cleaned, seen = [], set()
    for line in raw_lines:
        s = line.strip()
        if not s or s in seen: continue
        seen.add(s); cleaned.append(s)
    return cleaned


def _normalize_program_input(raw):
    if not raw: return set()
    raw = raw.replace(',', '\n')
    out = set()
    for line in raw.split('\n'):
        s = line.strip()
        if 32 <= len(s) <= 44: out.add(s)
    return out


@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.json or {}
    wallets = _normalize_wallet_input(data.get('wallets', '') or '')
    target_mint = (data.get('token_address', '') or '').strip()
    display_quote = data.get('display_quote', 'USDC')
    manual_dca_cost = float(data.get('manual_dca_cost', 0) or 0)
    manual_airdrop_tokens = float(data.get('airdrop_tokens', 0) or 0)
    force_fresh = bool(data.get('force_fresh', False))
    extra_airdrops = _normalize_program_input(data.get('extra_airdrop_programs', '') or '')

    airdrop_programs = DEFAULT_AIRDROP_PROGRAMS | extra_airdrops
    if extra_airdrops: print(f'[airdrops] custom programs added: {extra_airdrops}')

    if not wallets or not target_mint:
        return jsonify({'error': 'Provide wallet addresses and a token address.'}), 400

    print(f'\n[analyze] wallets={wallets}\n[analyze] target_mint={target_mint}')

    try:
        sol_price_usd   = get_token_price_usd(SOL_MINT)
        token_price_usd = get_token_price_usd(target_mint)
        token_symbol    = get_token_symbol(target_mint)
        print(f'[prices] SOL=${sol_price_usd:.2f}  {token_symbol or "?"}=${token_price_usd:.6f}')

        all_txs = []
        for w in wallets:
            print(f'\n[fetch] {w}')
            txs = get_all_transactions_cached(w, force_fresh=force_fresh)
            all_txs.extend(txs)

        seen, unique = set(), []
        for tx in all_txs:
            sig = tx.get('signature', '')
            if sig and sig not in seen: seen.add(sig); unique.append(tx)

        wallet_set = set(wallets)
        trades = analyze_token_trades(unique, target_mint, wallets, airdrop_programs)
        target_decimals = get_token_decimals(target_mint)

        funding_events = detect_funding_txs(unique, target_mint, wallet_set, sol_price_usd)
        auto_funding_usd = sum(e['funding_usd'] for e in funding_events)

        api_dca = get_jupiter_dca_aggregate_api(wallets, target_mint, target_decimals)
        if api_dca['order_count'] == 0:
            tx_dca = aggregate_dca_from_txs(trades, sol_price_usd)
            tx_dca['errors'] = api_dca['errors']
            dca_aggregate = tx_dca
        else:
            dca_aggregate = api_dca

        on_chain = 0.0; any_ok = False
        for w in wallets:
            bal = get_token_balance_on_chain(w, target_mint)
            if bal is not None: on_chain += bal; any_ok = True
        if not any_ok: on_chain = None

        # NEW: fetch open Jupiter Limit sell orders (off-wallet bucket)
        open_limit_orders, open_limit_err = get_jupiter_open_limit_orders(
            wallets, target_mint, target_decimals
        )
        # NEW: fetch Meteora DLMM positions (off-wallet bucket)
        dlmm_positions, dlmm_err = get_dlmm_positions(
            wallets, target_mint, target_decimals
        )

        # NEW: build the position breakdown
        wallet_tokens_for_breakdown = on_chain if on_chain is not None else 0.0
        position_breakdown = build_position_breakdown(
            wallet_tokens_for_breakdown,
            open_limit_orders, open_limit_err,
            dlmm_positions, dlmm_err,
            token_price_usd,
        )

        # v3.12: pair limit-order setups with their fills via Reserve token accounts
        limit_buy_orders, limit_sell_orders = analyze_limit_orders(
            unique, target_mint, wallet_set, sol_price_usd
        )

        trades = normalize_trade_prices(trades, display_quote, sol_price_usd)
        summary = calculate_summary(
            trades, dca_aggregate, on_chain,
            token_price_usd, sol_price_usd,
            auto_funding_usd, display_quote,
            manual_dca_cost, manual_airdrop_tokens,
            position_breakdown=position_breakdown,
        )

        lp_breakdown = analyze_lp_activity(trades, sol_price_usd, token_price_usd)
        best_worst = surface_best_worst_events(trades, dca_aggregate, limit_buy_orders, limit_sell_orders, top_n=15)

        return jsonify({
            'trades': trades,
            'summary': summary,
            'dca_orders':     dca_aggregate['orders'],
            'funding_events': funding_events,
            'airdrop_events': [t for t in trades if t['type'] == 'airdrop'],
            'lp_breakdown':   lp_breakdown,
            'best_worst':     best_worst,
            'limit_buy_orders':  limit_buy_orders,
            'limit_sell_orders': limit_sell_orders,
            'wallets_scanned': len(wallets),
            'transactions_scanned': len(unique),
            'target_decimals': target_decimals,
            'token_symbol':   token_symbol,
            'cache_used': not force_fresh,
        })

    except requests.exceptions.HTTPError as e:
        return jsonify({'error': f'API error: {e.response.status_code} - {e.response.text}'}), 500
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/clear-cache', methods=['POST'])
def clear_cache():
    data = request.json or {}
    wallets = _normalize_wallet_input(data.get('wallets', '') or '')
    if not wallets: return jsonify({'ok': False, 'message': 'No wallets specified.'})
    cleared = []
    for w in wallets:
        p = _cache_path(w)
        if p.exists(): p.unlink(); cleared.append(w)
    return jsonify({'ok': True, 'cleared': cleared, 'count': len(cleared)})


if __name__ == '__main__':
    print('Solana Token Tracker (v3.12) — http://localhost:5000')
    app.run(debug=True, port=5000)