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
    """최근 n턴을 슬림하게 반환 — 메타 5필드 + 로봇당 한 줄 요약.
    정확한 복원은 revert_to(코드)가 하므로 LLM에겐 '무슨 상황이었는지'면 충분하다."""
    out = []
    for h in _scene().recent(int(n)):
        robots = []
        for st in h["state"].values():
            if st.get("active") == "inactive":
                robots.append("%s: inactive dock" % st["robot"])
            else:
                robots.append("%s: active (%d,%d) rot%d L%d/R%d '%s'"
                              % (st["robot"], st["x"], st["y"], st.get("rot", 0),
                                 st["panel_left"], st["panel_right"],
                                 st.get("furniture", "")))
        out.append({"turn": h["turn"], "space": h["space"],
                    "intent_type": h["intent_type"], "utterance": h["utterance"],
                    "description": h["description"], "robots": robots})
    return out


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
        from tools import push_scene
        push_scene()          # 방까지 바뀌면 scene_change
    else:
        push_state()
    # 복원은 코드가 이미 끝냈다 — LLM에는 요약만 (전체 state 반환은 토큰 낭비)
    return {"turn": entry["turn"], "space": entry["space"],
            "description": entry["description"]}
