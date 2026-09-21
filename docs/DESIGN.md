# ONCHAIN RADAR — 설계서

> 온체인 선행 포착 텔레그램 봇. **예측하지 말고 추적한다.**
> "오를 코인"을 단정하지 않고, "자금 흐름이 먼저 포착된 코인"과 그 근거를 보여준다.

작성 2026-09-21 · 모든 API·링크 형식은 **실제로 호출해서 확인한 결과**다(`probe/probe_apis.py`, 원본 응답 `probe/out/`).

---

## 1. 실측으로 확인한 것 — 사양서와 달라진 점

설계 전에 무료 API를 전부 두드려 봤다. 사양서 가정과 다른 게 네 개 나왔다.

| # | 발견 | 설계에 미치는 영향 |
|---|---|---|
| 1 | **GoPlus가 무료로 `holder_count` + 상위 10 홀더 + LP 홀더를 준다**(EVM) | **Stage 1 홀더 스냅샷을 Phase 1부터 키 없이 시작할 수 있다.** Etherscan/Moralis 키는 보조로 강등 |
| 2 | **DefiLlama `/emissions`(언락) = HTTP 402 유료** | 사양서 예상대로 Stage 4는 Tokenomist 키가 있을 때만. 무료 대체 없음 |
| 3 | **DefiLlama 수수료 개요 1회 호출에 프로토콜 2,729개 + `gecko_id`** | Stage 5가 호출 1번. **토큰↔프로토콜 매핑 테이블을 손으로 안 만들어도 된다** |
| 4 | **Bitget 선물 tickers 1회 호출에 797종목의 펀딩비 + OI(`holdingAmount`)** | Stage 3이 종목별 호출 없이 1번에 끝난다 |

그리고 **링크 검증에서 중요한 함정 하나**:

> **Bitget·Jupiter·Uniswap 전부 SPA라서 없는 심볼에도 HTTP 200을 준다.**
> `bitget.com/spot/ZZZZNOTREALUSDT` 도 200이다. → **URL을 열어 보는 방식으로는 검증이 불가능하다.**
> 그래서 비트겟은 **API로 상장·거래 상태를 확인한 뒤에만** 링크를 만들고, DEX는 **컨트랙트 주소 기반**으로만 만든다.

---

## 2. 필요한 API 키

### 무료 — 키 없이 동작 (Phase 1 전부 이것만으로 돈다)

| 서비스 | 용도 | 확인한 엔드포인트 | 비고 |
|---|---|---|---|
| **DexScreener** | DEX 페어·유동성·거래량·신규/부스트 | `token-pairs/v1/{chain}/{addr}` · `token-profiles/latest/v1` · `token-boosts/latest/v1` | 키 없음. 응답 37~900ms |
| **GeckoTerminal** | 트렌딩·신규 풀 | `networks/{net}/trending_pools` · `new_pools` | 키 없음. `Accept: application/json;version=20230302` |
| **Bitget** | 현물·선물 상장, 펀딩, OI, 롱숏, 캔들 | `spot/public/symbols` · `mix/market/tickers` · `account-long-short` · `spot/market/candles` | 키 없음. 현물 2,710 / 선물 797 |
| **DefiLlama** | 프로토콜 수수료·매출·TVL | `overview/fees` · `summary/fees/{slug}?dataType=dailyRevenue` · `protocols` | 키 없음. **`/emissions` 만 402** |
| **GoPlus Security** | 허니팟·세금·민팅·오너권한·**홀더수·상위홀더·LP** | `token_security/{chainId}` · `solana/token_security` | 키 없음. 호출당 0.3~1초 |

### 무료 — 키 발급 권장 (없어도 돌지만 막힘이 잦다)

| 서비스 | 왜 필요한가 | 발급 |
|---|---|---|
| **CoinGecko Demo** | 키 없이 쓰면 **실측 중에도 429가 났다.** Demo 키는 무료로 월 1만 콜 · 분당 30콜 | https://www.coingecko.com/en/developers/dashboard |
| **Etherscan V2** | EVM 홀더 이력 보조 (한 키로 60+ 체인) | https://etherscan.io/myapikey |

### 무료 — Phase 2부터

| 서비스 | 용도 | 발급 |
|---|---|---|
| **Helius** | 솔라나 홀더·지갑 (GoPlus 솔라나는 홀더 정보가 빈약) | https://dashboard.helius.dev (무료 100만 크레딧/월) |
| **Birdeye** | 솔라나 대안 | https://bds.birdeye.so (무료 티어) |
| **CryptoPanic** | 뉴스 수집 | https://cryptopanic.com/developers/api/ |

### 유료 — 있을 때만 해당 Stage 활성화

| 서비스 | 켜지는 기능 | 비고 |
|---|---|---|
| **Anthropic API** | Stage 6 뉴스 실질호재 분류 | 하루 호출 상한·월 비용 상한을 config로 강제 |
| **Tokenomist** | Stage 4 언락 알림 | DefiLlama 대체가 402라 **이게 유일한 경로** |
| **Nansen / Arkham** | 라벨된 스마트머니 순매수 | 없으면 "상위 비거래소 지갑 순증"으로 대체 계산 |

### 이 봇 전용으로 새로 만들어야 하는 것

| 항목 | 방법 |
|---|---|
| **텔레그램 봇 토큰** | @BotFather → `/newbot`. **기존 봇과 공유하지 않는다**(사양서) |
| **받을 chat_id** | 봇에게 말 한마디 건 뒤 `python main.py chats` 로 확인 |

---

## 3. 구매 링크 — 확인한 형식

**우선순위: 비트겟 → DEX → 차트·익스플로러.** 강회장 지시로 CEX 매수 링크는 **비트겟 하나**로 통일한다.

### 비트겟 (레퍼럴 `W63KTT`)

`https://partner.bitget.com/bg/W63KTT` 가 `channelCode=yrbp&vipCode=m6v2` 로 풀리는 것을 리다이렉트 추적으로 확인했다.

| 버튼 | 형식 | 생성 조건 |
|---|---|---|
| 🛒 비트겟 현물 | `https://www.bitget.com/spot/{BASE}USDT?channelCode=yrbp&vipCode=m6v2` | `spot/public/symbols` 에서 `status == "online"` |
| 📈 비트겟 선물 | `https://www.bitget.com/futures/usdt/{BASE}USDT?channelCode=yrbp&vipCode=m6v2` | `mix/market/contracts` 에서 `symbolStatus == "normal"` |

레퍼럴은 `config.yaml` 의 `referral.bitget` 한 곳에서만 붙인다 — 비우면 일반 링크.

### DEX (컨트랙트 주소 기반만. 티커 검색 링크는 만들지 않는다)

| 체인 | DEX | 형식 | 확인 방법 |
|---|---|---|---|
| Solana | **Jupiter** | `https://jup.ag/swap?sell=So11111111111111111111111111111111111111112&buy={mint}` | 서버 렌더 HTML에 민트가 박혀 나옴. `jup.ag/tokens/{민트}`는 제목에 "JUP $0.30699"까지 → **정확한 토큰을 연다** |
| Ethereum | **Uniswap** | `https://app.uniswap.org/swap?chain=ethereum&inputCurrency=NATIVE&outputCurrency={addr}` | 코인게코가 실제 쓰는 trade_url 과 동일 형식 |
| Base | **Uniswap** | `…?chain=base&…` | 〃 |
| Arbitrum | **Uniswap** | `…?chain=arbitrum&…` | 〃 |
| BSC | **PancakeSwap** | `https://pancakeswap.finance/swap?chain=bsc&outputCurrency={addr}` | 코인게코 trade_url 과 동일 |
| 기타 | DexScreener 페어 페이지 | `https://dexscreener.com/{chain}/{pairAddress}` | DexScreener 응답의 `url` 필드 그대로 |

⚠️ **Uniswap은 BSC를 `bnb`로 부른다**(`explore/tokens/bnb/…`). 체인 이름표를 체인마다 따로 둔다.

### 공통

| 버튼 | 형식 |
|---|---|
| 📊 차트 | DexScreener 응답의 `url` (가장 유동성 큰 페어) |
| 🔍 컨트랙트 | Etherscan / Basescan / Arbiscan / BscScan / Solscan — 체인별 자동 선택 |

**안전 규칙** — GoPlus에 허니팟·매도불가·고세금이 걸리면 **구매 버튼을 아예 안 만들고** `🚨 구매 링크 비활성화: (사유)` 로 표시.
마이크로캡 카드의 버튼 라벨은 `⚠️ 초고위험 · 매수`.

---

## 4. 구조

```
onchain-radar/
  main.py                 진입점: run(상시) · scan · micro · coin · chats · test
  config.yaml             임계값·가중치·주기·레퍼럴 — 하드코딩 금지
  .env / .env.example     키
  providers/              외부 API 어댑터. 실패하면 None → 그 신호만 꺼짐
    base.py               httpx 비동기 · 레이트리밋 · 캐시 · 백오프 재시도
    dexscreener.py  geckoterminal.py  coingecko.py  bitget.py
    defillama.py    goplus.py         etherscan.py  helius.py
    cryptopanic.py  tokenomist.py     nansen.py     anthropic_news.py
    exchange_wallets.yaml   주요 거래소 핫월렛 (순유출 계산용)
  scanners/               Stage 단위. 예외는 스캐너 안에서 격리
    universe.py           풀 A(비트겟) · B(코인게코 상위) · C(DEX 트렌딩)
    stage1_accumulation.py  stage2_reaction.py  stage3_derivatives.py
    stage4_unlock.py        stage5_revenue.py   stage6_news.py
    stage7_microcap.py
  scoring/                가중합 · 재정규화 · 페널티
  storage/                SQLAlchemy 모델 · 스냅샷 (SQLite → Postgres 교체 가능)
  tracking/               1/3/7/14일 수익률 · BTC 대비 초과수익 · 백테스트
  bot/                    텔레그램 핸들러 · 포매터 · buy_links.py
  tests/                  모듈별 테스트 (구매 링크 테스트 포함)
  probe/                  API 실측 스크립트와 원본 응답
```

### 핵심 원칙 (사양서 → 구현)

| 원칙 | 구현 |
|---|---|
| 코인 고유키 | `cg:<coingecko_id>` 또는 `<chain>:<contract>`. 티커는 표시용 |
| 없는 데이터 | **0점 처리 금지.** 그 신호를 빼고 남은 가중치를 재정규화. 카드에 `사용된 신호 n/6` |
| 홀더 변화율 | API가 과거값을 안 주므로 **봇이 스냅샷을 쌓아 비교.** 쌓이기 전엔 `데이터 수집 중` — 추정값 절대 금지 |
| 무거운 조회 | 홀더·지갑 조회는 **Stage 2 통과 후보에만** |
| 쿨다운 | 같은 코인 6시간. 점수 급등·새 리스크면 예외 |
| 성과 추적 | 가격 조회 실패·유동성 소멸 = **-100%로 기록**(누락 금지) + BTC 대비 초과수익 |
| 시간 | 전부 KST 표시 |
| 창 없이 | 상시구동은 `pythonw` + 숨김 vbs (강회장 공통 지침) |

---

## 5. Phase 계획

| Phase | 내용 | 필요한 키 |
|---|---|---|
| **1** | 유니버스(A·B·C) + Stage 2·3·5·7 + 구매 링크 + 텔레그램 기본 명령어 + 성과 추적 DB + **홀더 스냅샷 수집 시작(GoPlus)** | **텔레그램 봇 토큰만.** CoinGecko Demo 권장 |
| 2 | Stage 1 홀더·고래 분석(스냅샷이 쌓인 뒤) · Stage 4 언락 · Stage 6 뉴스+Claude 분류 | Helius · CryptoPanic · (Tokenomist) · (Anthropic) |
| 3 | 스마트머니(Nansen/Arkham) · 성과 데이터로 가중치 튜닝 · 백테스트 | (Nansen/Arkham) |

각 Phase 끝에 실제로 돌려 샘플 출력을 확인한 뒤 다음으로 넘어간다.
