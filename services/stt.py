# -*- coding: utf-8 -*-
"""STT: 브라우저(push-to-talk)에서 올라온 오디오 바이트를 Groq Whisper로 전사 + 로그.

v4에서 콘솔·Windows(ctypes)·speech_recognition 경로는 제거됨 — 음성 입력은
브라우저 MediaRecorder → POST /stt 하나뿐이다 (ROS2/리눅스 전환과도 호환).
"""
import json
import os
import time
from io import BytesIO

import config

LOG_PATH = os.path.join("logs", "stt_log.json")


# 녹음(전사) 로그를 json으로 누적 저장
def save_recording_log(text, path=LOG_PATH):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        logs = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    logs = json.load(f)
            except Exception:
                logs = []
        logs.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[stt] 로그 저장 실패: {0}".format(e))


# 브라우저(push-to-talk)에서 올라온 오디오 바이트를 Groq로 전사
def transcribe_bytes(client, data, mime="audio/webm"):
    try:
        buf = BytesIO(data)
        buf.name = "speech." + ("wav" if "wav" in mime else "webm")
        transcription = client.audio.transcriptions.create(
            file=buf,
            model=config.GROQ_MODEL,
            language=config.GROQ_LANGUAGE,
        )
        text = transcription.text.strip()
        if text:
            print("You said: " + text)
            save_recording_log(text)
        return text
    except Exception as e:
        print("[stt] 전사 실패: {0}".format(e))
        return ""
