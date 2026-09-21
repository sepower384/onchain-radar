"""DefiLlama — 프로토콜 매출. Stage 5 의 원료.

실측 요점 (probe/out/defillama.json · 2026-09-21)
- `overview/fees?dataType=dailyRevenue` 한 번에 **프로토콜 2,369개**의 매출이 온다.
  행: defillamaId, name, slug, total7d, total30d, total60dto30d, total1y …  (gecko_id 는 **없다**)
- `/emissions`(언락) 은 **HTTP 402** 유료. 여기서 다루지 않는다(Stage 4 는 Tokenomist).
- **수수료(fees) ≠ 매출(revenue).** 매출 = 프로토콜/토큰 쪽에 남는 몫. 우리는 매출을 본다.

⚠️ 함정 — 토큰 매출은 **부모 단위로 합산**해야 한다.
  Uniswap 은 V1·V2·V3·V4·Labs 로, Pendle 은 "Pendle V2" 로 쪼개져 있고,
  하위 프로토콜의 geckoId 는 None 이다. 토큰(gecko_id)은 **부모(parent#pendle)** 에만 붙어 있다.
  `/lite/protocols2` 한 번이 하위→부모 연결(parentProtocol)과 부모의 gecko_id 를 모두 준다.
"""
from __future__ import annotations

from collections import defaultdict

from .base import Provider, fnum


class DefiLlama(Provider):
    name = "defillama"
    base_url = "https://api.llama.fi"

    def _ttl(self) -> float:
        return float(self.s.get("providers.cache_ttl_sec.defillama_fees", 3600))

    async def revenue_overview(self) -> list[dict] | None:
        js = await self.get_json("/overview/fees", {
            "excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true",
            "dataType": "dailyRevenue",
        }, ttl=self._ttl())
        return (js or {}).get("protocols") if isinstance(js, dict) else None

    async def catalog(self) -> dict | None:
        """{protocols: [...], parentProtocols: [...]} — 하위→부모, 부모→gecko_id."""
        js = await self.get_json("/lite/protocols2", {"b": "2"}, ttl=6 * 3600)
        return js if isinstance(js, dict) else None

    async def revenue_by_token(self) -> list[dict] | None:
        """토큰(gecko_id) 단위로 합친 매출. 매핑이 안 되는 프로토콜은 이름으로만 남긴다."""
        rows, cat = await self.revenue_overview(), await self.catalog()
        if rows is None:
            return None
        return aggregate(rows, cat or {})


def aggregate(rows: list[dict], cat: dict) -> list[dict]:
    """하위 프로토콜 매출을 부모로 묶는다. 순수 함수 — 테스트하기 쉽게 분리했다."""
    child_parent: dict[str, str] = {}
    child_gecko: dict[str, str] = {}
    child_symbol: dict[str, str] = {}
    for p in cat.get("protocols") or []:
        pid = str(p.get("defillamaId") or "")
        if not pid:
            continue
        if p.get("parentProtocol"):
            child_parent[pid] = p["parentProtocol"]
        gid = p.get("geckoId") or p.get("gecko_id")
        if gid:
            child_gecko[pid] = gid
        sym = p.get("symbol")
        if sym and sym != "-":
            child_symbol[pid] = sym
    parent_info = {pp["id"]: pp for pp in cat.get("parentProtocols") or [] if pp.get("id")}

    groups: dict[str, dict] = defaultdict(lambda: {"t7": 0.0, "t30": 0.0, "p30": 0.0, "t1y": 0.0,
                                                   "p30_known": False, "parts": []})
    meta: dict[str, dict] = {}
    for r in rows:
        pid = str(r.get("defillamaId") or r.get("id") or "")
        parent = child_parent.get(pid)
        key = parent or f"self#{pid or r.get('slug')}"
        g = groups[key]
        g["t7"] += fnum(r.get("total7d"), 0) or 0
        g["t30"] += fnum(r.get("total30d"), 0) or 0
        g["t1y"] += fnum(r.get("total1y"), 0) or 0
        p30 = fnum(r.get("total60dto30d"))
        if p30 is not None:
            g["p30"] += p30
            g["p30_known"] = True
        g["parts"].append(r.get("name"))
        if key not in meta:
            if parent and parent in parent_info:
                pi = parent_info[parent]
                meta[key] = {"name": pi.get("name"), "gecko_id": pi.get("gecko_id"),
                             "symbol": pi.get("symbol")}
            else:
                meta[key] = {"name": r.get("displayName") or r.get("name"),
                             "gecko_id": child_gecko.get(pid), "symbol": child_symbol.get(pid)}

    out = []
    for key, g in groups.items():
        t7, t30 = g["t7"], g["t30"]
        # 기준선 = 직전 30일 일평균. 없으면 최근 30일에서 최근 7일을 뺀 23일로 만든다.
        if g["p30_known"] and g["p30"] > 0:
            base_daily = g["p30"] / 30
        elif t30 > t7:
            base_daily = (t30 - t7) / 23
        else:
            base_daily = 0.0
        daily7 = t7 / 7
        out.append({
            "key": key, **meta[key],
            "rev_7d": t7, "rev_30d": t30, "rev_1y": g["t1y"],
            "daily_7d": daily7, "daily_base": base_daily,
            "ratio": (daily7 / base_daily) if base_daily > 0 else None,
            "parts": g["parts"],
        })
    return out
