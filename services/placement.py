# -*- coding: utf-8 -*-
"""배치 공용 헬퍼 + 연결 좌표 + 종합 검증 (순수 계산, 상태 없음).

형태층 후보 생성은 services/layout.py가 한다 (격자 스캔 + 앵커 밴드). 이 모듈은 그
후보 생성이 쓰는 public 헬퍼
(front_vec/anchor_geometry/dir8/nearby_items/panel_relation)와, 두 대 조합의 정밀
연결 좌표(find_connect), 종합 검증(feasibility)을 제공한다.

- find_connect: 두 대 조합의 정밀 연결 좌표 — 코드가 삼각함수를 풀어 준다.
- feasibility: 구성안의 물리 + 연결 기하 검증 (조화는 LLM+HITL-2 몫).
- panel_relation: 앵커와 가까운 쪽 패널이 right인지 left인지.

가구의 '앞' 규약: rot=0일 때 앞면은 +y. front 벡터 = (0,1)을 rot만큼 회전 = (-sin rot, cos rot).
(예: 소파 rot 180 → 앞면 -y. scene 시드와 뷰어도 이 규약을 따를 것)
"""
import math

from services import collision

# 아래 헬퍼는 형태층 후보 생성(services/layout.py)이 쓰는 public 함수다.
# (구 find_placement 격자·앵커 링 탐색은 layout으로 대체·제거, §6.4)

def front_vec(rot):
    """가구가 바라보는 방향 (rot=0 → +y). front = (-sin, cos)."""
    th = math.radians(rot or 0)
    return (-math.sin(th), math.cos(th))


def anchor_geometry(scene, near, robot_states=None):
    """near(가구 id·label / 로봇 이름) → (중심점, OBB rect, rot). 못 찾으면 (None,None,None)."""
    for f in (scene or {}).get("pre_existing_furniture", []):
        if f.get("id") == near or f.get("label") == near:
            return (f["x"], f["y"]), collision.furniture_rect(f), f.get("rot", 0)
    for st in robot_states or []:
        if st.get("robot") == near:
            return (st["x"], st["y"]), (st["x"], st["y"], collision.BODY,
                                        collision.BODY, st.get("rot", 0)), st.get("rot", 0)
    return None, None, None


def dir8(dx, dy):
    """중심 간 방향을 8방위 이름으로 (east=+x, north=+y — 뷰어·scene 좌표 규약)."""
    ang = (math.degrees(math.atan2(dy, dx)) + 360) % 360
    names = ("east", "northeast", "north", "northwest",
             "west", "southwest", "south", "southeast")
    return names[int(((ang + 22.5) % 360) // 45)]


def nearby_items(x, y, hw, hd, scene, robot_states, max_items=4):
    """주변 사물 사실 목록: 무엇이 어느 방향(8방위)·몇 cm 간격에 있는지.

    관계('앞'·'옆'·앵커)는 해석하지 않는다 — 그 판단은 LLM 몫이다."""
    proxy = (x, y, 2 * hw, 2 * hd, 0)
    items = []
    for f in (scene or {}).get("pre_existing_furniture", []):
        gap = collision.rect_gap(proxy, collision.furniture_rect(f))
        items.append((int(round(max(0.0, gap))), str(f.get("id") or f.get("label")),
                      f.get("label"), dir8(f["x"] - x, f["y"] - y)))
    for s in robot_states or []:
        gaps = [collision.rect_gap(proxy, rc) for rc in collision.footprint_rects(s)]
        label = "도크 대기" if s.get("active") == "inactive" \
            else (s.get("furniture") or "robot")
        items.append((int(round(max(0.0, min(gaps)))), str(s.get("robot")),
                      label, dir8(s["x"] - x, s["y"] - y)))
    items.sort(key=lambda t: (t[0], t[1]))
    return [{"id": iid, "label": lbl, "dir": dr, "dist": g}
            for g, iid, lbl, dr in items[:max_items]]


def panel_relation(x, y, rot, target):
    """앵커(기준)와 가까운 쪽에 달린 패널이 어느 쪽(right/left)인가 — 기하 사실 하나.

    '앞면이 기준을 향하는가'는 여기서 답하지 않는다. 그건 각도별 가정표가 아니라
    그 후보의 확정 각도에서 나오는 사실이어야 하고, layout.front_faces_ref가 답한다.
    (가정표는 Phase B에 3단계 교차참조를 시켜 오독이 2회 재현됐다.)"""
    th = math.radians(rot)
    dot = math.cos(th) * (target[0] - x) + math.sin(th) * (target[1] - y)
    return {"panel_on_anchor_side": "right" if dot >= 0 else "left"}


def find_connect(scene, all_states, anchor_name, mode="face", side="both",
                 anchor_panel=None, moving_panel=None, moving_robot=None):
    """두 대 조합의 정밀 연결 좌표 — 코드가 계산한다 (LLM이 삼각함수를 풀지 않게).

    mode "face": 마주보고 패널 맞대기. rot 차 180°,
                 중심 거리 = 본체 40 + 30·sinθa + 30·sinθb. connection_touching 통과 보장
                 (양쪽 각도가 0°·180°면 돌출이 0이라 본체끼리 gap 0으로 맞닿는다).
                 rot 차 180°에서는 같은 이름의 패널끼리 맞닿는다 → moving_side = 앵커 side.
    mode "side": 나란히 붙이기. rot 동일, 고정 패널 측면(앞/뒤)으로 본체 맞대기(거리 40).
    side: 앵커의 어느 패널 쪽(left/right)에 붙일지. "both"면 양쪽 후보.
    반환 후보: {"x","y","rot","tag","moving_side"} — 그대로 move_robot에 쓰면 된다."""
    anchor = next((s for s in all_states or [] if s.get("robot") == anchor_name), None)
    if anchor is None:
        return []
    w, d = scene["width"], scene["depth"]
    ax, ay, arot = anchor["x"], anchor["y"], anchor.get("rot", 0)
    th = math.radians(arot)
    u = (math.cos(th), math.sin(th))            # 앵커 panel_right 방향 (규약: collision.py)
    perp = (-u[1], u[0])                        # 고정 패널(앞/뒤) 방향
    obstacles = [collision.furniture_rect(f)
                 for f in scene.get("pre_existing_furniture", [])]
    for s in all_states:
        if s.get("robot") not in (anchor_name, moving_robot):
            obstacles += collision.footprint_rects(s)

    out = []
    if mode == "face":
        pa = collision.panel_protrusion(anchor_panel if anchor_panel is not None else 90)
        pm = collision.panel_protrusion(moving_panel if moving_panel is not None else 90)
        dist = collision.BODY + pa + pm
        sides = ("left", "right") if side not in ("left", "right") else (side,)
        for sd in sides:
            sign = 1 if sd == "right" else -1
            px, py = ax + sign * u[0] * dist, ay + sign * u[1] * dist
            mv = {"x": px, "y": py, "rot": (arot + 180) % 360,
                  "panel_left": 0, "panel_right": 0}
            mv["panel_%s" % sd] = moving_panel if moving_panel is not None else 90
            if collision.out_of_bounds(mv, w, d):
                continue
            if any(collision.rects_collide(r, rc)
                   for r in collision.footprint_rects(mv) for rc in obstacles):
                continue
            out.append({"x": round(px), "y": round(py), "rot": (arot + 180) % 360,
                        "tag": "connect_face_%s" % sd, "moving_side": sd})
    else:   # side-by-side (나란히): 앵커의 앞/뒤로 본체를 붙인다 (패널 축은 비워 둠)
        for name, sign in (("front", 1), ("back", -1)):
            px, py = ax + sign * perp[0] * collision.BODY, ay + sign * perp[1] * collision.BODY
            mv = {"x": px, "y": py, "rot": arot, "panel_left": 0, "panel_right": 0}
            if collision.out_of_bounds(mv, w, d):
                continue
            if any(collision.rects_collide(r, rc)
                   for r in collision.footprint_rects(mv) for rc in obstacles):
                continue
            out.append({"x": round(px), "y": round(py), "rot": arot,
                        "tag": "connect_side_%s" % name, "moving_side": None})
    return out

# ---------- 종합 검증 (check_feasibility의 몸체) ----------

def feasibility(robot_states, scene, connections=None):
    """물리 검증 + (connections가 있을 때만) 연결 기하 검증.

    connections는 layout._connected_combos의 face 모드에서만 채워진다 — side 모드는
    본체를 맞대므로 패널 접촉을 요구하지 않고, layout._feasible(비연결 경로)은 애초에
    connection 분기 앞에서 갈린다. collision.validate_layout을 직접 부르는 곳
    (placement_tools._with_issues·main.handle_logged)과의 차이는 이 연결 검증뿐이다.
    → 둘을 합치면 _connected_combos의 연결 검증이 사라진다. 합치지 마라."""
    #물리 검증
    issues = collision.validate_layout(robot_states, scene)
    #연결 검증
    by_name = {st.get("robot"): st for st in robot_states}
    for con in connections or []:
        a, b = by_name.get(con.get("robot_a")), by_name.get(con.get("robot_b"))
        if a is None or b is None:
            issues.append({"type": "connection_unknown_robot", "connection": con})
            continue
        if not collision.connection_touching(a, con.get("side_a", "right"),
                                             b, con.get("side_b", "right")):
            issues.append({"type": "connection_gap",
                           "robots": [a.get("robot"), b.get("robot")]})
    return {"feasible": not issues, "issues": issues}
#issue가 있으면 feasible이 false/어떤 issue인지 반환
