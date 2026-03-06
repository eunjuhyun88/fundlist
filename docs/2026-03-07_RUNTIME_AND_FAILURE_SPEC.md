# Runtime And Failure Spec

## 1. Purpose

이 문서는 실제 운영에서 문제가 되는 아래 항목을 명시한다.

- 에러 처리
- 재시도 규칙
- idempotency
- 중복 실행 / race condition
- review queue resolve contract
- webhook payload and delivery contract

이 문서가 필요한 이유는, 기능 설계만으로는 운영 품질이 보장되지 않기 때문이다.

## 2. Runtime Model

런타임은 세 가지 실행 경로를 가진다.

1. `scheduled run`
   - cron / launchd 기반
2. `manual operator run`
   - CLI / Telegram command
3. `API-triggered run`
   - internal/public API

이 세 경로는 모두 같은 DB truth를 읽고 써야 한다.

공통 원칙:

- DB write path는 idempotent해야 한다
- 같은 scan이 중복 실행돼도 catastrophic duplicate를 만들면 안 된다
- task update는 state transition validation을 통과해야 한다

## 3. Failure Classes

실패는 아래 6종으로 나눈다.

### 3.1 Source Import Failure

예:

- xlsx parse 실패
- pdf text extraction 실패
- file not found

대응:

- import_run error 기록
- 해당 file만 skip
- 전체 pipeline은 가능한 계속 진행

### 3.2 Network Fetch Failure

예:

- DNS 실패
- timeout
- SSL error
- 403 / 429 / 500

대응:

- opportunity status를 즉시 변경하지 않음
- fetch failure를 `unknown`으로 덮어쓰지 않음
- retry 대상에 추가

### 3.3 Parse Failure

예:

- deadline-like wording은 있지만 date parse 실패
- page는 읽었지만 submission_url 추출 실패

대응:

- review flag 추가
- confidence 하향
- observation은 저장하되 canonical state overwrite는 제한

### 3.4 Duplicate / Race Failure

예:

- 같은 opportunity를 동시에 upsert
- 같은 task를 동시에 create
- full scan과 delta scan이 겹침

대응:

- fingerprint unique key
- active task duplicate guard
- job lock / in-flight guard

### 3.5 Delivery Failure

예:

- Telegram send 실패
- webhook 5xx
- webhook timeout

대응:

- retry queue
- dead-letter log
- delivery result 기록

### 3.6 AI Reasoning Failure

예:

- rate limit
- provider timeout
- malformed JSON

대응:

- facts-only brief fallback
- AI output optional
- verification layer unaffected

## 4. Idempotency Rules

## 4.1 Import Idempotency

동일 source document는 아래 기준으로 중복 여부를 판단한다.

- `sha256`
- filename
- imported row normalized key

결과:

- 같은 파일 재업로드 시 raw duplicate row가 과도하게 생기지 않아야 한다

## 4.2 Opportunity Idempotency

`submission_targets`는 fingerprint 기준 unique.

fingerprint 우선순위:

1. canonical submission_url
2. canonical source_url + org_name
3. domain + normalized org/program

규칙:

- 동일 fingerprint upsert는 overwrite
- event 생성은 state diff가 있을 때만

## 4.3 Task Idempotency

같은 opportunity에 대해 active task는 1개만 유지한다.

active states:

- `not_started`
- `researching`
- `drafting`
- `waiting_assets`
- `ready_to_submit`
- `submitted`
- `follow_up_due`

terminal states:

- `won`
- `rejected`
- `archived`

규칙:

- active task가 있으면 `task-create`는 `exists`를 반환
- terminal state만 있으면 새 task 생성 가능

## 4.4 API Job Idempotency

`POST /scans/*`, `POST /tasks`는 `Idempotency-Key`를 지원해야 한다.

Phase 1 규칙:

- 동일 `Idempotency-Key + route + workspace` 조합이면
  - 같은 response를 재반환하거나
  - 이미 생성된 job/task를 반환

## 5. Concurrency And Locks

## 5.1 Scan Lock

같은 workspace에서 동시에 full scan은 1개만 허용.

규칙:

- `scan_full` running 중 동일 workspace에 새 `scan_full` 요청 오면
  - `409 conflict`
  - 또는 기존 job id 반환

### Allowed Concurrency

- `scan_delta`는 `scan_full`과 동시에 허용하지 않는 것이 안전
- `brief generation`은 scan과 병렬 가능
- `task update`는 scan과 병렬 가능

## 5.2 Task Update Lock

task update는 optimistic concurrency로 충분하다.

권장:

- `updated_at` 기반 compare-and-set

Phase 1 간소화:

- 마지막 write wins
- state transition invalid면 reject

## 6. Canonical State Overwrite Rules

모든 observation이 canonical opportunity를 덮어써서는 안 된다.

### Safe Overwrite

- explicit closed marker
- explicit deadline parse
- explicit direct form URL
- stronger evidence + same fingerprint

### Unsafe Overwrite

- fetch failure
- blank HTML
- low-confidence unknown
- weak page with no apply signal

규칙:

- weaker observation은 event만 남기고 canonical current state는 유지 가능

## 7. Retry Policy

## 7.1 Network Retry

- timeout: retry up to 2
- 429: exponential backoff
- 5xx: retry up to 2
- 403: no immediate retry; mark blocked
- SSL/DNS: retry once next cycle

## 7.2 Unknown Retry

`unknown`은 별도 queue로 관리.

조건:

- confidence < 70
- last_checked_at < 12h

실행:

- nightly retry
- JS-heavy suspicion이면 browser retry queue로 이동

## 7.3 Delivery Retry

webhook/Telegram 실패 시:

- immediate retry 1
- delayed retry 2
- 이후 dead-letter log

## 8. Review Queue Spec

## 8.1 Review Triggers

review queue 진입 조건:

- `status = unknown`
- `confidence < 70`
- `deadline_text != ''` but `deadline_date = ''`
- `submission_url = ''`
- conflicting duplicate candidates
- status changed from `open/deadline` to `closed`
- source fetch repeatedly failing for tracked target

## 8.2 Review Item Shape

필드:

- `review_item_id`
- `workspace_key`
- `fingerprint`
- `org_name`
- `review_reason`
- `severity`
- `suggested_action`
- `source_url`
- `submission_url`
- `snapshot_excerpt`
- `status_at_review`
- `created_at`
- `resolved_at`
- `resolved_by`
- `resolution_type`
- `resolution_note`

## 8.3 Resolution Types

- `confirm`
- `override_status`
- `override_deadline`
- `override_submission_url`
- `ignore`
- `merge_duplicate`
- `defer`

## 8.4 Review Resolution Rules

### confirm

- 현재 observation을 받아들인다
- review item closed

### override_status

- canonical status 변경
- manual override event 기록

### override_deadline

- canonical deadline_date/text 수정
- manual override event 기록

### override_submission_url

- canonical submission_url 수정
- manual override event 기록

### ignore

- review item만 닫음
- canonical state 유지

### merge_duplicate

- loser fingerprint를 winner fingerprint에 병합
- loser는 archived duplicate로 표시

## 8.5 Review Queue SLA

- `severity=high`: same day
- `severity=medium`: 48h
- `severity=low`: 7d

## 9. Error Response Contract

API 에러는 항상 아래 형태를 가진다.

```json
{
  "error": {
    "code": "invalid_transition",
    "message": "task cannot move from drafting to submitted directly",
    "request_id": "req_123"
  }
}
```

권장 error codes:

- `invalid_request`
- `not_found`
- `conflict`
- `invalid_transition`
- `idempotency_conflict`
- `rate_limited`
- `upstream_error`
- `delivery_failed`

## 10. Webhook Contract

## 10.1 Event Types

- `opportunity.discovered`
- `opportunity.updated`
- `opportunity.status_changed`
- `opportunity.deadline_changed`
- `opportunity.link_changed`
- `task.created`
- `task.updated`
- `task.submitted`
- `task.follow_up_due`
- `brief.generated`

## 10.2 Delivery Envelope

```json
{
  "event_id": "evt_123",
  "event_type": "opportunity.status_changed",
  "workspace_key": "default",
  "created_at": "2026-03-07T09:00:00Z",
  "data": {}
}
```

## 10.3 Opportunity Payload Example

```json
{
  "event_id": "evt_123",
  "event_type": "opportunity.deadline_changed",
  "workspace_key": "default",
  "created_at": "2026-03-07T09:00:00Z",
  "data": {
    "fingerprint": "abc123",
    "org_name": "Alliance",
    "change_type": "deadline_changed",
    "old_value": "2026-03-20",
    "new_value": "2026-03-25",
    "official_page": "https://alliance.xyz/apply",
    "submission_url": "https://alliance.xyz/apply"
  }
}
```

## 10.4 Task Payload Example

```json
{
  "event_id": "evt_234",
  "event_type": "task.submitted",
  "workspace_key": "default",
  "created_at": "2026-03-07T09:20:00Z",
  "data": {
    "task_id": 12,
    "opportunity_fingerprint": "abc123",
    "org_name": "Alliance",
    "submission_state": "submitted",
    "submitted_at": "2026-03-07T09:19:00Z",
    "follow_up_due_at": "2026-03-21"
  }
}
```

## 10.5 Signature

권장 헤더:

- `X-Fundlist-Signature`
- `X-Fundlist-Event`
- `X-Fundlist-Request-Id`

signature:

- HMAC-SHA256(body, webhook_secret)

## 10.6 Delivery Rules

- success: HTTP 2xx
- retryable: 408, 409, 429, 5xx
- non-retryable: 400, 401, 403, 404, 422

## 11. Race Condition Scenarios

## 11.1 Full Scan + Delta Scan

문제:

- 서로 다른 observation이 거의 동시에 canonical state를 갱신

규칙:

- full scan이 running이면 delta scan reject or queue

## 11.2 Two Task Creates For Same Opportunity

문제:

- duplicate active task 생성

규칙:

- active task uniqueness check
- unique active task guard in application logic

권장 later:

- partial unique index if DB moved to Postgres

## 11.3 Task Submitted Twice

문제:

- duplicate submitted event

규칙:

- if already `submitted` and same `submitted_at`, treat as idempotent success
- if already `submitted` and different payload, return conflict

## 11.4 Review Resolve After New Scan

문제:

- review resolve 시점에 newer observation이 들어와 있음

규칙:

- review item은 `review_base_observed_at`를 저장
- newer observation exists면 resolve API warns or requires force flag

## 12. Data Repair Rules

운영 중 수동 복구가 가능한 규칙:

- recompute priority scores
- rebuild changefeed from event history where possible
- archive duplicate tasks
- restore task from update history

필수 command 후보:

- `repair-priority`
- `repair-task-duplicates`
- `repair-review-items`

## 13. Observability Detail

필수 로그 필드:

- `request_id`
- `job_id`
- `workspace_key`
- `fingerprint`
- `task_id`
- `change_type`
- `delivery_endpoint_id`

필수 metrics:

- scan success rate
- fetch timeout rate
- unknown ratio
- review queue size
- webhook retry rate
- duplicate task prevention count

## 14. Phase 1 Enforcement Rules

Phase 1에서 꼭 지켜야 하는 것:

1. any failed fetch must not destroy previous good fact
2. any AI failure must not break factual delivery
3. duplicate task creation must return existing task, not create new one
4. scans must be safe to rerun
5. delivery failure must be visible in logs

## 15. Bottom Line

운영 시스템은 "기능이 있다"보다 "실패해도 무너지지 않는다"가 더 중요하다.

그래서 구현 시 아래를 고정한다.

- facts are durable
- retries are explicit
- duplicates are controlled
- review queue is first-class
- webhook contract is stable
