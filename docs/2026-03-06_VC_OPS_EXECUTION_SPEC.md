# VC Ops Execution Spec (reechewclow_bot 운영 기준)

## 목적

이 문서는 [2026-03-06_VC_OPS_TARGET_DESIGN.md](./2026-03-06_VC_OPS_TARGET_DESIGN.md)의 방향성을 현재 `fundlist` 코드베이스에 바로 붙일 수 있는 실행 스펙으로 내린 것이다.

핵심은 세 가지다.

1. `VC/Accelerator/Grant 제출 운영`을 매일 자동으로 굴린다.
2. `@reechewclow_bot`에서 "오늘/이번 주/스피드런/전체 큐"를 바로 확인할 수 있게 만든다.
3. AI는 우선순위 설명과 조사 보강을 담당하고, 본체는 결정론적 파이프라인으로 유지한다.

## 설계 원칙

- 파이프라인 우선:
  - 크롤링, 파싱, 데드라인 계산, dedupe, 보고서 생성은 코드가 담당한다.
- AI는 보조:
  - `researcher`는 신규 cohort 탐색, 시장 요약, 적합성 설명에만 쓴다.
- 보고서는 행동 중심:
  - "읽을거리"가 아니라 "오늘 뭘 낼지"가 먼저 보여야 한다.
- 기존 CLI 호환 유지:
  - `ops-sync`, `ops-list`, `ops-program-report`, `submission-scan`은 유지하고 옵션만 확장한다.

## 운영자 경험

사용자 입장에서 원하는 경험은 아래 순서다.

### 1. 아침 자동 보고

매일 아침 `@reechewclow_bot`이 아래 5개 구간을 보낸다.

1. `today`
2. `this week`
3. `new speedrun / cohort`
4. `new targets`
5. `no deadline / blocked`

### 2. 낮 시간 수동 질의

사용자는 텔레그램에서 짧게 물어본다.

- "오늘 바로 낼 수 있는 것 보여줘"
- "이번 주 마감 리스트 보여줘"
- "speedrun 뭐 새로 떴냐"
- "alliance dao 지금 제출 준비됐냐"

### 3. 저녁 재정리

저녁에는 아래만 다시 요약한다.

- 오늘 변경분
- 내일 가장 급한 3개
- 신규 발견 항목

## 입력원 정의

초기 입력원은 이미 사용자 파일 기준으로 정해져 있다.

### 구조화 입력

- `2025-2026 Fund Raising.xlsx`
- 기존 CSV/TSV/XLSX 타깃 파일

### 문서 입력

- `투자목록 vesting - 전체.pdf`
- `Research_Finance` 아래 Markdown/PDF 조사 문서

### 웹 입력

- 공식 apply / pitch / grant landing page
- accelerator / cohort / speedrun announcement page

## 권장 입력 매니페스트

현재는 명령 인자와 환경변수 중심이지만, 운영성을 위해 아래 텍스트 매니페스트를 두는 편이 낫다.

- `data/config/fundraise_files.txt`
  - 구조화 입력 파일 절대경로 한 줄씩
- `data/config/research_docs.txt`
  - 우선순위 설명에 참고할 리서치 문서 경로
- `data/config/submission_queries.txt`
  - `submission-scan` 반복 쿼리
- `data/config/program_watchlist.txt`
  - 매일 dossier를 만들 프로그램 키워드

이 방식의 장점은 Python 표준 라이브러리만으로 처리 가능하고, 크론/텔레그램/수동 실행이 모두 같은 설정을 공유한다는 점이다.

## 현재 코드 기준 핵심 확장 포인트

### 1. ingest / import

- `src/fundlist/fundraising.py`
  - 현재 `.csv`, `.tsv`, `.xlsx` 중심 import
  - 여기에 PDF importer 진입점 추가

### 2. ops queue / scoring / report

- `src/fundlist/vc_ops.py`
  - `SubmissionTask`
  - `vc_submission_tasks`
  - deadline / speedrun 계산
  - markdown report 렌더링

### 3. CLI entry

- `src/fundlist/cli.py`
  - 신규 옵션과 호환 alias 추가

### 4. Telegram digest / commands

- `scripts/push_telegram_reports.py`
  - 자동 push 메시지 포맷 강화
- `scripts/telegram_bot.py`
  - 명령 UX를 "오늘/이번 주/스피드런/프로그램 보고서" 중심으로 재배치

### 5. Schedule

- `scripts/vc_ops_cron.sh`
  - 아침/저녁 digest 분리

## 데이터 모델 변경안

핵심 원칙은 `vc_submission_tasks`를 계속 canonical active queue로 유지하는 것이다.

### 유지

- `deadline_date`
- `days_left`
- `is_speedrun`
- `status_norm`
- `website`

### 추가

`vc_submission_tasks` 또는 그에 대응하는 dataclass에 아래 필드를 추가한다.

- `priority_score INTEGER NOT NULL DEFAULT 0`
- `priority_reason TEXT NOT NULL DEFAULT ''`
- `fit_tags TEXT NOT NULL DEFAULT ''`
- `submission_url TEXT NOT NULL DEFAULT ''`
- `deadline_bucket TEXT NOT NULL DEFAULT ''`
- `source_kind TEXT NOT NULL DEFAULT 'structured'`

필드 의미는 아래와 같다.

- `priority_score`
  - 오늘 제출 우선순위 정렬용 총점
- `priority_reason`
  - 왜 상위에 올랐는지 한 줄 설명
- `fit_tags`
  - 예: `ai,crypto,apac,seed`
- `submission_url`
  - 실제 apply URL
- `deadline_bucket`
  - `today`, `this_week`, `later`, `no_deadline`, `overdue`
- `source_kind`
  - `structured`, `web`, `pdf`

`research_docs`를 직접 DB에 넣는 것은 Phase 1에서는 보류해도 된다. 먼저 파일 기반 신호 계산으로 충분하다. 문서 영향도가 커지면 그때 별도 테이블을 추가한다.

## 우선순위 계산 규칙

`priority_score`는 100점 만점 정수로 고정한다.

### 점수 구성

- deadline urgency: 최대 40
  - D-0 to D-3: 40
  - D-4 to D-7: 30
  - D-8 to D-14: 20
  - D-15+: 10
- status quality: 최대 15
  - `open`: 15
  - `rolling`: 10
  - `deadline`: 8
- submission readiness: 최대 15
  - 공식 `submission_url` 존재: 10
  - 요구사항/문서 단서 존재: 5
- speedrun / cohort boost: 최대 10
  - `speedrun`, `cohort`, `batch`, `apply now`: 10
- strategic fit: 최대 15
  - AI/Web3/Crypto 적합 태그
  - stage fit
  - region fit
- data completeness: 최대 5
  - 연락처/사이트/메모가 일정 수준 이상 채워짐

### priority_reason 생성 규칙

설명은 짧고 행동 중심으로 만든다.

예시:

- `D-2, 공식 apply URL 확인됨, speedrun cohort`
- `rolling but strong AI fit and clear submission form`
- `deadline unknown, keep warm only`

## 버킷 정의

모든 queue 출력은 날짜가 아니라 버킷 중심으로 먼저 나뉘어야 한다.

- `today`
  - `days_left <= 1`
- `this_week`
  - `2 <= days_left <= 7`
- `later`
  - `8 <= days_left <= 30`
- `overdue`
  - `days_left < 0`
- `no_deadline`
  - `deadline_date == ''`

정렬은 기본적으로 아래 순서를 사용한다.

1. `deadline_bucket`
2. `priority_score DESC`
3. `deadline_date ASC`
4. `org_name ASC`

## 리포트 포맷

### Morning digest

자동 push 메시지는 아래 포맷으로 통일한다.

```text
[VC OPS MORNING]
today:
1. Alliance DAO | D-1 | score=92 | speedrun | https://...
2. Example Capital | D-0 | score=88 | open | https://...

this week:
1. ...

new speedrun / cohort:
1. ...

new targets:
1. ...

no deadline / blocked:
1. ...
```

### Evening digest

```text
[VC OPS EVENING]
today changes:
- new target: ...
- deadline updated: ...

tomorrow top 3:
1. ...
2. ...
3. ...
```

### Program dossier

`ops-program-report`는 기존 구조를 유지하되 아래 필드를 앞단에 추가한다.

- `priority_score`
- `priority_reason`
- `fit_tags`
- `submission_url`
- `deadline_bucket`

즉, dossier는 "정보 모음"이 아니라 "제출 준비 상태 보고서"가 되어야 한다.

## Telegram 명령 UX

기존 명령은 유지하되, 운영자 입장에서 더 직접적인 alias를 추가한다.

### 유지

- `/ops_sync`
- `/ops_report`
- `/ops_list`
- `/submit_report <program>`
- `/submission_scan`
- `/submission_list`

### 추가 권장

- `/ops_today`
  - `today` 버킷만 요약
- `/ops_week`
  - `this_week` 버킷만 요약
- `/ops_speedrun`
  - speedrun/cohort만 요약
- `/ops_queue <days>`
  - 전체 큐를 일수 범위 기준 출력
- `/ops_program <keyword>`
  - `/submit_report`의 더 직관적인 alias

구현은 새 로직을 만들기보다 기존 `ops-list` / `ops-program-report` 호출 인자를 래핑하는 방식이 맞다.

## 실행 순서

매일 실행 순서는 아래로 고정한다.

1. 구조화 파일 import
2. PDF import
3. `submission-scan`
4. dedupe and merge
5. `priority_score` / `deadline_bucket` 계산
6. `ops-report` / `ops-program-report` 생성
7. telegram push

이 순서를 깨면 보고서와 텔레그램 메시지의 기준 시점이 달라진다.

## 구현 단계

### Phase 1: 운영 파이프라인 완성

목표:

- 지금 있는 코드만으로 "매일 쓸 수 있는 운영 시스템" 만들기

작업:

1. PDF importer 추가
2. `priority_score`, `priority_reason`, `fit_tags`, `deadline_bucket` 추가
3. `ops-list` 버킷 출력 지원
4. `push_telegram_reports.py`를 morning/evening digest 포맷으로 재구성
5. `telegram_bot.py`에 `/ops_today`, `/ops_week`, `/ops_speedrun` alias 추가

완료 기준:

- 봇에서 오늘/이번 주/스피드런을 별도 명령으로 즉시 확인 가능
- 자동 push가 행동 가능한 큐를 먼저 보여줌

### Phase 2: 연구 보강

목표:

- 단순 deadline queue가 아니라 "왜 이걸 먼저 해야 하는지"가 드러나게 만들기

작업:

1. `research_docs.txt` 기반 태그 추출
2. fit signal 계산
3. `priority_reason` 품질 개선
4. `researcher` 결과를 daily digest 하단 요약으로만 붙이기

완료 기준:

- 각 상위 타깃에 최소 1개의 명시적 적합성 설명 존재

### Phase 3: 관계/후속 관리

목표:

- 제출 이후 intro / follow-up까지 운영 범위를 넓히기

작업:

1. contact / intro / follow-up 상태 필드 추가
2. 수동 상태 업데이트 명령 추가
3. VC별 히스토리 보고서 생성

완료 기준:

- 제출 이후 후속 액션도 같은 시스템에서 추적 가능

## 구체적인 코드 작업 맵

### `src/fundlist/fundraising.py`

- PDF 파일 감지
- PDF 파서 진입점 추가
- 추출 결과를 `fundraising_records`로 정규화

### `src/fundlist/vc_ops.py`

- `SubmissionTask` 확장
- schema migration 추가
- `priority_score` 계산 함수 추가
- `deadline_bucket` 계산 함수 추가
- queue/report renderer 갱신

### `src/fundlist/cli.py`

- `ops-list --bucket ...`
- `ops-report --digest morning|evening`
- backward compatibility 유지

### `scripts/push_telegram_reports.py`

- 현재 summary excerpt 기반 포맷을 버킷 기반 digest로 교체
- `today`, `this_week`, `speedrun`, `new_targets`, `blocked` 섹션 출력

### `scripts/telegram_bot.py`

- alias command 추가
- 각 명령이 읽는 리포트 섹션을 더 짧고 직접적으로 조정

### `scripts/vc_ops_cron.sh`

- 아침/저녁 두 번 실행되도록 분리
- tracked program dossier generation 유지

## 수용 기준

이 설계가 제대로 구현되었다고 보려면 아래가 충족되어야 한다.

1. 아침에 봇 메시지만 보고도 "오늘 낼 3개"를 바로 알 수 있다.
2. `/ops_today`, `/ops_week`, `/ops_speedrun`이 각각 의미 있는 결과를 낸다.
3. PDF에서 들어온 타깃도 structured/web 타깃과 같은 queue에 섞여 나온다.
4. 상위 항목은 모두 `priority_score`, `priority_reason`, `submission_url`을 가진다.
5. 리포트가 날짜 나열이 아니라 행동 우선순위 중심으로 정리된다.

## 바로 다음 작업

가장 먼저 손대야 할 순서는 아래다.

1. PDF importer
2. `vc_ops.py` priority model
3. Telegram digest 포맷 개편
4. bot alias command 추가
