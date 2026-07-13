# 로봇 가구 LLM Agent 프로젝트 — 컨텍스트 프롬프트 (v4)

너는 이 프로젝트의 개발을 돕는 조수야. 아래는 이미 확정된 설계 내용이니, 이를 전제로 대화를 이어가.

> v3 → v4 주요 변경: **VLM 시각 자가검증(critic) 전면 제거**, **HITL-1 실제 블로킹 승인 구현**, **HITL-2 승인 시 코드가 자동 commit**, **revert를 main.py에서 결정론적으로 처리(의도층이 revert_to_turn 지정)**, **되묻기(clarification)를 형태층 tool에서 의도층+HITL 앞단으로 이관(tool 13→12개)**, **store_robot no-op 가드**, **check_feasibility issues에 수정 힌트**, **find_placement에 panel_toward_anchor 추가**, **시작 시 resume 제거(재시작=도크 초기화)**, **뷰어 중복 말풍선·GLB 스왑 race 수정**, **render.py 제거**.
>
> v4.1 패치: **find_placement에 connect 모드**(두 대 조합의 정밀 연결 좌표를 코드가 계산 — LLM 삼각함수 금지), **transform/move 직후 자동 validate_layout**(issues+fix 힌트를 결과에 실어 반환), **연결 자동 감지**(맞닿음 조건을 만족하는 패널 쌍은 충돌에서 제외 — slack 2cm vs tol 3cm 경계 오판 해소), **revert 대상 = '현재 상태와 다른' 가장 최근 커밋**(승인 자동 commit 후 no-op 방지, 프롬프트·fallback 모두), **commit_if_changed가 (entry, changed) 반환**(커밋 여부 역추론 제거), **HITL 대기 중 재접속 시 pending 요청 재전송**(F5 데드락 방지, req_id로 중복 방지), **되묻기 최대 2회 후 LLM이 잔여 정보 추론해 진행**, **handle() 예외 격리**(발화 하나의 실패가 세션을 죽이지 않음), **get_recent_context 슬림화**(메타 5필드+로봇 한 줄 요약), **GLB 스왑 후 inactive dim 재적용**, **stt.py에서 콘솔·Windows 경로 제거**(transcribe_bytes+로그만), **tests/ 제거**(테스트 스위트는 운용하지 않기로 함), **panel_away_from_anchor 추가**(두 값을 모두 명시해 '반대 값 뒤집기' 연산 제거 — 항상 toward로 여는 편향 수정. 어느 쪽을 열지는 규칙표·motif 힌트가 아니라 LLM이 '기능면이 몸·물건과 만나는 방향'으로 상황 판단), **형태층 마무리 발화 채팅 미표시**(승인 문구와 중복).

## 1. 프로젝트 개요

사용자의 **음성 발화**를 듣고, 변신 로봇 가구(shape-shifting robot furniture)를 알맞은 형태·위치로 구성해주는 LLM agent 시스템.
목적: 사용자가 "친구랑 밥 먹을 거야"라고 말하면 시스템이 상황을 파악해 로봇들을 알맞게 변형·배치하고, 사용자 피드백으로 수정하는 것.

- 기반 코드: STT(브라우저 push-to-talk + Groq Whisper) → OpenAI LLM 의도분석 → 로봇 명령 JSON 출력 (structured outputs, strict json_schema)
- **로봇은 BoT²로 확정** (KIST, 부모–자녀 공유 주거용 모듈형 로봇 가구. 단일 기종 2대: BOT 1, BOT 2). 상세 스펙은 12절.
- 타겟 사용자가 부모+자녀라 **성인/아이 이중 스케일**이 핵심: 같은 형태가 성인에겐 스툴, 아이에겐 테이블 (본체 높이 50cm). 의도층이 인원 수뿐 아니라 **구성(성인/아이)**까지 추론한다.
- **음성 입력은 브라우저 push-to-talk**: 채팅창 🎤 버튼 또는 스페이스바를 누른 채 말하기 → MediaRecorder → POST /stt → Groq Whisper. 콘솔·Windows(ctypes) 의존 제거. ROS2 변환은 사용자가 명시적으로 "ROS로 바꿔줘"라고 할 때만 진행.
- **배치의 '조화' 판단은 LLM 자가 점검 + HITL-2 몫**이다. (v3의 VLM 시각 자가검증은 도구 호출 과다로 제거됨 — 13절 참고.)

## 2. 핵심 설계 원칙 (차별점)

1. **3층 변환 구조**: 발화 → [의도층] intent → [기능층] 로봇 무관 가구 요구 목록 → [형태층] 로봇 구성(형태·위치). LLM 한 방 호출이 아니라 표현이 두 번 변환됨.
2. **2중 human-in-the-loop**: HITL-1 언어 게이트(의도 확인 — **분석된 의도를 사용자에게 블로킹 승인받은 뒤에야 형태층 진행**), HITL-2 공간 게이트(3D 뷰어로 배치 확인 — **승인 즉시 코드가 스냅샷을 자동 commit**). 두 게이트의 통과율·수정 턴 수가 실험 지표.
3. **상태 편집 + 버전 복원**: 대화는 버전된 history를 만들고, LLM은 현재 상태의 편집기. "원래대로"는 재생성이 아니라 저장된 state의 결정론적 **복원**.
4. **LLM 제안 / 코드 보장**: LLM은 배치를 제안만 하고, 충돌·경계·연결 기하 검증은 결정론적 코드 레이어가 최종 책임. **좌표·패널 방향 선택도 코드가 계산해 후보로 주고(find_placement) LLM은 고르기만 한다.**
5. **카탈로그가 아닌 문법**: 프롬프트(ROBOT_MECHANISM)는 물리적 사실만 서술. 가구 이름·용도 매핑은 furniture_motifs.json에 reference로만 둠 → LLM이 미등록 형태(발받침대 등)도 물리 스펙 안에서 자유 생성.
6. 환경은 scene JSON으로 표현 (고정 실험 제약 없음).

## 3. 의도 스키마 (의도층 출력, structured outputs strict)

```json
{
  "number": 2,              // nullable. 인원 단서가 전혀 없으면 반드시 null (추측 금지). null이면 needs_clarification 트리거
  "user_composition": {"adult": 1, "child": 1},  // 성인/아이 수. 아이는 명시 단서 있을 때만 — 없으면 전원 성인 간주(child 0, adult=number). number null이면 adult도 null. 조정성 발화면 직전 값 유지
  "situation": "아이와 식사하려는 상황",
  "activity": "식사",
  "space": "kitchen",       // living_room/bedroom/kitchen/bathroom/balcony/unknown (scenes/ 파일명과 일치). 애매하면 unknown → needs_clarification
  "furniture": [{"item": "식탁", "count": 1}, {"item": "의자", "count": 2}],  // array. 로봇 수 고려 없이 필요 가구 나열 (기능층)
  "posture": "sitting",     // standing/sitting/lying. 조정성 발화면 직전 값 유지
  "intent_type": "new_scene",  // confirm / modify / add / remove / revert / new_scene
  "revert_to_turn": null,   // nullable int. intent_type이 revert일 때만, 되돌릴 대상 turn 번호. main이 최근 history를 의도층에 넘겨줌
  "needs_clarification": false,  // 발화만으로 진행하기엔 정보 부족·해석 애매하면 true (number null / space unknown / 후보 여럿)
  "clarification_question": null, // needs_clarification true면 물을 한 문장. 아니면 null
  "confirmation_message": "아이와 함께 식사하시는 상황이군요, 식탁과 의자를 준비해드릴게요."  // HITL-1용
}
```

구현 상태: INTENT_PROMPT + INTENT_SCHEMA는 prompts.py에 완성 (12개 필드 전부 required, additionalProperties false). number·posture·space·user_composition은 조정성 발화에서 직전 값 유지 규칙 포함. 의도층은 최근 history 요약(recent_history)을 함께 받아 revert_to_turn을 고른다. ask_intent는 도출한 의도 결과를 `[INTENT]` 라벨로 콘솔에 출력.

## 4. intent_type 라우팅 (배치 후 사용자 반응 처리)

- confirm → 스냅샷 확정. **HITL-2 승인 시 이미 코드가 자동 commit하므로, confirm 발화는 그새 변화가 있을 때만 새로 commit**(commit_if_changed). turn 중복 증가 방지.
- modify/add/remove → 형태층만 재실행 (의도 재해석 불필요). **최소 편집**: 요청과 무관한 로봇은 그대로 유지, 놀고 있는 자원(0° 패널, 미사용 로봇)부터 활용
- revert → **main.py에서 결정론적으로 처리**. 의도층이 준 `revert_to_turn`으로 `revert_to(turn)`만 호출하고 형태층 LLM 생성은 스킵. **fallback·안전망: 대상이 null이거나 복원해도 무변화면 '현재 상태와 다른 가장 최근 커밋'을 선택** (승인 시 자동 commit되므로 최신 커밋==현재 상태 → 최신 turn 복원은 no-op이기 때문).
- new_scene → 의도층부터 전체 재실행. **space가 바뀌면 방 전환 포함** (아래 10절). 이전 구성에 얽매이지 않고 재구성하며, **새 구성에 쓰이지 않으면서 지금 active인 로봇만 store_robot으로 정리** (이미 inactive면 호출 불필요 — no-op)

**되묻기(clarification)는 HITL 앞단에서 처리**: 의도층이 `needs_clarification=true`를 내면, main이 HITL-1보다 먼저 사용자에게 되묻고(최대 2회) 답을 발화에 보태 의도를 재분석한 뒤 HITL-1로 넘어간다. (v3에서 형태층 tool이던 ask_clarification은 제거됨.) **2회 후에도 미해소면 발화에 '(되묻기 한도 도달)'을 붙여 1회 재분석 — LLM이 남은 정보를 가장 그럴듯하게 추론해 채우고 진행한다** (형태층은 정보가 해소된 intent만 받는다는 전제 유지).

편집 크기는 intent_type이 정한다: 조정(modify/add/remove)은 최소 편집, 새 상황(new_scene)은 재구성 + 잔여 정리. (AGENT_PROMPT에 명시)

## 5. history 형식

```python
history = [
  {"turn": 1, "intent_type": "new_scene", "space": "living_room",
   "utterance": "테이블 만들어줘", "description": "BOT 1 테이블 배치",
   "state": {로봇 전체의 절대 상태}},
  ...
]
```

- turn은 전역 번호(리셋 없음), state는 항상 전체 로봇의 절대 상태 스냅샷(diff 아님) → 어느 turn이든 단독 복원 가능
- `commit_if_changed`: 직전 커밋 이후 상태 변화가 없으면 재커밋하지 않음. **반환은 `(entry, changed)`** — 호출부가 커밋 여부를 역추론하지 않는다 (HITL-2 자동 commit ↔ confirm 발화 ↔ commit_layout tool 사이의 중복 turn 증가 방지)
- intent_type이 new_scene인 항목이 상황 경계. "원래대로"는 기본적으로 현재 상황 안에서 해석하되, "밥 먹을 때처럼"은 경계 넘어 탐색
- space도 함께 기록 → "아까 거실에서처럼" 해석 가능
- commit마다 logs/session.json에 저장 (로그·디버깅용)

## 6. Tool 목록 (12개, 전부 tools/ 폴더에 정의)

### placement_tools.py (6)
- `transform_robot(robot, panel_left, panel_right, furniture)` — 로봇 변형. size 개념 없음, 두 패널 각도(0/45/90/135/180)로만 변형. 변경 직후 자동 push_state. **실행 직후 코드가 validate_layout을 자동 실행** — issues(+fix 힌트)가 있으면 결과에 실어 LLM이 즉시 자가수정 (LLM의 check_feasibility 호출에 의존하지 않는 보장 레이어)
- `move_robot(robot, x, y, rot)` — 이동 + 회전. 변형과 분리. 이동 거리 비례 애니메이션 duration. **실행 직후 자동 검증(transform과 동일)**. **반환에 `panel_orientation` 포함**: 실행 후 실제 rot·위치 기준으로 주변 앵커(150cm 내 가구·active 로봇)별 toward/away 패널을 코드가 재계산한 확정값 — LLM이 rot_suggest를 안 따르거나 후보를 섞어 써서 계획값(panel_toward_anchor)이 무효가 돼도, transform 직전에 항상 신선한 진실이 공급된다. 패널 축에서 ≈70° 이상 벗어난 앵커는 off_axis 표시(고정 측면 방향)
- `store_robot(robot)` — "치워줘". 홈 도크 복귀 + 초기화(패널 0°)를 원자 처리. **이미 inactive(도크 정리 완료)인 로봇이면 no-op으로 반환하고 뷰어 push도 스킵** (중복 호출로 tool 한도 낭비 방지)
- `check_feasibility(robots, connections)` — 물리 검증(경계·가구·로봇 겹침) + 연결 기하(패널 끝 맞닿는 거리). 반환: `{"feasible": bool, "issues": [...]}`. **각 issue에 수정 방향 힌트**(`fix: {dx, dy, note}`, 겹침 `penetration`)를 실어 LLM 재시도 수렴을 돕는다. **조화 판단은 이 tool이 하지 않음 → LLM 자가 점검 + HITL-2 몫**
- `find_placement(footprint_radius, near?, avoid[], connect?, ...)` — 유효 후보 좌표 제안. near에 가구 id면 인접 후보(tag: <id>_front/_side/_back), 비우면 방 전체 가용 공간 조사(가구 앞·open_area·벽가). **각 후보에 tag·clearance·rot_suggest(앵커 바라보는 각도)·`panel_toward_anchor`/`panel_away_from_anchor`("left"/"right" — 그 rot에서 앵커를 향하는/등지는 패널, 둘 다 명시)를 부여** — LLM이 좌우를 삼각함수로 추론하거나 반대 값을 뒤집는 연산을 하지 않게 한다. 어느 쪽을 열지는 **motif나 규칙표가 아니라 LLM이 상황으로 판단**한다 — 기준은 '기능면이 몸·물건과 만나는 방향'이고 항상 toward가 정답이 아니다(예: 파티션이 설 자리는 앵커가 아니라 가려야 할 시선 쪽). 두 값은 rot_suggest 채택 시에만 유효. user 좌표는 쓰지 않음. **`connect` 모드(near=앵커 로봇 이름): 두 대 조합의 정밀 연결 좌표를 코드가 계산** — face(마주보고 패널 맞대기: rot 차 180°, 거리 = 40 + 30·sinθa + 30·sinθb, panels_touching 통과 보장, 맞댈 패널 moving_side 반환) / side(나란히: rot 동일, 본체 맞대기). 후보가 없으면 note를 실어 반환(좌표 지어내기 방지)
- `furniture_mapping(activity)` — 활동→가구 조합 참고표 조회. **강제 아닌 reference**. motif에 capacity(권장 인원) 필드

### context_tools.py (5)
- `robot_states()` — 로봇 현재 상태 조회
- `get_environment()` — 현재 방의 scene JSON(방 크기·기존 가구) 조회
- `get_recent_context(n)` — 최근 n턴 history 조회 ("아까 그거" 해석용). **슬림 반환**: 메타 5필드(turn/space/intent_type/utterance/description) + 로봇당 한 줄 요약 — full state 스냅샷은 LLM에 안 줌(복원은 revert_to가 코드로 처리)
- `commit_layout(description)` — 승인된 상태를 스냅샷 확정. **보통은 부를 필요 없다**(ask_user 승인 시 코드가 자동 commit). 변화 없으면 no-op(중복 커밋 방지)
- `revert_to(version)` — turn 번호로 상태 복원. **반환은 슬림**(`{turn, space, description}` — 전체 state는 코드가 이미 복원했으니 LLM에 안 줌)

### viewer_tools.py (1)
- `ask_user(message)` — 결과 승인 요청 (HITL-2 게이트). **승인받으면 그 자리에서 코드가 배치를 자동 commit**(commit_if_changed). approved=false면 feedback을 반영해 수정

주의: 뷰어 갱신은 tool이 아님. transform/move/store/revert가 상태 변경 직후 코드가 자동으로 push_state() 호출. 방 전환(scene_change)도 자동. **되묻기(ask_clarification)는 tool이 아니라 의도층 신호(needs_clarification)로 main이 HITL 앞단에서 처리**한다(4절).

## 7. tools/ vs services/ 구분 원칙

- **tools/** = LLM에게 보이는 껍데기. 함수 + 스키마 정의. 내용은 services 호출 한두 줄
- **services/** = 순수 계산·상태. LLM 없이 pytest로 단위 테스트 가능. 여러 tool이 공유
- LLM이 호출 가능한 건 registry.TOOLS에 스키마가 등록된 12개뿐. services 함수는 LLM에게 보이지 않음
- agent.py는 tool을 갖지 않음. LLM의 tool_call(JSON)을 받아 registry.HANDLERS에서 이름으로 찾아 실행하는 중계자. `[tool]` 로그는 인자·결과 JSON을 자르지 않고 전부 출력(ensure_ascii=False)
- tool ↔ services 매핑: robot_states→SceneState.states / get_environment→environment()+furniture() / get_recent_context→recent(n) / commit_layout→commit_if_changed / revert_to→revert_to / transform_robot→transform / move_robot→move / store_robot→store(no-op 가드는 tool 층) / check_feasibility→collision.validate_layout+panels_touching / find_placement→placement.find_placement

## 8. 프로젝트 구조

```
project/
├── main.py                # ✅ 조립 + 단일 발화 루프 + 라우팅. HITL-1 블로킹 승인 + 되묻기(HITL 앞단) + revert 결정론 처리. 시작 시 resume 안 함(재시작=도크)
├── agent.py               # ✅ ask_intent(의도층, recent_history 포함, [INTENT] 출력) + run_agent(tool-call 루프, 중계만)
├── config.py              # ✅ API 키 + 모델명 + 로봇 물리 상수 (단일 출처). VLM/critic 상수 제거됨
├── prompts.py             # ✅ ROBOT_MECHANISM ✅, INTENT_PROMPT ✅, INTENT_SCHEMA ✅(12필드), AGENT_PROMPT ✅. CRITIC_PROMPT/SCHEMA 제거됨
├── data/
│   └── furniture_motifs.json   # ✅ motif + modifiers + 활동표 (reference)
├── scenes/                # 방마다 JSON 하나
│   ├── living_room.json / bedroom.json / kitchen.json / bathroom.json / balcony.json
├── tools/
│   ├── registry.py        # ✅ TOOLS(strict 스키마 12개) + HANDLERS + dispatch
│   ├── __init__.py        # ✅ 공유 STATE(scene·client·viewer·intent·utterance·auto_approve) + push_state
│   ├── placement_tools.py # ✅ 6개 (store_robot no-op 가드 포함)
│   ├── context_tools.py   # ✅ 5개 (commit_layout idempotent, revert_to slim)
│   └── viewer_tools.py    # ✅ ask_user 1개 (HITL-2 승인 시 _commit_on_approval 자동 커밋). critic 훅·ask_clarification 제거됨
├── services/
│   ├── placement.py       # ✅ find_placement(tag/clearance/rot_suggest/panel_toward_anchor) + feasibility(물리+연결)
│   ├── collision.py       # ✅ OBB(SAT) 충돌·slack 2cm·경계 clamp·밀어내기·연결 검증. validate_layout issues에 fix 힌트
│   ├── scene.py           # ✅ SceneState: scene+robots+history + commit/commit_if_changed/revert/recent + load_scene + save/resume
│   └── stt.py             # ✅ transcribe_bytes(브라우저 오디오→Groq) + 로그 (콘솔·Windows 경로 제거됨)
│   #  (render.py — VLM critic용이었으나 제거됨)
│   #  (tests/ — 제거됨. 테스트 스위트는 운용하지 않기로 함)
├── viewer/
│   ├── popup_viewer.py    # ✅ FastAPI+WS + POST /stt + 발화/승인/되묻기 큐 + 재접속 스냅샷(+pending HITL 재전송)
│   └── static/
│       ├── index.html     # ✅ 3D(2/3) + 채팅 패널(1/3): 말풍선·입력창·🎤 push-to-talk
│       └── viewer.js      # ✅ buildRoom·가구 GLB(fallback 박스)·로봇 GLB 스왑(최신-키 race 가드 + 실패 시 조립식 fallback 복구)·최단경로 보간·방 라벨 dedup
└── models/
    ├── robot_<L>x<R>.glb  # 패널 상태별 25개 (5×5). 단위 mm(×0.1), 공통 원점, L=-z / R=+z쪽 패널. 뷰어는 상태 변경 시 모델 스왑
    │                      #   ⚠ 감축(decimation)본은 패널 열린 상태의 면이 뭉개질 수 있음 — 필요 시 raw 원본 사용
    ├── raw/               # 원본 25개 (개당 ~97MB, 2.4GB — git 제외 필수. 패널 지오메트리 온전)
    └── sofa.glb 등        # 가구 GLB (선택. 없으면 조립식 가구로 fallback)
```

의존 방향(한 방향만): main → agent → tools → services, tools → viewer(push만). services는 tools를 모름. viewer는 그리기만.

## 9. 뷰어 아키텍처

- 파이썬(두뇌)과 브라우저 three.js(화면)는 WebSocket으로 통신. 파이썬이 push, 브라우저는 받은 대로 그리고 애니메이션(현재값→목표값 보간. rot은 최단 경로)
- 메시지 종류: `state_update`(파→브, 로봇 상태+duration), `scene_change`(파→브, 새 방 JSON), `chat`(말풍선, 파→브), `message`(자막), `approval_request`(파→브, HITL 승인), `clarify_request`(파→브, 되묻기), `user_feedback`·`clarify_answer`·`user_utterance`(브→파)
- **중복 말풍선 방지**: HITL-1 승인·되묻기 문구는 `approval_request`/`clarify_request`가 말풍선을 그리므로, 파이썬 쪽에서 같은 문구를 `chat`으로 또 보내지 않는다. 방 라벨(`― 방이름 ―`)은 실제 방이 바뀔 때만 추가(재연결 스냅샷마다 중복 추가 방지)
- 브라우저 재접속 시 즉시 현재 scene + state 스냅샷 push (duration 0) → **F5 복구**(파이썬 프로세스가 살아 있으면 배치 유지). **HITL 승인/되묻기 대기 중이면 pending 요청도 재전송**(req_id로 같은 소켓 내 중복 방지) — 대기 중 F5로 인한 영구 블로킹 방지. 프로그램 재시작은 도크에서 새로 시작(시작 시 resume 안 함)
- 좌표계: scene (x,y) cm [0..w]×[0..d]. three.js: X=x−w/2, Z=d/2−y. rot(도 CCW) → rotation.y=rad(rot). 이 매핑에서 panel_left(−x)·panel_right(+x) 방향이 collision.py 규약과 일치 (검증됨)
- **로봇 에셋**: 패널 상태별 GLB 25개(robot_<L>x<R>.glb) — 상태 변경 시 모델 스왑. `swapGlb`는 **최신-키 가드**로 연속 push 시 로드 완료 순서가 꼬여 옛 모델이 최신을 덮어쓰는 것을 막고, **GLB 로드 실패 시 조립식 fallback(힌지 애니메이션)으로 복구**해 패널 변화가 항상 보이게 한다. 감축본이 패널 면을 잃으면 raw 원본으로 교체(브라우저 캐시 주의 — Disable cache + 하드 새로고침)
- **가구**: `"model"` 지정 시 GLB 로드(JSON 치수에 맞게 스케일·회전), 미지정/실패 시 label별 조립식 가구 fallback
- 충돌 계산은 항상 JSON의 w,d 사각형 기준

## 10. 다중 방(scene) 전환

- 방마다 scenes/<space>.json 하나 (5개). HTML/viewer.js는 방 개수와 무관하게 1개
- 방 크기: living_room 400×400 / kitchen 400×250 / bedroom 300×300 / bathroom 200×200 / balcony 400×100
- 도크: scene JSON의 "dock" 필드로 명시. 없으면 config.home_for()가 자동 계산
- 기존 가구(pre_existing_furniture): `{"id","label","x","y","w","d","rot","corners","model"?}`. corners는 바닥면 네 모서리
- **기존 가구는 장애물이자 배치 앵커**. 원칙: **"중복하지 말되, 반드시 기여하라"** — 단, 중복 판단은 이름·카테고리 일치가 아니라 **적합성**이다. 기존 가구가 그 활동의 필요를 높이·크기·전용 면적 면에서 *실제로* 충족할 때만 목록에서 빼라(부엌 식탁=식사 면 충족→빼기 / 거실 낮은 테이블≠공작 작업대→안 빼고 로봇이 제대로 된 작업대 제공이 기여). 활동 지원 발화에만 적용하고 remove는 예외
- 의도층이 발화에서 space 추론. 애매하면 unknown → needs_clarification
- 전환 흐름: SceneState.load_scene(space) → collision 장애물 교체 → `scene_change` push → viewer가 buildRoom() → 로봇은 새 방 도크에서 시작
- 방 전환은 intent_type new_scene의 특수 케이스. history에 space 기록

## 11. 구현 순서 및 현황

1. ~~의도층 파이프라인~~ ✅ — main.py + services/stt.py + agent.ask_intent + prompts.py
2. ~~services 층~~ ✅ — collision(OBB/SAT) + scene(SceneState) + placement(find_placement)
3. ~~tools/ 구현 + agent.run_agent~~ ✅ — strict 스키마 12개, tool 루프, main 라우팅
4. ~~시각 자가검증(VLM critic)~~ — **제거됨** (도구 호출 과다. 조화 판단은 AGENT_PROMPT 자가 점검 + HITL-2로 대체. 13절)
5. ~~viewer/~~ ✅ — FastAPI+WS 서버, three.js(방·가구·로봇 GLB 스왑, race 가드·fallback 복구), HITL 승인 버튼(승인 시 자동 commit)
6. models/ 가구 GLB 조달 + baseline 수동 모드 + 아파트 통합 뷰(선택) — 실험 직전
7. **로깅 (플러스알파)** — logs/metrics.json: 거부된 제안 수, check_feasibility 실패 횟수, 되묻기 발동, HITL-2 수정 턴. 실험 지표(통과율·수정 턴 수)의 분모를 실행 중 수집

## 12. 로봇 확정 스펙 (BoT²)

### 물리 스펙 (전부 확정)
- **구성**: 동일 기종 **2대** (BOT 1, BOT 2). 본체 크기는 변하지 않는다 (size 개념 없음).
- **본체**: 상자형 40 × 40 × 50 cm. 윗면은 평평한 40×40 면. 바퀴로 자율 이동 + 제자리 회전. 방향성 있음 → state에 rot(도) 필수.
- **이중 스케일**: 본체 높이 50cm는 고정이라, 같은 형태라도 성인과 아이에게 다르게 작용한다 — 성인에겐 앉는 좌석, **아이에겐 상판(테이블·작업면)이 된다. 아이에게는 좌석이 아니라 테이블 높이임을 전제로 형태와 역할을 정한다.**
- **가동 패널 2개**: 마주보는 두 측면의 윗모서리 힌지. 40(폭)×30(길이) cm. 각각 독립적으로 **0/45/90/135/180° 5단계**(이산)로만:
  - 0°=측면에 붙어 닫힘 / 45°=\ 아래 기울임 / 90°=수평(상판 옆으로 30cm 확장) / 135°=/ 위 기울임 / 180°=윗면 위 수직(꼭대기 80cm)
- **수납**: 패널을 열면 본체 내부 서랍장. **고정 패널 2개**: 나머지 두 측면(자석식 인터랙션 모듈 부착부, 배치 범위 밖).
- **조합**: **최대 2대**. 나란히(rot 동일) / 마주보고 패널 맞대기(rot 차 180°).

### state 스키마 (확정)
```json
{"robot": "BOT 1", "active": "active", "x": 180, "y": 140, "rot": 90,
 "panel_left": 90, "panel_right": 0}   // panel 값은 {0,45,90,135,180}만
// active: "active"=사용 중 / "inactive"=도크 대기(store_robot). inactive도 도크 바닥을 차지 → 충돌 계산 포함
```

### collision.py (구현 완료)
- **OBB(회전 사각형) + SAT** 충돌. footprint_rects = 본체 40×40 + 돌출 패널
- 패널 바닥 돌출 = 30 × sin(각도). 1대 풀 확장 상판 40×100cm
- **slack 2cm**: 침투 2cm 이하는 충돌 아님 → 복합 가구의 '맞닿는 연결'은 통과
- panels_touching(끝 맞닿음 + 폭 겹침 ≥30). clamp_to_bounds, place_without_overlap, **validate_layout(issues에 out_of_bounds/furniture_overlap/robot_overlap + 수정 힌트 fix/penetration. 맞닿음 조건을 만족하는 패널 쌍은 '연결'로 자동 감지해 충돌에서 제외 — slack 2cm vs tol 3cm 경계 오판 방지)**, snap_panel
- **rot 규약**: 도 단위 CCW. rot=0일 때 panel_left=−x쪽, panel_right=+x쪽. 뷰어(three.js rotation.y)와 일치 확인됨

### services/scene.py — SceneState (구현 완료)
- 조회: environment()/furniture(), states(), recent(n)
- 편집: transform(패널 snap)/move(x,y,rot)/store(도크 복귀+초기화). transform/move 직후 자동 clamp
- 버전: commit(전역 turn++, 절대 스냅샷, session.json 저장) / **commit_if_changed(변화 없으면 재커밋 안 함)** / revert_to(turn — 방 다르면 방 전환)
- 복구: resume()는 존재하되 **main 시작 시 호출하지 않음**(재시작=도크). session.json은 로그·수동 복구용으로 계속 기록

## 13. 시각 자가검증 (VLM critic) — **제거됨**

v3에서 계획·구현했던 VLM 시각 자가검증(배치를 이미지로 렌더 → 비전 모델에게 조화 질의 → 문제 피드백 루프)은 **제거되었다**.

- **제거 이유**: 턴마다 렌더+VLM 호출이 도구 호출·지연을 크게 늘렸고, HITL-2(사용자 공간 게이트)가 조화 확인을 이미 담당함.
- **대체**: 배치의 '조화'는 (1) AGENT_PROMPT의 **실행 전 자가 점검** 지시(LLM이 완성 구성을 사용자 눈으로 훑어 어색함을 스스로 교정)와 (2) **HITL-2 승인**이 담당. check_feasibility는 물리(경계·겹침·연결)만 검증하고 조화는 판단하지 않는다.
- 관련 제거 항목: `services/render.py`, `prompts.CRITIC_PROMPT`/`CRITIC_SCHEMA`, `config.VISUAL_CHECK`/`CRITIC_*`, `viewer_tools._critic_check`, `STATE["critic_rounds"]`, registry의 ask_user `visual_check` 안내 문구.
