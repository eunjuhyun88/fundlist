# Automation Scope And Roadmap

## 1. Goal

이 문서는 `자동 제출`까지는 하지 않되, `제출 가능한 상태까지 최대한 자동화`하는 범위를 정의하고, 실제 구현 순서를 정리한다.

핵심 질문은 이것이다.

- 무엇을 지금 바로 자동화할 수 있는가
- 무엇은 사람 검토가 반드시 필요한가
- 어디부터 시작해야 전체 시스템이 무너지지 않는가

정답은 아래와 같다.

- `발견`, `검증`, `상태 변경 추적`, `우선순위화`, `알림`, `제출 준비 관리`는 자동화한다.
- `최종 제출 클릭`, `캡차 처리`, `로그인 필요한 사이트`, `애매한 판단`은 사람 검토로 남긴다.

## 2. Product Boundary

이 시스템은 초기에 아래 역할까지만 책임진다.

1. opportunity를 찾는다
2. 공식 페이지와 실제 apply 링크를 검증한다
3. `open / rolling / deadline / closed / unknown` 상태를 추적한다
4. deadline과 requirements를 뽑는다
5. 고객 기준 priority queue를 만든다
6. submission task를 생성하고 상태를 관리한다
7. daily brief / webhook / Telegram으로 업데이트를 보낸다

이 시스템은 초기에 아래는 책임지지 않는다.

1. 실제 폼 자동 제출
2. 로그인/2FA/captcha 우회
3. 법적 문서 제출 검토
4. 사람 대신 최종 지원 문안 확정

## 3. Automation Categories

### 3.1 Fully Automatable Now

현재 코드와 현실적 기술 범위에서 자동화 가능한 것:

- workbook / pdf / url seed import
- seed dedupe / normalize
- official page 탐색
- submission link 추출
- `status` 판정
- `deadline_text`, `deadline_date` 추출
- change detection
- `open / deadline / closed / unknown` bucket 분류
- daily scan scheduling
- priority queue rebuild
- Telegram / webhook / API delivery
- submission task 생성
- task reminder / follow-up due 계산

### 3.2 Semi-Automatable

자동화는 가능하지만 사람 검토가 섞여야 하는 것:

- ambiguous deadline parse
- JS-heavy site verification
- requirements 요약
- fit score
- priority reason
- recommended next action
- duplicate merge
- one-off custom program classification

### 3.3 Human-In-The-Loop Required

초기에 반드시 사람 검토가 필요한 것:

- actual submit click
- captcha
- email verification
- login-required applications
- narrative quality approval
- final deck upload approval
- legal/compliance review

## 4. First Wedge

초기 wedge는 반드시 좁혀야 한다.

가장 먼저 자동화할 범위:

1. accelerator
2. grant
3. ecosystem incentive / startup program
4. 일부 open-application VC

초기 제외:

1. intro-only VC 전체
2. warm intro sourcing
3. generic CRM replacement
4. actual fundraising email sequencing

이유:

- accelerator/grant는 구조화가 잘 되어 있다
- apply 링크와 deadline이 상대적으로 명확하다
- automation 성공률이 높다

## 5. What The System Should Do End-To-End

### 5.1 Input

입력원:

- xlsx/csv/tsv
- pdf
- direct URLs
- tracked programs

### 5.2 Verification

시스템 동작:

1. source import
2. seed generation
3. site scan
4. opportunity extraction
5. status/deadline detection
6. observation save
7. change detection

### 5.3 Prioritization

1. workspace profile load
2. fit score calculation
3. urgency score calculation
4. trust score calculation
5. readiness score calculation
6. final priority queue build

### 5.4 Operations

1. task create
2. owner assign
3. ready-to-submit queue
4. submitted queue
5. follow-up queue

### 5.5 Delivery

1. daily morning brief
2. deadline alerts
3. changefeed webhook
4. on-demand API fetch

## 6. Automation Matrix

| Capability | MVP | Phase 2 | Phase 3 |
| --- | --- | --- | --- |
| Excel/PDF import | yes | yes | yes |
| Direct URL seed ingest | yes | yes | yes |
| Official page verification | yes | yes | yes |
| Deadline extraction | yes | yes | yes |
| Change detection | yes | yes | yes |
| Task management | yes | yes | yes |
| Daily Telegram brief | yes | yes | yes |
| API polling | yes | yes | yes |
| Webhooks | phase 2 | yes | yes |
| Browser fallback | phase 2 | yes | yes |
| AI priority explanation | phase 2 | yes | yes |
| Auto form fill suggestions | phase 3 | yes | yes |
| Actual auto submit | no | no | selective later |

## 7. Recommended Starting Point

가장 먼저 해야 하는 것은 `자동화 루프의 본체`를 완성하는 것이다.

순서:

1. verified opportunity DB
2. re-scan / changefeed
3. priority queue
4. submission task layer
5. API surface
6. AI summary layer

즉, 처음부터 AI를 많이 넣는 게 아니라 `검증된 운영 루프`를 먼저 만든다.

## 8. Phase 1: Reliable Internal Automation

목표:

- 네가 직접 매일 쓸 수 있는 운영 시스템 완성

필수 기능:

1. workbook import 안정화
2. full sweep scan
3. `open / deadline / closed / unknown` 결과 저장
4. direct apply 링크 저장
5. daily Telegram report
6. manual re-scan command
7. submission task CRUD

완료 정의:

- 네가 텔레그램이나 CLI에서
  - `오늘 open인 것`
  - `이번 주 마감`
  - `closed로 바뀐 것`
  - `ready_to_submit`
  를 확인할 수 있어야 한다.

## 9. Phase 2: Review And Confidence Layer

목표:

- 100% 자동이 아닌 현실을 반영해서 `review queue`를 만든다

필수 기능:

1. low confidence queue
2. unknown retry queue
3. browser fallback
4. duplicate merge review
5. deadline ambiguity review

핵심 아이디어:

자동화 제품의 품질은 `실패를 숨기는 것`이 아니라 `검토 큐를 잘 만드는 것`에서 나온다.

## 10. Phase 3: Priority And Submission Ops

목표:

- 데이터 제품을 운영 제품으로 바꾼다

필수 기능:

1. workspace profile
2. fit score
3. priority score
4. task lifecycle
5. owner / due / blockers
6. ready-to-submit board
7. submitted / follow-up board

이 단계부터 사용자는 "리스트"가 아니라 "일 처리 시스템"으로 느끼게 된다.

## 11. Phase 4: API Productization

목표:

- 다른 앱과 에이전트가 붙일 수 있는 API로 만든다

필수 엔드포인트:

- `GET /opportunities`
- `GET /changes`
- `POST /scans`
- `GET /priorities`
- `GET /tasks`
- `POST /tasks`
- `PATCH /tasks/{id}`
- `GET /briefs/latest`

필수 기능:

- workspace auth
- cursor pagination
- idempotency
- webhook delivery
- rate limit

## 12. Phase 5: AI-Assisted Operating Layer

목표:

- AI를 "검증"이 아니라 "운영 판단"에 투입

필수 기능:

1. priority reason generation
2. next action generation
3. blocker summaries
4. program dossier
5. required asset checklist suggestions

입력:

- verified facts only
- workspace thesis
- research notes
- task state

금지:

- AI가 source-backed fact를 덮어쓰기

## 13. What Should Never Be Automated Early

초기에 손대면 안 되는 것:

1. form auto-submit
2. captcha solving
3. password/login reuse
4. browser credential storage for many third-party sites
5. generic auto-apply to all opportunities

이유:

- 실패했을 때 비용이 크다
- 사이트별 예외가 너무 많다
- trust를 잃기 쉽다

## 14. The Right Human-In-The-Loop Design

사람이 개입해야 하는 지점을 시스템적으로 정의해야 한다.

### Review Triggers

- confidence < 70
- unknown status
- deadline parse missing but deadline-like wording 있음
- official page 없음
- submission_url 없음
- status changed to closed
- duplicate candidates detected

### Review Actions

- confirm
- override
- ignore
- merge
- mark submitted externally

## 15. Daily Operating Loop

### Morning

1. delta/full scan
2. change detection
3. priority rebuild
4. ready-to-submit queue refresh
5. brief send

### Midday

1. manual query
2. program-specific reverify
3. task updates

### Evening

1. unknown retry
2. near-deadline recheck
3. follow-up reminders
4. evening brief

## 16. Core Internal APIs Needed Even Before Public API

public API 이전에도 내부적으로는 아래 명령/모듈이 있어야 한다.

- `import_sources(workspace_id, files)`
- `run_full_scan(workspace_id)`
- `run_delta_scan(workspace_id)`
- `reverify_opportunity(workspace_id, opportunity_id)`
- `rebuild_priorities(workspace_id)`
- `create_submission_task(workspace_id, opportunity_id)`
- `update_submission_task(task_id, payload)`
- `generate_daily_brief(workspace_id, kind)`
- `deliver_brief(workspace_id, channel)`

## 17. Data Freshness Policy

freshness 기준이 없으면 운영 가치가 떨어진다.

- deadline within 7d: every 6h
- deadline within 14d: every 12h
- open/rolling: every 24h
- unknown: every 12h until resolved
- closed: every 72h

## 18. Concrete Build Order

지금 바로 구현 순서는 아래가 맞다.

### Step 1

`submission_task` 레이어 추가

필수:

- create/list/update task
- ready_to_submit / submitted / follow_up_due views

### Step 2

`changefeed` 추가

필수:

- status_changed
- deadline_changed
- submission_url_changed

### Step 3

`FastAPI Phase 1`

필수:

- `GET /opportunities`
- `GET /changes`
- `POST /scans`
- `GET /briefs/latest`

### Step 4

`review queue`

필수:

- unknown
- low confidence
- browser retry

### Step 5

`AI daily brief`

필수:

- top priorities
- why now
- blockers

## 19. Success Criteria For MVP

MVP가 성공했다고 말할 기준:

1. seed를 넣으면 매일 자동으로 verified opportunity list가 갱신된다
2. open/deadline/closed가 충분히 신뢰 가능하다
3. submission_url이 실제로 유효하다
4. ready-to-submit queue를 운영할 수 있다
5. changefeed가 동작한다
6. Telegram/API 둘 다 같은 truth를 본다

## 20. Bottom Line

우리가 지금 해야 하는 것은 `AI가 전부 대신하는 시스템`이 아니다.

정확히는 아래를 만드는 것이다.

1. `verified opportunity engine`
2. `continuous update loop`
3. `submission ops layer`
4. `human-in-the-loop review queue`
5. `public API on top`

이 순서로 가야 자동화 범위를 최대한 넓히면서도 시스템이 망가지지 않는다.
