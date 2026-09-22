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


SIG_KO = {"accumulation": "매집", "reaction": "반응", "derivatives": "파생", "revenue": "매출",
          "news": "호재", "security": "안전", "mcap": "소형가점"}


def signal_reasons(c: Candidate) -> list[str]:
    """데이터로 확인된 상승 근거 — 추측 없이 우리가 잰 것만."""
    out = []
    r = c.stages.get("reaction")
    vr = (r.detail or {}).get("volume_ratio") if r else None
    if vr and vr >= 1.5:
        out.append(f"거래량이 평소(7일 평균)의 {vr:.1f}배")
    n = c.stages.get("news")
    if n and n.score is not None and n.line:
        out.append(n.line)
    rv = c.stages.get("revenue")
    ratio = (rv.detail or {}).get("ratio") if rv else None
    if ratio and ratio >= 1.5:
        out.append(f"프로토콜 매출이 30일 평균 대비 {(ratio - 1) * 100:+.0f}%")
    a = c.stages.get("accumulation")
    d = (a.detail or {}) if a else {}
    if (d.get("smart_net_7d") or 0) > 0:
        out.append("스마트머니 7일 순매수")
    if (d.get("whale_pp") or 0) > 0.1:
        out.append(f"상위 개인지갑 비중 {d['whale_pp']:+.2f}%p")
    if (d.get("exchange_pp") or 0) < -0.1:
        out.append(f"거래소 지갑에서 {abs(d['exchange_pp']):.2f}%p 순유출")
    if c.funding_pct is not None and c.funding_pct < 0 and c.bitget_futures:
        out.append("선물 펀딩비 음수(숏 우위 — 숏 청산 연료)")
    return out


def reason_block(c: Candidate) -> list[str]:
    e = escape
    L = []
    sig = signal_reasons(c)
    ai = (c.research or {}).get("why_up") or []
    if sig or ai:
        L.append("🚀 <b>오른 이유</b>")
        L += [f"  · {e(s)}" for s in sig]
        L += [f'  · {e(w["reason_ko"])} <a href="{e(w["source_url"])}">[출처]</a>' for w in ai]
    return L


def backer_block(c: Candidate) -> list[str]:
    e = escape
    r = c.research or {}
    backers, rounds = r.get("backers") or [], r.get("rounds") or []
    if not backers and not rounds:
        return []
    parts = []
    if r.get("total_raised_usd"):
        n = len([x for x in rounds if x.get("amount_usd")])
        und = f" · 비공개 {r['undisclosed_rounds']}건" if r.get("undisclosed_rounds") else ""
        parts.append(f"공개 누적 {money(r['total_raised_usd'])}({n}건{und})")
    if rounds:
        x = rounds[0]
        amt = f" {money(x['amount_usd'])}" if x.get("amount_usd") else ""
        lead = f", {', '.join(x['lead'][:2])}" if x.get("lead") else ""
        parts.append(f'최근 {e(x["round"])}{amt} ({e(x.get("date") or "")}{e(lead)}) '
                     f'<a href="{e(x["source_url"])}">[출처]</a>')
    L = ["💼 <b>백커</b> " + (" · ".join(e(b) for b in backers[:5]) if backers else "")]
    if parts:
        L.append("  " + " · ".join(parts))
    return L


def score_block(c: Candidate) -> str:
    if not c.breakdown:
        return ""
    parts = [f"{SIG_KO.get(n, n)} {v:.0f}×{w * 100:.0f}%={pts:.0f}" for n, v, w, pts in
             sorted(c.breakdown, key=lambda x: -x[3])]
    pen = sum(p for _, p in c.penalties)
    tail = f" − 감점 {pen:.0f}" if pen else ""
    return f"🧮 <b>점수 근거</b> {' + '.join(parts)}{tail} → <b>{c.score:.0f}</b>"


def cap_line(c: Candidate) -> str:
    if not (c.mcap or c.fdv):
        return ""
    s = f"💎 시총 {money(c.mcap)}" if c.mcap else "💎 시총 -"
    if c.fdv and c.mcap and c.fdv > c.mcap * 1.02:
        s += f" · FDV {money(c.fdv)} (유통 {c.mcap / c.fdv * 100:.0f}%)"
    if c.price:
        s += f" · 가격 {price_fmt(c.price)}"
    return s


def card(c: Candidate) -> str:
    e = escape
    L = []
    mc = c.mcap or c.fdv
    L.append(f"{grade(c.score)} <b>{e(c.symbol)}</b> | 종합 <b>{c.score:.0f}점</b> | "
             f"사용된 신호 {c.signals_used}/{c.signals_total} | 시총 {money(mc)} | CEX: {c.cex_label}")
    if c.chain and c.address:
        L.append(f"🔗 체인: {CHAIN_KO.get(c.chain, c.chain)} | CA: <code>{e(c.address)}</code>")
    cl = cap_line(c)
    if cl:
        L.append(cl)
    elif c.price:
        L.append(f"💵 가격 {price_fmt(c.price)}")
    L += reason_block(c)
    L += backer_block(c)
    sb = score_block(c)
    if sb:
        L.append(sb)

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
    cl = cap_line(c)
    if cl:
        L.append(cl)
    ai = (c.research or {}).get("why_up") or []
    if ai:
        L.append("📰 <b>최근 움직임</b>")
        L += [f'  · {e(w["reason_ko"])} <a href="{e(w["source_url"])}">[출처]</a>' for w in ai]
    L += backer_block(c)
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
