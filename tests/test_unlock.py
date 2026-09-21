"""Stage 4 — 픽스처는 Tokenomist 공식 OpenAPI 의 예시값 그대로(ZRO, 2026-09-21 확인)."""
import time

from providers.tokenomist import parse
from scanners import stage4_unlock as s4
from scanners.model import Candidate

NOW = time.time()
CFG = {"stage4.big_unlock_pct": 2.0, "stage4.penalty_per_pct": 3, "stage4.alert_days": [7, 3, 1]}


def cfg(k, d=None):
    return CFG.get(k, d)


def spec_row(days_ahead, pct=12.908971602455575):
    """openapi.yaml /v1/unlock/events/upcoming 의 예시 항목 구조."""
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(NOW + days_ahead * 86400 + 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"tokenId": "layerzero", "tokenName": "LayerZero", "tokenSymbol": "ZRO", "marketCap": 278811255,
            "releasedPercentage": 35.28, "listedMethod": "INTERNAL", "dataSource": "Whitepaper",
            "upcomingEvent": {"unlockDate": iso, "referencePrice": 1.4,
                              "cliffUnlocks": {"totalCliffAmount": 25708332.666, "totalCliffValue": 35991665.73,
                                               "valueToMarketCap": pct, "allocationBreakdown": [
                                                   {"allocationName": "Investors", "standardAllocationName": "Private Investors",
                                                    "cliffAmount": 20e6, "cliffValue": 28e6},
                                                   {"allocationName": "Core", "standardAllocationName": "Team",
                                                    "cliffAmount": 5.7e6, "cliffValue": 8e6}]}}}


def test_스펙_응답을_파싱():
    u = parse(spec_row(5))
    assert u["symbol"] == "ZRO" and round(u["pct"], 1) == 12.9
    assert u["recipients"][0][0] == "Private Investors"


def test_카드에_D_day_비중_수령주체_그리고_감점():
    c = Candidate(coin_key="cg:layerzero", symbol="ZRO", cg_id="layerzero")
    s4.evaluate([c], [parse(spec_row(5))], cfg, now=NOW)
    tag = next(t for t in c.tags if "언락" in t)
    assert "D-5" in tag and "12.9%" in tag and "VC·팀" in tag
    assert "언락" in c.risks and c.penalties[0][1] == round(12.908971602455575 * 3, 1)


def test_작은_언락은_경고만_감점_없음():
    c = Candidate(coin_key="cg:layerzero", symbol="ZRO", cg_id="layerzero")
    s4.evaluate([c], [parse(spec_row(5, pct=0.8))], cfg, now=NOW)
    assert any("언락" in t for t in c.tags) and not c.penalties


def test_D7_D3_D1_에만_알림():
    rows = [parse(spec_row(d)) for d in (1, 2, 3, 6, 7, 10)]
    assert [d for _, d in s4.due_alerts(rows, cfg, now=NOW)] == [1, 3, 7]


def test_키_없으면_아무것도_안_붙는다():
    c = Candidate(coin_key="x", symbol="ZRO")
    s4.evaluate([c], None, cfg)
    assert not c.tags and not c.penalties
