# 2026-03-07 Agent Runtime And IO Contracts

## 1. Runtime Principle

The system is now `AI-agent-first, evidence-grounded`.

This means:

- agents do planning, research, synthesis, matching, and briefing
- tools fetch pages, search the web, extract text, capture evidence, and persist facts
- no user-facing funding recommendation is emitted without an explicit evidence path

## 2. Agent Set

### 2.1 Discovery Agent

Purpose:
- find official organization pages, program pages, application pages, cohort pages, grant pages

Input contract:
```json
{
  "seed": {
    "organization_name": "Alliance",
    "seed_urls": ["https://alliance.xyz/"],
    "seed_type": "xlsx_record"
  },
  "search_scope": {
    "sectors": ["ai", "crypto"],
    "regions": ["global"],
    "program_types": ["accelerator", "grant", "vc_apply"]
  }
}
```

Output contract:
```json
{
  "organization_candidates": [],
  "program_candidates": [],
  "opportunity_page_candidates": [],
  "confidence": 0.0,
  "reasoning_summary": "string",
  "evidence": []
}
```

System prompt:
```text
You are the Discovery Agent for funding intelligence.
Your job is to find official organization pages, program pages, and application windows.
Prefer official pages, program pages, cohort pages, and direct application pages.
Do not claim a page is actionable unless you can cite a path, phrase, or endpoint.
Return candidates with confidence and evidence.
```

### 2.2 Research Agent

Purpose:
- understand what the organization/program actually is
- produce dossier fields: thesis, focus, stages, geographies, check sizes, portfolio examples

Input contract:
```json
{
  "organization": {
    "id": "org_alliance",
    "canonical_name": "Alliance",
    "official_domain": "alliance.xyz"
  },
  "known_pages": ["https://alliance.xyz/", "https://alliance.xyz/apply"],
  "research_depth": "standard"
}
```

Output contract:
```json
{
  "organization_dossier": {
    "focus_sectors": [],
    "focus_stages": [],
    "focus_geographies": [],
    "fund_size_text": "",
    "check_size_range": {"min_usd": null, "max_usd": null},
    "thesis_summary": "",
    "investment_style": "",
    "portfolio_examples": [],
    "what_they_fund": ""
  },
  "confidence": 0.0,
  "evidence": []
}
```

System prompt:
```text
You are the Research Agent for funding intelligence.
Your job is to explain what the organization is, what it funds, how it behaves, and what evidence supports that.
Separate facts from inference.
If a field is not supported, mark it unknown instead of guessing.
```

### 2.3 Verification Agent

Purpose:
- verify if an opportunity is actually open, rolling, deadline-driven, or closed
- extract real submission endpoint, deadline, requirements, and evidence

Input contract:
```json
{
  "opportunity_candidate": {
    "organization_name": "Alliance",
    "program_name": "Alliance Accelerator",
    "page_urls": ["https://alliance.xyz/apply"]
  },
  "verification_mode": "strict"
}
```

Output contract:
```json
{
  "opportunity_fact": {
    "status": "deadline",
    "deadline_date": "2026-03-25",
    "deadline_text": "Apply by March 25, 2026",
    "primary_submission_url": "https://alliance.xyz/apply",
    "requirements_text": "team, product, traction summary"
  },
  "confidence": 0.0,
  "evidence": []
}
```

System prompt:
```text
You are the Verification Agent.
Your job is to verify deadline, status, official page, and submission endpoint from page evidence.
Never infer open/deadline/closed without evidence.
If ambiguous, emit unknown and create a review item.
```

### 2.4 Entity Resolution Agent

Purpose:
- merge aliases, duplicate pages, and duplicate candidates into canonical entities

Input contract:
```json
{
  "organization_candidates": [],
  "program_candidates": [],
  "existing_entities": []
}
```

Output contract:
```json
{
  "resolved_organizations": [],
  "resolved_programs": [],
  "merge_actions": [],
  "review_needed": []
}
```

System prompt:
```text
You are the Entity Resolution Agent.
Match names, aliases, and domains into canonical organization and program entities.
Only merge when evidence is strong.
Otherwise emit a review item.
```

### 2.5 Matching Agent

Purpose:
- determine why an opportunity fits the company profile
- recommend what kind of ask is realistic

Input contract:
```json
{
  "company_profile": {},
  "organization_dossier": {},
  "opportunity_fact": {}
}
```

Output contract:
```json
{
  "fit_recommendation": {
    "fit_score": 0.0,
    "urgency_score": 0.0,
    "expected_value_score": 0.0,
    "overall_priority_score": 0.0,
    "why_fit": "",
    "why_not_fit": "",
    "recommended_ask": "",
    "recommended_next_actions": []
  },
  "confidence": 0.0,
  "evidence": []
}
```

System prompt:
```text
You are the Matching Agent.
Given the company profile, organization dossier, and verified opportunity, decide whether the opportunity is strategically worth pursuing.
Explain the fit, the likely ask, and the next steps.
Do not invent factual details that were not verified.
```

### 2.6 Monitoring Agent

Purpose:
- re-check active opportunities
- emit change events

Input contract:
```json
{
  "active_opportunities": [],
  "recheck_policy": {
    "deadline_within_days": 14,
    "rolling_check_hours": 24,
    "unknown_check_hours": 12
  }
}
```

Output contract:
```json
{
  "updated_opportunities": [],
  "change_events": [],
  "review_items": []
}
```

System prompt:
```text
You are the Monitoring Agent.
Re-verify active opportunities, compare them to current state, and emit explicit change events.
Prefer precision over recall. Unknown is acceptable. Wrong status is not.
```

### 2.7 Briefing Agent

Purpose:
- produce daily human-readable briefings and machine-friendly summaries

Input contract:
```json
{
  "company_profile": {},
  "high_priority_opportunities": [],
  "recent_change_events": [],
  "submission_tasks": []
}
```

Output contract:
```json
{
  "daily_brief": {
    "headline": "",
    "top_actions": [],
    "new_opportunities": [],
    "changed_opportunities": [],
    "deadline_alerts": [],
    "ready_to_submit": []
  }
}
```

System prompt:
```text
You are the Briefing Agent.
Produce a concise but information-dense operating brief.
Prioritize deadlines, newly opened opportunities, changed submission links, and high-fit items.
Use grounded facts and clearly mark unknown fields.
```

## 3. Tool Contracts

All agents use the same tool layer.

### 3.1 Search Tool

Input:
```json
{"query": "Alliance accelerator apply", "domains": ["alliance.xyz"]}
```

Output:
```json
{"results": [{"title": "", "url": "", "snippet": ""}]}
```

### 3.2 Browser Fetch Tool

Input:
```json
{"url": "https://alliance.xyz/apply", "mode": "html"}
```

Output:
```json
{"final_url": "", "status_code": 200, "title": "", "html": "", "text": ""}
```

### 3.3 Structured Extract Tool

Input:
```json
{"text": "...page text...", "url": "https://alliance.xyz/apply"}
```

Output:
```json
{
  "status_candidates": [],
  "deadline_candidates": [],
  "submission_candidates": [],
  "requirements_candidates": [],
  "evidence": []
}
```

### 3.4 Persistence Tool

Input:
```json
{"table": "opportunities", "records": []}
```

Output:
```json
{"written": 1, "updated": 0}
```

## 4. Orchestration Flows

### 4.1 Initial Build Flow

1. ingest seeds from xlsx/pdf/url
2. entity resolution for organization seeds
3. discovery agent expands official pages and programs
4. research agent builds organization dossiers
5. verification agent creates opportunities
6. matching agent generates fit recommendations
7. briefing agent produces first daily brief

### 4.2 Daily Monitoring Flow

1. monitoring agent selects active opportunities
2. verification agent rechecks fields
3. entity resolution agent merges new aliases/pages
4. change events emitted
5. matching agent recalculates priority deltas
6. briefing agent publishes daily summary

### 4.3 Manual Query Flow

Example: "show me AI accelerators closing this month"

1. filter opportunities
2. attach dossier snippets
3. attach fit recommendation
4. render opportunity cards

## 5. Fact/Inference Boundary

### Facts
- status
- deadline
- submission link
- official page
- requirements
- award amount if directly stated

### Inference
- focus sector summary
- investment style summary
- fit score
- recommended ask
- strategic priority

### Enforcement
- every fact field requires evidence
- every inference field must cite which facts it depends on
- unknown is allowed
- hallucinated precision is not allowed

## 6. Review Queue Triggers

Create a review item when:

- status confidence < 0.7
- deadline is mentioned but date parse failed
- program and opportunity labels conflict
- application endpoint is indirect or suspicious
- organization merge confidence < 0.85
- AI dossier contradicts verified facts

## 7. Minimal Acceptance Tests

The runtime architecture is correct only if it can pass these scenarios:

1. one organization with two active programs
2. one program with one closed cohort and one new open cohort
3. a stale blog page should not win over an official apply page
4. a Google Form should be accepted if it is the real endpoint
5. a `closedform` URL must become `closed`
6. a fit recommendation must not overwrite verified deadline facts

## 8. Immediate Implementation Order

1. implement canonical tables
2. implement discovery/research/verification outputs against those tables
3. add entity resolution review queue
4. add matching agent output table
5. rebuild Telegram/API output on the new DTOs
