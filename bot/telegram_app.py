"""텔레그램 봇 — 명령어 · 자동 브리핑 · 실시간 알림. 기존 봇들과 코드·토큰·DB 를 공유하지 않는다.

안전
- 허용된 chat_id 만 명령을 쓸 수 있다. 목록이 비어 있으면 **아무도 못 쓴다**(안전 기본값).
- 카드 하나 = 메시지 하나 + 그 카드의 버튼. 4096자 제한을 넘지 않고, 버튼이 엉뚱한 카드에 붙지 않는다.
- 스캔은 락으로 한 번에 하나만. 방금(5분 내) 돈 결과가 있으면 /scan 은 그걸 다시 쓴다(API 절약).

스케줄 (config.schedule · stage8.scan_minutes · telegram.briefing_times)
  브리핑       매일 08:00·20:00 KST — 메인 상위 N + 되돌림 상위 3 + 마이크로 상위 3
  메인 스캔    20분 — 점수 ≥ alert_threshold 이면 실시간 알림 (쿨다운 6h)
  마이크로     5분 — 동일
  되돌림       60분 — 되돌림 점수 ≥ stage8.alert_threshold 이면 알림
  성과 추적    60분 — 1·3·7·14일 수익률 기록
  관심 코인    메인 스캔마다 — /watch 한 코인이 순위에 들면 그 사람에게만 알림
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes)

from scanners import single
from scanners.pipeline import ScanResult, make_providers, scan_dip, scan_main, scan_micro
from settings import KST, ROOT
from storage.db import Store
from tracking import tracker

from .buy_links import LinkBuilder
from .formatter import (DISCLAIMER, MICRO_BANNER, card_with_block_note, dip_card, header, links_for,
                        revenue_table)

log = logging.getLogger("bot")
PREFS = ROOT / "data" / "prefs.json"
DEFAULT_PREFS = {"alerts": True, "micro_alerts": True, "dip_alerts": True}

HELP = """🛰 <b>ONCHAIN RADAR</b>
자금 흐름이 먼저 포착된 코인을 추적합니다. 예측이 아니라 추적입니다.

/scan — 지금 전체 스캔, 상위 10개
/micro — 마이크로캡·DEX 전용 (초고위험)
/dip — 급락 후 피보나치 되돌림
/coin &lt;티커|주소&gt; — 단일 코인 전체 리포트
/buy &lt;티커|주소&gt; — 구매 링크만
/revenue — 매출 급증 프로토콜
/unlock — 14일 언락 캘린더
/news — 24h 실질 호재
/watch · /unwatch &lt;티커&gt; · /watchlist
/stats — 신호 성과
/settings — 알림 켜기/끄기"""


# ------------------------------------------------------------------ 공유 상태
class Radar:
    def __init__(self, settings):
        self.s = settings
        self.store = Store(settings.get("storage.url"))
        self.P = make_providers(settings)
        self.lb = LinkBuilder(settings)
        self.lock = asyncio.Lock()
        self.last: dict[str, tuple[float, ScanResult]] = {}

    async def run(self, kind: str, max_age: float = 0) -> ScanResult:
        hit = self.last.get(kind)
        if hit and time.time() - hit[0] <= max_age:
            return hit[1]
        async with self.lock:
            hit = self.last.get(kind)
            if hit and time.time() - hit[0] <= max_age:
                return hit[1]
            fn = {"main": scan_main, "micro": scan_micro, "dip": scan_dip}[kind]
            res = await fn(self.s, self.P, self.store)
            self.last[kind] = (time.time(), res)
            if res.errors:
                log.warning("%s 스캔 오류: %s", kind, res.errors)
            return res

    def prefs(self) -> dict:
        try:
            return {**DEFAULT_PREFS, **json.loads(PREFS.read_text(encoding="utf-8"))}
        except (FileNotFoundError, ValueError):
            return dict(DEFAULT_PREFS)

    def save_prefs(self, p: dict) -> None:
        PREFS.parent.mkdir(parents=True, exist_ok=True)
        PREFS.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def keyboard(ls) -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(b.text, url=b.url) for b in row] for row in ls.rows]
    return InlineKeyboardMarkup(rows) if rows else None


async def send_cards(bot, chat_id: int, radar: Radar, cands, *, title: str, sub: str = "",
                     micro: bool = False, dip: bool = False, record: str | None = None,
                     btc_price: float | None = None):
    """헤더 → 카드마다 (본문 + 그 카드 버튼) → 면책. record 가 있으면 성과 추적 원장에 적는다."""
    await bot.send_message(chat_id, header(title, sub), parse_mode=ParseMode.HTML)
    if micro:
        await bot.send_message(chat_id, MICRO_BANNER, parse_mode=ParseMode.HTML)
    if not cands:
        await bot.send_message(chat_id, "조건을 통과한 코인이 없습니다.", parse_mode=ParseMode.HTML)
    for c in cands:
        ls = links_for(c, radar.lb)
        text = dip_card(c) if dip else card_with_block_note(c, ls)
        if dip and ls.blocked_reason:
            text += f"\n🚨 구매 링크 비활성화: {escape(ls.blocked_reason)}"
        try:
            await bot.send_message(chat_id, text[:radar.s.get("telegram.message_limit", 4000)],
                                   parse_mode=ParseMode.HTML, reply_markup=keyboard(ls),
                                   disable_web_page_preview=True)
        except Exception as e:  # noqa: BLE001 — 한 카드 실패가 나머지를 막지 않게
            log.warning("카드 전송 실패 %s: %s", c.symbol, e)
            continue
        if record:
            try:
                radar.store.add_signal(
                    coin_key=c.coin_key, symbol=c.symbol, track="dip" if dip else c.track, kind=record,
                    price=c.price, btc_price=btc_price, score=c.score, signals_used=c.signals_used,
                    stages={k: v.score for k, v in c.stages.items()}, chain=c.chain, address=c.address,
                    bitget_symbol=c.bitget_spot or c.bitget_futures)
            except Exception as e:  # noqa: BLE001
                log.warning("신호 기록 실패 %s: %s", c.symbol, e)
    await bot.send_message(chat_id, DISCLAIMER, parse_mode=ParseMode.HTML)


# ------------------------------------------------------------------ 권한
def allowed(fn):
    async def wrap(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        radar: Radar = ctx.application.bot_data["radar"]
        cid = update.effective_chat.id if update.effective_chat else None
        if cid not in radar.s.allowed_chat_ids:
            log.info("차단된 chat_id %s", cid)
            if update.effective_message:
                await update.effective_message.reply_text(
                    f"허용되지 않은 채팅입니다. (chat_id {cid}) 관리자에게 이 번호를 알려 주세요.")
            return
        return await fn(update, ctx, radar)
    return wrap


# ------------------------------------------------------------------ 명령어
@allowed
async def cmd_start(update: Update, ctx, radar: Radar):
    await update.effective_message.reply_text(HELP, parse_mode=ParseMode.HTML)


@allowed
async def cmd_scan(update: Update, ctx, radar: Radar):
    m = await update.effective_message.reply_text("🔎 스캔 중입니다… (최대 몇 분)")
    res = await radar.run("main", max_age=300)
    await m.delete()
    await send_cards(ctx.bot, update.effective_chat.id, radar, res.ranked[:10], title="온체인 레이더 스캔",
                     sub=f"{res.stats.get('ranked', 0)}개 통과 · {res.took_s}s", record="scan",
                     btc_price=res.btc_price)


@allowed
async def cmd_micro(update: Update, ctx, radar: Radar):
    m = await update.effective_message.reply_text("🔎 DEX 스캔 중입니다…")
    res = await radar.run("micro", max_age=120)
    await m.delete()
    await send_cards(ctx.bot, update.effective_chat.id, radar, res.ranked[:int(radar.s.get("stage7.top_n", 10))],
                     title="마이크로캡 트랙", sub=f"{res.stats.get('passed', 0)}개 통과", micro=True,
                     record="scan", btc_price=res.btc_price)


@allowed
async def cmd_dip(update: Update, ctx, radar: Radar):
    m = await update.effective_message.reply_text("🔎 되돌림 분석 중입니다…")
    res = await radar.run("dip", max_age=900)
    await m.delete()
    await send_cards(ctx.bot, update.effective_chat.id, radar, res.ranked[:10],
                     title="급락 후 되돌림(피보나치)", sub=f"후보 {res.stats.get('pool', 0)}개 중 {len(res.ranked)}개",
                     dip=True, record="scan", btc_price=res.btc_price)


async def _coin_or_buy(update: Update, ctx, radar: Radar, *, buy_only: bool):
    q = single.parse_query(update.effective_message.text)
    if not q:
        await update.effective_message.reply_text("사용법: /coin UNI  또는  /coin 0x1f98…")
        return
    r = await single.resolve(q, radar.P)
    if r["kind"] == "none":
        await update.effective_message.reply_text(r["why"])
        return
    if r["kind"] == "choices":
        mode = "b" if buy_only else "c"
        kb = [[InlineKeyboardButton(f"{i['name']} ({i['symbol']}) · #{i['rank'] or '-'}",
                                    callback_data=f"pick:{mode}:{i['id']}"[:64])] for i in r["items"]]
        await update.effective_message.reply_text(
            f"'{escape(q)}' 심볼이 {len(r['items'])}개입니다. 어느 코인인가요?",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return
    await _render_one(ctx.bot, update.effective_chat.id, radar, r, buy_only)


async def _render_one(bot, chat_id, radar: Radar, r: dict, buy_only: bool):
    if r["kind"] == "cg":
        c = await single.build_cg(r["id"], radar.s, radar.P, radar.store)
    else:
        c = await single.build_address(r["pair"], radar.s, radar.P, radar.store)
    if c is None:
        await bot.send_message(chat_id, "코인 정보를 불러오지 못했습니다.")
        return
    ls = links_for(c, radar.lb)
    if buy_only:
        head = f"🛒 <b>{escape(c.symbol)}</b> 구매 링크"
        if c.chain and c.address:
            head += f"\nCA: <code>{escape(c.address)}</code>"
        if ls.blocked_reason:
            head += f"\n🚨 구매 링크 비활성화: {escape(ls.blocked_reason)}"
        await bot.send_message(chat_id, head, parse_mode=ParseMode.HTML, reply_markup=keyboard(ls))
        return
    await bot.send_message(chat_id, card_with_block_note(c, ls) + "\n\n" + DISCLAIMER,
                           parse_mode=ParseMode.HTML, reply_markup=keyboard(ls),
                           disable_web_page_preview=True)


@allowed
async def cmd_coin(update, ctx, radar):
    await _coin_or_buy(update, ctx, radar, buy_only=False)


@allowed
async def cmd_buy(update, ctx, radar):
    await _coin_or_buy(update, ctx, radar, buy_only=True)


async def on_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    radar: Radar = ctx.application.bot_data["radar"]
    if q.message.chat.id not in radar.s.allowed_chat_ids:
        await q.answer("허용되지 않은 채팅입니다.")
        return
    await q.answer()
    _, mode, cg_id = q.data.split(":", 2)
    await q.edit_message_text(f"⏳ {cg_id} 불러오는 중…")
    await _render_one(ctx.bot, q.message.chat.id, radar, {"kind": "cg", "id": cg_id}, buy_only=(mode == "b"))


@allowed
async def cmd_revenue(update, ctx, radar: Radar):
    res = radar.last.get("main")
    rows = res[1].revenue_ranking if res else None
    if not rows:
        from scanners.stage5_revenue import surge_ranking
        all_rows = await radar.P.defillama.revenue_by_token() or []
        rows = surge_ranking(all_rows, radar.s.get)
    await update.effective_message.reply_text(
        header("매출 급증 프로토콜", "7일 일평균 매출 / 직전 30일 일평균") + "\n\n" + revenue_table(rows, 15),
        parse_mode=ParseMode.HTML)


@allowed
async def cmd_unlock(update, ctx, radar: Radar):
    if not radar.s.has("TOKENOMIST_API_KEY"):
        await update.effective_message.reply_text(
            "🔒 언락 캘린더는 Tokenomist 키가 있어야 켜집니다.\n"
            "(DefiLlama 무료 언락 API 는 유료 전환되어 HTTP 402 를 줍니다 — 2026-09-21 확인)")
        return
    await update.effective_message.reply_text("언락 스캐너는 Phase 2 에서 연결됩니다.")


@allowed
async def cmd_news(update, ctx, radar: Radar):
    if not (radar.s.has("ANTHROPIC_API_KEY") and radar.s.get("stage6.enabled")):
        await update.effective_message.reply_text(
            "📰 실질 호재 브리핑은 Phase 2 기능입니다(뉴스 수집 + Claude 분류, Anthropic 키 필요).")
        return
    await update.effective_message.reply_text("뉴스 스캐너는 Phase 2 에서 연결됩니다.")


@allowed
async def cmd_watch(update, ctx, radar: Radar):
    q = single.parse_query(update.effective_message.text)
    if not q:
        await update.effective_message.reply_text("사용법: /watch UNI")
        return
    r = await single.resolve(q, radar.P)
    if r["kind"] == "choices":
        items = ", ".join(f"{i['name']}({i['id']})" for i in r["items"][:5])
        await update.effective_message.reply_text(f"심볼이 여러 개입니다: {items}\n코인게코 id 로 다시: /watch <id>")
        return
    if r["kind"] == "none":
        await update.effective_message.reply_text(r["why"])
        return
    if r["kind"] == "cg":
        key, sym = f"cg:{r['id']}", q.upper()
    else:
        p = r["pair"]["baseToken"]
        key, sym = f"{r['pair']['chainId']}:{p['address']}", p["symbol"].upper()
    ok = radar.store.watch(update.effective_chat.id, key, sym)
    await update.effective_message.reply_text(f"👀 {sym} 관심 등록{'' if ok else '(이미 등록됨)'} — 신호가 뜨면 따로 알려드립니다.")


@allowed
async def cmd_unwatch(update, ctx, radar: Radar):
    q = single.parse_query(update.effective_message.text)
    n = radar.store.unwatch(update.effective_chat.id, q) if q else 0
    await update.effective_message.reply_text(f"해제 {n}건")


@allowed
async def cmd_watchlist(update, ctx, radar: Radar):
    ws = radar.store.watchlist(update.effective_chat.id)
    txt = "관심 코인이 없습니다." if not ws else "👀 관심 코인\n" + "\n".join(
        f"· {escape(w.symbol)} <code>{escape(w.coin_key)}</code>" for w in ws)
    await update.effective_message.reply_text(txt, parse_mode=ParseMode.HTML)


@allowed
async def cmd_stats(update, ctx, radar: Radar):
    parts = [tracker.stats_text(tracker.stats(radar.store, "main"), "메인 트랙 성과"),
             tracker.stats_text(tracker.stats(radar.store, "micro"), "마이크로캡 성과"),
             tracker.stats_text(tracker.stats(radar.store, "dip"), "되돌림 성과")]
    await update.effective_message.reply_text("\n\n".join(parts), parse_mode=ParseMode.HTML)


@allowed
async def cmd_settings(update, ctx, radar: Radar):
    p = radar.prefs()
    arg = single.parse_query(update.effective_message.text).lower()
    names = {"alerts": "실시간 알림", "micro": "마이크로캡 알림", "dip": "되돌림 알림"}
    keymap = {"alerts": "alerts", "micro": "micro_alerts", "dip": "dip_alerts"}
    if arg in keymap:
        p[keymap[arg]] = not p[keymap[arg]]
        radar.save_prefs(p)
    lines = [f"⚙️ 설정 (바꾸려면 /settings alerts · micro · dip)"]
    for k, label in names.items():
        lines.append(f"· {label}: {'켜짐' if p[keymap[k]] else '꺼짐'}")
    lines.append(f"· 브리핑: {', '.join(radar.s.get('telegram.briefing_times', []))} KST (config.yaml)")
    await update.effective_message.reply_text("\n".join(lines))


# ------------------------------------------------------------------ 스케줄 작업
async def job_briefing(ctx: ContextTypes.DEFAULT_TYPE):
    radar: Radar = ctx.application.bot_data["radar"]
    n = int(radar.s.get("telegram.briefing_top_n", 7))
    main = await radar.run("main", max_age=600)
    dip = await radar.run("dip", max_age=1800)
    micro = await radar.run("micro", max_age=300)
    t = dt.datetime.now(KST)
    for cid in radar.s.broadcast_chat_ids:
        await send_cards(ctx.bot, cid, radar, main.ranked[:n], title=f"{'오전' if t.hour < 12 else '오후'} 브리핑",
                         sub=f"메인 상위 {min(n, len(main.ranked))}개", record="briefing", btc_price=main.btc_price)
        if dip.ranked:
            await send_cards(ctx.bot, cid, radar, dip.ranked[:3], title="되돌림 상위 3", dip=True,
                             record="briefing", btc_price=main.btc_price)
        if micro.ranked:
            await send_cards(ctx.bot, cid, radar, micro.ranked[:3], title="마이크로캡 상위 3", micro=True,
                             record="briefing", btc_price=main.btc_price)


async def _alert(ctx, radar: Radar, cands, *, kind: str, threshold: float, **kw):
    fresh = []
    for c in cands:
        if c.score < threshold:
            continue
        if radar.store.should_send(c.coin_key, c.score, c.risks,
                                   cooldown_h=radar.s.get("scoring.cooldown_hours", 6),
                                   bypass_jump=radar.s.get("scoring.cooldown_bypass_score_jump", 12)):
            fresh.append(c)
    if not fresh:
        return
    for cid in radar.s.broadcast_chat_ids:
        await send_cards(ctx.bot, cid, radar, fresh[:5], record="alert", **kw)
    for c in fresh[:5]:
        radar.store.mark_sent(c.coin_key, c.score, c.risks)


async def job_main(ctx: ContextTypes.DEFAULT_TYPE):
    radar: Radar = ctx.application.bot_data["radar"]
    res = await radar.run("main", max_age=0)
    p = radar.prefs()
    if p["alerts"]:
        await _alert(ctx, radar, res.ranked, kind="main", threshold=radar.s.get("scoring.alert_threshold", 75),
                     title="⚡ 실시간 포착", btc_price=res.btc_price)
    # 관심 코인 — 순위에 들면 등록한 사람에게만
    ranked = {c.coin_key: c for c in res.ranked}
    for w in radar.store.watchlist():
        c = ranked.get(w.coin_key)
        if c and radar.store.should_send(f"watch:{w.chat_id}:{c.coin_key}", c.score, c.risks,
                                         cooldown_h=radar.s.get("scoring.cooldown_hours", 6),
                                         bypass_jump=radar.s.get("scoring.cooldown_bypass_score_jump", 12)):
            await send_cards(ctx.bot, w.chat_id, radar, [c], title=f"👀 관심 코인 {c.symbol} 신호",
                             record="alert", btc_price=res.btc_price)
            radar.store.mark_sent(f"watch:{w.chat_id}:{c.coin_key}", c.score, c.risks)


async def job_micro(ctx: ContextTypes.DEFAULT_TYPE):
    radar: Radar = ctx.application.bot_data["radar"]
    if not radar.prefs()["micro_alerts"]:
        return
    res = await radar.run("micro", max_age=0)
    await _alert(ctx, radar, res.ranked, kind="micro", threshold=radar.s.get("scoring.alert_threshold", 75),
                 title="⚡ 마이크로캡 포착", micro=True, btc_price=res.btc_price)


async def job_dip(ctx: ContextTypes.DEFAULT_TYPE):
    radar: Radar = ctx.application.bot_data["radar"]
    if not radar.prefs()["dip_alerts"]:
        return
    res = await radar.run("dip", max_age=0)
    await _alert(ctx, radar, res.ranked, kind="dip", threshold=radar.s.get("stage8.alert_threshold", 60),
                 title="📐 급락 후 되돌림 포착", dip=True, btc_price=res.btc_price)


async def job_track(ctx: ContextTypes.DEFAULT_TYPE):
    radar: Radar = ctx.application.bot_data["radar"]
    await tracker.run_once(radar.s, radar.store, radar.P)


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("핸들러 오류: %s", ctx.error, exc_info=ctx.error)


# ------------------------------------------------------------------ 조립
def build_app(settings) -> Application:
    token = settings.key("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(".env 에 TELEGRAM_BOT_TOKEN 이 없습니다 (@BotFather → /newbot).")
    if not settings.allowed_chat_ids:
        log.warning("허용 chat_id 가 비어 있습니다 — 아무도 명령을 쓸 수 없습니다. "
                    "TELEGRAM_ALLOWED_CHAT_IDS 를 채우세요 (python main.py chats 로 확인).")
    app = Application.builder().token(token).build()
    app.bot_data["radar"] = Radar(settings)
    for name, fn in (("start", cmd_start), ("help", cmd_start), ("scan", cmd_scan), ("micro", cmd_micro),
                     ("dip", cmd_dip), ("coin", cmd_coin), ("buy", cmd_buy), ("revenue", cmd_revenue),
                     ("unlock", cmd_unlock), ("news", cmd_news), ("watch", cmd_watch),
                     ("unwatch", cmd_unwatch), ("watchlist", cmd_watchlist), ("stats", cmd_stats),
                     ("settings", cmd_settings)):
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_pick, pattern=r"^pick:"))
    app.add_error_handler(on_error)

    jq = app.job_queue
    for hhmm in settings.get("telegram.briefing_times", ["08:00", "20:00"]):
        h, m = (int(x) for x in hhmm.split(":"))
        jq.run_daily(job_briefing, time=dt.time(h, m, tzinfo=KST), name=f"briefing {hhmm}")
    sched = settings.get
    jq.run_repeating(job_main, interval=int(sched("schedule.pool_ab_minutes", 20)) * 60, first=60, name="main")
    jq.run_repeating(job_micro, interval=int(sched("schedule.pool_c_minutes", 5)) * 60, first=120, name="micro")
    jq.run_repeating(job_dip, interval=int(sched("stage8.scan_minutes", 60)) * 60, first=300, name="dip")
    jq.run_repeating(job_track, interval=int(sched("schedule.tracking_minutes", 60)) * 60, first=600, name="track")
    return app


def run_bot(settings) -> int:
    app = build_app(settings)
    log.info("봇 시작 — 허용 %d개 · 브로드캐스트 %s", len(settings.allowed_chat_ids), settings.broadcast_chat_ids)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    return 0


async def broadcast_result(settings, store, res: ScanResult, *, kind: str, title: str, top: int):
    """main.py scan --send 용 — 봇을 상시 구동하지 않고 한 번 보낸다."""
    from telegram import Bot
    token = settings.key("TELEGRAM_BOT_TOKEN")
    if not token:
        print(".env 에 TELEGRAM_BOT_TOKEN 이 없습니다.")
        return
    radar = Radar.__new__(Radar)
    radar.s, radar.store, radar.lb = settings, store, LinkBuilder(settings)
    async with Bot(token) as bot:
        micro = any(c.track == "micro" for c in res.ranked)
        for cid in settings.broadcast_chat_ids:
            await send_cards(bot, cid, radar, res.ranked[:top], title=title, micro=micro,
                             record=kind, btc_price=res.btc_price)
