# -*- coding: utf-8 -*-
"""배치 tool 6개 — LLM에 보이는 껍데기. 내용은 services 호출."""
import json
import os

from tools import STATE, push_state
from services import collision, placement


def _scene():
    return STATE["scene"]


def _with_issues(st):
    """실행 직후 코드가 자동 검증한다 (LLM의 check_feasibility 호출에 의존하지 않는 보장 레이어).
    문제가 있으면 결과에 issues(+fix 힌트)를 실어 LLM이 즉시 자가수정하게 한다."""
    sc = _scene()
    issues = collision.validate_layout(sc.states(), sc.environment())
    if issues:
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
    before = next((s for s in _scene().states() if s["robot"] == robot), None)
    st = _scene().move(robot, x, y, rot)
    # 애니메이션 시간 = 이동 거리 비례 (30cm/s 감각, 0.8~4초 clamp) — 순간이동 방지
    dist = math.hypot(st["x"] - before["x"], st["y"] - before["y"]) if before else 0
    push_state(duration=max(0.8, min(4.0, dist / 30.0)))
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


def check_feasibility(robots, connections=None):
    """구성안(부분 상태 목록)을 현재 상태에 덮어쓴 전체 상태로 물리+연결 검증."""
    sc = _scene()
    merged = {s["robot"]: s for s in sc.states()}
    for p in robots or []:
        base = dict(merged.get(p.get("robot"), {}))
        base.update({k: v for k, v in p.items() if v is not None})
        merged[p["robot"]] = base
    return placement.feasibility(list(merged.values()), sc.environment(), connections)


def find_placement(footprint_radius=None, near=None, avoid=None,
                   footprint_w=None, footprint_d=None, moving_robot=None,
                   connect=None):
    sc = _scene()
    if connect:   # 두 대 조합의 정밀 연결 좌표
        cands = placement.find_connect(
            sc.environment(), sc.states(), near,
            mode=connect.get("mode", "face"), side=connect.get("side", "both"),
            anchor_panel=connect.get("anchor_panel"),
            moving_panel=connect.get("moving_panel"), moving_robot=moving_robot)
        if not cands:
            return {"candidates": [], "note":
                    "연결 후보 없음 — near에 앵커 로봇 이름을 정확히 줬는지, 그 자리가 벽·가구에 "
                    "막히지 않았는지 확인하라 (앵커를 먼저 옮긴 뒤 재시도). 좌표를 직접 계산하지 마라."}
        return {"candidates": cands, "note":
                "x·y·rot을 그대로 move_robot에 쓰라. face면 앵커의 해당 쪽 패널을 anchor_panel로, "
                "옮긴 로봇의 moving_side 패널을 moving_panel로 열어야 연결이 성립한다 "
                "(둘 다 transform_robot으로) — 거리·각도를 직접 계산하지 마라."}
    fixed = [s for s in sc.states() if s["robot"] != moving_robot]
    out = placement.find_placement(sc.environment(), fixed, footprint_radius,
                                   near, avoid or (), footprint_w=footprint_w,
                                   footprint_d=footprint_d)
    if not out:
        return {"candidates": [], "note":
                "유효 후보 없음 — near id가 정확한지 get_environment로 확인하고, footprint를 "
                "줄이거나 조건을 바꿔 재시도하라. 좌표를 직접 지어내지 마라."}
    return out


def furniture_mapping(activity):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "furniture_motifs.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    match = data.get("activities", {}).get(activity)
    if match:
        motifs = {n: data["motifs"][n] for n in match.get("suggest", []) if n in data["motifs"]}
        return {"note": "reference일 뿐 강제가 아니다. capacity(권장 인원)에 맞는 최소 구성을 고르고, 필요하면 자유롭게 새 형태를 만들어라.",
                "activity": activity, "guide": match, "motifs": motifs,
                "modifiers": data.get("modifiers")}
    return {"note": "이 활동의 참고표는 없다 — 물리 스펙 안에서 자유롭게 구성하라.",
            "available_activities": list(data.get("activities", {}).keys())}
