# -*- coding: utf-8 -*-
import json
import math

from services import collision as C
from services import placement as P


def load(space):
    with open("scenes/%s.json" % space, encoding="utf-8") as f:
        return json.load(f)


def robot(x, y, rot=0, pl=0, pr=0, name="BOT 1", furniture="none"):
    return {"robot": name, "x": x, "y": y, "rot": rot,
            "panel_left": pl, "panel_right": pr, "furniture": furniture}


def no_collision(c, scene, extra=()):
    proxy = (c["x"], c["y"], 40, 40, 0)
    for f in scene["pre_existing_furniture"]:
        assert not C.rects_collide(proxy, C.furniture_rect(f)), (c, f["id"])
    for rc in extra:
        assert not C.rects_collide(proxy, rc)


def test_scenes_have_no_user():
    for s in ("living_room", "bedroom", "kitchen", "bathroom", "balcony"):
        assert "user" not in load(s)


def test_anchor_mode_front_first():
    scene = load("kitchen")   # dining_table_1 (250,150) rot 0 → 앞면 +y, 앞이 비어 있음
    cands = P.find_placement(scene, [], 20, near="dining_table_1")
    assert cands and cands[0]["tag"] == "dining_table_1_front"
    table = next(f for f in scene["pre_existing_furniture"] if f["id"] == "dining_table_1")
    for c in cands:
        no_collision(c, scene)
        assert C.rect_gap((c["x"], c["y"], 40, 40, 0), C.furniture_rect(table)) <= 30  # 인접(대각 방향은 모서리 기준이라 축 간격이 커짐)
        ang = math.degrees(math.atan2(table["y"] - c["y"], table["x"] - c["x"])) % 360
        assert abs((ang - c["rot_suggest"] + 180) % 360 - 180) <= 1  # rot_suggest = 앵커 방향


def test_anchor_mode_blocked_front_falls_back():
    scene = load("living_room")   # table_1 앞(+y)은 소파에 막힘 → side/back 후보만
    cands = P.find_placement(scene, [], 20, near="table_1")
    assert cands
    for c in cands:
        no_collision(c, scene)
        assert c["tag"].startswith("table_1_")


def test_survey_mode_tags():
    scene = load("living_room")
    cands = P.find_placement(scene, [], 20, near=None, k=12)
    tags = [c["tag"] for c in cands]
    assert any(t == "open_area" for t in tags)
    assert any(t.endswith("_front") for t in tags)       # tv_1 앞은 비어 있음
    assert any(t.startswith("wall_") for t in tags)
    for c in cands:
        no_collision(c, scene)
    # 정렬: 가구 앞 → open_area → 옆 → 벽가
    order = {"front": 0, "open": 1, "side": 2, "wall": 3}
    def rank(t):
        if t.endswith("_front"): return 0
        if t == "open_area": return 1
        if t.endswith("_side") or t.endswith("_back"): return 2
        return 3
    assert [rank(t) for t in tags] == sorted(rank(t) for t in tags)


def test_open_area_has_clearance():
    scene = load("living_room")
    cands = [c for c in P.find_placement(scene, [], 20, near=None, k=12)
             if c["tag"] == "open_area"]
    assert cands and all(c["clearance"] >= 20 for c in cands)


def test_avoid_and_fixed_robots():
    scene = load("living_room")
    other = robot(320, 240)
    cands = P.find_placement(scene, [other], 20, near="table_1", avoid=["sofa_1"])
    sofa = next(f for f in scene["pre_existing_furniture"] if f["id"] == "sofa_1")
    for c in cands:
        no_collision(c, scene, extra=[(other["x"], other["y"], 40, 40, 0)])
        assert math.hypot(c["x"] - sofa["x"], c["y"] - sofa["y"]) >= 20 + 40


def test_unknown_anchor_returns_empty():
    assert P.find_placement(load("living_room"), [], 20, near="ghost_9") == []


def test_feasibility_good_and_bad():
    scene = load("living_room")
    good = [robot(60, 60, name="BOT 1"), robot(340, 60, name="BOT 2")]
    res = P.feasibility(good, scene)
    assert res["feasible"] and res["issues"] == []
    bad = [robot(200, 220, name="BOT 1"), robot(205, 220, name="BOT 2")]  # 테이블 위 + 서로 겹침
    types = {i["type"] for i in P.feasibility(bad, scene)["issues"]}
    assert "furniture_overlap" in types and "robot_overlap" in types


def test_feasibility_connection():
    scene = load("living_room")
    a = robot(80, 120, rot=0, pr=90, name="BOT 1")
    b = robot(180, 120, rot=180, pr=90, name="BOT 2")
    con = [{"robot_a": "BOT 1", "side_a": "right", "robot_b": "BOT 2", "side_b": "right"}]
    assert P.feasibility([a, b], scene, connections=con)["feasible"]
    b_far = robot(200, 120, rot=180, pr=90, name="BOT 2")
    res = P.feasibility([a, b_far], scene, connections=con)
    assert any(i["type"] == "connection_gap" for i in res["issues"])


def test_rect_footprint_in_balcony():
    scene = load("balcony")   # 400×100 — 풀확장(100×40)은 직사각 proxy로만 배치 가능
    square = P.find_placement(scene, [], footprint_radius=50, near=None, k=99)
    rect = P.find_placement(scene, [], near=None, k=99, footprint_w=100, footprint_d=40)
    assert len(rect) > len(square)
    for c in rect:
        proxy = (c["x"], c["y"], 100, 40, 0)
        for f in scene["pre_existing_furniture"]:
            assert not C.rects_collide(proxy, C.furniture_rect(f))
