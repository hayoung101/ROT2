# -*- coding: utf-8 -*-
"""배치 공용 헬퍼 + 연결 좌표 + 종합 검증 (순수 계산, 상태 없음).

형태층 후보 생성은 services/layout.py가 한다 (격자 스캔 + 앵커 밴드). 이 모듈은 그
후보 생성과 move 후 재계산(panel_orientation)이 공유하는 public 헬퍼
(front_vec/anchor_geometry/dir8/nearby_items/panel_relation)와, 두 대 조합의 정밀
연결 좌표(find_connect), 종합 검증(feasibility)을 제공한다.

- find_connect: 두 대 조합의 정밀 연결 좌표 — 코드가 삼각함수를 풀어 준다.
- feasibility: 구성안의 물리 + 연결 기하 검증 (조화는 LLM+HITL-2 몫).
- panel_relation / panel_orientation: 앵커 기준 패널 위치·각도별 앞면 방향.

가구의 '앞' 규약: rot=0일 때 앞면은 +y. front 벡터 = (0,1)을 rot만큼 회전 = (-sin rot, cos rot).
(예: 소파 rot 180 → 앞면 -y. scene 시드와 뷰어도 이 규약을 따를 것)
"""
import math

from services import collision

# 아래 헬퍼는 형태층 후보 생성(services/layout.py)과 move 후 재계산(panel_orientation)이
# 공유하는 public 함수다. (구 find_placement 격자·앵커 링 탐색은 layout으로 대체·제거, §6.4)

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
    """앵커 기준 패널 위치와 각도별 앞면 방향을 기하 사실로 반환한다.

    panel_on_anchor_side는 단지 앵커와 가까운 쪽에 달린 패널이다. 패널의
    '앞면' 방향은 힌지 각도에 따라 달라지므로 별도로 제공한다: 45°는
    바깥쪽, 90°는 위쪽, 135°/180°는 본체 쪽을 향한다.
    """
    th = math.radians(rot)
    dot = math.cos(th) * (target[0] - x) + math.sin(th) * (target[1] - y)
    anchor_side = "right" if dot >= 0 else "left"
    opposite_side = "left" if anchor_side == "right" else "right"
    return {
        "panel_on_anchor_side": anchor_side,
        "panel_on_opposite_side": opposite_side,
        "front_face_toward_anchor": {
            "45": anchor_side,
            "90": "either",
            "135": opposite_side,
            "180": opposite_side,
        },
    }


def panel_orientation(state, scene, others=(), max_dist=150):
    """실행 '후' 실제 rot·위치 기준으로 주변 앵커와 패널 관계를 재계산한다.

    형태층은 후보 생성 중에 layout.panel_faces(panel_relation)로 같은 값을 미리 붙이지만,
    이 함수는 UI 수동 조작(§16.1)처럼 후보를 거치지 않은 배치에서도 move 직후의 확정
    상태에서 신선한 패널 관계를 공급한다.

    반환에는 앵커 쪽/반대쪽 패널의 물리적 위치와, 45/90/135/180°에서
    어느 패널의 앞면이 앵커를 향하는지가 분리되어 있다. max_dist(cm) 안의
    가구·active 로봇만 포함하며, 앵커가 가동 패널 축에서 크게 벗어나 있으면
    (≈70° 이상, 고정 측면 방향) off_axis=True를 함께 준다."""
    x, y, rot = state["x"], state["y"], state.get("rot", 0)
    th = math.radians(rot)
    anchors = [(f.get("id"), f["x"], f["y"])
               for f in (scene or {}).get("pre_existing_furniture", [])]
    anchors += [(s.get("robot"), s["x"], s["y"]) for s in others or ()
                if s.get("robot") != state.get("robot")
                and s.get("active") != "inactive"]
    out = {}
    for aid, ax, ay in anchors:
        dx, dy = ax - x, ay - y
        dist = math.hypot(dx, dy)
        if dist > max_dist or dist < 1e-6:
            continue
        dot = math.cos(th) * dx + math.sin(th) * dy   # panel_right 축과의 정렬
        o = panel_relation(x, y, rot, (ax, ay))
        if abs(dot) / dist < 0.35:   # 앵커가 패널 축에서 벗어남 → 고정 측면이 향하는 방향
            o["off_axis"] = True
        out[aid] = o
    return out


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
