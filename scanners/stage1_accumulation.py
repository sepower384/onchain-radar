"""Stage 1. 온체인 매집 — 홀더 · 고래 순매수 · 거래소 순유출.

API 가 과거값을 주지 않으므로 **봇이 스냅샷을 쌓고 스냅샷끼리 비교**한다(사양서).
쌓이기 전에는 "데이터 수집 중" — 점수 None, **절대 추정값을 만들지 않는다.**

재료 (GoPlus, 무료) — 스냅샷마다
  holder_count · 상위 10 홀더 {주소, 총공급 대비 %, 컨트랙트 여부}
  → 각 주소를 거래소(exchange_wallets.yaml · Etherscan 공개 라벨) / 컨트랙트 / 소각 / 개인지갑 으로 분류

신호 (각각 있으면 쓰고, 없으면 빼고 평균)
  ① 홀더 증가      24h 증가율. 0% → 40점, +2% → 80점 · 상위10 집중도 하락 가점(±20)
  ② 고래 순매수    상위 10 중 **개인 지갑**의 비중 변화 합(%p) — 양쪽 스냅샷에 다 있는 지갑만
                   (사양서: 스마트머니 키가 없으면 "상위 비거래소 지갑 잔고 변화"로 대체)
  ③ 거래소 순유출  상위 10 중 **거래소 지갑**의 비중 변화 합. 줄면(= 개인 지갑으로 빠져나감) 가점
  ※ 거래소 지갑이 고래로 오판되지 않도록 분류를 먼저 한다(사양서 요구). 솔라나는 거래소 목록이 없다.

한계를 숨기지 않는다: 상위 10 지갑만 본다. 카드에도 "상위10 기준" 이라고 쓴다.
"""
from __future__ import annotations

import logging
import statistics
import time
from collections import defaultdict

from providers.exchange_wallets import classify

from .model import Candidate

log = logging.getLogger("stage1")

MIN_HISTORY_H = 20        # 이보다 짧으면 "24h 변화"라 부르지 않는다


def _nearest(rows, target):
    return min(rows, key=lambda r: abs(r.ts - target)) if rows else None


def _by_ts(tops) -> dict[float, dict[str, tuple[float, str]]]:
    g: dict[float, dict[str, tuple[float, str]]] = defaultdict(dict)
    for r in tops:
        g[r.ts][r.address] = (r.percent, r.kind)
    return g


def flow(tops, now: float, hours: float) -> dict | None:
    """상위 홀더 스냅샷 두 장(지금 · hours 전)으로 고래·거래소 비중 변화(%p)."""
    snaps = _by_ts(tops)
    if len(snaps) < 2:
        return None
    ts_sorted = sorted(snaps)
    new_ts = ts_sorted[-1]
    old_ts = min(ts_sorted, key=lambda t: abs(t - (now - hours * 3600)))
    if old_ts == new_ts or (new_ts - old_ts) / 3600 < hours * 0.8:
        return None
    old, new = snaps[old_ts], snaps[new_ts]
    both = set(old) & set(new)
    whale = sum((new[a][0] - old[a][0]) for a in both if new[a][1] == "wallet") * 100
    exch_addrs = {a for a in set(old) | set(new) if (new.get(a) or old.get(a))[1] == "exchange"}
    exch = sum((new[a][0] - old[a][0]) for a in exch_addrs & both) * 100
    return {"whale_pp": whale, "exchange_pp": exch, "hours": (new_ts - old_ts) / 3600,
            "exchange_seen": bool(exch_addrs), "whales": sum(1 for a in both if new[a][1] == "wallet")}


def _usd(pp: float, c: Candidate) -> str:
    cap = c.fdv or c.mcap
    if not cap:
        return ""
    v = abs(pp) / 100 * cap
    for unit, d in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= d:
            return f" (약 ${v / d:,.1f}{unit})"
    return f" (약 ${v:,.0f})"


def _usd_abs(v: float) -> str:
    a = abs(v)
    for unit, d in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if a >= d:
            return f"${a / d:,.1f}{unit}"
    return f"${a:,.0f}"


def evaluate(c: Candidate, history: list, now: float | None = None, tops: list | None = None,
             smart: dict | None = None) -> None:
    st = c.stage("accumulation")
    now = time.time() if now is None else now
    pts = [h for h in history if h.holder_count]
    parts: list[float] = []
    lines: list[str] = []

    # ① 홀더
    if len(pts) >= 2 and (now - pts[0].ts) / 3600 >= MIN_HISTORY_H:
        old, new = _nearest(pts, now - 86400), pts[-1]
        growth = (new.holder_count / old.holder_count - 1) * 100 if old.holder_count else 0.0
        s = 40 + max(-40.0, min(40.0, growth / 2.0 * 40))
        conc = ""
        if old.top10_pct is not None and new.top10_pct is not None:
            d = new.top10_pct - old.top10_pct
            s += max(-20.0, min(20.0, -d * 4))
            conc = f", 상위10 집중도 {d:+.1f}%p"
        parts.append(max(0.0, min(100.0, s)))
        lines.append(f"홀더 24h {growth:+.2f}% ({new.holder_count:,}명){conc}")
        st.detail["holder_growth_24h_pct"] = round(growth, 3)

    # ② ③ 고래 · 거래소 (24h, 가능하면 7d 도 같이)
    if tops:
        f24 = flow(tops, now, 24)
        f7 = flow(tops, now, 24 * 7)
        f = f7 or f24
        if f24 or f7:
            label = "7d" if f7 else "24h"
            w = f["whale_pp"]
            parts.append(max(0.0, min(100.0, 50 + w * 25)))
            lines.append(f"고래(상위10 개인지갑) {label} 순{'매수' if w >= 0 else '매도'} {w:+.2f}%p{_usd(w, c)}")
            st.detail["whale_pp"] = round(w, 4)
            if f["exchange_seen"]:
                e = f["exchange_pp"]
                parts.append(max(0.0, min(100.0, 50 - e * 25)))
                lines.append(f"거래소 지갑 {e:+.2f}%p ({'순유출' if e < 0 else '순유입'})")
                st.detail["exchange_pp"] = round(e, 4)

    # ④ 라벨된 스마트머니(Nansen, 키 있을 때) — 7d 순유입을 시총 대비 %로
    if smart and smart.get("net_7d") is not None:
        net = smart["net_7d"]
        cap = c.mcap or c.fdv
        rel = (net / cap * 100) if cap else None
        parts.append(max(0.0, min(100.0, 50 + (rel if rel is not None else (10 if net > 0 else -10)) * 50)))
        who = f", {smart['traders']}명" if smart.get("traders") else ""
        lines.insert(0, f"스마트머니 7d 순{'매수' if net >= 0 else '매도'} {_usd_abs(net)}{who}")
        st.detail["smart_net_7d"] = net

    if not parts:
        have = (now - pts[0].ts) / 3600 if pts else 0
        st.score, st.line = None, f"홀더 데이터 수집 중입니다 ({have:.0f}h / {MIN_HISTORY_H}h)"
        return
    st.score = round(statistics.mean(parts), 1)
    st.line = " · ".join(lines) + " 으로 보입니다"


def snapshot(store, c: Candidate) -> None:
    """GoPlus 가 준 홀더 값을 적재. 보안 조회 직후에 부른다."""
    sec = c.security
    if sec is None:
        return
    if getattr(sec, "holder_count", None) is not None:
        store.add_holder_snapshot(c.coin_key, sec.holder_count, sec.top10_pct)
    rows = []
    for h in (getattr(sec, "top_holders", None) or [])[:10]:
        addr = h.get("address") or h.get("account")
        try:
            pct = float(h.get("percent") or 0)
        except (TypeError, ValueError):
            continue
        if not addr:
            continue
        kind = classify(addr, str(h.get("is_contract")) == "1", c.chain)
        rows.append({"address": addr.lower() if c.chain != "solana" else addr, "percent": pct, "kind": kind})
    if rows:
        store.add_holder_top(c.coin_key, rows)


def run(cands: list[Candidate], store, smart_map: dict | None = None) -> None:
    week_ago = time.time() - 8 * 86400
    for c in cands:
        try:
            snapshot(store, c)
            key = (c.chain, c.address if c.chain == "solana" else (c.address or "").lower())
            evaluate(c, store.holder_history(c.coin_key, week_ago),
                     tops=store.holder_top_history(c.coin_key, week_ago),
                     smart=(smart_map or {}).get(key))
        except Exception as e:  # noqa: BLE001
            log.warning("Stage1 %s 실패: %s", c.symbol, e)
            c.stage("accumulation").score = None
