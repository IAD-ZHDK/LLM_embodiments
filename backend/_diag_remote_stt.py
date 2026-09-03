#!/usr/bin/env python3
"""Diagnostic driver v3: feed speech-like audio directly into scriptRemoteSTT._run_whisper."""
import math
import struct
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import scriptRemoteSTT as r  # noqa: E402

orig_push = r.WhisperSession.push
orig_fin = r.WhisperSession._finalize


def push_logged(self, chunk):
    speaking_before = self._speaking
    res = orig_push(self, chunk)
    print(
        f"[push] chunk={len(chunk)} speaking={self._speaking} "
        f"silence_since={self._silence_since} buf={len(self._buffer)} "
        f"was={speaking_before} -> {res!r}",
        file=sys.stderr,
        flush=True,
    )
    return res


def fin_logged(self):
    print(f"[finalize] buf_in={len(self._buffer)}", file=sys.stderr, flush=True)
    out = orig_fin(self)
    print(f"[finalize] -> {out!r}", file=sys.stderr, flush=True)
    return out


r.WhisperSession.push = push_logged
r.WhisperSession._finalize = fin_logged
orig_send = r.send_message


def send_logged(name, value):
    print(f"[send_message] {name}={value}", file=sys.stderr, flush=True)
    orig_send(name, value)


r.send_message = send_logged


def speech_like(dur_s: float, rate: int = 16000) -> bytes:
    n = int(rate * dur_s)
    out = bytearray()
    for i in range(n):
        t = i / rate
        env = 0.5 + 0.5 * math.sin(2 * math.pi * 4 * t)
        s = sum(math.sin(2 * math.pi * f * t) for f in (220, 440, 880, 1760, 3000))
        s = s / 5 * env * 25000
        out += struct.pack("<h", int(max(-32768, min(32767, s))))
    return bytes(out)


def main() -> int:
    session = r.WhisperSession(
        model_name="tiny",
        device="cpu",
        compute_type="int8",
        device_index=0,
        language="en",
    )
    audio = speech_like(2.5)
    chunk = 1024
    # Feed in chunks
    for i in range(0, len(audio), chunk):
        text = session.push(audio[i : i + chunk])
        if text:
            print(f"[MAIN got] {text!r}", flush=True)
    # Then ~1s silence so finalize fires
    silence = b"\x00\x00" * 16000
    for i in range(0, len(silence), chunk):
        text = session.push(silence[i : i + chunk])
        if text:
            print(f"[MAIN got] {text!r}", flush=True)
    print("[MAIN done]", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
