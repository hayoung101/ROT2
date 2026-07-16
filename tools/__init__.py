# -*- coding: utf-8 -*-
"""tools 공유 상태. main이 init()으로 조립하고, tool 함수들이 참조한다."""

STATE = {
    "scene": None,        # SceneState 인스턴스
    "client": None,       # OpenAI client
    "viewer": None,       # PopupViewer 인스턴스 (없으면 콘솔 fallback)
    "intent": None,       # 현재 턴의 의도층 출력
    "utterance": "",      # 현재 턴의 발화 원문
    "auto_approve": False,  # 테스트용: ask_user 자동 승인
    "metrics": None,      # 발화(턴) 단위 실험 카운터 — main.handle_logged가 관리 (§14-2)
}


def init(scene_state, client=None, viewer=None):
    STATE["scene"] = scene_state
    STATE["client"] = client
    STATE["viewer"] = viewer


# ---------- 실험 metrics (§14-2) ----------
# 수집은 tools/main 층에서만 한다 (services는 카운터를 모른다 — 의존 방향).
# STATE["metrics"]가 None이면(예: baseline, 단독 tool 테스트) 전부 no-op.

def new_metrics():
    """발화 1건의 카운터. main.handle_logged가 발화 시작 시 리셋한다."""
    return {"intent_type": None, "outcome": None,
            "clarify_rounds": 0, "clarify_limit_hit": False,
            "hitl1_attempts": 0, "hitl1_rejects": 0, "hitl1_feedback_depth": 0,
            "hitl2_attempts": 0, "hitl2_rejects": 0,
            "func_excluded": 0, "func_all_infeasible": False,
            "tool_calls": {}, "tool_calls_total": 0,
            "feasibility_failures": 0, "auto_validate_issues": 0,
            "llm_calls": 0}


def metric(key, n=1):
    """카운터 증가 (metrics 미가동이면 no-op)."""
    m = STATE.get("metrics")
    if isinstance(m, dict) and key in m:
        m[key] += n


def metric_set(key, value):
    m = STATE.get("metrics")
    if isinstance(m, dict):
        m[key] = value


def metric_tool(name):
    """registry.dispatch가 tool 호출마다 부른다."""
    m = STATE.get("metrics")
    if isinstance(m, dict):
        m["tool_calls"][name] = m["tool_calls"].get(name, 0) + 1
        m["tool_calls_total"] += 1


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
