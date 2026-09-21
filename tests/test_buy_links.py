"""구매 링크 — 사양서가 테스트를 명시한 모듈.

실제 코인 주소로 링크를 만들어, **그 링크가 그 토큰을 가리키는지**를 확인한다.
(네트워크를 쓰는 실검증은 tests/test_links_live.py — `pytest -m live`)
"""
from urllib.parse import parse_qs, urlparse

import pytest

from bot.buy_links import LinkBuilder, short_addr, valid_address
from providers.goplus import Security


class FakeSettings:
    def __init__(self, cfg):
        self.cfg = cfg

    def get(self, dotted, default=None):
        cur = self.cfg
        for p in dotted.split("."):
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur


REF = {"referral": {"bitget": {"channelCode": "yrbp", "vipCode": "m6v2"}}}

# 실측에 쓴 실제 토큰들
UNI_ETH = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"
AERO_BASE = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
CAKE_BSC = "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82"
JUP_SOL = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"


@pytest.fixture
def lb():
    return LinkBuilder(FakeSettings(REF))


def q(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# ------------------------------------------------------------------ 비트겟 + 레퍼럴
def test_비트겟_현물에_레퍼럴이_붙는다(lb):
    u = lb.bitget_spot("UNIUSDT")
    assert u.startswith("https://www.bitget.com/spot/UNIUSDT?")
    assert q(u) == {"channelCode": "yrbp", "vipCode": "m6v2"}


def test_비트겟_선물에도_레퍼럴이_붙는다(lb):
    u = lb.bitget_futures("BTCUSDT")
    assert u.startswith("https://www.bitget.com/futures/usdt/BTCUSDT?")
    assert q(u)["channelCode"] == "yrbp"


def test_레퍼럴을_비우면_일반_링크():
    lb = LinkBuilder(FakeSettings({"referral": {"bitget": {}}}))
    assert lb.bitget_spot("UNIUSDT") == "https://www.bitget.com/spot/UNIUSDT"


def test_레퍼럴은_비트겟에만_붙고_DEX에는_안_붙는다(lb):
    _, u = lb.dex("ethereum", UNI_ETH)
    assert "channelCode" not in u and "vipCode" not in u


def test_상장_확인이_안_된_코인에는_비트겟_버튼이_없다(lb):
    ls = lb.build(bitget_spot=None, bitget_futures=None, chain="ethereum", address=UNI_ETH)
    assert not any("비트겟" in b.text for b in ls.buttons)


# ------------------------------------------------------------------ DEX — 올바른 토큰을 여는가
def test_솔라나는_Jupiter_민트로(lb):
    name, u = lb.dex("solana", JUP_SOL)
    assert name == "Jupiter"
    assert urlparse(u).netloc == "jup.ag"
    assert q(u) == {"sell": "So11111111111111111111111111111111111111112", "buy": JUP_SOL}


@pytest.mark.parametrize("chain,addr,uchain", [
    ("ethereum", UNI_ETH, "ethereum"),
    ("base", AERO_BASE, "base"),
    ("arbitrum", "0x912CE59144191C1204E64559FE8253a0e49E6548", "arbitrum"),
])
def test_EVM은_Uniswap_체인_파라미터_포함(lb, chain, addr, uchain):
    name, u = lb.dex(chain, addr)
    assert name == "Uniswap"
    p = q(u)
    assert p["chain"] == uchain and p["outputCurrency"] == addr and p["inputCurrency"] == "NATIVE"


def test_BSC는_PancakeSwap(lb):
    name, u = lb.dex("bsc", CAKE_BSC)
    assert name == "PancakeSwap"
    assert q(u) == {"chain": "bsc", "outputCurrency": CAKE_BSC}


def test_대표_DEX를_모르는_체인은_DexScreener로(lb):
    name, u = lb.dex("sui", "0x" + "a" * 64, pair_url="https://dexscreener.com/sui/0xabc")
    assert name == "DexScreener" and u == "https://dexscreener.com/sui/0xabc"


# ------------------------------------------------------------------ 안전 규칙
@pytest.mark.parametrize("bad", ["UNI", "uniswap", "0x1234", "", None, "0xZZZZ" + "0" * 36])
def test_주소가_아니면_DEX_링크를_만들지_않는다(lb, bad):
    """티커·이름·깨진 주소로는 절대 링크를 만들지 않는다 — 가짜 토큰 오매수 방지."""
    assert lb.dex("ethereum", bad) is None


def test_솔라나_주소에_EVM_형식을_넣으면_거부(lb):
    assert lb.dex("solana", UNI_ETH) is None


def test_허니팟이면_구매_버튼을_전부_뺀다(lb):
    sec = Security(chain="base", address=AERO_BASE, known=True, honeypot=True)
    ls = lb.build(bitget_spot="AEROUSDT", chain="base", address=AERO_BASE, security=sec)
    texts = [b.text for b in ls.buttons]
    assert ls.blocked_reason == "허니팟"
    assert not any("매수" in t or "비트겟" in t for t in texts)
    assert "📊 차트" in texts and "🔍 컨트랙트" in texts   # 왜 막혔는지 볼 수는 있어야 한다


def test_매도세_30퍼센트_이상도_차단(lb):
    sec = Security(chain="bsc", address=CAKE_BSC, known=True, sell_tax_pct=35)
    ls = lb.build(chain="bsc", address=CAKE_BSC, security=sec)
    assert ls.blocked_reason == "매도세 35%"


def test_마이크로캡_구매_라벨에_경고(lb):
    ls = lb.build(chain="solana", address=JUP_SOL, microcap=True)
    buy = [b for b in ls.buttons if "매수" in b.text]
    assert buy and buy[0].text.startswith("⚠️ 초고위험 · ")


def test_전체_카드_버튼_배치(lb):
    ls = lb.build(bitget_spot="UNIUSDT", bitget_futures="UNIUSDT",
                  chain="ethereum", address=UNI_ETH,
                  pair_url="https://dexscreener.com/ethereum/0xabc")
    assert [b.text for b in ls.rows[0]] == ["🛒 비트겟 현물", "📈 비트겟 선물"]
    assert [b.text for b in ls.rows[1]] == ["🔄 Uniswap 매수", "📊 차트", "🔍 컨트랙트"]
    assert ls.rows[1][2].url == f"https://etherscan.io/token/{UNI_ETH}"


def test_주소_줄임표시():
    assert short_addr(UNI_ETH) == "0x1f98…F984"
    assert short_addr(None) == "-"


def test_주소_형식_판정():
    assert valid_address("ethereum", UNI_ETH)
    assert valid_address("solana", JUP_SOL)
    assert not valid_address("solana", "0OIl" * 10)      # base58 에 없는 글자
