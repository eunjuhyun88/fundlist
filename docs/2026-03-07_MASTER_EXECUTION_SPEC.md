# Master Execution Spec

## 1. Why This Document Exists

현재 `fundlist`에는 아래 종류의 문서가 이미 있다.

- 제품 방향 문서
- API 상세 스펙
- 자동화 범위 문서
- 구현 청사진

이 문서는 그 문서들을 실제 실행 순서로 고정하는 `master spec`이다.

목적은 세 가지다.

1. 남은 구현을 정확한 순서로 고정한다.
2. 어떤 파일을 언제 바꾸는지 명시한다.
3. 각 milestone의 완료 기준을 객관적으로 만든다.

## 2. Final Product Definition

이 시스템은 다음 한 줄로 정의한다.

`공식 페이지 기준으로 VC/Accelerator/Grant opportunity를 지속적으로 검증하고, 고객별 우선순위와 제출 준비 상태를 API/Telegram/webhook으로 운영하는 submission operations system`

핵심은 아래 4개다.

1. `verified opportunity engine`
2. `change monitoring loop`
3. `submission task management`
4. `API delivery layer`

## 3. Hard Boundaries

### Included

- seed import
- site verification
- deadline/status extraction
- change detection
- submission task lifecycle
- daily brief
- Telegram ops
- internal/public API
- AI reasoning for prioritization

### Excluded

- auto submit click
- captcha solving
- login/session reuse across third-party sites
- legal/compliance automation
- investor CRM full replacement

## 4. Current State

### Already Implemented

- xlsx/pdf import
- verified submission scan
- `open / rolling / deadline / closed` status extraction
- `submission_url` extraction
- Telegram reporting
- daily cron
- task workflow v1

### Remaining

- changefeed
- review queue
- priority module split
- FastAPI
- workspace profile
- AI reasoning layer

## 5. Execution Order

이 순서는 고정한다.

1. `M1 Submission Task Workflow`
2. `M2 Changefeed`
3. `M3 Priority Module Split`
4. `M4 FastAPI Phase 1`
5. `M5 Review Queue`
6. `M6 Workspace Profile + AI Reasoning`

이 순서를 바꾸면 운영 중심 설계가 무너진다.

## 6. Milestone Specs

## M1. Submission Task Workflow

### Goal

verified opportunity를 실제 운영 task로 전환한다.

### Status

implemented

### Files

- [submission_tasks.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/src/fundlist/submission_tasks.py)
- [cli.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/src/fundlist/cli.py)
- [telegram_bot.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/scripts/telegram_bot.py)
- [push_telegram_reports.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/scripts/push_telegram_reports.py)

### Done Criteria

- task 생성 가능
- task 상태 전이 가능
- ready/submitted/followup 조회 가능
- Telegram에서 quick ops 가능
- daily digest에서 managed tasks 노출

## M2. Changefeed

### Goal

opportunity가 바뀐 것을 별도 feed로 관리한다.

### Why It Matters

daily update의 핵심 가치는 "현재 뭐가 있나"보다 "무엇이 바뀌었나"다.

### Files To Add

- `src/fundlist/changefeed.py`

### Files To Modify

- [submission_finder.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/src/fundlist/submission_finder.py)
- [cli.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/src/fundlist/cli.py)
- [push_telegram_reports.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/scripts/push_telegram_reports.py)
- [telegram_bot.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/scripts/telegram_bot.py)

### Schema

`opportunity_changes`

필드:

- `workspace_key`
- `fingerprint`
- `org_name`
- `change_type`
- `old_value`
- `new_value`
- `source_url`
- `submission_url`
- `detected_at`

### Change Types

- `new_opportunity`
- `status_changed`
- `deadline_changed`
- `submission_url_changed`
- `source_url_changed`
- `reopened`

### CLI

- `changes-list`
- `changes-report`

### Telegram

- `/changes_today`
- `/changes_recent`

### Acceptance

- full scan 후 변경분이 별도 테이블에 저장된다
- 같은 값 반복 스캔 시 duplicate event가 과도하게 쌓이지 않는다
- digest에서 changed section이 생긴다

## M3. Priority Module Split

### Goal

현재 `vc_ops.py`에 섞여 있는 scoring을 재사용 가능한 모듈로 분리한다.

### Files To Add

- `src/fundlist/priority.py`

### Files To Modify

- [vc_ops.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/src/fundlist/vc_ops.py)
- [submission_tasks.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/src/fundlist/submission_tasks.py)
- [push_telegram_reports.py](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/scripts/push_telegram_reports.py)

### Score Contract

- `trust_score`
- `urgency_score`
- `fit_score`
- `readiness_score`
- `change_boost`
- `priority_score`
- `priority_reason`

### Acceptance

- task layer와 digest가 같은 scoring 규칙을 본다
- score breakdown을 API/CLI에서 노출 가능하다

## M4. FastAPI Phase 1

### Goal

internal API를 먼저 만든다.

### Files To Add

- `src/fundlist/api/app.py`
- `src/fundlist/api/schemas.py`
- `src/fundlist/api/deps.py`

### Runtime

- local bind only
- bearer token env
- single workspace first

### Endpoints

- `GET /health`
- `GET /opportunities`
- `GET /opportunities/{fingerprint}`
- `GET /changes`
- `GET /tasks`
- `POST /tasks`
- `PATCH /tasks/{id}`
- `POST /tasks/{id}/submitted`
- `POST /scans/full`
- `POST /scans/delta`
- `GET /briefs/latest`

### Acceptance

- API와 Telegram/CLI가 같은 DB truth를 읽는다
- OpenAPI schema가 생성된다
- response shape가 stable하다

## M5. Review Queue

### Goal

완전 자동화가 불가능한 항목을 review queue로 분리한다.

### Files To Add

- `src/fundlist/review_queue.py`

### Inputs

- status = `unknown`
- confidence < 70
- deadline parse ambiguous
- missing submission_url
- duplicate candidates
- conflicting changed values

### Outputs

- `review-list`
- `review-resolve`
- `review-ignore`
- Telegram `/review_queue`

### Acceptance

- unknown/low-confidence가 운영 큐를 오염시키지 않는다
- 사람이 개입해야 할 대상이 분리된다

## M6. Workspace Profile + AI Reasoning

### Goal

고객별 전략과 AI 요약을 붙인다.

### Files To Add

- `src/fundlist/workspaces.py`
- `src/fundlist/ai_reasoning.py`
- `src/fundlist/briefs.py`

### Workspace Fields

- `sector_tags`
- `stage_tags`
- `geo_tags`
- `program_type_tags`
- `excluded_orgs`
- `thesis_text`
- `timezone`

### AI Outputs

- `priority_reason`
- `next_action`
- `brief_summary`
- `blockers`

### Guardrails

- AI는 verified fact를 덮어쓰지 않는다
- AI는 source 없는 링크를 만들지 않는다

### Acceptance

- 같은 opportunity라도 workspace에 따라 priority가 달라진다
- AI는 only-on-top of verified facts로 동작한다

## 7. Database Migration Order

additive only로 간다.

### Wave 1

- `submission_tasks`
- `submission_task_updates`

### Wave 2

- `opportunity_changes`

### Wave 3

- `workspace_profiles`
- `job_runs`

### Wave 4

- `review_queue_items` 또는 equivalent

## 8. Command Surface Target

## CLI

### Existing Core

- `fundraise-import`
- `ops-sync`
- `submission-scan`
- `submission-list`
- `submission-report`

### Target Additions

- `task-create`
- `task-list`
- `task-view`
- `task-update`
- `task-add-note`
- `task-ready`
- `task-submitted`
- `task-followup`
- `changes-list`
- `changes-report`
- `review-list`
- `review-resolve`

## Telegram

### Existing Core

- `/ops_daily`
- `/submission_scan full`
- `/apply_open`
- `/apply_deadline`
- `/apply_closed`

### Target Additions

- `/task_create`
- `/task_view`
- `/task_ready`
- `/task_submitted`
- `/tasks_ready`
- `/tasks_followup`
- `/changes_today`
- `/review_queue`

## API

### Phase 1

- opportunities
- changes
- tasks
- scans
- briefs

### Phase 2

- workspace profile
- priority rebuild
- review resolve
- webhook delivery

## 9. Testing Order

### Unit

- submission task transitions
- duplicate task prevention
- changefeed event emission
- priority score calculation

### Integration

- scan -> task create -> submitted -> followup
- scan -> changefeed -> digest
- API -> DB -> Telegram parity

### Golden Fixtures

- direct apply page
- closed google form
- rolling program page
- deadline page
- ambiguous page

## 10. Operational Readiness Checklist

배포 전 확인 항목:

- scan cron runs
- Telegram bot running
- task commands loaded
- daily digest includes managed tasks
- DB migrations additive only
- fallback paths stable
- API bind local only in phase 1
- secrets not committed

## 11. Rollout Strategy

### Stage A

local operator mode

- CLI
- Telegram
- cron

### Stage B

internal API mode

- FastAPI
- local token
- own client only

### Stage C

multi-tenant API

- API key table
- workspace isolation
- webhook delivery

## 12. Definition Of Completion

이 추가작업 묶음이 끝났다고 말하려면 아래가 모두 필요하다.

1. verified opportunities가 매일 갱신된다
2. changed opportunities가 따로 보인다
3. managed tasks가 daily ops에 들어간다
4. review queue가 unknown/ambiguous를 분리한다
5. API가 opportunities/tasks/changes를 읽을 수 있다
6. workspace profile 기반 priority가 가능하다

## 13. Immediate Next Action

이 문서 기준으로 다음 구현은 아래 순서로 고정한다.

1. `M2 Changefeed`
2. `M4 FastAPI Phase 1`
3. `M5 Review Queue`
4. `M3 Priority Module Split`
5. `M6 Workspace Profile + AI Reasoning`

순서를 이렇게 두는 이유:

- changefeed가 daily value를 가장 빨리 만든다
- API가 붙어야 서비스 형태가 생긴다
- review queue가 품질을 지킨다
- priority/AI는 그 다음에 올려도 된다

## 14. Source Of Truth Docs

상위 방향:

- [2026-03-07_VC_AGENT_API_PRODUCT_ARCHITECTURE.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_VC_AGENT_API_PRODUCT_ARCHITECTURE.md)

상세 API:

- [2026-03-07_VC_AGENT_API_DETAILED_SPEC.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_VC_AGENT_API_DETAILED_SPEC.md)

자동화 범위:

- [2026-03-07_AUTOMATION_SCOPE_AND_ROADMAP.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_AUTOMATION_SCOPE_AND_ROADMAP.md)

구현 청사진:

- [2026-03-07_IMPLEMENTATION_BLUEPRINT.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_IMPLEMENTATION_BLUEPRINT.md)

운영 실패/재시도/idempotency/review/webhook 계약:

- [2026-03-07_RUNTIME_AND_FAILURE_SPEC.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_RUNTIME_AND_FAILURE_SPEC.md)

아키텍처 전환 기준:

- [2026-03-07_FUNDING_INTELLIGENCE_CANONICAL_SCHEMA.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_FUNDING_INTELLIGENCE_CANONICAL_SCHEMA.md)
- [2026-03-07_AGENT_RUNTIME_AND_IO_CONTRACTS.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_AGENT_RUNTIME_AND_IO_CONTRACTS.md)
- [2026-03-07_OUTPUT_CONTRACTS_AND_EXPERIENCE_SPEC.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_OUTPUT_CONTRACTS_AND_EXPERIENCE_SPEC.md)

## 15. Architecture Pivot Note

이 문서의 기존 milestone은 `submission scanner + operator workflow` 축에서 작성되었다.

현재 목표는 그보다 상위인:

- `organization intelligence`
- `program intelligence`
- `verified opportunities`
- `fit recommendations`
- `submission operations`

을 포함하는 `AI-agent-first funding intelligence system`이다.

따라서 앞으로의 구현 우선순위는 아래 문서를 기준으로 재설정한다.

1. [2026-03-07_FUNDING_INTELLIGENCE_CANONICAL_SCHEMA.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_FUNDING_INTELLIGENCE_CANONICAL_SCHEMA.md)
2. [2026-03-07_AGENT_RUNTIME_AND_IO_CONTRACTS.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_AGENT_RUNTIME_AND_IO_CONTRACTS.md)
3. [2026-03-07_OUTPUT_CONTRACTS_AND_EXPERIENCE_SPEC.md](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/docs/2026-03-07_OUTPUT_CONTRACTS_AND_EXPERIENCE_SPEC.md)
