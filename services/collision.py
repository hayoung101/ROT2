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


def _labeled_rects(state):
    """(label, rect) 목록 — label: 'body'/'left'/'right'. 연결 자동 감지·제외 판정용."""
    out = [("body", (state["x"], state["y"], BODY, BODY, state.get("rot", 0)))]
    for side in ("left", "right"):
        r = panel_rect(state, side)
        if r is not None:
            out.append((side, r))
    return out


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


def _touching(ra, rb, tol=3.0, min_align=30.0):
    """두 접촉면 OBB가 맞닿아 있는가: 거리(±tol 이내) + 정렬(폭 방향 겹침 ≥ min_align).
    정렬 조건이 있어야 모서리만 스치는 배치를 연결로 세지 않는다."""
    depth, _ = obb_penetration(ra, rb)
    if not (-tol <= depth <= tol):   # 간격도 침투도 tol 이내
        return False
    axw = _axes(ra[4])[1]            # 접촉면 폭 방향 (로컬 y)
    lo1, hi1 = _project(rect_corners(*ra), axw)
    lo2, hi2 = _project(rect_corners(*rb), axw)
    return min(hi1, hi2) - max(lo1, lo2) >= min_align


def _panel_pair_touching(a, side_a, b, side_b, tol=3.0, min_align=30.0):
    """지정한 두 '패널'이 맞닿아 있는가 — 본체 폴백 없음. validate_layout의 연결 자동 감지 전용.

    여기서는 일반화하면 안 된다: 본체가 닿았다는 이유로 (side_a, side_b) 쌍을 '연결됨'으로
    표시하면 그 패널 쌍이 충돌 검사에서 통째로 제외되어, 실제로 깊이 겹친 패널을 놓친다.
    자동 감지가 답해야 할 질문은 '이 패널 쌍이 맞닿았나'이고, 선언된 연결의 검증
    (connection_touching)이 답할 질문은 '연결 지점에서 두 로봇이 인접한가'다."""
    ra, rb = panel_rect(a, side_a), panel_rect(b, side_b)
    if ra is None or rb is None:
        return False
    return _touching(ra, rb, tol, min_align)


def _connect_rect(state, side):
    """연결 지점의 접촉면 rect — 패널이 돌출하면 그 패널, 없으면(0°·180°) 본체."""
    r = panel_rect(state, side)
    if r is not None:
        return r
    return (state["x"], state["y"], BODY, BODY, state.get("rot", 0))


def connection_touching(a, side_a, b, side_b, tol=3.0, min_align=30.0):
    """선언된 연결 지점에서 두 로봇 footprint가 실제로 인접한가 (연결 기하 검증용).

    패널 끝 맞닿음이든 본체 맞닿음이든 성립한다 — 맞댈 쪽이 0°(접힘)·180°(수직)라 바닥
    돌출이 없으면 두 '본체'가 맞닿아 이어지며, 그것도 물리적으로 정당한 연결이다(상자 둘을
    이어 긴 상판을 만드는 실제 가구 구성 — large_worktable의 140 = 30+40+40+30).
    구 panels_touching은 panel_rect가 None이면 무조건 False라 그 구성을 '연결 불가'로
    보고했는데, 그건 물리적 사실이 아니라 검사의 한계였다(find_connect는 dist =
    BODY + pa + pm = 40으로 본체를 gap 0에 붙여 놓고 있었다).
    정렬 조건(min_align)은 유지한다 — 모서리만 스치는 것은 연결이 아니다."""
    return _touching(_connect_rect(a, side_a), _connect_rect(b, side_b), tol, min_align)


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
            a, b = robots[i], robots[j]
            # 맞닿음 조건(±tol·정렬)을 만족하는 패널 쌍은 '연결'로 보고 충돌에서 제외 (자동 감지).
            # slack(2cm) < tol(3cm) 구간에서 연결이 robot_overlap으로 오판되는 것을 막는다.
            connected = {(sa, sb) for sa in ("left", "right") for sb in ("left", "right")
                         if _panel_pair_touching(a, sa, b, sb)}
            pen = None
            for na, ra in _labeled_rects(a):
                for nb, rb in _labeled_rects(b):
                    if (na, nb) in connected:
                        continue
                    depth, axis = obb_penetration(ra, rb)
                    if depth > slack and (pen is None or depth > pen[0]):
                        pen = (depth, axis)
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
