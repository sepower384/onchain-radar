"""성과 추적 — 가격을 못 구하면 누락이 아니라 −100%, BTC 대비 초과수익."""
import asyncio

from storage.db import Store
from tracking import tracker


class FakeCG:
    def __init__(self, prices):
        self.prices = prices

    async def markets_by_ids(self, ids):
        return [{"id": i, "current_price": self.prices[i]} for i in ids if self.prices.get(i) is not None]


class FakeDS:
    async def best_pair(self, chain, addr):
        return None                     # 유동성 소멸 — 페어가 사라졌다


class FakeBG:
    async def markets(self):
        return {}


class P:
    def __init__(self, prices):
        self.coingecko, self.dexscreener, self.bitget = FakeCG(prices), FakeDS(), FakeBG()


class S:
    def get(self, k, d=None):
        return {"tracking.horizons_days": [1, 3], "tracking.dead_return_pct": -100}.get(k, d)


def test_수익률_BTC_초과_그리고_소멸은_마이너스100():
    st = Store("sqlite:///:memory:")
    t0 = 1_800_000_000.0
    st.add_signal(ts=t0, coin_key="cg:good", symbol="GOOD", track="main", kind="alert", price=10.0,
                  btc_price=100.0, score=80, signals_used=3, stages={"reaction": 80})
    st.add_signal(ts=t0, coin_key="solana:DEAD", symbol="DEAD", track="micro", kind="alert", price=1.0,
                  btc_price=100.0, score=70, signals_used=3, stages={"reaction": 70})
    prices = {"good": 12.0, "bitcoin": 110.0}
    n = asyncio.run(tracker.run_once(S(), st, P(prices), now=t0 + 1.5 * 86400))
    assert n == 2                                   # 1일만 도래, 3일은 아직

    s = tracker.stats(st)
    h1 = s["horizons"][1]
    assert h1["n"] == 2 and h1["dead"] == 1
    assert h1["worst"] == -100                      # 누락되지 않고 −100 으로 남았다
    main = tracker.stats(st, "main")["horizons"][1]
    assert round(main["avg"], 1) == 20.0            # 10 → 12
    assert round(main["avg_excess"], 1) == 10.0     # BTC +10% 대비 +10%p


def test_같은_기간을_두_번_기록하지_않는다():
    st = Store("sqlite:///:memory:")
    t0 = 1_800_000_000.0
    st.add_signal(ts=t0, coin_key="cg:good", symbol="GOOD", track="main", kind="alert", price=10.0,
                  btc_price=100.0, score=80, signals_used=3, stages={})
    p = P({"good": 11.0, "bitcoin": 100.0})
    asyncio.run(tracker.run_once(S(), st, p, now=t0 + 2 * 86400))
    assert asyncio.run(tracker.run_once(S(), st, p, now=t0 + 2.5 * 86400)) == 0


def test_쿨다운_6시간_그리고_새_리스크면_예외():
    st = Store("sqlite:///:memory:")
    kw = dict(cooldown_h=6, bypass_jump=12)
    assert st.should_send("cg:x", 80, set(), now=0, **kw)
    st.mark_sent("cg:x", 80, set(), now=0)
    assert not st.should_send("cg:x", 82, set(), now=3600, **kw)          # 6시간 안 · 비슷한 점수
    assert st.should_send("cg:x", 95, set(), now=3600, **kw)              # 점수 급등 예외
    assert st.should_send("cg:x", 80, {"과열"}, now=3600, **kw)          # 새 리스크 예외
    assert st.should_send("cg:x", 80, set(), now=7 * 3600, **kw)          # 쿨다운 끝
