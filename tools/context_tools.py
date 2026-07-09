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
    intent = STATE.get("intent") or {}
    entry = _scene().commit(description,
                            intent.get("intent_type", "new_scene"),
                            STATE.get("utterance", ""))
    return {"turn": entry["turn"], "description": description}


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
    return {"turn": entry["turn"], "space": entry["space"],
            "description": entry["description"], "state": entry["state"]}
