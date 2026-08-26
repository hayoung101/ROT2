# -*- coding: utf-8 -*-
"""API 키 로드 + 모델/로봇 상수 (단일 출처)."""
import os
from dotenv import load_dotenv
load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# 모델
OPENAI_MODEL = "gpt-5.5"                # 의도분석 + agent
# Phase B(자리 선택)는 '후보 중 인덱스 고르기'라 추론 부담이 낮아 더 빠른 모델을 쓸 수 있다.
# 기본값은 동일 모델(동작 무변경) — 실제 빠른 id는 llm_call 계측(§B-1)을 보고 env로 주입한다.
MODEL_PLACE = os.environ.get("OPENAI_MODEL_PLACE", OPENAI_MODEL)

# 로봇 물리 상수 (cm) — BoT² 확정 스펙
BODY_W_CM = 40
BODY_D_CM = 40
BODY_H_CM = 50
PANEL_W_CM = 40
PANEL_LEN_CM = 30
PANEL_ANGLES = (0, 45, 90, 135, 180)
MAX_COMBINE = 2          # 조합 가능한 최대 로봇 수
# ↑ BODY_H_CM·PANEL_W_CM·MAX_COMBINE은 코드가 참조하지 않는 '문서용 상수'다.
#   ROBOT_MECHANISM 프롬프트의 서술과 값이 갈라지지 않도록 여기 단일 출처로 남긴다.

# 확정: 단일 기종 2대
ROBOT_NAMES = ("BOT 1", "BOT 2")

# 콘솔 fallback에서 승인으로 간주하는 입력 (HITL-1·HITL-2 공용, 소문자 비교).
# 두 곳에 따로 두면 한쪽만 고쳐져 게이트가 비대칭이 된다.
APPROVE_WORDS = ("y", "yes", "", "ㅇ", "네", "좋아")

# 미사용(inactive) 로봇의 홈 도크 — 우선순위: scene JSON의 "dock" 필드가 있으면 그쪽이
# 우선이고, home_for()는 dock이 없는 방을 위한 fallback이다 (scene.py.dock_state 참고).
# fallback 계산: 원점 구석(0,0)의 두 벽에 본체를 붙이고, 같은 벽(y=0)을 따라 나란히 정박.
# 방 크기와 무관하게 성립: x = 반폭 + i×(본체폭 + 간격), y = 반폭.
DOCK_GAP_CM = 20   # 도크 간 여유
# 주의: scenes/*.json 5개 전부에 dock이 있어 home_for()는 현재 도달하지 않는다.
#       dock 없는 방을 추가할 때를 위한 안전망으로만 남긴다.


def home_for(name):
    """로봇 이름 → 홈 도크 좌표 (scene JSON에 dock이 없을 때의 fallback — 원점 구석 벽 기준 자동 계산)."""
    try:
        idx = ROBOT_NAMES.index(name)
    except ValueError:
        idx = 0
    x = BODY_W_CM / 2 + idx * (BODY_W_CM + DOCK_GAP_CM)
    y = BODY_D_CM / 2
    return (int(x), int(y))
