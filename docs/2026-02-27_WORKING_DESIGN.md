# Working Design (Operation First)

## 목표

사용자가 원하는 흐름이 항상 작동하도록 설계한다:

1. 투자 데이터 수집
2. DB 저장 및 목록 조회
3. 컨텍스트 저장/압축/복구
4. 주기 자동화

## 사용자 의도 → 실행 계약

- `데이터 모아줘`
  - `python3 fundlist.py collect`
- `투자처 자료 모아줘`
  - `python3 fundlist.py fundraise-import`
- `목록 보여줘`
  - `python3 fundlist.py list --limit 30`
- `보고서 써줘`
  - `python3 fundlist.py fundraise-report --output <path>`
- `자료모으고 보고서까지 한 번에`
  - `python3 fundlist.py fundraise-run --with-ai --output <path>`
- `데드라인/스피드런 관리해줘`
  - `python3 fundlist.py ops-sync --alert-days 14 --output <path>`
- `날짜기준 제출 리스트 보여줘`
  - `python3 fundlist.py ops-list --from-days -365 --to-days 30`
- `계속 감시해줘`
  - `python3 fundlist.py ops-watch --interval-seconds 900`
- `오픈클로로 여러 에이전트 돌려줘`
  - `python3 fundlist.py openclaw-multi --query "<query>" --max-agents 3 --output <path>`
- `현재를 저장`
  - `python3 scripts/context_ctl.py save --label manual --summary "<핵심요약>"`
- `컨텍스트 컴팩션 해`
  - `python3 scripts/context_ctl.py compact`
- `복구해줘`
  - 먼저 의도 확인: 컨텍스트 복구 vs 파일 복구
  - 컨텍스트 복구면 `python3 scripts/context_ctl.py restore --mode compact`

## 실패 방지 규칙

- 소스 1개 실패가 전체 수집 실패로 번지지 않게 한다.
- 요약본(`.context/COMPACT.md`)은 항상 최신 상태로 유지한다.
- 자동 컴팩션은 3시간마다 실행한다.
- 스냅샷 파일은 최근 40개만 유지한다.

## 성공 기준 (Definition of Done)

- `collect` 실행 후 DB에 레코드가 삽입된다.
- `list`가 최소 1개 이상 출력된다.
- `fundraise-run` 실행 후 보고서 파일이 생성된다.
- `ops-sync` 실행 후 `vc_ops_snapshots`/`vc_ops_events`가 갱신된다.
- `ops-list`에서 날짜기준 제출 큐가 출력된다.
- `save -> compact -> restore`가 모두 정상 동작한다.
- `crontab -l`에서 3시간 주기 항목이 확인된다.

## 운영 체크

하루 시작 시:

```bash
python3 scripts/runbook_check.sh
```
