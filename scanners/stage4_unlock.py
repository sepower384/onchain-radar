"""Stage 4. 토큰 언락 — **제외가 아니라 알림**. TOKENOMIST_API_KEY 있을 때만 켜진다.

카드   : 14일 안에 언락이 있으면 "⚠️ 언락 D-5, 유통량의 4.2% (VC·팀)" 경고 + 비중만큼 감점
         (big_unlock_pct 이상일 때 유통량 1% 당 penalty_per_pct). 리스크 태그 "언락"
알림   : D-7 · D-3 · D-1 (alert_days) 에, 비중이 big_unlock_pct 이상인 언락을 한 번씩
/unlock: 14일 캘린더

코인 매칭: Tokenomist tokenId 가 코인게코 id 와 같으면 그걸로, 아니면 심볼로(동명 코인 주의 — 카드에만 쓴다).
"""
from __future__ import annotations

import logging
import time

from providers.tokenomist import recipients_ko

from .model import Candidate

log = logging.getLogger("stage4")


def days_left(ts: float, now: float | None = None) -> int:
    now = time.time() if now is None else now
    return max(0, int((ts - now) // 86400))


def evaluate(cands: list[Candidate], unlocks: list[dict] | None, cfg, now: float | None = None) -> None:
    if not unlocks:
        return
    by_id = {u["token_id"]: u for u in unlocks if u.get("token_id")}
    by_sym: dict[str, dict] = {}
    for u in unlocks:
        if u.get("symbol") and (u["symbol"] not in by_sym or (u.get("mcap") or 0) > (by_sym[u["symbol"]].get("mcap") or 0)):
            by_sym[u["symbol"]] = u
    big = float(cfg("stage4.big_unlock_pct", 2.0))
    per = float(cfg("stage4.penalty_per_pct", 3))
    for c in cands:
        u = by_id.get(c.cg_id or "") or by_sym.get(c.symbol.upper())
        if not u or u.get("ts") is None or u.get("pct") is None:
            continue
        d = days_left(u["ts"], now)
        who = recipients_ko(u.get("recipients") or [])
        note = f"언락 D-{d}, 유통량의 {u['pct']:.1f}%" + (f" ({who})" if who else "")
        c.tags.append(f"⚠️ {note}")
        if u["pct"] >= big:
            c.penalize(note, round(u["pct"] * per, 1), risk_tag="언락")


def due_alerts(unlocks: list[dict] | None, cfg, now: float | None = None) -> list[tuple[dict, int]]:
    """오늘 D-7/3/1 에 해당하는 큰 언락 (unlock, D)."""
    days = set(int(x) for x in cfg("stage4.alert_days", [7, 3, 1]))
    big = float(cfg("stage4.big_unlock_pct", 2.0))
    out = []
    for u in unlocks or []:
        if u.get("ts") is None or (u.get("pct") or 0) < big:
            continue
        d = days_left(u["ts"], now)
        if d in days:
            out.append((u, d))
    return sorted(out, key=lambda x: (x[1], -(x[0].get("pct") or 0)))


def calendar_text(unlocks: list[dict] | None, n: int = 25) -> str:
    from html import escape

    from settings import fmt_kst
    if unlocks is None:
        return "언락 데이터를 불러오지 못했습니다."
    rows = sorted([u for u in unlocks if u.get("ts")], key=lambda u: u["ts"])[:n]
    if not rows:
        return "14일 안에 큰 언락이 없습니다."
    out = []
    for u in rows:
        who = recipients_ko(u.get("recipients") or [], 3)
        val = f" · ${u['value_usd'] / 1e6:,.1f}M" if u.get("value_usd") else ""
        out.append(f"{fmt_kst(u['ts'], '%m/%d')} D-{days_left(u['ts'])} <b>{escape(u['symbol'])}</b> "
                   f"유통량 {u['pct'] or 0:.1f}%{val}" + (f" ({escape(who)})" if who else ""))
    return "\n".join(out)
