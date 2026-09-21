"""GoPlus Security — 스캠 체크 + **홀더 수·상위 홀더·LP 홀더(무료)**.

실측 요점 (probe/out/goplus.json)
- EVM: `/api/v1/token_security/{chainId}?contract_addresses=…`
  응답 {"code":1,"message":"OK","result":{<소문자 주소>: {...}}}. **주소 키가 소문자**다.
- 값은 전부 문자열: "0"/"1" 플래그, "0.05" 세율(= 5%), "" 는 모름.
- holder_count · holders[10] · lp_holders[10] 까지 무료로 온다
  → Stage 1 홀더 스냅샷을 키 없이 Phase 1 부터 쌓을 수 있다.
- 솔라나는 `/api/v1/solana/token_security` 로 따로다. 필드 이름이 다르다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .base import Provider, fnum

# GoPlus 체인 id (DexScreener 체인 이름 → GoPlus)
CHAIN_ID = {
    "ethereum": "1", "bsc": "56", "base": "8453", "arbitrum": "42161",
    "polygon": "137", "optimism": "10", "avalanche": "43114",
    "linea": "59144", "blast": "81457", "scroll": "534352", "zksync": "324",
}
BURN_ADDRS = {"0x000000000000000000000000000000000000dead", "0x0000000000000000000000000000000000000000"}


@dataclass
class Security:
    chain: str
    address: str
    known: bool = False                   # GoPlus 가 이 토큰을 아는가
    honeypot: bool = False
    cannot_sell: bool = False
    buy_tax_pct: float | None = None
    sell_tax_pct: float | None = None
    mintable: bool = False
    owner_can_change: bool = False        # 오너 권한(잔고 변경·소유권 회수·숨은 오너)
    blacklist: bool = False
    proxy: bool = False
    open_source: bool | None = None
    holder_count: int | None = None
    top10_pct: float | None = None        # LP·소각·컨트랙트 제외한 상위 10 개인 지갑 비중
    creator_pct: float | None = None
    lp_locked_pct: float | None = None    # LP 중 잠김·소각 비중
    top_holders: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)   # 사람이 읽을 사유

    @property
    def block_buy(self) -> bool:
        """구매 버튼을 만들지 말아야 하는가(사양서: 허니팟·매도 제한·고세금)."""
        return bool(self.honeypot or self.cannot_sell or
                    (self.sell_tax_pct or 0) >= 30 or (self.buy_tax_pct or 0) >= 30)

    def block_reason(self) -> str:
        why = []
        if self.honeypot:
            why.append("허니팟")
        if self.cannot_sell:
            why.append("매도 불가")
        if (self.sell_tax_pct or 0) >= 30:
            why.append(f"매도세 {self.sell_tax_pct:.0f}%")
        if (self.buy_tax_pct or 0) >= 30:
            why.append(f"매수세 {self.buy_tax_pct:.0f}%")
        return " · ".join(why)


def _flag(v) -> bool:
    return str(v) == "1"


def parse_evm(chain: str, address: str, r: dict) -> Security:
    s = Security(chain=chain, address=address, known=True)
    s.honeypot = _flag(r.get("is_honeypot"))
    # `cannot_sell_all` 은 문제 있는 토큰에만 실려 온다(UNI 응답엔 키 자체가 없다).
    # `transfer_pausable` 은 오너가 전송을 멈출 수 있다는 뜻 — 매도가 막힐 수 있다.
    s.cannot_sell = _flag(r.get("cannot_sell_all"))
    if _flag(r.get("transfer_pausable")):
        s.flags.append("전송 중지 가능")
    bt, st = fnum(r.get("buy_tax")), fnum(r.get("sell_tax"))
    s.buy_tax_pct = bt * 100 if bt is not None else None
    s.sell_tax_pct = st * 100 if st is not None else None
    s.mintable = _flag(r.get("is_mintable"))
    s.owner_can_change = any(_flag(r.get(k)) for k in
                             ("owner_change_balance", "can_take_back_ownership", "hidden_owner",
                              "selfdestruct"))
    s.blacklist = _flag(r.get("is_blacklisted"))
    s.proxy = _flag(r.get("is_proxy"))
    os_ = r.get("is_open_source")
    s.open_source = None if os_ in (None, "") else _flag(os_)
    hc = fnum(r.get("holder_count"))
    s.holder_count = int(hc) if hc is not None else None
    cp = fnum(r.get("creator_percent"))
    s.creator_pct = cp * 100 if cp is not None else None

    holders = r.get("holders") or []
    s.top_holders = holders
    # 상위 10 집중도는 **개인 지갑만** 센다. LP·소각·컨트랙트까지 세면 멀쩡한 토큰도 90% 가 된다.
    personal = [h for h in holders
                if not _flag(h.get("is_contract")) and not _flag(h.get("is_locked"))
                and (h.get("address") or "").lower() not in BURN_ADDRS]
    if holders:
        s.top10_pct = sum((fnum(h.get("percent"), 0) or 0) for h in personal[:10]) * 100

    lp = r.get("lp_holders") or []
    if lp:
        locked = sum((fnum(h.get("percent"), 0) or 0) for h in lp
                     if _flag(h.get("is_locked")) or (h.get("address") or "").lower() in BURN_ADDRS)
        s.lp_locked_pct = locked * 100

    if s.honeypot:
        s.flags.append("허니팟")
    if s.cannot_sell:
        s.flags.append("매도 불가")
    if (s.sell_tax_pct or 0) > 0 or (s.buy_tax_pct or 0) > 0:
        s.flags.append(f"세금 매수 {s.buy_tax_pct or 0:.1f}% / 매도 {s.sell_tax_pct or 0:.1f}%")
    if s.mintable:
        s.flags.append("추가 발행 가능")
    if s.owner_can_change:
        s.flags.append("오너 권한 보유")
    if s.blacklist:
        s.flags.append("블랙리스트 기능")
    if s.open_source is False:
        s.flags.append("소스 비공개")
    return s


def _status(obj) -> bool:
    return isinstance(obj, dict) and str(obj.get("status")) == "1"


def parse_solana(address: str, r: dict) -> Security:
    """솔라나는 필드 구조가 EVM 과 전혀 다르다(실측: probe/out/goplus.json sol_jup).
      mintable / freezable / balance_mutable_authority / closable  → {"status": "0"|"1", "authority": [...]}
      non_transferable "0"|"1"  ·  transfer_fee {} 또는 {fee_rate…}
      holders[{account, percent, is_locked, …}]  ← 주소 키가 `address` 가 아니라 `account`
    """
    s = Security(chain="solana", address=address, known=True)
    s.mintable = _status(r.get("mintable"))
    s.blacklist = _status(r.get("freezable"))                 # 동결 권한 = 사실상 블랙리스트
    s.owner_can_change = _status(r.get("balance_mutable_authority")) or _status(r.get("closable"))
    s.cannot_sell = str(r.get("non_transferable")) == "1"
    fee = r.get("transfer_fee") or {}
    rate = fnum(fee.get("fee_rate")) if isinstance(fee, dict) else None
    if rate is not None:
        s.sell_tax_pct = s.buy_tax_pct = rate * 100
    hc = fnum(r.get("holder_count"))
    s.holder_count = int(hc) if hc is not None else None

    holders = r.get("holders") or []
    s.top_holders = [{**h, "address": h.get("account")} for h in holders]
    personal = [h for h in holders if not _flag(h.get("is_locked"))]
    if holders:
        s.top10_pct = sum((fnum(h.get("percent"), 0) or 0) for h in personal[:10]) * 100

    if s.cannot_sell:
        s.flags.append("전송 불가 토큰")
    if s.mintable:
        s.flags.append("추가 발행 가능")
    if s.blacklist:
        s.flags.append("동결 권한 보유")
    if s.owner_can_change:
        s.flags.append("잔고 조작·계정 폐쇄 권한")
    if (s.sell_tax_pct or 0) > 0:
        s.flags.append(f"전송세 {s.sell_tax_pct:.1f}%")
    return s


class GoPlus(Provider):
    name = "goplus"
    base_url = "https://api.gopluslabs.io"

    def _ttl(self) -> float:
        return float(self.s.get("providers.cache_ttl_sec.goplus", 21600))

    async def security(self, chain: str, address: str) -> Security | None:
        """모르는 체인이면 None(= 안전 신호 없음). 알면 Security."""
        if chain == "solana":
            js = await self.get_json("/api/v1/solana/token_security",
                                     {"contract_addresses": address}, ttl=self._ttl())
            res = ((js or {}).get("result") or {})
            r = res.get(address) or next(iter(res.values()), None) if res else None
            return parse_solana(address, r) if r else None
        cid = CHAIN_ID.get(chain)
        if not cid:
            return None
        js = await self.get_json(f"/api/v1/token_security/{cid}",
                                 {"contract_addresses": address}, ttl=self._ttl())
        if not js or js.get("code") != 1:
            return None
        r = (js.get("result") or {}).get(address.lower())
        return parse_evm(chain, address, r) if r else None
