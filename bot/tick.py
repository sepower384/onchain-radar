"""깃허브 Actions 용 '한 번 깨어나서 할 일만 하고 끝나는' 모드 — PC·VPS 없이 무료로 돈다.

강회장 지시(2026-09-21): "VPS 말고 무료로 … git clone 으로". 다른 봇들처럼
cron-job.org 가 5분마다 workflow_dispatch 로 깨우면, 매번:
  1) 밀린 텔레그램 명령을 처리한다(getUpdates + offset) — 답은 최대 5분 늦다
  2) 주기가 돌아온 작업만 돈다 — 마지막 실행 시각을 DB(jobs_state)에 적어 두고 비교
  3) 끝. DB 는 워크플로가 actions/cache 로 다음 실행에 넘겨준다

상시 구동(run_polling)과 **같은 함수들**(job_main·job_news … cmd_scan …)을 그대로 부른다.
그래서 PC 로 돌리든 깃허브로 돌리든 결과가 같다. 단, 둘을 동시에 켜면 안 된다 —
같은 토큰으로 getUpdates 를 두 곳에서 부르면 409 Conflict. PC 쪽 예약작업은 끈다.

구독 LLM(오른 이유·백커 조사, 뉴스 분류)은 CLAUDE_CODE_OAUTH_TOKEN secret(`claude setup-token`)이 있을 때만
Actions 에서도 켜진다 — 과금이 아니라 구독 사용량. 없으면 꺼지고 거래소 공식 공지(규칙)·신호 기반 이유는 그대로 돈다.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from telegram import Bot, Update
from telegram.ext import Application

from settings import KST

log = logging.getLogger("tick")


class _Job:
    """PTB 의 ContextTypes.DEFAULT_TYPE 흉내 — job_* 함수들이 ctx.bot · ctx.application 만 쓴다."""

    def __init__(self, app, bot):
        self.application, self.bot = app, bot


def due(store, name: str, every_min: float, now: float) -> bool:
    last = store.job_last(name)
    # 5분 주기로 깨우므로 1분 여유를 둔다(20분 작업이 19.5분 만에 깨어도 돈다)
    return last is None or now - last >= every_min * 60 - 60


def due_daily(store, name: str, hhmm: str, now: float, weekday: int | None = None) -> bool:
    """오늘 hh:mm 이 지났고 오늘 아직 안 돌았으면. 깃허브가 늦게 깨워도 그날 한 번은 돈다."""
    t = dt.datetime.fromtimestamp(now, KST)
    if weekday is not None and t.weekday() != weekday:
        return False
    h, m = (int(x) for x in hhmm.split(":"))
    target = t.replace(hour=h, minute=m, second=0, microsecond=0)
    if t < target:
        return False
    last = store.job_last(name)
    return last is None or dt.datetime.fromtimestamp(last, KST) < target


async def run_tick(settings, budget_s: float = 540) -> dict:
    from . import telegram_app as T
    t0 = time.time()
    app = T.build_app(settings)
    radar: T.Radar = app.bot_data["radar"]
    store = radar.store
    done: list[str] = []
    async with app:                       # Application.initialize/shutdown — 폴링은 하지 않는다
        bot: Bot = app.bot
        # 1) 밀린 명령
        offset = store.job_last("tg_offset")
        updates = await bot.get_updates(offset=int(offset) + 1 if offset else None, timeout=0,
                                        allowed_updates=Update.ALL_TYPES)
        for u in updates:
            try:
                await app.process_update(u)
            except Exception as e:  # noqa: BLE001 — 명령 하나 실패가 나머지를 막지 않게
                log.warning("명령 처리 실패: %s", e)
            store.job_mark("tg_offset", u.update_id)
        if updates:
            done.append(f"명령 {len(updates)}건")

        ctx = _Job(app, bot)
        s = settings.get
        now = time.time()
        # 첫 실행(깃허브 캐시가 비어 있음) — 오늘치 하루 1회 작업은 '이미 함'으로 두고 시작한다.
        # 안 그러면 옮기는 순간 오전·오후 브리핑·언락이 한꺼번에 나간다.
        if store.job_last("initialized") is None:
            for n in [f"briefing {h}" for h in s("telegram.briefing_times", ["08:00", "20:00"])] + ["unlock", "tune"]:
                store.job_mark(n, now)
            store.job_mark("initialized", now)
            done.append("첫 실행 — 오늘 하루 1회 작업은 건너뜀")
        plan = [
            ("news", due(store, "news", s("stage6.scan_minutes", 10), now), T.job_news),
            ("micro", due(store, "micro", s("schedule.pool_c_minutes", 5), now), T.job_micro),
            ("main", due(store, "main", s("schedule.pool_ab_minutes", 20), now), T.job_main),
            ("dip", due(store, "dip", s("stage8.scan_minutes", 60), now), T.job_dip),
            ("track", due(store, "track", s("schedule.tracking_minutes", 60), now), T.job_track),
            ("holders", due(store, "holders", s("schedule.holder_snapshot_minutes", 180), now), T.job_holders),
            ("unlock", due_daily(store, "unlock", "09:00", now), T.job_unlock),
            ("tune", due_daily(store, "tune", "21:00", now, weekday=6), T.job_tune),
        ]
        for hhmm in s("telegram.briefing_times", ["08:00", "20:00"]):
            plan.append((f"briefing {hhmm}", due_daily(store, f"briefing {hhmm}", hhmm, now), T.job_briefing))

        for name, is_due, fn in plan:
            if not is_due:
                continue
            if time.time() - t0 > budget_s:
                log.info("시간 예산 소진 — %s 는 다음 실행으로", name)
                break
            try:
                await fn(ctx)
                done.append(name)
            except Exception as e:  # noqa: BLE001
                log.exception("%s 실패", name)
                done.append(f"{name}(실패: {type(e).__name__})")
            store.job_mark(name, time.time())
        await radar.P.close()
    took = round(time.time() - t0, 1)
    log.info("tick 끝 %.1fs — %s", took, ", ".join(done) or "할 일 없음")
    return {"done": done, "took": took}
