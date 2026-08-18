# -*- coding: utf-8 -*-
"""형태층 후보 생성 — 격자 스캔 + 앵커 밴드 필터 (순수 계산, 상태 없음).

형태층 자리 후보의 유일한 생성기 (구 placement.find_placement 앵커 링 탐색을 대체·폐기, §6.4).
services/ 밖은 import하지 않는다 — LLM·프롬프트·tool 없이 pytest 단독 검증 가능.

핵심 원칙 — 하나의 충돌 경로:
  후보의 충돌·경계 검사는 오직 collision.footprint_rects(proxy state)로만 한다.
  검사한 도형 = 실제로 move_robot이 놓을 도형이므로, scan이 통과시킨 (x,y,rot)에
  대해 collision.validate_layout 위반 0이 '희망'이 아니라 '구성상' 보장된다
  (v4.5 §6.3, §1-4 수용 기준). 반환 (x,y)는 곧 본체 중심이다.

  예외는 _fits_rect(2대 조합 용량 프로브)뿐이다 — 2대 강체는 단일 로봇 footprint로
  표현되지 않아 병합 사각형을 쓴다. 그 결과는 '가능/불가' 판정에만 쓰고 후보 좌표로는
  절대 쓰지 마라: 비대칭 패널은 무게중심이 본체 중심에서 어긋나므로 병합 치수로 검사하면
  검증한 배치와 실행된 배치가 갈라진다.

placement의 public 헬퍼(panel_relation/nearby_items/dir8/anchor_geometry/front_vec/
  find_connect/feasibility)를 재사용한다. layout→placement 단방향이라 순환은 없다
  (이관 대신 public 승격만 — placement.panel_orientation이 panel_relation을 필요로 해서
  이관하면 placement→layout 역방향이 생긴다).

다양성 원칙 (Phase B 후보를 다루는 모든 곳에 적용 — Stage 2 포함):
  "정렬된 목록을 앞에서 k개 자른다"가 네 번 연속 다양성을 무너뜨렸다 —
  ① raster 순서 → 벽면 6칸 뭉침, ② (x,y) 거리 이격 → 같은 자리 rot 전멸,
  ③ 중첩 for → 첫 로봇 자리 고정, ④ 자리당 rot 상위 2개 → 뒤집힌 배향 전멸.
  그때마다 원칙 4(코드는 후보만, 선택은 LLM)가 새어 기하 지표(clearance 1cm 차)가
  활동 판단을 대신 내렸다. 그러므로:
  **먼저 다양성 축(자리 / rot / 로봇별 자리 / 배향)을 정하고, 그 축을 라운드로빈한 뒤에 자른다.**
  단순 정렬-슬라이스로 후보를 줄이지 마라.
"""
import math

from services import collision, placement

GRID_CM = 20                 # 격자 간격 (placement.GRID_CM과 동일값 유지)
CLEARANCE_CM = 5.0           # 앵커 밴드 하한 = 후보와 앵커 사이 최소 여유
BAND_MAX = 70.0              # 앵커 밴드 상한. [미정] 파일럿에서 보정 — v4.5 §18-16
AVOID_MARGIN_CM = 40.0       # avoid 대상과의 최소 이격 (placement와 동일)
SURVEY_MIN_APART = 80        # '자리'(x,y)끼리 최소 이격 — 벽면 한 줄로 뭉치지 않게 분산
K_POSITIONS = 6             # 이격 후 남길 자리 개수. [미정] — v4.5 §18-17
MAX_ROTS_PER_POS = 2        # 자리당 남길 유효 rot 상한 — 방향(rot) 선택은 Phase B로 (원칙 4)
PRESENT_CAP = K_POSITIONS * MAX_ROTS_PER_POS   # 최종 제시 상한 (총 6~12개, §6.4)
ROT_STEPS = (0, 45, 90, 135, 180, 225, 270, 315)
BODY = collision.BODY


# ---------- footprint (용량 열거 전용 — 충돌 경로 아님) ----------


def _proxy(x, y, rot, panels):
    """격자 후보 1개의 로봇 상태 proxy — 실행 규약과 동일한 좌우 배정 (v4.5 §6.5).

    panel_right = panels[0] (+u쪽), panel_left = panels[1] (-u쪽). u = rot 방향.
    스왑 로직 없음: (left=a,right=b,rot=θ)와 (left=b,right=a,rot=θ+180)은 같은 물체이고
    그 배향 차이는 rot 8종이 이미 덮는다."""
    return {"robot": None, "active": "active", "x": x, "y": y, "rot": rot,
            "panel_right": panels[0], "panel_left": panels[1]}


def _panel_for_side(panels, side):
    """side('right'/'left')에 달린 패널 각도. _proxy의 좌우 배정과 동일 규약 (§6.5)."""
    return panels[0] if side == "right" else panels[1]


def _symmetric(panels):
    """좌우 대칭(두 패널 각도 동일) 여부. 180° 회전이 footprint 항등이 된다 (§6.5)."""
    return collision.snap_panel(panels[0]) == collision.snap_panel(panels[1])


def _rot_steps(panels):
    """대칭이면 rot 4종(0/45/90/135)으로 축소 — 180° 항등이라 중복 후보 방지 (§6.5).

    주의: 축소 집합에 없는 rot(예: 앵커 rot=270)과의 '일치' 판정은 필터 쪽에서
    반드시 mod 180으로 정규화해야 한다 (_rot_matches/_faces_anchor). 안 그러면
    alongside/facing이 조용히 빈 배열을 낸다."""
    return (0, 45, 90, 135) if _symmetric(panels) else ROT_STEPS


# ---------- 장애물 조립 ----------

def _obstacles(scene, states, exclude=None):
    """방의 충돌 장애물 rect 목록. exclude(가구 id/label 또는 로봇 이름)는 통째 제외한다.

    앵커를 exclude로 넘기면 그 로봇의 '본체+패널' footprint 전체가 빠진다 —
    이름으로 빼므로 튜플 동일성 비교의 사각지대(패널 사각형 잔존)가 없다 (결함 6 해소)."""
    rects = []
    for f in (scene or {}).get("pre_existing_furniture", []):
        if exclude is not None and (f.get("id") == exclude or f.get("label") == exclude):
            continue
        rects.append(collision.furniture_rect(f))
    for st in states or []:
        if exclude is not None and st.get("robot") == exclude:
            continue
        rects += collision.footprint_rects(st)
    return rects


def _avoid_rects(scene, avoid, states):
    out = []
    for item in avoid or ():
        _, arect, _ = placement.anchor_geometry(scene, item, states)
        if arect is not None:
            out.append(arect)
    return out


# ---------- 격자 스캔 ----------

def scan(scene, fixed_states, panels, avoid=()):
    """방 전체 격자를 rot 8종(대칭이면 4종) OBB로 훑어 놓일 수 있는 셀을 모은다.

    충돌·경계는 collision.footprint_rects(proxy)로만 검사한다 — rot을 반영하므로
    결함 4(축정렬 40×40으로 100×40을 검사)가 해소된다. 반환은 **주석 없는 bare 셀**
    [{"x","y","rot"}]이다 — 비싼 annotate(free_extents/nearby/panel_faces)는 필터·랭킹·k
    선정이 끝난 최종 후보에만 붙인다 (버린 셀에 비용 들이지 않게)."""
    w, d = scene["width"], scene["depth"]
    obstacles = _obstacles(scene, fixed_states)
    avoid_rects = _avoid_rects(scene, avoid, fixed_states)
    rots = _rot_steps(panels)
    cells = []
    for i in range(1, int(w // GRID_CM)):
        for j in range(1, int(d // GRID_CM)):
            x, y = i * GRID_CM, j * GRID_CM
            for rot in rots:
                proxy = _proxy(x, y, rot, panels)
                if collision.out_of_bounds(proxy, w, d):
                    continue
                rects = collision.footprint_rects(proxy)
                if any(collision.rects_collide(r, ob) for r in rects for ob in obstacles):
                    continue
                if any(collision.rect_gap(r, ar) < AVOID_MARGIN_CM
                       for r in rects for ar in avoid_rects):
                    continue
                cells.append({"x": x, "y": y, "rot": rot})
    return cells


# ---------- 후보 주석 ----------

def _bbox_half(rects, x, y):
    """footprint 전체를 (x,y) 중심 축정렬 사각형으로 감쌀 반폭·반깊이 (nearby proxy용)."""
    corners = [c for r in rects for c in collision.rect_corners(*r)]
    hw = max(abs(px - x) for px, _ in corners)
    hd = max(abs(py - y) for _, py in corners)
    return hw, hd


def _nearest_wall(rects, w, d):
    """footprint에서 가장 가까운 벽과 그 거리·최근접점. 좌표 규약: east=+x, north=+y.

    벽은 free 모드의 안정적 기준이다 — nearby[0]는 후보마다 바뀌고(실측: 거실 free 12조합의
    ref가 sofa_1↔table_1로 흔들렸다) 가구 없는 방에서는 아예 없어 toward가 통째로 사라졌다.
    그 로봇은 무엇과도 관계를 맺으라는 지시를 받은 적이 없다 — nearby[0]는 '고른 대상'이
    아니라 우연히 옆에 있는 것이라, 그 위에서 잰 panel_on_ref_side·front_faces_ref는
    불안정한 기준 위의 값이 된다. 벽은 움직이지 않고 항상 존재하며 scene의 width·depth에서
    바로 나오므로 방별 가정(§10 '고정 실험 제약 없음')을 코드에 넣지 않아도 된다.
    동거리(모서리)면 아래 나열 순서로 결정론적으로 고른다."""
    corners = [c for r in rects for c in collision.rect_corners(*r)]
    x0 = min(px for px, _ in corners); x1 = max(px for px, _ in corners)
    y0 = min(py for _, py in corners); y1 = max(py for _, py in corners)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    cands = [("wall_south", y0,      (cx, 0.0)),
             ("wall_north", d - y1,  (cx, float(d))),
             ("wall_west",  x0,      (0.0, cy)),
             ("wall_east",  w - x1,  (float(w), cy))]
    name, dist, pt = min(cands, key=lambda t: t[1])   # 동거리면 위 순서가 tie-break
    return name, int(round(max(0.0, dist))), pt


def _clearance(rects, w, d, obstacles):
    """footprint 전체의 최소 여유(cm): 벽·장애물까지의 최소 간격. 앵커 제외는 obstacles에서."""
    corners = [c for r in rects for c in collision.rect_corners(*r)]
    x0 = min(px for px, _ in corners); x1 = max(px for px, _ in corners)
    y0 = min(py for _, py in corners); y1 = max(py for _, py in corners)
    wall = min(x0, y0, w - x1, d - y1)
    gaps = [collision.rect_gap(r, ob) for r in rects for ob in obstacles]
    g = min(gaps) if gaps else wall
    return int(round(max(0.0, min(wall, g))))


def _free_extents(x, y, rot, panels, scene, obstacles, step=5.0):
    """이 지점의 footprint가 동서남북으로 밀려갈 수 있는 빈 거리(cm) — rot 반영 OBB로 밀어 본다.

    벽·장애물(앵커 포함)에 막히면 멈춘다. 후보가 '어느 쪽이 트여 있는지'를 사실로
    서술할 뿐, 방향(rot) 판단은 상위(활동)의 몫이다."""
    w, d = scene["width"], scene["depth"]
    limit = max(w, d)
    out = {}
    for name, ux, uy in (("east", 1, 0), ("west", -1, 0),
                         ("north", 0, 1), ("south", 0, -1)):
        dist = 0.0
        while dist < limit:
            nx, ny = x + ux * (dist + step), y + uy * (dist + step)
            proxy = _proxy(nx, ny, rot, panels)
            if collision.out_of_bounds(proxy, w, d):
                break
            rects = collision.footprint_rects(proxy)
            if any(collision.rects_collide(r, ob) for r in rects for ob in obstacles):
                break
            dist += step
        out[name] = int(round(dist))
    return out


def front_faces_ref(angle, on_ref_side):
    """이 패널의 앞면이 기준(ref)을 향하는가 — 실제 각도로 확정한 기하 사실.

    패널 앞면 방향은 각도가 정한다: 0°=접혀 기능면 없음, 45°=앞면이 패널 바깥쪽,
    90°=앞면이 위, 135°·180°=앞면이 본체 쪽. 따라서 45°는 ref 쪽에 달렸을 때,
    135°·180°는 ref '반대'쪽에 달렸을 때 앞면이 ref를 향한다.

    90°가 false인 것은 정상이다 — 상판은 어느 쪽도 향하지 않는다. 이 값을 '기능면이
    ref를 향해야 좋다'는 판단으로 읽으면 안 된다(그건 Phase B 몫). 코드는 사실만 낸다."""
    a = collision.snap_panel(angle)
    if a == 45:
        return bool(on_ref_side)
    if a in (135, 180):
        return not on_ref_side
    return False


def _panel_faces(x, y, rot, panels, ref_id=None, ref_center=None, ref_dist=None):
    """이 rot에서 각 패널이 어디로 뻗는지(절대 8방위) + 기준 대비 상대 방향.

    toward는 모드와 무관하게 항상 채운다 — 기준(ref)은 관계를 맺기로 '고른' 앵커가 있으면
    앵커, 없으면 가장 가까운 벽이다(_nearest_wall). 벽이 항상 정의되므로 toward는 어떤
    후보에서도 non-null이다. free에서 toward를 None으로 두면 절대 8방위 이름만
    남아 Phase B가 nearby 목록과 머릿속으로 대조해야 했다. 이름을 toward_anchor가 아닌
    toward/ref로 둔 것은 기준이 앵커에 한정되지 않기 때문이다.
    ref_dist(기준까지 cm)를 함께 준다 — 방 한가운데면 벽이 멀어 기준으로서의 의미가 약하고,
    그때는 Phase B가 nearby를 보면 된다. 그 판단에 쓸 숫자가 없으면 먼 기준도 가까운 기준과
    똑같이 다뤄진다. '멀면 무시하라'는 판단은 코드가 하지 않는다 (원칙 4).
    free와 facing이 '동일한 주석 집합'을 갖게 하는 근거다 (§6.4, §1-4 수용 기준).
    v4.4의 사후 panel_orientation을 대체: move 후가 아니라 후보 생성 중에 계산한다.

    front_faces_ref는 각도별 가정표(구 front_face_toward_ref, "만약 180°라면 left")가
    아니라 '이 후보의 확정 각도에서 어느 쪽 패널 앞면이 ref를 보는가'라는 사실이다.
    가정표는 Phase B에 3단계 교차 참조(표 조회 → 내 180°가 panels[0]=right임을 상기 →
    panel_on_ref_side와 대조)를 시켰고, 실키에서 로봇 2대 중 하나만 맞히는 오독이
    2회 재현됐다. 코드가 계산할 수 있는 것을 LLM에게 시키지 않는다 — panel_faces가
    사후 panel_orientation을 대체한 것과 같은 종류의 이동이다. 판단이 아니라 기하
    사실이므로 원칙 4는 그대로다. 부수적으로 후보당 필드도 순감한다(4항목 dict +
    panel_on_opposite_side 삭제 → 2항목 dict, §B-2 payload)."""
    th = math.radians(rot)
    u = (math.cos(th), math.sin(th))
    toward = None
    if ref_center is not None:
        near = placement.panel_relation(x, y, rot, ref_center)["panel_on_anchor_side"]
        angle = {"right": panels[0], "left": panels[1]}   # _proxy의 좌우 배정과 동일
        toward = {"ref": ref_id, "ref_dist": ref_dist, "panel_on_ref_side": near,
                  "front_faces_ref": {s: front_faces_ref(angle[s], s == near)
                                      for s in ("right", "left")}}
    return {"panel_right_dir": placement.dir8(u[0], u[1]),
            "panel_left_dir": placement.dir8(-u[0], -u[1]),
            "toward": toward}


def annotate(cell, scene, states, panels, anchor_id=None):
    """후보 1개에 clearance / free / nearby / panel_faces를 붙여 새 dict로 반환한다.

    clearance는 앵커(가구·로봇)를 obstacles에서 제외해 잰다 — 밴드 하한(5cm)에 눌려
    0으로 깔리지 않게 (결함 6). free는 앵커도 물리 장애물이므로 전체 obstacles로 민다.
    panel_faces.toward의 기준(ref)은 (1) 관계를 맺기로 '고른' 앵커, (2) 없거나 조회에
    실패하면 가장 가까운 벽이다. nearby[0]는 쓰지 않는다 — 우연히 가까운 것이지 기준이
    아니다(_nearest_wall). 벽은 어떤 자리에서도 정의되므로 toward는 항상 non-null이고,
    Phase B는 모드·방과 무관하게 같은 필드 집합을 읽는다 (§6.4의 같은 근거)."""
    x, y, rot = cell["x"], cell["y"], cell["rot"]
    w, d = scene["width"], scene["depth"]
    rects = collision.footprint_rects(_proxy(x, y, rot, panels))
    obstacles_full = _obstacles(scene, states)
    obstacles_clear = _obstacles(scene, states, exclude=anchor_id)
    hw, hd = _bbox_half(rects, x, y)
    nearby = placement.nearby_items(x, y, hw, hd, scene, states)
    ref_id, ref_center, ref_dist = anchor_id, None, None
    if anchor_id is not None:
        ref_center, arect, _ = placement.anchor_geometry(scene, anchor_id, states)
        if arect is not None:
            ref_dist = int(round(min(collision.rect_gap(r, arect) for r in rects)))
    if ref_center is None:                 # 앵커 없음(free) 또는 앵커 조회 실패 → 벽
        ref_id, ref_dist, ref_center = _nearest_wall(rects, w, d)
    return {"x": x, "y": y, "rot": rot,
            "clearance": _clearance(rects, w, d, obstacles_clear),
            "free": _free_extents(x, y, rot, panels, scene, obstacles_full),
            "nearby": nearby,
            "panel_faces": _panel_faces(x, y, rot, panels, ref_id, ref_center, ref_dist)}


# ---------- 앵커 밴드 필터 (링 탐색 대체) ----------

def _anchor_rects(scene, anchor_id, states):
    """앵커의 충돌 OBB 목록. 로봇이면 footprint 전체(본체+패널), 가구면 본체 rect."""
    for f in (scene or {}).get("pre_existing_furniture", []):
        if f.get("id") == anchor_id or f.get("label") == anchor_id:
            return [collision.furniture_rect(f)]
    for s in states or []:
        if s.get("robot") == anchor_id:
            return collision.footprint_rects(s)
    return []


def _half_extent(rect, direction):
    """rect 중심에서 direction 방향으로의 support 거리 (그 방향 반경)."""
    cx, cy = rect[0], rect[1]
    return max((px - cx) * direction[0] + (py - cy) * direction[1]
               for px, py in collision.rect_corners(*rect))


def _anchor_face(arects, ax, ay, fv, x, y):
    """앵커 로컬축 투영으로 후보가 앵커의 어느 면 구획에 있는지: front/back/side.

    앞/뒤는 **앵커 폭만큼의 띠** 안일 때만이다. 구 판정 abs(pf) >= abs(ps)는 중심에서
    45° 부채꼴이라 pf가 크면 ps도 그만큼 허용돼 앵커 폭 밖도 '앞'이 됐다 — 실측(파일럿
    1턴): 소파(120×80, 반폭 60) 앞이라며 pf=110·ps=100인 자리가 통과했고, 소파에 앉은
    사람(x 140~260)과 작업대(x 280~320)가 어긋나 대각선으로 손을 뻗어야 했다.
    (중심 간 각도 dot>0.5로 판정하던 구-구 결함 3은 그대로 해소 상태를 유지한다.)

    arects를 받는 이유: 앵커가 로봇이면 본체+패널 여러 rect라 폭이 하나로 안 나온다.
    + BODY/2는 후보 본체가 띠에 걸치기만 해도 '앞'으로 보기 위한 여유다 — 없으면 좁은
    앵커(폭 40)에서 후보가 굶는다. [미정] 이 여유값 — 파일럿에서 facing 후보 수로 보정."""
    sv = (-fv[1], fv[0])                  # 측면 축 (앞면을 90° 회전)
    dx, dy = x - ax, y - ay
    pf = dx * fv[0] + dy * fv[1]          # 앞면 축 투영
    ps = dx * sv[0] + dy * sv[1]          # 측면 축 투영
    side_half = max(_half_extent(r, sv) for r in arects) + BODY / 2.0
    if abs(ps) <= side_half:
        return "front" if pf >= 0 else "back"
    return "side"


def _faces_anchor(x, y, rot, ax, ay, tol_deg=45.0):
    """후보의 '패널축' u=(cos rot, sin rot)이 앵커와 나란한가 — 축 정렬 |cos| (±45°).

    로봇 규약(collision.py)상 패널은 ±u에 달려 있다 — 가구 앞 규약 _front_vec(=(-sin,cos))가
    아니다. 90° 어긋난 축을 쓰면 패널이 앵커 수직을 향하는 후보가 facing을 통과한다.

    부호는 보지 않는다(대칭·비대칭 모두 |cos|). rot과 rot+180은 같은 축이고 차이는
    '어느 패널이 앵커 쪽에 달리느냐'뿐인데, cos 부호를 걸면 그중 한쪽만 통과해
    panels[0]이 항상 앵커 쪽으로 고정된다 — §6.5(배향은 rot이 흡수한다)를 필터가
    무효화하는 것이다. 그러면 앞면이 본체 쪽을 향하는 패널(135° 독서대, 180° 등받이)을
    앵커 반대쪽에 두는 구성이 후보 집합에서 원천적으로 사라진다(실키 S3: 등받이가
    테이블 쪽을 향해 벽처럼 섰다). 어느 쪽이 앵커를 향할지는 panel_faces가 사실로
    서술하고 Phase B가 고른다 — 원칙 4. mod 180 정규화 효과도 함께 얻는다(향하는 rot이
    [180,315]인데 대칭 축소 집합이 0~135라 빈 배열이 나던 결함)."""
    th = math.radians(rot)
    u = (math.cos(th), math.sin(th))     # 패널축 (앵커 가구 판정용 _front_vec와 구분)
    dx, dy = ax - x, ay - y
    n = math.hypot(dx, dy)
    if n < 1e-6:
        return True
    cosang = (u[0] * dx + u[1] * dy) / n
    return abs(cosang) >= math.cos(math.radians(tol_deg))


def _rot_matches(rot, anchor_rot):
    """alongside: 후보 패널축이 앵커 축과 나란한가 — 대칭 여부와 무관하게 mod 180.

    _faces_anchor와 같은 이유로 부호를 보지 않는다. rot=arot과 rot=arot+180은 둘 다
    '나란히'이고 차이는 어느 패널이 앵커 쪽이냐뿐인데, 비대칭에서 mod 360을 쓰면
    배향 하나만 남아 소파 옆 [180,0] 협탁의 등받이 방향을 코드가 정해 버린다."""
    return (rot - anchor_rot) % 180 == 0


def band_filter(cells, scene, anchor_id, states, mode, panels):
    """scan 셀 중 앵커 밴드(rect_gap ∈ [5,70]) 안이며 모드 구획에 맞는 것만 남긴다.

    앵커를 후보 '생성' 주체에서 '거르는' 필터로 강등한 부분 — 밴드가 두께를 가지므로
    다중 링이 자동으로 생긴다 (결함 1 해소). 반환은 **bare 셀**이다 — 랭킹·k 선정 뒤에
    annotate한다 (band_filter가 살아남은 셀을 다시 annotate하던 중복 비용 제거)."""
    center, _, arot = placement.anchor_geometry(scene, anchor_id, states)
    if center is None:
        return []
    ax, ay = center
    fv = placement.front_vec(arot)      # 앵커가 가구일 때의 앞면 규약 (그대로)
    arects = _anchor_rects(scene, anchor_id, states)
    out = []
    for cell in cells:
        x, y, rot = cell["x"], cell["y"], cell["rot"]
        rects = collision.footprint_rects(_proxy(x, y, rot, panels))
        gap = min(collision.rect_gap(r, ar) for r in rects for ar in arects)
        if not (CLEARANCE_CM <= gap <= BAND_MAX):
            continue
        face = _anchor_face(arects, ax, ay, fv, x, y)
        if mode == "facing":
            if face != "front" or not _faces_anchor(x, y, rot, ax, ay):
                continue
        elif mode == "alongside":
            if face != "side" or not _rot_matches(rot, arot):
                continue
        out.append({"x": x, "y": y, "rot": rot})
    return out


# ---------- 랭킹 · 이격 (bare 셀 → 최종 후보 풀) ----------

def _clearance_of(x, y, rot, panels, scene, states, exclude=None):
    """랭킹용 clearance (annotate와 같은 정의 — 앵커 제외분과 일치)."""
    rects = collision.footprint_rects(_proxy(x, y, rot, panels))
    return _clearance(rects, scene["width"], scene["depth"],
                      _obstacles(scene, states, exclude=exclude))


def _cell_key(c, panels, scene, states, anchor_id, mode, arects):
    """랭킹 키. facing/alongside면 (앵커 gap↑, clearance↓), free면 clearance↓.

    주의: 이 키는 '자리'를 고르는 데만 쓴다 — rot 사이 우열을 매기는 데 쓰면 방향 선택이
    clearance 1cm 차이로 결정되어 원칙 4(방향은 Phase B 몫)를 어긴다."""
    x, y, rot = c["x"], c["y"], c["rot"]
    clr = _clearance_of(x, y, rot, panels, scene, states, exclude=anchor_id)
    if anchor_id and mode in ("facing", "alongside") and arects:
        gap = min(collision.rect_gap(r, ar)
                  for r in collision.footprint_rects(_proxy(x, y, rot, panels))
                  for ar in arects)
        return (gap, -clr)
    return (-clr,)


def _toward_dot(cell, ax, ay):
    """패널축 u와 (앵커 − 후보) 방향의 내적. 양수면 panels[0](=+u쪽)이 앵커 쪽에 있다."""
    th = math.radians(cell["rot"])
    return math.cos(th) * (ax - cell["x"]) + math.sin(th) * (ay - cell["y"])


def _orient_pair(cells, ax, ay, per_pos):
    """한 자리의 rot 우선순 목록을 '앵커 쪽 하나 + 반대쪽 하나'의 배향 짝으로 남긴다.

    다양성 원칙(모듈 상단)의 네 번째 적용. _faces_anchor를 축 정렬로 바꿔 양쪽 배향을
    살려 놓아도, 앞에서 per_pos개를 그냥 자르면 뒤집힌 짝이 함께 남는다는 보장이 없다 —
    [180,0]·[0,0]·[180,180]은 footprint가 40×40 정사각(180°·0° 모두 돌출 0)이라 모든
    rot의 clearance·gap이 동일하고, 랭킹 키가 순서를 못 정해 같은 배향 둘만 남을 수 있다.
    그러면 (A)로 되살린 반대쪽 배향이 후보 목록에서 다시 증발한다.
    그래서 배향(내적 부호)을 축으로 삼아 라운드로빈한 뒤 자른다."""
    toward = [c for c in cells if _toward_dot(c, ax, ay) >= 0]
    away = [c for c in cells if _toward_dot(c, ax, ay) < 0]
    out = []
    while len(out) < per_pos and (toward or away):
        for bucket in (toward, away):
            if bucket and len(out) < per_pos:
                out.append(bucket.pop(0))
    return out


def _rank_thin(cells, scene, states, panels, anchor_id, mode,
               k=K_POSITIONS, per_pos=MAX_ROTS_PER_POS, min_apart=SURVEY_MIN_APART):
    """bare 셀을 '자리(x,y)' 단위로 랭킹·이격해 k개 자리를 뽑고, 자리마다 유효 rot을 남긴다.

    이격을 (x,y) 거리로만 걸면 같은 자리의 다른 rot이 전부 탈락해 방향 선택이 코드로
    새어 들어간다 (원칙 4 위반). 그래서 이격은 자리 단위로 먼저 적용하고, 각 자리의
    유효 rot 상위 per_pos개를 남긴다 → 총 6~12개, rot 선택은 Phase B로 (§6.4).
    좁아서 자리가 3개 미만이면 이격을 절반으로 완화한다 (구 _pick 폴백).
    facing/alongside는 그 per_pos개를 배향 짝으로 고른다 (_orient_pair)."""
    arects = _anchor_rects(scene, anchor_id, states) if anchor_id else []
    acenter = None
    if anchor_id and mode in ("facing", "alongside"):
        acenter, _, _ = placement.anchor_geometry(scene, anchor_id, states)
    groups = {}                        # (x,y) → [(key, cell), ...]
    for c in cells:
        key = _cell_key(c, panels, scene, states, anchor_id, mode, arects)
        groups.setdefault((c["x"], c["y"]), []).append((key, c))
    positions = []                     # (대표키, (x,y), [rot 우선순 cell])
    for xy, items in groups.items():
        items.sort(key=lambda t: t[0])
        positions.append((items[0][0], xy, [c for _, c in items]))
    positions.sort(key=lambda t: t[0])

    def _pick(apart):
        chosen_xy, out = [], []
        for _, (x, y), rots in positions:
            if all(math.hypot(x - cx, y - cy) >= apart for cx, cy in chosen_xy):
                chosen_xy.append((x, y))
                out.append(rots)
            if len(chosen_xy) >= k:
                break
        return out

    picked = _pick(min_apart)
    if len(picked) < min(3, k):        # 좁은 밴드·방 → 이격 완화 재선정
        picked = _pick(min_apart / 2)
    if acenter is not None:            # 자리당 rot을 배향 짝(앵커 쪽/반대쪽)으로
        return [c for rots in picked
                for c in _orient_pair(rots, acenter[0], acenter[1], per_pos)]
    return [c for rots in picked for c in rots[:per_pos]]   # 자리당 유효 rot per_pos개


# ---------- 공간 요약 (Phase A 입력, 용량 정보만) ----------

_SPAN_PANELS = {                       # 6개 폭 → 그 폭을 내는 순서쌍 하나
    40: (0, 0), 61.2: (0, 45), 70: (0, 90),
    82.4: (45, 45), 91.2: (45, 90), 100: (90, 90),
}
_SPANS = (100, 91.2, 82.4, 70, 61.2, 40)   # 큰 것부터


def _rect_out_of_bounds(r, w, d, tol=0.5):
    for px, py in collision.rect_corners(*r):
        if px < -tol or py < -tol or px > w + tol or py > d + tol:
            return True
    return False


def _fits_panels(scene, obstacles, panels):
    """panels 구성이 obstacles를 피해 방 어딘가에 놓이는가 (boolean, 첫 성립에서 즉시 종료).

    span 판정용 — 후보 충돌 경로와 같은 footprint_rects를 쓰되 주석 없이 early-exit이라
    scan 전체를 도는 비용이 없다."""
    w, d = scene["width"], scene["depth"]
    for i in range(1, int(w // GRID_CM)):
        for j in range(1, int(d // GRID_CM)):
            x, y = i * GRID_CM, j * GRID_CM
            for rot in _rot_steps(panels):
                proxy = _proxy(x, y, rot, panels)
                if collision.out_of_bounds(proxy, w, d):
                    continue
                if not any(collision.rects_collide(r, ob)
                           for r in collision.footprint_rects(proxy) for ob in obstacles):
                    return True
    return False


def _fits_rect(scene, rw, rd):
    """폭 rw × 깊이 rd 강체 사각형이 가구를 피해 방 어딘가에 들어가는가 (pair probe 전용).

    2대 조합은 단일 로봇 footprint로 표현되지 않으므로 병합 사각형을 쓴다 (§6.1).
    첫 성립에서 즉시 종료. 가구만 장애물 — 조합될 두 로봇은 놓을 대상이다."""
    w, d = scene["width"], scene["depth"]
    obstacles = [collision.furniture_rect(f)
                 for f in (scene or {}).get("pre_existing_furniture", [])]
    for i in range(1, int(w // GRID_CM)):
        for j in range(1, int(d // GRID_CM)):
            x, y = i * GRID_CM, j * GRID_CM
            for rot in (0, 45, 90, 135):     # rw×rd 사각형은 180° 대칭 → 4종
                r = (x, y, rw, rd, rot)
                if _rect_out_of_bounds(r, w, d):
                    continue
                if any(collision.rects_collide(r, ob) for ob in obstacles):
                    continue
                return True
    return False


# 2대 조합의 '최소' 점유 (병합 사각형). 이 프로브는 "그 연결이 아예 불가능한가"만 답한다 —
# 최소보다 큰 구성을 기준으로 재면 들어갈 수 있는 방을 false로 보고해 Phase A가 그 구성
# 자체를 포기한다. face의 중심 거리는 40 + 30·sinθa + 30·sinθb이고 안쪽을 접으면(0°·180°)
# 돌출이 0이라 본체끼리 맞닿는다 — 즉 최소는 45°가 아니라 0°이고 side와 같은 80×40이다.
# (예전 값 122.4는 45°를 최소로 가정해, large_worktable(inner 0,0) 같은 구성을 막았다.)
_PAIR_SIDE = (2 * BODY, BODY)   # 80 × 40 — 나란히 본체 맞대기
_PAIR_FACE = (2 * BODY, BODY)   # 80 × 40 — 마주보고 안쪽을 접었을 때(돌출 0)


def space_summary(scene, states=None):
    """Phase A에 넘길 용량 정보만 (§6.1). 가구 이름·좌표 없음 — 관계 편향 방지.

    largest_fit.span: 깊이 40 구성이 방 어딘가에서 가질 수 있는 최대 폭 (6개 값 중 첫 성립).
      자리를 지키는 active 로봇만 장애물로 넣는다 — inactive(도크 대기)는 new_scene에서
      놓을 대상이라 장애물로 세면 자기 용량을 깎는다.
    pair_connect_fits: 나란히(side)·마주보기(face)를 나눠 준다 — 80×40만 보면 face(≈122×40)가
      못 들어가는데도 true가 나오던 오판(면별 치수 차)을 없앤다 (§5-2 지적)."""
    active = [s for s in states or [] if s.get("active") != "inactive"]
    obstacles = _obstacles(scene, active)
    span = 40
    for s in _SPANS:
        if _fits_panels(scene, obstacles, _SPAN_PANELS[s]):
            span = s
            break
    return {"largest_fit": {"depth": int(BODY), "span": span},
            "pair_connect_fits": {"side": _fits_rect(scene, *_PAIR_SIDE),
                                  "face": _fits_rect(scene, *_PAIR_FACE)}}


# ---------- 배치 단위 열거 (§6.4) ----------

def _unit_candidates(unit, scene, states, sibling_robots=()):
    """단위 하나의 최종 후보 목록 (랭킹·이격·k 적용 + annotate).

    장애물·앵커·주석은 fixed로 계산한다. fixed에서 빼는 대상: (1) 이 로봇 자신(옮길 것),
    (2) sibling_robots — 이번 턴에 함께 배치될 다른 로봇. 그들의 현재 위치가 서로의 후보를
    막지 않게 한다(쌍 충돌은 _feasible이 두 배치를 합쳐 검증하므로 정확성은 그대로). 단
    이 단위의 relation.anchor인 로봇은 실제 앵커이므로 빼지 않는다 — 다만 main._validate_form이
    relation.anchor를 방의 가구로 제한한 뒤로 이 예외(- {anchor})는 현재 도달하지 않는다.
    Phase 3의 순차 배치(anchor 로봇을 먼저 놓고 그 최종 자리로 밴드를 잡는 것)를 넣으면 다시
    살아난다 — 지우고 다시 쓰느니 남긴다.
    순서: scan(bare) → [facing/alongside면 band_filter] → _rank_thin → annotate(풀에만)."""
    panels = unit["panels"]
    rel = unit.get("relation") or {}
    mode, anchor = rel.get("mode", "free"), rel.get("anchor")
    drop = {unit.get("robot")} | (set(sibling_robots) - {anchor})
    fixed = [s for s in states or [] if s.get("robot") not in drop]
    cells = scan(scene, fixed, panels)
    if mode in ("facing", "alongside") and anchor is not None:
        cells = band_filter(cells, scene, anchor, fixed, mode, panels)
        ranked = _rank_thin(cells, scene, fixed, panels, anchor, mode)
        return [annotate(c, scene, fixed, panels, anchor_id=anchor) for c in ranked]
    ranked = _rank_thin(cells, scene, fixed, panels, None, "free")
    return [annotate(c, scene, fixed, panels) for c in ranked]


def _placement_state(unit, cand, base=None):
    st = dict(base or {})
    st.update({"robot": unit["robot"], "active": "active",
               "x": cand["x"], "y": cand["y"], "rot": cand["rot"],
               "panel_right": unit["panels"][0], "panel_left": unit["panels"][1]})
    return st


def _feasible(assignment, scene, states):
    """단위→후보 배정을 현재 상태에 덮어쓴 전체 상태가 물리 검증을 통과하는가."""
    merged = {s["robot"]: dict(s) for s in states or []}
    for unit, cand in assignment:
        merged[unit["robot"]] = _placement_state(unit, cand,
                                                 merged.get(unit["robot"]))
    return placement.feasibility(list(merged.values()), scene).get("feasible", False)


def _combo(assignment):
    """제시용 조합 dict. Phase B는 이 목록에서 choice 인덱스 하나만 고른다.
    assignment의 cand는 이미 annotate된 후보다(clearance/free/nearby/panel_faces 보유)."""
    return {"placements": [
        {"robot": u["robot"], "furniture": u.get("furniture"),
         "x": c["x"], "y": c["y"], "rot": c["rot"], "panels": list(u["panels"]),
         "clearance": c.get("clearance"), "free": c.get("free"),
         "nearby": c.get("nearby"), "panel_faces": c.get("panel_faces"),
         "rationale": u.get("rationale")}
        for u, c in assignment]}


def _entry(unit, x, y, rot, panels, scene, annot_states, anchor_id):
    """제시용 placement 항목 1개를 그 자리에서 annotate해 만든다 (연결 조합용 — cand가 없다)."""
    a = annotate({"x": x, "y": y, "rot": rot}, scene, annot_states, panels,
                 anchor_id=anchor_id)
    return {"robot": unit["robot"], "furniture": unit.get("furniture"),
            "x": x, "y": y, "rot": rot, "panels": list(panels),
            "clearance": a["clearance"], "free": a["free"],
            "nearby": a["nearby"], "panel_faces": a["panel_faces"],
            "rationale": unit.get("rationale")}


def _connected_combos(units, connection, scene, states):
    """연결(2대 강체) 조합 열거 (§6.2 connection). anchor를 그 relation으로 놓고, moving을
    placement.find_connect로 anchor에 상대 배치해 하나의 강체로 다룬다.

    anchor 후보는 _unit_candidates가 이미 자리 이격·랭킹했으므로, 이를 바깥 루프로 돌면
    anchor 자리 다양성이 유지된다. 좌표·각도는 코드(find_connect)가 계산 — LLM은 연결을
    '선언'만 하고 삼각함수를 풀지 않는다.

    맞댈 각도는 connection이 아니라 **두 unit의 panels에서 유도한다**. 같은 사실을 두 곳에
    두면 어긋난다 — 구버전은 거리를 connection.anchor_panel로 재고 실제로는 unit["panels"]를
    놓아서 '계산한 기하'와 '놓인 기하'가 달랐고, moving의 panels를 [0,0]에서 한쪽만 덮어써
    Phase A가 정한 형태까지 조용히 바뀌었다.
    face는 rot 차 180°라 같은 이름의 패널끼리 맞닿으므로(find_connect docstring) anchor·moving
    모두 같은 side의 각도를 쓴다. side마다 각도가 다를 수 있어 side를 여기서 펼쳐 돈다 —
    find_connect에 both를 넘기면 한 각도 쌍으로 양쪽 거리를 재게 된다.
    그리고 face에서만 feasibility에 connections를 넘겨 connection_touching을 실제로 태운다
    (§6.9 '물리 + 연결 기하'에서 연결 기하가 한 번도 검증되지 않던 결함). side 모드는 본체를
    맞대므로 패널 접촉을 요구하지 않는다 — connections=None이 맞다."""
    by = {u["robot"]: u for u in units}
    ua, um = by.get(connection.get("anchor")), by.get(connection.get("moving"))
    if ua is None or um is None:
        return []
    a_name, m_name = ua["robot"], um["robot"]
    others = [s for s in states or [] if s.get("robot") not in (a_name, m_name)]
    mode = connection.get("mode", "face")
    side_req = connection.get("side", "both")
    if mode == "face":
        sides = (side_req,) if side_req in ("left", "right") else ("left", "right")
    else:
        sides = (side_req,)          # side 모드: find_connect가 앞/뒤를 스스로 돈다
    combos = []
    for ac in _unit_candidates(ua, scene, others):     # 랭킹·이격된 anchor 자리
        astate = _placement_state(ua, ac)
        for sd in sides:
            kw, connections = {}, None
            if mode == "face":
                kw = {"anchor_panel": _panel_for_side(ua["panels"], sd),
                      "moving_panel": _panel_for_side(um["panels"], sd)}
                connections = [{"robot_a": a_name, "side_a": sd,
                                "robot_b": m_name, "side_b": sd}]
            mvs = placement.find_connect(scene, others + [astate], a_name, mode=mode,
                                         side=sd, moving_robot=m_name, **kw)
            for mv in mvs:
                mpanels = list(um["panels"])   # Phase A가 정한 형태를 그대로 놓는다
                mstate = {"robot": m_name, "active": "active", "x": mv["x"], "y": mv["y"],
                          "rot": mv["rot"], "panel_right": mpanels[0],
                          "panel_left": mpanels[1]}
                merged = {s["robot"]: dict(s) for s in others}
                merged[a_name], merged[m_name] = astate, mstate
                if not placement.feasibility(list(merged.values()), scene,
                                             connections=connections).get("feasible", False):
                    continue
                # 각 로봇의 주석은 '상대 로봇을 포함해' 계산한다 — 붙어 있는 짝을 장애물에서
                # 빼면 clearance가 서로를 못 봐 실제 0cm 맞닿음을 50cm로 잘못 보고한다. 연결에서
                # 두 로봇 사이 clearance≈0은 정확한 사실이고, nearby에도 짝이 나타나야 한다.
                combos.append({"placements": [
                    _entry(ua, astate["x"], astate["y"], astate["rot"], ua["panels"],
                           scene, others + [mstate], (ua.get("relation") or {}).get("anchor")),
                    _entry(um, mstate["x"], mstate["y"], mstate["rot"], mpanels,
                           scene, others + [astate], a_name)]})
                if len(combos) >= PRESENT_CAP:
                    return combos
    return combos


def _ordered(units):
    """앵커가 '다른 로봇'인 단위를 그 로봇 뒤로 (§6.4-2). 최대 2대라 단순 정렬로 충분.

    main._validate_form이 relation.anchor를 방의 가구로 제한한 뒤로 이 정렬은 현재 아무것도
    바꾸지 않는다(로봇 앵커가 들어올 수 없다). Phase 3의 순차 배치를 위해 남겨 둔 자리다."""
    names = {u.get("robot") for u in units}
    return sorted(units, key=lambda u: 1 if (u.get("relation") or {}).get("anchor")
                  in names else 0)


def enumerate_units(units, scene, states, connection=None):
    """Phase A 단위들을 유효 조합 목록으로 (§6.4). 모든 조합은 feasibility 통과분만.

    connection이 있으면 두 대를 1강체로 결합해 열거한다(_connected_combos). 없으면
    1개는 후보 나열, 2개는 두 후보 목록의 곱 중 서로 충돌 안 하는 쌍(결합 열거) — 밴드가
    겹치든(같은 앵커) 안 겹치든 feasibility가 걸러 주므로 순차/결합을 나눌 필요가 없다.
    반환 개수는 PRESENT_CAP 상한."""
    units = _ordered(list(units or []))
    if not units:
        return []
    if connection:
        return _connected_combos(units, connection, scene, states)
    # 각 단위 후보는 이미 자리 이격·자리당 rot(≤PRESENT_CAP)로 줄어 있다 — 여기선 재수축하지
    # 않는다(다시 자르면 rot 다양성이 사라진다). 조합 총수만 PRESENT_CAP으로 상한.
    # sibling_robots: 함께 배치될 다른 로봇을 서로의 후보 장애물에서 뺀다 (A-2).
    siblings = {u.get("robot") for u in units}
    unit_cands = [(u, _unit_candidates(u, scene, states, siblings)) for u in units]

    if len(unit_cands) == 1:
        u, cands = unit_cands[0]
        combos = []
        for c in cands:
            if _feasible([(u, c)], scene, states):
                combos.append(_combo([(u, c)]))
                if len(combos) >= PRESENT_CAP:
                    break
        return combos

    (u1, c1), (u2, c2) = unit_cands[0], unit_cands[1]
    if not c1 or not c2:
        return []
    # 다양성 원칙(모듈 상단): 중첩 for는 PRESENT_CAP을 c1[0] 하나로 채워 첫 로봇 자리를
    # 고정시킨다(_rank_thin의 (x,y) 붕괴와 같은 결함의 조합판). 대각 라운드로빈으로 a·b를
    # 동시에 회전시켜 두 로봇 모두의 자리 다양성을 지킨다.
    combos, seen = [], set()
    for r in range(len(c2)):
        for i, a in enumerate(c1):
            j = (i + r) % len(c2)
            if (i, j) in seen:
                continue
            seen.add((i, j))
            if _feasible([(u1, a), (u2, c2[j])], scene, states):
                combos.append(_combo([(u1, a), (u2, c2[j])]))
                if len(combos) >= PRESENT_CAP:
                    return combos
    return combos
