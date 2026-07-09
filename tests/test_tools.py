# -*- coding: utf-8 -*-
import os

import tools
from tools import registry
from services.scene import SceneState


def setup(tmp_path):
    s = SceneState(session_path=os.path.join(str(tmp_path), "session.json"))
    s.load_scene("living_room")
    tools.init(s, client=None)
    tools.STATE["auto_approve"] = True
    tools.STATE["intent"] = {"intent_type": "new_scene"}
    tools.STATE["utterance"] = "다리 아파"
    return s


def test_registry_schema():
    assert len(registry.TOOLS) == 12
    assert {t["name"] for t in registry.TOOLS} == set(registry.HANDLERS)
    for t in registry.TOOLS:
        p = t["parameters"]
        assert t["strict"] and p["required"] == list(p["properties"])   # strict 규칙


def test_full_turn_roundtrip(tmp_path):
    s = setup(tmp_path)
    env = registry.dispatch("get_environment", {})
    assert env["space"] == "living_room"

    # 등받이 의자(180/45)는 45° 패널이 21cm 돌출 → 반경 = 본체 20 + 21 ≈ 42
    cands = registry.dispatch("find_placement",
                              {"footprint_radius": 42, "near": "table_1",
                               "moving_robot": "BOT 1"})
    assert cands
    c = cands[0]
    res = registry.dispatch("check_feasibility", {"robots": [
        {"robot": "BOT 1", "x": c["x"], "y": c["y"], "rot": c["rot_suggest"],
         "panel_left": 180, "panel_right": 45}]})
    assert res["feasible"], res
    registry.dispatch("move_robot", {"robot": "BOT 1", "x": c["x"], "y": c["y"],
                                     "rot": c["rot_suggest"]})
    registry.dispatch("transform_robot", {"robot": "BOT 1", "panel_left": 180,
                                          "panel_right": 45, "furniture": "등받이 의자"})
    out = registry.dispatch("ask_user", {"message": "테이블 앞에 의자를 놓았어요"})
    assert out["approved"]
    registry.dispatch("commit_layout", {"description": "테이블 앞 등받이 의자"})
    assert s.turn == 1

    registry.dispatch("store_robot", {"robot": "BOT 1"})
    assert s.robots["BOT 1"]["active"] == "inactive"
    rev = registry.dispatch("revert_to", {"version": 1})
    assert rev["turn"] == 1
    assert s.robots["BOT 1"]["furniture"] == "등받이 의자"


def test_furniture_mapping():
    setup_needed = tools.STATE.get("scene")
    guide = registry.dispatch("furniture_mapping", {"activity": "독서"})
    assert "motifs" in guide and guide["guide"]["suggest"]
    miss = registry.dispatch("furniture_mapping", {"activity": "우주여행"})
    assert "available_activities" in miss



