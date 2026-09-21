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
