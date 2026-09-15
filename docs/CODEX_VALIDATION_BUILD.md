> 현재 경로 (2026-09-11): 소스 `D:\Claude\masonjar-7.0.2-MC.1-improving`, 실행본 `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64`, 인계 문서 `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\STATUS.md`.
> 아래는 2026-09-09 당시 생성·검증 이력이며, 당시 경로와 검사 결과는 현재 작업 지침이 아닙니다. 현재 절차는 소스 루트 AI_COLLAB_GUIDE.md를 따릅니다.

# Codex 검증용 Windows 실행본

- 생성일: 2026-09-09
- 소스: `D:\Claude\masonjar-7.0.2-MC.1`
- 출력: `D:\Claude\masonjar-win32-x64-7.0.2\masonjar-7.0.2-MC.1-Codex-win32-x64`
- 실행 파일: 출력 폴더의 `masonjar.exe`
- 앱 버전: `7.0.2-MC.1` (폴더 이름으로 검증본 구분)
- Electron: 18.1.0 / Node: 16.13.2 / Windows x64

## 빌드

잠금 파일 기준 npm ci 및 patch-package 적용 후 TypeScript 컴파일, Electron Forge Windows x64 package 수행. 소스의 out 폴더에서 완성된 패키지를 위 출력 경로로 이동했다. 업데이트 도구 호환용으로 resources/app/package.json을 실행본 루트에도 복사했다. 기존 실행본 파일을 복사해 조립한 것이 아니라 새 소스에서 패키징했다.

빌드 도구 npm 10.9.2는 `D:\Claude\masonjar-build-tools\package\bin`에 있으며, 소스의 전체 개발 의존성 설치를 완료했다. MC_SOURCE_SETUP.md의 '컴파일 검증 패키지만 준비' 상태는 이 빌드 이전 기록이다.

## 확인

- TypeScript 컴파일 및 생성 코드 6개 일치 검사 통과.
- 소스와 패키지의 실행 관련 파일 288개 바이트 일치 확인.
- test-file-index 10개, test-pipeline-run 7개 통과.
- 새 masonjar.exe를 ELECTRON_RUN_AS_NODE 모드로 실행해 Electron/Node/x64와 런타임 의존성 5개가 패키지 내부에서 해석되는 것을 확인.
- 원본 소스, 원본 실행본, 기존 MC 실행본은 변경하지 않음.

화면을 여는 앱 부팅, Seam 기능, 실제 CZI/GPU 처리는 아직 검증하지 않았다. 사용자 요청의 1번(별도 실행본 만들기) 완료 기록이다. 앱의 Python 환경과 사용자 설정은 기존 동작대로 사용자 홈의 .masonjar를 이용하므로, 실행 폴더 분리는 사용자 환경까지 분리하는 것은 아니다.

