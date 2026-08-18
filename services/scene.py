# -*- coding: utf-8 -*-
"""SceneState: 현재 방 scene + robots + history + commit/revert/recent + load_scene(space).

- history의 state는 항상 전체 로봇의 절대 스냅샷 (diff 아님) → 어느 turn이든 단독 복원.
- turn은 전역 번호 (리셋 없음). commit마다 logs/session.json 저장 → 재시작 시 resume().
- viewer를 모름 (push는 tools 계층의 몫).
"""
import copy
import json
import os
from datetime import datetime

import config
from services import collision


class SceneState:
    def __init__(self, scenes_dir="scenes",
                 session_path=os.path.join("logs", "session.json")):
        self.scenes_dir = scenes_dir
        self.session_path = session_path
        self.space = None
        self.scene = None          # 현재 방 JSON dict
        self.robots = {}           # name -> state dict
        self.history = []
        self.turn = 0

    # ---------- 방 ----------
    #쓰이는 곳 3군데: 시작시 초기 방 로드
    # intent_type이 new_scene이고 space가 바뀔때(방전환 → 이후 코드가 뷰어에 scene_changepush),
    # revert로 다른 방 상태를 복원할 때(이때는 reset_robots = False로 로봇 리셋 없이 방만 로드)

    def load_scene(self, space, reset_robots=True):
        """방 전환. scenes/<space>.json 로드. 기본적으로 로봇은 새 방 도크에서 시작."""
        path = os.path.join(self.scenes_dir, "%s.json" % space)
        with open(path, encoding="utf-8") as f:
            self.scene = json.load(f)
        self.space = space
        if reset_robots or not self.robots:
            self.robots = {name: self.dock_state(name) for name in config.ROBOT_NAMES}
        return self.scene

    def dock_state(self, name):
        idx = list(config.ROBOT_NAMES).index(name)
        docks = (self.scene or {}).get("dock", [])
        if idx < len(docks):
            dx, dy = docks[idx]["x"], docks[idx]["y"]   # 방별 도크가 있으면 우선
        else:
            dx, dy = config.home_for(name)              # 기본: home_for()가 원점 구석 기준 자동 계산
        return {"robot": name, "active": "inactive", "x": dx, "y": dy, "rot": 0,
                "panel_left": 0, "panel_right": 0, "furniture": "none"}

#방 json 그대로 반환
    def environment(self):
        return self.scene

#기존 가구 목록만 반환
    def furniture(self):
        return (self.scene or {}).get("pre_existing_furniture", [])

#로봇들의 현재 상태
    def states(self):
        return [dict(self.robots[n]) for n in config.ROBOT_NAMES if n in self.robots]

#snap_panel->각도(rot)보정 함수
    def _clamp(self, st):
        """footprint(패널 포함)가 방 안에 들어오도록 위치 보정 (코드 보장 레이어)."""
        if self.scene:
            c = collision.clamp_to_bounds(st, self.scene["width"], self.scene["depth"])
            st["x"], st["y"] = round(c["x"]), round(c["y"])

    def transform(self, name, panel_left, panel_right, furniture=None):
        st = self.robots[name]
        st["active"] = "active"
        st["panel_left"] = collision.snap_panel(panel_left)
        st["panel_right"] = collision.snap_panel(panel_right)
        if furniture is not None:
            st["furniture"] = furniture
        self._clamp(st)   # 벽 앞에서 패널을 펼치면 안쪽으로 밀려남
        return dict(st)

    def move(self, name, x, y, rot=None):
        st = self.robots[name]
        st["active"] = "active"
        st["x"], st["y"] = x, y
        if rot is not None:
            st["rot"] = rot % 360
        self._clamp(st)
        return dict(st)

#초기화 처리
    def store(self, name):
        self.robots[name] = self.dock_state(name)
        return dict(self.robots[name])

    # ---------- history ----------

    def commit(self, description, intent_type="new_scene", utterance=""):
        self.turn += 1
        entry = {"turn": self.turn, "intent_type": intent_type, "space": self.space,
                 "utterance": utterance, "description": description,
                 "timestamp": datetime.now().isoformat(timespec="seconds"),
                 "state": copy.deepcopy(self.robots)}
        self.history.append(entry)
        self.save()
        return entry

    def commit_if_changed(self, description, intent_type="new_scene", utterance=""):
        """직전 커밋 이후 상태 변화가 없으면 재커밋하지 않는다.
        반환: (entry, changed) — changed=False면 기존 마지막 엔트리 (커밋 안 됨).
        호출부가 커밋 여부를 역추론하지 않게 플래그를 직접 준다."""
        if self.history and self.history[-1]["state"] == self.robots:
            return self.history[-1], False
        return self.commit(description, intent_type, utterance), True

    def revert_to(self, turn):
        """turn 번호의 스냅샷으로 결정론적 복원 (LLM 생성 스킵). 방이 다르면 방도 전환."""
        entry = next((h for h in self.history if h["turn"] == turn), None)
        if entry is None:
            return None
        if entry["space"] != self.space:
            self.load_scene(entry["space"], reset_robots=False)
        self.robots = copy.deepcopy(entry["state"])
        return entry

    def snapshot(self):
        """되돌릴 지점 (space + robots). history·turn·events는 건드리지 않는다 —
        events는 append-only이고(§17.6 D2), 커밋은 승인된 것만 남는다."""
        return {"space": self.space, "robots": copy.deepcopy(self.robots)}

    def restore(self, snap):
        """snapshot 시점으로 복원. 방이 바뀌었으면 True를 반환해 호출부가 push_scene 하게.

        revert_to(turn)와 다르다: 저건 승인·커밋된 과거 턴으로 돌아가는 사용자 기능이고,
        이건 승인 없이 끝난 턴의 실행을 물리는 내부 정정이라 history에 흔적을 남기지 않는다."""
        changed = snap["space"] != self.space
        if changed:
            self.load_scene(snap["space"], reset_robots=False)
        self.robots = copy.deepcopy(snap["robots"])
        return changed

#n턴 전 history조회
    def recent(self, n):
        return copy.deepcopy(self.history[-n:])

    # ---------- 저장 / 복구 ----------

    def save(self):
        os.makedirs(os.path.dirname(self.session_path) or ".", exist_ok=True)
        # events는 append-only 로그의 형제 필드 — SceneState는 소유하지 않고 직렬화 시점에
        # 읽어 얹기만 한다 (revert는 events를 건드리지 않는다, §17.6 D2).
        from services import eventlog
        data = {"space": self.space, "turn": self.turn,
                "robots": self.robots, "history": self.history,
                "events": eventlog.events()}
        # 원자적 쓰기 — 참가자 데이터는 재수집이 불가능하다. 임시 파일에 다 쓰고 교체하면
        # 쓰기 도중 종료돼도 직전 성공본이 온전히 남는다 (os.replace는 Windows에서도 원자적).
        # 커밋 없는 턴도 매번 save()되므로(§17.6 T10 신호) 노출 빈도가 커 원자성이 필요하다.
        tmp = self.session_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.session_path)

    def resume(self):
        """재시작 시 logs/session.json에서 복원. 성공하면 True."""
        if not os.path.exists(self.session_path):
            return False
        with open(self.session_path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("space"):
            self.load_scene(data["space"], reset_robots=True)
        self.robots = data.get("robots", self.robots)
        self.history = data.get("history", [])
        self.turn = data.get("turn", 0)
        return True
