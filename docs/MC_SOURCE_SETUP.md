> 현재 경로 (2026-09-11): 소스 `D:\Claude\masonjar-7.0.2-MC.1-improving`, 실행본 `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64`, 인계 문서 `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\STATUS.md`.
> 아래는 2026-09-09 당시 생성·검증 이력이며, 당시 경로와 검사 결과는 현재 작업 지침이 아닙니다. 현재 절차는 소스 루트 AI_COLLAB_GUIDE.md를 따릅니다.

# 수정본 전용 소스 기준

작성일: 2026-09-09

## 경로와 보존 원칙

- 수정본 개발 소스: `D:\Claude\masonjar-7.0.2-MC.1`
- 원본 소스: `D:\Claude\masonjar-7.0.2` — 수정하지 않음
- 원본 실행본: `D:\Claude\masonjar-win32-x64-7.0.2\masonjar-win32-x64` — 수정하지 않음
- 현재 수정본 실행본: `D:\Claude\masonjar-win32-x64-7.0.2\masonjar-7.0.2-MC.1-win32-x64`

이 소스는 원본 소스를 복사하고 현재 수정본의 `resources/app` 변경을 반영해 구성했다. 이번 작업에서는 실행본을 교체하지 않았다. 실제 애플리케이션은 `py/`를 사용하므로 이미지 처리 변경은 실행 경로를 확인하고 적용한다.

## 반영 내역

- 실행본에서 변경 파일 7개와 추가 파일 3개 반영. 파일 목록과 SHA-256은 `MC_SOURCE_BASELINE.json` 참조.
- Seam 화면, 보정 알고리즘, CZI 타일 경계 기록, Adjustment Viewer 보정 표시 기능 포함.
- JavaScript에만 존재하던 변경을 `src/main.ts`와 `src/update_manager.ts`에 복원: Seam IPC/자동 조정/실패 표시, BaSiC 자동 조정, 세션 로그와 성능 로그, 시작 시 업데이트 확인 설정, 메뉴 표시, 조직 경계 조정 범위.
- `package-lock.json`의 프로젝트 버전 표기를 `package.json`과 같은 `7.0.2-MC.1`로 정리. 의존성 버전은 변경하지 않음.
- Python 캐시와 실행본의 의존성 폴더는 소스 반영 대상에서 제외.

## 검증 결과

- 잠금 파일의 TypeScript 4.9.5, @types/node 20.12.10, undici-types 5.26.5로 전체 타입 검사 및 컴파일 통과. 다운로드 패키지는 잠금 파일의 SHA-512로 확인.
- TypeScript에서 생성하는 JavaScript 6개가 현재 수정본과 코드 토큰 기준으로 일치. 주석, 줄바꿈, 서식과 선택적 마지막 인자 쉼표는 비교에서 정규화.
- 실행 코드/이미지/화면 등 288개 파일이 수정본과 바이트 단위로 일치. Python 캐시 제외.
- `test-menu-category.js`, `test-preprocess-batch-completion.js` 통과.
- `test-update-manager.js` 통과. Windows 프로세스 조회에서 제한 환경의 접근 거부 경고가 있었으므로 실제 업데이트 설치 검증으로 해석하지 않음. 이 테스트의 실행 의존성은 현재 수정본의 node_modules를 읽어 사용.
- 실제 Electron 화면, GPU/CZI 분석, 전체 배포 패키징은 수행하지 않음.

## 메모리 계측과 CZI 순차 처리 준비

`MASONJAR_PERF=1`일 때 `py/perf_log.py`가 Python 할당 피크와 psutil이 설치된 경우 프로세스 RSS를 `LOG: memory ...`로 기록한다. MAX 파일 전후와 CZI Z-stack 전후에 계측 지점을 추가했다. 기존 처리 결과에는 영향을 주지 않는다.

`py/czi_extract.py`에는 `max_project_plane_iter()`라는 순차 MAX 누적 building block을 추가했다. 합성 CZI 평면 테스트에서 기존 `numpy.max()`와 픽셀·dtype·shape가 동일함을 확인했다. 현재 import의 미리보기와 원본 Z-stack 저장은 모든 평면을 필요로 하므로, 이 함수는 후속 CZI writer 변경 전까지 실제 경로에 연결하지 않는다.

검토 결과, 원본 Z-stack은 `original_scans`에서 각 Z 평면을 보존해야 하므로 전체 MAX 결과만 남기는 방식으로 대체할 수 없다. 현재 writer는 `np.stack(planes)` 후 `tifffile.imwrite(..., compression="zlib")`를 호출한다. 순차 writer로 바꾸려면 TIFF 페이지를 `TiffWriter`로 차례로 기록하고, 8비트 변환의 전 스택 peak scaling을 보존하기 위해 1차 peak 측정 후 2차 기록(또는 16비트 보존 후 후처리)이 필요하다.

`write_pipeline_tiff_iter()`를 이 설계대로 추가했다. 이 함수는 iterable 평면을 한 페이지씩 기록하고, caller가 전달한 `scale_max`를 사용해 기존 8비트 변환 규칙을 유지한다. `scripts/test-czi-writer.py`에서 16비트 원본 페이지, 8비트 peak scaling, 페이지 수와 픽셀 동일성을 합성 데이터로 확인했다. 아직 `extract_z_stack()`에 연결하지 않은 이유는 현재 미리보기 선택과 전체 peak 계산을 한 번의 CZI 읽기로 동시에 유지할 수 없기 때문이다.

import 완료 후 성능 로그는 다음처럼 요약할 수 있다. 이 도구는 로그를 수정하지 않는다.

```powershell
node scripts/analyze-perf-log.js "C:\Users\mirih\.masonjar\masonjar.log"
```

미리보기는 `_preview_plane_from_stack()`에서 각 평면의 99번째 percentile을 비교해 가장 밝은 평면 하나를 고른다. 따라서 미리보기 자체는 모든 평면을 저장할 필요가 없고, 평면을 읽으며 현재 최고 평면만 보관하는 방식으로 바꿀 수 있다. 다만 DAPI preview는 선택된 평면을 다시 autoscale·downscale해 두 개의 PNG를 쓰므로, 원본 stack writer와 분리해 최적화해야 한다.

현재 발견된 후속 작업은 [MC_TASKS.md](MC_TASKS.md)에 기록한다.

## 이후 개발

`src/*.ts`를 수정한 경우 JavaScript만 수동 수정하지 말고 컴파일한다. 화면 JavaScript와 Python은 각각 `js/`, `py/`에서 수정한다.

현재 로컬 `node_modules`에는 컴파일 검증에 필요한 패키지만 준비되어 있다. 전체 개발·패키징 환경은 Node/npm 설치 후 이 폴더에서 `npm ci`로 준비한다.

```powershell
node node_modules/typescript/bin/tsc --noEmit
node node_modules/typescript/bin/tsc
node scripts/verify-source-parity.js
```

현재 실행본과 다시 비교하려면:

```powershell
node scripts/verify-source-parity.js "D:\Claude\masonjar-win32-x64-7.0.2\masonjar-7.0.2-MC.1-win32-x64\resources\app"
```

기능을 개선한 후에는 현재 실행본과 차이가 발생하는 것이 정상이다. 이 비교는 복원 기준 확인용이며, 새로운 기능의 정답을 보장하는 테스트가 아니다. 향후 배포 전에는 별도 출력 폴더에서 빌드·기능 검증 후 수정본 실행 폴더에 반영한다.

