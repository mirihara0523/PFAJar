# masonjar 개선 — Claude ↔ ChatGPT 교차 운용 지침 (v4, 2026-09-11)

두 AI(Claude, ChatGPT/Codex)가 **하나의 전용 소스**를 번갈아 편집할 때 충돌·분기(divergence)·회귀를 막기 위한 규칙. 두 AI는 서로의 메모리·대화를 공유하지 못하므로 **git 이력 + 이 문서 + STATUS.md** 가 유일한 공유 상태다. 시작 전 반드시 이 셋을 읽는다.

> v2 변경(2026-09-11, Codex 검토 반영): 편집 대상을 **패키지 실행본 → 전용 소스**로 정정. 소스↔실행본 반영 흐름 명시. seam refine window 설명 정정(±274 고정 → 스케일+5% cap). batch OOM은 확정 아닌 가설로 강등. Adjustment Viewer 작업 항목 추가.
>
> v3 변경(2026-09-11, 사용자 중계로 전달된 Codex 피드백 3건 반영 — **Codex 재확인 완료, 정식 확정**):
> 1. STATUS.md 협업 상태 필드(현재 담당자/다음 담당자/상태) 정식 채택 (§6).
> 2. §4 소스↔실행본 드리프트 규칙 완화 — 검증(회귀 테스트) 대기 중에는 차이가 정상이며, STATUS.md에 미반영 사유를 기록하면 된다. 검증 통과 후에는 지체 없이 반영.
> 3. §7 검증 정책 재정리 — "AI가 검증 완료를 자체 선언하지 않는다"는 원칙은 유지하되, 변경에 맞는 회귀 테스트 작성은 폭넓게 허용. 과거 특정 세션의 제약을 전체 규칙으로 자동 일반화하지 않는다. §9 백로그는 착수 전 현재 코드 대조 재확인을 의무화.
>
> v4 변경(2026-09-11, 사용자 요청에 따른 Codex 초안 — **Claude 재확인 대기**): 중간 사용량 한도 도달을 피하기 위해, 새 구현·재빌드 시작 전 사용량을 확인한다. 어느 창이 20% 이하이면 사용자에게 Claude 인계 여부를 묻고, 10% 이하이면 새 작업을 시작하지 않고 체크포인트 후 인계를 요청한다. 실시간 백그라운드 감시는 하지 않는다.

---

## 0. TL;DR (매 세션 체크리스트)

1. `git -C "<소스>" log --oneline -15` + `git status` → 상대 변경 확인.
2. §4 규칙 확인(전용 소스만 편집, fork 금지).
3. STATUS.md 의 Pending/미해결 확인 → 작업 항목 하나 선택. **§9 백로그 항목은 선택 전 현재 코드와 대조해 여전히 유효한지 확인한다** (과거 기록을 확인 없이 사실로 인용하지 않음).
4. **소스 편집** → 문법검증 + 필요한 회귀 테스트(§7) → (통과 시) **실행본에 반영**(§3) — 아직 통과 전이라 반영하지 않았다면 STATUS.md에 어떤 파일이 다른지·사유를 기록(§4.6) → **사용자 실앱 검증** → 통과 후 소스에 `git commit`(§5).
5. STATUS.md **협업 상태** 절 갱신: 현재 담당자/다음 담당자/상태(작업 중 → 인계 가능) + 방금 한 일 1줄 + 다음 AI 주의 1줄 + 검사 결과 + 실행본 반영 여부(아니오면 사유)(§6).
6. 새 구현·재빌드 전 사용량을 확인한다. 장기 사용량 창의 **잔여가 10% 미만**이면 새 구현·재빌드를 시작하기 전에 사용자에게 Claude 인계 여부를 묻고, 현재 변경과 검사 결과를 STATUS.md에 기록한다.

---

## 1. 경로 — 소스(편집) vs 실행본(테스트)

| 역할 | 경로 | 용도 |
|---|---|---|
| **전용 소스 (편집·git·정본)** | `D:\Claude\masonjar-7.0.2-MC.1-improving` | 모든 편집·커밋은 **여기서만**. electron-forge 소스 트리(`py/`·`js/`·`pages/`·`main.js` 루트). |
| **실행본 (반영·테스트)** | `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64` | 소스 편집분을 여기 `resources\app\`에 반영해 **실앱 실행·검증**(`masonjar.exe`, 로그 생성처). |

- **전용 소스 밖 편집 금지 / 별도 사본 fork 금지 / 동시 병렬 금지.** 순차 교대만.
- 참고용(편집 금지): `...MC.1-improving-win32-x64`(=실행본, 반영 대상이지 편집 원본 아님), 구 `...MC.1-win32-x64`, `masonjar-7.0.2\`(구 소스), `-Codex-win32-x64\`(과거 fork). 필요분은 소스로 **선별 이식**만.

---

## 2. 프로젝트 개요

masonjar = 마우스 뇌 이미지(brain image) 분석용 Electron(+Python) 데스크톱 앱, v7.0.2-MC.1. electron-forge 기반. 개선 축: BaSiCPy shading correction, **seam correction**(tile 경계 보정), Adjustment viewer(`py/adjust.py`), CZI import(`py/czi_extract.py`).

---

## 3. 반영 흐름 (소스 → 실행본) 과 빌드 규칙

**사용자 결정 (2026-09-11): 전체 재빌드 및 sync -Apply를 포함한 실행본 반영은 사용자 명시 확인을 받은 뒤 수행한다. 테스트 통과 또는 앱 종료만으로 반영을 승인한 것으로 간주하지 않는다. 확인 대기 중 소스·실행본 차이는 STATUS에 기록하며 허용한다. 이 지침은 아래의 즉시 반영 문구보다 우선한다.

**편집은 소스에서, 테스트는 실행본에서.** 소스 변경은 **`scripts\sync-improving.ps1`로 반영한다**(asar 미압축 = 평문 파일 그대로 로드). 임의 수동 복사 대신 이 스크립트를 쓴다.

```powershell
cd D:\Claude\masonjar-7.0.2-MC.1-improving
.\scripts\sync-improving.ps1 -Files 'py\adjust.py'          # 기본: 읽기전용 해시 비교(변경 여부만)
# 회귀 테스트 통과 + 실행본 종료 후:
.\scripts\sync-improving.ps1 -Files 'py\adjust.py' -Apply    # 문법검사→백업→복사→SHA256 확인
```

- 지정한 파일만 해시 비교(전체 자동탐색 안 함). 허용: `main.js`, `py/js/pages/css` 아래 Python·JS·HTML·CSS.
- `-Apply`: 실행 프로세스 확인 → 문법 검사(py는 소스 `.venv` 사용) → 백업(`_coordination\deploy-backups`) → 복사 → SHA256. 복사 실패 시 이번 파일 복구.
- 반영 도중 앱을 다시 실행하지 않는다(닫은 상태 유지). TypeScript는 먼저 컴파일해 생성 JS를 지정. 의존성·패키징·자산·삭제 변경은 별도 검토 후 전체 빌드.
- 회귀 테스트를 아직 통과하지 못해 반영을 미룬 상태(소스 ≠ 실행본)는 정상이다. 규칙은 §4.6 참고 — 언제까지나 미반영으로 방치하는 것이 아니라 **기록된** 드리프트만 허용된다.

| 변경 파일 | 실행본 반영 후 효과 |
|---|---|
| `py/*.py` | 실행마다 새 프로세스 spawn → 복사만 하면 **다음 실행에 즉시 반영**(빌드 불필요) |
| `js/*.js`, `pages/*.html`, `css/*` | 해당 창 reload 시 반영 |
| `main.js` (Electron main) | **앱 재시작** 필요 |
| `map.py`/`adjust.py`(FORCE_SHELL 뷰어) | 해당 뷰어 재실행 시 반영 |

- 정식 패키지 검증이 필요하면 소스에서 `npm run make:win32`(→ `out\masonjar-win32-x64\`) 후 실행본 갱신. (참고) `npm start`(electron-forge)로 소스 직접 실행도 가능하나 합의 창구는 **실행본**이다.

**venv (런타임):** 앱은 `~/.masonjar/benv`(= `C:\Users\mirih\.masonjar\benv\Scripts\python.exe`)를 사용(main.js 확정). 핵심 패키지(`aicspylibczi` 등)는 이 venv에만 있음 — 진단 스크립트도 이 venv로 실행. **소스의 `.venv/`는 dev/tooling용이며 앱 런타임과 무관**(git 제외 대상).

---

## 4. 절대 규칙 (하지 말 것)

1. **전용 소스 밖 편집 금지 / fork 금지 / 병렬 금지.** 순차 교대만.
2. **대용량·생성물 커밋 금지.** `.gitignore`가 `out/`·`node_modules/`·`logs`·`__pycache__`·`nrrd/`·`*.pt/pth` 제외. `.venv/`·루트 `*.nrrd`는 미제외이니 커밋 전 추가(§부록 A).
3. **미검증 코드 커밋 금지.** 실앱 결과는 **사용자 실행본 확인** 전엔 "검증됨"이 아니다(§7).
4. **설계 계약(§8) 위반 금지.** 특히 seam은 offset-only + known-geometry.
5. **관측성 없이 기능 추가 금지(§7).** 실패 원인이 durable log에 남게 계측을 함께.
6. **소스↔실행본 드리프트는 검증 대기 중엔 허용, 단 반드시 STATUS.md에 기록한다.** (v3) 회귀 테스트를 아직 통과하지 못한 변경은 실행본에 반영하지 않아도 된다 — 이때 STATUS.md "협업 상태"(§6) 또는 "현재 변경 / 검증 대기" 절에 어떤 파일이 다른지와 미반영 사유(예: "브러시 회귀 테스트 대기")를 명시한다. 검증을 통과하면 지체 없이 §3 절차로 반영한다. 커밋은 소스 기준. **기록 없는 drift(=다음 담당자가 소스≠실행본을 모르는 상태)는 여전히 금지.**

---

## 5. git 워크플로 (소스 리포지토리)

전제: **git은 전용 소스에만.** 단일 브랜치 `main`, 순차 선형 커밋(병렬 아니므로 브랜치·머지 불필요).

1. 시작: `git status`(clean 확인) → `git log --oneline -15`(상대 변경 파악).
2. 소스 편집 → 문법검증(§7) → 실행본 반영(또는 §4.6에 따라 미반영 사유 기록) → 사용자 실앱 검증.
3. 커밋(한 논리 단위 = 한 커밋):
   ```
   [AI][영역] 요약 (검증상태)

   - 무엇을 / 왜(근본원인·의도)
   - 검증: node --check / py_compile / 회귀 테스트 / 사용자 실앱 확인 여부
   - 실행본 반영: 예/아니오 (아니오면 사유 한 줄)
   ```
   `[AI]` = `CLD`(Claude) / `GPT`(ChatGPT). 커밋 주체 항상 식별.
4. 상대 커밋 리뷰: 교대 시작 시 `git show <hash>` / `git diff` 로 검토. 계약 위반·회귀 발견 시 STATUS.md에 지적 + 새 커밋으로 `git revert`(히스토리 보존; `push --force`·`rebase`·`reset --hard` 금지).
5. 롤백: `git revert <hash>` 또는 파일 단위 `git checkout <hash> -- <file>`.

**커밋 실행 주체:** git 명령은 D: 드라이브에서 실행되며 Claude 샌드박스에서는 직접 실행이 안 될 수 있다 → 그 경우 **사용자가 PowerShell로 커밋**(Claude는 변경 파일·메시지 초안 제공). ChatGPT/Codex는 자체 CLI로 직접 커밋 시 위 규칙 준수.

---

## 6. 핸드오프 (STATUS.md) — 공유 상태 단일 창구

위치: `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\STATUS.md`(양쪽 접근 확인됨). 구성: **협업 상태**(아래 필드, v3 정식 채택) · 확정 경로/작업방식 · 반영 도구 · 확인된 상태 · 현재 변경/검증 대기 · 다음 작업 · 이력.

**협업 상태 필드 (v3, 정식):**

| 필드 | 의미 |
|---|---|
| 현재 담당자 | 지금 작업 중인 AI(`Claude`/`Codex`). 아무도 작업 중이 아니면 `없음(대기)`. |
| 다음 담당자 | 상태가 `인계 가능`일 때 다음에 작업할 것으로 예상되는 쪽. 비워두면 상대측 AI로 간주. |
| 상태 | `작업 중` 또는 `인계 가능`. |
| 방금 한 일 | 1줄 — 이번 교대에서 실제로 한 일. |
| 다음 AI 주의 | 1줄 이상 — 다음 담당자가 바로 알아야 할 것(이견·리스크·미반영 사유 등). |
| 검사 결과 | 수행한 검증(문법/회귀/사용자 확인) 요약. |
| 실행본 반영 여부 | 예/아니오 — 아니오면 사유(§4.6 참고). |
| 사용량 인계 상태 | 마지막 확인 시점과 남은 비율. 20% 이하라면 사용자 인계 확인 여부도 기록. |

- 교대를 **시작**할 때: 현재 담당자를 자신으로, 상태를 `작업 중`으로 바꾼다.
- 교대를 **마칠** 때: STATUS.md를 수정하기 **전에** 불변 백업을 만든다. 경로는 `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\handoffs\status-snapshots\STATUS-<yyyyMMdd-HHmmss>-<sender>.md`이며, 기존 백업을 덮어쓰지 않는다. 복사 뒤 원본·백업 SHA-256이 같은지 확인한 다음 방금 한 일·다음 AI 주의를 갱신하고, 다음 담당자를 지정, 상태를 `인계 가능`으로 바꾼다.
- 교대를 **받을** 때: 최신 handoff snapshot의 경로·해시를 확인한 뒤 STATUS.md를 읽는다. STATUS.md가 예기치 않게 축소·교체된 경우 snapshot을 근거로 복구 후보를 만들되, 동기화 간섭이 의심되면 원본을 즉시 덮어쓰지 않는다.
- 사용량은 실시간 백그라운드 감시 대상이 아니다. 새 구현·재빌드처럼 중단 비용이 큰 단계의 **시작 전** 장기 사용량을 확인한다. 잔여가 10% 미만이면 안전한 체크포인트를 남긴 뒤 사용자에게 Claude 인계 여부를 확인한다.
- 장문 이력 누적 금지(구버전은 `STATUS-history-*.md`로 보관, 상세는 git log).
- **STATUS.md를 통째로 덮어쓰지 말고** 실파일 확인 후 해당 절만 통합.

---

## 7. 검증 정책

- **AI가 "검증 완료"를 자체 선언하지 않는다.** 합성 이미지를 만들어 육안 비교한 뒤 그것을 근거로 스스로 검증 완료라고 판정하는 것은 금지. **실제 화면·성능의 최종 판정은 항상 사용자의 실행본 확인.**
- **변경에 필요한 회귀 테스트는 폭넓게 허용한다.** (v3, 완화) `node --check`(JS) / `python -m py_compile`(PY) / 순수 로직 unit test(소스 `scripts/test-*.js` 및 그 확장)뿐 아니라, 변경 성격에 맞는 다른 회귀 테스트(예: seam 좌표 계산 회귀, known-geometry sidecar 파싱 회귀 등)도 작성·실행해도 된다. 단 이 테스트들의 통과가 "사용자 확인"을 대체하지는 않는다 — 커밋·STATUS에는 여전히 "미검증/검증완료(사용자 확인 여부)"를 명시.
- **예외:** 사용자 제공 **실측 파일**의 수치 진단(autocorrelation·gradient·per-seam step)은 허용·권장.
- **관측성 우선:** 새 기능/수정은 실패 원인이 durable log에 남게 계측(`_log` stdout/traceback 미러링 등).
- **과거 세션 제한을 자동으로 일반화하지 않는다.** (v3) 특정 세션에서 필요했던 검증 제약을 전체 규칙으로 자동 승격하지 않는다. 새로운 전면 제약이 필요하다고 판단되면 이 문서에 근거와 함께 명시적으로 추가한다(암묵적 관례로 남기지 않음).

---

## 8. 핵심 설계 계약 (준수) — 코드가 최종 진실, 아래는 요약

**결정 B — whole-section BaSiC 비권장.** stitch된 whole-section에 BaSiCPy는 용도 위반(서로 다른 섹션이 공통 shading field 공유 가정 붕괴 + 단일 전역 flatfield가 tile 반복 vignette 표현 불가). 증상: cortex flatfield<1 → 과증폭 saturate. 보정은 per-tile(stitch 전)/촬영단계 shading. BaSiC 탭 유지하되 정량 비권장.

**Seam correction = offset-only + known-geometry.**
- 세로 tile seam의 DC step을 **offset 보정만**(gain/blend 금지 — residual 지표 gaming 취약).
- **Known-geometry(권장):** import 시 `czi_extract.py::_write_seam_grid_sidecar()`가 tile 경계를 `<bundle>/.masonjar/seamgrid/<slice_id>.json`에 기록(scene당 1회·채널무관·멱등, 실패 시 `LOG: seamgrid_skip <reason>`). **실 CZI 검증 완료**(`(63).czi` → 6×6, 세로/가로 seam 각 5).
  - 스키마: `{version, source:"czi_bbox", extent_source, scene_extent_px:[W,H], n_tiles, vertical/horizontal:{boundaries_px, boundaries_frac, period_px, phase_px}}`. 다운스케일 대응 위해 **분수좌표 동봉**.
  - gotcha: (1) `get_mosaic_scene_bounding_box(scene)` 대신 **tile bbox union으로 extent**. (2) `get_all_mosaic_tile_bounding_boxes`는 **S 없이 `{Z,C}` 먼저**(overspecified 회피). (3) stage jitter로 outer edge ~1px 가짜 seam → period 절반 이내 edge-filter.
  - **refine window(정정):** `SEAMGRID_REFINE_MARGIN=274`는 **base 상수**일 뿐. 실제 window는 `_geometry_refine_margin()`가 이미지 크기에 맞춰 스케일(`274 × length/source_length`)하고 **타일 간격의 5%로 상한(cap)** 한다(다운스케일 이미지 과탐색 방지). `SEAMGRID_SOFT_THRESHOLD=1.2 gray`.
  - **방향 처리:** `_orient_seam_grid(grid, ops)`가 회전/플립 이력을 sidecar 격자에 반영(로드 시 이미지 방향과 정합).
- **Autocorr fallback(sidecar 없을 때):** `AUTOTUNE_SCORE_BAND=4`(고정 채점창 — 후보 band와 독립해야 오버슈팅 방지, 실효 최적 band ≈ period/6~period/4). 게이트 `SEAM_COVERAGE_MIN=0.5`, `_PERIODIC=0.35`, `_RECOVERED=0.7`, `SEAM_STEP_MIN=6.0`, `SEAM_RECOVERED_EDGE_MARGIN=20`. period 선택 `PERIOD_CANDIDATE_TOP_K=8`, `_MIN_PERIODIC=2`, `_SELECT_SCORE_BAND=4`.

**아키텍처 한계:** offset은 경계 DC step(13~20 gray)만 제거. tile 내부 vignette bow(~22 gray)는 원리적으로 못 없앰(촬영단계/per-tile flatfield 몫). band 튜닝으로 해결 불가.

---

## 9. 승계된 미해결 이슈 (backlog)

**작업 시작 전 확인(v3):** 아래 항목은 작성 시점 기준 기록이다. 이 backlog에서 작업을 고르기 전에 해당 코드를 직접 열어 지금도 유효한지(이미 고쳐졌는지, 증상이 달라졌는지) 확인하고 STATUS.md에 결과를 갱신한다. 확인 없이 과거 서술을 그대로 현재 사실처럼 인용하지 않는다.

- **버그 B (로그 은폐, `main.js`):** `spawnPreprocessPreview`가 `PREVIEW_JSON:{ok:false}`를 generic "exited code 1"로 덮어씀 + `pyshell.on("stderr")` 미등록 → traceback 은폐. py측 `_log` 미러링으로 우회, main.js 자체 수정 미해결.
- **Seam batch exit-1 크래시:** batch가 파일마다 autotune 후 traceback 없이 exit 1 → "process ended without writing outputs" 팝업. **원인 미확정** — OOM(full-res 184MP × autotune 13×`correct()` × 다수파일)은 **가설**일 뿐, 종료코드 1+로그누락만으로 단정 불가. **이후 발생한 파일명 필터 오류(점 포함 파일명 등)와 반드시 구분**할 것. 선행 조치: `run_batch` 파일별 try/except + `_log` 미러링으로 **원인부터 관측**.
- **Signal branch sparse 리스크:** seam 채널별 opt-in 지원되나 `tissue_threshold=8`이 DAPI 밝기 가정 → non-DAPI sparse 신호 미검증.
- **Adjustment Viewer — 상태는 STATUS.md 기준(실파일):** *사용자 확인 완료*(당시 실행본): DAPI 표시·Options 스크롤·Atlas preview 개선, 브러시 기본 35, Use region color 기본 해제, Known-geometry 개선, **Seam Process 점(.) 포함 파일명 정상 처리**(= 해결됨, 미해결 아님). *검증 대기*: DAPI·annotation 분리 레이어 + 브러시 변경영역 최적화의 드래그 성능·저장 동일성, 두 번째 슬라이스 목록 누락, Ring thickness 버튼 배치, sync 스크립트 실제 `-Apply` 배포.

---

## 10. 역할 분담 (운영 선택 — 규칙은 위와 동일)

두 AI 산출물이 수렴하는 경향(같은 핸드오프 노트 → 유사 코드)이 있으므로, **한 AI 구현 / 다른 AI 리뷰·감사**가 병렬 구현보다 다양성 대비 효율적. 또는 서브시스템 분담(seam vs adjust). 어느 쪽이든 §4·§5 불변.

---

## 부록 A. git 초기화 (전용 소스, 사용자 PowerShell 1회)

```powershell
cd "D:\Claude\masonjar-7.0.2-MC.1-improving"
# .venv 와 대용량 정적 바이너리를 커밋에서 제외
Add-Content .gitignore "`n# dev venv / large static`n.venv/`n*.nrrd"
git init
git add -A
git status        # node_modules/out/.venv/logs 등 대용량이 목록에 없어야 함
git commit -m "[baseline] 7.0.2-MC.1-improving 소스 (기존 개선 포함)"
```

- 이 소스에는 `.gitmodules`(submodule)가 있다. `git status`에 submodule 경고가 뜨면 알려줄 것 — 로컬 추적 목적이면 해당 경로를 일반 파일로 추가하거나 별도 처리.
- 원격 없이 로컬 전용으로 충분(교대·롤백·diff 목적).
- 최초 1회 필요 시: `git config user.name "..."` / `git config user.email "..."`.

---

## 부록 B. 이 문서(AI_COLLAB_GUIDE.md)의 개정 절차

이 문서 자체도 git 추적 대상 소스이므로 §4·§5의 순차 교대·리뷰 원칙이 적용된다. 다만 협업 규칙(문서) 개정은 애플리케이션 코드 편집과 분리해 처리한다:

1. 한쪽이 변경을 제안하면(코드 리뷰든, 사용자를 통한 전달이든) STATUS.md 또는 이 문서 changelog에 "**<AI명> 재확인 대기**"로 표시하고 초안을 반영한다.
2. 다음 교대에서 상대 AI가 changelog를 읽고 동의하면 "재확인 대기" 문구를 제거, 이견이 있으면 STATUS.md에 근거와 함께 남기고 문서를 되돌리거나 수정한다.
3. 두 AI가 합의한 내용만 "정식(확정)"으로 표시한다. 한쪽만 반영한 규칙은 changelog에 대기 상태로 남겨 다른 쪽이 인지하게 한다.

---

*이 문서는 전용 소스 루트(`AI_COLLAB_GUIDE.md`)에 두고 git으로 함께 추적한다. 규칙 변경 시 커밋과 함께 갱신.*


