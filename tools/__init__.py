# -*- coding: utf-8 -*-
"""tools 공유 상태. main이 init()으로 조립하고, tool 함수들이 참조한다."""

STATE = {
    "scene": None,        # SceneState 인스턴스
    "viewer": None,       # PopupViewer 인스턴스 (없으면 콘솔 fallback)
    "intent": None,       # 현재 턴의 의도층 출력
    "utterance": "",      # 현재 턴의 발화 원문
    "auto_approve": False,  # 테스트용: ask_user 자동 승인
    "metrics": None,      # 발화(턴) 단위 실험 카운터 — main.handle_logged가 관리 (§14-2)
}


def scene():
    """STATE["scene"] 단축 — tool 모듈들이 _scene으로 alias해 쓴다 (중복 정의 방지)."""
    return STATE["scene"]


def init(scene_state, viewer=None):
    STATE["scene"] = scene_state
    STATE["viewer"] = viewer


# ---------- 실험 metrics (§14-2) ----------
# 수집은 tools/main 층에서만 한다 (services는 카운터를 모른다 — 의존 방향).
# STATE["metrics"]가 None이면(예: baseline, 단독 tool 테스트) 전부 no-op.

def new_metrics():
    """발화 1건의 카운터. main.handle_logged가 발화 시작 시 리셋한다."""
    return {"intent_type": None, "outcome": None,
            "clarify_rounds": 0, "clarify_limit_hit": False,
            "hitl1_attempts": 0, "hitl1_rejects": 0, "hitl1_feedback_depth": 0,
            "hitl2_attempts": 0, "hitl2_rejects": 0, "hitl2_feedback_depth": 0,
            "func_excluded": 0, "func_all_infeasible": False,
            "auto_validate_issues": 0,
            # 형태층 Phase A/B (§6.7·6.6): 재호출 예산·공집합·거부·강행 착지·message 재생성
            "form_recalls": 0, "empty_set": 0, "phase_b_rejects": 0,
            "forced_landing": False,
            # message 재생성은 필터별로 나눠 센다 — term(수치·내부 용어)은 정상 동작이고
            # leak(내부 사정)은 예측 기반 필터라 오탐률 자체가 관측 대상이다. 합계는 나중에
            # 더하면 되지만 합쳐진 숫자는 나중에 못 나눈다 (agent.ask_place 참조).
            "place_regen_term": 0, "place_regen_leak": 0,
            "place_regen_term_failed": 0, "place_regen_leak_failed": 0,
            "llm_calls": 0,
            "persistence_error": False}   # session.json 저장 실패 (턴은 계속 진행)


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
    """이름별 tool 호출 카운터. 형태층 tool 루프 폐기(§6.9)로 현재 호출부는 없다 —
    §16.1 UI 조작 통합처럼 이름 있는 실행이 되살아나면 그 훅으로 남겨 둔다."""
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
