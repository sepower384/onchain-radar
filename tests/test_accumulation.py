"""Stage 1 — 고래 순매수·거래소 순유출은 스냅샷끼리 비교. 이력이 모자라면 추정하지 않는다."""
from types import SimpleNamespace as NS

from providers.exchange_wallets import classify, load
from scanners.model import Candidate
from scanners.stage1_accumulation import evaluate, flow

BINANCE8 = "0xf977814e90da44bfa03b6295a0616a897441acec"      # Etherscan 라벨로 확인
WHALE = "0x1111111111111111111111111111111111111111"
T0 = 1_800_000_000.0


def top(ts, addr, pct, kind):
    return NS(ts=ts, address=addr, percent=pct, kind=kind)


def test_거래소_지갑은_Etherscan_라벨로_분류된다():
    assert load()[BINANCE8] == "Binance"
    assert classify(BINANCE8, False, "ethereum") == "exchange"
    assert classify(BINANCE8, False, "solana") == "wallet"          # 솔라나에는 EVM 목록을 쓰지 않는다
    assert classify("0x000000000000000000000000000000000000dead", False, "bsc") == "burn"


def test_고래는_늘고_거래소는_줄면_매집():
    tops = [top(T0, WHALE, 0.020, "wallet"), top(T0, BINANCE8, 0.050, "exchange"),
            top(T0 + 86400, WHALE, 0.028, "wallet"), top(T0 + 86400, BINANCE8, 0.041, "exchange")]
    f = flow(tops, T0 + 86400, 24)
    assert round(f["whale_pp"], 3) == 0.8          # +0.8%p
    assert round(f["exchange_pp"], 3) == -0.9      # 거래소 −0.9%p = 순유출


def test_거래소_지갑_증가를_고래_매수로_오판하지_않는다():
    """사양서: 거래소 지갑 이동을 고래 매집으로 오판하지 않도록 라벨 필터링."""
    tops = [top(T0, BINANCE8, 0.05, "exchange"), top(T0 + 86400, BINANCE8, 0.09, "exchange")]
    f = flow(tops, T0 + 86400, 24)
    assert f["whale_pp"] == 0 and f["exchange_pp"] > 0


def test_이력이_모자라면_수집중_추정하지_않는다():
    c = Candidate(coin_key="x", symbol="X")
    evaluate(c, [NS(ts=T0, holder_count=100, top10_pct=30)], now=T0 + 3600,
             tops=[top(T0, WHALE, 0.02, "wallet")])
    assert c.stages["accumulation"].score is None
    assert "수집 중" in c.stages["accumulation"].line


def test_카드_문장에_달러_환산():
    c = Candidate(coin_key="x", symbol="X", fdv=100_000_000)
    tops = [top(T0, WHALE, 0.020, "wallet"), top(T0 + 86400, WHALE, 0.032, "wallet")]
    evaluate(c, [], now=T0 + 86400, tops=tops)
    line = c.stages["accumulation"].line
    assert "순매수 +1.20%p" in line and "$1.2M" in line
