"""공식 공지 규칙 — 2026-09-21 실제로 받은 제목으로 고정한다."""
import pytest

from scanners.news_rules import classify


def up(t):
    return classify({"source": "upbit", "title": t})


def bn(t, cat="48"):
    return classify({"source": "binance", "title": t, "sub": cat})


def bg(t, sub="spot"):
    return classify({"source": "bitget", "title": t, "sub": sub})


def test_업비트_원화_상장은_강한_호재():
    r = up("클러스터프로토콜(CP) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)")
    assert r["kind"] == "listing" and r["strength"] == "strong" and r["symbols"] == ["CP"]


def test_업비트_원화마켓_디지털자산_추가():
    r = up("커브(CRV) KRW, USDT 마켓 디지털 자산 추가")
    assert r["kind"] == "listing" and r["symbols"] == ["CRV"] and r["market"] == "KRW"


def test_업비트_BTC_USDT_마켓만이면_중간():
    r = up("BTC, USDT 마켓 신규 거래지원 안내 (BICO, BMT, NIL, GWEI) (BICO, BMT, NIL, GWEI 거래지원 개시 시점 안내)")
    assert r["strength"] == "medium" and r["symbols"] == ["BICO", "BMT", "NIL", "GWEI"]


def test_부분_취소면_취소된_코인만_빼고_나머지는_상장():
    """오분류 회귀: HEMI 만 취소됐는데 USELESS 까지 '취소'로 처리했다(2026-09-21 실측)."""
    r = up("헤미(HEMI), 유즈리스(USELESS) 신규 거래지원 안내 (BTC, USDT 마켓) (헤미(HEMI) 거래지원 취소 안내)")
    assert r["kind"] == "listing" and r["symbols"] == ["USELESS"] and r["strength"] == "medium"


def test_전부_취소면_취소():
    r = up("헤미(HEMI) 신규 거래지원 안내 (BTC, USDT 마켓) (헤미(HEMI) 거래지원 취소 안내)")
    assert r["kind"] == "cancel"


@pytest.mark.parametrize("t,kind", [
    ("아이콘(ICX) 거래지원 종료 안내 (10/19 15:00)", "delisting"),
    ("멀티버스엑스(EGLD) 거래 유의 종목 지정 안내", "warning"),
    ("샌드박스(SAND) 유의 촉구 안내", "warning"),
])
def test_업비트_리스크_공지(t, kind):
    r = up(t)
    assert r["kind"] == kind and r["symbols"] and "KRW" not in r["symbols"]


def test_바이낸스_현물_상장():
    r = bn("Binance Will List 牛来 (牛来) with Seed Tag Applied")
    assert r is None or r["kind"] == "listing"      # 한자 심볼은 규칙상 심볼 형식이 아니라 거른다
    r = bn("Binance Will List Zama (ZAMA)")
    assert r["kind"] == "listing" and r["symbols"] == ["ZAMA"]


@pytest.mark.parametrize("t", [
    "Binance Adds 25 Stocks on Binance Stock Trading - 2026-09-21",
    "Binance Futures Will Launch Multiple USDⓈ-Margined Perpetual Contracts (2026-09-18)",
    "Binance Exchange Adds GoPro (GPROB) and Reddit (RDDTB) bStocks Trading Pairs on Binance Spot/Convert",
    "Binance Will Add 牛来 (牛来) on Earn, Buy Crypto, Convert, VIP Loan & Margin",
])
def test_바이낸스_주식_선물_부가서비스는_상장이_아니다(t):
    assert bn(t) is None


def test_바이낸스_상폐():
    r = bn("Binance Will Delist ICX, SCRT, STORJ on 2026-09-03", "161")
    assert r["kind"] == "delisting" and r["symbols"] == ["ICX", "SCRT", "STORJ"]


def test_비트겟_현물_최초상장():
    r = bg("[Initial Listing] Bitget to list MarsCat (MCAT)")
    assert r["kind"] == "listing" and r["symbols"] == ["MCAT"] and r["strength"] == "medium"


@pytest.mark.parametrize("t,sub", [
    ("Bitget lists 480 spot stock assets, including rCOMP, rIAG, rQGEN, rRRC, and rCBSH", "spot"),
    ("STONKUSDT now launched for futures trading and trading bots", "futures"),
])
def test_비트겟_주식_선물은_제외(t, sub):
    assert bg(t, sub) is None
