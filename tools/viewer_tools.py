# -*- coding: utf-8 -*-
"""뷰어/HITL tool 2개 + 시각 자가검증(VLM critic) 훅.

ask_user는 HITL-2 게이트다. LLM이 승인을 요청하면 코드가 먼저 배치를 렌더해
VLM critic에게 조화를 묻고(§13), 문제가 있으면 사용자 대신 problems를 돌려줘
LLM이 수정하게 한다 (최대 CRITIC_MAX_ROUNDS회). 통과해야 진짜 사용자에게 간다."""
import base64
import io
import json

import config
import prompts
from tools import STATE
from services import render


def _critic_check():
    """렌더 → VLM 조화 점검. 문제 없으면 None, 있으면 {score, problems}."""
    client, sc = STATE.get("client"), STATE.get("scene")
    if not config.VISUAL_CHECK or client is None or sc is None:
        return None
    if STATE.get("critic_rounds", 0) >= config.CRITIC_MAX_ROUNDS:
        return None
    img = render.render_topdown(sc.environment(), sc.states())
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    intent = STATE.get("intent") or {}
    summary = json.dumps({k: intent.get(k) for k in
                          ("situation", "activity", "number", "user_composition", "posture")},
                         ensure_ascii=False)
    try:
        resp = client.responses.create(
            model=config.CRITIC_MODEL,
            input=[{"role": "developer", "content": prompts.CRITIC_PROMPT},
                   {"role": "user", "content": [
                       {"type": "input_text", "text": "사용자 의도: " + summary},
                       {"type": "input_image", "image_url": "data:image/png;base64," + b64}]}],
            text={"format": {"type": "json_schema", "name": "critic_result",
                             "strict": True, "schema": prompts.CRITIC_SCHEMA}},
        )
        result = json.loads(resp.output_text)
    except Exception as e:
        print("[critic] 시각 검증 실패(무시하고 진행): %s" % e)
        return None
    if result.get("score", 1) >= config.CRITIC_PASS_SCORE or not result.get("problems"):
        return None
    STATE["critic_rounds"] = STATE.get("critic_rounds", 0) + 1
    return result


def ask_user(message):
    """결과 승인 요청 (HITL-2). 시각 자가검증을 먼저 통과해야 사용자에게 도달."""
    check = _critic_check()
    if check is not None:
        return {"visual_check": "failed", "score": check["score"],
                "problems": check["problems"],
                "instruction": "위 문제를 수정한 뒤 다시 ask_user를 호출하라."}
    if STATE.get("auto_approve"):
        return {"approved": True, "feedback": ""}
    viewer = STATE.get("viewer")
    if viewer is not None and viewer.clients:   # 브라우저가 붙어 있을 때만 (아니면 무한 대기)
        return viewer.request_approval(message)
    print("\n[HITL-2 승인 요청] " + str(message))
    for st in STATE["scene"].states():
        print("   ", st)
    ans = input("승인: y / 수정할 점 입력: ").strip()
    if ans.lower() in ("y", "yes", "", "ㅇ", "좋아"):
        return {"approved": True, "feedback": ""}
    return {"approved": False, "feedback": ans}


def ask_clarification(type, question, candidates=None):
    """입력 보완. type: missing_info / ambiguous_intent. 턴당 최대 2회 (코드 강제)."""
    STATE["clarify_count"] = STATE.get("clarify_count", 0) + 1
    if STATE["clarify_count"] > 2: #최대 2회 코드적으로도 제한
        return {"error": "질문 한도(2회) 초과 — 더 묻지 말고 지금까지의 정보로 가장 합리적인 구성을 진행하라"}
    if STATE.get("auto_answer") is not None:
        return {"answer": STATE["auto_answer"]}
    viewer = STATE.get("viewer")
    if viewer is not None and viewer.clients:
        return {"answer": viewer.ask(question, candidates)}
    print("\n[확인 질문] " + str(question))
    if candidates:
        print("   후보:", candidates)
    return {"answer": input("답변: ").strip()}
