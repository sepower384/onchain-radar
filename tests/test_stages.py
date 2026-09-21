"""Stage 순수 함수 테스트 — 첫 실제 실행에서 잡힌 버그들을 회귀 테스트로 고정한다."""
from providers.defillama import aggregate
from scanners import stage2_reaction as s2
from scanners import stage3_derivatives as s3
from scanners import stage5_revenue as s5
from scanners.model import Candidate
from scanners.security import same_asset

CFG = {
    "stage2.price_24h_min_pct": 3.0, "stage2.price_24h_max_pct": 15.0, "stage2.price_7d_max_pct": 30.0,
    "stage2.volume_ratio_min": 2.0, "stage2.volume_ratio_full": 5.0, "stage2.late_penalty": 25,
    "stage3.funding_hot_pct": 0.05, "stage3.funding_extreme_pct": 0.10, "stage3.long_ratio_hot": 0.70,
    "stage3.oi_jump_pct": 30.0, "stage3.oi_price_stall_pct": 3.0, "stage3.overheat_penalty": 20,
    "stage5.surge_ratio_min": 1.5, "stage5.min_7d_revenue_usd": 20000, "stage5.min_base_daily_usd": 1000,
    "stage5.require_token": True, "stage5.price_lag_max_7d_pct": 10.0, "stage5.fundamental_lead_bonus": 15,
    "buy_links.min_dex_liquidity_usd": 250000, "buy_links.min_dex_liq_to_mcap_pct": 0.05,
    "buy_links.max_price_gap_pct": 3.0,
}


def cfg(k, d=None):
    return CFG.get(k, d)


# ------------------------------------------------------------------ Stage 2
def test_거래량_비율은_같은_출처끼리_나눈다():
    """버그 회귀: 코인게코 전체 거래량 ÷ 비트겟 일봉 평균 → BTC 149배(2026-09-21 첫 실행)."""
    c = Candidate(coin_key="cg:bitcoin", symbol="BTC", change_24h=5.3, change_7d=8)
    c.volume_24h = 33_800_000_000          # 코인게코: 전 거래소 합계
    c.bitget_volume_24h = 305_000_000      # 비트겟 24h
    c.volume_avg_7d = 212_000_000          # 비트겟 일봉 평균
    c.volume_ratio_src = "bitget"
    s2.evaluate(c, cfg)
    assert c.stages["reaction"].detail["volume_ratio"] == round(305 / 212, 2)   # 1.44배, 149배 아님


def test_7일_평균이_없으면_추정하지_않고_수집중():
    c = Candidate(coin_key="x", symbol="X", change_24h=5, volume_24h=1e6)
    s2.evaluate(c, cfg)
    assert c.stages["reaction"].score is None and "수집 중" in c.stages["reaction"].line


def test_이미_급등하면_늦음_태그와_감점():
    c = Candidate(coin_key="x", symbol="X", change_24h=25, change_7d=40, volume_24h=5e6,
                  volume_avg_7d=1e6)
    s2.evaluate(c, cfg)
    assert "늦음" in c.tags and any("늦음" in r for r, _ in c.penalties)


def test_초입_구간():
    c = Candidate(coin_key="x", symbol="X", change_24h=8, change_7d=10, volume_24h=4e6,
                  volume_avg_7d=1e6)
    s2.evaluate(c, cfg)
    assert "초입" in c.stages["reaction"].line and not c.penalties


# ------------------------------------------------------------------ Stage 3
def test_선물_없으면_파생_None():
    c = Candidate(coin_key="x", symbol="X")
    s3.evaluate(c, cfg)
    assert c.stages["derivatives"].score is None


def test_펀딩_극단이면_과열_감점():
    c = Candidate(coin_key="x", symbol="X", bitget_futures="XUSDT", funding_pct=0.12, change_24h=5)
    s3.evaluate(c, cfg)
    assert "과열" in c.risks and c.stages["derivatives"].score <= 50


def test_OI_급증인데_가격_정체면_과열():
    c = Candidate(coin_key="x", symbol="X", bitget_futures="XUSDT", funding_pct=0.01,
                  change_24h=1.0, oi_usd=1.5e7)
    s3.evaluate(c, cfg, oi_24h_ago=1.0e7)          # +50%
    assert "과열" in c.risks and "정체" in c.stages["derivatives"].line


# ------------------------------------------------------------------ Stage 5
def test_하위_프로토콜_매출을_부모로_합산하고_gecko_id를_잇는다():
    rows = [{"defillamaId": "2197", "name": "Uniswap V2", "total7d": 100, "total30d": 400, "total60dto30d": 300},
            {"defillamaId": "2198", "name": "Uniswap V3", "total7d": 900, "total30d": 3600, "total60dto30d": 2700}]
    cat = {"protocols": [{"defillamaId": "2197", "parentProtocol": "parent#uniswap"},
                         {"defillamaId": "2198", "parentProtocol": "parent#uniswap"}],
           "parentProtocols": [{"id": "parent#uniswap", "name": "Uniswap", "gecko_id": "uniswap", "symbol": "UNI"}]}
    out = aggregate(rows, cat)
    assert len(out) == 1
    u = out[0]
    assert u["gecko_id"] == "uniswap" and u["rev_7d"] == 1000
    assert round(u["ratio"], 3) == round((1000 / 7) / (3000 / 30), 3)


def test_기준선이_얕은_신규_프로토콜은_급증_랭킹에서_뺀다():
    """버그 회귀: 매출 0 이던 프로토콜이 +4,218,288% 로 1위(첫 실행)."""
    rows = [
        {"name": "New", "gecko_id": "new", "rev_7d": 30000, "daily_base": 0.1, "ratio": 42000},
        {"name": "NoToken", "gecko_id": None, "rev_7d": 90000, "daily_base": 5000, "ratio": 3},
        {"name": "Real", "gecko_id": "real", "rev_7d": 90000, "daily_base": 5000, "ratio": 2.5},
    ]
    assert [r["name"] for r in s5.surge_ranking(rows, cfg)] == ["Real"]


def test_매출_급증인데_가격이_안_오르면_펀더멘털_선행():
    c = Candidate(coin_key="cg:x", symbol="X", change_7d=2)
    s5.evaluate(c, {"rev_7d": 100000, "daily_base": 5000, "ratio": 2.5}, cfg)
    assert "펀더멘털 선행" in c.tags


# ------------------------------------------------------------------ 같은 자산 검증
def test_옛_ERC20_BNB는_같은_자산이_아니다():
    """버그 회귀: BNB 카드에 이더리움 위 옛 ERC-20 BNB 로 Uniswap 버튼이 붙었다."""
    c = Candidate(coin_key="cg:binancecoin", symbol="BNB", price=784.51, mcap=104e9)
    ok, why = same_asset(c, {"liquidity": {"usd": 306411}, "priceUsd": "817.31"}, cfg)
    assert not ok and "시총" in why


def test_진짜_UNI는_같은_자산이다():
    c = Candidate(coin_key="cg:uniswap", symbol="UNI", price=8.956, mcap=5.36e9)
    ok, _ = same_asset(c, {"liquidity": {"usd": 17367224}, "priceUsd": "8.97"}, cfg)
    assert ok
