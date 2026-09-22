"""봇 조립·권한·카드 — 네트워크 없이."""
import asyncio

import pytest

from bot import telegram_app as T
from bot.buy_links import LinkBuilder
from bot.formatter import card_with_block_note, dip_card, links_for
from scanners.model import Candidate, StageResult
from tests.test_buy_links import REF, FakeSettings


class S(FakeSettings):
    def __init__(self, cfg, keys=None):
        super().__init__(cfg)
        self.keys = keys or {}

    def key(self, n):
        return self.keys.get(n)

    def has(self, n):
        return bool(self.keys.get(n))

    @property
    def allowed_chat_ids(self):
        return {111}

    @property
    def broadcast_chat_ids(self):
        return [111]


CFG = {**REF, "storage": {"url": "sqlite:///:memory:"},
       "telegram": {"briefing_times": ["08:00", "20:00"], "message_limit": 4000},
       "schedule": {"pool_ab_minutes": 20, "pool_c_minutes": 5, "tracking_minutes": 60},
       "stage8": {"scan_minutes": 60}}


def test_봇이_모든_명령어와_스케줄을_갖고_조립된다():
    app = T.build_app(S(CFG, {"TELEGRAM_BOT_TOKEN": "123456:TEST"}))
    cmds = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
    need = {"scan", "micro", "dip", "coin", "buy", "unlock", "revenue", "news", "watch",
            "unwatch", "watchlist", "stats", "settings", "start", "tune"}
    assert need <= cmds
    jobs = {j.name for j in app.job_queue.jobs()}
    assert {"briefing 08:00", "briefing 20:00", "main", "micro", "dip", "track",
            "news", "holders", "unlock", "tune"} <= jobs


def test_토큰이_없으면_시작하지_않는다():
    with pytest.raises(SystemExit):
        T.build_app(S(CFG, {}))


class Msg:
    def __init__(self):
        self.replies = []

    async def reply_text(self, t, **kw):
        self.replies.append(t)


class Chat:
    def __init__(self, i):
        self.id = i


class Upd:
    def __init__(self, cid):
        self.effective_chat = Chat(cid)
        self.effective_message = Msg()


class Ctx:
    def __init__(self, radar):
        self.application = type("A", (), {"bot_data": {"radar": radar}})()


def test_허용되지_않은_채팅은_차단():
    called = []

    @T.allowed
    async def h(update, ctx, radar):
        called.append(1)

    radar = type("R", (), {"s": S(CFG)})()
    u = Upd(999)
    asyncio.run(h(u, Ctx(radar)))
    assert not called and "허용되지 않은" in u.effective_message.replies[0]
    u2 = Upd(111)
    asyncio.run(h(u2, Ctx(radar)))
    assert called == [1]


def test_카드에_CA가_code_태그로_들어가_탭하면_복사된다():
    c = Candidate(coin_key="base:0xabc", symbol="AERO", chain="base",
                  address="0x940181a94A35A4569E4529A3CDfB74e38FD98631")
    c.stages["reaction"] = StageResult(score=80, line="거래량 3.4배 (아직 초입으로 보입니다)")
    c.score, c.signals_used = 82, 3
    ls = links_for(c, LinkBuilder(S(CFG)))
    txt = card_with_block_note(c, ls)
    assert "<code>0x940181a94A35A4569E4529A3CDfB74e38FD98631</code>" in txt
    assert "사용된 신호 3/6" in txt


def test_티커에_HTML_특수문자가_있어도_깨지지_않는다():
    c = Candidate(coin_key="x", symbol="<B&W>")
    c.score = 50
    txt = card_with_block_note(c, links_for(c, LinkBuilder(S(CFG))))
    assert "&lt;B&amp;W&gt;" in txt


def test_되돌림_카드():
    c = Candidate(coin_key="cg:x", symbol="TRUMP", bitget_spot="TRUMPUSDT")
    c.stages["retrace"] = StageResult(score=90, line="고점 대비 −41%",
                                      detail={"fib": "0.618 $2.2", "confirm": "+18% 반등",
                                              "invalid": "$1.6 이탈 시 무효"})
    c.score = 90
    t = dip_card(c)
    assert "피보나치" in t and "무효화" in t and "반등 확인" in t


def test_카드에_오른이유_백커_시총_점수근거():
    from scanners.research import clean
    from scoring.engine import score
    c = Candidate(coin_key="cg:zama", symbol="ZAMA", mcap=234e6, fdv=1072e6, price=0.0948,
                  bitget_spot="ZAMAUSDT")
    c.stages["reaction"] = StageResult(score=80, line="x", detail={"volume_ratio": 2.4})
    c.stages["derivatives"] = StageResult(score=70, line="y")
    score(c, {"reaction": 0.25, "derivatives": 0.15})
    c.research = clean({
        "why_up": [{"reason_ko": "FHE 섹터 자금 유입", "source_url": "https://ex.com/a"},
                   {"reason_ko": "출처 없는 주장", "source_url": ""}],
        "rounds": [{"round": "Series B", "amount_usd": 57e6, "date": "2025-06", "lead": ["Pantera"],
                    "source_url": "https://ex.com/b"},
                   {"round": "Series A", "amount_usd": 73e6, "date": "2024-03", "lead": ["Multicoin"],
                    "source_url": "https://ex.com/c"},
                   {"round": "가짜", "amount_usd": 999e6, "date": "2024-01", "lead": [], "source_url": ""}],
        "backers": ["Pantera Capital", "Multicoin Capital"]})
    t = card_with_block_note(c, links_for(c, LinkBuilder(S(CFG))))
    assert "거래량이 평소(7일 평균)의 2.4배" in t
    assert "FHE 섹터 자금 유입" in t and "출처 없는 주장" not in t      # 출처 없으면 버린다
    assert "공개 누적 $130.0M(2건)" in t                                 # 합계는 코드가 — AI 숫자 안 믿음
    assert "Pantera Capital" in t and "최근 Series B $57.0M" in t
    assert "FDV $1.1B (유통 22%)" in t
    assert "점수 근거" in t and "반응 80×62%" in t
