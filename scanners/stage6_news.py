"""Stage 6. '실질적' 호재 브리핑.

1차 출처 우선
  거래소 공식 공지(업비트·바이낸스·비트겟)는 **규칙**으로 가른다 — 키·비용 0, 그 자체가 검증된 출처.
  일반 뉴스(CryptoPanic)는 **Claude** 로 가른다 — ANTHROPIC_API_KEY 와 stage6.enabled 가 있을 때만.
  상대방 확인이 안 된 파트너십은 카드에 "교차 확인 안 됨"을 붙인다(사양서).

카드 반영
  호재  : lookback_hours(72h) 안의 실질 호재가 그 심볼에 있으면
          강(업비트 원화·바이낸스 현물) 100 · 중(업비트 BTC/USDT·비트겟 현물) 75 · 일반 뉴스 실질 70(상대방 확인 +10)
          없으면 None — 뉴스가 없다고 감점하지 않는다(재정규화에서 빠짐)
  리스크: risk_lookback_days(14일) 안의 상폐·거래지원 종료·유의 지정 → 리스크 태그 + 감점
          (새 리스크는 쿨다운을 뚫고 재알림된다 — storage.should_send)
"""
from __future__ import annotations

import logging
import time

from providers.announcements import BinanceCMS, BitgetAnn, CryptoPanic, Upbit
from providers.claude_news import NewsClassifier

from .model import Candidate
from .news_rules import classify

log = logging.getLogger("stage6")

STRENGTH_SCORE = {"strong": 100.0, "medium": 75.0, "weak": 55.0}
RISK_KINDS = ("delisting", "warning")


class NewsFeeds:
    def __init__(self, settings):
        self.upbit, self.binance = Upbit(settings), BinanceCMS(settings)
        self.bitget, self.cp = BitgetAnn(settings), CryptoPanic(settings)

    async def close(self):
        for p in (self.upbit, self.binance, self.bitget, self.cp):
            await p.close()

    async def fetch(self) -> list[dict]:
        out: list[dict] = []
        for coro in (self.upbit.notices(), self.binance.articles(48), self.binance.articles(161),
                     self.bitget.listings(), self.cp.posts()):
            try:
                out += await coro or []
            except Exception as e:  # noqa: BLE001 — 한 거래소 공지가 막혀도 나머지는 받는다
                log.warning("공지 수집 실패: %s", e)
        return out


async def refresh(settings, feeds: NewsFeeds, store, classifier: NewsClassifier | None = None) -> list[dict]:
    """새 공지·뉴스를 분류해 저장하고, **새로 들어온 것**만 돌려준다(알림용)."""
    items = await feeds.fetch()
    seen = store.news_seen([i["id"] for i in items])
    new = [i for i in items if i["id"] not in seen]
    if not new:
        return []
    rows, general = [], []
    for it in new:
        r = classify(it)
        if r:
            rows.append(_row(it, r["kind"], r.get("strength"), r.get("exchange"), r["symbols"], r["why"], 1))
        elif it["source"] == "cryptopanic":
            general.append(it)
        # 규칙에 안 걸린 거래소 공지(주식·선물 등)는 '본 것'으로만 남긴다
        else:
            rows.append(_row(it, "ignored", None, None, [], None, 0))

    if general:
        clf = classifier or NewsClassifier(settings, store)
        verdicts = await clf.classify(general) if clf.enabled else {}
        for it in general:
            v = verdicts.get(it["id"])
            if v is None:
                rows.append(_row(it, "pending" if clf.enabled else "unclassified", None, None,
                                 it.get("symbols") or [], None, 0))
                continue
            syms = [s.upper() for s in (v.get("symbols") or it.get("symbols") or [])]
            why = v.get("reason_ko") or ""
            if v["verdict"] == "real" and v["category"] == "partnership" and not v.get("counterparty_confirmed"):
                why += " (상대방 공식 발표 교차 확인 안 됨)"
            rows.append(_row(it, "real" if v["verdict"] == "real" else "noise", "medium", None, syms,
                             f"[{v['category']}] {why}", int(bool(v.get("counterparty_confirmed")))))
    store.add_news(rows)
    log.info("공지·뉴스 신규 %d건 (공식 규칙 %d · 일반 %d)", len(rows), len(rows) - len(general), len(general))
    return [r for r in rows if r["kind"] in ("listing", "real", "delisting", "warning", "cancel")]


def _row(it, kind, strength, exchange, symbols, why, verified) -> dict:
    return {"id": it["id"], "source": it["source"], "ts": it["ts"], "title": it["title"][:500],
            "url": it.get("url"), "kind": kind, "strength": strength, "exchange": exchange,
            "symbols": ",".join(dict.fromkeys(s.upper() for s in symbols)), "why": why, "verified": verified}


def evaluate(cands: list[Candidate], store, cfg, now: float | None = None) -> None:
    now = time.time() if now is None else now
    good = store.news_since(now - cfg("stage6.lookback_hours", 72) * 3600, ("listing", "real"))
    bad = store.news_since(now - cfg("stage6.risk_lookback_days", 14) * 86400, RISK_KINDS)
    by_sym_good: dict[str, list] = {}
    by_sym_bad: dict[str, list] = {}
    for n in good:
        for s in filter(None, n.symbols.split(",")):
            by_sym_good.setdefault(s, []).append(n)
    for n in bad:
        for s in filter(None, n.symbols.split(",")):
            by_sym_bad.setdefault(s, []).append(n)

    for c in cands:
        st = c.stage("news")
        hits = by_sym_good.get(c.symbol.upper(), [])
        if hits:
            best = max(hits, key=lambda n: (STRENGTH_SCORE.get(n.strength or "", 60)
                                            + (10 if n.kind == "real" and n.verified else 0), n.ts))
            score = STRENGTH_SCORE.get(best.strength or "", 60.0) if best.kind == "listing" else \
                70.0 + (10 if best.verified else 0)
            st.score = min(100.0, score)
            src = best.exchange or "뉴스"
            st.line = f"{best.why or best.title[:60]}"
            st.detail = {"url": best.url, "source": src, "title": best.title}
        else:
            st.score = None
        for n in by_sym_bad.get(c.symbol.upper(), []):
            tag = "상폐예정" if n.kind == "delisting" else "투자유의"
            c.penalize(f"{n.exchange or ''} {n.why or tag}".strip(), float(cfg("stage6.risk_penalty", 30)),
                       risk_tag=tag)
