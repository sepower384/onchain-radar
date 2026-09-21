"""구매 링크 — 모든 코인 카드의 버튼은 여기서만 만든다.

우선순위 (사양서 + 강회장 지시 "코인 매수 거래소는 비트겟")
  1. 비트겟 상장 → [🛒 비트겟 현물] [📈 비트겟 선물]   ← 레퍼럴 W63KTT
  2. DEX 거래 가능 → 체인별 DEX 스왑 (유동성 가장 큰 페어 기준)
  3. 공통 → [📊 차트] [🔍 컨트랙트]

안전 규칙 (위반하면 가짜 토큰 오매수로 이어진다)
  - DEX 링크는 **체인 + 컨트랙트 주소**로만 만든다. 티커·이름 검색 링크는 절대 만들지 않는다.
  - 비트겟 링크는 **API 로 상장·거래 가능을 확인한 심볼**에만 만든다.
    (bitget.com 은 SPA 라 `spot/ZZZZNOTREALUSDT` 에도 200 을 준다 — 페이지로는 검증 불가.)
  - GoPlus 가 허니팟·매도 불가·고세금을 잡으면 **구매 버튼을 아예 만들지 않는다.**
  - 마이크로캡 카드의 구매 라벨에는 경고를 붙인다.

URL 형식은 전부 실제로 확인했다(docs/DESIGN.md §3).
  비트겟  `partner.bitget.com/bg/W63KTT` → `channelCode=yrbp&vipCode=m6v2` (리다이렉트 추적)
  Jupiter `jup.ag/swap?sell=<SOL>&buy=<민트>` — 서버 렌더 HTML 이 민트를 인식
  Uniswap `app.uniswap.org/swap?chain=<체인>&inputCurrency=NATIVE&outputCurrency=<주소>` — 코인게코 trade_url 과 동일
  Pancake `pancakeswap.finance/swap?chain=bsc&outputCurrency=<주소>` — 코인게코 trade_url 과 동일
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode

SOL_MINT = "So11111111111111111111111111111111111111112"

# Uniswap 은 체인 이름이 제각각이다. BSC 는 "bnb" 로 부른다(explore/tokens/bnb/… 실측).
UNISWAP_CHAIN = {"ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum",
                 "optimism": "optimism", "polygon": "polygon", "blast": "blast"}

EXPLORER = {
    "ethereum": "https://etherscan.io/token/{a}",
    "base": "https://basescan.org/token/{a}",
    "arbitrum": "https://arbiscan.io/token/{a}",
    "bsc": "https://bscscan.com/token/{a}",
    "polygon": "https://polygonscan.com/token/{a}",
    "optimism": "https://optimistic.etherscan.io/token/{a}",
    "avalanche": "https://snowtrace.io/token/{a}",
    "linea": "https://lineascan.build/token/{a}",
    "blast": "https://blastscan.io/token/{a}",
    "solana": "https://solscan.io/token/{a}",
    "sui": "https://suiscan.xyz/mainnet/coin/{a}",
    "tron": "https://tronscan.org/#/token20/{a}",
}

EVM_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOL_ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")   # base58


@dataclass
class Button:
    text: str
    url: str


@dataclass
class LinkSet:
    rows: list[list[Button]]
    blocked_reason: str | None = None     # 구매 버튼을 막은 이유(스캠 플래그)

    @property
    def buttons(self) -> list[Button]:
        return [b for row in self.rows for b in row]


def valid_address(chain: str | None, address: str | None) -> bool:
    """주소 모양이 체인에 맞는가. 틀린 주소로 링크를 만들면 엉뚱한 토큰이 열린다."""
    if not chain or not address:
        return False
    if chain == "solana":
        return bool(SOL_ADDR.match(address))
    if chain in ("sui", "tron"):
        return len(address) > 20
    return bool(EVM_ADDR.match(address))


def short_addr(address: str | None) -> str:
    """0x1234…abcd — 카드 표시용."""
    if not address:
        return "-"
    return address if len(address) <= 12 else f"{address[:6]}…{address[-4:]}"


class LinkBuilder:
    """설정(레퍼럴·URL 틀)을 한 번 받아 두고 카드마다 버튼을 찍어 낸다."""

    def __init__(self, settings):
        self.s = settings
        self.spot_tpl = settings.get("buy_links.bitget_spot_url", "https://www.bitget.com/spot/{symbol}")
        self.fut_tpl = settings.get("buy_links.bitget_futures_url",
                                    "https://www.bitget.com/futures/usdt/{symbol}")
        self.micro_prefix = settings.get("buy_links.microcap_label_prefix", "⚠️ 초고위험 · ")

    # ---------------------------------------------------------------- 레퍼럴 (유일한 삽입 지점)
    def _ref(self, venue: str) -> dict[str, str]:
        ref = self.s.get(f"referral.{venue}") or {}
        return {k: str(v) for k, v in ref.items() if v not in (None, "")} if isinstance(ref, dict) else {}

    def _with_ref(self, url: str, venue: str) -> str:
        q = self._ref(venue)
        if not q:
            return url
        return url + ("&" if "?" in url else "?") + urlencode(q)

    # ---------------------------------------------------------------- 개별 링크
    def bitget_spot(self, symbol: str) -> str:
        return self._with_ref(self.spot_tpl.format(symbol=symbol), "bitget")

    def bitget_futures(self, symbol: str) -> str:
        return self._with_ref(self.fut_tpl.format(symbol=symbol), "bitget")

    def dex(self, chain: str, address: str, pair_url: str | None = None) -> tuple[str, str] | None:
        """(버튼 이름, URL). 주소가 체인 형식에 안 맞으면 None — 추측해서 만들지 않는다."""
        if not valid_address(chain, address):
            return None
        if chain == "solana":
            url = "https://jup.ag/swap?" + urlencode({"sell": SOL_MINT, "buy": address})
            return "Jupiter", self._with_ref(url, "jupiter")
        if chain in UNISWAP_CHAIN:
            url = "https://app.uniswap.org/swap?" + urlencode({
                "chain": UNISWAP_CHAIN[chain], "inputCurrency": "NATIVE", "outputCurrency": address})
            return "Uniswap", self._with_ref(url, "uniswap")
        if chain == "bsc":
            url = "https://pancakeswap.finance/swap?" + urlencode(
                {"chain": "bsc", "outputCurrency": address})
            return "PancakeSwap", self._with_ref(url, "pancakeswap")
        # 대표 DEX 를 모르는 체인 — DexScreener 페어 페이지로 대신한다(거기서 스왑 가능).
        if pair_url and pair_url.startswith("https://dexscreener.com/"):
            return "DexScreener", pair_url
        return None

    @staticmethod
    def explorer(chain: str, address: str) -> str | None:
        tpl = EXPLORER.get(chain)
        return tpl.format(a=address) if tpl and valid_address(chain, address) else None

    @staticmethod
    def chart(chain: str | None, address: str | None, pair_url: str | None) -> str | None:
        if pair_url and pair_url.startswith("https://dexscreener.com/"):
            return pair_url
        if chain and address and valid_address(chain, address):
            return f"https://dexscreener.com/{chain}/{address}"
        return None

    # ---------------------------------------------------------------- 카드용 묶음
    def build(self, *, bitget_spot: str | None = None, bitget_futures: str | None = None,
              chain: str | None = None, address: str | None = None, pair_url: str | None = None,
              security=None, microcap: bool = False) -> LinkSet:
        """bitget_* 는 **API 로 상장이 확인된 심볼만** 넘겨야 한다(호출하는 쪽 책임).

        security 가 구매 차단 사유를 갖고 있으면 구매 버튼은 전부 빼고
        차트·컨트랙트만 남긴다 — "왜 막혔는지"를 보려면 그 둘은 필요하다.
        """
        rows: list[list[Button]] = []
        blocked = None
        if security is not None and getattr(security, "block_buy", False):
            blocked = security.block_reason() or "보안 플래그"

        prefix = self.micro_prefix if microcap else ""
        if not blocked:
            cex = []
            if bitget_spot:
                cex.append(Button(f"{prefix}🛒 비트겟 현물", self.bitget_spot(bitget_spot)))
            if bitget_futures:
                cex.append(Button(f"{prefix}📈 비트겟 선물", self.bitget_futures(bitget_futures)))
            if cex:
                rows.append(cex)

        tail: list[Button] = []
        if not blocked and chain and address:
            d = self.dex(chain, address, pair_url)
            if d:
                label = f"{prefix}🔄 {d[0]} 매수" if not microcap else f"{prefix}{d[0]} 매수"
                tail.append(Button(label, d[1]))
        ch = self.chart(chain, address, pair_url)
        if ch:
            tail.append(Button("📊 차트", ch))
        if chain and address:
            ex = self.explorer(chain, address)
            if ex:
                tail.append(Button("🔍 컨트랙트", ex))
        if tail:
            rows.append(tail)
        return LinkSet(rows=rows, blocked_reason=blocked)
