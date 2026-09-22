"""스캔 파이프라인을 흐르는 한 코인의 모든 것.

원칙 (사양서)
- 코인 고유키 = `cg:<coingecko_id>` 또는 `<chain>:<contract>`. 티커는 표시용일 뿐이다.
- 어떤 Stage 가 데이터를 못 구하면 그 Stage 의 score 는 **None** 이다(0 이 아니다).
  점수 엔진이 None 을 빼고 남은 가중치를 재정규화한다. 카드에 "사용된 신호 n/6".
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 신호 이름 — config.scoring.weights 의 키와 같다. 순서 = 카드 표시 순서.
SIGNALS = ("accumulation", "reaction", "derivatives", "revenue", "news", "security")
SIGNAL_KO = {"accumulation": "매집", "reaction": "반응", "derivatives": "파생",
             "revenue": "매출", "news": "호재", "security": "안전"}


@dataclass
class StageResult:
    score: float | None = None            # 0~100. None = 데이터 없음(재정규화에서 제외)
    line: str = ""                        # 카드에 찍을 한 줄 (세력 말투)
    tags: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class Candidate:
    coin_key: str
    symbol: str
    name: str = ""
    track: str = "main"                   # main | micro
    cg_id: str | None = None

    # 온체인 식별자 (DEX 링크·GoPlus·홀더 스냅샷의 기준)
    chain: str | None = None
    address: str | None = None
    pair_url: str | None = None

    # 비트겟 — API 로 상장 확인된 심볼만 들어온다
    bitget_spot: str | None = None
    bitget_futures: str | None = None

    # 시장
    price: float | None = None
    mcap: float | None = None
    fdv: float | None = None
    change_24h: float | None = None
    change_7d: float | None = None
    volume_24h: float | None = None
    volume_avg_7d: float | None = None
    # 거래량 비율은 **같은 출처끼리** 나눠야 한다. 코인게코 전체 합계 ÷ 비트겟 일봉 평균으로
    # 나눴더니 BTC 가 149배로 찍혔다(2026-09-21 첫 실행). 비트겟 일봉을 쓰면 분자도 비트겟.
    bitget_volume_24h: float | None = None
    volume_ratio_src: str | None = None
    liquidity_usd: float | None = None
    pair_age_h: float | None = None
    funding_pct: float | None = None
    oi_usd: float | None = None

    security: object | None = None       # providers.goplus.Security
    stages: dict[str, StageResult] = field(default_factory=dict)
    penalties: list[tuple[str, float]] = field(default_factory=list)
    risks: set[str] = field(default_factory=set)
    tags: list[str] = field(default_factory=list)

    score: float = 0.0
    signals_used: int = 0
    breakdown: list = field(default_factory=list)   # [(신호, 점수, 정규화 가중치, 기여점)]
    research: dict | None = None                    # 카드 보강 조사(오른 이유·백커)

    @property
    def signals_total(self) -> int:
        # 마이크로캡은 신호 묶음이 다르다(시총·안전·매집·반응 4개). "n/6" 으로 찍으면 오해한다.
        return 4 if self.track == "micro" else len(SIGNALS)

    def stage(self, name: str) -> StageResult:
        return self.stages.setdefault(name, StageResult())

    def penalize(self, reason: str, points: float, risk_tag: str | None = None):
        if points > 0:
            self.penalties.append((reason, points))
        if risk_tag:
            self.risks.add(risk_tag)

    @property
    def cex_label(self) -> str:
        if self.bitget_futures:
            return "비트겟 선물 O"
        if self.bitget_spot:
            return "비트겟 현물 O"
        return "CEX 미상장"
