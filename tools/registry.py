# -*- coding: utf-8 -*-
"""TOOLS(스키마 12개) + HANDLERS(이름→함수) + dispatch.

스키마의 description은 프롬프트의 일부다 — 규칙이 아니라 사용법과 원칙을 담는다."""
import config

from . import context_tools, placement_tools, viewer_tools

_ROBOT = {"type": "string", "enum": list(config.ROBOT_NAMES), "description": "로봇 이름"}
_PANEL_L = {"type": "number", "enum": list(config.PANEL_ANGLES),
            "description": "왼쪽 패널 각도. 0=닫힘, 45=\\ 아래 기울임, 90=수평 확장, 135=/ 위 기울임, 180=수직"}
_PANEL_R = {"type": "number", "enum": list(config.PANEL_ANGLES),
            "description": "오른쪽 패널 각도 (왼쪽과 동일한 5단계)"}
_SIDE = {"type": "string", "enum": ["left", "right"], "description": "패널 쪽"}

_STATE_ITEM = {
    "type": "object",
    "properties": {
        "robot": _ROBOT,
        "x": {"type": "number", "description": "본체 바닥 중심 x (cm)"},
        "y": {"type": "number", "description": "본체 바닥 중심 y (cm)"},
        "rot": {"type": "number", "description": "방향 (도, CCW)"},
        "panel_left": _PANEL_L,
        "panel_right": _PANEL_R,
    },
    "required": ["robot", "x", "y", "rot", "panel_left", "panel_right"],
    "additionalProperties": False,
}

_CONNECTION_ITEM = {
    "type": "object",
    "properties": {
        "robot_a": _ROBOT, "side_a": _SIDE,
        "robot_b": _ROBOT, "side_b": _SIDE,
    },
    "required": ["robot_a", "side_a", "robot_b", "side_b"],
    "additionalProperties": False,
}


def _tool(name, description, props):
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": props,
                           "required": list(props), "additionalProperties": False}}


TOOLS = [
    # ---------- placement (6) ----------
    _tool("transform_robot",
          "로봇의 두 패널 각도를 바꿔 변형한다. 실행형 — 즉시 적용되고 뷰어가 갱신된다. "
          "반드시 check_feasibility 통과 후 호출하라. 각도가 유효 범위를 벗어나면 코드가 가장 가까운 단계로 교정한다. "
          "실행 직후 코드가 자동 검증한다 — 반환에 issues가 있으면 fix 힌트대로 즉시 해소하라.",
          {"robot": _ROBOT, "panel_left": _PANEL_L, "panel_right": _PANEL_R,
           "furniture": {"type": "string",
                         "description": "이 형태를 사람이 읽기 쉽게 표현한 자유 라벨 (예: 등받이 의자, 독서대). 실제 명령값은 패널 각도"}}),
    _tool("move_robot",
          "로봇을 이동·회전한다. 실행형 — 즉시 적용. footprint(패널 포함)가 방을 벗어나면 코드가 안으로 끌어당긴다. "
          "find_placement의 후보 좌표와 rot_suggest를 참고해 좌표를 정하라. "
          "실행 직후 코드가 자동 검증한다 — 반환에 issues가 있으면 fix 힌트대로 즉시 해소하라. "
          "반환의 panel_orientation은 실행 후 실제 rot 기준으로 재계산한 주변 앵커별 패널 위치와 각도별 앞면 방향이다. "
          "panel_on_anchor_side는 앵커와 가까운 패널의 위치일 뿐 기능면 방향이 아니다. front_face_toward_anchor를 사용해 "
          "원하는 기능면 방향을 실제 left/right로 변환하라 (off_axis는 그 앵커가 고정 측면 방향이라는 뜻).",
          {"robot": _ROBOT,
           "x": {"type": "number", "description": "본체 바닥 중심 x (cm)"},
           "y": {"type": "number", "description": "본체 바닥 중심 y (cm)"},
           "rot": {"type": ["number", "null"], "description": "방향 (도, CCW). null이면 현재 방향 유지"}}),
    _tool("store_robot",
          "'치워줘' 또는 새 구성에 쓰지 않는 로봇 정리: 방 구석 홈 도크로 복귀시키고 패널 0°, inactive로 초기화한다 (원자 처리). "
          "active인 로봇에만 호출하라 — 이미 inactive(도크 정리 완료)인 로봇에는 호출하지 마라. 호출해도 noop만 반환된다.",
          {"robot": _ROBOT}),
    _tool("check_feasibility",
          "구성안을 실행 전에 검증한다 (경계·기존 가구·로봇 겹침 + 선언한 연결의 패널 맞닿음). "
          "robots에는 바꿀 로봇의 목표 상태만 넣으면 되고 나머지는 현재 상태로 채워진다. "
          "feasible=false면 issues를 보고 수정 후 재검증하라. 조화(어색함) 판단은 이 tool이 하지 않는다 — 스스로 점검하라.",
          {"robots": {"type": "array", "items": _STATE_ITEM, "description": "검증할 목표 상태 목록"},
           "connections": {"type": ["array", "null"], "items": _CONNECTION_ITEM,
                           "description": "두 대를 패널로 맞대는 연결 선언 (마주보기 조합 시). 없으면 null"}}),
    _tool("find_placement",
          "로봇이 가구가 될 수 있는 가용 공간을 코드가 추출해 준다. 두 모드의 역할이 다르다. "
          "[조사 모드] near=null: 방의 빈 공간 후보를 관계 해석 없이 기하 사실로 서술한다 — clearance(최소 여유 cm), "
          "free(동서남북 4방향으로 확보된 빈 거리 cm), nearby(주변 사물의 방향·거리). 어디에 놓을지·어느 방향을 보게 할지(rot)·"
          "사물과 관계를 맺을지는 이 사실들로 네가 활동에 맞게 판단하라. 패널 좌우가 필요하면 move_robot 실행 후 반환의 panel_orientation(실측)으로 정하라. "
          "[앵커 모드] near=가구 id/로봇 이름: 그 사물과 '마주 보는' 관계를 만들 때 호출하라 — 인접 후보(tag: <id>_front/_side/_back)에 "
          "앵커를 마주 보는 rot_suggest(도), panel_on_anchor_side/panel_on_opposite_side(패널의 물리적 위치), "
          "front_face_toward_anchor(각도별로 앞면이 앵커를 향하는 패널)가 붙는다. 이 값들은 rot_suggest 채택 시에만 유효하다. "
          "45° 앞면은 바깥쪽, 90°는 위쪽, 135°/180°는 본체 쪽이다. 어느 기능면을 어디로 향하게 할지는 상황으로 판단하라. "
          "나란히 놓일 가구(사이드 테이블 등)는 rot_suggest 대신 짝 가구의 rot에 맞춰라. 좌우(left/right)를 rot에서 직접 계산하지 마라. "
          "footprint는 반경(정사각) 또는 footprint_w×footprint_d(직사각 — 풀확장 100×40 같은 길쭉한 구성은 이쪽이 후보가 많다). "
          "두 대 조합의 연결 좌표는 connect를 쓰라 — 거리·각도를 직접 계산하지 마라.",
          {"footprint_radius": {"type": ["number", "null"], "description": "배치할 구성의 대략 반경 (cm). 직사각 proxy를 쓰면 null"},
           "near": {"type": ["string", "null"], "description": "앵커: 가구 id/label 또는 로봇 이름. null=방 전체 조사. connect 사용 시에는 앵커 '로봇' 이름 필수"},
           "avoid": {"type": ["array", "null"], "items": {"type": "string"}, "description": "멀리 둘 대상 id 목록"},
           "footprint_w": {"type": ["number", "null"], "description": "직사각 proxy 폭 (cm)"},
           "footprint_d": {"type": ["number", "null"], "description": "직사각 proxy 깊이 (cm)"},
           "moving_robot": {"type": ["string", "null"], "description": "이 자리로 옮길 로봇 이름 — 장애물 계산에서 제외"},
           "connect": {"type": ["object", "null"],
                       "description": "두 대 조합용 정밀 연결 후보 (near=앵커 로봇 이름 필수). mode face=마주보고 패널 맞대기(rot 차 180°, 패널 끝 맞닿음 보장), side=나란히 붙이기(rot 동일, 본체 맞대기). 반환된 x·y·rot을 그대로 move_robot에 쓰고, face면 앵커의 해당 쪽 패널을 anchor_panel로, 옮긴 로봇의 moving_side 패널을 moving_panel로 열어라 (둘 다 transform_robot).",
                       "properties": {
                           "mode": {"type": "string", "enum": ["face", "side"], "description": "조합 방식"},
                           "side": {"type": "string", "enum": ["left", "right", "both"], "description": "앵커의 어느 패널 쪽에 붙일지. both=양쪽 후보"},
                           "anchor_panel": {"type": ["number", "null"], "description": "face 모드: 앵커 쪽에서 맞댈 패널 각도 (0/45/90/135/180). side 모드면 null"},
                           "moving_panel": {"type": ["number", "null"], "description": "face 모드: 옮길 로봇 쪽에서 맞댈 패널 각도. side 모드면 null"}},
                       "required": ["mode", "side", "anchor_panel", "moving_panel"],
                       "additionalProperties": False}}),
    _tool("furniture_mapping",
          "활동→가구 조합 참고표(motif)를 조회한다. 강제가 아닌 reference — capacity(권장 인원)에 맞는 최소 구성을 고르되, "
          "참고표에 없는 형태도 물리 스펙 안에서 자유롭게 만들 수 있다.",
          {"activity": {"type": "string", "description": "활동 이름 (예: 식사, 공부, 놀이, 독서, 정리, 휴식, 공존)"}}),
    # ---------- context (5) ----------
    _tool("robot_states", "로봇 전체의 현재 상태를 조회한다. 편집을 시작하기 전에 반드시 확인하라.", {}),
    _tool("get_environment",
          "현재 방의 scene JSON을 조회한다: 방 크기(width×depth), 기존 가구(pre_existing_furniture: id·label·중심·크기·rot·corners). "
          "기존 가구는 장애물이자 배치 앵커다.", {}),
    _tool("get_recent_context", "최근 n턴의 history(발화·설명·상태 스냅샷)를 조회한다. '아까 그거' 같은 참조 해석용.",
          {"n": {"type": "integer", "description": "가져올 턴 수"}}),
    _tool("revert_to", "지정한 turn의 스냅샷으로 결정론적 복원한다 (방이 다르면 방도 전환). '원래대로/아까처럼' 처리용 — "
          "get_recent_context로 turn을 찾은 뒤 호출하라.",
          {"version": {"type": "integer", "description": "복원할 turn 번호"}}),
    # ---------- viewer (1) ----------
    _tool("ask_user",
          "배치 결과의 승인을 사용자에게 요청한다 (HITL 게이트). 실행을 마친 뒤 반드시 호출하라. "
          "메시지는 사람의 언어로 쓴다 — 패널 각도·rot·좌표 같은 내부 용어와 수치는 쓰지 말고, "
          "무엇을(가구 이름·역할) 어디에(기존 가구와의 관계로) 만들었는지 2~3문장으로 말하라. "
          "intent에 complement_note가 있으면 '(기존 가구)가 (필요)를 해주니, 로봇은 (보완 역할)이 되었어요' 흐름으로 그 이유를 담아라. "
          "approved=false면 feedback을 반영해 수정하라.",
          {"message": {"type": "string", "description": "무엇을 어떻게 배치했는지 요약한 승인 요청 문구"}}),
]

HANDLERS = {
    "transform_robot": placement_tools.transform_robot,
    "move_robot": placement_tools.move_robot,
    "store_robot": placement_tools.store_robot,
    "check_feasibility": placement_tools.check_feasibility,
    "find_placement": placement_tools.find_placement,
    "furniture_mapping": placement_tools.furniture_mapping,
    "robot_states": context_tools.robot_states,
    "get_environment": context_tools.get_environment,
    "get_recent_context": context_tools.get_recent_context,
    "revert_to": context_tools.revert_to,
    "ask_user": viewer_tools.ask_user,
}


def dispatch(name, args):
    """LLM tool_call(JSON)을 이름으로 찾아 실행."""
    from tools import metric_tool
    metric_tool(name)   # 실험 metrics: tool 호출 수 (이름별)
    return HANDLERS[name](**args)
