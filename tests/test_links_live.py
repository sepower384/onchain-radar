"""링크가 **정말 그 토큰을 여는지** 네트워크로 확인한다. `pytest -m live`

거래소·DEX 페이지는 SPA 라 없는 심볼에도 200 을 준다. 그래서 "페이지가 열리나"가 아니라
**"그 링크에 박힌 식별자가 그 토큰인가"**를 독립된 소스로 맞춰 본다.
  - 비트겟: 공식 API 에서 그 심볼이 online/normal 인가
  - DEX: 링크에 들어간 주소를 DexScreener 에 물어 심볼이 맞는가
  - Jupiter: jup.ag/tokens/<민트> 서버 렌더 제목에 심볼이 찍히는가
"""
import re
import ssl
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest

from bot.buy_links import LinkBuilder
from tests.test_buy_links import AERO_BASE, CAKE_BSC, JUP_SOL, REF, UNI_ETH, FakeSettings

pytestmark = pytest.mark.live

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"


def fetch(url, as_json=True):
    import json
    r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}),
                               timeout=30, context=CTX)
    b = r.read()
    return json.loads(b) if as_json else b.decode("utf-8", "replace")


@pytest.fixture
def lb():
    return LinkBuilder(FakeSettings(REF))


@pytest.mark.parametrize("chain,addr,symbol", [
    ("ethereum", UNI_ETH, "UNI"), ("base", AERO_BASE, "AERO"), ("bsc", CAKE_BSC, "Cake"),
])
def test_DEX_링크의_주소가_그_토큰이다(lb, chain, addr, symbol):
    _, url = lb.dex(chain, addr)
    out = parse_qs(urlparse(url).query)["outputCurrency"][0]
    pairs = fetch(f"https://api.dexscreener.com/token-pairs/v1/{chain}/{out}")
    syms = {p["baseToken"]["symbol"].upper() for p in pairs
            if p["baseToken"]["address"].lower() == out.lower()}
    assert symbol.upper() in syms


def test_Jupiter_민트가_JUP_토큰을_연다(lb):
    _, url = lb.dex("solana", JUP_SOL)
    mint = parse_qs(urlparse(url).query)["buy"][0]
    html = fetch(f"https://jup.ag/tokens/{mint}", as_json=False)
    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
    assert title.startswith("JUP ")


@pytest.mark.parametrize("base", ["BTC", "UNI"])
def test_비트겟_심볼이_실제로_현물_거래중(base):
    d = fetch("https://api.bitget.com/api/v2/spot/public/symbols")["data"]
    row = next(x for x in d if x["symbol"] == f"{base}USDT")
    assert row["status"] == "online"


def test_비트겟_레퍼럴_단축링크가_같은_코드로_풀린다():
    """config 의 channelCode/vipCode 가 강회장 파트너 링크와 일치하는지 — 코드가 바뀌면 여기서 잡힌다."""
    class Stop(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a):
            return None
    op = urllib.request.build_opener(Stop, urllib.request.HTTPSHandler(context=CTX))
    try:
        op.open(urllib.request.Request("https://partner.bitget.com/bg/W63KTT",
                                       headers={"User-Agent": UA}), timeout=30)
        pytest.fail("리다이렉트가 없다")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
    got = {k: v[0] for k, v in parse_qs(urlparse(loc).query).items()}
    assert got.get("channelCode") == REF["referral"]["bitget"]["channelCode"]
    assert got.get("vipCode") == REF["referral"]["bitget"]["vipCode"]
