"""저장소 — SQLite 로 시작하고 URL 만 바꾸면 Postgres 로 간다(SQLAlchemy).

테이블
  signals          발송한 신호 전부 (성과 추적의 원장 — 지우지 않는다)
  signal_returns   신호별 1/3/7/14일 뒤 가격·수익률·BTC 대비 초과수익
  holder_snapshots 홀더 수·상위10 집중도 스냅샷. API 가 과거값을 안 주므로 봇이 직접 쌓는다
  price_snapshots  가격·거래량 스냅샷 (7일 평균 거래량이 API 에 없는 DEX 토큰용)
  alert_log        쿨다운 판단용 마지막 발송 기록
  watchlist        /watch 로 등록한 관심 코인
  clicks           구매 버튼 클릭(콜백 경유, 옵션)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import (Float, Integer, String, Text, UniqueConstraint, create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from settings import ROOT


class Base(DeclarativeBase):
    pass


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    coin_key: Mapped[str] = mapped_column(String(160), index=True)   # cg:<id> 또는 <chain>:<addr>
    symbol: Mapped[str] = mapped_column(String(40))
    track: Mapped[str] = mapped_column(String(20))                   # main | micro
    kind: Mapped[str] = mapped_column(String(20))                    # briefing | alert | scan
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    btc_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float] = mapped_column(Float)
    signals_used: Mapped[int] = mapped_column(Integer)
    stages_json: Mapped[str] = mapped_column(Text)                   # 각 Stage 세부 점수
    chain: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bitget_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)

    @property
    def stages(self) -> dict:
        return json.loads(self.stages_json or "{}")


class SignalReturn(Base):
    __tablename__ = "signal_returns"
    __table_args__ = (UniqueConstraint("signal_id", "horizon_days"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(Integer, index=True)
    horizon_days: Mapped[int] = mapped_column(Integer)
    ts: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ret_pct: Mapped[float] = mapped_column(Float)          # 조회 실패·유동성 소멸 = -100
    btc_ret_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    excess_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dead: Mapped[int] = mapped_column(Integer, default=0)  # 1 = 가격을 못 구해 -100 처리


class HolderSnapshot(Base):
    __tablename__ = "holder_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    coin_key: Mapped[str] = mapped_column(String(160), index=True)
    holder_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    top10_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="goplus")


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    coin_key: Mapped[str] = mapped_column(String(160), index=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_usd: Mapped[float | None] = mapped_column(Float, nullable=True)   # 선물 OI — 24h 변화율용


class AlertLog(Base):
    __tablename__ = "alert_log"
    coin_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    ts: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    risks: Mapped[str] = mapped_column(Text, default="")   # 쉼표로 이은 리스크 태그


class Watch(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("chat_id", "coin_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    coin_key: Mapped[str] = mapped_column(String(160))
    symbol: Mapped[str] = mapped_column(String(40))
    ts: Mapped[float] = mapped_column(Float)


class Click(Base):
    __tablename__ = "clicks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[float] = mapped_column(Float)
    chat_id: Mapped[int] = mapped_column(Integer)
    coin_key: Mapped[str] = mapped_column(String(160))
    venue: Mapped[str] = mapped_column(String(30))


class Store:
    def __init__(self, url: str | None = None):
        url = url or "sqlite:///data/radar.db"
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and ":memory:" not in url:
            rel = url[len("sqlite:///"):]
            path = Path(rel) if Path(rel).is_absolute() else ROOT / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{path.as_posix()}"
        self.engine = create_engine(url, future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def session(self) -> Session:
        return self.Session()

    # ---------------------------------------------------------------- 쿨다운
    def should_send(self, coin_key: str, score: float, risks: set[str], *,
                    cooldown_h: float, bypass_jump: float, now: float | None = None) -> bool:
        """같은 코인 6시간 재발송 금지. 점수가 크게 뛰었거나 새 리스크가 생기면 예외(사양서)."""
        now = time.time() if now is None else now
        with self.session() as s:
            row = s.get(AlertLog, coin_key)
            if row is None or now - row.ts >= cooldown_h * 3600:
                return True
            if score - row.score >= bypass_jump:
                return True
            old = set(filter(None, (row.risks or "").split(",")))
            return bool(risks - old)

    def mark_sent(self, coin_key: str, score: float, risks: set[str], now: float | None = None):
        with self.session() as s:
            s.merge(AlertLog(coin_key=coin_key, ts=time.time() if now is None else now, score=score,
                             risks=",".join(sorted(risks))))
            s.commit()

    # ---------------------------------------------------------------- 신호 원장
    def add_signal(self, **kw) -> int:
        stages = kw.pop("stages", {})
        with self.session() as s:
            sig = Signal(ts=kw.pop("ts", time.time()), stages_json=json.dumps(stages, ensure_ascii=False), **kw)
            s.add(sig)
            s.commit()
            return sig.id

    # ---------------------------------------------------------------- 스냅샷
    def add_holder_snapshot(self, coin_key: str, holder_count, top10_pct, source="goplus",
                            now: float | None = None):
        with self.session() as s:
            s.add(HolderSnapshot(ts=time.time() if now is None else now, coin_key=coin_key,
                                 holder_count=holder_count, top10_pct=top10_pct, source=source))
            s.commit()

    def holder_history(self, coin_key: str, since: float) -> list[HolderSnapshot]:
        with self.session() as s:
            return list(s.scalars(select(HolderSnapshot)
                                  .where(HolderSnapshot.coin_key == coin_key, HolderSnapshot.ts >= since)
                                  .order_by(HolderSnapshot.ts)))

    def add_price_snapshots(self, rows: list[dict], now: float | None = None):
        """한 번 스캔에 수백 개 — 트랜잭션 하나로 넣는다."""
        t = time.time() if now is None else now
        with self.session() as s:
            s.add_all([PriceSnapshot(ts=t, coin_key=r["coin_key"], price=r.get("price"),
                                     volume_24h=r.get("volume_24h"),
                                     liquidity_usd=r.get("liquidity_usd"), oi_usd=r.get("oi_usd"))
                       for r in rows])
            s.commit()

    def snapshot_near(self, coin_key: str, target_ts: float, tolerance_s: float = 3 * 3600):
        """target_ts 에 가장 가까운 스냅샷(허용오차 안). 없으면 None — 추정하지 않는다."""
        with self.session() as s:
            rows = list(s.scalars(select(PriceSnapshot).where(
                PriceSnapshot.coin_key == coin_key,
                PriceSnapshot.ts >= target_ts - tolerance_s,
                PriceSnapshot.ts <= target_ts + tolerance_s)))
        return min(rows, key=lambda r: abs(r.ts - target_ts)) if rows else None

    def price_history(self, coin_key: str, since: float) -> list[PriceSnapshot]:
        with self.session() as s:
            return list(s.scalars(select(PriceSnapshot)
                                  .where(PriceSnapshot.coin_key == coin_key, PriceSnapshot.ts >= since)
                                  .order_by(PriceSnapshot.ts)))

    # ---------------------------------------------------------------- 관심 코인
    def watch(self, chat_id: int, coin_key: str, symbol: str) -> bool:
        with self.session() as s:
            if s.scalar(select(Watch).where(Watch.chat_id == chat_id, Watch.coin_key == coin_key)):
                return False
            s.add(Watch(chat_id=chat_id, coin_key=coin_key, symbol=symbol, ts=time.time()))
            s.commit()
            return True

    def unwatch(self, chat_id: int, symbol_or_key: str) -> int:
        with self.session() as s:
            rows = list(s.scalars(select(Watch).where(Watch.chat_id == chat_id)))
            gone = [w for w in rows if w.coin_key == symbol_or_key
                    or w.symbol.upper() == symbol_or_key.upper()]
            for w in gone:
                s.delete(w)
            s.commit()
            return len(gone)

    def watchlist(self, chat_id: int | None = None) -> list[Watch]:
        with self.session() as s:
            q = select(Watch)
            if chat_id is not None:
                q = q.where(Watch.chat_id == chat_id)
            return list(s.scalars(q.order_by(Watch.ts)))

    def log_click(self, chat_id: int, coin_key: str, venue: str):
        with self.session() as s:
            s.add(Click(ts=time.time(), chat_id=chat_id, coin_key=coin_key, venue=venue))
            s.commit()
