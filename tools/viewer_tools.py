# -*- coding: utf-8 -*-
"""뷰어/HITL tool.

ask_user는 HITL-2 게이트다 — 배치 결과를 사용자에게 한 번 승인받고,
승인 즉시 코드가 배치를 확정한다 (LLM의 commit 호출에 의존하지 않음).
되묻기는 의도 단계에서 HITL 앞단이 처리한다 → main._ask_clarification."""
import config
from tools import STATE
from tools.context_tools import commit_layout


def _commit_on_approval(message):
    """HITL-2 승인 즉시 코드가 배치를 확정한다.

    main._run_form_layer가 '승인 시엔 ask_user가 이미 커밋했으므로 롤백 대상이 아니다'를
    전제로 짜여 있다 — 여기서 커밋하지 않으면 승인해도 history에 아무것도 남지 않는다.
    커밋 로직은 commit_layout 한 벌 — 변화가 없으면 재커밋하지 않는다(멱등)."""
    if STATE.get("scene") is None:
        return
    res = commit_layout(message)
    if res.get("noop"):
        return                      # 변화 없음 — 재커밋도 안내도 하지 않는다
    print("[commit] turn %d 확정" % res["turn"])
    viewer = STATE.get("viewer")
    if viewer is not None:          # turn 번호는 내부 개념 — 채팅에 노출하지 않는다
        viewer.chat("system", "배치가 확정되었습니다.")


def ask_user(message):
    """결과 승인 요청 (HITL-2). 승인받으면 그 자리에서 코드가 확정(commit)한다."""
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
            if ans.lower() in config.APPROVE_WORDS:
                res = {"approved": True, "feedback": ""}
            else:
                res = {"approved": False, "feedback": ans}
    if res.get("approved"):
        _commit_on_approval(message)   # 승인 직후 코드가 확정 — 못 박음
    return res


