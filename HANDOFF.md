# 인수인계 — 형태 변형 로봇 가구 LLM 에이전트 (v4.5)

> 새 세션의 첫 컨텍스트. **먼저 `project_prompt_v4.5.md`를 읽어라** — 이 문서는 그 설계 문서를
> 대체하지 않고, "지금 어디까지 왔고 무엇을 조심해야 하는가"만 압축한다.

---

## 0. 반드시 지킬 사용자 규칙 (원문 그대로)

- **"도구 호출(tool call)을 최소화해줘"**
- **"이번 세션에서 이미 읽은 파일은 다시 읽지 마"**
- **"코드를 고치기 전에, 코드의 어떤 부분을 어떻게 고칠 것인지 나에게 채팅으로 말을 해줘.
  내가 그걸 읽고 승인을 하면 그제서야 코드들을 다 고치는거야."**
- **커밋은 사용자가 수동으로 한다.** 에이전트가 `git commit`하지 않는다.
- **발표자료·요약 슬라이드는 만들지 않는다.**

실제 코드 수정은 **Claude Code(별도 에이전트)**가 한다. 이 세션의 역할은 ① 설계·검증 ②
Claude Code에게 줄 지시문 작성 ③ 결과 독립 검증(샌드박스에서 코드를 **실제로 실행**해서).

> 읽어서 확인하지 말고 **돌려서 확인한다.** 이 프로젝트에서 발견된 결함 대부분은 코드를
> 읽어선 안 보였고 실행해서 수치를 찍어봐야 보였다 (§11 목록 참조).

---

## 1. 프로젝트 정체

- **BoT² 형태 변형 로봇 가구** (KIST) 를 자연어로 조작하는 HRI 연구 시스템.
- 로봇 2대(BOT 1, BOT 2). 각 로봇 = 40×40×50cm 본체 + 좌/우 패널 2장(각 30cm).
- 패널 각도는 `{0, 45, 90, 135, 180}` 5단계로 스냅. **돌출 = 30·sin θ** → `{0, 21.2, 30, 21.2, 0}`.
- 그래서 가능한 로봇 폭은 여섯 가지뿐: **40 / 61.2 / 70 / 82.4 / 91.2 / 100**.
- 회전 `rot`은 45° 스텝 8종.
- 연구 목적은 "잘 되는 시스템"이 아니라 **참가자의 평가 발화를 얻는 것**이다 (§15).
  시스템을 너무 매끄럽게 다듬으면 데이터가 줄어든다 — 이 긴장을 항상 의식할 것.

---

## 2. 구조 — 3층 + Phase A/B

```
발화
 └[의도층]  ask_intent   → intent JSON (intent_type, situation, activity, posture, number, space)
    │
    ├ confirm / revert  →  형태층 안 감 (결정론적 처리 후 종료)
    │
 └[기능층]  ask_function → 로봇 무관 「가구 요구 목록」 + feasible 판정 + motif 키
    │        ※ new_scene / add 일 때만 호출
 └[형태층]
     ├ Step 0  layout.space_summary()      코드 — 방의 용량만 요약
     ├ Phase A ask_form                    LLM — 형태(panels)·관계(relation)   ★좌표 없음
     ├ 코드     layout.enumerate_units()    코드 — 유효 후보/조합 열거 + 주석
     ├ Phase B ask_place                   LLM — 후보 목록에서 **인덱스 하나** 선택
     └ 코드     _execute → move/transform → HITL-2 → commit
```

**Phase A는 좌표를 모르고, Phase B는 형태를 못 바꾼다.** 이 분리가 스키마로 강제된다.

---

## 3. 절대 어기면 안 되는 설계 원칙

### 원칙 4 — LLM 제안 / 코드 보장
"코드가 후보를 주고 LLM은 고르기만 한다." **프롬프트로 부탁하지 말고 스키마로 강제한다.**
Phase A 스키마에 좌표 필드가 아예 없고, Phase B 출력은 정수 인덱스뿐이다.

### 원칙 5 — 카탈로그가 아닌 문법
코드는 **물리적 사실만** 기술한다. "이 형태는 독서용" 같은 용도 매핑을 코드에 넣지 않는다.
용도 판단은 LLM의 몫이고, 그것이 관측하려는 대상이다.

### 다양성 원칙 (★가장 자주 깨진 것 — 개발 중 5회 위반)
> **정렬된 목록을 앞에서 k개 자르면 원칙 4가 조용히 새어 나간다.**
> 다양성 축(자리 / rot / 로봇별 자리 / 배향)을 **먼저 정하고 라운드로빈한 뒤에** 자른다.

일반화: **공통 메커니즘은 「자르기」가 아니라 「좁히기」다.** 극단에서 후보가 하나로 수렴하면
원칙 4는 형식적으로 지켜지면서 실질적으로는 코드가 결정한다. 필터를 추가할 때마다
"이게 극단에서 후보를 1개로 만드나?"를 물어라.

### 단일 충돌 경로
모든 충돌 검사는 `collision.footprint_rects(state)`를 쓴다. **병합 사각형을 쓰지 않는다** —
좌우 패널 각도가 다르면 무게중심이 최대 10.6cm 밀린다.

### §6.5 순서쌍
`(left=a, right=b, rot=θ)` ≡ `(left=b, right=a, rot=θ+180)`.
**left/right는 독립 자유도가 아니라 rot의 180° 성분이다.** 이걸 잊고 "패널 좌우를 Phase A가
정해야 하나?"를 다시 논하지 말 것 — 이미 결론 났다.

### rot 규약 (헷갈리기 쉬움)
- `u = (cos rot, sin rot)` → **`panel_right`가 `+u`**, `panel_left`가 `-u` 방향.
- 가구의 **정면**은 `_front_vec(rot) = (-sin rot, cos rot)` — **패널 축과 다른 축이다.**
- 이 둘을 혼동해서 실제 결함이 났었다 (§11-6).

---

## 4. 파일 지도

| 파일 | 줄수 | 역할 |
|---|---|---|
| `project_prompt_v4.5.md` | ~735 | **설계 문서. 단일 진실 원천.** |
| `main.py` | 801 | 층 오케스트레이션, HITL, 라우팅, 롤백 |
| `agent.py` | 183 | **LLM 호출 4개뿐.** tool 없음 |
| `prompts.py` | — | INTENT/FUNCTION/FORM/PLACE 프롬프트 + 스키마 |
| `services/layout.py` | 762 | **격자 스캔 + 밴드 필터 + 후보/조합 열거** (v4.5 신설) |
| `services/collision.py` | 311 | footprint, OBB, 연결 접촉 판정 |
| `services/scene.py` | 178 | 상태·history·snapshot/restore·원자적 save |
| `services/placement.py` | 199 | 기하 헬퍼 (링 탐색은 삭제됨) |
| `services/eventlog.py` | 38 | append-only 이벤트, 단일 서버 시계 |
| `data/furniture_motifs.json` | — | **모티프 16개** (1대 9 / 2대 7) |
| `scripts/verify_form_layer.py` | 1097 | 회귀 스위트 ~62항목 |
| `scripts/probe_pair_motifs.py` | 135 | 모티프 실측 (읽기 전용) |

**삭제됨**: `tools/registry.py`, `project_prompt_v4.2.md`, placement의 링 탐색·격자 조사.

---

## 5. 모티프 라이브러리 참조 경로 (직전 대화 주제)

```
data/furniture_motifs.json   { _comment, motifs(16), modifiers, activities(9) }
   │
   │ main._load_motifs()           ← 파일 전체
   ▼
[기능층] ask_function(intent, room_furniture, motifs)      ★전체 라이브러리를 봄
   │        activities로 활동→후보 좁히기, description·capacity로 적합성 판단
   ▼
   출력: {"item":"종이접기 작업대", "count":1, "motif":"large_table", "feasible":true}
   │
   │ main.py 무결성 교정 — motifs["motifs"]에 없는 키면 null로 강등 (환각 방어)
   ▼
intent["furniture"] = [ {item, count, motif}, ... ]        ★키만 실림
   │
   │ main._referenced_motifs(intent)  ← 실제로 참조된 motif의 **상세만** 추출 (보통 1~2개)
   ▼
[형태층 Phase A] ask_form(..., motifs=참조된 상세)          ★고른 것만 봄
   │        panels를 기본형으로, why를 근거로, 2대면 arrangement
   ▼
[형태층 Phase B] ask_place(...)                            ★motif를 아예 안 봄
```

**핵심: 기능층이 고르고, 형태층은 고른 것만 본다.** intent는 **키**를 나르고, **상세는 별도
필드**(`"가구 참고표(motifs)"`)로 프롬프트에 들어간다 — 상세는 상태가 아니라 프롬프트 입력이라서.

`modify` / `remove`는 기능층을 안 거치므로 motifs를 안 받는다 (`ask_form`의 조건이
`if it in ("new_scene","add")`). 다만 `_referenced_motifs`는 무조건 계산돼 버려진다 — 사소한 낭비.

---

## 6. 지금까지 한 일 (v4.4 → v4.5)

1. **`find_placement`의 앵커 링 탐색(15° 스텝) 폐기** → 격자 스캔 + 앵커 밴드 필터로 통일.
   결함 6개가 한꺼번에 해소됨.
2. **형태층을 Phase A / Phase B로 분리.** tool 루프 제거 → 구조화 출력 2회.
3. **롤백 도입** — 승인 아닌 모든 종료 경로에서 baseline으로 복원. `_run_form_layer`.
4. **원자적 세션 저장** (`.tmp` → `os.replace` + `fsync`).
5. **`pair` 연결 검증 구멍 메움** — 예전엔 100cm 떨어진 로봇도 "연결됨"으로 통과했다.
6. **모티프 카탈로그 실측 정리** — `l_shaped_table` 제거(직각 미지원), `arrangement`를 `face`로 통일.
7. **`eventlog` 신설** — 단일 서버 시계, append-only.
8. **회귀 스위트 62항목**으로 위 결정들을 고정.

---

## 7. 지금 남은 작업

### A. 지시문은 썼는데 아직 실행 안 됨 — `CLAUDE_CODE_motif_마무리.md`
1. **`study_carrel` 제거** (아직 파일에 살아 있음 — 확인함). `l_shaped_table`과 달리 **복원
   후보가 아니다.** §12 「능력 경계」 목록에 **「패널 축 고정」**을 추가하고 두 모티프를 근거로 달 것.
2. **capacity를 인원이 아니라 로봇 대수 기준으로** 노출. `large_worktable`의 "3인 이상" 게이트 제거.
3. `activities.공작.note` 범위 확대.
4. `scripts/preview_motif.py` — 스크린샷 자동화는 하지 않는다(사용자가 직접 캡처).

### B. 식별됐지만 지시문 미작성
- `_validate_form`이 connection 대상을 **units 기준으로** 검사해야 한다 (지금은 robots 기준 →
  store 대상 연결이 조용히 `[]`를 낳고 재호출 예산만 태움).
- `_PAIR_FACE = 122.4` 주석이 `connection_touching` 일반화 이후 낡음 (최소 face는 80×40).
- `_fits_rect`는 로봇을 장애물에서 빼는데 span 프로브는 포함한다 — 비일관.
- `_enumerate_with_fallback`이 `dropped_connection`을 로깅하지 않음.
- `layout.BAND_MAX` 전역 변이 (Phase 3-2).
- docstring에서 「형태층」이 상위 층과 Phase A 둘 다를 가리켜 혼란.

### C. 🔴 평가 층 보류에도 **이것만은 해야 함**
```python
if excluded:
    eventlog.record("func_infeasible", utterance=text, items=excluded)
```
기능층의 `feasible=false` **사유(reason)는 LLM 출력이라 나중에 재현 불가**다. 지금
`main.py:643-656`이 개수만 세고 내용은 `print`로 버린다. **소급 계산 불가 목록 중 유일한 구멍.**

### D. 그 다음
수동 커밋 → `git tag v4.5-pilot` → **4인 S3 3회** → **B-2 (속도)**.

---

## 8. 평가 층(§17)은 보류다 — 사용자 결정

**구현하지 않는다.** 관련 파일(`services/metrics.py`, `detectors.py`, `eval/*`)은 **하나도
존재한 적이 없고**, 만들지 않기로 했다. §14 TODO 10~14번이 취소선 처리됨.

**보류가 가능한 이유**: §17.1이 트랙을 「실시간 / 사후」로 나눠뒀고, 10·11·13·14는 전부
**사후 트랙**이다. history가 절대 스냅샷이라 데이터 수집 후 분석 스크립트로 돌리면 된다(§17.5).
§15.4의 주 종속변수는 어차피 **발화에서 코더가 뽑는다** (§15.8: "novel 판정은 user_verbatim만으로").

**단, 실시간이어야 하는 것 하나가 파급을 남긴다** — `eval/probe.py`(실시간 프로빙).
없으면 §15.3의 **C3 정의**가 바뀌고 **C3 내부 비교(프로빙 vs 인터뷰)가 성립 안 한다.**
→ **§18 [미정] 20번**에 선택지 (a)/(b)/(c)로 기록됨. **파일럿 후에 정할 것.**
(b)—프로빙 문구만 고정 세트로 남기고 탐지는 연구자가 실시간으로—가 코드 0줄이라 가장 싸다.

---

## 9. 파일럿에서 확인할 열린 관찰

- `ref_dist ≥ 100`인 후보를 Phase B가 무슨 근거로 고르는가
- `place_face_away` 카운트 — **0이면 §13의 "VLM 불필요" 주장을 지지**
- facing에서 `min_apart: 40` 출현 빈도 (§18-16에 이미 관측 기록)
- `place_regen_leak > 0`인데 폴백 이벤트가 없으면 → 정규식 오탐
- `free_fallback` / `band_expand`가 한 번도 안 터짐 → **예측 기반 수정 2개가 미정산**
- `[90,90] facing`이 후보 3개뿐 — **가장 흔한 형태가 가장 빡빡하다**

**속도 (미해결, B-2)**
```
Turn 1  LLM 129.1s / turn 251.3s ;  place 27.4s, 38.2s
Turn 2  LLM  83.3s / turn 281.1s ;  place 18.4s ; HITL-2 대기 154s
```
`place`가 일관되게 가장 느린 층. **사람의 대기 시간은 낭비가 아니라 데이터다** (§15.4 IF).

---

## 10. VLM 질문 — 결론: 지금은 넣지 않는다

관측된 결함 5개 중 VLM이 잡았을 것은 1개뿐이고 그건 이미 고쳐졌다. 게다가 ① 지연이 이미
최대 문제이고 ② 서버측 렌더러가 없으며 ③ §15.1의 기여 프레이밍상 시스템을 다듬을수록
데이터인 참가자 발화가 줄어든다.

---

## 11. ★ 함정 — 이전 세션이 실제로 틀렸던 것들

**절대 반복하지 말 것:**

1. **"`panels_touching`을 하나로 일반화하라"** → 틀렸다. Claude Code가 `connection_touching`
   (public) + `_panel_pair_touching`(private)로 **쪼갠 게 맞다.** 후자는 `validate_layout`이
   **연결된 패널쌍을 충돌 검사에서 제외**하는 데 쓰는데, 일반화하면 깊이 겹친 패널이 통과한다.
2. **"0°·180°면 닿을 면이 없어 연결이 안 된다"** → 틀렸다. `panel_rect`의 `None` 반환을 물리적
   사실로 오독한 것. **본체-본체 접촉(gap 0)은 유효한 연결이다** (`find_connect`가 dist=40으로 처리).
3. **「코너」를 기하학적 직각으로 읽음** → 틀렸다. `parent_child_corner`의 코너는 **공간/영역**
   (독서 코너)이다. 데이터의 `use`에 "공유 공간"이라 적혀 있다.
4. **"벽 기준이면 free 제외가 필요 없다"** → 틀렸다. 벽을 향해 180° 스크린을 편 작업대는
   `front_faces_ref: false`가 **맞다**. facing/alongside 제한을 유지할 것.
5. **`_feasible`에 connections를 넘기라고 지시** → 구조적으로 도달 불가. `enumerate_units`가
   먼저 `_connected_combos`로 분기한다.

**사용자가 방향을 바꾼 지점:**
- V자 골은 `bookshelf`에 못 박아둔 것 — 다른 모티프에 퍼뜨리지 말 것.
- `parent_child_corner` 문구는 손대지 않는다.
- free 모드 기준은 "북쪽 가정"이 아니라 **"가구와 가장 가까운 벽"**으로 결정됨.

---

## 12. 첫 턴에 할 일

1. `project_prompt_v4.5.md` 읽기 (§6 형태층 파이프라인, §14 TODO, §18 미정 목록 우선).
2. 사용자에게 **무엇부터 할지 묻지 말고**, 위 §7-A(모티프 마무리)가 아직 실행 안 됐음을 알리고
   진행 여부를 확인.
3. 코드를 고치기 전에 **반드시 채팅으로 수정안을 먼저 설명하고 승인받을 것.**
