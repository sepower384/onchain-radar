"""거래소 지갑 목록 — `exchange_wallets.yaml` 을 읽어 주소 → 거래소 이름.

출처는 Etherscan 공개 라벨(yaml 머리말 참고). 기억으로 적은 주소는 넣지 않는다 —
실제로 대조해 보니 기억한 "Binance 14" 가 사실은 "Binance 28" 이었다(2026-09-21).
EVM 주소는 체인이 달라도 같은 지갑인 경우가 많아(바이낸스 핫월렛이 ETH·BSC 공통) EVM 전체에 쓴다.
솔라나 거래소 지갑은 목록이 없다 — 솔라나 코인의 거래소 순유출은 "데이터 없음"이다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).with_name("exchange_wallets.yaml")
BURN = {"0x000000000000000000000000000000000000dead", "0x0000000000000000000000000000000000000000"}


@lru_cache(maxsize=1)
def load() -> dict[str, str]:
    try:
        data = yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    out: dict[str, str] = {}
    for ex, rows in (data.get("exchanges") or {}).items():
        for r in rows or []:
            a = str(r.get("address", "")).lower()
            if a.startswith("0x") and len(a) == 42:
                out[a] = ex
    return out


def classify(address: str, is_contract: bool, chain: str | None) -> str:
    a = (address or "").lower()
    if a in BURN:
        return "burn"
    if chain != "solana" and a in load():
        return "exchange"
    if is_contract:
        return "contract"
    return "wallet"
