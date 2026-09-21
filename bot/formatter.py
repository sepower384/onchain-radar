"""코인 카드 — 세력 말투("~입니다 / ~으로 보입니다"). 텔레그램 HTML.

  🟢 [티커] | 종합 82점 | 사용된 신호 5/6 | 시총 $xxM | CEX: 비트겟 선물 O
  🔗 체인: Base | CA: 0x1234…abcd
  💰 자금흐름: …
  📈 반응: …
  🌡️ 파생: …
  📊 매출: …
  📰 실질 호재: …
  ⚠️ 리스크: …
  [버튼들]

모든 브리핑 하단에 "투자 권유가 아닌 데이터 추적 결과입니다" 고정.
CA 는 <code> 로 감싼다 — 텔레그램에서 탭하면 전체 주소가 복사된다(사양서 요구).
"""
from __future__ import annotations

from html import escape

from scanners.model import Candidate
from settings import fmt_kst, now_kst

from .buy_links import LinkBuilder, LinkSet, short_addr

DISCLAIMER = "<i>투자 권유가 아닌 데이터 추적 결과입니다.</i>"
MICRO_BANNER = "🚨 <b>초고위험</b> · CEX 미상장 DEX 전용 토큰입니다. 유동성이 얕아 한 번에 크게 움직입니다."

CHAIN_KO = {"ethereum": "Ethereum", "base": "Base", "solana": "Solana", "bsc": "BSC",
            "arbitrum": "Arbitrum", "polygon": "Polygon", "optimism": "Optimism",
            "avalanche": "Avalanche", "sui": "Sui", "tron": "Tron"}


def money(v: float | None) -> str:
    if v is None:
        return "-"
    for unit, d in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= d:
            return f"${v / d:,.1f}{unit}"
    return f"${v:,.0f}"


def price_fmt(v: float | None) -> str:
    if v is None:
        return "-"
    if v >= 1:
        return f"${v:,.4g}" if v < 1000 else f"${v:,.0f}"
    return f"${v:.6g}"


def grade(score: float) -> str:
    return "🟢" if score >= 75 else ("🟡" if score >= 55 else "⚪")


def card(c: Candidate) -> str:
    e = escape
    L = []
    mc = c.mcap or c.fdv
    L.append(f"{grade(c.score)} <b>{e(c.symbol)}</b> | 종합 <b>{c.score:.0f}점</b> | "
             f"사용된 신호 {c.signals_used}/{c.signals_total} | 시총 {money(mc)} | CEX: {c.cex_label}")
    if c.chain and c.address:
        L.append(f"🔗 체인: {CHAIN_KO.get(c.chain, c.chain)} | CA: <code>{e(c.address)}</code>")
    elif c.price:
        L.append(f"💵 가격 {price_fmt(c.price)}")

    def line(icon, label, key):
        st = c.stages.get(key)
        if st and st.line:
            L.append(f"{icon} {label}: {e(st.line)}")

    line("💰", "자금흐름", "accumulation")
    line("📈", "반응", "reaction")
    line("🌡️", "파생", "derivatives")
    line("📊", "매출", "revenue")
    line("📰", "실질 호재", "news")
    line("🛡️", "안전", "security")

    tags = [t for t in c.tags if t]
    if tags:
        L.append("🏷️ " + " · ".join(e(t) for t in dict.fromkeys(tags)))
    risk = []
    if c.risks:
        risk.append(", ".join(sorted(c.risks)))
    risk += [f"{e(r)} −{p:.0f}" for r, p in c.penalties]
    if risk:
        L.append("⚠️ 리스크: " + " · ".join(risk))
    return "\n".join(L)


def dip_card(c: Candidate) -> str:
    """Stage 8 되돌림 카드."""
    e = escape
    st = c.stages.get("retrace")
    d = (st.detail if st else {}) or {}
    L = [f"📐 <b>{e(c.symbol)}</b> | 되돌림 {c.score:.0f}점 | 시총 {money(c.mcap or c.fdv)} | CEX: {c.cex_label}"]
    if st:
        L.append(f"📉 {e(st.line)}")
    if d.get("fib"):
        L.append(f"📏 피보나치: {e(d['fib'])}")
    if d.get("confirm"):
        L.append(f"🔁 반등 확인: {e(d['confirm'])}")
    if d.get("invalid"):
        L.append(f"⛔ 무효화: {e(d['invalid'])}")
    tags = [t for t in dict.fromkeys(c.tags) if t]
    if tags:
        L.append("🏷️ " + " · ".join(e(t) for t in tags))
    return "\n".join(L)


def links_for(c: Candidate, lb: LinkBuilder) -> LinkSet:
    return lb.build(bitget_spot=c.bitget_spot, bitget_futures=c.bitget_futures,
                    chain=c.chain, address=c.address, pair_url=c.pair_url,
                    security=c.security, microcap=(c.track == "micro"))


def card_with_block_note(c: Candidate, ls: LinkSet) -> str:
    txt = card(c)
    if ls.blocked_reason:
        txt += f"\n🚨 구매 링크 비활성화: {escape(ls.blocked_reason)}"
    return txt


def header(title: str, sub: str = "") -> str:
    t = now_kst()
    s = f"🛰 <b>{escape(title)}</b> · {t.month}/{t.day} {t:%H:%M} KST"
    return s + (f"\n{escape(sub)}" if sub else "")


def revenue_table(rows: list[dict], n: int = 10) -> str:
    if not rows:
        return "매출 급증 프로토콜이 없습니다."
    out = []
    for i, r in enumerate(rows[:n], 1):
        sym = f" ({r['symbol']})" if r.get("symbol") else ""
        out.append(f"{i}. <b>{escape(r.get('name') or '?')}</b>{escape(sym)} — "
                   f"7d 매출 {money(r.get('rev_7d'))}, 30d 평균 대비 {(r['ratio'] - 1) * 100:+.0f}%")
    return "\n".join(out)


__all__ = ["dip_card", "card", "card_with_block_note", "links_for", "header", "revenue_table",
           "DISCLAIMER", "MICRO_BANNER", "money", "short_addr", "fmt_kst"]
