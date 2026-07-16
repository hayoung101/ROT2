# -*- coding: utf-8 -*-
"""배치 탐색 + 종합 검증 (순수 계산, 상태 없음).

find_placement — 좌표 찝기의 역할 분담:
  코드는 '로봇이 가구가 될 수 있는 가용 공간'을 계산해 제안하고,
  좋은 자리의 선택·방향·관계 판단은 LLM이 한다. user 좌표는 쓰지 않는다.

- 조사 모드 (near=None): 방의 빈 공간 후보를 관계 해석 없이 '기하 사실'로 서술 —
  clearance(최소 여유) + free(동서남북 4방향 빈 거리) + nearby(주변 사물의 방향·거리).
  무엇을 마주 볼지·어떤 관계를 맺을지는 후보가 정하지 않는다 (LLM 몫).
  rot_suggest·패널 관계값 없음 — 좌우는 move 후 panel_orientation(실측)으로.
- 앵커 모드 (near=가구 id/label·로봇 이름): 그 앵커와 '마주 보는' 관계를 만들 때 —
  인접 후보(tag: <id>_front/_side/_back) + 앵커를 바라보는 rot_suggest +
  패널의 물리적 위치와 각도별 앞면 방향 (rot_suggest 채택 시에만 유효).
- feasibility: check_feasibility tool의 몸체 (물리 + 연결 기하만. 조화는 LLM+HITL-2 몫)

가구의 '앞' 규약: rot=0일 때 앞면은 +y. front 벡터 = (0,1)을 rot만큼 회전 = (-sin rot, cos rot).
(예: 소파 rot 180 → 앞면 -y. scene 시드와 뷰어도 이 규약을 따를 것)
"""
import math

from services import collision

CLEARANCE_CM = 5.0        # 앵커 가장자리와 후보 사이 기본 여유
AVOID_MARGIN_CM = 40.0    # avoid 대상과의 최소 이격
STEP_DEG = 15             # 앵커 링 탐색 방향 간격
GRID_CM = 20              # 조사 모드 격자 간격
SURVEY_MIN_APART = 80     # 조사 모드 후보끼리 최소 거리 (방을 대표하게 분산)

#가구가 바라보는 방향
def _front_vec(rot):
    th = math.radians(rot or 0)
    return (-math.sin(th), math.cos(th))


def _anchor_geometry(scene, near, robot_states=None):
    """near(가구 id·label / 로봇 이름) → (중심점, OBB rect, rot). 못 찾으면 (None,None,None)."""
    for f in (scene or {}).get("pre_existing_furniture", []):
        if f.get("id") == near or f.get("label") == near:
            return (f["x"], f["y"]), collision.furniture_rect(f), f.get("rot", 0)
    for st in robot_states or []:
        if st.get("robot") == near:
            return (st["x"], st["y"]), (st["x"], st["y"], collision.BODY,
                                        collision.BODY, st.get("rot", 0)), st.get("rot", 0)
    return None, None, None


def _half_extent(rect, direction):
    """rect 중심에서 direction 방향으로의 support 거리 (그 방향 반폭)."""
    cx, cy = rect[0], rect[1]
    return max((px - cx) * direction[0] + (py - cy) * direction[1]
               for px, py in collision.rect_corners(*rect))


def _ok(x, y, hw, hd, w, d, rects, avoid_pts):
    if not (hw <= x <= w - hw and hd <= y <= d - hd):
        return False
    proxy = (x, y, 2 * hw, 2 * hd, 0)
    if any(collision.rects_collide(proxy, rc) for rc in rects):
        return False
    if any(math.hypot(x - px, y - py) < max(hw, hd) + AVOID_MARGIN_CM for px, py in avoid_pts):
        return False
    return True

#주변 여유 계산
def _clearance(x, y, hw, hd, w, d, rects):
    """후보 주변 여유(cm): 벽·장애물까지의 최소 간격."""
    wall = min(x - hw, y - hd, w - (x + hw), d - (y + hd))
    proxy = (x, y, 2 * hw, 2 * hd, 0)
    gaps = [collision.rect_gap(proxy, rc) for rc in rects]
    g = min(gaps) if gaps else wall
    return int(round(max(0.0, min(wall, g))))


def _dir8(dx, dy):
    """중심 간 방향을 8방위 이름으로 (east=+x, north=+y — 뷰어·scene 좌표 규약)."""
    ang = (math.degrees(math.atan2(dy, dx)) + 360) % 360
    names = ("east", "northeast", "north", "northwest",
             "west", "southwest", "south", "southeast")
    return names[int(((ang + 22.5) % 360) // 45)]


def _free_extents(x, y, hw, hd, w, d, rects, step=5.0):
    """이 지점의 footprint가 동서남북 각 방향으로 밀려갈 수 있는 빈 거리(cm).

    후보가 '어느 쪽이 얼마나 트여 있는지'를 사실로 서술한다 — 그 사실로
    방향(rot)·동선을 판단하는 것은 LLM 몫이다."""
    out = {}
    limit = max(w, d)
    for name, ux, uy in (("east", 1, 0), ("west", -1, 0),
                         ("north", 0, 1), ("south", 0, -1)):
        dist = 0.0
        while dist < limit:
            nx, ny = x + ux * (dist + step), y + uy * (dist + step)
            if not (hw <= nx <= w - hw and hd <= ny <= d - hd):
                break
            proxy = (nx, ny, 2 * hw, 2 * hd, 0)
            if any(collision.rects_collide(proxy, rc) for rc in rects):
                break
            dist += step
        out[name] = int(round(dist))
    return out


def _nearby(x, y, hw, hd, scene, robot_states, max_items=4):
    """주변 사물 사실 목록: 무엇이 어느 방향(8방위)·몇 cm 간격에 있는지.

    관계('앞'·'옆'·앵커)는 해석하지 않는다 — 그 판단은 LLM 몫이다."""
    proxy = (x, y, 2 * hw, 2 * hd, 0)
    items = []
    for f in (scene or {}).get("pre_existing_furniture", []):
        gap = collision.rect_gap(proxy, collision.furniture_rect(f))
        items.append((int(round(max(0.0, gap))), str(f.get("id") or f.get("label")),
                      f.get("label"), _dir8(f["x"] - x, f["y"] - y)))
    for s in robot_states or []:
        gaps = [collision.rect_gap(proxy, rc) for rc in collision.footprint_rects(s)]
        label = "도크 대기" if s.get("active") == "inactive" \
            else (s.get("furniture") or "robot")
        items.append((int(round(max(0.0, min(gaps)))), str(s.get("robot")),
                      label, _dir8(s["x"] - x, s["y"] - y)))
    items.sort(key=lambda t: (t[0], t[1]))
    return [{"id": iid, "label": lbl, "dir": dr, "dist": g}
            for g, iid, lbl, dr in items[:max_items]]


#로봇 배치 후보 묶어서 내보내기 (앵커 모드 전용)
def _panel_relation(x, y, rot, target):
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


def _cand(x, y, tag, clearance, rot_suggest, order, target=None):
    c = {"x": round(x), "y": round(y), "tag": tag,
         "clearance": clearance, "rot_suggest": round(rot_suggest) % 360,
         "_ord": order}
    # 위치(toward/away 같은 의미 편향 어휘를 쓰지 않음)와 기능면 방향을 분리한다.
    # 규약(collision.py): panel_right는 rot 방향(+x축이 rot만큼 회전)을,
    # panel_left는 그 반대를 향한다.
    if target is not None:
        c.update(_panel_relation(x, y, rot_suggest, target))
    return c


def find_placement(scene, fixed_states, footprint_radius=None, near=None, avoid=(), k=6,
                   footprint_w=None, footprint_d=None):
    """가용 공간 후보 제안.

    조사 모드(near=None) 반환: [{"x","y","clearance","free":{동서남북 빈 거리},
      "nearby":[{id,label,dir,dist}]}] — 관계 라벨·rot_suggest 없음 (기하 사실만).
    앵커 모드(near=id) 반환: [{"x","y","tag","clearance","rot_suggest",
      "panel_on_anchor_side","panel_on_opposite_side","front_face_toward_anchor"}]
      — 마주 보는 관계 전용.

    footprint_radius: 배치할 구성의 대략 반경(cm) — 패널 펼침 포함해 LLM이 추정."""
    w, d = scene["width"], scene["depth"]
    # 정사각 proxy(반경) 또는 직사각 proxy(w×d — 풀확장 100×40처럼 길쭉한 구성용)
    if footprint_w is not None and footprint_d is not None:
        hw, hd = float(footprint_w) / 2, float(footprint_d) / 2
    else:
        hw = hd = float(footprint_radius if footprint_radius is not None else 29)
    furn = scene.get("pre_existing_furniture", [])
    rects_all = [collision.furniture_rect(f) for f in furn]
    for st in fixed_states or []:
        rects_all += collision.footprint_rects(st)
    avoid_pts = []
    for item in avoid or ():
        pt, _, _ = _anchor_geometry(scene, item, fixed_states)
        if pt:
            avoid_pts.append(pt)

    if near is None:
        # ---- 조사 모드: 가용 공간 추출 (관계 라벨 없음 — 기하 사실만) ----
        # 격자 스캔으로 놓일 수 있는 지점을 모으고, 여유가 큰 순서대로 서로
        # 떨어진 k개를 대표로 뽑는다. 후보는 free·nearby로 스스로를 서술할 뿐
        # 무엇을 마주 볼지 정하지 않는다 — rot_suggest·패널 관계값은 앵커 모드 전용.
        spots = []
        for i in range(1, int(w // GRID_CM)):
            for j in range(1, int(d // GRID_CM)):
                x, y = i * GRID_CM, j * GRID_CM
                if _ok(x, y, hw, hd, w, d, rects_all, avoid_pts):
                    spots.append((_clearance(x, y, hw, hd, w, d, rects_all), x, y))
        spots.sort(reverse=True)

        def _pick(min_apart):
            picked = []
            for c, x, y in spots:
                if all(math.hypot(x - px, y - py) >= min_apart for _, px, py in picked):
                    picked.append((c, x, y))
                if len(picked) == k:
                    break
            return picked

        picked = _pick(SURVEY_MIN_APART)
        if len(picked) < 3:   # 좁은 방·장애물 과다 → 이격 기준을 낮춰 재선정
            picked = _pick(SURVEY_MIN_APART / 2)
        return [{"x": round(x), "y": round(y), "clearance": c,
                 "free": _free_extents(x, y, hw, hd, w, d, rects_all),
                 "nearby": _nearby(x, y, hw, hd, scene, fixed_states)}
                for c, x, y in picked]

    # ---- 앵커 모드: 지정 가구/로봇 주변 링 탐색 (마주 보는 관계 전용) ----
    out = []
    anchor, arect, arot = _anchor_geometry(scene, near, fixed_states)
    if anchor is None:
        return []
    ax, ay = anchor
    fv = _front_vec(arot)
    rects_excl = [rc for rc in rects_all if rc != arect]
    for deg in range(0, 360, STEP_DEG):
        th = math.radians(deg)
        dirv = (math.cos(th), math.sin(th))
        dist = _half_extent(arect, dirv) + (hw * abs(dirv[0]) + hd * abs(dirv[1])) + CLEARANCE_CM
        x, y = ax + dirv[0] * dist, ay + dirv[1] * dist
        if not _ok(x, y, hw, hd, w, d, rects_all, avoid_pts):
            continue
        dot = dirv[0] * fv[0] + dirv[1] * fv[1]
        side = "front" if dot > 0.5 else ("back" if dot < -0.5 else "side")
        out.append(_cand(x, y, "%s_%s" % (near, side),
                         _clearance(x, y, hw, hd, w, d, rects_excl),
                         math.degrees(math.atan2(ay - y, ax - x)),
                         {"front": 0, "side": 1, "back": 2}[side],
                         target=(ax, ay)))

    out.sort(key=lambda c: (c["_ord"], -c["clearance"]))
    for c in out:
        c.pop("_ord", None)
    return out[:k]
#추천하는 배치 구역을 목록으로 내보냄


def panel_orientation(state, scene, others=(), max_dist=150):
    """실행 '후' 실제 rot·위치 기준으로 주변 앵커와 패널 관계를 재계산한다.

    find_placement 후보의 패널 관계 값은 rot_suggest 채택을 전제한 계획값이라,
    LLM이 rot을 바꾸거나 후보를 섞어 쓰면 조용히 무효가 된다. 이 함수는 move 직후의
    확정 상태에서 항상 신선한 값을 공급해 그 스테일 문제를 원천 차단한다.
    조사 모드(기하 사실만 제공)로 배치한 경우에도 이 값이 패널 좌우의 유일한 근거다.

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
        o = _panel_relation(x, y, rot, (ax, ay))
        if abs(dot) / dist < 0.35:   # 앵커가 패널 축에서 벗어남 → 고정 측면이 향하는 방향
            o["off_axis"] = True
        out[aid] = o
    return out


def find_connect(scene, all_states, anchor_name, mode="face", side="both",
                 anchor_panel=None, moving_panel=None, moving_robot=None):
    """두 대 조합의 정밀 연결 좌표 — 코드가 계산한다 (LLM이 삼각함수를 풀지 않게).

    mode "face": 마주보고 패널 맞대기. rot 차 180°,
                 중심 거리 = 본체 40 + 30·sinθa + 30·sinθb. panels_touching 통과 보장.
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
        if not collision.panels_touching(a, con.get("side_a", "right"),
                                         b, con.get("side_b", "right")):
            issues.append({"type": "connection_gap",
                           "robots": [a.get("robot"), b.get("robot")]})
    return {"feasible": not issues, "issues": issues}
#issue가 있으면 feasible이 false/어떤 issue인지 반환
