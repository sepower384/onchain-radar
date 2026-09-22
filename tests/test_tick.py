"""tick 모드 — 5분마다 깨어날 때 어떤 작업이 '돌 차례'인가."""
import datetime as dt

from bot.tick import due, due_daily
from settings import KST
from storage.db import Store


def ts(h, m, day=21):
    return dt.datetime(2026, 9, day, h, m, tzinfo=KST).timestamp()


def test_주기_작업은_마지막_실행_후_주기가_지나야_돈다():
    st = Store("sqlite:///:memory:")
    assert due(st, "main", 20, ts(10, 0))                  # 처음이면 돈다
    st.job_mark("main", ts(10, 0))
    assert not due(st, "main", 20, ts(10, 15))
    assert due(st, "main", 20, ts(10, 19, ) + 30)          # 1분 여유 — 19.5분에 깨어도 돈다


def test_하루_한번_작업은_시각이_지나고_오늘_안_돌았을_때만():
    st = Store("sqlite:///:memory:")
    assert not due_daily(st, "briefing 08:00", "08:00", ts(7, 55))
    assert due_daily(st, "briefing 08:00", "08:00", ts(8, 3))
    st.job_mark("briefing 08:00", ts(8, 3))
    assert not due_daily(st, "briefing 08:00", "08:00", ts(9, 0))      # 오늘 이미 돌았다
    assert due_daily(st, "briefing 08:00", "08:00", ts(8, 1, day=22))  # 다음 날


def test_깃허브가_늦게_깨워도_그날은_돈다():
    st = Store("sqlite:///:memory:")
    assert due_daily(st, "briefing 20:00", "20:00", ts(22, 40))


def test_요일_지정():
    st = Store("sqlite:///:memory:")
    assert not due_daily(st, "tune", "21:00", ts(21, 30, day=21), weekday=6)   # 9/21 = 월
    assert due_daily(st, "tune", "21:00", ts(21, 30, day=27), weekday=6)       # 9/27 = 일


def test_텔레그램_offset_보관():
    st = Store("sqlite:///:memory:")
    assert st.job_last("tg_offset") is None
    st.job_mark("tg_offset", 123456)
    assert st.job_last("tg_offset") == 123456
