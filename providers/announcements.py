"""거래소 공식 공지 — "메이저 거래소 현물 상장 확정" 을 **1차 출처**에서 받는다. 전부 무료·키 없음.

실측 (2026-09-21)
  업비트  api-manager.upbit.com/api/v1/announcements?category=trade&per_page≤20
          → {"success":true,"data":{"notices":[{id, title, listed_at(+09:00)}]}}
          ⚠ per_page=40 은 HTTP 400. 20 이 상한이다.
  바이낸스 bapi/composite/v1/public/cms/article/list/query?type=1&catalogId=48(신규상장)·161(상폐)
          → data.catalogs[0].articles[{id, code, title, releaseDate(ms)}]
  비트겟  api/v2/public/annoucements?annType=coin_listings   ← 'annoucements' 오타가 실제 경로다
          → data[{annId, annTitle, annSubType(spot|futures), annUrl, cTime(ms)}]
  빗썸   JSON API 없음(HTML) — 제외.
"""
from __future__ import annotations

from datetime import datetime

from .base import Provider

UA_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"}


def _item(source, id_, ts, title, url, sub=None):
    return {"source": source, "id": f"{source}:{id_}", "ts": ts, "title": title, "url": url, "sub": sub}


class Upbit(Provider):
    name = "upbit"
    base_url = "https://api-manager.upbit.com"
    default_headers = UA_H

    async def notices(self, pages: int = 2) -> list[dict] | None:
        out = []
        for page in range(1, pages + 1):
            js = await self.get_json("/api/v1/announcements",
                                     {"os": "web", "page": page, "per_page": 20, "category": "trade"}, ttl=120)
            if not js or not js.get("success"):
                return out or None
            for n in (js.get("data") or {}).get("notices", []):
                try:
                    ts = datetime.fromisoformat(n["listed_at"]).timestamp()
                except (KeyError, ValueError):
                    continue
                out.append(_item("upbit", n["id"], ts, n.get("title", ""),
                                 f"https://upbit.com/service_center/notice?id={n['id']}"))
        return out


class BinanceCMS(Provider):
    name = "binance_cms"
    base_url = "https://www.binance.com"
    default_headers = UA_H

    async def articles(self, catalog: int, size: int = 20) -> list[dict] | None:
        js = await self.get_json("/bapi/composite/v1/public/cms/article/list/query",
                                 {"type": 1, "catalogId": catalog, "pageNo": 1, "pageSize": size}, ttl=120)
        if not js or js.get("code") != "000000":
            return None
        cats = (js.get("data") or {}).get("catalogs") or []
        arts = cats[0].get("articles", []) if cats else []
        return [_item("binance", a["id"], a["releaseDate"] / 1000, a.get("title", ""),
                      f"https://www.binance.com/en/support/announcement/{a.get('code')}", sub=str(catalog))
                for a in arts]


class BitgetAnn(Provider):
    name = "bitget_ann"
    base_url = "https://api.bitget.com"

    async def listings(self) -> list[dict] | None:
        js = await self.get_json("/api/v2/public/annoucements",
                                 {"language": "en_US", "annType": "coin_listings"}, ttl=120)
        if not js or js.get("code") != "00000":
            return None
        return [_item("bitget", a["annId"], int(a["cTime"]) / 1000, a.get("annTitle", ""), a.get("annUrl"),
                      sub=a.get("annSubType")) for a in js.get("data", [])]


class CryptoPanic(Provider):
    """일반 뉴스 — 키(무료) 있을 때만. 규칙으로 못 가르는 건 Claude 분류로 넘긴다."""
    name = "cryptopanic"
    base_url = "https://cryptopanic.com"

    @property
    def enabled(self) -> bool:
        return self.s.has("CRYPTOPANIC_TOKEN")

    async def posts(self) -> list[dict] | None:
        js = await self.get_json("/api/developer/v2/posts/", {
            "auth_token": self.s.key("CRYPTOPANIC_TOKEN"), "public": "true", "kind": "news"}, ttl=300)
        if not js:
            return None
        out = []
        for p in js.get("results", []):
            try:
                ts = datetime.fromisoformat(p["published_at"].replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError):
                continue
            syms = [c.get("code") for c in (p.get("instruments") or p.get("currencies") or []) if c.get("code")]
            it = _item("cryptopanic", p.get("id"), ts, p.get("title", ""), p.get("original_url") or p.get("url"))
            it["symbols"] = syms
            it["domain"] = (p.get("source") or {}).get("domain")
            out.append(it)
        return out
