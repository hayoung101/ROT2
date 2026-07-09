# -*- coding: utf-8 -*-
"""컨텍스트 tool 5개."""
from tools import STATE, push_state


def _scene():
    return STATE["scene"]


def robot_states():
    return _scene().states()


def get_environment():
    return _scene().environment()


def get_recent_context(n):
    return _scene().recent(int(n))


def commit_layout(description):
    """보통은 부를 필요 없다 — ask_user 승인 시 코드가 자동 확정한다.
    직전 커밋 이후 변화가 없으면 중복 커밋하지 않고 no-op으로 반환."""
    sc = _scene()
    intent = STATE.get("intent") or {}
    entry = sc.commit_if_changed(description,
                                 intent.get("intent_type", "new_scene"),
                                 STATE.get("utterance", ""))
    noop = bool(sc.history) and sc.history[-1] is entry and entry["description"] != description
    return {"turn": entry["turn"], "description": entry["description"], "noop": noop}


def revert_to(version):
    sc = _scene()
    before_space = sc.space
    entry = sc.revert_to(int(version))
    if entry is None:
        return {"error": "해당 turn이 history에 없음", "version": version}
    if sc.space != before_space:
        from tools import push_scene
        push_scene()          # 방까지 바뀌면 scene_change
    else:
        push_state()
    # 복원은 코드가 이미 끝냈다 — LLM에는 요약만 (전체 state 반환은 토큰 낭비)
    return {"turn": entry["turn"], "space": entry["space"],
            "description": entry["description"]}
