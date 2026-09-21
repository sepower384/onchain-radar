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

---

## 테스트

```powershell
.venv\Scripts\python -m pytest            # 단위 58개 (네트워크 없음)
.venv\Scripts\python -m pytest -m live    # 실검증 7개: 링크가 정말 그 토큰을 여는가, 레퍼럴 코드 일치
```

## 배포

PC 가 절전에 들어가면 알림이 멈춘다(관제탑 교훈). 실시간 알림을 안정적으로 받으려면 VPS 권장.

- **VPS (권장)** — 월 $5 급이면 충분. `git clone` → venv → `.env` → `systemd` 서비스로 `python main.py run`
- **Railway** — 저장소 연결, Start Command `python main.py run`, 환경변수에 `.env` 내용. SQLite 는 볼륨을 붙여야 재배포 때 안 날아간다
- 깃허브 Actions 는 **부적합** — 명령어 응답에 상시 프로세스가 필요하다

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
