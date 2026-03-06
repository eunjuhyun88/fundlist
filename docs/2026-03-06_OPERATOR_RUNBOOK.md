# VC Ops Operator Runbook

## 목적

이 문서는 `fundlist`를 실제 운영에 쓰기 위한 최소 실행 순서를 정리한다.

현재 기준으로 시스템은 아래 두 축으로 나뉜다.

1. `VC outreach queue`
   - `2025-2026 Fund Raising.xlsx` 기반
   - 마감일 없는 투자처/연락처 관리
2. `submission / cohort queue`
   - `submission-scan` 기반
   - accelerator / grant / speedrun / apply form 관리

## 기본 전제

기본 `ops-sync` 입력은 현재 아래 실제 파일 경로를 사용한다.

- `/Users/ej/Downloads/문서/VC_Fundraising/2025-2026 Fund Raising.xlsx`

PDF importer는 구현되어 있지만, 기본 운영 큐에는 자동 포함하지 않는다.

이유:

- `Investment Portfolio / Vesting` 류 PDF는 제출 큐가 아니라 참고 문서이기 때문이다.

운영자가 자주 수정하게 되는 목록은 `config/`에 둔다.

- [program_watchlist.txt](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/config/program_watchlist.txt)
- [submission_queries.txt](/Users/ej/Downloads/문서/VC_Fundraising/VC%20list/fundlist-git/config/submission_queries.txt)

필요할 때만 명시적으로 `--files ...pdf`로 넣는다.

## 가장 중요한 명령

레포 루트 기준:

```bash
cd "/Users/ej/Downloads/문서/VC_Fundraising/VC list/fundlist-git"
```

### 1. seed import + queue 갱신

```bash
python3 fundlist.py ops-sync
```

결과:

- `fundraising_records` 갱신
- `vc_submission_tasks` 갱신
- `data/reports/vc_ops_report.md` 생성

### 2. VC outreach queue 확인

```bash
python3 fundlist.py ops-list --bucket no_deadline --limit 30
```

이 출력이 실제로는 `VC 투자처 / 연락처 / 웹사이트 seed queue`다.

여기서 봐야 하는 필드:

- `score=...`
- `priority_reason`
- `fit_tags`
- URL

### 3. 이번 주 마감 있는 프로그램 확인

```bash
python3 fundlist.py ops-list --bucket this_week --limit 20
```

### 4. 오늘 급한 항목 확인

```bash
python3 fundlist.py ops-list --bucket today --limit 20
```

## cohort / speedrun / apply discovery

이 축은 seed xlsx와 별개다.

### 1. 스캔 실행

```bash
python3 fundlist.py submission-scan \
  --max-sites 80 \
  --max-pages-per-site 6 \
  --max-results-per-query 10 \
  --report-limit 80
```

특정 검색어로 좁히고 싶으면:

```bash
python3 fundlist.py submission-scan --query "crypto accelerator apply"
python3 fundlist.py submission-scan --query "ai speedrun cohort apply"
python3 fundlist.py submission-scan --query "grant foundation application"
```

### 2. 결과 확인

```bash
python3 fundlist.py submission-report --limit 80 --min-score 8
```

또는 표 형식:

```bash
python3 fundlist.py submission-list --limit 40 --min-score 8
```

## Telegram push

### morning digest

```bash
python3 scripts/push_telegram_reports.py --mode morning
```

현재 morning digest는 아래 4개 축을 같이 보여준다.

1. `today`
2. `this week`
3. `apply now`
4. `new speedrun / cohort`
5. `no deadline / outreach`

### evening digest

```bash
python3 scripts/push_telegram_reports.py --mode evening
```

dry-run 검증:

```bash
python3 scripts/push_telegram_reports.py --mode morning --dry-run
```

## Telegram bot 명령

현재 바로 쓰는 명령:

- `/ops_daily`
- `/ops_daily evening`
- `/ops_sync`
- `/ops_report`
- `/ops_list 21`
- `/ops_today`
- `/ops_week`
- `/ops_program alliance dao`
- `/ops_speedrun`
- `/ops_push`
- `/ops_push evening`
- `/submission_scan ai accelerator apply`
- `/submission_report`

운영 기준 역할은 아래처럼 본다.

- `/ops_*`
  - xlsx seed 기반 outreach / deadline queue
- `/submission_*`
  - 실제 apply form / cohort / speedrun discovery

`/ops_daily`는 아래를 한 번에 실행하는 운영용 명령이다.

1. `ops-sync`
2. watched program report 갱신
3. `submission-scan`
4. telegram push

## 실제 일일 루틴

### Morning

```bash
python3 scripts/vc_ops_cron.sh morning
```

### Midday

```bash
python3 fundlist.py submission-scan --query "ai accelerator apply"
python3 fundlist.py submission-report --limit 50 --min-score 8
```

### Evening

```bash
python3 scripts/vc_ops_cron.sh evening
```

## 중요한 해석 기준

- `ops-sync`는 기본적으로 `VC outreach queue` 쪽이다.
- `submission-scan`은 `apply / cohort / speedrun queue` 쪽이다.
- 둘은 합쳐서 운영하지만, 현재 DB와 명령은 아직 분리되어 있다.

즉, 지금 실제 사용 방식은 아래처럼 보면 된다.

1. xlsx로 투자처/VC seed 관리
2. web scan으로 cohort/apply 기회 발견
3. Telegram으로 요약 받아보기

## 다음 구현 우선순위

실제 운영 품질을 더 높이려면 다음 순서가 맞다.

1. `submission_targets`와 `vc_submission_tasks` 통합 우선순위 레이어
2. `priority_score`에 AI/Web3/stage/geography 적합도 강화
3. watchlist 프로그램 자동 dossier 생성
4. intro / outreach 상태 업데이트 명령 추가
