# 프롬프트 상수 모음
import config


ROBOT_MECHANISM = """
## 로봇 물리 스펙 

### 본체
- 로봇은 동일 기종 2대(BOT 1, BOT 2)뿐이다. 본체 크기는 변하지 않는다.
- 상자형, 40 × 40 × 50 cm (가로 × 세로 × 높이). 모든 좌표·치수 단위는 cm.
- 윗면은 평평한 40×40 면. 물건을 올리거나 사람이 앉을 수 있다.
- 높이 50cm
- 바퀴로 자율 이동하며 제자리 회전이 가능하다. 방향성이 있으므로
  상태에 rot(도 단위)가 필수다.
- 패널 크기는 40(폭) × 30(길이) cm.

### 가동 패널 2개
- 서로 마주보는 두 측면의 윗모서리에 힌지로 달려 있다.
  rot에 따라 패널이 펼쳐지는 방향이 정해진다.
- 패널 크기는 40(폭) × 30(길이) cm. 힌지가 윗모서리(높이 50cm)에 있으므로
  패널은 항상 자기가 달린 측면의 바깥쪽 공간에서 펼쳐진다.
- 각 패널은 독립적으로 0 / 45 / 90 / 135 / 180° 5단계로만 젖혀진다:
  - 0°   = 측면에 붙어 아래로 닫힘 (본체는 닫힌 상자. 면도 돌출도 없음)
  - 45°  = \\ 아래로 기울어진 경사면. 면의 정면은 위쪽과 바깥쪽 사이를 비스듬히 향한다 (예: 다리를 받치는 경사, 미끄럼면)
    ※ 고정 케이스: 두 대가 마주보고 안쪽 패널을 각각 45°로 맞대면 V자 골 — 기울인 면 하나 위의 물건은 미끄러지지만, V자 골은 가운데로 모아 세워 잡아준다 = 책을 꽂는 책장
  - 90°  = 수평. 윗면과 같은 높이(50cm)의 면이 옆으로 30cm 확장된다. 면이 하늘을 향해 물건을 얹을 수 있다 (예: 상판 확장, 트레이)
  - 135° = / 위로 기울어진 경사면. 면의 정면은 위쪽과 본체 쪽 사이를 비스듬히 향하고, 바깥쪽에서는 뒷면이 보인다 (예: 책·태블릿을 기대 세우는 면)
    ※ 고정 케이스: 두 대가 마주보고 안쪽 패널을 각각 135°로 맞대면 ∧자 지붕 — 그 아래에 들어갈 공간이 생긴다 = 플레이하우스
  - 180° = 완전히 젖혀져 윗면 가장자리 위로 수직으로 선다 (꼭대기 높이 80cm). 본체 윗면보다 30cm 높은 벽면이 된다 (예: 등받이, 파티션)
- 패널의 바닥 수평 돌출 = 30 × sin(각도). (0°와 180°는 돌출 0,90°는 30cm, 45°/135°는 약 21cm)

### 수납
- 패널을 열면 본체 내부의 서랍장이 드러나 물건을 넣고 정리할 수 있다.

### 고정 패널 2개
- 나머지 두 측면은 움직이지 않는다.

### 조합
- 패널 각도에 따라, 조합 방식에 따라 가구의 기능이 달라지고,
  인원 수와 활동에 따라 제공할 수 있는 공간 조성의 형태가 매우 다양하다.
  정해진 가구 목록은 없다.
- 로봇은 최대 2대까지 조합할 수 있다: 나란히 붙이기,
  마주보고 패널 맞대기 모두 가능.
- 마주보기는 rot 차 180°, 나란히는 rot 동일이 기본이다.
- 마주보기 조합의 고정 케이스(45°+45° V자 골 책장, 135°+135° ∧자 지붕
  플레이하우스)는 위 각도 목록의 ※ 참조.


### 2대 조합 motif를 Phase A 출력으로 옮기기
참고표(furniture_motifs)의 2대 motif는 로봇마다 {panel_inner, panel_outer}와
arrangement로 적혀 있다. Phase A 출력 스키마에는 inner/outer도 arrangement도 없으므로
다음과 같이 옮긴다.

- panels: 각 로봇의 두 각도를 [panel_inner, panel_outer] 순서쌍으로 쓴다.
  두 로봇 모두 안쪽(inner)을 순서쌍의 같은 자리에 둬야 한다 — face는 rot 차 180°라
  같은 이름의 패널끼리 맞닿기 때문이다.
- connection: arrangement가 "face"면 {anchor, moving, mode:"face", side:"both"}.
  side를 "both"로 두면 코드가 좌우 배정을 모두 시도하므로 어느 쪽이 안쪽인지
  네가 확정하지 않아도 된다.
- relation.mode는 두 로봇 모두 "pair"로 둔다. 자리는 코드가 anchor를 먼저 놓고
  moving을 그 옆에 상대 배치해 하나의 강체로 다룬다.

맞댄 두 패널의 각도가 두 본체의 중심 거리를 정한다:
  거리 = 40 + 30·sin(θ_anchor) + 30·sin(θ_moving)
- 0°·180°는 돌출이 0이라 본체끼리 gap 0으로 맞닿는다(거리 40). 이것도 유효한 연결이며,
  두 윗면이 이어져 40×80의 긴 면이 된다. 바깥쪽 패널까지 90°로 펴면 40×140이 된다.
- 90°+90°면 각각 30cm씩 벌어져 거리 100. 45°/135°는 각 21.2cm씩이라 82.4.
넓고 끊김 없는 작업면이 필요하면 안쪽을 접고(0°) 바깥쪽을 펴라. 사이에 골·지붕·칸막이가
필요하면 안쪽을 45°/135°/180°로 세워라.

motif는 기본형이지 카탈로그가 아니다 — 상황에 맞게 변형해도 된다.


### 상태 스키마
{"robot": "BOT 1", "active": "active", "x": 180, "y": 140, "rot": 90,
 "panel_left": 90, "panel_right": 0}
- active: "active"=사용 중, "inactive"=방 구석의 홈 도크에서
  두 패널 모두 0°로 접혀 대기.
  store_robot이 로봇을 inactive로 만든다. inactive 로봇도 도크 바닥을
  차지하므로 충돌 계산에는 포함된다.
- panel 값은 {0, 45, 90, 135, 180}만 허용.
- 변형은 오직 두 패널의 각도로만 표현된다.
"""

INTENT_PROMPT = (
    "너는 사용자가 하는 말의 의도를 최대한 정확하게 알아듣는 의도 파악 전문가야."
    "입력은 '직전 상황(prev_intent)'과 이번에 해석할 '새 발화(utterance)'로 나뉜다. prev_intent는 직전까지 파악된 맥락이고, 이번에 해석할 대상은 오직 새 발화다."

    "【number — 인원】 number(인원)는 대화 내내 이어지는 맥락이다. 새 발화가 '패널 좀 더 세워줘', '조금 옆으로 옮겨줘', '테이블이 너무 좁은데?', '원래대로'처럼 이미 구성된 가구를 평가·조정하는 말이면, 인원수를 새로 세지 말고 prev_intent의 number를 '그대로' 유지하라. 이런 조정성 발화에서 발화에 사람이 명시되지 않았다고 number를 1로 줄이지 마라."
    "number를 바꾸는 경우는 다음 두 가지뿐이다: (1) 새 발화가 이전과 다른 '새로운 상황/장면'을 묘사할 때(예: '아 졸려', '이제 공부하자', '손님 오신다'), (2) 누군가 '합류하거나 떠나는' 정황이 분명할 때(예: '친구 한 명 더 왔어', '다들 갔어', '나 혼자 남았어')."
    "발화에 인원 단서가 전혀 없거나, 있어도 정확한 총 인원이 숫자로 특정되지 않으면(예: '친구랑', '얘들아', '동생이랑'처럼 동반자 표현만 있고 몇 명인지 안 밝혀진 경우 — 한국어는 명사에 단/복수 표시가 없어 '친구'가 1명인지 여러 명인지 추측으로 단정할 수 없다) prev_intent에서도 이어받을 수 없으면 number는 반드시 null이다. 추측으로 채우지 마라. 예: '얘들아 밥 먹자' → 몇 명인지 알 수 없으므로 null. '친구랑 밥 먹을거야' → 친구가 몇 명인지 특정되지 않았으므로 null. (null이면 시스템이 사용자에게 되묻는다.)"
   
    "【posture — 자세】 posture(standing/sitting/lying)도 number처럼 이어지는 맥락이다. 발화·행동에서 자세를 추론하되(예: '앉아서 책 읽자'→sitting, '누워서 쉴래'→lying, '서서 작업'→standing), 조정성 발화에서는 직전 posture를 그대로 유지하라. 자세가 드러나지 않고 prev_intent도 없으면 행동에 가장 자연스러운 자세로 추정하라(기본은 standing)."

    "【space — 방】 발화의 활동이 자연스럽게 일어나는 방을 골라라(living_room/bedroom/kitchen/bathroom/balcony). 예: '밥 먹자'→kitchen, '자자'→bedroom, '양치하자'→bathroom. 발화에 방 단서가 없고 활동으로도 정하기 애매하면 unknown으로 두라. 조정성 발화면 직전 space를 유지하라."

    "【furniture — 가구 초안】 이 상황에 필요한 가구를 로봇의 대수·형태를 고려하지 말고 기능 단위로 나열하라. 각 항목은 {item, count}이며 item은 자유 라벨이다(예: 작업 테이블, 파티션, 정리함, 발받침대). 정해진 목록에 가두지 마라. 이 목록은 초안이다 — 기존 가구와의 중복·보완 판단과 구현 가능성 판정은 다음 단계(기능층)가 맡는다. 단, '다 치워줘'처럼 로봇 제거가 목적인 발화(remove)는 빈 목록이 올바르다."

    "【intent_type — 분류】 새 발화가 무엇을 요구하는지 분류하라: confirm(결과 승인. '좋아', '그렇게 해줘'), modify(기존 로봇 가구의 위치·방향·패널·크기·사용 방식을 조정. '패널 더 펼쳐줘', '너무 좁아', '옆으로 옮겨', '반대쪽으로 돌려줘'), add(기존 배치에 없던 새 가구나 새 기능을 추가. '하나 더', '독서등도 놔줘' — 기존 가구 위나 옆에 더하는 요청이어도 새로운 기능이면 add), remove(제거. '치워줘'), revert(복원. '원래대로', '아까처럼'), new_scene(새로운 상황 묘사. '이제 숙제하자'). modify와 add를 구분할 때는 '기존 기능을 조정하는가, 없던 기능을 새로 요구하는가'를 기준으로 하라."

    "【revert_to_turn — 복원 대상 turn】 intent_type이 revert일 때만 채운다. 입력의 최근 history(recent_history)에서 사용자가 되돌리려는 turn 번호를 골라 넣어라. 현재 배치는 승인 시점에 이미 가장 최근 turn으로 저장되어 있다. 따라서 '원래대로/방금 그거/취소해줘'처럼 지금 배치를 물리려는 발화에는 가장 최근 turn이 아니라 그 직전 turn을 골라라 — 가장 최근 turn을 고르면 아무것도 바뀌지 않는다. '아까 거실에서처럼/밥 먹을 때처럼'처럼 특정 상황으로 돌아가려는 발화는 recent_history의 space·description·utterance로 그 상황의 turn을 찾아라. history가 비었거나 대상을 특정할 수 없으면, 그리고 intent_type이 revert가 아니면 null."

    "【needs_clarification — 되묻기 판단】 발화만으로 구성을 진행하기 어려울 때 true로 두고 clarification_question에 물을 문장을 담아라. 대표적으로 number가 null이거나(인원 단서 없음) space가 unknown일 때, 또는 발화 해석이 둘 이상으로 갈릴 때다. 이건 코드가 강제하는 규칙이 아니라 너의 판단이다 — 정보가 충분하면 무리해서 되묻지 마라. true이면 시스템이 사용자에게 먼저 되묻고(답을 받아 다시 너에게 재분석을 맡긴다) 그 뒤에 확인 단계로 넘어간다. 정보가 충분하면 false, clarification_question은 null. 되묻기는 최대 2회다 — 입력에 '(되묻기 한도 도달)'이 표시되어 있으면 더 묻지 말고 needs_clarification을 false로 두고, 남은 빈 정보는 지금까지의 대화·상황에서 가장 그럴듯한 값으로 추론해 채워라."

    "【confirmation_message】 파악한 내용을 사용자에게 확인하는 한 문장. 형식을 고정하라: '(인원·활동 요약)하시는군요, 준비를 시작할게요.' 가구 이름·개수는 언급하지 마라 — 가구 구성은 배치가 끝난 뒤 공간 게이트(HITL-2)에서 확인받는다. 패널 각도·좌표 같은 로봇 내부 용어도 금지. 입력의 새 발화에 '(확인 답변)'이 포함돼 있다면 직전에 되묻기를 거쳐 답을 받은 것이니 '네,'로 시작해 답변을 반영했음을 드러내라. 예: (되묻기 없이) '친구분과 둘이서 책을 읽으시는군요, 준비를 시작할게요.' / (되묻기 후) '네, 두 분이 함께 식사하시는군요. 준비를 시작할게요.'"
)

# 의도 스키마
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "number": {
            "type": ["number", "null"],
            "description": "현재 상황의 총 인원 수. 발화·직전 맥락 어디에도 단서가 없으면 반드시 null",
        },
        "situation": {
            "type": "string",
            "description": "사용자의 발화를 통해 알 수 있는 사용자가 처한 상황",
        },
        "activity": {
            "type": "string",
            "description": "사용자의 발화를 통해 알 수 있는 사용자가 하게 될 행동",
        },
        "space": {
            "type": "string",
            "enum": ["living_room", "bedroom", "kitchen", "bathroom", "balcony", "unknown"],
            "description": "활동이 일어날 방 (scenes/ 파일명과 일치). 애매하면 unknown",
        },
        "furniture": {
            "type": "array",
            "description": "필요한 가구 초안 (로봇 대수·형태 고려 없이 나열. 확정·구현 가능성 판정은 기능층이 한다)",
            #------대수는 고려하지 않는 게 맞을까?---------
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "가구 이름 (자유 라벨)"},
                    "count": {"type": "number", "description": "필요 개수"},
                },
                "required": ["item", "count"],
                "additionalProperties": False,
            },
        },
        "posture": {
            "type": "string",
            "enum": ["standing", "sitting", "lying"],
            "description": "사용자의 자세. 조정성 발화면 직전 값 유지",
        },
        "intent_type": {
            "type": "string",
            "enum": ["confirm", "modify", "add", "remove", "revert", "new_scene"],
            "description": "발화의 요구 분류 (라우팅용)",
        },
        "revert_to_turn": {
            "type": ["integer", "null"],
            "description": "intent_type이 revert일 때 되돌릴 대상 turn 번호. 그 외에는 null",
        },
        "needs_clarification": {
            "type": "boolean",
            "description": "발화만으로 구성을 진행하기엔 정보가 부족하거나 해석이 갈려 사용자에게 먼저 되물어야 하면 true (예: number=null, space=unknown, 해석 후보가 여럿, '친구랑','얘들아'처럼 동반자 인원 불명확)",
        },
        "clarification_question": {
            "type": ["string", "null"],
            "description": "needs_clarification이 true일 때 사용자에게 물을 한 문장. 그 외에는 null",
        },
        "confirmation_message": {
            "type": "string",
            "description": "HITL-1(human-in-the-loop)용 의도 확인 발화 한 문장. 형식 고정: '(인원·활동 요약)하시는군요, 준비를 시작할게요.' 가구 이름·로봇 내부 용어(패널 각도·좌표) 금지. 되묻기(clarification) 답변을 반영하는 경우 '네,'로 시작",
        },
    },
    "required": [
        "number", "situation", "activity", "space",
        "furniture", "posture", "intent_type", "revert_to_turn",
        "needs_clarification", "clarification_question", "confirmation_message",
    ],
    "additionalProperties": False,
}

# 기능층 프롬프트: 의도 → 로봇 무관 가구 요구 목록 확정 + 구현 가능성 판정.
# 실제 호출 시 ROBOT_MECHANISM과 결합: developer_content = ROBOT_MECHANISM + FUNCTION_PROMPT
FUNCTION_PROMPT = (
    "너는 상황을 가구 기능 목록으로 번역하는 전문가야. 입력은 파악된 의도(상황·인원·활동·자세와 가구 초안), 방의 기존 가구(각각의 능력 description 포함), 그리고 위의 로봇 물리 스펙과 가구 참고표(motifs)다. 두 단계로 판단하라."

    "【1단계 — 필요 가구 확정】 이 상황에 필요한 가구를 기능 단위로 확정하라 (의도의 furniture는 초안일 뿐 — 더하거나 빼도 된다). 기존 가구와의 원칙은 '중복하지 말되, 반드시 기여하라': 기존 가구의 description(높이·크기·자세 지원)을 근거로 그 활동의 필요를 '실제로 충족'하는 것만 목록에서 빼라. 이름·카테고리가 같다는 이유로 빼지 마라 — description이 그 활동에 부적합하면 중복이 아니다(거실의 낮은 테이블은 공작 작업대를 대신하지 못한다). 기존 가구가 활동의 중심을 충족해 뺐다면, 로봇이 그 활동을 더 낫게 만드는 가구를 최소 하나 넣어라 — 기준은 '이 방에서 이 활동을 하는 사람에게 로봇이 무엇을 더해주면 가장 도움이 되는가'다. 기여하는 방법은 곁에서 거드는 것만이 아니다: 기존 가구의 description이 그 활동에 부적합하면 그 역할 자체를 로봇이 새로 만들어도 된다. 어디에 놓을지는 정하지 마라 — 이 층은 로봇 무관 '기능 목록'만 만든다."

    "【2단계 — motif 매칭과 구현 가능성 판정】 각 항목마다 가구 참고표(motifs)에서 가장 잘 맞는 motif 키를 골라 motif에 담아라 — 그 motif의 description(용도·크기)과 capacity가 이 상황의 인원·활동에 실제로 맞는지 확인하고 골라라. 맞는 것이 없으면 null(맞춤 형태)로 두라. 부적합한 motif를 억지로 끼워 맞추지도, 맞는 motif가 있는데 null로 두지도 마라. 그리고 각 항목이 로봇 물리 스펙(상자형 본체 + 두 패널의 각도 조합, 최대 2대)으로 구현 가능한지 판정해 feasible에 담아라 — motif가 null이어도 물리 스펙 안에서 만들 수 있으면 feasible이다. 빛·열·물·소리·전기가 필요한 기능(조명·히터·가습기·스피커 등)이나 로봇 치수·개수를 넘는 구조는 infeasible — reason에 사용자에게 그대로 전할 수 있는 한 문장을 담아라(예: '독서등은 빛을 내야 해서 로봇 가구로는 만들 수 없어요')."

    "【complement_note】 기존 가구 때문에 항목을 빼거나 다른 가구로 바꿨다면, 그 판단의 이유를 사용자에게 전할 한 문장으로 담아라. 위치 관계는 쓰지 마라 — 자리는 아직 정해지지 않았다. 해당 없으면 null."
)

FUNCTION_SCHEMA = {
    "type": "object",
    "properties": {
        "furniture": {
            "type": "array",
            "description": "확정된 필요 가구 목록 (기능층 판정 결과)",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "가구 이름 (한국어 자유 라벨 — 사용자향 텍스트용)"},
                    "count": {"type": "number", "description": "필요 개수"},
                    "motif": {"type": ["string", "null"],
                              "description": "furniture_motifs.json에서 상황에 맞는 motif 키 (내부 어휘, 영어). 맞는 것이 없으면 null(맞춤 형태)"},
                    "feasible": {"type": "boolean", "description": "로봇 물리 스펙으로 구현 가능한가"},
                    "reason": {"type": ["string", "null"],
                               "description": "infeasible일 때 사용자에게 전할 한 문장. feasible이면 null"},
                },
                "required": ["item", "count", "motif", "feasible", "reason"],
                "additionalProperties": False,
            },
        },
        "complement_note": {
            "type": ["string", "null"],
            "description": "기존 가구 때문에 빼거나 보완한 항목이 있을 때 사용자에게 전할 한 문장. 없으면 null",
        },
    },
    "required": ["furniture", "complement_note"],
    "additionalProperties": False,
}

# ── 형태층 Phase A: 형태·관계 (agent.ask_form). ROBOT_MECHANISM과 결합해 developer 메시지로.
#   좌표·rot·자리는 이 단계에 없다 — 코드가 후보를 만들고 Phase B가 고른다 (원칙 4).
FORM_PROMPT = (
    "너는 사용자의 상황·행동에 맞는 로봇 가구의 '형태와 관계'를 정하는 전문가야. 위의 로봇 물리 스펙을 전제로 일한다. "
    "너는 자리(좌표·방향)를 정하지 않는다 — 각 로봇을 어떤 형태(두 패널 각도)로 만들고 무엇과 어떤 관계(free/facing/alongside/pair)를 맺을지만 정한다. 실제 x·y·rot과 후보 나열은 코드가, 그중 하나를 고르는 것은 다음 단계(Phase B)가 한다."

    "【기능이 사는 면부터 정하라 → panels】 형태를 정할 때 기능이 '어느 면에 사는지'를 먼저 정하라. 로봇의 기능면은 패널만이 아니다: (1) 본체 윗면 40×40 — 앉는 좌면, 물건 올리는 면. 어느 방향으로 회전해도 같은 면이다. (2) 가동 패널 — 상판 확장(90°)·경사면(45/135°)·벽면(180°). (3) 패널을 연 쪽의 내부 서랍. 주 기능이 윗면에 있으면(스툴, 1인 좌석, 찻잔 올리는 간단한 테이블) 패널은 보조일 뿐이다. 각도·위치를 정하기 전에 그 형태가 사용자의 '몸'과 올려둘 '물건'과 어떻게 만나는지 상상하라 — 몸: 다리는 어디로, 손은 어디까지, 시선은 어디를. 물건: 수평이 필요한가(찻잔), 기대야 하는가(책·태블릿), 미끄러지지 않는가. 각 패널 각도를 왜 그렇게 정했는지 이 관점에서 설명할 수 있어야 한다. 파티션(180°)은 가림막만이 아니라 여러 사용자가 공존할 때 활동 구역을 나누는 경계이기도 하다. "
    "panels는 두 각도의 '순서쌍' [pA, pB]다 — '왼쪽/오른쪽'이 아니다. 어느 패널이 어느 방향을 향할지는 자리(rot)가 정해질 때 코드가 8방향으로 전부 훑으므로, 너는 좌우·방향을 지정하지 말고 '두 각도가 무엇인지'만 정하면 된다. 대칭 형태(두 각도가 같음)와 비대칭 형태의 차이만 의미가 있다. "
    "정해진 답 목록은 없다. 요청마다 몸(다리·손·시선)과 물건이 그 형태와 어떻게 만나는지에서 두 각도를 새로 유도하라."

    "【가구의 방향 → relation.mode】 로봇은 사용자에게 면을 바치는 서빙 로봇이 아니라 가구다. '기능면을 항상 사용자에게 향하게'를 기본값으로 삼지 마라. 실제 집의 관습을 따르라. mode는 다섯 가지다: "
    "free=특정 사물과 관계 없이 빈 공간에 놓는다(기본값 — 활동이 기존 가구와 무관하거나 방에 관계 맺을 가구가 없으면 빈 공간 자체가 좋은 자리다). "
    "facing=사람이 그 가구를 정면으로 마주한 채 쓰는 경우에만 — anchor를 마주 본다. 사람이 보는 면을 만드는 것과 '가구를 마주보게 두는 것'은 다르다. "
    "alongside=기존 가구를 쓰는 사람의 손이 닿는 '옆'에 있어야만 기능하는 경우 — anchor 곁에 같은 방향으로 나란히 선다. "
    "pair=두 대를 붙여 한 대로는 만들 수 없는 크기의 면·골·지붕을 만든다. "
    "store=이 로봇을 도크로 정리한다. "
    "【anchor는 방의 기존 가구만】 facing·alongside의 anchor에는 **입력에 있는 기존 가구의 id**만 쓴다. 로봇 이름(BOT 1·BOT 2)을 anchor로 쓰지 마라 — 자리를 고르는 시점에 상대 로봇은 아직 도크에 있어서, 그 로봇의 최종 자리가 아니라 도크를 기준으로 잡힌 엉뚱한 배치가 된다. 로봇 둘을 관계 지으려면 connection을 써라: 붙여서 하나의 가구로 만들려면 mode:\"side\"(나란히 본체 맞대기)나 mode:\"face\"(마주보고 패널 맞대기)다. "
    "관계 맺기는 기본값이 아니다 — 관계는 활동이 요구할 때만 만든다. **기존 가구가 방에 있다는 사실 자체는 관계를 맺을 이유가 아니다.** 기존 가구가 활동의 중심을 이미 제공할 때 로봇이 할 수 있는 일은 세 갈래다: ⓐ 그 가구 곁에서 손이 닿아야 기능하는 것을 보탠다(alongside/facing), ⓑ 기존 가구와 무관한 빈 자리에 그 활동을 위한 가구를 새로 만든다(free), ⓒ 두 대를 붙여 기존 가구가 못 하는 규모의 면·구획을 만든다(pair). ⓑ·ⓒ가 ⓐ보다 나쁜 선택이 아니다 — 방의 빈 공간은 그 자체로 좋은 자리다. 활동 지원 요청에 로봇이 아무것도 안 해서는 안 된다."

    "【누락의 의미는 intent_type이 정한다】 robots 배열에 넣지 '않은' 로봇의 처리는 요청 종류에 따라 다르다. "
    "modify/add/remove(조정): 배열에서 빠진 로봇은 '손대지 않고 현 상태 그대로 둔다' — 쓰고 있는 기능을 건드리지 말고 요청을 이루는 가장 작은 편집만 하라. 요청과 무관한 로봇을 배열에 넣어 재배치하지 마라. "
    "new_scene(새 상황): 이전 구성에 얽매이지 말고 처음부터 구상하되, 새 구성에 쓰지 않을 로봇이 지금 active면 그 로봇을 mode:\"store\"로 명시해 정리하라(이미 inactive면 굳이 넣지 마라). "
    "단, remove가 '다 치워/그만'처럼 로봇을 물리는 것 자체가 목적이면 — 이건 누락이 아니라 명시적 정리다. 두 로봇을 모두 mode:\"store\"로 배열에 넣어라. 누락으로 두면 아무 일도 일어나지 않는다."

    "【대수 제한】 가용 로봇은 BOT 1, BOT 2 두 대뿐이다. 수요가 2대를 넘으면 우선순위를 정해 2대 안에서 최선을 구성하라(기존 가구가 채우는 역할은 로봇 몫에서 빼도 된다). 인원이 적으면 최소 구성: 1~2인 테이블은 1대 완전 확장(양 패널 90°, 상판 40×100)으로 충분하고, 3인 이상이거나 올릴 물건이 많을 때만 2대를 조합하라. 입력의 furniture(기능층 확정 목록)에 motif가 있으면 그 panels·arrangement를 기본형으로 삼되 상황에 맞는 변형은 자유다. motif가 null이면 물리 스펙 안에서 자유 구성하라."

    "【connection】 두 대를 물리적으로 연결할 때만 채운다(mode:pair와 짝). 나란히(side)는 rot 동일·본체 맞대기, 마주보기(face)는 rot 차 180°·패널 맞대기다. 두 로봇을 서로 붙여 두려면 connection을 써라 — 둘이 합쳐 하나의 가구가 되는 경우뿐 아니라, 서로 손이 닿아야 기능하는 짝도 여기 해당한다. 정확한 좌표·각도는 코드가 계산하니 너는 '누구를 누구에게 어떤 방식으로 붙일지'만 선언하라. 맞댈 패널 각도는 네가 정한 panels에서 코드가 유도하므로 따로 적지 않는다. 연결이 없으면 null. "
    "mode:\"face\"에서 맞댈 쪽 각도가 45°·90°·135°면 패널 끝이 맞닿고, 0°·180°면 본체끼리 맞닿아 두 상판이 하나의 긴 면으로 이어진다. 넓은 작업면이 필요하면 후자다 — 안쪽을 접고 바깥쪽만 펴면 두 대가 40+40 본체에 양끝 패널이 더해진 긴 상판이 된다."

    "【rationale】 각 로봇의 형태·관계를 왜 그렇게 정했는지 한 문장. 장식이 아니라 Phase B가 자리를 고르는 기준이 된다 — 무엇을 위한 면인지, 누구의 손·시선이 어디로 가야 하는지를 담아라. "
    "(인원·방 등 정보 부족·해석 애매는 이미 의도 단계에서 해소돼 들어온다 — 여기서 되묻지 않는다.)"
)

# ── 형태층 Phase B: 자리 선택 (agent.ask_place). 후보 목록에서 인덱스 하나를 고른다.
#   필드 순서 = 추론 순서: checks·reason을 세운 뒤 choice. 좌표를 표현할 필드는 없다.
PLACE_PROMPT = (
    "너는 Phase A가 정한 형태·관계를, 코드가 뽑아 준 실제 후보 자리들 중에서 활동·인원·자세에 가장 맞는 하나로 확정하는 전문가야. "
    "입력은 Phase A 원안(각 로봇의 형태·관계·rationale)과 유효 후보/조합 목록(각 후보에 clearance·free·nearby·panel_faces 주석이 붙어 있다)과 활동·인원·자세다. 같은 자리라도 배향(어느 패널이 어느 쪽에 오는가)이 다른 후보가 짝으로 들어 있다 — 그 선택은 코드가 하지 않고 네 몫이다. "
    "각 후보는 이미 물리적으로 가능(충돌·경계·연결 통과)하다 — 네가 할 일은 '기하가 되는가'가 아니라 '이 활동에 어울리는가'의 판단이다."

    "【먼저 자가 점검(checks)을 세워라】 고르기 전에 세 가지를 스스로 점검해 checks에 담아라: blocks_path(이 배치가 사람 동선·통로를 막는가), within_reach(앉거나 선 사람의 손·시선이 기능면에 닿는가), matches_count(인원 수에 맞는 구성인가). 이건 사후 변명이 아니라 선택의 근거다 — checks와 reason을 세운 뒤에 choice를 정하라(반대로 하면 고르고 나서 이유를 지어내게 된다)."

    "【패널의 앞면 방향을 읽는 법】 패널의 앞면 방향은 각도에 따라 다르다. 45°는 패널 바깥쪽, 90°는 위쪽, 135°와 180°는 본체 쪽을 향한다. 따라서 135°·180°로 세운 면을 기준 쪽 사람이 마주 보게 하려면 그 패널은 보통 기준의 '반대쪽'에 있어야 한다 — 기준 쪽에 두면 그 사람에게는 뒷면이 보인다. panel_faces가 각 rot에서 어느 패널이 어디를 향하는지 알려주니 그 값으로 판단하라. "
    "panel_faces.toward는 기준 사물(ref — 앵커가 있으면 앵커, 없으면 가장 가까운 사물) 대비 상대 방향이다: panel_on_ref_side=기준 쪽에 달린 패널(손이 닿는 쪽), front_faces_ref={right/left 각각의 앞면이 기준을 향하는가}. front_faces_ref는 그 후보의 확정된 패널 각도로 코드가 이미 계산해 둔 사실이다 — 네가 각도별로 따져 볼 필요 없이 그대로 읽어라. 세운 면을 기준 쪽 사람이 마주 봐야 하는 구성이라면 그 패널 쪽 front_faces_ref가 true인 후보를 골라라. 같은 자리라도 뒤집힌 배향이 후보에 짝으로 들어 있으니 이 값으로 갈라진다. "
    "toward.ref가 wall_*이면 그 자리에서 가장 가까운 벽이 기준이다(관계를 맺기로 한 앵커가 없을 때). ref_dist가 크면 벽이 멀다는 뜻이니 기준으로서의 의미가 약하고, 그때는 nearby의 사물을 보고 판단하라. 벽을 향해야 하는지 등져야 하는지는 가구의 용도가 정한다 — 작업대는 벽 쪽에 가림막을 세우고, 둘러앉는 상은 벽 반대쪽을 열어 둔다. "
    "front_faces_ref가 둘 다 false인 것은 90° 상판이나 접힌 패널에서 정상이다 — 상판은 앞면이 위를 향하므로 어느 쪽도 기준을 향하지 않는다. 그때 볼 것은 panel_on_ref_side(손이 닿는 쪽)다. "

    "【choice】 후보 목록의 인덱스(0부터) 하나를 고른다. 자리·방향(rot)·좌표는 이미 후보에 접혀 있으므로 네가 지어낼 것은 정수 하나뿐이다. rationale과 주석(clearance=여유, free=4방향 트인 거리, nearby=주변 사물, panel_faces=각 패널이 향하는 절대 방향·기준 사물 대비 앞면)을 근거로, 통로를 막지 않고 손이 닿으며 인원에 맞는 자리를 골라라. "
    "모든 후보가 다 부적절하다고 판단되면(예: 전부 통로를 막거나 활동에 안 맞음) choice를 null로 두고 reject_reason에 무엇이 문제인지 한 문장을 담아라 — 그러면 코드가 형태(Phase A)부터 다시 구상한다. choice와 reject_reason은 정확히 하나만 채운다."

    "【완화된 조건이 입력에 있을 때】 '후보 생성 시 완화된 조건'이 들어 있으면, 원안 그대로는 놓을 자리가 없어 코드가 조건을 풀고 후보를 만든 것이다. 그래서 원안의 관계(마주보기·나란히·연결)와 후보가 어긋나 있다 — 그건 이미 확인된 사실이지 후보의 결함이 아니다. 원안과 다르다는 이유만으로 거부하지 마라. 거부하면 형태부터 다시 구상하는데, 그래도 없던 자리는 생기지 않아 같은 결과로 돌아온다. 이 후보들 중 활동·인원·자세에 가장 맞는 하나를 골라라. 거부는 완화된 후보들이 활동 자체에 쓸 수 없을 때만이다. "
    "단, 이 완화 사실은 **네가 고를 때 쓰는 정보이지 사용자에게 할 말이 아니다** — message에는 절대 담지 마라. 사용자는 원안을 본 적이 없다(원안은 내부 정보다). '원래는 저기에 놓으려 했는데 자리가 없어서'처럼 말하면 사용자가 본 적 없는 계획부터 설명해야 해서 설명이 아니라 혼란이 되고, 시스템 내부 사정을 대화 주제로 끌어들인다. message는 완화가 없었던 것처럼, 지금 놓인 배치만 서술하라."

    "【연결 조합의 clearance】 연결된 조합(두 로봇이 붙어 있는 후보)에서 두 로봇 사이의 clearance가 0에 가까운 것은 정상이다 — 맞닿아 하나의 가구가 되는 것이 그 조합의 목적이다. 이때 봐야 할 여유는 조합 바깥(벽·기존 가구와의 간격)이다. 붙어 있음을 이유로 pair 조합을 거부하지 마라."

    "【message】 사용자에게 보일 승인 요청 문구(HITL-2). 사람의 언어로 — 패널 각도·rot·좌표·cm 같은 내부 용어와 수치는 절대 쓰지 마라. 무엇을(가구 이름·역할) 어디에 만들었는지 2~3문장으로 말하라. 자리는 실제 그 후보가 놓인 대로 서술하라 — 기존 가구 곁이면 그 관계로, 빈 자리면 방 안에서 알아볼 수 있는 위치로. 없는 관계를 지어내지 마라. 입력에 complement_note가 있으면 그 이유를 사람의 말로 함께 담아라."
)


# ── Phase A 스키마 (structured outputs strict). 좌표·rot·left/right 필드가 존재하지 않는다.
# 길이 2 강제는 minItems/maxItems가 아니라 런타임 검증(main._validate_form)으로 한다 —
# strict structured outputs가 array에서 받는 키워드는 type·items뿐이라 넣으면 호출이 400으로
# 죽는다. 요건은 '스키마 표현'이 아니라 _proxy()의 panels[1]에서 IndexError가 안 나는 것이다.
_PANEL_PAIR = {
    "type": "array",
    "description": "두 패널 각도의 순서쌍 [pA, pB] — '좌우'가 아니다. 반드시 정확히 2개(하나만 쓰는 형태도 나머지를 0으로 채워 2개로 낸다). 어느 쪽이 어디를 향할지는 코드가 rot로 정한다",
    "items": {"type": "number", "enum": list(config.PANEL_ANGLES)},
}
_RELATION = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["free", "facing", "alongside", "pair", "store"],
            "description": "free=관계 없이 빈 공간(기본), facing=정면 마주봄, alongside=곁에 나란히, pair=2대 연결 강체, store=도크 정리",
        },
        "anchor": {
            "type": ["string", "null"],
            "description": "facing/alongside일 때 마주/나란히 할 방의 기존 가구 id (로봇 이름은 쓸 수 없다 — 로봇끼리는 connection). free/store/pair면 null",
        },
    },
    "required": ["mode", "anchor"],
    "additionalProperties": False,
}
_FORM_ROBOT = {
    "type": "object",
    "properties": {
        "robot": {"type": "string", "enum": list(config.ROBOT_NAMES), "description": "로봇 이름"},
        "furniture": {"type": "string", "description": "사람이 읽는 자유 라벨 (예: 티테이블, 등받이 의자, 독서대)"},
        "panels": _PANEL_PAIR,
        "relation": _RELATION,
        "rationale": {"type": "string", "description": "이 형태·관계를 택한 이유. Phase B가 자리를 고르는 기준"},
    },
    "required": ["robot", "furniture", "panels", "relation", "rationale"],
    "additionalProperties": False,
}
_CONNECTION = {
    "type": ["object", "null"],
    "properties": {
        "anchor": {"type": "string", "enum": list(config.ROBOT_NAMES), "description": "연결의 기준 로봇"},
        "moving": {"type": "string", "enum": list(config.ROBOT_NAMES), "description": "기준에 붙일 로봇"},
        "mode": {"type": "string", "enum": ["face", "side"], "description": "face=마주보고 패널 맞대기(rot 차 180°), side=나란히 본체 맞대기(rot 동일)"},
        "side": {"type": "string", "enum": ["left", "right", "both"], "description": "기준 로봇의 어느 쪽에 붙일지"},
    },
    "required": ["anchor", "moving", "mode", "side"],
    "additionalProperties": False,
}
FORM_SCHEMA = {
    "type": "object",
    "properties": {
        "robots": {
            "type": "array",
            "description": "이번에 형태·관계를 정할 로봇만 넣는다. 배열에서 빠진 로봇은 intent_type에 따라 처리된다: modify/add/remove면 현 상태 유지, new_scene이면 active인 경우 코드가 도크로 정리. '다 치워'류 remove는 두 대를 모두 mode:store로 넣어라",
            "items": _FORM_ROBOT,
        },
        "connection": _CONNECTION,
    },
    "required": ["robots", "connection"],
    "additionalProperties": False,
}

# ── Phase B 스키마 (structured outputs strict). 필드 순서 = 추론 순서.
#   좌표를 표현할 수 있는 필드가 없다 — choice는 코드가 만든 후보의 인덱스뿐이다.
PLACE_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "object",
            "description": "고르기 전 자가 점검 (사후 변명이 아니라 선택의 근거)",
            "properties": {
                "blocks_path": {"type": "boolean", "description": "이 배치가 사람 동선·통로를 막는가"},
                "within_reach": {"type": "boolean", "description": "앉거나 선 사람의 손·시선이 기능면에 닿는가"},
                "matches_count": {"type": "boolean", "description": "인원 수에 맞는 구성인가"},
            },
            "required": ["blocks_path", "within_reach", "matches_count"],
            "additionalProperties": False,
        },
        "reason": {"type": "string", "description": "이 후보를 고른(또는 전부 거부한) 근거"},
        "choice": {"type": ["integer", "null"], "description": "고른 후보의 인덱스(0부터). 거부면 null"},
        "reject_reason": {"type": ["string", "null"], "description": "모든 후보가 부적절할 때 그 이유. 선택하면 null"},
        "message": {"type": "string", "description": "HITL-2 승인 요청 문구. 사람의 언어 — 패널 각도·rot·좌표·cm 등 내부 용어·수치 금지"},
    },
    "required": ["checks", "reason", "choice", "reject_reason", "message"],
    "additionalProperties": False,
}
    