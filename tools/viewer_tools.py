# -*- coding: utf-8 -*-
"""뷰어/HITL tool 1개.

ask_user는 HITL-2 게이트다 — 배치 결과를 사용자에게 한 번 승인받고,
승인 즉시 코드가 배치를 확정한다. (되묻기는 의도 단계에서 HITL 앞단에 처리 → main.py)"""
from tools import STATE, metric
from tools.context_tools import commit_layout


def _commit_on_approval(message):
    """HITL-2 승인 즉시 코드가 배치를 확정한다 (LLM의 commit 호출에 의존하지 않음).
    커밋 로직은 commit_layout 한 벌만 쓴다 — 변화가 없으면 재커밋하지 않는다."""
    if STATE.get("scene") is None:
        return
    res = commit_layout(message)
    viewer = STATE.get("viewer")
    if not res["noop"] and viewer is not None:   # 실제로 커밋됐을 때만 안내 (멱등). turn 번호는 내부 개념 — 노출 금지
        viewer.chat("system", "배치가 확정되었습니다.")


def ask_user(message):
    """결과 승인 요청 (HITL-2). 승인받으면 그 자리에서 코드가 확정(commit)한다."""
    metric("hitl2_attempts")   # 실험 metrics: HITL-2 시도/거부 (통과율 분모/분자)
    if STATE.get("auto_approve"):
        res = {"approved": True, "feedback": ""}
    else:
        viewer = STATE.get("viewer")
        if viewer is not None and viewer.clients:   # 브라우저가 붙어 있을 때만 (아니면 무한 대기)
            res = viewer.request_approval(message)
        else:
            print("\n[HITL-2 승인 요청] " + str(message))
            for st in STATE["scene"].states():
                print("   ", st)
            ans = input("승인: y / 수정할 점 입력: ").strip()
            if ans.lower() in ("y", "yes", "", "ㅇ", "좋아"):
                res = {"approved": True, "feedback": ""}
            else:
                res = {"approved": False, "feedback": ans}
    if res.get("approved"):
        _commit_on_approval(message)   # 승인 직후 코드가 확정 — 못 박음
    else:
        metric("hitl2_rejects")
    return res
