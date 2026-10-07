from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional


def _utf8_env() -> Dict[str, str]:
    """Child scripts print non-ASCII text; Windows pipes would otherwise default to cp1252."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


class SpeechToTextWorker:
    def __init__(
        self,
        repo_root: Path,
        callback: Callable[[Dict[str, Any]], None],
        model_name: str,
        backend: str = "vosk",
        source: str = "local",
        device: str = "auto",
        compute_type: str = "auto",
        device_index: int = 0,
        language: str = "auto",
    ):
        self.callback = callback
        self.source = source
        self._remote_audio_queue: Optional[queue.Queue[bytes]] = None
        self._remote_control_queue: Optional[queue.Queue[Dict[str, Any]]] = None
        self._remote_writer_stop = threading.Event()
        stt_args = ["--backend", backend, "--model", str(model_name)]
        if backend == "whisper":
            # Only meaningful for the faster-whisper backend; ignored by the Vosk path.
            stt_args += [
                "--device", str(device),
                "--compute-type", str(compute_type),
                "--device-index", str(device_index),
                "--language", str(language),
            ]
        if source == "remote":
            # Binary stdin carries length-prefixed audio/control frames (see scriptRemoteSTT.py); stdout stays text.
            self.proc = subprocess.Popen(
                [sys.executable, "scriptRemoteSTT.py", *stt_args],
                cwd=str(repo_root / "backend"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_utf8_env(),
                bufsize=0,
            )
        else:
            self.proc = subprocess.Popen(
                [sys.executable, "scriptSTT.py", *stt_args],
                cwd=str(repo_root / "backend"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_utf8_env(),
                bufsize=1,
            )
        if source == "remote":
            self._remote_audio_queue = queue.Queue(maxsize=128)
            self._remote_control_queue = queue.Queue()
            self._remote_writer_thread = threading.Thread(target=self._write_remote_frames, daemon=True)
            self._remote_writer_thread.start()
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="ignore")
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                self.callback(payload)
            except Exception:
                continue

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="ignore")
            if line.strip():
                print(f"[STT] {line.strip()}")

    def pause(self) -> None:
        self._send_control({"STT": "pause"})

    def resume(self) -> None:
        self._send_control({"STT": "resume"})

    def push_audio(self, chunk: bytes) -> None:
        if self.source != "remote" or self._remote_audio_queue is None or self._remote_writer_stop.is_set():
            return
        try:
            self._remote_audio_queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._remote_audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._remote_audio_queue.put_nowait(chunk)
            except queue.Full:
                pass

    def _send_control(self, obj: Dict[str, Any]) -> None:
        if not self.proc.stdin:
            return
        if self.source == "remote":
            if self._remote_control_queue is None:
                return
            if obj.get("STT") == "pause" and self._remote_audio_queue is not None:
                while True:
                    try:
                        self._remote_audio_queue.get_nowait()
                    except queue.Empty:
                        break
            self._remote_control_queue.put(obj)
        else:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    def _write_remote_frames(self) -> None:
        audio_queue = self._remote_audio_queue
        control_queue = self._remote_control_queue
        if audio_queue is None or control_queue is None:
            return

        while not self._remote_writer_stop.is_set():
            if self.proc.poll() is not None:
                return
            try:
                control = control_queue.get_nowait()
            except queue.Empty:
                try:
                    chunk = audio_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                self._write_frame(b"A", chunk)
            else:
                self._write_frame(b"C", json.dumps(control).encode("utf-8"))

    def _write_frame(self, frame_type: bytes, body: bytes) -> None:
        if not self.proc.stdin:
            return
        header = len(body) + 1
        try:
            self.proc.stdin.write(header.to_bytes(4, "big") + frame_type + body)
            self.proc.stdin.flush()
        except Exception:
            self._remote_writer_stop.set()

    def close(self) -> None:
        self._remote_writer_stop.set()
        if self.proc.poll() is None:
            self.proc.terminate()


class TextToSpeechWorker:
    def __init__(self, repo_root: Path, callback: Callable[[Dict[str, Any]], None]):
        self.callback = callback
        self.proc = subprocess.Popen(
            [sys.executable, "scriptTTS.py"],
            cwd=str(repo_root / "backend"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_utf8_env(),
            bufsize=1,
        )
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                self.callback(payload)
            except Exception:
                continue

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            if line.strip():
                print(f"[TTS] {line.strip()}")

    def say(self, text: str, model: str, volume: int, output: str = "local", request_id: str = "") -> None:
        self._send({"volume": int(volume)})
        self._send({"text": text, "model": model, "output": output, "requestId": request_id})

    def pause(self) -> None:
        self._send({"tts": "pause"})

    def resume(self) -> None:
        self._send({"tts": "resume"})

    def stop_local(self, request_id: str) -> None:
        self._send({"tts": "stop_local", "requestId": request_id})

    def _send(self, obj: Dict[str, Any]) -> None:
        if self.proc.stdin:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
