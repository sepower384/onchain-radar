"""Stage 6 — 공식 공지 → 카드 호재·리스크, Claude 분류는 상한을 지키고 모르면 비운다."""
import asyncio
import time

from providers.claude_news import NewsClassifier
from scanners import stage6_news as s6
from scanners.model import Candidate
from storage.db import Store

CFG = {"stage6.lookback_hours": 72, "stage6.risk_lookback_days": 14, "stage6.risk_penalty": 30,
       "stage6.enabled": False, "stage6.daily_call_limit": 2}


def cfg(k, d=None):
    return CFG.get(k, d)


class FakeFeeds:
    def __init__(self, items):
        self.items = items

    async def fetch(self):
        return self.items


class S:
    def get(self, k, d=None):
        return CFG.get(k, d)

    def key(self, n):
        return None


NOW = time.time()


def test_업비트_원화_상장은_카드에_강한_호재():
    st = Store("sqlite:///:memory:")
    feeds = FakeFeeds([{"source": "upbit", "id": "upbit:1", "ts": NOW - 3600, "url": "u",
                        "title": "클러스터프로토콜(CP) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)"}])
    new = asyncio.run(s6.refresh(S(), feeds, st))
    assert [n["kind"] for n in new] == ["listing"]
    c = Candidate(coin_key="cg:cp", symbol="CP")
    s6.evaluate([c], st, cfg, now=NOW)
    assert c.stages["news"].score == 100 and "원화" in c.stages["news"].line


def test_같은_공지는_두_번_알리지_않는다():
    st = Store("sqlite:///:memory:")
    item = {"source": "upbit", "id": "upbit:1", "ts": NOW, "url": "u",
            "title": "커브(CRV) KRW, USDT 마켓 디지털 자산 추가"}
    assert asyncio.run(s6.refresh(S(), FakeFeeds([item]), st))
    assert asyncio.run(s6.refresh(S(), FakeFeeds([item]), st)) == []


def test_상폐_공지는_리스크_태그와_감점():
    st = Store("sqlite:///:memory:")
    asyncio.run(s6.refresh(S(), FakeFeeds([{"source": "binance", "id": "binance:9", "ts": NOW, "url": "u",
                                            "sub": "161", "title": "Binance Will Delist ICX, SCRT, STORJ on 2026-10-03"}]), st))
    c = Candidate(coin_key="cg:icx", symbol="ICX")
    s6.evaluate([c], st, cfg, now=NOW)
    assert "상폐예정" in c.risks and any(p == 30 for _, p in c.penalties)
    assert c.stages["news"].score is None          # 호재 없음 = 감점이 아니라 '데이터 없음'


def test_뉴스_없으면_None_재정규화에서_빠진다():
    st = Store("sqlite:///:memory:")
    c = Candidate(coin_key="cg:x", symbol="X")
    s6.evaluate([c], st, cfg, now=NOW)
    assert c.stages["news"].score is None and not c.penalties


def test_분류기가_꺼져_있으면_일반뉴스는_미분류로_남는다():
    st = Store("sqlite:///:memory:")
    asyncio.run(s6.refresh(S(), FakeFeeds([{"source": "cryptopanic", "id": "cp:1", "ts": NOW, "url": "u",
                                            "title": "X partners with Y", "symbols": ["X"]}]), st))
    rows = st.news_since(NOW - 60)
    assert rows[0].kind == "unclassified"


def test_하루_호출_상한을_넘으면_멈춘다():
    st = Store("sqlite:///:memory:")
    clf = NewsClassifier(S(), st)          # 키 없음 → client None
    for _ in range(2):
        st.log_llm("claude-opus-5", 100, 100, 0.001)
    ok, why = clf.budget_left()
    assert not ok and "호출 상한" in why


def test_구독_호출은_API_키를_지운_환경으로():
    """API 키가 섞이면 구독이 아니라 API 로 과금된다 — 강회장 지시 '과금 없이'."""
    import os
    from providers.claude_news import cli_args, cli_env
    os.environ["ANTHROPIC_API_KEY"] = "sk-should-not-leak"
    try:
        env = cli_env()
        assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    finally:
        del os.environ["ANTHROPIC_API_KEY"]
    args = cli_args("haiku", "x")
    assert "--bare" not in args                      # bare 는 구독 로그인을 안 읽는다
    assert "--strict-mcp-config" in args             # MCP 도구 정의가 실리면 토큰 13배
    assert args[args.index("--tools") + 1] == ""
