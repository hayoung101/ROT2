# -*- coding: utf-8 -*-
"""LLM 호출 계층.

- ask_intent(): 의도층 — 발화 → intent JSON (structured outputs strict)
- run_agent(): 형태층 — tool-call 루프 (중계만). tool_call을 registry.dispatch로 실행
"""
import json

import config
from prompts import AGENT_PROMPT, INTENT_PROMPT, INTENT_SCHEMA, ROBOT_MECHANISM


# 전사된 텍스트를 OpenAI LLM를 통해 의도 분석
def ask_intent(client, usertext, prev_intent=None, room_furniture=None):
    try:
        response = client.responses.create(
            model=config.OPENAI_MODEL,
            input=[
                {
                    "role": "developer",
                    "content": INTENT_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "직전 상황(prev_intent)": prev_intent,
                        "방의 기존 가구(pre_existing_furniture)": room_furniture,
                        "새 발화(utterance)": usertext,
                    }, ensure_ascii=False),
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "intent_result",
                    "strict": True,
                    "schema": INTENT_SCHEMA,
                }
            },
        )

        result = json.loads(response.output_text)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result
    except Exception as e:
        print("Sorry, an error occurred while asking OpenAI: {0}".format(e))
    return None


def run_agent(client, intent, utterance, max_steps=20):
    """형태층 tool-call 루프. LLM 제안 → tool 실행 → 결과 반환 반복.

    agent.py는 tool을 갖지 않는다 — tool_call(JSON)을 registry.HANDLERS에서
    이름으로 찾아 실행하는 중계자일 뿐이다."""
    import tools
    from tools import registry

    tools.STATE["intent"] = intent
    tools.STATE["utterance"] = utterance
    tools.STATE["critic_rounds"] = 0   # 시각 자가검증 라운드 초기화 (턴 단위)
    tools.STATE["clarify_count"] = 0   # 되묻기 한도 초기화 (턴당 2회)

    msgs = [
        {"role": "developer", "content": ROBOT_MECHANISM + AGENT_PROMPT},
        {"role": "user", "content": json.dumps({
            "현재 방(space)": tools.STATE["scene"].space if tools.STATE["scene"] else None,
            "새 요청(intent)": intent,
            "사용자 발화 원문(utterance)": utterance,
        }, ensure_ascii=False)},
    ]

    for _ in range(max_steps):
        response = client.responses.create(
            model=config.OPENAI_MODEL, input=msgs, tools=registry.TOOLS)
        msgs += response.output
        calls = [o for o in response.output
                 if getattr(o, "type", None) == "function_call"]
        if not calls:
            return response.output_text   # tool 호출이 없으면 종료 (턴 마무리 발화)
        for call in calls:
            try:
                args = json.loads(call.arguments)
                args = {k: v for k, v in args.items() if v is not None}
                result = registry.dispatch(call.name, args)
            except Exception as e:   # tool 실패도 LLM에게 알려 스스로 수정하게
                result = {"error": str(e)}
            print("[tool] %s(%s) -> %s" % (call.name, call.arguments[:80],
                                           str(result)[:100]))
            msgs.append({"type": "function_call_output", "call_id": call.call_id,
                         "output": json.dumps(result, ensure_ascii=False, default=str)})
    return "(중단: tool 루프 최대 단계 초과)"
