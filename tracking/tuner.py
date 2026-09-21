"""Phase 3 — 성과 데이터로 가중치 튜닝.

흐름
  1) 매주(일요일 21시) 쌓인 신호·수익률로 격자 탐색 → 현재 가중치보다 나은 조합을 **제안**만 한다
  2) 강회장이 /tune 으로 보고, /tune apply 로 적용 → data/weights_override.yaml 에 쓴다
  3) 점수 엔진은 config.yaml 가중치 위에 이 파일을 덮어 쓴다. 되돌리기 = /tune reset (파일 삭제)
config.yaml 은 절대 자동으로 고치지 않는다 — 사람이 쓴 기본값과 기계가 찾은 값을 섞지 않기 위해서.

과적합 방지
  - 표본 min_samples(기본 50) 미만이면 제안하지 않는다
  - 학습(앞 70%) · 검증(뒤 30%)을 **시간순**으로 나눠, 검증 구간에서도 이겨야 제안한다
  - 개선폭이 min_edge_pct 미만이면 제안하지 않는다(노이즈)
"""
from __future__ import annotations

import itertools
import statistics
from pathlib import Path

import yaml
from sqlalchemy import select

from settings import ROOT
from storage.db import Signal, SignalReturn

OVERRIDE = ROOT / "data" / "weights_override.yaml"
SUGGEST = ROOT / "data" / "weights_suggested.yaml"
SIGS = ("accumulation", "reaction", "derivatives", "revenue", "news", "security")
GRID = (0.0, 0.1, 0.2, 0.3, 0.4)


def effective_weights(settings) -> dict[str, float]:
    base = dict(settings.get("scoring.weights", {}) or {})
    try:
        over = yaml.safe_load(OVERRIDE.read_text(encoding="utf-8")) or {}
        base.update({k: float(v) for k, v in (over.get("weights") or {}).items() if k in SIGS})
    except FileNotFoundError:
        pass
    return base


def _rescore(stages: dict, w: dict) -> float:
    num = den = 0.0
    for k in SIGS:
        v = stages.get(k)
        if v is None or w.get(k, 0) <= 0:
            continue
        num += w[k] * v
        den += w[k]
    return num / den if den else 0.0


def _top_mean(rows, w, frac) -> float:
    ranked = sorted(rows, key=lambda r: -_rescore(r[1], w))
    k = max(3, int(len(ranked) * frac))
    return statistics.mean(y for _, _, y in ranked[:k])


def samples(store, horizon: int, metric: str = "excess"):
    with store.session() as s:
        sigs = {g.id: g for g in s.scalars(select(Signal).where(Signal.track == "main"))}
        rets = list(s.scalars(select(SignalReturn).where(SignalReturn.horizon_days == horizon)))
    rows = []
    for r in rets:
        g = sigs.get(r.signal_id)
        y = r.excess_pct if metric == "excess" else r.ret_pct
        if g and y is not None:
            rows.append((g.ts, g.stages, y))
    return sorted(rows, key=lambda x: x[0])


def propose(store, settings) -> dict:
    h = int(settings.get("tuning.horizon_days", 7))
    frac = float(settings.get("tuning.top_frac", 0.3))
    rows = samples(store, h, settings.get("tuning.metric", "excess"))
    need = int(settings.get("tuning.min_samples", 50))
    if len(rows) < need:
        return {"ok": False, "why": f"표본 {len(rows)}건 < {need}건 — 아직 튜닝하지 않습니다", "n": len(rows)}
    cut = int(len(rows) * 0.7)
    train, test = rows[:cut], rows[cut:]
    cur = effective_weights(settings)
    best = None
    for combo in itertools.product(GRID, repeat=4):
        if not any(combo):
            continue
        w = {**cur, "reaction": combo[0], "derivatives": combo[1], "revenue": combo[2], "accumulation": combo[3]}
        sc = _top_mean(train, w, frac)
        if best is None or sc > best[0]:
            best = (sc, w)
    cur_train, cur_test = _top_mean(train, cur, frac), _top_mean(test, cur, frac)
    new_test = _top_mean(test, best[1], frac)
    edge = float(settings.get("tuning.min_edge_pct", 1.0))
    res = {"n": len(rows), "horizon": h, "current": cur, "proposed": best[1],
           "train": {"current": cur_train, "proposed": best[0]},
           "test": {"current": cur_test, "proposed": new_test}}
    if new_test - cur_test < edge or best[0] - cur_train < edge:
        res.update(ok=False, why=f"검증 구간 개선 {new_test - cur_test:+.2f}%p — 기준({edge}%p) 미달, 유지합니다")
        return res
    res.update(ok=True, why=f"검증 구간 상위 {frac:.0%} 평균 {cur_test:+.2f}% → {new_test:+.2f}%")
    SUGGEST.parent.mkdir(parents=True, exist_ok=True)
    SUGGEST.write_text(yaml.safe_dump({"weights": best[1], "evidence": res["why"], "n": len(rows)},
                                      allow_unicode=True, sort_keys=False), encoding="utf-8")
    return res


def apply_suggestion() -> dict | None:
    try:
        sug = yaml.safe_load(SUGGEST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    OVERRIDE.write_text(yaml.safe_dump({"weights": sug["weights"], "source": sug.get("evidence")},
                                       allow_unicode=True, sort_keys=False), encoding="utf-8")
    return sug


def reset() -> bool:
    if OVERRIDE.exists():
        OVERRIDE.unlink()
        return True
    return False


def text(res: dict) -> str:
    from html import escape
    if not res.get("proposed"):
        return f"🎛 가중치 튜닝 — {escape(res['why'])}"
    names = ("reaction", "derivatives", "revenue", "accumulation")
    cur = " ".join(f"{n}={res['current'].get(n, 0)}" for n in names)
    new = " ".join(f"{n}={res['proposed'].get(n, 0)}" for n in names)
    L = [f"🎛 <b>가중치 튜닝</b> (표본 {res['n']}건 · {res['horizon']}일 BTC 대비 초과수익)",
         f"현재: {cur}", f"제안: {new}",
         f"학습 {res['train']['current']:+.2f}% → {res['train']['proposed']:+.2f}% · "
         f"검증 {res['test']['current']:+.2f}% → {res['test']['proposed']:+.2f}%",
         escape(res["why"])]
    if res.get("ok"):
        L.append("적용하려면 /tune apply · 되돌리기 /tune reset")
    return "\n".join(L)
