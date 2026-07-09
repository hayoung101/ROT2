# -*- coding: utf-8 -*-
import math

from services import collision as C


def robot(x, y, rot=0, pl=0, pr=0, name="BOT 1"):
    return {"robot": name, "x": x, "y": y, "rot": rot,
            "panel_left": pl, "panel_right": pr}


def test_panel_protrusion():
    assert C.panel_protrusion(0) == 0
    assert C.panel_protrusion(180) < 1e-9
    assert abs(C.panel_protrusion(90) - 30) < 1e-9
    assert abs(C.panel_protrusion(45) - 30 * math.sin(math.radians(45))) < 1e-9  # ~21.2


def test_snap_panel():
    assert C.snap_panel(50) == 45
    assert C.snap_panel(70) == 90
    assert C.snap_panel(-10) == 0
    assert C.snap_panel(999) == 180
    assert C.snap_panel(None) == 0


def test_footprint_counts_and_span():
    assert len(C.footprint_rects(robot(100, 100))) == 1              # 닫힌 상자
    assert len(C.footprint_rects(robot(100, 100, pl=90, pr=90))) == 3
    x0, y0, x1, y1 = C.footprint_bbox(robot(100, 100, pl=90, pr=90))
    assert abs((x1 - x0) - 100) < 1e-6   # 40 + 30 + 30 = 풀 확장 상판 100cm
    assert abs((y1 - y0) - 40) < 1e-6


def test_body_collision_and_touching():
    a = robot(100, 100)
    assert C.robots_collide(a, robot(135, 100))       # 침투 5cm > slack 2
    assert not C.robots_collide(a, robot(141, 100))   # 간격 1cm
    assert not C.robots_collide(a, robot(139, 100))   # 침투 1cm <= slack (맞닿음 허용)


def test_panel_extends_footprint():
    a = robot(100, 100, pr=90)          # 오른쪽 패널 +30cm → 오른끝 150
    assert C.robots_collide(a, robot(165, 100))       # 상대 왼끝 145 → 침투 5
    assert not C.robots_collide(a, robot(175, 100))   # 상대 왼끝 155 → 간격 5
    assert not C.robots_collide(robot(100, 100), robot(165, 100))  # 패널 없으면 안 겹침


def test_rotated_collision():
    a = robot(100, 100, rot=45)         # 대각 반폭 ~28.28
    assert C.robots_collide(a, robot(145, 100))       # 28.28+20=48.28 > 45
    assert not C.robots_collide(a, robot(152, 100))   # 48.28 < 52


def test_clamp_to_bounds():
    st = C.clamp_to_bounds(robot(10, 10), 400, 300)
    assert (st["x"], st["y"]) == (20, 20)
    st = C.clamp_to_bounds(robot(380, 150, pr=90), 400, 300)   # 오른끝 380+50=430
    assert abs(st["x"] - 350) < 1e-6


def test_furniture_collision():
    table = {"id": "t", "x": 200, "y": 150, "w": 100, "d": 60, "rot": 0}
    assert C.robot_hits_furniture(robot(200, 105), table)      # 침투 5
    assert not C.robot_hits_furniture(robot(200, 95), table)   # 간격 5


def test_place_without_overlap():
    fixed = C.footprint_rects(robot(100, 100))
    placed = C.place_without_overlap(robot(110, 100), fixed, 400, 300)
    assert placed is not None
    assert not C.robots_collide(placed, robot(100, 100))


def test_panels_touching_facing():
    a = robot(100, 100, rot=0, pr=90)     # 오른쪽 패널 끝 = 150
    b = robot(200, 100, rot=180, pr=90)   # rot 180이라 panel_right가 -x쪽 → 끝 = 150
    assert C.panels_touching(a, "right", b, "right")
    b_far = robot(210, 100, rot=180, pr=90)
    assert not C.panels_touching(a, "right", b_far, "right")


def test_validate_layout():
    scene = {"width": 400, "depth": 300,
             "pre_existing_furniture": [{"id": "t", "x": 200, "y": 150, "w": 100, "d": 60, "rot": 0}]}
    ok = [robot(60, 60, name="BOT 1"), robot(340, 60, name="BOT 2")]
    assert C.validate_layout(ok, scene) == []
    bad = [robot(200, 150, name="BOT 1"), robot(205, 150, name="BOT 2")]
    types = {i["type"] for i in C.validate_layout(bad, scene)}
    assert "furniture_overlap" in types and "robot_overlap" in types


def test_panels_touching_requires_alignment():
    a = robot(100, 100, rot=0, pr=90)
    b_offset = robot(200, 135, rot=180, pr=90)   # 옆으로 35cm 어긋남 → 폭 겹침 5cm
    assert not C.panels_touching(a, "right", b_offset, "right")
