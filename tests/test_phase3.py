"""Phase 3 — 스마트머니 반영, 가중치 튜닝(과적합 방지·덮어쓰기 파일)."""
from types import SimpleNamespace as NS

import pytest

from scanners.model import Candidate
from scanners.stage1_accumulation import evaluate
from storage.db import SignalReturn, Store
from tracking import tuner


def test_스마트머니_순매수가_카드와_점수에():
    c = Candidate(coin_key="x", symbol="X", mcap=100e6)
    evaluate(c, [], now=0, smart={"net_7d": 1_200_000, "net_24h": 3e5, "traders": 14})
    st = c.stages["accumulation"]
    assert st.score is not None and st.score > 50
    assert "스마트머니 7d 순매수 $1.2M, 14명" in st.line


class S:
    def __init__(self, cfg):
        self.cfg = cfg

    def get(self, k, d=None):
        cur = self.cfg
        for p in k.split("."):
            if not isinstance(cur, dict) or p not in cur:
                return d
            cur = cur[p]
        return cur


W = {"accumulation": 0.3, "reaction": 0.25, "derivatives": 0.15, "revenue": 0.15, "news": 0.1, "security": 0.05}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(tuner, "OVERRIDE", tmp_path / "o.yaml")
    monkeypatch.setattr(tuner, "SUGGEST", tmp_path / "s.yaml")
    return tmp_path


def seed(store, n, signal_fn):
    for i in range(n):
        stages, ret = signal_fn(i)
        sid = store.add_signal(ts=1e9 + i * 3600, coin_key=f"cg:{i}", symbol="X", track="main", kind="alert",
                               price=1, btc_price=1, score=50, signals_used=3, stages=stages)
        with store.session() as s:
            s.add(SignalReturn(signal_id=sid, horizon_days=7, ts=0, price=1, ret_pct=ret, excess_pct=ret))
            s.commit()


def test_표본이_적으면_제안하지_않는다(paths):
    st = Store("sqlite:///:memory:")
    seed(st, 10, lambda i: ({"reaction": 50}, 1.0))
    r = tuner.propose(st, S({"scoring": {"weights": W}}))
    assert r["ok"] is False and "표본" in r["why"]


def test_매출이_진짜_예측력이_있으면_매출_가중치를_올리자고_한다(paths):
    """매출 점수가 높을수록 수익이 난 세계 — 튜너가 그걸 찾아야 한다(학습·검증 양쪽에서)."""
    import random
    random.seed(7)

    def fn(i):
        rev = random.uniform(0, 100)
        rea = random.uniform(0, 100)
        return {"revenue": rev, "reaction": rea}, rev / 10 - 5 + random.uniform(-1, 1)

    st = Store("sqlite:///:memory:")
    seed(st, 120, fn)
    r = tuner.propose(st, S({"scoring": {"weights": W}, "tuning": {"min_edge_pct": 0.5}}))
    assert r["ok"] is True
    assert r["proposed"]["revenue"] > r["proposed"]["reaction"]
    assert tuner.apply_suggestion() is not None
    eff = tuner.effective_weights(S({"scoring": {"weights": W}}))
    assert eff["revenue"] == r["proposed"]["revenue"]
    assert tuner.reset() is True
    assert tuner.effective_weights(S({"scoring": {"weights": W}}))["revenue"] == 0.15


def test_아무_신호도_예측력이_없으면_유지(paths):
    import random
    random.seed(3)
    st = Store("sqlite:///:memory:")
    seed(st, 120, lambda i: ({"revenue": random.uniform(0, 100), "reaction": random.uniform(0, 100)},
                             random.uniform(-5, 5)))
    r = tuner.propose(st, S({"scoring": {"weights": W}, "tuning": {"min_edge_pct": 1.5}}))
    assert r["ok"] is False
