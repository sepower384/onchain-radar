"""ONCHAIN RADAR 진입점.

  python main.py run            텔레그램 봇 상시 구동 (스케줄 브리핑·실시간 알림·명령어)
  python main.py scan           메인 트랙 1회 스캔 → 콘솔 출력 (텔레그램 없이)
  python main.py micro          마이크로캡 트랙 1회 → 콘솔 출력
  python main.py dip            급락 후 피보나치 되돌림 1회 → 콘솔 출력
  python main.py revenue        매출 급증 랭킹 → 콘솔 출력
  python main.py chats          봇에게 말 건 chat_id 확인 (허용 목록 설정용)
  python main.py track          성과 추적 1회 (1/3/7/14일 수익률 기록)
  python main.py tick           깃허브 Actions 용: 밀린 명령 처리 + 주기 돌아온 작업만 하고 끝

  --top N      출력 개수 (기본 10)
  --send       scan·micro 결과를 텔레그램 브로드캐스트 방으로 실제 전송
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from html import unescape

# 윈도우 시스템 인증서 저장소를 쓴다 — 이 PC 는 기본 certifi 번들로는 텔레그램 SSL 검증이 실패했다
# (CERTIFICATE_VERIFY_FAILED, 2026-09-21 첫 구동). 모든 import 보다 먼저 해야 먹는다.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import settings as S  # noqa: E402

LOG_FMT = "%(asctime)s %(levelname).1s %(name)s: %(message)s"


def setup_logging(level=logging.INFO):
    (S.ROOT / "logs").mkdir(exist_ok=True)
    handlers = [logging.FileHandler(S.ROOT / "logs" / "radar.log", encoding="utf-8")]
    import os
    # 깃허브 Actions 에선 터미널이 아니지만 로그가 화면(Actions 로그)에 보여야 한다
    if (sys.stdout and sys.stdout.isatty()) or os.environ.get("GITHUB_ACTIONS"):
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=level, format=LOG_FMT, handlers=handlers, force=True)
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def plain(html: str) -> str:
    """콘솔용 — 텔레그램 HTML 태그를 벗긴다."""
    return unescape(re.sub(r"<[^>]+>", "", html))


def print_cards(title, res, top, lb):
    from bot.formatter import DISCLAIMER, MICRO_BANNER, card_with_block_note, header, links_for
    print(plain(header(title, f"{len(res.ranked)}개 통과 · {res.took_s}s")))
    if res.stats:
        print("  stats:", res.stats)
    if res.errors:
        print("  ⚠ 오류:", *res.errors, sep="\n    ")
    micro = any(c.track == "micro" for c in res.ranked)
    if micro:
        print(plain(MICRO_BANNER))
    for i, c in enumerate(res.ranked[:top], 1):
        ls = links_for(c, lb)
        print(f"\n── {i} " + "─" * 60)
        print(plain(card_with_block_note(c, ls)))
        for row in ls.rows:
            print("   " + "  ".join(f"[{b.text}]" for b in row))
            for b in row:
                print(f"      {b.text:18} {b.url}")
    print("\n" + plain(DISCLAIMER))


async def cmd_scan(args, s, micro=False):
    from bot.buy_links import LinkBuilder
    from scanners.pipeline import make_providers, scan_main, scan_micro
    from storage.db import Store
    store = Store(s.get("storage.url"))
    P = make_providers(s)
    try:
        res = await (scan_micro(s, P, store) if micro else scan_main(s, P, store))
    finally:
        await P.close()
    lb = LinkBuilder(s)
    print_cards("마이크로캡 트랙" if micro else "온체인 레이더 스캔", res, args.top, lb)
    if args.send:
        from bot.telegram_app import broadcast_result
        await broadcast_result(s, store, res, kind="scan",
                               title="마이크로캡 트랙" if micro else "온체인 레이더 스캔", top=args.top)
    return res


async def cmd_dip(args, s):
    from bot.buy_links import LinkBuilder
    from bot.formatter import DISCLAIMER, dip_card, header, links_for
    from scanners.pipeline import make_providers, scan_dip
    from storage.db import Store
    store = Store(s.get("storage.url"))
    P = make_providers(s)
    try:
        res = await scan_dip(s, P, store)
    finally:
        await P.close()
    lb = LinkBuilder(s)
    print(plain(header("급락 후 되돌림(피보나치)", f"{len(res.ranked)}개 · 후보 {res.stats.get('pool')}개 · {res.took_s}s")))
    for e in res.errors:
        print("  ⚠", e)
    for i, c in enumerate(res.ranked[:args.top], 1):
        ls = links_for(c, lb)
        print(f"\n── {i} " + "─" * 60)
        print(plain(dip_card(c)))
        for row in ls.rows:
            print("   " + "  ".join(f"[{b.text}]" for b in row))
    print("\n" + plain(DISCLAIMER))
    return res


async def cmd_revenue(args, s):
    from bot.formatter import revenue_table
    from providers.defillama import DefiLlama
    d = DefiLlama(s)
    try:
        rows = await d.revenue_by_token()
    finally:
        await d.close()
    if rows is None:
        print("DefiLlama 조회 실패")
        return
    from scanners.stage5_revenue import surge_ranking
    rk = surge_ranking(rows, s.get)
    n_tok = sum(1 for r in rows if r.get("gecko_id"))
    print(f"매출 급증 {len(rk)}개 (토큰 있는 프로토콜 {n_tok}개 중 · "
          f"기준 {s.get('stage5.surge_ratio_min')}배 · 7일 ${s.get('stage5.min_7d_revenue_usd'):,} · "
          f"기준선 일 ${s.get('stage5.min_base_daily_usd'):,} 이상)\n")
    print(plain(revenue_table(rk, args.top)))


async def cmd_chats(s):
    import httpx
    tok = s.key("TELEGRAM_BOT_TOKEN")
    if not tok:
        print(".env 에 TELEGRAM_BOT_TOKEN 이 없습니다.")
        return
    async with httpx.AsyncClient(timeout=20, verify=False) as c:
        r = (await c.get(f"https://api.telegram.org/bot{tok}/getUpdates")).json()
    seen = {}
    for u in r.get("result", []):
        m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        ch = m.get("chat") or {}
        if ch.get("id"):
            seen[ch["id"]] = f"{ch.get('type')} · {ch.get('title') or ch.get('username') or ch.get('first_name')}"
    if not seen:
        print("받은 메시지가 없습니다. 봇에게 아무 말이나 한 번 보낸 뒤 다시 실행하세요.")
    for k, v in seen.items():
        print(f"{k}\t{v}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="ONCHAIN RADAR")
    ap.add_argument("cmd", choices=["run", "scan", "micro", "dip", "revenue", "chats", "track", "tick"])
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    s = S.load()

    if args.cmd == "run":
        from bot.telegram_app import run_bot
        return run_bot(s)
    if args.cmd == "scan":
        asyncio.run(cmd_scan(args, s))
    elif args.cmd == "micro":
        asyncio.run(cmd_scan(args, s, micro=True))
    elif args.cmd == "dip":
        asyncio.run(cmd_dip(args, s))
    elif args.cmd == "revenue":
        asyncio.run(cmd_revenue(args, s))
    elif args.cmd == "chats":
        asyncio.run(cmd_chats(s))
    elif args.cmd == "tick":
        from bot.tick import run_tick
        from storage.db import Store
        asyncio.run(run_tick(s))
        Store(s.get("storage.url")).prune()
    elif args.cmd == "track":
        from tracking.tracker import run_once
        asyncio.run(run_once(s))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
