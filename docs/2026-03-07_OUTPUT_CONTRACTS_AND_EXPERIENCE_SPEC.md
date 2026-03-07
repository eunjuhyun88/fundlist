# 2026-03-07 Output Contracts And Experience Spec

## 1. Goal

Every surface must show the same object model.

Surfaces:

- Telegram
- HTTP API
- CLI
- dashboard

They may differ in formatting, but not in meaning.

## 2. Core Output Objects

### 2.1 Organization Dossier Card

Required fields:

- `organization_name`
- `organization_type`
- `official_domain`
- `checked_at`
- `focus_sectors`
- `focus_stages`
- `focus_geographies`
- `fund_size_text`
- `check_size_range`
- `thesis_summary`
- `investment_style`
- `portfolio_examples`
- `what_they_fund`
- `why_relevant_to_us`
- `confidence`
- `evidence`

### 2.2 Opportunity Card

Required fields:

- `organization_name`
- `program_name`
- `opportunity_label`
- `status`
- `checked_at`
- `deadline_date`
- `deadline_text`
- `days_left`
- `focus`
- `award_or_check_size`
- `what_they_want`
- `why_this_matters`
- `recommended_ask`
- `requirements`
- `official_page`
- `apply_link`
- `confidence`
- `evidence`

### 2.3 Fit Recommendation Card

Required fields:

- `fit_score`
- `overall_priority_score`
- `why_fit`
- `why_not_fit`
- `recommended_ask`
- `recommended_next_actions`
- `recommended_materials`
- `risk_flags`

### 2.4 Daily Brief

Required fields:

- `brief_generated_at`
- `top_actions`
- `new_opportunities`
- `deadline_alerts`
- `changed_opportunities`
- `ready_to_submit`
- `review_needed`

## 3. Telegram Presentation Contract

### 3.1 Opportunity Detail Format

```text
[Alliance / Alliance Accelerator]

- checked_at: 2026-03-07 12:10 KST
- status: deadline
- deadline: 2026-03-25
- days_left: D-18
- focus: crypto infra, AI agents, early-stage builders
- award_or_check_size: unknown
- what_they_fund: early-stage technical founders building network or product leverage
- portfolio_examples: unknown
- why_this_matters: strong fit for crypto x AI builder profile
- recommended_ask: accelerator application
- requirements: team summary, product summary, traction
- official_page: https://alliance.xyz/apply
- apply_link: https://alliance.xyz/apply
- confidence: high
- evidence: page phrase, official endpoint, verified on 2026-03-07
```

### 3.2 Daily Brief Format

```text
[FUNDING BRIEF]
- generated_at: 2026-03-07 09:00 KST
- active_opportunities: 28
- deadlines_within_7_days: 4
- changed_since_yesterday: 3

[TOP ACTIONS]
1. Antler — D-2 — accelerator — strong fit for early-stage AI team
2. SkyDeck — D-2 — accelerator — direct apply form verified today

[NEW / REOPENED]
...

[LINK CHANGES]
...

[REVIEW NEEDED]
...
```

### 3.3 Telegram Rules

1. never send raw crawl logs by default
2. always show `checked_at`
3. show `days_left` only when `deadline_date` is real
4. if a field is unknown, print `미확인`, not `-`
5. never show a marketing/vendor page as `apply_link`
6. if `apply_link` is missing, still show `official_page`
7. closed opportunities go to a separate section

## 4. HTTP API Contract

The API should expose DTO-first endpoints.

### 4.1 Read Endpoints

- `GET /v1/organizations`
- `GET /v1/organizations/{id}`
- `GET /v1/organizations/{id}/dossier`
- `GET /v1/programs`
- `GET /v1/opportunities`
- `GET /v1/opportunities/{id}`
- `GET /v1/opportunities/{id}/fit-recommendations`
- `GET /v1/briefs/latest`

### 4.2 Write Endpoints

- `POST /v1/scans/discovery`
- `POST /v1/scans/research`
- `POST /v1/scans/verify`
- `POST /v1/matching/run`
- `POST /v1/tasks`
- `PATCH /v1/tasks/{id}`

### 4.3 Response Envelope

```json
{
  "status": "ok",
  "generated_at": "2026-03-07T03:00:00Z",
  "data": {}
}
```

### 4.4 Error Envelope

```json
{
  "status": "error",
  "error": {
    "code": "validation_error",
    "message": "deadline_date must be yyyy-mm-dd",
    "retryable": false,
    "details": {}
  }
}
```

## 5. CLI Contract

CLI should mirror the API DTOs.

### 5.1 Examples

- `fundlist opportunity-list --output json`
- `fundlist organization-dossier --org alliance --output json`
- `fundlist daily-brief --output json`

### 5.2 Rules

- `--output json|text|ndjson`
- `--dry-run` for write-like actions
- `describe <command>` must describe IO schema and side effects

## 6. Ranking Contract

Every opportunity card should be able to explain rank using these fields:

- `freshness_score`
- `deadline_score`
- `fit_score`
- `confidence_score`
- `operator_readiness_score`
- `overall_priority_score`

These should be included in API responses even if Telegram hides some of them.

## 7. Required Missing-Data Behavior

If any field is missing:

- `deadline_date` -> show `미확인`
- `days_left` -> show `미확인`
- `ticket_size` -> show `미확인`
- `portfolio_examples` -> show `미확인`
- `recommended_ask` -> show `추가 분석 필요`

Missing is acceptable.
Fabricated precision is not.

## 8. Output Schemas

Reference JSON schemas:

- [organization-dossier.schema.json](./schemas/organization-dossier.schema.json)
- [opportunity-card.schema.json](./schemas/opportunity-card.schema.json)
- [fit-recommendation.schema.json](./schemas/fit-recommendation.schema.json)
- [daily-brief.schema.json](./schemas/daily-brief.schema.json)

## 9. Acceptance Criteria

The output layer is correct only if:

1. Telegram, API, and CLI all describe the same opportunity the same way
2. every card shows `checked_at`
3. every opportunity shows `official_page` and `apply_link` separately
4. every deadline card shows `days_left`
5. every recommendation card separates facts from reasoning
