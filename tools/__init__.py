# -*- coding: utf-8 -*-
"""tools 공유 상태. main이 init()으로 조립하고, tool 함수들이 참조한다."""

STATE = {
    "scene": None,        # SceneState 인스턴스
    "client": None,       # OpenAI client
    "viewer": None,       # PopupViewer 인스턴스 (없으면 콘솔 fallback)
    "intent": None,       # 현재 턴의 의도층 출력
    "utterance": "",      # 현재 턴의 발화 원문
    "auto_approve": False,  # 테스트용: ask_user 자동 승인
}


def init(scene_state, client=None, viewer=None):
    STATE["scene"] = scene_state
    STATE["client"] = client
    STATE["viewer"] = viewer


def push_state(duration=1.2):
    """상태 변경 직후 코드가 자동 호출 (LLM이 잊을 수 없게)."""
    v, s = STATE.get("viewer"), STATE.get("scene")
    if v and s:
        v.push_state(s.states(), duration)


def push_scene():
    """방 전환 시 코드가 자동 호출."""
    v, s = STATE.get("viewer"), STATE.get("scene")
    if v and s:
        v.push_scene(s.environment(), s.states())
