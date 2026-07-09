# -*- coding: utf-8 -*-
import os

from services.scene import SceneState


def make(tmp_path):
    return SceneState(scenes_dir="scenes",
                      session_path=os.path.join(str(tmp_path), "session.json"))


def test_load_scene_docks(tmp_path):
    s = make(tmp_path)
    s.load_scene("living_room")
    states = s.states()
    assert len(states) == 2
    assert all(st["panel_left"] == 0 and st["panel_right"] == 0 for st in states)
    assert all(st["active"] == "inactive" for st in states)   # 시작은 도크 대기
    assert s.furniture() and s.environment()["space"] == "living_room"


def test_commit_and_revert(tmp_path):
    s = make(tmp_path)
    s.load_scene("living_room")
    s.transform("BOT 1", 90, 0, "테이블")
    s.move("BOT 1", 200, 120, rot=90)
    s.commit("BOT 1 테이블", "new_scene", "테이블 만들어줘")          # turn 1
    s.transform("BOT 1", 90, 90, "큰 테이블")
    s.commit("확장", "modify", "더 크게")                              # turn 2
    assert s.turn == 2
    entry = s.revert_to(1)
    assert entry["turn"] == 1
    assert s.robots["BOT 1"]["panel_right"] == 0
    assert s.robots["BOT 1"]["furniture"] == "테이블"


def test_revert_across_space(tmp_path):
    s = make(tmp_path)
    s.load_scene("living_room")
    s.transform("BOT 1", 90, 0, "테이블")
    s.commit("거실 테이블", "new_scene", "")                          # turn 1
    s.load_scene("bedroom")
    s.commit("침실 이동", "new_scene", "")                            # turn 2
    s.revert_to(1)
    assert s.space == "living_room"
    assert s.robots["BOT 1"]["panel_left"] == 90


def test_store_and_snap(tmp_path):
    s = make(tmp_path)
    s.load_scene("kitchen")
    s.transform("BOT 2", 70, 200, "?")     # snap: 70→90? (70은 45와 90 중 90에 가까움... 67.5 기준) → 90, 200→180
    assert s.robots["BOT 2"]["panel_left"] in (45, 90)
    assert s.robots["BOT 2"]["panel_right"] == 180
    assert s.robots["BOT 2"]["active"] == "active"            # transform → active
    dock = s.store("BOT 2")
    assert dock["panel_left"] == 0 and dock["furniture"] == "none"
    assert dock["active"] == "inactive"                        # store → inactive


def test_resume(tmp_path):
    s = make(tmp_path)
    s.load_scene("living_room")
    s.transform("BOT 1", 135, 0, "독서대")
    s.commit("독서대", "new_scene", "책 읽을래")
    s2 = make(tmp_path)
    assert s2.resume()
    assert s2.turn == 1 and s2.space == "living_room"
    assert s2.robots["BOT 1"]["panel_left"] == 135
    assert s2.recent(1)[0]["description"] == "독서대"


def test_transform_clamps_at_wall(tmp_path):
    s = make(tmp_path)
    s.load_scene("living_room")
    s.move("BOT 1", 20, 200)                 # 왼쪽 벽에 밀착 (본체 x 0~40)
    s.transform("BOT 1", 90, 0, "테이블")     # 왼쪽 패널을 벽 쪽으로 펼침
    assert s.robots["BOT 1"]["x"] == 50      # footprint가 방 안으로 밀려남 (30cm 돌출 보정)
