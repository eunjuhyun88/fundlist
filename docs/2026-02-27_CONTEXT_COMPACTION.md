# Context Compaction Protocol

## 목적

대화/작업 컨텍스트가 길어질 때 토큰 비용과 실패율을 줄이기 위해 상태를 저장하고 압축한다.

## 핵심 명령

```bash
# 1) 현재를 저장
python3 scripts/context_ctl.py save --label manual --summary "핵심 진행상황 요약"

# 2) 컨텍스트 컴팩션
python3 scripts/context_ctl.py compact

# 3) 복구 (컴팩트본)
python3 scripts/context_ctl.py restore --mode compact

# 4) 복구 (가장 최근 스냅샷)
python3 scripts/context_ctl.py restore --mode latest

# 5) 저장 목록
python3 scripts/context_ctl.py list
```

## 파일 위치

- `.context/snapshots/*.md`: 저장 스냅샷
- `.context/COMPACT.md`: 압축 요약본
- `.context/LATEST`: 최신 스냅샷 포인터

## 운영 규칙

- 큰 작업 시작 전 `save`
- 작업 단위 완료 후 `save`
- 2~3회 save마다 `compact`
- 리셋 후에는 `restore --mode compact`부터 확인
- 자동 주기는 `3시간` 권장 (cron)

## 자동 실행 (3시간)

```bash
cd /Users/ej/Downloads/VC\ list/fundlist

# 크론 등록(중복 제거 후 1개만 유지)
tmp="$(mktemp)"
(crontab -l 2>/dev/null || true) \
  | grep -v 'fundlist-context-compact-every-3h' \
  | grep -v 'context_cron.sh' > "$tmp"
{
  cat "$tmp"
  echo '# fundlist-context-compact-every-3h'
  echo '0 */3 * * * /bin/zsh "/Users/ej/Downloads/VC list/fundlist/scripts/context_cron.sh"'
} | crontab -
rm -f "$tmp"

# 확인
crontab -l
```

해제:

```bash
tmp="$(mktemp)"
(crontab -l 2>/dev/null || true) \
  | grep -v 'fundlist-context-compact-every-3h' \
  | grep -v 'context_cron.sh' > "$tmp"
crontab "$tmp"
rm -f "$tmp"
```

## 주의

- `복구해줘`는 의미가 두 가지일 수 있음:
  - 컨텍스트 복구
  - 파일/코드 복구
- 따라서 먼저 의도를 확인한 뒤 실행한다.
