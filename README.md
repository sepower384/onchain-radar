# 🛰 ONCHAIN RADAR

온체인 선행 포착 텔레그램 봇. **예측하지 말고 추적한다.**
"오를 코인"을 단정하지 않고, **자금 흐름이 먼저 포착된 코인과 그 근거**를 보여준다.
기존 알트 순환매 봇들과 **완전히 별개**다 — 별도 봇 토큰, 별도 DB, 코드 공유 없음.

설계와 실측 근거는 [`docs/DESIGN.md`](docs/DESIGN.md). 모든 외부 API 응답과 매수 링크 형식은
추측이 아니라 **실제로 호출해서 확인**했다(`probe/probe_apis.py`, 링크는 `pytest -m live`).

---

## 무엇을 보나

| 트랙 | 명령 | 내용 |
|---|---|---|
| **메인** | `/scan` | 비트겟 상장 전체 ∪ 코인게코 상위 500 → 거래량 반응 · 파생 과열 · 매출 급증 · 안전 · 홀더 |
| **마이크로캡** | `/micro` | DEX 전용 · 시총 $2천만 이하. GoPlus 안전 필터 통과분만. **초고위험** 고정 표시 |
| **되돌림** | `/dip` | 잘 가다가 30~50% 급락한 코인의 **피보나치 되돌림** + 반등 확인 |
| 매출 | `/revenue` | DefiLlama 매출 7일 vs 30일 급증 프로토콜 (토큰 있는 것만) |
| 호재 | `/news` | 업비트·바이낸스·비트겟 **공식 상장 공지**(1차 출처) + 상폐·유의 리스크. Claude 분류는 키 있을 때 |
| 언락 | `/unlock` | 14일 언락 캘린더 · D-7/3/1 알림 (Tokenomist 키 있을 때) |
| 튜닝 | `/tune` | 쌓인 성과로 가중치 제안 → `/tune apply` · `/tune reset` |
| 단일 | `/coin` · `/buy` | 티커 또는 컨트랙트. 동명 코인이면 **고르게 한다** |

모든 카드에 **구매 버튼**이 붙는다. CEX 매수는 **비트겟(레퍼럴 W63KTT)**, DEX 는 체인별(Jupiter · Uniswap · PancakeSwap).

### 점수 = 재정규화 가중합 − 페널티

데이터가 없는 신호는 **0점이 아니라 제외**하고 남은 가중치로 나눈다. 카드에 `사용된 신호 n/6`.
단, **반응(Stage 2) 신호가 없으면 순위에 못 오른다** — 파생·안전은 "나쁜 게 없다"일 뿐이라서.

---

## 설치 (5분)

```powershell
cd C:\dev\onchain-radar
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
notepad .env
```

### 텔레그램 봇 만들기

1. 텔레그램에서 **@BotFather** → `/newbot` → 이름·아이디 정하기 → 토큰을 `.env` 의 `TELEGRAM_BOT_TOKEN=` 에
2. 만든 봇에게 아무 말이나 한 번 보내기
3. `.venv\Scripts\python main.py chats` → 나온 chat_id 를 `.env` 의 `TELEGRAM_ALLOWED_CHAT_IDS=` 에
4. **허용 목록이 비어 있으면 아무도 명령을 못 쓴다**(안전 기본값)

### 키 — 없어도 Phase 1 은 전부 돈다

| 키 | 없으면 | 발급 |
|---|---|---|
| `COINGECKO_DEMO_KEY` | 429 가 잦아 느려진다(자동으로 분당 10회로 줄임) | https://www.coingecko.com/en/developers/dashboard |
| `ETHERSCAN_API_KEY` | 보조 홀더 이력 없음 (GoPlus 로 대체 중) | https://etherscan.io/myapikey |
| `HELIUS_API_KEY` | 솔라나 홀더 정밀도 낮음 (Phase 2) | https://dashboard.helius.dev |
| `CRYPTOPANIC_TOKEN` | 뉴스 없음 (Phase 2) | https://cryptopanic.com/developers/api/ |
| `ANTHROPIC_API_KEY` | 실질 호재 분류 없음 (Phase 2, 유료) | https://console.anthropic.com |
| `TOKENOMIST_API_KEY` | 언락 알림 없음 (유료 — DefiLlama 무료 대체는 402) | https://tokenomist.ai |

---

## 돌리기

```powershell
# 텔레그램 없이 콘솔로 결과 보기
.venv\Scripts\python main.py scan        # 메인 트랙
.venv\Scripts\python main.py micro       # 마이크로캡
.venv\Scripts\python main.py dip         # 급락 후 되돌림
.venv\Scripts\python main.py revenue     # 매출 급증

# 봇 상시 구동 (창 없이)
wscript start_hidden.vbs
```

`start_hidden.vbs` 는 `pythonw` 로 띄워 **검은 창이 뜨지 않는다.** 로그는 `logs\radar.log`.
멈추려면 `stop.ps1`.

### 스케줄 (config.yaml 에서 조정)

| 작업 | 주기 |
|---|---|
| 브리핑 | 매일 08:00 · 20:00 KST — 메인 상위 7 + 되돌림 3 + 마이크로 3 |
| 메인 스캔·실시간 알림 | 20분 (점수 ≥ 75, 같은 코인 6시간 쿨다운) |
| 마이크로 스캔 | 5분 |
| 되돌림 스캔 | 60분 |
| 성과 추적 | 60분 — 1·3·7·14일 수익률, **BTC 대비 초과수익**, 소멸은 **−100%** |
| 공지·뉴스 | 10분 — 새 상장·상폐·유의 공지 즉시 알림(비트겟 매수 버튼 포함) |
| 홀더 적재 | 3시간 — 한 번이라도 잡힌 코인의 홀더·상위지갑 스냅샷(고래·거래소 흐름의 원료) |
| 언락 | 매일 09:00 — D-7·3·1 |
| 가중치 튜닝 | 매주 일 21:00 — **제안만**, 적용은 사람이 `/tune apply` |

## Phase 별 상태

| Phase | 내용 | 상태 |
|---|---|---|
| 1 | 유니버스 · Stage 2·3·5·7 · 구매 링크 · 봇 · 성과 추적 · 홀더 적재 · **되돌림(Stage 8)** | ✅ 완료·실데이터 검증 |
| 2 | Stage 1 매집(홀더·**고래 순매수·거래소 순유출**) | ✅ 완료 — 스냅샷 20시간 쌓이면 점수가 켜진다 |
| 2 | Stage 6 공식 공지 규칙 분류 | ✅ 완료·실데이터 검증(상장 23·상폐 11·유의 11) |
| 2 | Stage 6 일반 뉴스 Claude 분류 | ✅ 코드 완료 · **ANTHROPIC_API_KEY + stage6.enabled 필요**(과금) |
| 2 | Stage 4 언락 | ✅ 코드 완료(공식 OpenAPI 스펙 기준) · **TOKENOMIST_API_KEY 필요**(유료) |
| 3 | 스마트머니(Nansen) | ✅ 코드 완료(공식 문서 기준) · **NANSEN_API_KEY 필요**(유료). 없으면 고래 순매수로 대체 |
| 3 | 가중치 튜닝 | ✅ 완료 · 표본 50건(약 2~3주) 쌓여야 첫 제안 |

키가 필요한 부분은 키가 없으면 **그 신호만 꺼지고** 나머지는 그대로 돈다. 키를 넣은 뒤엔 실제 응답을 한 번 대조할 것
(`probe/probe_apis.py` 에 추가) — 유료 API 는 키 없이 실호출 검증을 못 해 공식 스펙 기준으로 짰다.

---

## 테스트

```powershell
.venv\Scripts\python -m pytest            # 단위 96개 (네트워크 없음)
.venv\Scripts\python -m pytest -m live    # 실검증 7개: 링크가 정말 그 토큰을 여는가, 레퍼럴 코드 일치
```

## 배포 — 깃허브 Actions 무료 구동 (2026-09-22~ 가동)

VPS 없이 공개 저장소 `sepower384/onchain-radar` 의 Actions 로 돈다(공개 저장소는 실행 시간 무료).

- `main.py tick` = **한 번 깨어나서 밀린 명령 처리 + 주기가 돌아온 작업만 하고 끝**(`bot/tick.py`).
  명령 답은 최대 5분 늦다. 마지막 실행 시각·DB 는 `actions/cache` 로 다음 실행에 넘긴다.
- 깨우기: cron-job.org 가 5분마다 `workflow_dispatch` (`python tools/cron_setup.py <PAT> <CJ키>`).
  워크플로 안의 `schedule`(30분)은 예비용 — 깃허브 schedule 은 붐비면 건너뛴다.
- Secrets: `TELEGRAM_BOT_TOKEN` · `TELEGRAM_ALLOWED_CHAT_IDS` (필수) / `COINGECKO_DEMO_KEY` ·
  `CLAUDE_CODE_OAUTH_TOKEN`(`claude setup-token` — 구독으로 오른 이유·백커 조사·뉴스 분류, 과금 아님) 선택.
- **PC 예약작업 OnchainRadar 는 꺼 둔다.** 같은 토큰으로 두 곳에서 getUpdates 하면 409 충돌.
- 클라우드(미국 IP) 실측: 업비트 공지 403(비트겟·바이낸스 공지는 됨), 지오테르미널 간헐 429,
  비트겟 캔들 429 는 재시도로 전부 회복. 첫 실행 115초.

---

## 함정 기록 — 첫 실제 실행에서 잡은 것들 (2026-09-21)

| 증상 | 원인 | 조치 |
|---|---|---|
| BTC "거래량 7일 평균 대비 **149배**" | 분자는 코인게코 전 거래소 합계, 분모는 비트겟 일봉 평균 | **같은 출처끼리** 나눈다 (`volume_ratio_src`) |
| BNB 카드에 **이더리움 옛 ERC-20 BNB** Uniswap 버튼 | 코인게코 platforms 에 구버전 토큰이 들어 있다. 유동성 $306K 라 하한만으론 못 거름 | **같은 자산 검증** — 유동성/시총(옛 BNB 0.0003% vs UNI 0.32%) + 가격 괴리 |
| "반응 데이터 없음"인데 100점 | 파생(과열 없음)+안전(플래그 없음) 두 신호만으로 만점 | 반응을 **필수 신호**로 |
| 선물 전용 코인 반응 없음 | 현물이 없어 일봉을 못 받음 | **선물 일봉**(`granularity=1D`, 현물은 `1day` — 선물에 `1day` 주면 400) |
| 매출 급증 1위 **+4,218,288%** | 기준선이 0 이던 신규 프로토콜 | 기준선 일 $1,000 하한 + 토큰 없는 프로토콜 제외 |
| 한 바퀴 **555초** | 상승장엔 예선 통과가 수백 개 | 싼 사전점수로 **상위 150개만** 일봉 조회 → 약 3~4분 |
| 마이크로 1위가 24h **+430%** | 이미 쏜 코인 | 마이크로에도 "늦음" 감점 |
| DefiLlama Pendle 매출이 토큰에 안 붙음 | 하위 프로토콜(Pendle V2) 의 gecko_id 는 None, **부모에만** 있다 | `/lite/protocols2` 로 부모 단위 합산 |
| 비트겟·Jupiter 링크 검증 불가 | **SPA 라 없는 심볼에도 200** | 비트겟은 API 상장 확인 후에만, DEX 는 주소 기반 + DexScreener 로 심볼 대조 |
| 쿨다운 테스트 실패 | `now or time.time()` 이 `now=0` 을 "값 없음"으로 봄 | `is None` 비교 |
| 거래소 지갑 라벨 | GoPlus 홀더 `tag` 가 비어 온다. 기억한 "Binance 14" 가 실제로는 **Binance 28** | Etherscan 공개 라벨 29,945개에서 거래소 291개 추출(`exchange_wallets.yaml`) |
| 업비트 공지 400 | `per_page=40` | 상한 20 |
| 업비트 부분 취소 | "HEMI·USELESS 신규 … (HEMI 거래지원 취소)" 를 둘 다 취소로 처리 | 취소된 심볼만 빼고 나머지는 상장 |
| 바이낸스 신규상장 카테고리 | bStocks(토큰화 주식)·선물 공지가 섞여 온다 | 현물 코인 상장만 규칙으로 |
| 비트겟 공지 경로 | 실제 경로가 `annoucements`(오타) | 그대로 쓴다 |
| Nansen 체인 이름 | BSC 를 `bnb` 로 부른다 | 체인 이름표 분리 |
| Claude 모델 ID | 날짜 붙은 `claude-haiku-4-5-20251001` 을 썼다 | 날짜 없는 ID(`claude-opus-5`), 공식 SDK, fallback 기본 켬 |
