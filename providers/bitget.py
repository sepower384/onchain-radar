"""비트겟 — 현물·선물 상장 여부, 펀딩비, 미결제약정, 롱숏, 캔들.

실측 요점 (probe/out/bitget.json)
- 응답은 {"code":"00000","msg":"success","data":…}. code 가 00000 이 아니면 실패로 본다.
- 숫자는 전부 **문자열**이다("0.000062"). fnum 으로 바꾼다.
- 선물 tickers 한 번에 797종목의 `fundingRate` · `holdingAmount`(OI, 코인 수량)가 같이 온다.
  → Stage 3 은 종목별 호출 없이 이 한 방으로 끝난다.
- 현물 상장 판정 = `status == "online"`, 선물 = `symbolStatus == "normal"`.
- 페이지는 SPA 라 없는 심볼에도 200 을 준다. **링크 생성 전 반드시 여기서 상장을 확인한다.**
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import Provider, fnum


@dataclass
class BitgetMarket:
    base: str
    spot_symbol: str | None = None       # BTCUSDT (현물 online 일 때만)
    futures_symbol: str | None = None    # BTCUSDT (선물 normal 일 때만)
    price: float | None = None
    change24h_pct: float | None = None
    quote_volume_24h: float | None = None
    funding_rate_pct: float | None = None   # 8h 기준 % (0.0062 → 0.62 가 아니라 원값×100)
    oi_coins: float | None = None
    oi_usd: float | None = None


class Bitget(Provider):
    name = "bitget"
    base_url = "https://api.bitget.com"

    async def _data(self, path: str, params: dict | None = None, ttl_key: str = "bitget_tickers"):
        ttl = float(self.s.get(f"providers.cache_ttl_sec.{ttl_key}", 60))
        js = await self.get_json(path, params, ttl=ttl)
        if not js or js.get("code") != "00000":
            return None
        return js.get("data")

    async def spot_symbols(self) -> dict[str, str] | None:
        """base → 현물 심볼. USDT 마켓 · online 만."""
        data = await self._data("/api/v2/spot/public/symbols", ttl_key="bitget_symbols")
        if data is None:
            return None
        return {d["baseCoin"].upper(): d["symbol"] for d in data
                if d.get("quoteCoin") == "USDT" and d.get("status") == "online"}

    async def futures_contracts(self) -> dict[str, str] | None:
        """base → 선물 심볼. USDT 무기한 · normal 만."""
        data = await self._data("/api/v2/mix/market/contracts",
                                {"productType": "USDT-FUTURES"}, ttl_key="bitget_symbols")
        if data is None:
            return None
        return {d["baseCoin"].upper(): d["symbol"] for d in data
                if d.get("quoteCoin") == "USDT" and d.get("symbolStatus") == "normal"
                and d.get("symbolType") == "perpetual"}

    async def spot_tickers(self) -> dict[str, dict] | None:
        data = await self._data("/api/v2/spot/market/tickers")
        return None if data is None else {d["symbol"]: d for d in data}

    async def futures_tickers(self) -> dict[str, dict] | None:
        data = await self._data("/api/v2/mix/market/tickers", {"productType": "USDT-FUTURES"})
        return None if data is None else {d["symbol"]: d for d in data}

    async def markets(self) -> dict[str, BitgetMarket] | None:
        """상장·시세·펀딩·OI 를 한 번에 묶는다(호출 4번)."""
        spot, fut = await self.spot_symbols(), await self.futures_contracts()
        if spot is None and fut is None:
            return None
        st, ft = await self.spot_tickers() or {}, await self.futures_tickers() or {}
        out: dict[str, BitgetMarket] = {}
        for base in set(spot or {}) | set(fut or {}):
            m = BitgetMarket(base=base, spot_symbol=(spot or {}).get(base),
                             futures_symbol=(fut or {}).get(base))
            t = st.get(m.spot_symbol or "") or {}
            f = ft.get(m.futures_symbol or "") or {}
            m.price = fnum(t.get("lastPr")) or fnum(f.get("lastPr"))
            ch = fnum(t.get("change24h")) if t else fnum(f.get("change24h"))
            m.change24h_pct = ch * 100 if ch is not None else None
            m.quote_volume_24h = fnum(t.get("usdtVolume")) or fnum(f.get("usdtVolume"))
            fr = fnum(f.get("fundingRate"))
            m.funding_rate_pct = fr * 100 if fr is not None else None
            m.oi_coins = fnum(f.get("holdingAmount"))
            if m.oi_coins is not None and m.price:
                m.oi_usd = m.oi_coins * m.price
            out[base] = m
        return out

    async def long_short(self, symbol: str, period: str = "1h") -> float | None:
        """가장 최근 롱 계좌 비율(0~1)."""
        data = await self._data("/api/v2/mix/market/account-long-short",
                                {"symbol": symbol, "productType": "USDT-FUTURES", "period": period})
        if not data:
            return None
        latest = max(data, key=lambda d: int(d.get("ts", 0)))
        return fnum(latest.get("longAccountRatio"))

    async def daily_candles(self, symbol: str, days: int = 15, futures: bool = False) -> list[dict] | None:
        """일봉 [{ts, open, high, low, close, quote_vol}] 오래된 것 → 최근 순.
        현물 행: [ts, open, high, low, close, baseVol, usdtVol, quoteVol]  granularity=1day
        선물 행: [ts, open, high, low, close, baseVol, quoteVol]           granularity=1D
        두 쪽 다 USDT 거래대금이 [6] 이다(2026-09-21 실측). 선물에 '1day' 를 주면 400."""
        if futures:
            data = await self._data("/api/v2/mix/market/candles",
                                    {"symbol": symbol, "productType": "USDT-FUTURES",
                                     "granularity": "1D", "limit": str(days)})
        else:
            data = await self._data("/api/v2/spot/market/candles",
                                    {"symbol": symbol, "granularity": "1day", "limit": str(days)})
        if not data:
            return None
        rows = []
        for r in data:
            if len(r) < 7:
                continue
            rows.append({"ts": int(r[0]) / 1000, "open": fnum(r[1]), "high": fnum(r[2]),
                         "low": fnum(r[3]), "close": fnum(r[4]), "quote_vol": fnum(r[6])})
        return sorted(rows, key=lambda x: x["ts"])
