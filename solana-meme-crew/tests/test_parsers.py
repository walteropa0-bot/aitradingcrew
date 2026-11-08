"""Parsers are tested against REAL API responses captured on 2026-11-08 (tests/fixtures)."""
import pytest

import config
from crew.chain.jupiter import parse_order
from crew.market.contracts import evaluate
from crew.market.dexscreener import parse_pair, pick_best
from crew.market.gecko import parse_pool
from crew.market.solana_rpc import MintInfo
from crew.util import clamp, fmt_duration, redact_url, scale, sf, si, utc_day

from .helpers import load_fixture


def test_dexscreener_real_pair_parses():
    raw = load_fixture("dx_tokens_v1.json")
    pairs = [p for p in (parse_pair(r, 1_000.0) for r in raw) if p]
    bonk = next(p for p in pairs if p.symbol.lower() == "bonk")
    assert bonk.quote_symbol == "SOL" and bonk.dex == "orca"
    assert bonk.price_native == pytest.approx(3.111e-8)
    assert bonk.price_sol == pytest.approx(3.111e-8)          # quote is WSOL -> priceNative is SOL
    assert bonk.liquidity_usd == pytest.approx(276034.7)
    assert bonk.buys_m5 == 11 and bonk.sells_m5 == 0
    assert bonk.buy_ratio_m5 == 1.0 and bonk.txns_m5 == 11
    assert bonk.chg_h24 == pytest.approx(-1.13)
    assert "twitter" in bonk.socials and bonk.websites
    assert bonk.age_min(at=2_000_000_000.0) > 1000


def test_dexscreener_tolerates_fresh_pool_without_liquidity():
    raw = {"chainId": "solana", "pairAddress": "P", "dexId": "pumpfun",
           "baseToken": {"address": "MintX", "symbol": "NEW", "name": "New"},
           "quoteToken": {"address": config.WSOL_MINT, "symbol": "SOL"}, "priceNative": "0.0000001",
           "txns": {"m5": {"buys": 3, "sells": 1}}, "pairCreatedAt": 1700000000000}
    p = parse_pair(raw, 1.0)
    assert p is not None and p.liquidity_usd == 0.0 and p.price_usd == 0.0 and p.price_sol == pytest.approx(1e-7)


def test_dexscreener_rejects_other_chains_and_unwanted_mints():
    raw = {"chainId": "ethereum", "baseToken": {"address": "0x1"}, "quoteToken": {}}
    assert parse_pair(raw, 1.0) is None
    raw2 = {"chainId": "solana", "baseToken": {"address": "A"}, "quoteToken": {}}
    assert parse_pair(raw2, 1.0, wanted={"B"}) is None
    assert parse_pair("garbage", 1.0) is None


def test_pick_best_prefers_allowed_quote_then_liquidity():
    from crew.models import Pair
    a = Pair(mint="m", quote_symbol="SOL", liquidity_usd=5000)
    b = Pair(mint="m", quote_symbol="SOL", liquidity_usd=9000)
    c = Pair(mint="m", quote_symbol="WBTC", liquidity_usd=99999)
    assert pick_best([a, b, c], ("SOL", "USDC")) is b


def test_gecko_real_pool_parses():
    item = load_fixture("gt_new.json")["data"][0]
    gp = parse_pool(item, "new")
    assert gp is not None and gp.mint.endswith("pump")
    assert gp.dex == "pump-fun" and gp.buyers_m5 == 1 and gp.created_s > 1_700_000_000
    assert gp.reserve_usd > 0


def test_jupiter_order_without_taker():
    o = parse_order(load_fixture("jup_order.json"))
    assert o.transaction is None and not o.can_sign
    assert o.in_amount == 10_000_000 and o.out_amount > 0 and o.fee_bps == 10
    assert o.price_impact_pct == pytest.approx(0.0855, abs=1e-3)   # priceImpact is in percent points
    assert o.router == "metis" and o.request_id


def test_jupiter_order_with_insufficient_funds_is_not_signable():
    o = parse_order(load_fixture("jup_order_taker.json"))
    assert o.transaction == "" and o.error_code == 1 and not o.can_sign
    assert "Insufficient" in o.error_message


def test_contract_real_rugcheck_summary_blocks_unlocked_lp():
    rc = load_fixture("rc_sum_bonk.json")
    mi = MintInfo(mint="m", decimals=5, supply=1, program=config.TOKEN_PROGRAM, mint_authority=None, freeze_authority=None)
    rep = evaluate("m", mi, rc)
    assert not rep.ok
    assert any("gesperrt" in r for r in rep.blocked_reasons)          # LP only ~17 % locked
    assert not any("Mutable" in w for w in rep.warnings)              # ignored by config
    assert rep.mint_authority_disabled and rep.freeze_authority_disabled


def _mi(**kw):
    base = dict(mint="m", decimals=6, supply=1, program=config.TOKEN_PROGRAM, mint_authority=None, freeze_authority=None, extensions=[])
    base.update(kw)
    return MintInfo(**base)


GOOD_RC = {"lpLockedPct": 100, "score_normalised": 3, "risks": []}


def test_contract_clean_token_passes():
    rep = evaluate("m", _mi(), GOOD_RC)
    assert rep.ok and rep.safety_score >= 90 and rep.sources == ["rpc", "rugcheck"]


@pytest.mark.parametrize("mi_kw,needle", [
    ({"mint_authority": "Auth111"}, "Mint-Authority"),
    ({"freeze_authority": "Frz111"}, "Freeze-Authority"),
    ({"extensions": ["transferFeeConfig"]}, "Token-2022"),
    ({"extensions": ["permanentDelegate"]}, "Token-2022"),
])
def test_contract_blocks_dangerous_onchain_flags(mi_kw, needle):
    rep = evaluate("m", _mi(**mi_kw), GOOD_RC)
    assert not rep.ok and any(needle in r for r in rep.blocked_reasons)
    assert rep.safety_score <= 35


def test_contract_allows_benign_token2022_extensions():
    rep = evaluate("m", _mi(program=config.TOKEN_2022_PROGRAM, extensions=["metadataPointer", "tokenMetadata"]), GOOD_RC)
    assert rep.ok


def test_contract_blocks_rugcheck_danger_and_high_score():
    rc = {"lpLockedPct": 100, "score_normalised": 81, "risks": [{"name": "Creator history of rugged tokens", "level": "danger"}]}
    rep = evaluate("m", _mi(), rc)
    assert not rep.ok
    assert any("Creator history" in r for r in rep.blocked_reasons) and any("Risikoscore" in r for r in rep.blocked_reasons)


def test_contract_fails_closed_when_a_source_is_missing():
    assert not evaluate("m", None, GOOD_RC).ok          # RPC unreachable
    assert not evaluate("m", _mi(), None).ok            # RugCheck unreachable
    assert not evaluate("m", None, None).ok


def test_helpers():
    assert scale(5, 0, 10) == 0.5 and scale(-1, 0, 10) == 0 and scale(99, 0, 10) == 1 and scale(None, 0, 1) == 0
    assert clamp(5, 0, 1) == 1
    assert sf("abc", 7.0) == 7.0 and sf(None) == 0.0 and sf("nan") == 0.0 and sf("1.5") == 1.5
    assert si("12.9") == 12 and si(None, 3) == 3
    assert utc_day(0) == "1970-01-01"
    assert fmt_duration(45) == "45s" and fmt_duration(3600 * 5 + 120) == "5h 2m"
    assert "SECRET" not in redact_url("https://x.io/?api-key=SECRET&a=1") and "a=1" in redact_url("https://x.io/?api-key=SECRET&a=1")
