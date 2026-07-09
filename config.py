# -*- coding: utf-8 -*-
"""API 키 로드 + 모델/로봇 상수 (단일 출처)."""
import os
from dotenv import load_dotenv
load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# 모델
GROQ_MODEL = "whisper-large-v3-turbo"   # STT
GROQ_LANGUAGE = "ko"
OPENAI_MODEL = "gpt-5.5"                # 의도분석 + agent

# 로봇 물리 상수 (cm) — BoT² 확정 스펙
BODY_W_CM = 40
BODY_D_CM = 40
BODY_H_CM = 50
PANEL_W_CM = 40
PANEL_LEN_CM = 30
PANEL_ANGLES = (0, 45, 90, 135, 180)
MAX_COMBINE = 2          # 조합 가능한 최대 로봇 수

# 확정: 단일 기종 2대
ROBOT_NAMES = ("BOT 1", "BOT 2")

# 미사용(inactive) 로봇의 홈 도크: 절대 좌표가 아니라 '벽에서 얼마' 방식으로 계산한다.
# 원점 구석(0,0)의 두 벽에 본체를 붙이고, 같은 벽(y=0)을 따라 나란히 정박.
# 방 크기와 무관하게 성립: x = 반폭 + i×(본체폭 + 간격), y = 반폭.
DOCK_GAP_CM = 20   # 도크 간 여유


def home_for(name):
    """로봇 이름 → 홈 도크 좌표 (원점 구석 벽 기준 자동 계산)."""
    try:
        idx = ROBOT_NAMES.index(name)
    except ValueError:
        idx = 0
    x = BODY_W_CM / 2 + idx * (BODY_W_CM + DOCK_GAP_CM)
    y = BODY_D_CM / 2
    return (int(x), int(y))
