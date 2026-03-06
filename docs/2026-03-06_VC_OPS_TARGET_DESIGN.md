# VC Ops Target Design (reechewclow_bot 중심)

## 목표

이 시스템의 목적은 "VC/Accelerator/Grant 제출 운영"을 매일 자동으로 굴리는 것이다.

사용자가 원하는 결과는 아래 4개다.

1. 경쟁사 가격/기능 조사와 시장 메모를 계속 축적한다.
2. VC/Accelerator/Grant 제출 기회를 자동으로 찾고 갱신한다.
3. "지금 무엇을 제출할 수 있는지"와 "언제까지 제출해야 하는지"를 날짜별로 정리한다.
4. 이 결과를 `@reechewclow_bot`을 통해 매일 보고받고, 필요할 때 명령형으로 재실행한다.

## 핵심 판단

이 시스템의 본체는 `AI agent`가 아니라 `운영 파이프라인`이다.

- 크롤링, 파싱, 데드라인 계산, 상태 저장은 결정론적 코드가 담당한다.
- AI는 요약, 우선순위 설명, 조사 보강, 신규 타깃 추천을 담당한다.
- 즉, `researcher`는 "엔진"이 아니라 "보조 분석 레이어"다.

## 사용자 기준 운영 흐름

### 1. 입력 레이어

세 가지 입력원을 함께 쓴다.

- 구조화 입력:
  - `2025-2026 Fund Raising.xlsx`
  - 기존 CSV/TSV/XLSX 타깃 파일
- 문서 입력:
  - VC 리스트 PDF
  - 시장/리서치 Markdown
  - 경쟁사/시장 조사 PDF
- 웹 입력:
  - 공식 제출 폼
  - accelerator / grant / speedrun landing page
  - cohort announcement / application page

### 2. 정규화 레이어

모든 입력을 최종적으로 아래 운영 단위로 바꾼다.

- `fundraising_records`
  - 원본 행/문서 스냅샷
- `vc_submission_tasks`
  - 실제 제출 관리용 active queue
- `submission targets`
  - 웹에서 발견된 apply/pitch 대상
- `vc_ops_events`
  - deadline soon / overdue / speedrun started 같은 이벤트 로그
- `vc_ops_snapshots`
  - 매 실행 결과 요약

### 3. 출력 레이어

매일 보고는 세 종류로 나눈다.

- Daily digest:
  - 오늘 새로 발견된 타깃
  - D-14 / D-7 / D-3 / D-1 항목
  - speedrun/cohort 관련 신규 또는 상태 변경
- Program dossier:
  - 특정 프로그램 하나에 대한 제출 보고서
  - 예: `Alliance DAO`, `a16z speedrun`
- Submission queue:
  - 날짜순 전체 제출 리스트
  - `today`, `this week`, `later`, `no deadline` 구간으로 정렬

## 역할 분리

### Deterministic layer

- `fundraise-import`
  - 엑셀/CSV/TSV를 DB에 넣는다.
- `submission-scan`
  - 웹에서 제출 페이지를 찾는다.
- `ops-sync`
  - 제출 태스크를 재구성하고 deadline/speedrun 이벤트를 만든다.
- `ops-list`
  - 날짜기준 제출 큐를 출력한다.
- `ops-program-report`
  - 특정 프로그램용 보고서를 생성한다.
- `push_telegram_reports.py`
  - digest를 텔레그램으로 밀어 넣는다.

### AI layer

- `researcher`
  - 신규 cohort 탐색 쿼리 제안
  - 경쟁사 가격/기능 비교 요약
  - 특정 VC/프로그램 적합성 코멘트
- `coordinator`
  - 오늘 제출 우선순위 재정렬
  - "지금 당장 해야 할 3개" 선정
- `communicator`
  - 신청 문구, 답장, 후속 메일 초안

## @reechewclow_bot 동작 모델

`@reechewclow_bot`은 두 가지 모드만 갖는다.

### Push mode

크론이 정해진 시간에 자동 실행한다.

- 아침 digest:
  - 오늘 제출 대상
  - 이번 주 마감
  - 신규 speedrun/cohort
- 저녁 digest:
  - 오늘 변경분
  - 내일 가장 급한 제출 3개

### Pull mode

사용자가 텔레그램 명령으로 직접 호출한다.

- `/ops_sync`
- `/ops_report`
- `/ops_list 21`
- `/submit_report alliance dao`
- `/submission_scan ai accelerator apac`

## 우선순위 모델

우선순위는 단순 날짜순보다 강해야 한다.

각 타깃에 아래 점수를 준다.

- deadline urgency:
  - D-0 to D-3: 매우 높음
  - D-4 to D-7: 높음
  - D-8 to D-14: 보통
- submission quality:
  - 공식 apply URL 존재
  - 상태가 `open` / `rolling`
  - 요구사항이 명확함
- strategic fit:
  - web3 / crypto / AI 적합도
  - stage fit
  - geography fit
- speedrun boost:
  - `speedrun`, `cohort`, `batch`, `apply now` 문구 탐지

최종적으로 보고서에는 아래 필드가 필요하다.

- `priority_score`
- `reason`
- `deadline_date`
- `days_left`
- `fit_tags`
- `submission_url`

## 사용자 기준 일일 리듬

### Morning run

- `ops-sync`
- `submission-scan`
- `ops-program-report` for tracked programs
- telegram push

결과:

- "오늘 제출 가능"
- "이번 주 마감"
- "새로 발견된 cohort"

### Midday on-demand

사용자가 봇에 물어본다.

- "alliance dao 지금 내도 되냐"
- "이번 달 speedrun 뭐 있냐"
- "VC 리스트 중 지금 당장 intro 넣을 곳 10개"

### Evening run

- 재스캔
- 상태 변경 반영
- 내일 기준 우선순위 리포트

## 현재 구현과의 매핑

이미 있는 것:

- 구조화 파일 import
- submission page discovery
- speedrun / deadline event generation
- telegram push
- bot command execution

부족한 것:

- PDF 투자목록 파싱
- 리서치 문서(`.md`, `.pdf`)를 submission priority에 반영하는 규칙
- "today / this week / later" 형식의 사용자 친화적 digest 강화
- fit scoring (`AI/Web3`, stage, geography) 명시적 계산

## 구현 우선순위

### Phase 1

현재 코드 위에 바로 붙일 것

1. PDF VC list importer 추가
2. `priority_score` 계산 추가
3. daily digest를 `today / this week / speedrun / no deadline` 형식으로 재구성
4. `@reechewclow_bot` 고정 채널로 자동 push

### Phase 2

AI 보강

1. researcher가 신규 cohort 후보를 제안
2. coordinator가 오늘 제출 우선순위를 설명
3. communicator가 outreach / follow-up 문안 생성

### Phase 3

운영 품질 강화

1. 제출 상태 수동 업데이트 명령
2. VC별 contact / intro / follow-up 히스토리 결합
3. 프로그램별 dossier 자동 생성

## 권장 제품 정의

이 프로젝트를 "VC Ops Assistant"로 정의한다.

정확한 한 줄 정의:

"구조화 파일 + 웹 제출 페이지 + 리서치 메모를 합쳐, 제출 가능한 VC/Accelerator/Grant 기회를 매일 갱신하고, 날짜별 우선순위와 speedrun/cohort 상태를 `@reechewclow_bot`으로 보고하는 운영 시스템"

## 다음 구현 순서

바로 손대야 하는 순서는 아래가 가장 맞다.

1. PDF 투자목록 importer
2. deadline digest 포맷 개선
3. `priority_score` / `fit_tags` 추가
4. 텔레그램 daily push 포맷 보강

세부 실행 스펙은 [2026-03-06_VC_OPS_EXECUTION_SPEC.md](./2026-03-06_VC_OPS_EXECUTION_SPEC.md)에 정리한다.

API 제품 관점의 상위 아키텍처는 [2026-03-07_VC_AGENT_API_PRODUCT_ARCHITECTURE.md](./2026-03-07_VC_AGENT_API_PRODUCT_ARCHITECTURE.md)에 정리한다.
