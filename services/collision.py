# -*- coding: utf-8 -*-
"""BoT² OBB(회전된 사각형) 충돌 계산 — 순수 기하, 상태 없음 (pytest 단독 검증 가능).

좌표 규약:
- 방 좌표 [0..w]×[0..d] cm. rot은 도 단위 반시계(CCW).
- rot=0일 때 panel_left는 -x쪽, panel_right는 +x쪽 측면에 달려 있다.
- 패널의 바닥 수평 돌출 = PANEL_LEN × sin(각도) → 0°/180°는 0, 90°는 30, 45°/135°는 약 21.
- viewer.js의 rot 부호 규약과 반드시 일치시킬 것.

핵심 표현: rect = (cx, cy, w, d, deg) — 중심, 폭(로컬 x), 깊이(로컬 y), 회전각.
"""
import math

import config

BODY = config.BODY_W_CM            # 본체 바닥 40×40
PANEL_LEN = config.PANEL_LEN_CM    # 30
PANEL_ANGLES = config.PANEL_ANGLES # (0, 45, 90, 135, 180)
DEFAULT_SLACK = 2.0                # 맞닿음(연결) 허용 오차 cm — 이 깊이 이하의 겹침은 충돌로 안 봄
EPS = 1e-6


# ---------- 패널 기하 ----------

def snap_panel(angle):
    """패널 각도를 허용 5단계 {0,45,90,135,180} 중 가장 가까운 값으로."""
    try:
        a = float(angle)
    except (TypeError, ValueError):
        return 0
    a = max(0.0, min(180.0, a))
    return min(PANEL_ANGLES, key=lambda s: abs(s - a))

#사영된 길이 구하기
def panel_protrusion(angle):
    """패널 바닥 수평 돌출(cm) = 30·sin(θ)."""
    return PANEL_LEN * math.sin(math.radians(snap_panel(angle)))


# ---------- OBB 기본 연산 ----------
#회전된 사각형 좌표 구하기
def rect_corners(cx, cy, w, d, deg):
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    pts = []
    for dx, dy in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)):
        pts.append((cx + dx * c - dy * s, cy + dx * s + dy * c))
    return pts


def _axes(deg):
    th = math.radians(deg)
    return ((math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th)))


def _project(corners, axis):
    vals = [px * axis[0] + py * axis[1] for px, py in corners]
    return min(vals), max(vals)


def obb_penetration(r1, r2):
    """SAT 침투 깊이. 반환 (depth, axis).
    depth > 0: 겹침(그 값만큼 침투). depth <= 0: 분리(-depth는 간격 하한).
    axis는 r1을 r2에서 밀어내는 단위 방향."""
    c1, c2 = rect_corners(*r1), rect_corners(*r2)
    min_overlap = math.inf
    best_axis = (1.0, 0.0)
    for deg in (r1[4], r2[4]):
        for ax in _axes(deg):
            lo1, hi1 = _project(c1, ax)
            lo2, hi2 = _project(c2, ax)
            overlap = min(hi1, hi2) - max(lo1, lo2)
            if overlap < min_overlap:
                min_overlap = overlap
                best_axis = ax
    # 밀어내는 방향: r1 중심이 r2 중심에서 멀어지는 쪽
    dx, dy = r1[0] - r2[0], r1[1] - r2[1]
    if dx * best_axis[0] + dy * best_axis[1] < 0:
        best_axis = (-best_axis[0], -best_axis[1])
    return min_overlap, best_axis

#충돌감지(2cm 이상 겹칩->충돌)
def rects_collide(r1, r2, slack=DEFAULT_SLACK):
    depth, _ = obb_penetration(r1, r2)
    return depth > slack


def rect_gap(r1, r2):
    """두 OBB 사이 간격 하한(cm). 겹치면 0."""
    depth, _ = obb_penetration(r1, r2)
    return max(0.0, -depth)


# ---------- 로봇 footprint ----------
#로봇 점유 바닥 면적 계산
def footprint_rects(state):
    """로봇 state → 바닥 점유 OBB 목록. 본체 1개 + 돌출량이 있는 패널."""
    x, y, rot = state["x"], state["y"], state.get("rot", 0)
    rects = [(x, y, BODY, BODY, rot)]
    th = math.radians(rot)
    for sign, key in ((-1, "panel_left"), (1, "panel_right")):
        p = panel_protrusion(state.get(key, 0))
        if p > EPS:
            off = sign * (BODY / 2 + p / 2)
            rects.append((x + off * math.cos(th), y + off * math.sin(th), p, BODY, rot))
    return rects

#패널 바닥 계산
def panel_rect(state, side):
    """side('left'/'right') 패널의 바닥 OBB. 돌출이 없으면(0°/180°) None."""
    x, y, rot = state["x"], state["y"], state.get("rot", 0)
    sign = -1 if side == "left" else 1
    p = panel_protrusion(state.get("panel_%s" % side, 0))
    if p <= EPS:
        return None
    th = math.radians(rot)
    off = sign * (BODY / 2 + p / 2)
    return (x + off * math.cos(th), y + off * math.sin(th), p, BODY, rot)


def footprint_bbox(state):
    xs, ys = [], []
    for r in footprint_rects(state):
        for px, py in rect_corners(*r):
            xs.append(px)
            ys.append(py)
    return min(xs), min(ys), max(xs), max(ys)


# ---------- 충돌 판정 ----------

def robots_collide(a, b, slack=DEFAULT_SLACK):
    return any(rects_collide(ra, rb, slack) for ra in footprint_rects(a) for rb in footprint_rects(b))

#기존 scene 가구 항목 좌표
def furniture_rect(f):
    return (f["x"], f["y"], f["w"], f["d"], f.get("rot", 0))

#로봇과 기존 가구 충돌 검사
def robot_hits_furniture(state, furniture, slack=DEFAULT_SLACK):
    fr = furniture_rect(furniture)
    return any(rects_collide(r, fr, slack) for r in footprint_rects(state))


def panels_touching(a, side_a, b, side_b, tol=3.0, min_align=30.0):
    """두 로봇의 지정 패널 끝이 맞닿아 있는가 (연결 기하 검증용).
    거리 조건(끝이 ±tol 이내)과 정렬 조건(패널 폭 방향 겹침 ≥ min_align)을
    모두 만족해야 한다 — 모서리만 스치는 배치는 연결이 아니다."""
    ra, rb = panel_rect(a, side_a), panel_rect(b, side_b)
    if ra is None or rb is None:
        return False
    depth, _ = obb_penetration(ra, rb)
    if not (-tol <= depth <= tol):   # 간격도 침투도 tol 이내
        return False
    axw = _axes(ra[4])[1]            # 패널 폭 방향 (로컬 y)
    lo1, hi1 = _project(rect_corners(*ra), axw)
    lo2, hi2 = _project(rect_corners(*rb), axw)
    return min(hi1, hi2) - max(lo1, lo2) >= min_align


# ---------- 경계 ----------
#방을 벗어나는지
def out_of_bounds(state, room_w, room_d, tol=0.5):
    x0, y0, x1, y1 = footprint_bbox(state)
    return x0 < -tol or y0 < -tol or x1 > room_w + tol or y1 > room_d + tol

#벗어난 만큼 중심을 안으로
def clamp_to_bounds(state, room_w, room_d):
    """footprint 전체(패널 포함)가 방 안에 들어오도록 중심을 평행이동한 사본 반환."""
    st = dict(state)
    x0, y0, x1, y1 = footprint_bbox(st)
    dx = -x0 if x0 < 0 else (room_w - x1 if x1 > room_w else 0.0)
    dy = -y0 if y0 < 0 else (room_d - y1 if y1 > room_d else 0.0)
    st["x"] = st["x"] + dx
    st["y"] = st["y"] + dy
    return st


# ---------- 배치 해소 ----------

def place_without_overlap(state, fixed_rects, room_w, room_d,
                          slack=DEFAULT_SLACK, max_iter=25):
    """고정 OBB들과 겹치지 않는 위치로 state를 밀어낸 사본 반환. 실패 시 None.
    fixed_rects: 기존 가구·다른 로봇의 footprint rect 목록."""
    st = dict(state)
    for _ in range(max_iter):
        st = clamp_to_bounds(st, room_w, room_d)
        worst = None
        for r in footprint_rects(st):
            for fr in fixed_rects:
                depth, axis = obb_penetration(r, fr)
                if depth > slack and (worst is None or depth > worst[0]):
                    worst = (depth, axis)
        if worst is None:
            return st
        depth, (ax, ay) = worst
        step = depth - slack + 0.5
        st["x"] += step * ax
        st["y"] += step * ay
    return None


def _worst_overlap(rects_a, rects_b, slack=DEFAULT_SLACK):
    """두 rect 목록 사이 최대 침투 (depth, axis). 겹침 없으면 None.
    axis는 a를 b에서 밀어내는 단위 방향."""
    worst = None
    for ra in rects_a:
        for rb in rects_b:
            depth, axis = obb_penetration(ra, rb)
            if depth > slack and (worst is None or depth > worst[0]):
                worst = (depth, axis)
    return worst


def _bounds_hint(st, room_w, room_d):
    """방 밖으로 나간 방향·거리를 문자열과 이동 벡터로."""
    x0, y0, x1, y1 = footprint_bbox(st)
    dx = -x0 if x0 < 0 else (room_w - x1 if x1 > room_w else 0.0)
    dy = -y0 if y0 < 0 else (room_d - y1 if y1 > room_d else 0.0)
    return {"dx": round(dx, 1), "dy": round(dy, 1),
            "note": "footprint를 (%+.0f, %+.0f) 옮기면 방 안으로 들어온다" % (dx, dy)}


def validate_layout(robots, scene, slack=DEFAULT_SLACK):
    """전체 배치 검증. 반환: 문제 목록(비면 통과).
    robots: state 목록, scene: {"width","depth","pre_existing_furniture":[...]}
    각 issue에 수정 방향 힌트(수치)를 실어 LLM이 재시도 시 빠르게 수렴하게 한다."""
    issues = []
    w, d = scene["width"], scene["depth"]
    furn = scene.get("pre_existing_furniture", [])
    for st in robots:
        if out_of_bounds(st, w, d):
            issues.append({"type": "out_of_bounds", "robot": st.get("robot"),
                           "fix": _bounds_hint(st, w, d)})
        for f in furn:
            pen = _worst_overlap(footprint_rects(st), [furniture_rect(f)], slack)
            if pen:
                depth, (ax, ay) = pen
                issues.append({"type": "furniture_overlap",
                               "robot": st.get("robot"), "furniture": f.get("id"),
                               "penetration": round(depth, 1),
                               "fix": {"robot": st.get("robot"),
                                       "dx": round(ax * depth, 1), "dy": round(ay * depth, 1),
                                       "note": "%s를 (%+.0f, %+.0f) 옮기면 겹침이 풀린다"
                                               % (st.get("robot"), ax * depth, ay * depth)}})
    for i in range(len(robots)):
        for j in range(i + 1, len(robots)):
            pen = _worst_overlap(footprint_rects(robots[i]), footprint_rects(robots[j]), slack)
            if pen:
                depth, (ax, ay) = pen
                mover = robots[i].get("robot")
                issues.append({"type": "robot_overlap",
                               "robots": [robots[i].get("robot"), robots[j].get("robot")],
                               "penetration": round(depth, 1),
                               "fix": {"robot": mover,
                                       "dx": round(ax * depth, 1), "dy": round(ay * depth, 1),
                                       "note": "%s를 (%+.0f, %+.0f) 옮기면 겹침이 풀린다"
                                               % (mover, ax * depth, ay * depth)}})
    return issues
