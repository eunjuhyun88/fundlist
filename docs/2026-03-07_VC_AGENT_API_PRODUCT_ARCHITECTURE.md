# VC Agent API Product Architecture

## Goal

이 문서는 현재 `fundlist`를 개인 운영 도구에서 `VC opportunity verification + prioritization API` 제품으로 확장할 때 필요한 구조를 정리한다.

핵심 정의는 아래와 같다.

- 이 제품의 본체는 `AI chat bot`이 아니다.
- 본체는 `verified opportunity operating system`이다.
- AI는 그 위에서 해석, 적합도 판단, 우선순위 설명, next action 생성을 담당한다.

즉, 팔아야 하는 것은 "대화형 AI"가 아니라 아래 세 가지다.

1. `Verified data API`
2. `Prioritization / fit scoring API`
3. `Daily operating agent`

## Product Definition

한 줄 정의:

`엑셀/PDF/웹/수동 링크를 입력받아, 실제 제출 가능 여부와 마감일을 공식 페이지 기준으로 검증하고, 고객별 전략에 맞춰 우선순위를 매겨 API/봇/대시보드로 제공하는 VC application intelligence system`

## What Makes It A Real AI VC Agent

`AI VC agent`라고 부르려면 아래 4개가 모두 있어야 한다.

1. `Memory`
   - 고객별 타깃, stage, thesis, region, sector, previous outreach history를 저장
2. `Verification`
   - 공식 페이지를 실제로 읽고 `open/closed/deadline/unknown`을 판정
3. `Reasoning`
   - 이 기회가 왜 중요한지, 왜 지금 내야 하는지, 왜 이 고객에게 맞는지 설명
4. `Action`
   - daily brief 생성, webhook 전송, Telegram/Slack push, follow-up queue 생성

이 중 `Verification` 없이 `Reasoning`만 있는 건 그냥 리서치 챗봇이다.

## Product Layers

### 1. Source Ingestion Layer

입력원:

- structured:
  - `.xlsx`, `.csv`, `.tsv`
- documents:
  - `.pdf`, `.md`
- live web:
  - 공식 사이트
  - 신청 폼
  - accelerator/cohort landing page
- direct user seeds:
  - URL
  - org name
  - program name

출력:

- `seed_records`
- `source_documents`
- `import_runs`

요구사항:

- 원본 스냅샷 저장
- 행 단위 provenance 저장
- import idempotency

### 2. Verification Engine

이 레이어가 제품의 핵심이다.

역할:

- seed URL 정규화
- 공식 도메인 탐색
- apply/pitch/form URL 추출
- 페이지 텍스트 추출
- `deadline_text`, `deadline_date` 추출
- `status` 분류
- `requirements` 추출
- `source snapshot` 저장

현재 `fundlist`의 `submission_finder.py`가 이 레이어의 초기 형태다.

이 레이어는 반드시 `deterministic first`여야 한다.

판정 우선순위:

1. explicit closed markers
2. explicit deadline markers + parsed date
3. explicit rolling markers
4. actionable submission link 존재 여부
5. otherwise `unknown`

LLM은 이 레이어에서 보조 역할만 해야 한다.

- 허용:
  - requirements 요약
  - org/program category 보강
  - noisy page summary
- 비허용:
  - 근거 없는 open/closed 판정
  - 날짜 hallucination

### 3. Knowledge and Strategy Layer

이 레이어가 `AI`가 진짜 가치 내는 부분이다.

입력:

- verified opportunities
- customer profile
- historical outcomes
- watchlists
- research documents

출력:

- `fit_score`
- `priority_score`
- `priority_reason`
- `next_actions`
- `daily_brief`
- `program_dossier`

여기서 AI가 하는 일:

- 고객 thesis와 opportunity의 정합성 계산
- "오늘 내야 하는 이유" 설명
- "지금 넣지 말아야 하는 이유" 설명
- unknown/ambiguous targets 재검토 필요성 코멘트

### 4. Delivery Layer

채널:

- REST API
- webhook
- Telegram
- Slack
- dashboard
- CSV/JSON export

이 레이어는 "조회"와 "푸시"를 둘 다 지원해야 한다.

### 5. Control Plane

API 제품이면 반드시 별도로 있어야 한다.

필수 기능:

- tenant/workspace 분리
- API key 발급
- rate limit
- usage metering
- run scheduling
- job queue
- audit log
- retry / dead-letter queue

## Core Domain Model

최소 스키마는 아래 10개 엔터티가 필요하다.

1. `workspace`
   - 고객/팀 단위
2. `workspace_profile`
   - sector, stage, geography, thesis, exclusions
3. `source_document`
   - xlsx/pdf/md 원본
4. `seed_record`
   - import된 행 또는 사용자 입력 seed
5. `verification_run`
   - 스캔 실행 이력
6. `opportunity`
   - deduped canonical target
7. `opportunity_observation`
   - 특정 시점의 상태 스냅샷
8. `priority_queue_item`
   - workspace 기준 우선순위 결과
9. `brief`
   - daily/weekly digest
10. `action_task`
   - review, submit, follow-up, update needed

권장 canonical `opportunity` 필드:

- `opportunity_id`
- `workspace_id`
- `org_name`
- `program_name`
- `org_type`
- `domain`
- `official_page`
- `submission_url`
- `submission_type`
- `status`
- `deadline_text`
- `deadline_date`
- `requirements`
- `evidence`
- `source_page_snapshot`
- `verified_at`
- `confidence`
- `verification_method`

권장 `priority_queue_item` 필드:

- `priority_score`
- `fit_score`
- `urgency_score`
- `readiness_score`
- `trust_score`
- `priority_reason`
- `deadline_bucket`
- `next_action`
- `review_state`

## API Product Surface

최소 API는 아래처럼 잡는 게 맞다.

### Ingestion API

- `POST /v1/workspaces`
- `POST /v1/workspaces/{id}/sources/files`
- `POST /v1/workspaces/{id}/sources/urls`
- `POST /v1/workspaces/{id}/imports/run`
- `GET /v1/workspaces/{id}/imports`

### Verification API

- `POST /v1/workspaces/{id}/scans`
  - full sweep
  - delta scan
  - program-specific scan
- `GET /v1/workspaces/{id}/scans/{scan_id}`
- `GET /v1/workspaces/{id}/opportunities`
- `GET /v1/workspaces/{id}/opportunities/{opportunity_id}`
- `POST /v1/workspaces/{id}/opportunities/{opportunity_id}/reverify`

### Agent API

- `POST /v1/workspaces/{id}/briefs/daily`
- `POST /v1/workspaces/{id}/priorities/rebuild`
- `POST /v1/workspaces/{id}/agents/research`
- `POST /v1/workspaces/{id}/agents/next-actions`
- `POST /v1/workspaces/{id}/agents/program-dossier`

### Delivery API

- `POST /v1/workspaces/{id}/deliveries/telegram/test`
- `POST /v1/workspaces/{id}/deliveries/slack/test`
- `POST /v1/workspaces/{id}/webhooks`
- `GET /v1/workspaces/{id}/briefs/latest`

### Review API

- `POST /v1/workspaces/{id}/opportunities/{opportunity_id}/review`
- `POST /v1/workspaces/{id}/opportunities/{opportunity_id}/ignore`
- `POST /v1/workspaces/{id}/opportunities/{opportunity_id}/override`

## Agent Contract

`AI agent`는 자유롭게 말하게 두면 안 된다.

반드시 아래 계약을 둬야 한다.

### Research Agent

입력:

- verified opportunities
- research docs
- workspace thesis

출력:

- new watchlist suggestions
- fit tags
- missing info list

금지:

- `verified_at` 없는 사실 단정
- `official_page` 없는 제출 링크 추천

### Prioritization Agent

입력:

- open/deadline opportunities
- workspace profile
- current queue

출력:

- top N priorities
- 이유 1~3줄
- next action

금지:

- closed target를 top priority로 추천

### Communication Agent

입력:

- selected opportunity
- customer/company profile

출력:

- outreach draft
- application short answer draft
- follow-up draft

## Execution Model

운영 모델은 `sync request`와 `async job`로 나눈다.

### Sync

빠른 API:

- list opportunities
- fetch latest brief
- generate next actions from existing verified data

### Async

무거운 API:

- full sweep crawl
- multi-page reverification
- PDF import
- program dossier generation

비동기 job 응답 예:

```json
{
  "job_id": "job_123",
  "status": "queued",
  "kind": "full_sweep_scan"
}
```

## Scoring Model

실제 판매 가능한 제품이 되려면 점수를 분해해야 한다.

최소 4축:

1. `trust_score`
   - 공식 도메인 여부
   - direct form 여부
   - evidence strength
   - last verified freshness
2. `urgency_score`
   - 마감일 임박도
3. `fit_score`
   - sector, stage, geography, check size, thesis 적합도
4. `readiness_score`
   - requirements clarity
   - form completeness
   - intro-only 여부

최종:

`priority_score = trust + urgency + fit + readiness + strategic boosts`

이렇게 쪼개야 고객이 결과를 신뢰한다.

## Why AI Is Needed

고객이 돈 내는 이유는 단순 크롤링이 아니다.

크롤링만으로는:

- 데이터는 나오지만
- "그래서 오늘 뭘 해야 하지?"가 안 나온다.

AI가 필요한 지점:

- 고객 전략에 맞춘 우선순위
- 중복 opportunity 묶기
- ambiguous program 해석
- research memo와 live target 연결
- actionable daily brief 생성

즉:

- `verification`은 코드가 책임진다.
- `judgment`는 AI가 책임진다.
- `factual truth`는 항상 source-backed data가 책임진다.

## Multi-Tenant Requirements

API 제품이면 고객별 설정이 완전히 분리돼야 한다.

workspace별 설정:

- sector focus
- stage focus
- geography focus
- check size
- thesis tags
- blocked orgs
- preferred channels
- daily brief time
- watchlist programs

고객별 저장 분리:

- API key scope
- source docs
- imported rows
- verified opportunities
- manual overrides
- briefs

## Reliability Requirements

이 제품은 "리서치 툴"이 아니라 "운영 툴"이므로 신뢰성이 중요하다.

필수 규칙:

- 모든 상태에는 `verified_at` 포함
- 모든 링크 추천에는 `official_page` 포함
- `deadline`은 raw text와 parsed date 둘 다 저장
- fetch 실패는 `unknown`으로 남기고 재시도 큐에 넣음
- JS-heavy site는 browser worker로 재검증
- status 변경은 observation history로 남김

## Recommended Runtime Architecture

### API Service

- FastAPI or equivalent
- auth, tenant routing, query endpoints

### Worker Service

- crawl jobs
- pdf/xlsx import jobs
- daily brief jobs

### Browser Service

- Playwright
- JS-rendered forms 전용

### DB

- Postgres 권장
- SQLite는 single-operator local mode에만 적합

### Queue

- Redis/RQ, Celery, or equivalent job system

### Object Storage

- source documents
- HTML snapshots
- generated briefs

## Commercial Packaging

API로 팔려면 SKU를 분리하는 게 맞다.

### Tier 1. Verified Opportunity API

제공:

- opportunity list
- verified apply link
- status
- deadline

고객:

- founder tools
- accelerators
- CRM products

### Tier 2. Prioritization API

제공:

- fit_score
- priority_score
- next actions
- daily briefs

고객:

- founder OS
- fundraising workflow tools
- internal venture studios

### Tier 3. Agent Workflow API

제공:

- briefs
- alerts
- dossier generation
- webhook automation
- outreach drafting

고객:

- higher-touch SaaS
- concierge fundraising products

## What To Build First

현재 `fundlist` 기준으로 제품 MVP는 아래 순서가 맞다.

### Phase 1. Verification API

목표:

- `submission_targets`를 외부 API로 노출
- workspace 없이 single-tenant MVP

필수:

- `GET /opportunities`
- `POST /scans`
- `GET /reports/latest`

### Phase 2. Fit and Priority API

목표:

- workspace profile 추가
- customer-specific priority queue 생성

필수:

- `workspace_profile`
- `fit_score`
- `priority_reason`
- `daily_brief`

### Phase 3. Multi-Tenant SaaS

목표:

- 여러 고객에게 API 키 발급
- schedule / webhook / billing 추가

## Immediate Refactor Path From Current Code

현재 코드에서 바로 해야 할 분리는 아래다.

1. `submission_finder.py`
   - verification engine으로 유지
2. `vc_ops.py`
   - workspace-aware priority engine으로 확장
3. `telegram_bot.py`
   - first-party delivery channel로 유지
4. `new api service`
   - REST facade 추가
5. `new worker module`
   - scheduled scan / reverify / brief job 분리

## Product Truth Model

고객에게 보여줄 때는 이 원칙을 고정해야 한다.

- `source-backed fact`
  - 링크, 마감일, open/closed, verified_at
- `AI inference`
  - fit, urgency explanation, recommendation

이 둘을 UI/API에서 섞어버리면 신뢰를 잃는다.

반드시 응답 JSON에서도 분리해야 한다.

예:

```json
{
  "opportunity_id": "opp_123",
  "facts": {
    "official_page": "https://alliance.xyz/apply",
    "submission_url": "https://alliance.xyz/apply",
    "status": "deadline",
    "deadline_date": "2026-03-25",
    "verified_at": "2026-03-07T01:10:00Z"
  },
  "inference": {
    "fit_score": 82,
    "priority_score": 91,
    "priority_reason": "crypto + accelerator + direct application + near deadline"
  }
}
```

## Success Metrics

이 제품은 아래 지표로 관리해야 한다.

- verification precision
  - open/deadline/closed 판정 정확도
- fresh coverage
  - 최근 7일 내 검증 비율
- actionable coverage
  - verified `submission_url` 보유 비율
- unknown rate
  - 판정 실패 비율
- alert usefulness
  - 고객이 실제 클릭/제출한 비율

## Bottom Line

`자동화 VC 기반 AI agent`를 API로 팔고 싶다면,

- 크롤링이 아니라 `verified opportunity infra`
- 챗봇이 아니라 `workspace-aware operating agent`
- 단순 추천이 아니라 `fact/inference-separated API`

로 설계해야 한다.

지금 `fundlist`는 그중 `verification engine + operator workflow`까지는 이미 들어와 있다.
다음 제품화 단계는 `workspace profile`, `priority engine`, `REST API`, `async worker`다.
