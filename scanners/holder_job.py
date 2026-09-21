"""홀더 적재 작업 — 한 번이라도 후보에 오른 코인을 3시간마다 계속 찍어 둔다.

왜 따로 도는가: 스캔은 그때그때 상위 후보만 찍는다. 그러면 어떤 코인이 후보에 올라온
바로 그 순간엔 24h 이력이 없어 Stage 1 이 늘 "수집 중"이 된다. 한 번 잡힌 코인(최근 14일)과
관심 코인을 따로 계속 찍어 두면, 다음에 다시 올라왔을 때는 이미 비교할 이력이 있다.

GoPlus 공개 한도(분당 30)에 맞춰 천천히 돈다. 300개면 약 10분.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .model import Candidate
from .stage1_accumulation import snapshot

log = logging.getLogger("holders")


async def run(settings, P, store, *, days: int = 14) -> dict:
    targets = {c.coin_key: c for c in store.contracts(time.time() - days * 86400)}
    done = fail = 0
    sem = asyncio.Semaphore(3)

    async def one(ct):
        nonlocal done, fail
        async with sem:
            sec = await P.goplus.security(ct.chain, ct.address, fresh=True)
        if sec is None:
            fail += 1
            return
        c = Candidate(coin_key=ct.coin_key, symbol=ct.symbol, chain=ct.chain, address=ct.address)
        c.security = sec
        snapshot(store, c)
        done += 1

    await asyncio.gather(*(one(t) for t in targets.values()), return_exceptions=True)
    log.info("홀더 적재 %d개 (실패 %d)", done, fail)
    return {"targets": len(targets), "done": done, "fail": fail}
