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
    ):
        self.callback = callback
        self.proc = subprocess.Popen(
            ["python3", "scriptSTT.py", "--backend", backend, "--model", str(model_name)],
            cwd=str(repo_root / "python"),
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
                print(f"[STT] {line.strip()}")

    def pause(self) -> None:
        self._send({"STT": "pause"})

    def resume(self) -> None:
        self._send({"STT": "resume"})

    def _send(self, obj: Dict[str, Any]) -> None:
        if self.proc.stdin:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()


class TextToSpeechWorker:
    def __init__(self, repo_root: Path, callback: Callable[[Dict[str, Any]], None]):
        self.callback = callback
        self.proc = subprocess.Popen(
            ["python3", "scriptTTS.py"],
            cwd=str(repo_root / "python"),
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
