# 로봇 가구 LLM Agent 프로젝트 — 컨텍스트 프롬프트 (v3)

너는 이 프로젝트의 개발을 돕는 조수야. 아래는 이미 확정된 설계 내용이니, 이를 전제로 대화를 이어가.

## 1. 프로젝트 개요

사용자의 **음성 발화**를 듣고, 변신 로봇 가구(shape-shifting robot furniture)를 알맞은 형태·위치로 구성해주는 LLM agent 시스템.
목적: 사용자가 "친구랑 밥 먹을 거야"라고 말하면 시스템이 상황을 파악해 로봇들을 알맞게 변형·배치하고, 사용자 피드백으로 수정하는 것.

- 기반 코드: STT(스페이스바 녹음 + Groq Whisper) → OpenAI LLM 의도분석 → 로봇 명령 JSON 출력 (structured outputs, strict json_schema)
- **로봇은 BoT²로 확정** (KIST, 부모–자녀 공유 주거용 모듈형 로봇 가구. 단일 기종 2대: BOT 1, BOT 2). 상세 스펙은 12절.
- 타겟 사용자가 부모+자녀라 **성인/아이 이중 스케일**이 핵심: 같은 형태가 성인에겐 스툴, 아이에겐 테이블 (본체 높이 50cm). 의도층이 인원 수뿐 아니라 **구성(성인/아이)**까지 추론한다.
- **음성 입력은 브라우저 push-to-talk**: 채팅창 🎤 버튼 또는 스페이스바를 누른 채 말하기 → MediaRecorder → POST /stt → Groq Whisper. 콘솔·Windows(ctypes) 의존 제거. ROS2 변환은 사용자가 명시적으로 "ROS로 바꿔줘"라고 할 때만 진행.

## 2. 핵심 설계 원칙 (차별점)

1. **3층 변환 구조**: 발화 → [의도층] intent → [기능층] 로봇 무관 가구 요구 목록 → [형태층] 로봇 구성(형태·위치). LLM 한 방 호출이 아니라 표현이 두 번 변환됨.
2. **2중 human-in-the-loop**: HITL-1 언어 게이트(의도 확인 발화), HITL-2 공간 게이트(3D 뷰어로 배치 확인). 두 게이트의 통과율·수정 턴 수가 실험 지표.
3. **상태 편집 + 버전 복원**: 대화는 버전된 history를 만들고, LLM은 현재 상태의 편집기. "원래대로"는 재생성이 아니라 저장된 state의 결정론적 **복원**.
4. **LLM 제안 / 코드 보장**: LLM은 배치를 제안만 하고, 충돌·경계·연결 기하 검증은 결정론적 코드 레이어가 최종 책임.
5. **카탈로그가 아닌 문법**: 프롬프트(ROBOT_MECHANISM)는 물리적 사실만 서술. 가구 이름·용도 매핑은 furniture_motifs.json에 reference로만 둠 → LLM이 미등록 형태(발받침대 등)도 물리 스펙 안에서 자유 생성.
6. 환경은 scene JSON으로 표현 (고정 실험 제약 없음).

## 3. 의도 스키마 (의도층 출력, structured outputs strict)

```json
{
  "number": 2,              // nullable. 인원 단서가 전혀 없으면 반드시 null (추측 금지, few-shot: "얘들아 밥 먹자"→null). null이면 ask_clarification 트리거
  "user_composition": {"adult": 1, "child": 1},  // 성인/아이 수. 아이는 명시 단서 있을 때만 — 없으면 전원 성인 간주(child 0, adult=number). number null이면 adult도 null. 조정성 발화면 직전 값 유지
  "situation": "아이와 식사하려는 상황",
  "activity": "식사",
  "space": "kitchen",       // living_room/bedroom/kitchen/bathroom/balcony/unknown (scenes/ 파일명과 일치). 애매하면 unknown → ask_clarification
  "furniture": [{"item": "식탁", "count": 1}, {"item": "의자", "count": 2}],  // array. 로봇 수 고려 없이 필요 가구 나열 (기능층)
  "posture": "sitting",     // standing/sitting/lying. 조정성 발화면 직전 값 유지
  "intent_type": "new_scene",  // confirm / modify / add / remove / revert / new_scene
  "confirmation_message": "아이와 함께 식사하시는 상황이군요, 식탁과 의자를 준비해드릴게요."  // HITL-1용
}
```

구현 상태: INTENT_PROMPT + INTENT_SCHEMA는 prompts.py에 완성. number·posture·space·user_composition은 조정성 발화에서 직전 값 유지 규칙 포함.

## 4. intent_type 라우팅 (배치 후 사용자 반응 처리)

- confirm → commit_layout (turn++), 대기
- modify/add/remove → 형태층만 재실행 (의도 재해석 불필요). **최소 편집**: 요청과 무관한 로봇은 그대로 유지, 놀고 있는 자원(0° 패널, 미사용 로봇)부터 활용
- revert → history에서 state 로드 (LLM 생성 스킵)
- new_scene → 의도층부터 전체 재실행. **space가 바뀌면 방 전환 포함** (아래 10절). 이전 구성에 얽매이지 않고 재구성하며, **새 구성에 쓰이지 않는 로봇은 store_robot으로 정리** (잔여 가구 방지. 이전 상태는 history로 복원 가능)

편집 크기는 intent_type이 정한다: 조정(modify/add/remove)은 최소 편집, 새 상황(new_scene)은 재구성 + 잔여 정리. (AGENT_PROMPT에 명시할 규칙)

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
- intent_type이 new_scene인 항목이 상황 경계. "원래대로"는 기본적으로 현재 상황 안에서 해석하되, "밥 먹을 때처럼"은 경계 넘어 탐색
- space도 함께 기록 → "아까 거실에서처럼" 해석 가능
- commit마다 logs/session.json에 저장 (재시작 시 resume 복원)

## 6. Tool 목록 (13개, 전부 tools/ 폴더에 정의)

### placement_tools.py (6)
- `transform_robot(robot, panel_left, panel_right, furniture)` — 로봇 변형. size 개념 없음, 두 패널 각도(0/45/90/135/180)로만 변형
- `move_robot(robot, x, y, rot)` — 이동 + 회전. 변형과 분리 (회전 전용 tool은 없음)
- `store_robot(robot)` — "치워줘". 홈 도크 복귀 + 초기화(패널 0°)를 원자 처리
- `check_feasibility(구성안)` — 물리 검증(경계·가구·로봇 겹침) + 연결 기하(패널 끝 맞닿는 거리). 반환: `{"feasible": bool, "issues": [...]}`. **조화 판단은 코드로 열거 불가 → LLM 사고 지시 + HITL-2 몫** (경고 heuristic은 규칙표 회귀라 제거)
- `find_placement(footprint_radius, near?, avoid[])` — 유효 후보 좌표 제안 (기하 탐색은 코드가 대행). near에 가구 id를 주면 그 가구 인접 후보(tag: <id>_front/_side/_back), near를 비우면 방 전체 가용 공간 조사(가구 앞·open_area·벽가). footprint는 반경(정사각) 또는 **w×d 직사각 proxy**(풀확장 100×40처럼 길쭉한 구성 — 좁은 방에서 후보 확보). **각 후보에 tag·clearance·rot_suggest 부여 — 코드가 유효한 자리를 계산하고, LLM은 tag 의미로 선택 (연속 공간 → 객관식). user 좌표는 쓰지 않음**
- `furniture_mapping(activity)` — 활동→가구 조합 참고표 조회. **강제 아닌 reference**임을 description에 명시. motif에 capacity(권장 인원) 필드 — 1~2인 테이블은 1대 확장, 3인 이상은 2대 조합 (최소 구성 원칙)

### context_tools.py (5)
- `robot_states()` — 로봇 현재 상태 조회
- `get_environment()` — 현재 방의 scene JSON(방 크기·기존 가구) 조회
- `get_recent_context(n)` — 최근 n턴 history 조회 ("아까 그거" 해석용)
- `commit_layout(description)` — 승인된 상태를 스냅샷 확정
- `revert_to(version)` — turn 번호로 상태 복원

### viewer_tools.py (2)
- `ask_user(message)` — 결과 승인 요청 (HITL 게이트)
- `ask_clarification(type, ...)` — 입력 보완. type: missing_info(필드 누락: number null, space unknown) / ambiguous_intent(해석 후보 candidates 구조화). 턴당 1회, 질문 루프 최대 2회, 실패 시 STT 재시도 fallback

주의: 뷰어 갱신은 tool이 아님. transform/move/store/revert가 상태 변경 직후 코드가 자동으로 push_state() 호출 (LLM이 갱신을 잊는 경우 원천 차단). 방 전환(scene_change)도 동일하게 자동.

## 7. tools/ vs services/ 구분 원칙

- **tools/** = LLM에게 보이는 껍데기. 함수 + 스키마 정의. 내용은 services 호출 한두 줄
- **services/** = 순수 계산·상태. LLM 없이 pytest로 단위 테스트 가능. 여러 tool이 공유
- LLM이 호출 가능한 건 registry.TOOLS에 스키마가 등록된 13개뿐. services 함수는 LLM에게 보이지 않음
- agent.py는 tool을 갖지 않음. LLM의 tool_call(JSON)을 받아 registry.HANDLERS에서 이름으로 찾아 실행하는 중계자
- tool ↔ services 매핑 (구현 기준): robot_states→SceneState.states / get_environment→environment()+furniture() / get_recent_context→recent(n) / commit_layout→commit / revert_to→revert_to / transform_robot→transform / move_robot→move / store_robot→store / check_feasibility→collision.validate_layout+panels_touching / find_placement→collision.place_without_overlap 기반(placement.py에서 조합)

## 8. 프로젝트 구조

```
project/
├── main.py                # ✅ 조립 + 단일 발화 루프(브라우저 채팅/음성 → utterance_q) + 라우팅
├── agent.py               # ✅ ask_intent(의도층) + run_agent(tool-call 루프, 중계만)
├── config.py              # API 키 + 모델명 + 로봇 물리 상수 (단일 출처) ✅
├── prompts.py             # ROBOT_MECHANISM ✅, INTENT_PROMPT ✅, INTENT_SCHEMA ✅, AGENT_PROMPT ✅ (10섹션: 자가 점검 포함)
├── data/
│   └── furniture_motifs.json   # ✅ motif 17개 + modifiers + 활동표 8개 (reference)
├── scenes/                # 방마다 JSON 하나 (HTML은 방 개수와 무관하게 1개)
│   ├── living_room.json / bedroom.json / kitchen.json / bathroom.json / balcony.json
├── tools/
│   ├── registry.py        # ✅ TOOLS(strict 스키마 13개) + HANDLERS + dispatch
│   ├── __init__.py        # ✅ 공유 STATE(scene·client·intent) + push_state placeholder
│   ├── placement_tools.py # ✅ 6개 구현 (services 호출 한두 줄)
│   ├── context_tools.py   # ✅ 5개 구현
│   └── viewer_tools.py    # ✅ ask_user(critic 훅 포함)·ask_clarification (콘솔 fallback)
├── services/
│   ├── placement.py       # ✅ find_placement v2(앵커 인접 + 가용공간 조사, tag/clearance/rot_suggest) + feasibility(물리+연결)
│   ├── collision.py       # ✅ OBB(SAT) 충돌·slack 2cm·경계 clamp·밀어내기·연결 검증 (12절)
│   │                      #   pre_existing_furniture를 고정 장애물로 사용 (방 전환 시 교체)
│   ├── scene.py           # ✅ SceneState: scene + robots + history + commit/revert/recent
│   │                      #   + load_scene(space) 방 전환 + save/resume(logs/session.json)
│   ├── render.py          # ✅ 탑다운 2D 배치도 렌더 (PIL) — 시각 자가검증용
│   └── stt.py             # ✅ transcribe_bytes(브라우저 오디오→Groq) + 스페이스바 녹음(레거시) + 로그
├── tests/                 # ✅ pytest 36개 통과 (tools 왕복·렌더·뷰어 WS·STT 엔드포인트 포함)
│   ├── test_collision.py  # 돌출·footprint·충돌·slack·경계·밀어내기·연결(정렬 포함)·layout
│   ├── test_scene.py      # 도크·commit/revert(방 전환 포함)·snap·store·clamp·resume
│   ├── test_placement.py  # 앵커/조사 모드·태그·직사각 proxy·avoid·feasibility
│   └── test_tools.py      # 스키마 규칙·턴 왕복(find→check→move→transform→ask→commit→revert)·렌더
├── viewer/
│   ├── popup_viewer.py    # ✅ FastAPI+WS + POST /stt(push-to-talk) + 발화/승인/질문 큐 + 재접속 스냅샷
│   └── static/
│       ├── index.html     # ✅ 3D(2/3) + 채팅 패널(1/3): 말풍선·입력창·🎤 push-to-talk (baseline 슬라이더 TODO)
│       └── viewer.js      # ✅ buildRoom·가구 GLB(fallback 박스)·로봇 GLB 스왑+조립식 힌지 fallback·최단경로 보간
└── models/
    ├── robot_<L>x<R>.glb  # ✅ 패널 상태별 25개 (5×5, 각 7.5MB — 원본 97MB에서 폴리곤 97% 감소)
    │                      #   단위 mm(×0.1), 공통 원점, L=-z / R=+z쪽 패널. 뷰어는 상태 변경 시 모델 스왑
    ├── raw/               # 원본 25개 (2.4GB — git 제외 필수)
    └── sofa.glb 등        # 가구 GLB TODO (선택. 없으면 조립식 가구로 fallback)
```

의존 방향(한 방향만): main → agent → tools → services, tools → viewer(push만). services는 tools를 모름. viewer는 그리기만.

## 9. 뷰어 아키텍처

- 파이썬(두뇌)과 브라우저 three.js(화면)는 WebSocket으로 통신. 파이썬이 push, 브라우저는 받은 대로 그리고 애니메이션(현재값→목표값 보간. rot 보간은 최단 경로: 차이를 -180~+180으로 정규화 후 보간)
- 메시지 5종: `state_update`(파→브, 로봇 상태+duration), `scene_change`(파→브, 새 방 JSON 통째로), `message`(자막, 파→브), `user_feedback`(브→파), `manual_command`(브→파, baseline 모드)
- 브라우저 재접속 시 ws_endpoint가 즉시 현재 scene + state 스냅샷을 push (duration=0) → F5 복구 가능
- 좌표계: scene JSON의 (x,y)는 방 좌표 [0..w]×[0..d] cm. three.js에서는 방 중심 원점, posX=x−w/2, posZ=y−d/2. 본체 회전은 `robot.rotation.y = degToRad(rot)` (방향 부호 규약은 collision.py와 일치시킬 것)
- **로봇 에셋**: 현재는 패널 상태별 GLB 25개(robot_<L>x<R>.glb) — 상태 변경 시 **모델 스왑**(이동·회전은 보간, 패널 변화는 전환). 전부 로드해 캐시(~190MB) 권장. 원본 툴에서 패널을 별도 노드(panel_L/panel_R)로 export할 수 있게 되면 1파일+노드 회전 방식(부드러운 펼침 애니메이션)으로 업그레이드. GLB 없으면 상자 fallback
- **가구**: 가구(pre_existing_furniture)에 `"model": "sofa.glb"` 지정 시 GLB 로드(JSON 치수 w,h,d 안에 맞게 스케일, `"rot"` 회전 지원), 미지정/실패 시 label별 조립식 가구(코드 생성) fallback. 가구 GLB는 Poly Pizza·Kenney·Sketchfab(CC0) 등에서 조달
- 충돌 계산은 항상 JSON의 w,d 사각형 기준 (화면에 GLB가 뜨든 조립식이 뜨든 무관)
- baseline 실험 조건: 같은 뷰어에 수동 조작 모드(슬라이더+드래그) — LLM 경로만 끄고 검증·렌더는 공유
- 시각 품질 업그레이드(선택): ACESFilmicToneMapping + RoomEnvironment 환경광

## 10. 다중 방(scene) 전환

- 방마다 scenes/<space>.json 하나 (5개). HTML/viewer.js는 방 개수와 무관하게 1개 (JSON대로 그릴 뿐)
- 방 크기 (이어붙여 아파트 조립 가능하도록 변 길이 호환): living_room 400×400(16㎡, 정사각) / kitchen 400×250(10㎡, 직사각) / bedroom 300×300(9㎡, 정사각) / bathroom 200×200(4㎡, 정사각) / balcony 400×100(4㎡, 긴 직사각). 면적 순: 발코니=화장실 < 침실 < 부엌 < 거실
- 도크: 방마다 scene JSON의 "dock" 필드로 명시 (가구 없는 구석 선택). dock이 없으면 config.home_for()가 원점 구석 벽 기준으로 자동 계산
- 기존 가구(pre_existing_furniture) 형식: `{"id": "table_1", "label": "table", "x", "y", "w", "d", "rot", "corners": [[x,y]×4], "model"(선택)}`. corners는 바닥면 네 모서리 좌표 (collision 계산·LLM 충돌 참고용, x·y·w·d·rot에서 자동 계산해 저장)
- **기존 가구는 장애물이자 배치 앵커** (양쪽 프롬프트에 명시됨). 원칙: **"중복하지 말되, 반드시 기여하라"** — 의도층은 이미 있는 가구가 채우는 항목은 목록에서 빼되 빈 목록 금지, 로봇이 곁에서 활동을 낫게 만드는 보완 가구를 최소 하나 나열 (거실에 책장 존재 + "책 읽고 싶어" → 책장 대신 책장 옆 독서 의자·독서대). 형태층은 앵커 우선 배치(테이블 앞 의자) + 기존 가구가 중심이면 보완 역할, 부족하면 곁에서 확장(보조 책장), 마땅한 앵커가 없으면 가용 공간 후보(open_area·벽가) 중 활동 성격에 맞는 자리 (user 좌표 없음)
- 의도층이 발화에서 space 추론 ("밥먹자"→kitchen, "양치하자"→bathroom). 애매하면 unknown → ask_clarification
- 전환 흐름: SceneState.load_scene(space) → collision 장애물 교체 → `scene_change` push → viewer가 기존 방 제거 후 buildRoom() → 로봇은 새 방 도크에서 시작
- 방 전환은 intent_type new_scene의 특수 케이스. history에 space 기록
- 시뮬 실험에서 로봇의 방간 이동은 "장면 전환"으로 표현 (참가자에게 안내)

## 11. 구현 순서 및 현황

1. ~~의도층 파이프라인~~ ✅ — main.py(음성 루프) + services/stt.py + agent.ask_intent + prompts.py(INTENT_PROMPT/SCHEMA/ROBOT_MECHANISM) 동작
2. ~~services 층~~ ✅ — collision(OBB/SAT) + scene(SceneState) + placement(find_placement v2), pytest 28개 + fuzz 검증
3. ~~tools/ 구현 + agent.run_agent~~ ✅ — strict 스키마 13개, tool 루프, main 라우팅(--text 모드 포함)
4. ~~시각 자가검증(VLM critic)~~ ✅ — render.py + CRITIC_PROMPT/SCHEMA + ask_user 훅 (실 API 검증은 실기기에서)
5. ~~viewer/~~ ✅ — FastAPI+WS 서버, three.js(방·가구·로봇 GLB 스왑, 조립식 fallback은 패널 힌지 애니메이션), HITL-2 승인 버튼. baseline 수동 모드는 TODO
6. models/ 가구 GLB 조달 + baseline 수동 모드 + 아파트 통합 뷰(선택) — 실험 직전 ← **다음 단계**
7. **로깅 (플러스알파, 마지막)** — logs/metrics.json: 거부된 제안 수, check_feasibility 실패 횟수, ask_clarification 발동, HITL-2 수정 턴, 시각 검증 라운드. 실험 지표(통과율·수정 턴 수)의 분모를 실행 중에 공짜로 수집

참고: three.js 뷰어의 프로토타입(정적 버전)은 이미 제작·검증됨 — 표준 라이브러리 http.server로 서빙, room JSON→방 그리기, GLB 로드·스케일·배치, 조립식 가구 fallback까지 동작 확인. 실제 구현 시 이 코드를 popup_viewer(FastAPI+WebSocket)로 승격.

## 12. 로봇 확정 스펙 (BoT²)

### 물리 스펙 (전부 확정)
- **구성**: 동일 기종 **2대** (BOT 1, BOT 2). 본체 크기는 변하지 않는다 (size 개념 없음).
- **본체**: 상자형 40 × 40 × 50 cm (가로×세로×높이). 윗면은 평평한 40×40 면 — 물건을 올리거나 사람이 앉을 수 있다. 바퀴로 자율 이동 + 제자리 회전. 방향성 있음 → state에 rot(도) 필수.
- **이중 스케일**: 높이 50cm는 성인에겐 앉는 높이, 아이에겐 테이블 높이.
- **가동 패널 2개**: 마주보는 두 측면의 **윗모서리 힌지**. 크기 40(폭) × 30(길이) cm. 각각 독립적으로 **0/45/90/135/180° 5단계**(이산)로만 젖혀진다:
  - 0° = 측면에 붙어 아래로 닫힘 / 45° = \ 아래로 기울어진 면 / 90° = 수평(상판이 옆으로 30cm 확장) / 135° = / 위로 기울어진 면 / 180° = 윗면 위로 수직으로 섬 (꼭대기 80cm)
- **수납**: 패널을 열면 본체 내부의 서랍장이 드러나 물건 정리 가능.
- **고정 패널 2개**: 나머지 두 측면. 자석식 인터랙션 모듈 부착부 (배치 에이전트 범위 밖).
- **조합**: **최대 2대**. 나란히(rot 동일) / 마주보고 패널 맞대기(rot 차 180°). 각도·조합·인원·활동에 따라 형태는 무한 — 정해진 가구 목록 없음.

### state 스키마 (확정)
```json
{"robot": "BOT 1", "active": "active", "x": 180, "y": 140, "rot": 90,
 "panel_left": 90, "panel_right": 0}   // panel 값은 {0,45,90,135,180}만 허용
// active: "active"=사용 중 / "inactive"=도크 대기(store_robot). inactive도 도크 바닥을 차지 → 충돌 계산 포함
```

### collision.py (구현 완료)
- **OBB(회전 사각형) + SAT** 충돌. footprint_rects(state) = 본체 40×40 + 돌출 패널 rect 목록
- 패널 바닥 돌출 = 30 × sin(각도) (0°·180°는 0, 90°는 30, 45°/135°는 약 21). 파생: 1대 풀 확장 상판 40×100cm — 테스트로 검증
- **slack 2cm**: 침투 깊이 2cm 이하는 충돌로 보지 않음 → 복합 가구의 '맞닿는 연결'은 그대로 통과
- panels_touching(a, side, b, side, tol=3, min_align=30): 패널 끝 맞닿음 + **측면 정렬(폭 겹침 ≥30cm)** — 모서리만 스치는 배치는 연결로 안 침
- clamp_to_bounds(패널 포함 경계), place_without_overlap(겹침 밀어내기, 실패 시 None→도크), validate_layout(경계+기존가구+로봇 겹침 → 문제 목록), snap_panel(임의 각도를 5단계로 강제)
- **rot 규약**: 도 단위 CCW. rot=0일 때 panel_left=-x쪽, panel_right=+x쪽. 뷰어(three.js rotation.y)와 이 규약을 반드시 일치시킬 것 (물리 상수는 config.py 단일 출처)

### services/scene.py — SceneState (구현 완료)
- 조회: environment()/furniture() → get_environment tool·의도층 room_furniture, states() → robot_states, recent(n) → get_recent_context
- 편집: transform(패널 snap 강제) → transform_robot, move(x,y,rot) → move_robot, store(도크 복귀+초기화 원자 처리) → store_robot. **transform/move 직후 자동 clamp** — 벽 앞에서 패널을 펼치면 방 안쪽으로 밀려남 (코드 보장)
- 버전: commit(전역 turn++, 절대 스냅샷, logs/session.json 저장) → commit_layout, revert_to(turn — 방이 다르면 방 전환 포함, LLM 생성 스킵) → revert_to
- 복구: resume() — 시작 시 session.json에서 방·로봇·history·turn 복원 (main.py 부팅 시 1회)
- pytest 28개 통과 (collision 12 + scene 6 + placement 10) + 무작위 fuzz 검증

## 13. 시각 자가검증 (VLM critic) — 계획

- **동기**: LLM은 좌표 목록만으로 공간 게슈탈트(밀집, 방향 어색함, 동선)를 잘 못 느낀다. 배치안을 이미지로 렌더해 비전 모델에게 조화를 묻는다 — HSM(3dlg-hcvc/hsm)의 VLM 배치 검증에서 착안
- **흐름**: 실행 tool 완료 → ask_user(HITL-2) 직전에 코드가 자동 발동 (tool 아님 — LLM이 건너뛸 수 없게):
  render(scene, robots) → VLM에 이미지 + 의도 요약 → structured 출력 `{ok, problems: [{target, issue, suggestion}]}` → 문제가 있으면 agent 루프에 피드백으로 주입해 수정, **최대 2라운드** → 그다음 HITL-2
- **렌더**: 1차는 탑다운 2D (PIL, collision.rect_corners 재사용 — 방·가구(라벨+앞방향 화살표)·로봇(본체+패널 각도) 주석 포함, ~50줄, 브라우저 불필요). 3D 시점이 필요해지면 뷰어의 canvas.toDataURL을 웹소켓으로 받는 업그레이드 (선택)
- **신규 파일**: services/render.py (순수 렌더, pytest 가능) + prompts.py에 CRITIC_PROMPT/CRITIC_SCHEMA + agent.py의 ask_user 경로에 훅
- **config.VISUAL_CHECK 토글** — 실험 조건(있음/없음)으로 분리해 HITL-2 수정 턴 감소 효과를 측정 가능 (논문 비교 포인트)
- 로깅(§11-7)과 연계: 라운드 수·problems 내용을 metrics에 기록
