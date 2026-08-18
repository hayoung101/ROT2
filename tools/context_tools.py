# -*- coding: utf-8 -*-
"""컨텍스트 함수 2개 — commit_layout / revert_to.

구 tool 3개(robot_states·get_environment·get_recent_context)는 형태층 tool 루프
폐기와 함께 쓰임이 사라졌다 — 지금은 main의 _slim_states·_room_desc·_slim_history가
각 층에 맞게 좁힌 뷰를 직접 만든다 (§6.2)."""
from tools import STATE, push_scene, push_state, scene as _scene


def commit_layout(description):
    """보통은 부를 필요 없다 — ask_user 승인 시 코드가 자동 확정한다.
    직전 커밋 이후 변화가 없으면 중복 커밋하지 않고 no-op으로 반환."""
    sc = _scene()
    intent = STATE.get("intent") or {}
    entry, changed = sc.commit_if_changed(description,
                                          intent.get("intent_type", "new_scene"),
                                          STATE.get("utterance", ""))
    return {"turn": entry["turn"], "description": entry["description"],
            "noop": not changed}


def revert_to(version):
    sc = _scene()
    before_space = sc.space
    entry = sc.revert_to(int(version))
    if entry is None:
        return {"error": "해당 turn이 history에 없음", "version": version}
    if sc.space != before_space:
        push_scene()          # 방까지 바뀌면 scene_change
    else:
        push_state()
    # 복원은 코드가 이미 끝냈다 — LLM에는 요약만 (전체 state 반환은 토큰 낭비)
    return {"turn": entry["turn"], "space": entry["space"],
            "description": entry["description"]}
