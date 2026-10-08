# Hermes PLAUD → Obsidian

수동 실행으로 PLAUD의 Transcript, 모든 Summary, Highlights를 원문 보존형 Markdown으로 가져오는 Hermes standalone 플러그인입니다. 자동 실행·스케줄러·Hermes Core 수정은 없습니다.

## 동작

- 최초에는 조회 가능한 전체 자료를 가져옵니다. 이후에는 전체 목록을 조회하되 신규·변경·파일 누락만 처리합니다.
- Transcript: 원본 화자·시각·발언 단락과 줄바꿈을 유지하고, 비한국어 발언 뒤에 한국어 번역을 붙입니다.
- Summary/Highlights: 원문 뒤에 전체 한국어 번역을 붙입니다. 재요약하지 않습니다.
- 번역이 검사에 걸리면 해당 번역 단위에 `[원문참조]`를 표시하고 원문은 그대로 보존합니다. 번역이 없으면 표시만 제공합니다. 자동 교정 재번역은 하지 않습니다.
- 한국어 원문은 번역을 생략합니다. 영문 제품명 등이 포함된 혼합 문장은 보수적으로 번역할 수 있습니다.
- 플러그인 소유권이 확인된 결과만 원자적으로 덮어씁니다. 관련 없는 노트는 보호하고, PLAUD에서 삭제된 자료의 노트를 자동 삭제하지 않습니다.
- 원문이 같으면 정상 번역·렌더 캐시를 재사용합니다. 모델 변경만으로 재번역하지 않습니다.

## 준비

- Hermes Agent, Hermes가 사용하는 Python 환경, Node.js와 npm이 필요합니다.
- 현재 Windows 환경에서 검증했습니다. Linux/macOS의 설치 경로·CLI 재구성은 미검증입니다.
- 기존 Obsidian vault와 vault 아래의 출력 폴더 이름을 준비합니다.
- PLAUD 로그인과 Hermes 모델 인증은 별도입니다. 비밀번호·인증코드·토큰을 채팅에 제공하지 마세요.
- 번역에 실제 모델 요청이 발생할 수 있습니다. 긴 기록의 최초 전체 번역은 오래 걸릴 수 있습니다.
- 공식 npm `@plaud-ai/mcp@0.3.14`를 사용합니다. 비공식 PLAUD API를 별도 구현하지 않습니다.

## GitHub에서 설치

이 저장소는 비공개이므로 GitHub 계정에 접근 권한이 있어야 합니다. Git은 해당 계정의 인증된 credential helper를 사용해야 합니다.

```text
hermes plugins install Gary-Y-Lee/Hermes_Plugin_PLAUD/source --no-enable --yes-deps
```

정확한 버전으로 재구성하려면 검증된 40자리 commit SHA를 `--ref`에 지정합니다.

플러그인 설치 후, **Hermes 모듈을 import할 수 있는 Hermes 관리 Python**으로 다음을 실행합니다. 일반 시스템 Python과 혼동하지 마세요. `HERMES_HOME`은 사용할 Profile의 실제 홈이어야 합니다.

```text
<Hermes-Python> -B <HERMES_HOME>/plugins/hermes-plaud/install_local.py --runtime-only --vault "<vault 절대 경로>" --output "<vault 아래 출력 폴더>"
hermes plugins enable hermes-plaud --no-allow-tool-override
hermes plaud status
hermes plaud login
hermes plaud sync
```

`--runtime-only`는 이미 설치된 동일 코드의 runtime·설정만 준비합니다. 플러그인 코드를 덮어쓰지 않습니다. Hermes의 모델 설정을 이용하므로 이 플러그인에 별도 모델 API 키를 넣지 않습니다.

## ZIP 또는 clone으로 설치

배포 ZIP을 풀거나 저장소를 clone한 폴더에서 실행합니다.

```text
<Hermes-Python> -B source/install_local.py --vault "<vault 절대 경로>" --output "<vault 아래 출력 폴더>"
hermes plugins enable hermes-plaud --no-allow-tool-override
hermes plaud status
hermes plaud login
hermes plaud sync
```

기존 플러그인 폴더가 있으면 일반 설치는 중단합니다. 임의 삭제하지 마세요. 동일 코드는 `--runtime-only`로 설정할 수 있지만, 버전 교체는 diff·상태·설치 출처를 확인한 뒤 처리해야 합니다.

## 수동 사용과 보고서

```text
hermes plaud status
hermes plaud sync
```

새 Hermes 세션에서는 `/plaud status`, `/plaud sync`도 사용할 수 있습니다. 긴 최초 Import는 CLI 실행을 권장합니다.

- CLI 마지막 JSON의 `failed`, `pending`을 확인합니다. **종료 코드만으로 전체 성공을 판단하지 마세요.**
- `pending`의 `Highlights: unavailable`은 해당 자료가 조회되지 않았다는 뜻이며 임의로 Highlight를 생성하지 않습니다.
- `${HERMES_HOME}/plugin-data/hermes-plaud/last-report.json`은 마지막 실행 범위의 보고서입니다. 부분 재처리의 총계를 전체 계정 총계로 오해하지 마세요.
- 원문·번역 캐시·인증과 runtime은 `${HERMES_HOME}/plugin-data/hermes-plaud`에 저장됩니다. Vault·GitHub·배포 ZIP에 포함하지 마세요.
- JSON 구조·발언 ID·원문 연결·파일 경계는 검증하지만, 의미상 번역 정확성을 보증하지 않습니다. `[원문참조]` 구간은 원문을 확인하세요.

## 개발·검증

구현·테스트·설치·검증 스크립트는 `source/` 아래에 있습니다. 추가 Python 패키지 없이 오프라인 테스트를 실행할 수 있습니다.

```text
PYTHONPATH=source python -B -m unittest discover -s source/tests -v
```

`source/verify_install.py`는 별도의 임시 Hermes home/vault에서 실제 설치·활성화·명령 인식·runtime handshake를 검증하며 로그인은 하지 않습니다. GitHub 설치 시험에는 `--repo`와 정확한 `--revision`을 지정할 수 있습니다.

`source/verify_live.py`는 인증된 실제 계정을 읽되 모델 호출을 차단한 반복 실행 시험입니다. 새 기록이 생기거나 재번역이 필요하면 의도적으로 실패합니다. 누락 복구는 실제 노트를 삭제하지 않고 임시 private vault에서 재현합니다.

`source/retry_failed.py`는 명시한 기존 보고서의 실패 record만 수동으로 재처리하는 운영 도우미입니다. 보고서·개인 자료는 저장소에 포함되지 않습니다.

## 라이선스

MIT. 공식 PLAUD npm 패키지·Hermes와 해당 서비스의 이용 조건·라이선스는 각각 적용됩니다.
