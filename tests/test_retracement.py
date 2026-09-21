"""Stage 8 피보나치 되돌림 — 합성 캔들로 패턴 판정을 고정한다."""
from scanners.stage8_retracement import analyze

CFG = {"stage8.swing_window_days": 60, "stage8.leg_lookback_days": 45, "stage8.min_days_since_high": 2,
       "stage8.min_leg_gain_pct": 60.0, "stage8.dd_min_pct": 30.0, "stage8.dd_max_pct": 50.0,
       "stage8.invalid_ratio": 0.886, "stage8.level_tolerance": 0.06, "stage8.bounce_lookback_days": 5,
       "stage8.bounce_min_pct": 3.0, "stage8.exhaustion_ratio": 0.8, "stage8.require_confirms": 2}


def cfg(k, d=None):
    return CFG.get(k, d)


def bar(i, o, h, l, c, v):
    return {"ts": 1.7e9 + i * 86400, "open": o, "high": h, "low": l, "close": c, "quote_vol": v}


def path(prices, vols=None, last=None):
    """종가 경로 → 일봉. 몸통 ±1%, 꼬리 ±2%."""
    rows = []
    for i, p in enumerate(prices):
        prev = prices[i - 1] if i else p
        rows.append(bar(i, prev, max(prev, p) * 1.02, min(prev, p) * 0.98, p, (vols or [1e6] * len(prices))[i]))
    if last:
        rows[-1] = bar(len(prices) - 1, *last)
    return rows


def rally_then_dip(dd_target, bounce=True):
    """10일 횡보 1.0 → 20일 동안 2.0 까지 +100% → 하락 → (반등)."""
    flat = [1.0] * 10
    up = [1.0 + (i + 1) * 0.05 for i in range(20)]            # 1.05 … 2.0
    bottom = 2.0 * (1 - dd_target / 100)
    down = [2.0 - (2.0 - bottom) * (i + 1) / 8 for i in range(8)]
    prices = flat + up + down
    vols = [1e6] * 10 + [3e6] * 20 + [4e6] * 8
    if bounce:
        b = bottom * 1.06
        prices += [bottom * 0.99, b]
        vols += [1.2e6, 1.0e6]
    return prices, vols


def test_100퍼_상승_후_38퍼_급락은_0_382_부근_되돌림():
    # L≈0.98(횡보 꼬리) H≈2.04(꼭대기 꼬리). 38% 하락 → r ≈ 0.75
    prices, vols = rally_then_dip(38)
    rt = analyze(path(prices, vols), cfg)
    assert rt is not None
    assert 30 <= rt.drawdown_pct <= 50
    assert rt.leg_gain_pct >= 60
    assert 0.382 <= rt.ratio <= 0.886


def test_골든포켓_반등이면_확인_2개_이상():
    # 38% 떨어뜨린 뒤 +6% 반등(→ 고점 대비 약 −34%) + 매도 소진.
    # 32% 로 두면 반등 후 고점(꼬리 2.04) 대비 −29% 가 돼 30~50% 구간 밖으로 나간다.
    prices, vols = rally_then_dip(38)
    rt = analyze(path(prices, vols), cfg)
    assert rt is not None and rt.confirms >= 2
    assert rt.volume_exhausted is True
    assert rt.bounce_pct >= 3


def test_하락폭이_30퍼_미만이면_대상_아님():
    prices, vols = rally_then_dip(20)
    assert analyze(path(prices, vols), cfg) is None


def test_하락폭이_50퍼_넘으면_대상_아님():
    prices, vols = rally_then_dip(60)
    assert analyze(path(prices, vols), cfg) is None


def test_이전_상승이_약하면_잘_가던_코인이_아니다():
    """+30% 오른 뒤 30% 빠진 건 '잘 가다가 급락'이 아니다."""
    prices = [1.0] * 10 + [1.0 + (i + 1) * 0.015 for i in range(20)] + [1.3 * (1 - 0.35 * (i + 1) / 8) for i in range(8)]
    assert analyze(path(prices), cfg) is None


def test_반등_없이_계속_빠지는_중이면_확인_부족():
    prices, vols = rally_then_dip(35, bounce=False)
    vols[-3:] = [9e6, 9e6, 9e6]                    # 거래량 폭증하며 하락 = 매도 소진 아님
    rows = path(prices, vols)
    last = rows[-1]
    rows[-1] = {**last, "open": last["close"] * 1.05, "high": last["close"] * 1.06,
                "low": last["close"] * 0.999}      # 장대 음봉, 아래꼬리 없음
    rt = analyze(rows, cfg)
    assert rt is None or rt.confirms < 2


def test_피보나치_레벨_가격():
    prices, vols = rally_then_dip(35)
    rt = analyze(path(prices, vols), cfg)
    H, L = rt.high, rt.low
    assert abs(rt.levels[0.618] - (H - 0.618 * (H - L))) < 1e-9
    assert rt.invalid_price < rt.levels[0.786]
