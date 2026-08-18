# -*- coding: utf-8 -*-
"""2대 조합 motif가 실제로 구현 가능한지 측정한다 (읽기 전용 — 데이터·프롬프트 안 고침).

python scripts/probe_pair_motifs.py

왜: furniture_motifs.json의 2대 motif 8개는 panel_inner/panel_outer + arrangement로 적혀
있는데, Phase A 스키마는 로봇별 panels [pA,pB] 순서쌍 + connection{mode,side}다. 번역 규칙이
어디에도 없고, arrangement 이름(side_by_side/facing/right_angle)이 코드 어휘(face/side)와
같은 뜻인지도 확인된 적이 없다. 규칙을 쓰기 전에 무엇이 실제로 되는지 잰다.

두 숫자를 따로 센다 — 0의 원인이 다르면 처방도 다르다:
  find_connect 원후보 = 0  → 기하 자체가 안 나옴 (맞댈 면 없음 / 벽·가구에 막힘)
  원후보 N, 최종 0        → feasibility(connection_touching)가 다 걸러냄

방을 둘 재는 이유: 거실에서만 0이면 '방이 좁아서'인지 '기하가 불가능해서'인지 못 가른다.
빈 방(500×400, 가구 없음)이 그 대조군이다.

번역 규약: _panel_for_side(panels, side)가 side="right"면 panels[0]을 쓰므로
  panels = [inner, outer] + connection.side="right" 가 motif 그대로다.
  ([outer, inner] + side="left"는 그 거울상이라 같은 기하 — 한쪽만 잰다.)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import collision, layout, placement   # noqa: E402

DOCK = [{"robot": "BOT 1", "active": "inactive", "x": 380, "y": 20, "rot": 0,
         "panel_left": 0, "panel_right": 0},
        {"robot": "BOT 2", "active": "inactive", "x": 320, "y": 20, "rot": 0,
         "panel_left": 0, "panel_right": 0}]

ROOMS = [("거실", json.load(open("scenes/living_room.json", encoding="utf-8"))),
         ("빈방500", {"width": 500, "depth": 400, "pre_existing_furniture": []})]


def unit(name, panels):
    return {"robot": name, "furniture": "probe", "panels": list(panels),
            "relation": {"mode": "pair", "anchor": None}, "rationale": "probe"}


def _span(combo):
    """조합이 실제로 만든 도형을 앵커 로컬축으로 잰다 → (u축 길이, v축 길이, rot쌍).

    '조합이 나왔다'와 'motif가 말한 형태가 나왔다'는 다른 문제다. large_worktable의
    "약 140×40"은 30+40+40+30이라 두 본체가 **패널 축(u)으로** 맞닿아야 나오는 수인데,
    코드의 side 모드는 앞/뒤(v축)로 맞대므로 같은 이름이라도 다른 도형이 된다.
    월드 축정렬 bbox는 rot 45에서 부풀어 비교가 안 되므로 로컬축으로 잰다."""
    import math
    ps = combo["placements"]
    rot = ps[0]["rot"]
    th = math.radians(rot)
    u, v = (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))
    pts = []
    for p in ps:
        st = {"robot": p["robot"], "active": "active", "x": p["x"], "y": p["y"],
              "rot": p["rot"], "panel_right": p["panels"][0], "panel_left": p["panels"][1]}
        for r in collision.footprint_rects(st):
            pts += collision.rect_corners(*r)
    su = max(px * u[0] + py * u[1] for px, py in pts) - \
        min(px * u[0] + py * u[1] for px, py in pts)
    sv = max(px * v[0] + py * v[1] for px, py in pts) - \
        min(px * v[0] + py * v[1] for px, py in pts)
    return int(round(su)), int(round(sv)), (ps[0]["rot"], ps[1]["rot"])


def probe(scene, panels_a, panels_b, mode):
    """(anchor 후보 수, find_connect 원후보 합, 최종 조합 수). find_connect를 감싸 센다."""
    raw = {"n": 0}
    real = placement.find_connect

    def counting(*a, **k):
        out = real(*a, **k)
        raw["n"] += len(out)
        return out

    placement.find_connect = counting
    try:
        units = [unit("BOT 1", panels_a), unit("BOT 2", panels_b)]
        n_anchor = len(layout._unit_candidates(units[0], scene, [], ["BOT 2"]))
        combos = layout.enumerate_units(
            units, scene, DOCK,
            connection={"anchor": "BOT 1", "moving": "BOT 2",
                        "mode": mode, "side": "right"})
    finally:
        placement.find_connect = real
    return n_anchor, raw["n"], len(combos), (_span(combos[0]) if combos else None)


def main():
    data = json.load(open("data/furniture_motifs.json", encoding="utf-8"))
    pairs = [(k, m) for k, m in data["motifs"].items() if m.get("robots") == 2]
    print("2대 motif %d개 · 방 %d곳 · mode 2종\n" % (len(pairs), len(ROOMS)))
    print("%-26s %-12s %-6s %-7s %5s %5s %5s  %-16s %s"
          % ("motif", "arrangement", "mode", "room",
             "anch", "raw", "final", "실제 도형(u×v,rot)", "비고"))
    print("-" * 140)
    for key, m in pairs:
        pa = m["panels"][0]
        pb = m["panels"][1] if len(m["panels"]) > 1 else pa
        io = "%s,%s / %s,%s" % (pa["panel_inner"], pb["panel_inner"],
                                pa["panel_outer"], pb["panel_outer"])
        panels_a = [pa["panel_inner"], pa["panel_outer"]]
        panels_b = [pb["panel_inner"], pb["panel_outer"]]
        # 맞댈 면이 물리적으로 존재하는가 (0°=접힘, 180°=수직 → 바닥 돌출 없음)
        no_face = [a for a in (pa["panel_inner"], pb["panel_inner"])
                   if collision.panel_protrusion(a) <= 0]
        print("· %s (%s) inner/outer %s | 설명: %s"
              % (key, m.get("arrangement", "-"), io, m["description"][:52]))
        for mode in ("face", "side"):
            for room_name, scene in ROOMS:
                n_anchor, raw, final, span = probe(scene, panels_a, panels_b, mode)
                note = []
                if m.get("arrangement") == "right_angle":
                    note.append("arrangement=right_angle: find_connect 미지원")
                if mode == "face" and no_face:
                    note.append("inner %s → 바닥 돌출 0 = 본체 맞댐"
                                % "·".join(str(a) for a in no_face))
                if raw and not final:
                    note.append("feasibility가 전부 탈락")
                if not raw:
                    note.append("find_connect 원후보 0")
                shape = ("%d×%d rot%s,%s" % (span[0], span[1], span[2][0], span[2][1])
                         if span else "-")
                print("%-26s %-12s %-6s %-7s %5d %5d %5d  %-16s %s"
                      % (key, m.get("arrangement", "-"), mode, room_name,
                         n_anchor, raw, final, shape, "; ".join(note)))
        print()


if __name__ == "__main__":
    main()
