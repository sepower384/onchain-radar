"""공식 공지 규칙 분류 — LLM 없이 가를 수 있는 것은 규칙으로 가른다(싸고, 틀리지 않는다).

거래소가 **자기 채널에 올린 상장 공지**는 그 자체로 1차 출처다 = "검증 가능한 실질 호재".
규칙은 실제 제목을 보고 짰다(2026-09-21 업비트·바이낸스·비트겟 실측). 함정:
  - 업비트 "헤미(HEMI) 신규 거래지원 안내 … (헤미(HEMI) 거래지원 취소 안내)" — **취소**가 뒤에 붙는다
  - 업비트 "BTC, USDT 마켓 신규 거래지원 안내 (BICO, BMT, NIL, GWEI)" — 심볼이 괄호 목록
  - "(KRW, BTC, USDT 마켓)" 괄호는 마켓이지 심볼이 아니다
  - "(거래지원 개시 시점 변경 안내)" 같은 꼬리는 같은 상장의 **정정 공지** — 중복
  - 바이낸스 신규상장 카테고리에 bStocks(토큰화 주식)·선물 공지가 섞여 온다 — 현물 코인 상장만
  - 비트겟 선물·주식 무기한(Stock Perps)은 현물 상장이 아니다
"""
from __future__ import annotations

import re

MARKETS = {"KRW", "BTC", "USDT", "USDC", "ETH"}
SYM = re.compile(r"^[A-Z0-9][A-Z0-9.]{1,14}$")


def _paren_symbols(title: str) -> list[str]:
    out = []
    for grp in re.findall(r"\(([^()]*)\)", title):
        if "마켓" in grp or "안내" in grp or "시점" in grp or "/" in grp or ":" in grp:
            continue
        for tok in re.split(r"[,\s]+", grp.strip()):
            tok = tok.strip()
            if SYM.match(tok) and tok not in MARKETS:
                out.append(tok)
    return list(dict.fromkeys(out))


def classify(item: dict) -> dict | None:
    """공지 하나 → {kind, strength, exchange, symbols, market, why}. 해당 없으면 None.
    kind: listing(호재) · delisting · warning · cancel
    strength: strong(메이저 원화·바이낸스 현물) · medium · weak
    """
    src, t = item["source"], item.get("title") or ""
    if src == "upbit":
        return _upbit(t)
    if src == "binance":
        return _binance(t, item.get("sub"))
    if src == "bitget":
        return _bitget(t, item.get("sub"))
    return None


def _upbit(t: str) -> dict | None:
    syms = _paren_symbols(t)
    if not syms:
        return None
    if "취소" in t:
        # "헤미(HEMI), 유즈리스(USELESS) 신규 거래지원 안내 … (헤미(HEMI) 거래지원 취소 안내)"
        # → 취소된 건 HEMI 뿐, USELESS 는 실제로 상장됐다(2026-09-21 실측 오분류). 부분 취소를 가른다.
        cancelled = set(re.findall(r"\(([A-Z0-9.]{2,15})\)\s*거래지원 취소", t))
        rest = [s_ for s_ in syms if s_ not in cancelled]
        if cancelled and rest and ("신규 거래지원" in t or "디지털 자산 추가" in t):
            krw = "KRW" in t
            return {"kind": "listing", "strength": "strong" if krw else "medium", "exchange": "업비트",
                    "symbols": rest, "market": "KRW" if krw else "BTC/USDT",
                    "why": f"업비트 {'원화' if krw else 'BTC·USDT'} 마켓 신규 상장 ({', '.join(sorted(cancelled))} 은 취소)"}
        return {"kind": "cancel", "strength": "strong", "exchange": "업비트", "symbols": syms,
                "why": "업비트 거래지원 취소"}
    if "거래지원 종료" in t:
        return {"kind": "delisting", "strength": "strong", "exchange": "업비트", "symbols": syms,
                "why": "업비트 거래지원 종료(상장폐지)"}
    if "유의 종목" in t or "유의 촉구" in t:
        return {"kind": "warning", "strength": "strong", "exchange": "업비트", "symbols": syms,
                "why": "업비트 투자 유의 지정"}
    if "신규 거래지원" in t or "디지털 자산 추가" in t:
        krw = "KRW" in t
        # "BTC, USDT 마켓 신규 거래지원 안내 (A, B)" — 원화 마켓이 없으면 약하다
        return {"kind": "listing", "strength": "strong" if krw else "medium", "exchange": "업비트",
                "symbols": syms, "market": "KRW" if krw else "BTC/USDT",
                "why": f"업비트 {'원화' if krw else 'BTC·USDT'} 마켓 신규 상장"}
    return None


_BN_LIST = re.compile(r"^Binance Will List (.+?)(?: with .+)?$", re.I)
_BN_DELIST = re.compile(r"^Binance Will Delist (.+?) on \d{4}-\d{2}-\d{2}", re.I)


def _binance(t: str, catalog: str | None) -> dict | None:
    low = t.lower()
    if any(w in low for w in ("bstock", "stock", "futures", "perpetual", "alpha", "margin", "tradfi",
                              "collateral", "earn,", "delivery contract")):
        return None
    m = _BN_LIST.match(t.strip())
    if m:
        syms = _paren_symbols(t) or []
        if syms:
            seed = "seed tag" in low
            return {"kind": "listing", "strength": "strong", "exchange": "바이낸스", "symbols": syms,
                    "market": "spot", "why": "바이낸스 현물 신규 상장" + (" (시드 태그 — 변동성 큼)" if seed else "")}
    m = _BN_DELIST.match(t.strip())
    if m:
        syms = [s.strip() for s in re.split(r",|\band\b", m.group(1)) if SYM.match(s.strip())]
        if syms:
            return {"kind": "delisting", "strength": "strong", "exchange": "바이낸스", "symbols": syms,
                    "why": "바이낸스 상장폐지"}
    return None


_BG_SPOT = re.compile(r"Bitget (?:to list|will list|lists) (.+)", re.I)


def _bitget(t: str, sub: str | None) -> dict | None:
    if sub != "spot":
        return None
    if re.search(r"stock|perp|rCOMP|\br[A-Z]{2,}\b", t):
        return None
    if _BG_SPOT.search(t):
        syms = _paren_symbols(t)
        if syms:
            initial = "initial listing" in t.lower()
            return {"kind": "listing", "strength": "medium", "exchange": "비트겟", "symbols": syms,
                    "market": "spot", "why": "비트겟 현물 " + ("최초 상장" if initial else "상장")}
    return None
