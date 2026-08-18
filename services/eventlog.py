# -*- coding: utf-8 -*-
"""append-only 이벤트 로그 — 되돌릴 수 없는 사실의 단일 서버 시각 기록 (§17.6·17.7).

SceneState(commit/revert = 되돌릴 수 있는 것)와 분리한다. revert가 일어났다는 사실
자체가 이벤트이므로 같은 객체에 두면 "revert가 events도 되돌려야 하나"라는 잘못된 질문이
생긴다 — 답은 '아니오'다. session.json에는 SceneState.save()가 events를 commits의
'형제'로 얹는다(§17.6 스키마, 기존 필드 불변).

다양성 지표(제시 자리 수·자리당 rot 수·조합의 로봇별 자리 수)는 상태 스냅샷에서 소급
계산이 불가능한 유일한 종류라(§17.5 — 스냅샷을 봐도 코드가 그때 몇 개의 자리를 제시했는지
복원할 수 없다) 커밋과 무관하게 반드시 여기 남긴다.

서버 시각 단일 기준(§17.7): t는 세션 시작(reset) 기준 초. 브라우저 시각을 섞지 않는다.
"""
import time

_events = []
_t0 = time.time()


def reset(t0=None):
    """세션 시작 시 시각 원점을 잡고 로그를 비운다 (프로그램 재시작 = 새 세션)."""
    global _events, _t0
    _events = []
    _t0 = t0 if t0 is not None else time.time()


def record(type, **fields):
    """이벤트 1건 append. t는 세션 시작 기준 서버 시각(초). 반환은 기록된 이벤트."""
    ev = {"t": round(time.time() - _t0, 1), "type": type}
    ev.update(fields)
    _events.append(ev)
    return ev


def events():
    """지금까지의 이벤트 목록 (append-only 사본)."""
    return list(_events)
