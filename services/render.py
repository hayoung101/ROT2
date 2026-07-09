# -*- coding: utf-8 -*-
"""탑다운 2D 배치도 렌더 (PIL, 순수 함수) — 시각 자가검증(VLM critic)용.

collision.rect_corners를 재사용하므로 물리 계산과 그림이 항상 일치한다.
좌표계: 방 좌표 (x 오른쪽+, y 위쪽+) → 이미지에서는 y를 뒤집어 위가 +y."""
from PIL import Image, ImageDraw

from services import collision, placement

SCALE = 2      # px per cm
MARGIN = 24    # px

ROBOT_COLORS = {"BOT 1": (66, 133, 244), "BOT 2": (234, 88, 12)}


def render_topdown(scene, robots, path=None):
    w, d = scene["width"], scene["depth"]
    W, H = w * SCALE + MARGIN * 2, d * SCALE + MARGIN * 2
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)

    def P(x, y):
        return (MARGIN + x * SCALE, H - MARGIN - y * SCALE)

    # 방 경계
    dr.rectangle([P(0, d), P(w, 0)], outline="black", width=3)

    # 기존 가구: 회색 + label + 앞방향 화살표
    for f in scene.get("pre_existing_furniture", []):
        cs = [P(px, py) for px, py in collision.rect_corners(*collision.furniture_rect(f))]
        dr.polygon(cs, fill=(210, 210, 210), outline="black")
        fv = placement._front_vec(f.get("rot", 0))
        dr.line([P(f["x"], f["y"]),
                 P(f["x"] + fv[0] * 25, f["y"] + fv[1] * 25)], fill="black", width=3)
        dr.text(P(f["x"], f["y"] - 8), str(f.get("label", f.get("id", ""))),
                fill="black", anchor="mm")

    # 로봇: 본체(진한 색) + 펼친 패널(연한 색) + 이름/각도 주석
    for st in robots or []:
        col = ROBOT_COLORS.get(st.get("robot"), (120, 120, 120))
        light = tuple(min(255, c + 90) for c in col)
        rects = collision.footprint_rects(st)
        for i, r in enumerate(rects):
            cs = [P(px, py) for px, py in collision.rect_corners(*r)]
            dr.polygon(cs, fill=(col if i == 0 else light), outline="black")
        dr.text(P(st["x"], st["y"] + 6), str(st.get("robot", "")), fill="white", anchor="mm")
        dr.text(P(st["x"], st["y"] - 8),
                "r%d L%d R%d" % (st.get("rot", 0), st.get("panel_left", 0), st.get("panel_right", 0)),
                fill="white", anchor="mm")

    if path:
        img.save(path)
    return img
