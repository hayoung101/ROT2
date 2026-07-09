# -*- coding: utf-8 -*-
"""배치 tool 6개 — LLM에 보이는 껍데기. 내용은 services 호출."""
import json
import os

from tools import STATE, push_state
from services import placement


def _scene():
    return STATE["scene"]


def transform_robot(robot, panel_left, panel_right, furniture):
    st = _scene().transform(robot, panel_left, panel_right, furniture)
    push_state()
    return st


def move_robot(robot, x, y, rot=None):
    import math
    before = next((s for s in _scene().states() if s["robot"] == robot), None)
    st = _scene().move(robot, x, y, rot)
    # 애니메이션 시간 = 이동 거리 비례 (30cm/s 감각, 0.8~4초 clamp) — 순간이동 방지
    dist = math.hypot(st["x"] - before["x"], st["y"] - before["y"]) if before else 0
    push_state(duration=max(0.8, min(4.0, dist / 30.0)))
    return st


def store_robot(robot):
    st = _scene().store(robot)
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
                   footprint_w=None, footprint_d=None, moving_robot=None):
    sc = _scene()
    fixed = [s for s in sc.states() if s["robot"] != moving_robot]
    return placement.find_placement(sc.environment(), fixed, footprint_radius,
                                    near, avoid or (), footprint_w=footprint_w,
                                    footprint_d=footprint_d)


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
