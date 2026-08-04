# 로봇 가구 LLM Agent 프로젝트 — 컨텍스트 프롬프트 (v4.2)

너는 이 프로젝트의 개발을 돕는 조수야. 아래는 이미 확정된 설계 내용이니, 이를 전제로 대화를 이어가.

> v3 → v4 주요 변경: **VLM 시각 자가검증(critic) 전면 제거**, **HITL-1 실제 블로킹 승인 구현**, **HITL-2 승인 시 코드가 자동 commit**, **revert를 main.py에서 결정론적으로 처리(의도층이 revert_to_turn 지정)**, **되묻기(clarification)를 형태층 tool에서 의도층+HITL 앞단으로 이관(tool 13→12개)**, **store_robot no-op 가드**, **check_feasibility issues에 수정 힌트**, **find_placement에 panel_toward_anchor 추가**, **시작 시 resume 제거(재시작=도크 초기화)**, **뷰어 중복 말풍선·GLB 스왑 race 수정**, **render.py 제거**.
>
> v4.1 패치: **find_placement에 connect 모드**(두 대 조합의 정밀 연결 좌표를 코드가 계산 — LLM 삼각함수 금지), **transform/move 직후 자동 validate_layout**(issues+fix 힌트를 결과에 실어 반환), **연결 자동 감지**(맞닿음 조건을 만족하는 패널 쌍은 충돌에서 제외), **revert 대상 = '현재 상태와 다른' 가장 최근 커밋**, **commit_if_changed가 (entry, changed) 반환**, **HITL 대기 중 재접속 시 pending 요청 재전송**(req_id로 중복 방지), **되묻기 최대 2회 후 LLM이 잔여 정보 추론해 진행**, **handle() 예외 격리**, **get_recent_context 슬림화**, **GLB 스왑 후 inactive dim 재적용**, **stt.py에서 콘솔·Windows 경로 제거**, **tests/ 제거**, **panel_away_from_anchor 추가**, **move_robot 반환에 panel_orientation**, **형태층 마무리 발화 채팅 미표시**, **성인/아이 구분 제거**, **기능층 독립(ask_function)**, **scenes 가구에 능력 description 추가**, **V자 골·∧자 지붕을 각도 목록의 고정 케이스로 명시**.
>
> **v4.2 코드 패치 (리팩터링 + 로봇 모델 교체): (1) 로봇 GLB를 패널 각도 조합별 25개 파일(5×5, 약 2.3GB) 스왑 방식에서 `robot_animated.glb` 단일 파일(DRACO 압축, 약 400KB)로 교체 — 패널은 피벗 노드(`left/right_wing_pivot_anim`)를 직접 회전시켜 구동하며, 이동·회전이 끝난 뒤에야 패널이 움직인다. (2) 뷰어 GPU 리소스 누수 수정(scene_change 시 이전 방 dispose, 로봇당 재질 1개 공유). (3) `registry.HANDLERS`를 TOOLS 스키마에서 자동 생성 — 스키마에만 있고 함수가 없으면 import 시점에 실패. (4) `tools.scene()` 헬퍼 도입으로 `_scene` 중복 정의 제거, 미사용 `STATE["client"]` 제거(`init(scene_state, viewer)`). (5) 커밋 로직을 `commit_layout` 한 벌로 통합(`viewer_tools._commit_on_approval`이 재사용), `_do_revert`도 `context_tools.revert_to` 재사용으로 통합. (6) `handle()`의 `_depth` 재귀를 루프로 전환(재시도 한도·metrics 동작 동일). (7) baseline 수동 조작 UI를 본 뷰어·main에서 제거 — 별도 모듈 분리 완료.**
>
> **v4.2 변경 (논문/실험 방향 확정 + baseline 분리): (1) 논문 방향을 "LLM 시뮬레이션의 기술 완성도 → HRI 사용자 연구"의 VoicePilot식 구조로 확정(15절). (2) 핵심 비교는 세 조작 방식 — 수동 / 발화만(LLM 배치·수정) / 혼합(LLM 배치+사용자 UI 수정) — 을 자율성(autonomy) 독립변수로 두고, 인지부하(NASA-TLX)와 신뢰·통제감을 별개 종속변수로 측정(Shared Control 논문의 "부하·신뢰는 독립적으로 움직인다" 프레임 차용). (3) baseline(수동 조작)을 `--baseline` 플래그가 아니라 완전 별도 폴더/모듈로 분리 — LLM·에이전트·채팅·STT 전부 제거, 순수 사람 조작 + 로그 수집만. (4) baseline 조작 방식 확정: 이동=드래그, 회전=버튼(45° 스텝), 패널=5단계 버튼(로봇 전용). (5) baseline은 참가자가 기존 가구까지 직접 배치(아이템 패널에서 소환) — 로봇 2대 + 가구 asset. (6) 이벤트 궤적 전체를 로깅. 자세한 사항은 15·16절.**

## 1. 프로젝트 개요

사용자의 **음성 발화**를 듣고, 변신 로봇 가구(shape-shifting robot furniture)를 알맞은 형태·위치로 구성해주는 LLM agent 시스템.
목적: 사용자가 "친구랑 밥 먹을 거야"라고 말하면 시스템이 상황을 파악해 로봇들을 알맞게 변형·배치하고, 사용자 피드백으로 수정하는 것.

- 기반 코드: STT(브라우저 push-to-talk + Groq Whisper) → OpenAI LLM 의도분석 → 로봇 명령 JSON 출력 (structured outputs, strict json_schema)
- **로봇은 BoT²로 확정** (KIST, 모듈형 로봇 가구. 단일 기종 2대: BOT 1, BOT 2). 상세 스펙은 12절.
- 사용자는 성인/아이를 구분하지 않는다 (v4.1: user_composition 제거 — 구분 없이 동작하며, 성인 가정을 프롬프트에 명시하지도 않는다).
- **음성 입력은 브라우저 push-to-talk**: 채팅창 🎤 버튼 또는 스페이스바를 누른 채 말하기 → MediaRecorder → POST /stt → Groq Whisper. 콘솔·Windows(ctypes) 의존 제거. ROS2 변환은 사용자가 명시적으로 "ROS로 바꿔줘"라고 할 때만 진행.
- **배치의 '조화' 판단은 LLM 자가 점검 + HITL-2 몫**이다. (v3의 VLM 시각 자가검증은 도구 호출 과다로 제거됨 — 13절 참고.)

## 2. 핵심 설계 원칙 (차별점)

1. **3층 변환 구조**: 발화 → [의도층] intent → [기능층] 로봇 무관 가구 요구 목록 → [형태층] 로봇 구성(형태·위치). **v4.1부터 기능층은 별도 LLM 호출(ask_function)로 독립**: 의도층은 가구 초안만 내고, 기능층이 기존 가구 description을 근거로 중복·보완을 판단하고 각 항목의 구현 가능성(feasible)을 판정한다. HITL-1 승인 후, intent_type이 new_scene/add일 때만 실행.
2. **2중 human-in-the-loop**: HITL-1 언어 게이트(의도 확인 — **분석된 의도를 사용자에게 블로킹 승인받은 뒤에야 형태층 진행**), HITL-2 공간 게이트(3D 뷰어로 배치 확인 — **승인 즉시 코드가 스냅샷을 자동 commit**). 두 게이트의 통과율·수정 턴 수가 실험 지표.
3. **상태 편집 + 버전 복원**: 대화는 버전된 history를 만들고, LLM은 현재 상태의 편집기. "원래대로"는 재생성이 아니라 저장된 state의 결정론적 **복원**.
4. **LLM 제안 / 코드 보장**: LLM은 배치를 제안만 하고, 충돌·경계·연결 기하 검증은 결정론적 코드 레이어가 최종 책임. **좌표·패널 방향 선택도 코드가 계산해 후보로 주고(find_placement) LLM은 고르기만 한다.**
5. **카탈로그가 아닌 문법**: 프롬프트(ROBOT_MECHANISM)는 물리적 사실만 서술. 가구 이름·용도 매핑은 furniture_motifs.json에 reference로만 둠 → LLM이 미등록 형태(발받침대 등)도 물리 스펙 안에서 자유 생성.
6. 환경은 scene JSON으로 표현 (고정 실험 제약 없음).

## 3. 의도 스키마 (의도층 출력, structured outputs strict)

```json
{
  "number": 2,              // nullable. 인원 단서가 전혀 없으면 반드시 null. null이면 needs_clarification 트리거
  "situation": "친구와 식사하려는 상황",
  "activity": "식사",
  "space": "kitchen",       // living_room/bedroom/kitchen/bathroom/balcony/unknown. 애매하면 unknown → needs_clarification
  "furniture": [{"item": "식탁", "count": 1}, {"item": "의자", "count": 2}],  // 로봇 수 고려 없이 나열한 '초안' — 확정은 기능층(ask_function)
  "posture": "sitting",     // standing/sitting/lying. 조정성 발화면 직전 값 유지
  "intent_type": "new_scene",  // confirm / modify / add / remove / revert / new_scene
  "revert_to_turn": null,   // nullable int. revert일 때만, 되돌릴 대상 turn 번호
  "needs_clarification": false,  // 정보 부족·해석 애매하면 true
  "clarification_question": null, // needs_clarification true면 물을 한 문장
  "confirmation_message": "두 분이 함께 식사하시는군요, 준비를 시작할게요."  // HITL-1용. 가구 이름 언급 금지
}
```

**기능층(ask_function, v4.1)**: HITL-1 승인 후 new_scene/add에서 실행. 입력 = intent(초안) + 방 기존 가구(description) + motifs + ROBOT_MECHANISM. 출력 = `{"furniture": [{item, count, motif, feasible, reason}], "complement_note": str|null}`. motif 키는 코드가 라이브러리와 대조해 참조 무결성 보장. feasible 항목이 intent["furniture"]로 교체되고, 보완 이유만 형태층에 전달돼 HITL-2 메시지에서 고지. 구현 불가 항목은 형태층에 안 넘김(콘솔 로그만). 전부 불가면 형태층 스킵하고 바로 안내.

## 4. intent_type 라우팅 (배치 후 사용자 반응 처리)

- confirm → 스냅샷 확정. **HITL-2 승인 시 이미 자동 commit되므로, 변화 있을 때만 새로 commit**(commit_if_changed).
- modify/add/remove → 형태층만 재실행. **최소 편집**: 요청 무관 로봇은 유지, 놀고 있는 자원부터 활용.
- revert → **main.py에서 결정론 처리**. `revert_to_turn`으로 `revert_to(turn)`만 호출, 형태층 스킵. fallback: 대상 null/무변화면 '현재와 다른 가장 최근 커밋' 선택.
- new_scene → 의도층부터 전체 재실행. space 바뀌면 방 전환. **새 구성에 안 쓰이는 active 로봇만 store_robot으로 정리**.

**되묻기는 HITL 앞단에서 처리**: needs_clarification=true면 main이 HITL-1 전에 되묻고(최대 2회) 답을 발화에 보태 재분석. 2회 후 미해소면 '(되묻기 한도 도달)' 붙여 1회 재분석.

## 5. history 형식

```python
history = [
  {"turn": 1, "intent_type": "new_scene", "space": "living_room",
   "utterance": "테이블 만들어줘", "description": "BOT 1 테이블 배치",
   "state": {로봇 전체의 절대 상태}},
]
```

- turn은 전역 번호, state는 항상 전체 로봇의 절대 스냅샷(diff 아님) → 단독 복원 가능.
- `commit_if_changed`: 직전 커밋 이후 변화 없으면 재커밋 안 함. 반환은 `(entry, changed)`.
- intent_type new_scene 항목이 상황 경계. space도 기록. commit마다 logs/session.json 저장.

## 6. Tool 목록 (11개, 전부 tools/ 폴더에 정의)

### placement_tools.py (6)
- `transform_robot(robot, panel_left, panel_right, furniture)` — 로봇 변형. 두 패널 각도(0/45/90/135/180)로만. 실행 직후 자동 push_state + 자동 validate_layout(issues+fix 힌트).
- `move_robot(robot, x, y, rot)` — 이동 + 회전. 실행 직후 자동 검증. 반환에 `panel_orientation`(실제 rot 기준 앵커별 toward/away 확정값).
- `store_robot(robot)` — 홈 도크 복귀 + 초기화. 이미 inactive면 no-op.
- `check_feasibility(robots, connections)` — 물리 검증 + 연결 기하. 반환 `{"feasible", "issues"}`, 각 issue에 fix 힌트. 조화 판단은 안 함.
- `find_placement(footprint_radius, near?, avoid[], connect?, ...)` — 유효 후보 좌표 제안. tag·clearance·rot_suggest·panel_toward_anchor·panel_away_from_anchor 부여. connect 모드(near=앵커 로봇): 두 대 조합 정밀 연결 좌표를 코드가 계산.
- `furniture_mapping(activity)` — 활동→가구 조합 참고표. 강제 아닌 reference.

### context_tools.py (4 — LLM 노출 기준)
- `robot_states()` / `get_environment()` / `get_recent_context(n)`(슬림 반환) / `revert_to(version)`(슬림 반환).
- `commit_layout(description)`은 **tool 스키마에서 제외됨** — HITL-2 승인 시 코드가 자동 commit하므로 LLM이 부를 이유가 없다. 함수 자체는 남아 있고, `viewer_tools._commit_on_approval`과 형태층 자동 확정이 이 한 벌을 재사용한다(커밋 로직 단일화).

### viewer_tools.py (1)
- `ask_user(message)` — HITL-2 게이트. 승인 시 코드가 자동 commit. 메시지는 사람 언어 2~3문장, 내부 용어·수치 금지, complement_note 있으면 이유 고지.

주의: 뷰어 갱신은 tool 아님(상태 변경 직후 코드가 자동 push_state). 되묻기는 tool 아니라 의도층 신호(needs_clarification)로 main이 HITL 앞단 처리.

## 7. tools/ vs services/ 구분 원칙

- **tools/** = LLM에게 보이는 껍데기(함수+스키마). 내용은 services 호출 한두 줄.
- **services/** = 순수 계산·상태. LLM 없이 테스트 가능. 여러 tool이 공유.
- LLM 호출 가능한 건 registry.TOOLS의 11개뿐. agent.py는 중계자(tool_call → HANDLERS 실행).
- `HANDLERS`는 수동 dict가 아니라 **TOOLS에서 자동 생성**한다(이름으로 placement/context/viewer_tools를 훑음). 스키마에 이름을 추가하고 함수를 빠뜨리면 import 시점에 원인이 명시된 에러로 즉시 드러난다.
- 매핑: robot_states→states / get_environment→environment()+furniture() / transform_robot→transform / move_robot→move / store_robot→store(no-op 가드는 tool 층) / check_feasibility→validate_layout+panels_touching / find_placement→placement.find_placement.

## 8. 프로젝트 구조

```
project/
├── main.py                # 조립 + 단일 발화 루프 + 라우팅. HITL-1 블로킹 + 되묻기 + 기능층 호출 + revert 결정론. 시작 시 resume 안 함
├── agent.py               # ask_intent(의도층) + ask_function(기능층) + run_agent(형태층 tool 루프, 중계만)
├── config.py              # API 키 + 모델명 + 로봇 물리 상수 (단일 출처)
├── prompts.py             # ROBOT_MECHANISM + INTENT_PROMPT + INTENT_SCHEMA(12필드) + AGENT_PROMPT
├── data/furniture_motifs.json   # motif + modifiers + 활동표 (reference)
├── scenes/                # living_room/bedroom/kitchen/bathroom/balcony.json
├── tools/                 # registry.py(HANDLERS 자동 생성) + __init__.py(공유 STATE + scene() + push_state) + placement_tools.py + context_tools.py + viewer_tools.py
├── services/              # placement.py + collision.py + scene.py + stt.py  (render.py·tests/ 제거됨)
├── viewer/                # popup_viewer.py(FastAPI+WS+POST /stt) + static/index.html + static/viewer.js
└── models/robot_animated.glb (DRACO 압축 단일 모델, 약 400KB) + 가구 GLB(선택)
```

의존 방향(한 방향): main → agent → tools → services, tools → viewer(push만). services는 tools를 모름. viewer는 그리기만.

## 9. 뷰어 아키텍처

- 파이썬(두뇌) ↔ 브라우저 three.js(화면)는 WebSocket. 파이썬 push, 브라우저는 받은 대로 그리고 애니메이션(rot은 최단 경로).
- 메시지: state_update / scene_change / chat / message / approval_request / clarify_request / user_feedback / clarify_answer / user_utterance.
- 중복 말풍선 방지: approval/clarify 문구는 그쪽이 말풍선을 그리므로 chat으로 또 안 보냄.
- 재접속 시 현재 scene+state 스냅샷 push(F5 복구). HITL 대기 중이면 pending 요청도 재전송(req_id 중복 방지). 재시작은 도크에서 새로.
- 좌표계: scene (x,y) cm. three.js X=x−w/2, Z=d/2−y, rotation.y=rad(rot). collision.py 규약과 일치(검증됨).
- 로봇 에셋: **`robot_animated.glb` 단일 모델**(본체 + 좌우 날개가 피벗 노드로 분리, DRACO 압축). 패널 각도는 파일 스왑이 아니라 피벗 노드 `rotation.x`를 보간해 만든다 → 25개 조합에 갇히지 않고 연속값 표현 가능. 로드 실패 시 조립식 fallback 유지(안전망 동일). 가구: model 지정 시 GLB, 아니면 조립식 fallback. 충돌은 항상 JSON w,d 사각형 기준.
  - 주의: 모델 노드 명명이 방 규약과 반대다 — 방 왼쪽(−z) 패널의 피벗이 `right_wing_pivot_anim`.
  - DRACO 디코더는 three와 같은 CDN에서 런타임 로드한다(`extensionsRequired`이므로 디코더 없으면 파싱 자체가 실패). 오프라인 구동이 필요하면 three·디코더를 함께 `static/`으로 내려야 한다.
  - **패널 동작 타이밍**: 이동·회전이 목표에 닿은 뒤에야 패널 보간이 시작된다. 이전 스왑 방식은 명령 수신 즉시 최종 각도 파일로 점프해 주행 중 패널이 먼저 펴져 있었다 — 즉 이번 교체 전까지 GLB 로봇에는 패널 애니메이션이 사실상 없었다.

## 10. 다중 방(scene) 전환

- 방마다 scenes/<space>.json 하나(5개). 방 크기: living_room 400×400 / kitchen 400×250 / bedroom 300×300 / bathroom 200×200 / balcony 400×100.
- 도크: scene JSON "dock" 필드. 없으면 config.home_for() 자동.
- 기존 가구(pre_existing_furniture): `{"id","label","x","y","w","d","rot","corners","model"?}`.
- **기존 가구는 장애물이자 배치 앵커**. 원칙: "중복하지 말되 반드시 기여하라" — 중복 판단은 이름이 아니라 **적합성**(높이·크기·전용 면적으로 실제 충족할 때만 목록에서 뺌).
- 의도층이 space 추론, 애매하면 unknown → needs_clarification. 전환: load_scene → 장애물 교체 → scene_change push → buildRoom. 방 전환은 new_scene 특수 케이스.

## 11. 구현 순서 및 현황

1. ~~의도층 파이프라인~~ ✅  2. ~~services 층~~ ✅  3. ~~tools/ + run_agent~~ ✅
4. ~~시각 자가검증(VLM critic)~~ — 제거됨(13절)
5. ~~viewer/~~ ✅  6. ~~기능층 독립(ask_function)~~ ✅ (v4.1)
7. ~~baseline 별도 모듈 분리~~ ✅ — 본 프로젝트에서 `--baseline` 플래그·수동 조작 UI·`manual_command` 경로 제거 완료.
8. ~~로봇 모델 파이프라인 교체~~ ✅ — 단일 DRACO 모델 + 피벗 직접 구동(9절).
9. **남은 작업은 14절 TODO 참조** — 로깅 확장, find_placement 앵커 탐색 개선.

## 12. 로봇 확정 스펙 (BoT²)

### 물리 스펙
- **구성**: 동일 기종 2대(BOT 1, BOT 2). 본체 크기 불변(size 개념 없음).
- **본체**: 40×40×50 cm. 윗면 평평(40×40). 바퀴 이동 + 제자리 회전. 방향성 있음 → rot 필수. 높이 50cm 고정.
- **가동 패널 2개**: 마주보는 두 측면 윗모서리 힌지, 40(폭)×30(길이) cm. 각각 0/45/90/135/180° 5단계:
  - 0°=닫힘 / 45°=\ 아래 / 90°=수평(상판 30cm 확장) / 135°=/ 위 / 180°=수직(꼭대기 80cm)
- 수납: 패널 열면 서랍장. 고정 패널 2개: 나머지 두 측면(모듈 부착부, 배치 범위 밖).
- 조합: 최대 2대. 나란히(rot 동일) / 마주보고 패널 맞대기(rot 차 180°).

### state 스키마
```json
{"robot": "BOT 1", "active": "active", "x": 180, "y": 140, "rot": 90,
 "panel_left": 90, "panel_right": 0}   // panel 값은 {0,45,90,135,180}만. inactive도 도크 바닥 차지 → 충돌 포함
```

### collision.py (구현 완료)
- OBB(SAT) 충돌. footprint = 본체 40×40 + 돌출 패널. 패널 바닥 돌출 = 30×sin(각도). 1대 풀 확장 상판 40×100cm.
- slack 2cm(침투 2cm 이하는 충돌 아님). panels_touching(끝 맞닿음 + 폭 겹침 ≥30). clamp_to_bounds, place_without_overlap, validate_layout(issues에 fix 힌트, 맞닿음 패널 쌍은 연결로 자동 감지해 충돌 제외), snap_panel.
- rot 규약: 도 CCW. rot=0일 때 panel_left=−x쪽, panel_right=+x쪽. 뷰어와 일치 확인됨.

### services/scene.py — SceneState (구현 완료)
- 조회 environment()/furniture()/states()/recent(n). 편집 transform(snap)/move(x,y,rot)/store. transform/move 직후 자동 clamp.
- 버전 commit(전역 turn++, 절대 스냅샷, session.json) / commit_if_changed / revert_to. resume()은 존재하나 시작 시 호출 안 함.

## 13. 시각 자가검증 (VLM critic) — **제거됨**

v3의 VLM 시각 자가검증(렌더 → 비전 모델 질의 → 피드백 루프)은 제거. 이유: 턴마다 렌더+VLM 호출이 도구 호출·지연 과다, HITL-2가 조화 확인 담당. 대체: (1) AGENT_PROMPT의 실행 전 자가 점검, (2) HITL-2 승인. 제거 항목: render.py, CRITIC_PROMPT/SCHEMA, config.VISUAL_CHECK, viewer_tools._critic_check 등.

## 14. TODO (우선순위 — 갱신)

### (1) Baseline 모드 — **별도 모듈로 분리 ✅ (본 프로젝트 쪽 정리 완료)**

- **목적**: (a) 실험 B의 수동 조작 조건(비교군), (b) 실험 A의 사람 배치 데이터 수집 도구 — 두 역할을 동시에 한다.
- **분리 원칙**: 기존 `--baseline` 플래그 방식을 폐기하고, **완전히 별도 폴더/모듈**(예: `baseline/`)로 뺀다. LLM·에이전트(agent.py)·채팅·STT·기능층/의도층 전부 **제외**. 순수하게 "사람이 조작 → 로그 수집"만 남긴다.
- **본 프로젝트 쪽 제거 완료**: `main.py`의 `--baseline` 플래그와 `baseline_loop`, `PopupViewer(baseline=…)`·`manual_q`·`manual_command` 수신, `index.html`의 조작 패널 CSS/마크업, `viewer.js`의 선택 링·`sendManual`·`refreshManualUI`·조작 버튼 바인딩이 모두 빠졌다. 이후 baseline 모듈은 독립 폴더에서 진행한다.
- **물리 보장은 공유**: collision(OBB/SAT), 경계 clamp, 패널 snap 등 결정론 물리 레이어는 본 프로젝트에서 **그대로 이식**한다. 사람이 조작해도 벽을 못 뚫고, 겹침이 보정되어야 나중에 세 조건 비교가 공정하다.
- **씬**: 본 시뮬레이션과 동일한 방들(거실/침실/부엌/화장실/발코니). 참가자가 **기존 가구까지 직접 배치**하는 방식으로 확정(아이템 패널에서 소환). 로봇 2대 + 가구 asset이 아이템 패널(원래 채팅 자리)에 놓인다.
  - ⚠ 참고: 참가자가 기존 가구를 매번 다르게 놓으면 세션 간 방이 달라져 로봇 배치 비교의 통제가 약해진다. 데이터 분석 시 이 점을 감안하거나, 필요하면 후속 조건에서 가구 고정 버전을 별도로 둘 수 있다. (현재 결정: 참가자 배치)
- **조작 방식 (드래그+버튼 혼합, 확정)**:
  - 이동: 로봇/가구를 마우스 드래그. (격자 스냅은 옵션)
  - 회전: 선택 시 뜨는 회전 버튼(↺↻), 45° 스텝.
  - 패널(로봇 전용): 선택된 로봇에 좌/우 패널 각각 5단계 버튼(0/45/90/135/180). 가구엔 이 UI가 뜨지 않는다.
  - 선택/소환/제거: 클릭 선택, 아이템 패널에서 꺼내기, X로 제거/도크.
- **완료(commit)**: '완료' 버튼 = 현재 배치를 최종 스냅샷으로 확정 + 로그 flush.
- **구현 위치**: 사용자가 새 채팅에서 폴더(`Downloads\baseline` 등)를 연결해 진행 예정. 파일 구조는 16절.

### (2) session.json / metrics 로깅 필드 확장 — 16절의 로그 스키마로 통합.

### (3) find_placement 앵커 링 탐색(15°)의 사각지대
- 문제: 앵커 둘레 한 겹 링을 15°로만 훑음 → 앵커가 벽에 붙으면 후보 급감, 링 바깥 빈 공간 못 봄, 좁은 틈 건너뜀.
- 해결(택1/조합): ① 다중 링(+20cm씩 재탐색) ② 조사 모드 폴백(후보 0개면 방 전체 조사, 앵커 거리 정렬) ③ 각도 세분화(부족 시 15°→7.5°). 전부 services/placement 내부 완결 — 스키마·프롬프트 무변경.

## 15. 논문 / 실험 방향 (v4.2 신규)

### 15.1 큰 틀
- **구조**: VoicePilot(UIST'24, CMU) 형식 차용 — 먼저 LLM 시뮬레이션의 **기술 완성도**를 제시하고(System), 그 위에 **HRI 사용자 연구**를 얹는다(User Study → Design Guidelines). 시스템은 기여의 도구, 발견·가이드라인이 주 기여.
- **핵심 비교 (실험 B)**: 같은 태스크(가구명 없는 활동 프롬프트, 예: "거실에서 친구와 차를 마실 거야")를 세 조작 방식으로 수행·비교.
  - 수동: 사용자가 UI로 직접 조작 (baseline, 자율성 낮음)
  - 발화만: 사용자는 말만, LLM이 배치·수정 (자율성 높음)
  - 혼합: LLM이 초안 배치 → 사용자가 UI로 수정 (자율성 중간)
- **자율성(autonomy)을 독립변수**로 본다(수동→혼합→발화 스펙트럼). Shared Control 논문(자율성 γ)의 실험 프레임 차용 — γ 연속 블렌딩 수식은 이산 편집 도메인엔 직접 적용 불가하므로 "결정권 분담"의 세 지점으로 구현.

### 15.2 측정 (Steinfeld 2006 프레임 + Shared Control 프레임)
- 객관(효율): 완료 시간(시스템/사람 분해), 조작·발화·수정 횟수, operator effort 비율, 배치 완성도.
- 객관(품질): 사람다움/적절성 — 실험 A 사람 배치 분포(합의 클러스터) 부합도, 또는 제3자 블라인드 비교(Merrell식).
- 주관: **NASA-TLX(인지부하)** 와 **신뢰·통제감(agency)** 을 **각각 별개 종속변수로** 측정. (Shared Control 핵심 발견: 자율성↑ 시 부하↓여도 신뢰는 독립적으로 움직임 → 둘 다 재야 "발화는 편하지만 통제감 낮다" 발견이 나옴.) + 조건 선호·상황별 적합성.
- 정성: think-aloud, 종료 인터뷰.

### 15.3 실험 A (사람 배치 수집 → 품질 채점 기준)
- baseline UI로 다수의 사람 배치 수집 → 태스크별 합의 클러스터 = "사람다움" 기준선. 실험 B의 품질 채점 루브릭으로만 사용(라이브러리로 LLM에 주면 컨닝 → 채점용/참고용 데이터 분리·홀드아웃).
- 이상값 처리: 평균±극단제거 대신 **클러스터로 요약**(대표 유형 + 빈도). 공간 배치는 평균이 위험.

### 15.4 선행연구 5축 (related work 골격)
1. 변신·로봇 가구: RoomShift(CHI'20), ShapeBots, Robotecture(TEI'25), ChairBot 계열.
2. LLM 공간 배치 생성: Holodeck(CVPR'24), HSM(3DV'26), Merrell(SIGGRAPH'11, 규칙 최적화=논리 엔진).
3. LLM 로봇 인터페이스+사용자 평가: VoicePilot(UIST'24), Robot-Assisted Feeding(UIST Adj'24), RABBIT(HRI'24), VeriPlan(CHI'25).
4. 사람이 직접 배치하는 UI(baseline): Web-Based Multi-Robot Furniture UI(HRI'21), SwarmControl(2014), M4Bench(2025), Scene Editing as Teleoperation(2021).
5. 로그 분석+지표+혼합주도권: Steinfeld(HRI'06), Shared Control(2024), Mixed-Initiative AI(IUI'26), Technical-to-Perception.

## 16. baseline 모듈 명세 (v4.2 신규 — 새 채팅에서 구현 예정)

### 16.1 파일 구조
```
baseline/
├── index.html      # 3D 뷰 + 아이템 패널(로봇2 + 가구 asset). 채팅/음성 UI 없음
├── viewer.js       # buildRoom + 드래그 이동 + 회전 버튼 + 패널 5단 버튼 + 선택/소환/제거 + 로그 수집
├── scenes/         # 거실/침실/부엌/화장실/발코니 (본 시뮬과 동일 방 크기)
├── collision.js    # OBB/SAT 충돌·경계 clamp·패널 snap (본 프로젝트에서 이식, LLM 무관)
└── logger.js       # 이벤트 로그 → 서버 POST 또는 파일 저장
```
- LLM·agent·prompts·STT·기능층/의도층 전부 미포함.

### 16.2 조작 → 이벤트 매핑
- 드래그 종료 → `move`, 회전 버튼 → `rotate`, 패널 버튼 → `panel`, 소환 → `spawn`, 제거 → `store`/`remove`, 완료 → `commit`.

### 16.3 로그 스키마
```json
{
  "session": "p03_livingroom",
  "task": "친구와 차를 마실 거야",
  "participant": { "id": "p03", "input": "mouse" },
  "events": [
    {"t": 2.1, "type": "spawn",  "obj": "BOT 1"},
    {"t": 3.4, "type": "move",   "obj": "BOT 1", "to": [120, 300]},
    {"t": 8.0, "type": "rotate", "obj": "BOT 1", "rot": 90},
    {"t": 9.2, "type": "panel",  "obj": "BOT 1", "side": "left", "val": 90}
  ],
  "final_state": { "로봇+가구 절대 상태 스냅샷": "..." },
  "duration": 94.5
}
```
- **최종 상태만이 아니라 이벤트 전체 궤적**을 남긴다 → "위치부터 잡나 변형부터 하나" 같은 조작 전략 분석(Interaction-to-Intent식)의 근거.
- 품질관리(온라인 수집 시): 최소 조작 수·최소 시간 미달 제외, catch trial 1개, 중복 차단.
