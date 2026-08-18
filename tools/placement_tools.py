# -*- coding: utf-8 -*-
"""배치 실행 함수 — main._run_form_layer가 Phase B 결과대로 직접 호출한다 (LLM tool 아님).

move_robot/transform_robot/store_robot만 남는다. 자리 후보 생성·검증은 services/layout·
placement가, 승인은 viewer_tools.ask_user가 맡는다 (구 find_placement/check_feasibility/
furniture_mapping tool 껍데기는 형태층 tool 루프 폐기와 함께 제거, §6.9)."""
from tools import STATE, metric, push_state, scene as _scene
from services import collision, placement


def _with_issues(st):
    """실행 직후 코드가 자동 검증한다 (LLM의 check_feasibility 호출에 의존하지 않는 보장 레이어).
    문제가 있으면 결과에 issues(+fix 힌트)를 실어 LLM이 즉시 자가수정하게 한다."""
    sc = _scene()
    issues = collision.validate_layout(sc.states(), sc.environment())
    if issues:
        metric("auto_validate_issues")   # 실험 metrics: 실행 후 자동 검증 위반 횟수
        st = dict(st)
        st["issues"] = issues
        st["warning"] = "실행은 됐지만 위 issues가 남아 있다 — fix 힌트대로 move_robot로 해소하라"
    return st


def transform_robot(robot, panel_left, panel_right, furniture):
    st = _scene().transform(robot, panel_left, panel_right, furniture)
    push_state()
    return _with_issues(st)


def move_robot(robot, x, y, rot=None):
    import math
    sc = _scene()
    before = next((s for s in sc.states() if s["robot"] == robot), None)
    st = sc.move(robot, x, y, rot)
    # 애니메이션 시간 = 이동 거리 비례 (30cm/s 감각, 0.8~4초 clamp) — 순간이동 방지
    dist = math.hypot(st["x"] - before["x"], st["y"] - before["y"]) if before else 0
    push_state(duration=max(0.8, min(4.0, dist / 30.0)))
    # 실행 후 실제 rot·위치 기준의 패널 위치·기능면 방향 — 후보의 계획값이 무효가 됐어도
    # transform 직전에 항상 신선한 진실을 공급한다 (LLM은 제안, 코드는 보장).
    ori = placement.panel_orientation(st, sc.environment(), sc.states())
    if ori:
        st = dict(st)
        st["panel_orientation"] = ori
    return _with_issues(st)


def store_robot(robot):
    sc = _scene()
    before = next((s for s in sc.states() if s["robot"] == robot), None)
    # 이미 도크에 정리된(inactive) 로봇이면 아무 것도 하지 않는다 — inactive는
    # store로만 도달하므로 도크 복귀·패널0·초기화가 이미 끝난 상태다. 뷰어 push도 스킵.
    if before is not None and before.get("active") == "inactive":
        return {"robot": robot, "noop": True,
                "note": "이미 도크에 정리되어 있어 변화 없음 — store 불필요"}
    st = sc.store(robot)
    push_state()
    return st

