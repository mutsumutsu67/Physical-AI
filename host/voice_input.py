#!/usr/bin/env python3
"""voice_input.py - Windows（ホスト）で動かす音声入力
  Enter で録音開始 → もう一度 Enter で停止 → OpenAI 音声認識 → コンテナの /workspace/inbox へ送る

準備（PowerShell、1回だけ）:
    pip install openai sounddevice soundfile numpy
    $env:OPENAI_API_KEY = "sk-..."        # または、このファイルと同じフォルダに .env（OPENAI_API_KEY=...）
使い方:
    python voice_input.py                 # マイクから
    python voice_input.py --text "お辞儀して"   # マイクなしで送信テスト
    python voice_input.py --list-devices  # マイク一覧（--device 番号 で指定）
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

CONTAINER = os.environ.get("PAI_CONTAINER", "pai_ros2")
INBOX = "/workspace/inbox"
RATE = 16000


def load_env():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def send_text(text):
    """テキストを一時ファイルにして docker cp でコンテナの inbox へ"""
    subprocess.run(["docker", "exec", CONTAINER, "mkdir", "-p", INBOX], check=True)
    name = time.strftime("%Y%m%d_%H%M%S") + ".txt"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp = f.name
    try:
        subprocess.run(["docker", "cp", tmp, f"{CONTAINER}:{INBOX}/{name}"], check=True)
    finally:
        os.remove(tmp)
    print(f"  → 送信: {text}")


def record(device=None):
    import numpy as np
    import sounddevice as sd
    frames = []
    input("Enter で録音開始 ▶ ")
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", device=device,
                        callback=lambda d, n, t, s: frames.append(d.copy())):
        input("● 録音中… 話し終えたら Enter ")
    audio = np.concatenate(frames) if frames else np.zeros((0, 1), dtype="int16")
    print(f"  録音 {len(audio) / RATE:.1f} 秒")
    return audio


def transcribe(audio):
    import soundfile as sf
    from openai import OpenAI
    client = OpenAI(default_headers={"Accept-Encoding": "gzip, deflate"})
    model = os.environ.get("OPENAI_STT_MODEL", "whisper-1")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = f.name
    sf.write(wav, audio, RATE)
    try:
        t0 = time.time()
        with open(wav, "rb") as fh:
            r = client.audio.transcriptions.create(model=model, file=fh, language="ja")
        print(f"  認識 {time.time() - t0:.1f} 秒（{model}）")
        return r.text.strip()
    finally:
        os.remove(wav)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="マイクを使わずにこのテキストを送る")
    ap.add_argument("--device", type=int, default=None)
    ap.add_argument("--list-devices", action="store_true")
    args = ap.parse_args()
    load_env()
    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return
    if args.text:
        send_text(args.text)
        return
    print("音声入力（Ctrl+C で終了）")
    while True:
        try:
            audio = record(args.device)
            if len(audio) < RATE * 0.5:
                print("  短すぎるのでやり直し")
                continue
            text = transcribe(audio)
            print(f"🎤 「{text}」")
            if text:
                send_text(text)
        except KeyboardInterrupt:
            print("\n終了")
            break
        except Exception as e:
            print("  エラー:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
