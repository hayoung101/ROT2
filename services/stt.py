import ctypes
import json
import os
from dotenv import load_dotenv
load_dotenv()
import time
from io import BytesIO

import speech_recognition as sr

import config

POLL_SECONDS = 0.01   # 폴링 (대기시간) 간격
LOG_PATH = os.path.join("logs", "stt_log.json")


# 스페이스바 눌림 감지 확인 (0x20은 스페이스바의 가상 키 코드)
def is_space_pressed():
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x20) & 0x8000)


# 스페이스바가 눌려있는 동안 음성 녹음
def record_while_space_is_pressed(source):
    frames = []
    print("Recording... release Space to stop.")

    while is_space_pressed():
        frames.append(source.stream.read(source.CHUNK))

    return sr.AudioData(
        b"".join(frames),
        source.SAMPLE_RATE,
        source.SAMPLE_WIDTH,
    )


# Groq API를 사용해 음성 인식
def transcribe_audio(client, audio):
    try:
        wav_file = BytesIO(audio.get_wav_data())
        wav_file.name = "speech.wav"

        transcription = client.audio.transcriptions.create(
            file=wav_file,
            model=config.GROQ_MODEL,
            language=config.GROQ_LANGUAGE,
        )
        text = transcription.text
        print("You said: " + text)
        save_recording_log(text)
        return text
    except sr.UnknownValueError:
        print("Sorry, I could not understand what you said.")
    except Exception as e:
        print("Sorry, an error occurred while processing your request: {0}".format(e))
    return None


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
