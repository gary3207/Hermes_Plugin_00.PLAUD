# PLAUD Import — Kanban 수동 실행

기존 Import 엔진 `hermes-plaud`에 선택적으로 연결하는 별도 플러그인 `hermes-plaud-kanban`입니다. 엔진을 복제하지 않고 동일 Profile에 설치된 엔진·설정·번역·캐시를 재사용합니다. 자동 시간 예약은 없으며, 사용자가 Ready로 이동한 대기 카드만 실행합니다.

## 저장소 구조

- `source/`: 기존 `hermes-plaud` 엔진과 설치·검증 도우미.
- `source/kanban/`: Kanban 연결 플러그인 `plugin.yaml`, `__init__.py`, `plaud_kanban.py` 및 합성 오프라인 테스트.
- `source/kanban/skills/plaud-kanban-manual-jobs/SKILL.md`: 플러그인이 등록하는 재설치·운영·진단 스킬. 새 Hermes 세션에서 `hermes-plaud-kanban:plaud-kanban-manual-jobs`로 조회합니다.
- 두 플러그인은 각각 설치합니다. 엔진만 필요한 경우 기존 설치 방법이 그대로 유효합니다.
- Kanban 실행기는 Windows 프로세스 트리 격리에 의존합니다. Linux/macOS에서의 실행은 지원 검증되지 않았으며, Windows가 아니면 실행 단계에서 실패 처리합니다.
- 현재 Hermes 네이티브 Kanban API와 dispatch hook을 사용합니다. Hermes 업그레이드 후에는 아래 테스트와 `plugins doctor`를 다시 확인하세요.

## 설치·재구성

먼저 [README](README.md)에 따라 같은 Profile에 `hermes-plaud`를 설치·활성화하고 vault·출력 폴더·PLAUD 로그인·모델 인증을 준비합니다. 실행마다 동일 Profile의 `HERMES_HOME`을 사용하세요. 기존 계정의 인증·원문 캐시를 저장소에서 가져오는 방식은 아닙니다.

그다음 비공개 저장소 접근 권한이 있는 GitHub 계정으로 연결 플러그인을 설치합니다.

```text
hermes plugins install gary3207/Hermes_Plugin_00.PLAUD/source/kanban --no-enable --yes-deps
hermes plugins enable hermes-plaud-kanban --no-allow-tool-override
hermes plugins doctor hermes-plaud-kanban --ci
hermes plaud-kanban status --board default
```

정확한 재구성에는 두 설치 명령 모두 같은 검증된 40자리 commit SHA를 `--ref`에 지정합니다. 이 명령들은 활성 Profile을 대상으로 하므로 다른 Profile을 원하면 `hermes -p <Profile>`로 동일하게 지정하세요.

최초 보드 구성은 명시적으로 실행합니다.

```text
hermes plaud-kanban initialize --board default
hermes plaud-kanban status --board default
```

`initialize`는 대기 카드만 생성하며 Import를 시작하지 않습니다. 새 프로세스 또는 재시작한 Gateway에 `on_kanban_dispatch_tick` hook이 로드되어야 Ready 이동을 감지합니다. Gateway의 자동 시작·스케줄 설정은 이 플러그인이 구성하지 않습니다. 실행 중에는 초기화·설치 교체·플러그인 비활성화를 하지 마세요.

## 실행·취소·다음 실행

1. **Scheduled 대기:** `[실행 대기] PLAUD Import` 카드 한 장. 날짜·wake-up 타이머 없음.
2. **수동 실행:** 해당 카드만 Ready로 이동합니다. 연결 플러그인이 claim한 뒤 Running으로 전환합니다. Running으로 직접 드래그하지 않습니다.
3. **성공:** 검증까지 통과하면 Done의 `[완료]` 카드로 남습니다.
4. **실패·부분 실패:** Blocked의 `[실패]` 카드로 남습니다. 정상 저장한 노트는 유지합니다.
5. **취소:** 실행 카드를 Blocked로 옮기거나 삭제합니다. 실제 소유 프로세스 트리 종료를 확인한 뒤 정리합니다. 삭제한 카드의 보드 이력은 없어지지만 비공개 실행 요약은 별도로 보존합니다.
6. **다음 실행:** 종료·정리 뒤 새로운 Scheduled 대기 카드 한 장을 생성합니다. 이전 완료·실패·취소 카드를 Ready로 옮겨 재실행하지 마세요.

카드 ID는 실행마다 새로 부여합니다. `status`의 `waiting` 값으로 현재 대기 카드를 확인하고, 필요하면 `hermes kanban --board default show <waiting-ID> --json`으로 조회하세요. `plaud-job`는 전용 담당자 lane이며 새 Agent Profile이 아닙니다.

## 진행·로그·보존

- 신규 회의록은 녹음 날짜 기준으로 출력 폴더 아래 `YYYY/MM/`에 저장합니다. 예: `Meeting Minutes/2026/10/2026-10-23 09-00-00 - 회의 제목 - Summary.md`. 원본 시각의 연도·월을 유지하며 날짜를 알 수 없으면 `날짜 미상/`에 저장합니다. 기존 노트는 현재 위치에서 갱신하고 자동 이동하거나 중복 생성하지 않습니다.

- 카드에는 시작·종료 시각, 총 소요시간, 현재 단계, 단계별 시간, 처리 집계, 정제된 오류와 비공개 로그 위치를 표시합니다.
- 단계: 준비 → 목록 조회 → 기록별 원문 조회·번역·저장 → 산출물 검증. 본문은 단계 전환·실패·취소·최종 결과 때만 갱신합니다.
- 시간은 분·초로 표시합니다. 3.x의 원문 조회·번역·기타 레코드 처리는 상위 단계에 포함된 누적 시간이며 별도 순차 단계가 아닙니다. 과거 미측정 작업은 `미보고 / 기록 없음`으로 표시합니다. 상세 표는 Description에만 두고 Result·실행 Summary는 짧게 남깁니다.
- `${HERMES_HOME}/plugin-data/hermes-plaud-kanban/`에 journal과 실행별 요약·진단을 저장합니다. 최근 100회의 비공개 실행 자료를 보존하며 네이티브 이력 카드를 이 정책으로 자동 삭제하지 않습니다.
- 회의 원문·번역 본문·자격 증명은 진단 로그에 기록하지 않습니다. 상태·실행 자료·보드 DB·인증·개인 vault 경로를 GitHub나 배포 ZIP에 포함하지 마세요.
- CLI 종료 코드만으로 성공을 판단하지 않습니다. JSON의 `success: false`, 실제 카드 상태, `active`, 실행 요약의 outcome·failed·verification_errors를 확인하세요.
- `tick`·`reconcile`은 Ready/복구 대상 작업을 시작하거나 정리할 수 있는 운영 명령입니다. 단순 상태 확인에는 `status`를 사용하세요.

## 카드의 실제 처리 순서

Description과 대기 카드에는 준비(출력 경로·잠금 → 기존 노트 인덱스), 전체 목록 조회, 레코드별 처리(원문 조회 → 기존 노트 변경·미완료·무결성 비교 → 변경 없음 생략 또는 렌더 캐시/조건부 번역 → 안전 저장), 최종 재인덱스·산출물 검증을 표시합니다. 하위 항목은 처리 계획이며 실행 증거가 아닙니다. 실측된 원문 조회·번역은 레코드별 누적이고 상위 시간에 포함됩니다. 측정하지 않은 하위 작업은 `미보고 / 기록 없음`으로 표시하며 시간을 추정하지 않습니다.

## 구체적인 오류와 Aside 재로그인

인식 가능한 MCP 오류는 코드·도구명·안전한 설명·확인된 HTTP 상태를 실행별 `events.jsonl`, `result.json`, `summary.json` 및 카드 Description에 남깁니다. 인증 필요는 `PLAUD_AUTH_REQUIRED`, 호출 제한은 `PLAUD_RATE_LIMITED`, 네트워크 실패는 `PLAUD_NETWORK_ERROR`로 구분합니다. 알려지지 않은 오류는 원문·토큰 노출 방지를 위해 일반 코드로 유지합니다. 과거 실행 로그는 소급해서 바꾸지 않습니다.

인증이 필요하면 Hermes에 PLAUD 재로그인을 요청하세요. Agent는 `hermes plaud login --aside`를 백그라운드로 실행하고 출력된 공식 OAuth URL을 Aside 브라우저에 엽니다. 사용자가 직접 로그인·승인하면 기존 공식 SDK가 PKCE와 localhost:8199 callback으로 인증을 저장합니다. 대기는 10분이며, Agent는 인증 결과와 최소 목록 조회를 확인합니다. 로그인 중 비밀번호·인증코드를 채팅에 보내지 마세요. 실패한 Import나 새 대기 카드는 자동 재실행하지 않습니다.

Aside 연결은 고정된 공식 MCP 0.3.14 SDK를 재사용합니다. 다른 버전의 내부 구조는 임의로 추정하지 않고 실패 처리하며 검토 후 지원합니다. 공식 문서의 `list_files` 검색·날짜 필터는 선택적 기능이므로 전체 Import에는 추가하지 않습니다.

## 번역 연결 복구와 원문 보존

- Summary는 PLAUD 원문을 가져와 번역하며 새로 요약하지 않습니다. `plaud_translation` 전용 auxiliary task는 기본 모델을 그대로 사용하고 무출력 제한만 기본 120초로 분리합니다. 요청 timeout은 180초이고, 의미 있는 출력이 이어지는 스트림의 전체 시간과 같은 뜻은 아닙니다. 운영자의 해당 task 설정이 등록 기본값보다 우선합니다.
- 일시적인 번역 timeout/연결 오류에는 새 host facade 호출로 한 번만 복구합니다. 인증·권한·형식 오류를 반복 호출하거나 숨기지 않습니다. 모델·기존 12,000자 분할 기준·성공 캐시 identity는 변경하지 않습니다.
- 복구 실패 시 원문과 가능한 번역을 유지하고 미완료 단위에는 `[원문참조]`를 표시합니다. 노트 frontmatter는 `plaud_translation: "incomplete-transport"`, 본문은 `번역 미완료`로 남고 JOB은 계속 실패/부분실패입니다. 미완료 자료를 성공 번역/렌더 캐시에 넣지 않으며 다음 수동 실행에서 해당 번역을 보완합니다.
- `${HERMES_HOME}/plugin-data/hermes-plaud/translation-events.jsonl`에는 시도 번호·요청 문자 수·소요시간·재시도 여부만 남깁니다. 원문·번역·자격증명·임의 서버 오류 문구는 기록하지 않습니다. 첫 출력/출력 정지의 상세 시각까지 계측하는 로그는 아닙니다.

## 오프라인 검증

Hermes 모듈과 의존성을 사용할 수 있는 관리 Python으로 저장소 루트에서 실행합니다.

```text
PYTHONPATH=source python -B -m unittest discover -s source/tests -v
PYTHONPATH=source/kanban python -B -m unittest discover -s source/kanban -p "test_*.py" -v
```

Kanban 테스트는 `source/kanban/test-sandboxes/`의 폐기 가능한 보드·vault와 합성 worker를 사용합니다. 엔진은 저장소의 `source/`에서 가져오며 개인 설치 경로에 의존하지 않습니다. 테스트는 실제 계정 로그인·PLAUD 네트워크·모델 호출을 하지 않습니다.

검증 범위에는 Scheduled 자동 실행 방지, Ready 수동 시작, 기존 Blocked 대기 카드 이전, 취소·삭제·자식 프로세스 종료, 중복 실행·대기 카드 방지, crash 복구, 과거 실행의 덮어쓰기 차단, nonzero exit와 성공 요약 충돌, 부분 성공 유지·산출물 hash 검사·Windows 파일 교체 재시도가 포함됩니다. 실제 계정 Import와 Desktop 드래그 동작의 전수 검증으로 해석하지 마세요.
