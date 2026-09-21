"""무료 API 실측 — 문서 말고 **실제 응답**을 보고 코드를 짠다.

사양서 지시: "외부 API 응답 구조와 거래소/DEX 링크 형식은 추측하지 말고
실제로 확인한 뒤 코드를 작성해라."

  python probe/probe_apis.py            전부 두드려 보고 요약
  python probe/probe_apis.py dexscreener  하나만

결과는 probe/out/<이름>.json 에 통째로 저장한다. 나중에 필드가 바뀌면 diff 로 잡는다.
표준 라이브러리만 쓴다 — venv 가 준비되기 전에도 돌아야 하기 때문이다.
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE      # 윈도우 사내 인증서 이슈 회피

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) onchain-radar-probe/0.1"


def get(url, headers=None, timeout=30):
    """한 엔드포인트가 막혀도 나머지 탐침은 계속 돌아야 한다 — 예외를 결과로 돌려준다.
    (402 = 유료 전환 요구, 429 = 레이트리밋. 둘 다 실제로 만난다.)"""
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            body = r.read()
            return {"status": r.status, "ms": round((time.time() - t0) * 1000),
                    "headers": dict(r.headers), "json": json.loads(body)}
    except urllib.error.HTTPError as e:
        try:
            msg = e.read()[:300].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            msg = ""
        return {"status": e.code, "ms": round((time.time() - t0) * 1000),
                "error": f"HTTP {e.code}", "body": msg, "url": url}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "error": f"{type(e).__name__}: {e}", "url": url}


def post(url, payload, headers=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"}
    h.update(headers or {})
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=h)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return {"status": r.status, "ms": round((time.time() - t0) * 1000),
                "json": json.loads(r.read())}


def shape(o, depth=0, max_depth=3):
    """응답의 뼈대만 뽑는다 — 통째로 찍으면 읽을 수가 없다."""
    pad = "  " * depth
    if isinstance(o, dict):
        if depth >= max_depth:
            return f"{{…{len(o)}키}}"
        return "\n" + "\n".join(f"{pad}  {k}: {shape(v, depth + 1, max_depth)}"
                                for k, v in list(o.items())[:28])
    if isinstance(o, list):
        if not o:
            return "[] (빈 배열)"
        return f"[{len(o)}개] 첫 항목:" + shape(o[0], depth + 1, max_depth)
    if isinstance(o, str):
        return f'"{o[:60]}"' + ("…" if len(o) > 60 else "")
    return f"{o}"


def save(name, obj):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ 개별 탐침
# 테스트에 쓸 실제 토큰들 — 체인별로 하나씩, 링크 검증까지 같이 한다.
SAMPLES = {
    "eth_uni": ("ethereum", "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"),   # UNI
    "base_aero": ("base", "0x940181a94A35A4569E4529A3CDfB74e38FD98631"),     # AERO
    "bsc_cake": ("bsc", "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82"),       # CAKE
    "sol_jup": ("solana", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"),    # JUP
}


def p_dexscreener():
    out = {}
    chain, addr = SAMPLES["base_aero"]
    out["token_pairs"] = get(f"https://api.dexscreener.com/token-pairs/v1/{chain}/{addr}")
    out["latest_token_profiles"] = get("https://api.dexscreener.com/token-profiles/latest/v1")
    out["latest_boosted"] = get("https://api.dexscreener.com/token-boosts/latest/v1")
    out["search"] = get("https://api.dexscreener.com/latest/dex/search?q=AERO%2FWETH")
    return out


def p_geckoterminal():
    out = {}
    out["networks"] = get("https://api.geckoterminal.com/api/v2/networks?page=1",
                          {"Accept": "application/json;version=20230302"})
    out["trending_pools"] = get(
        "https://api.geckoterminal.com/api/v2/networks/base/trending_pools?page=1",
        {"Accept": "application/json;version=20230302"})
    out["new_pools"] = get(
        "https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1",
        {"Accept": "application/json;version=20230302"})
    return out


def p_coingecko():
    out = {}
    out["markets"] = get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
                         "&order=market_cap_desc&per_page=5&page=1"
                         "&price_change_percentage=24h,7d")
    out["coin_detail"] = get("https://api.coingecko.com/api/v3/coins/uniswap"
                             "?localization=false&tickers=true&market_data=true"
                             "&community_data=false&developer_data=false&sparkline=false")
    return out


def p_bitget():
    out = {}
    out["spot_symbols"] = get("https://api.bitget.com/api/v2/spot/public/symbols")
    out["spot_tickers"] = get("https://api.bitget.com/api/v2/spot/market/tickers")
    out["mix_contracts"] = get("https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES")
    out["mix_tickers"] = get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES")
    out["funding"] = get("https://api.bitget.com/api/v2/mix/market/current-fund-rate"
                         "?symbol=BTCUSDT&productType=USDT-FUTURES")
    out["oi"] = get("https://api.bitget.com/api/v2/mix/market/open-interest"
                    "?symbol=BTCUSDT&productType=USDT-FUTURES")
    out["long_short"] = get("https://api.bitget.com/api/v2/mix/market/account-long-short"
                            "?symbol=BTCUSDT&productType=USDT-FUTURES&period=1h")
    out["candles"] = get("https://api.bitget.com/api/v2/spot/market/candles"
                         "?symbol=BTCUSDT&granularity=1day&limit=10")
    return out


def p_defillama():
    out = {}
    out["fees_overview"] = get("https://api.llama.fi/overview/fees?excludeTotalDataChart=true"
                               "&excludeTotalDataChartBreakdown=true")
    out["protocol_fees"] = get("https://api.llama.fi/summary/fees/pendle?dataType=dailyRevenue")
    out["protocols"] = get("https://api.llama.fi/protocols")
    out["emissions"] = get("https://api.llama.fi/emissions")
    return out


def p_goplus():
    out = {}
    chain_ids = {"ethereum": "1", "base": "8453", "bsc": "56"}
    for key in ("eth_uni", "base_aero", "bsc_cake"):
        chain, addr = SAMPLES[key]
        out[key] = get(f"https://api.gopluslabs.io/api/v1/token_security/{chain_ids[chain]}"
                       f"?contract_addresses={addr}")
    _, sol = SAMPLES["sol_jup"]
    out["sol_jup"] = get(f"https://api.gopluslabs.io/api/v1/solana/token_security?contract_addresses={sol}")
    return out


def p_cryptopanic():
    # 키가 있어야 한다. 없으면 어떤 에러가 오는지만 기록해 둔다.
    key = os.environ.get("CRYPTOPANIC_TOKEN", "")
    url = f"https://cryptopanic.com/api/developer/v2/posts/?auth_token={key or 'NOKEY'}&public=true"
    return {"note": "키 필요", "result": get(url)}


PROBES = {
    "dexscreener": p_dexscreener,
    "geckoterminal": p_geckoterminal,
    "coingecko": p_coingecko,
    "bitget": p_bitget,
    "defillama": p_defillama,
    "goplus": p_goplus,
    "cryptopanic": p_cryptopanic,
}


def run(name):
    print(f"\n{'=' * 72}\n{name}\n{'=' * 72}")
    try:
        res = PROBES[name]()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read()[:400].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        print(f"  HTTP {e.code} — {body}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"  실패 {type(e).__name__}: {e}")
        return
    save(name, res)
    for k, v in res.items():
        if isinstance(v, dict) and "json" in v:
            print(f"\n[{k}] {v['status']} · {v['ms']}ms")
            print(shape(v["json"])[:2600])
        elif isinstance(v, dict) and "error" in v:
            print(f"\n[{k}] ❌ {v['error']} — {str(v.get('body', ''))[:160]}")
        else:
            print(f"\n[{k}] {json.dumps(v, ensure_ascii=False)[:500]}")


def main():
    names = sys.argv[1:] or list(PROBES)
    for n in names:
        if n in PROBES:
            run(n)
        else:
            print(f"모르는 이름: {n} (가능: {', '.join(PROBES)})")


if __name__ == "__main__":
    main()
