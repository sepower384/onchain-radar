"""스캔 파이프라인 — Stage 들을 순서대로 엮는다. 한 Stage 가 죽어도 나머지는 돈다.

메인 트랙 (Phase 1)
  유니버스(A∪B) → 가격·OI 스냅샷 적재 → Stage2 반응(예선→정밀) → Stage3 파생
  → Stage5 매출 → [상위 후보만] 컨트랙트 확인 + GoPlus 안전 + Stage1 홀더 스냅샷
  → 재정규화 점수 → 순위
마이크로캡 트랙 → scanners/stage7_microcap.py
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from scoring.engine import rank

from . import security, stage1_accumulation, stage2_reaction, stage3_derivatives, stage5_revenue
from . import stage4_unlock, stage6_news, stage7_microcap, stage8_retracement, universe
from .model import Candidate

log = logging.getLogger("pipeline")


@dataclass
class Providers:
    bitget: object
    coingecko: object
    dexscreener: object
    geckoterminal: object
    defillama: object
    goplus: object
    tokenomist: object = None       # Stage 4 — 키 있을 때만 enabled
    nansen: object = None           # Stage 1 스마트머니 — 키 있을 때만 enabled
    feeds: object = None            # Stage 6 거래소 공지·뉴스

    async def close(self):
        for p in (self.bitget, self.coingecko, self.dexscreener, self.geckoterminal,
                  self.defillama, self.goplus, self.tokenomist, self.nansen, self.feeds):
            if p is not None:
                await p.close()


@dataclass
class ScanResult:
    ranked: list[Candidate] = field(default_factory=list)
    revenue_ranking: list[dict] = field(default_factory=list)
    btc_price: float | None = None
    stats: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    took_s: float = 0.0
    unlocks: list | None = None


def make_providers(settings) -> Providers:
    from providers.bitget import Bitget
    from providers.coingecko import CoinGecko
    from providers.defillama import DefiLlama
    from providers.dexscreener import DexScreener
    from providers.geckoterminal import GeckoTerminal
    from providers.goplus import GoPlus
    from providers.nansen import Nansen
    from providers.tokenomist import Tokenomist
    return Providers(Bitget(settings), CoinGecko(settings), DexScreener(settings),
                     GeckoTerminal(settings), DefiLlama(settings), GoPlus(settings),
                     Tokenomist(settings), Nansen(settings), stage6_news.NewsFeeds(settings))


async def _guard(name: str, res: ScanResult, coro):
    """Stage 예외 격리 — 실패는 기록하고 None 을 돌려준다."""
    try:
        return await coro
    except Exception as e:  # noqa: BLE001
        log.exception("%s 실패", name)
        res.errors.append(f"{name}: {type(e).__name__}: {e}")
        return None


async def scan_main(settings, P: Providers, store, *, deep_top: int = 40) -> ScanResult:
    t0 = time.time()
    res = ScanResult()
    cfg = settings.get

    built = await _guard("유니버스", res, universe.build_main(settings, P.bitget, P.coingecko))
    if not built or not built[0]:
        res.took_s = time.time() - t0
        return res
    cands, extra = built
    res.btc_price = extra.get("btc_price")
    res.stats["universe"] = extra.get("counts", {})

    # 스냅샷 먼저 — 다음 스캔의 OI 24h 비교·거래량 평균의 원료가 된다.
    try:
        store.add_price_snapshots([{"coin_key": c.coin_key, "price": c.price,
                                    "volume_24h": c.volume_24h, "oi_usd": c.oi_usd} for c in cands])
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"스냅샷: {e}")

    reacting = await _guard("Stage2", res, stage2_reaction.run(cands, P.bitget, store, cfg)) or []
    res.stats["stage2_pass"] = len(reacting)

    await _guard("Stage3", res, stage3_derivatives.run(reacting, P.bitget, store, cfg))
    res.revenue_ranking = await _guard("Stage5", res, stage5_revenue.run(reacting, P.defillama, cfg)) or []

    # Stage 6 — 공지는 뉴스 작업이 10분마다 쌓는다. 여기서는 쌓인 걸 카드에 붙이기만(없으면 한 번 받는다).
    if P.feeds is not None and not store.news_since(time.time() - 3600):
        await _guard("Stage6 수집", res, stage6_news.refresh(settings, P.feeds, store))
    try:
        stage6_news.evaluate(reacting, store, cfg)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"Stage6: {e}")

    # Stage 4 — 언락 (Tokenomist 키 있을 때만)
    if P.tokenomist is not None and P.tokenomist.enabled:
        res.unlocks = await _guard("Stage4", res, P.tokenomist.upcoming(int(cfg("stage4.lookahead_days", 14))))
        stage4_unlock.evaluate(reacting, res.unlocks, cfg)

    # 무거운 조회는 1차 점수 상위만
    from tracking.tuner import effective_weights
    weights = effective_weights(settings)
    required = tuple(cfg("scoring.required_signals", ["reaction"]) or ())
    # 보안·홀더 조회(코인게코 상세·GoPlus)는 비싸다 — 반응이 확인된 코인에만 쓴다.
    pre = rank(reacting, weights, min_signals=1, required=required)
    top = pre[:deep_top]
    await _guard("보안", res, security.run(top, P.goplus, P.coingecko, P.dexscreener, cfg, top=deep_top, store=store))
    smart = None
    if P.nansen is not None and P.nansen.enabled:
        smart = await _guard("스마트머니", res, P.nansen.netflows(cfg("universe.pool_c_chains", ["ethereum"])))
    try:
        stage1_accumulation.run(top, store, smart)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"Stage1: {e}")

    res.ranked = rank(reacting, weights, min_signals=int(cfg("scoring.min_signals", 2)),
                      required=required)
    res.took_s = round(time.time() - t0, 1)
    res.stats["ranked"] = len(res.ranked)
    return res


async def scan_micro(settings, P: Providers, store, bitget_bases: set[str] | None = None) -> ScanResult:
    t0 = time.time()
    res = ScanResult()
    if bitget_bases is None:
        bg = await P.bitget.markets() or {}
        bitget_bases = set(bg)
    out = await _guard("Stage7", res, stage7_microcap.run(settings, P.dexscreener, P.geckoterminal,
                                                          P.goplus, store, bitget_bases))
    if out:
        res.ranked = stage7_microcap.micro_rank(out["candidates"])
        res.stats = out["stats"]
    res.took_s = round(time.time() - t0, 1)
    return res


async def scan_dip(settings, P: Providers, store) -> ScanResult:
    """Stage 8 — 코인게코 상위 ∩ 비트겟 상장 코인의 일봉으로 급락 후 되돌림을 찾는다.
    잡코인까지 보면 패턴이 무의미하고 호출만 는다 — 시총 상위 + 거래대금 하한으로 좁힌다."""
    t0 = time.time()
    res = ScanResult()
    cfg = settings.get
    built = await _guard("유니버스", res, universe.build_main(settings, P.bitget, P.coingecko))
    if not built or not built[0]:
        return res
    cands, extra = built
    res.btc_price = extra.get("btc_price")
    floor = cfg("stage8.min_volume_24h_usd", 1_000_000)
    # 거래대금 하한은 전체 거래소 기준(코인게코)으로 — 비트겟 단독으로 걸면 후보가 70개로 줄었다.
    pool = [c for c in cands if c.cg_id and (c.bitget_spot or c.bitget_futures)
            and max(c.volume_24h or 0, c.bitget_volume_24h or 0) >= floor]
    found = await _guard("Stage8", res, stage8_retracement.run(pool, P.bitget, cfg)) or []
    res.ranked = found
    res.stats = {"pool": len(pool), "found": len(found)}
    res.took_s = round(time.time() - t0, 1)
    return res
