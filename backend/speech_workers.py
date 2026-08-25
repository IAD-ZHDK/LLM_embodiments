from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional


class SpeechToTextWorker:
    def __init__(
        self,
        repo_root: Path,
        callback: Callable[[Dict[str, Any]], None],
        model_name: str,
        backend: str = "vosk",
        source: str = "local",
    ):
        self.callback = callback
        self.source = source
        if source == "remote":
            # Binary stdin carries length-prefixed audio/control frames (see scriptRemoteSTT.py); stdout stays text.
            self.proc = subprocess.Popen(
                ["python3", "scriptRemoteSTT.py", "--model", str(model_name)],
                cwd=str(repo_root / "backend"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        else:
            self.proc = subprocess.Popen(
                ["python3", "scriptSTT.py", "--backend", backend, "--model", str(model_name)],
                cwd=str(repo_root / "backend"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
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
        if self.source != "remote":
            return
        self._write_frame(b"A", chunk)

    def _send_control(self, obj: Dict[str, Any]) -> None:
        if not self.proc.stdin:
            return
        if self.source == "remote":
            self._write_frame(b"C", json.dumps(obj).encode("utf-8"))
        else:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    def _write_frame(self, frame_type: bytes, body: bytes) -> None:
        if not self.proc.stdin:
            return
        header = len(body) + 1
        try:
            self.proc.stdin.write(header.to_bytes(4, "big") + frame_type + body)
            self.proc.stdin.flush()
        except Exception:
            pass

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()


class TextToSpeechWorker:
    def __init__(self, repo_root: Path, callback: Callable[[Dict[str, Any]], None]):
        self.callback = callback
        self.proc = subprocess.Popen(
            ["python3", "scriptTTS.py"],
            cwd=str(repo_root / "backend"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
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

    def say(self, text: str, model: str, volume: int) -> None:
        self._send({"volume": int(volume)})
        self._send({"text": text, "model": model})

    def pause(self) -> None:
        self._send({"tts": "pause"})

    def resume(self) -> None:
        self._send({"tts": "resume"})

    def _send(self, obj: Dict[str, Any]) -> None:
        if self.proc.stdin:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
