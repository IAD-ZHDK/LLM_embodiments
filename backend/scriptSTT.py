#!/usr/bin/env python3
# filepath: /Users/lfranzke/Documents/ZHdK/11_Physical Computing Lab/Technology/LLM_embodiments/python/scriptSTT.py

import os
import sys
import argparse
import json
import numpy as np
import time
import threading
from Microphone.scriptMicrophone import MicrophoneStream
from Microphone.vad_utils import VAD
import importlib.util

# Check if vosk is installed
vosk_available = importlib.util.find_spec("vosk") is not None
if not vosk_available:
    print("Vosk not found. Please install it with: pip install vosk", file=sys.stderr)
else:
    from vosk import Model, KaldiRecognizer
    # Also import the model downloader if available
    try:
        from model_downloader import download_and_extract_model, check_model_exists
    except ImportError:
        # Define minimal versions of these functions if missing
        def check_model_exists(model_name, model_path):
            return os.path.exists(os.path.join(model_path, model_name))
        
        def download_and_extract_model(model_name, model_path, base_url=""):
            print(f"Model downloader not available. Please download model manually to {os.path.join(model_path, model_name)}", file=sys.stderr)
            return False

# Check if faster-whisper is installed
whisper_available = importlib.util.find_spec("faster_whisper") is not None
if whisper_available:
    from faster_whisper import WhisperModel
else:
    print("faster-whisper not found. Install it with: pip install faster-whisper", file=sys.stderr)


def _cuda_available() -> bool:
    """Best-effort GPU probe for the "auto" whisper device setting."""
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False

# Constants
DEVICE_INDEX = 0  # Update this to match speaker device index
RATE = 16000      # Sample rate
CHUNK = 1024      # Frame size
THRESHOLD = 100  # Adjust this to match your environment's noise level

MODEL_PATH = "STTmodels/"  # Default model path
MODEL_DEFAULT = "vosk-model-small-en-us-0.15"  # Default model https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip
MODEL_EN_LARGE = "vosk-model-en-us-0.22-lgraph"       # Large English model
MODEL_DE_SMALL = "vosk-model-small-de-0.15"    # German model
current_model = 0  # Default model index
stt_backend = "vosk"
whisper_device = "auto"
whisper_compute_type = "auto"
whisper_device_index = 0
whisper_language = "auto"
# Global variables for communication
_recognizer = None
_recognizer_ready = threading.Event()
mic = None

class SpeechRecognizer:
    """
    Speech recognition using Vosk.
    """
    def __init__(self, audio_source, size="medium", callback=None, rate=RATE, chunk=CHUNK, modelName=MODEL_DEFAULT):
       # check if model is string or number
        if isinstance(current_model, int):
            if current_model == 0:
                modelName = MODEL_DEFAULT
            elif current_model == 1:
                modelName = MODEL_EN_LARGE
            elif current_model == 2:
                modelName = MODEL_DE_SMALL
            elif current_model == 3:
                modelName = "vosk-model-en-us-0.42-gigaspeech"
        elif isinstance(current_model, str):
            modelName = str(current_model)
        else:
            modelName = MODEL_DEFAULT
        print(f"Using model: {current_model}", file=sys.stderr)
        # Check if model exists, otherwise download
        if not check_model_exists(modelName, MODEL_PATH):
            print(f"Model '{modelName}' not found. Attempting to download...", file=sys.stderr)
            
            # Try to download the specified model
            success = download_and_extract_model(modelName, MODEL_PATH)
            
            # If that fails and it's not the default model, try the default
            if not success and modelName != MODEL_DEFAULT:
                print(f"Falling back to default model '{MODEL_DEFAULT}'", file=sys.stderr)
                success = download_and_extract_model(MODEL_DEFAULT, MODEL_PATH)
                if success:
                    modelName = MODEL_DEFAULT
        else:
            print(f"Using model '{modelName}'", file=sys.stderr)

        self.PAUSE = False
        self.callback = callback or self.default_callback
        self.model_path = os.path.join(MODEL_PATH, modelName)
        self.model = None
        self.recognizer = None
        self.running = False
        self.RATE = rate
        self.CHUNK = chunk
        self.audio_source = audio_source

        if not os.path.exists(self.model_path):
            print(f"Model '{self.model_path}' was not found. Please check the path.", file=sys.stderr)
            return

        try:
            self.model = Model(self.model_path)
            self.recognizer = KaldiRecognizer(self.model, self.RATE)
            self.pre_buffer = []  # Buffer for pre-voice audio
            self.pre_buffer_maxlen = int(1.0 * rate / chunk)  # e.g., 1 second of audio
            print(f"✅ Speech recognizer initialized with {modelName}", file=sys.stderr)
        except Exception as e:
            print(f"❌ Error initializing speech recognizer: {e}", file=sys.stderr)

    def default_callback(self, text, partial):
        if text:
            print(f"Final Text: {text}", file=sys.stderr)
        if partial:
            print(f"Partial Text: {partial}", file=sys.stderr)

    def run(self):
        self.running = True
        print("\nSpeak now...", file=sys.stderr)

        # Check if voice gating is available
        has_voice_gate = (hasattr(self.audio_source, "speaker") and 
                          hasattr(self.audio_source.speaker, "is_voice_active") and 
                          callable(getattr(self.audio_source.speaker, "is_voice_active", None)))
        
        print(f"Voice gating {'enabled' if has_voice_gate else 'disabled'}", file=sys.stderr)
        
        # Choose the appropriate processing method
        if has_voice_gate:
            self._run_with_voice_gate()
        else:
            self._run_without_voice_gate()

    def _run_with_voice_gate(self):
        """Process audio with voice activity detection gating."""
        voice_status_threshold = False
        prev_voice_status_threshold = False
        voice_on_since = None
        voice_off_since = None

        while self.running:
            # --- Voice timing threshold logic ---
            voice_now = self.audio_source.speaker.is_voice_active()
            
            current_time = time.time()
            
            if voice_now:
                if voice_on_since is None:
                    voice_on_since = current_time
                voice_off_since = None
                if not voice_status_threshold and (current_time - voice_on_since > 0.1):
                    voice_status_threshold = True
            else:
                if voice_off_since is None:
                    voice_off_since = current_time
                voice_on_since = None
                if voice_status_threshold and (current_time - voice_off_since > 0.9):
                    voice_status_threshold = False

            if not self.running:
                break

            # --- Handle transition from speaking to silence (finalize utterance) ---
            if prev_voice_status_threshold and not voice_status_threshold:
                # Feed a few chunks of silence to flush the recognizer
                for _ in range(3):
                    self.recognizer.AcceptWaveform(b'\x00' * self.CHUNK)
                # Get the final result
                result_json = json.loads(self.recognizer.Result())
                text = result_json.get('text', '')
                if text:
                    self.callback(text, None)
                self.recognizer.Reset()
            
            # --- Always read audio, but only process if voice is active ---
            try:
                data = self.audio_source.read(self.CHUNK)
            except OSError as e:
                print(f"Audio input overflow: {e}", file=sys.stderr)
                data = b'\x00' * self.CHUNK 

            if voice_status_threshold:
                # If we just transitioned to True, feed the pre-buffer
                if not prev_voice_status_threshold and self.pre_buffer:
                    for chunk in self.pre_buffer:
                        self.recognizer.AcceptWaveform(chunk)
                    self.pre_buffer.clear()

                if not self.PAUSE:
                    if self.recognizer.AcceptWaveform(data):
                        result_json = json.loads(self.recognizer.Result())
                        text = result_json.get('text', '')
                        if text:
                            self.callback(text, None)
                        self.recognizer.Reset()
                    else:
                        partial_json = json.loads(self.recognizer.PartialResult())
                        partial = partial_json.get('partial', '')
                        self.callback(None, partial)
                else:
                    self.recognizer.Reset()
            else:
                # Buffer the last N chunks before voice activation
                if not self.PAUSE:
                    self.pre_buffer.append(data)
                    if len(self.pre_buffer) > self.pre_buffer_maxlen:
                        self.pre_buffer.pop(0)
                        
            prev_voice_status_threshold = voice_status_threshold

    def _run_without_voice_gate(self):
        """Process audio without voice activity detection."""
        while self.running:
            try:
                data = self.audio_source.read(self.CHUNK)
            except OSError as e:
                print(f"Audio input overflow: {e}", file=sys.stderr)
                data = b'\x00' * self.CHUNK 

            if not self.PAUSE:
                if self.recognizer.AcceptWaveform(data):
                    result_json = json.loads(self.recognizer.Result())
                    text = result_json.get('text', '')
                    if text:
                        self.callback(text, None)
                    self.recognizer.Reset()
                else:
                    partial_json = json.loads(self.recognizer.PartialResult())
                    partial = partial_json.get('partial', '')
                    self.callback(None, partial)
            else:
                self.recognizer.Reset()
    
    def pause(self):
        self.PAUSE = True
        print(f"Recognizer paused, Time: {time.time()}", file=sys.stderr)

    def resume(self):
        self.PAUSE = False
        print(f"Recognizer resumed, Time: {time.time()}", file=sys.stderr)

    def stop(self):
        self.running = False
        print("Recognizer stopped.", file=sys.stderr)


class WhisperRecognizer:
    """Speech recognition using faster-whisper (CTranslate2).

    Unlike Vosk, Whisper is not a streaming recognizer: it transcribes a full utterance at once.
    This class uses the same WebRTC VAD helper as the microphone modules to detect when speech
    starts/stops, buffers audio while the user is speaking, and transcribes once a short silence
    follows. Each instance loads its own model, so a future version that runs one worker per GPU
    (via device_index) can simply start multiple SpeechToTextWorker subprocesses side by side.
    """

    def __init__(
        self,
        audio_source,
        callback=None,
        rate=RATE,
        chunk=CHUNK,
        modelName="small",
        device="auto",
        compute_type="auto",
        language="auto",
        device_index=0,
        silence_hangover=0.6,
    ):
        self.callback = callback or self.default_callback
        self.audio_source = audio_source
        self.RATE = rate
        self.CHUNK = chunk
        self.running = False
        self.PAUSE = False
        self.silence_hangover = silence_hangover
        self.language = None if str(language).lower() in ("auto", "", "none") else str(language)
        resolved_device = device if device != "auto" else ("cuda" if _cuda_available() else "cpu")
        resolved_compute_type = compute_type
        if resolved_compute_type == "auto":
            resolved_compute_type = "float16" if resolved_device == "cuda" else "int8"

        self.model = None
        print(
            f"Loading Whisper model '{modelName}' (device={resolved_device}, index={device_index}, "
            f"compute_type={resolved_compute_type})...",
            file=sys.stderr,
        )
        try:
            self.model = WhisperModel(
                modelName,
                device=resolved_device,
                device_index=device_index,
                compute_type=resolved_compute_type,
            )
            print(f"✅ Whisper recognizer initialized with {modelName}", file=sys.stderr)
        except Exception as e:
            if device != "auto" or resolved_device != "cuda":
                print(f"❌ Error initializing Whisper recognizer: {e}", file=sys.stderr)
                raise
            print(f"CUDA initialization failed ({e}); falling back to CPU/int8.", file=sys.stderr)
            self.model = WhisperModel(modelName, device="cpu", compute_type="int8")
            print(f"Whisper recognizer initialized with {modelName} on CPU", file=sys.stderr)

        self.vad = VAD(aggressiveness=2, sampling_rate=rate, frame_duration_ms=30)
        self._speech_buffer = bytearray()
        self._silence_since = None
        self._speaking = False

    def default_callback(self, text, partial):
        if text:
            print(f"Final Text: {text}", file=sys.stderr)

    def _transcribe_buffer(self):
        buffered, self._speech_buffer = self._speech_buffer, bytearray()
        if not self.model or not buffered:
            return
        audio_np = np.frombuffer(bytes(buffered), dtype=np.int16).astype(np.float32) / 32768.0
        try:
            segments, _info = self.model.transcribe(audio_np, language=self.language, beam_size=1, vad_filter=False)
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as e:
            print(f"Whisper transcription error: {e}", file=sys.stderr)
            text = ""
        if text:
            self.callback(text, None)

    def run(self):
        self.running = True
        print("\nSpeak now (Whisper)...", file=sys.stderr)

        while self.running:
            try:
                data = self.audio_source.read(self.CHUNK)
            except OSError as e:
                print(f"Audio input overflow: {e}", file=sys.stderr)
                data = b'\x00' * self.CHUNK

            if self.PAUSE:
                self._speech_buffer = bytearray()
                self._speaking = False
                self._silence_since = None
                continue

            is_speech = self.vad.process(data)
            now = time.time()
            if is_speech:
                self._speech_buffer.extend(data)
                self._silence_since = None
                self._speaking = True
            elif self._speaking:
                if self._silence_since is None:
                    self._silence_since = now
                elif now - self._silence_since >= self.silence_hangover:
                    self._transcribe_buffer()
                    self._speaking = False
                    self._silence_since = None

    def pause(self):
        self.PAUSE = True
        print(f"Recognizer paused, Time: {time.time()}", file=sys.stderr)

    def resume(self):
        self.PAUSE = False
        print(f"Recognizer resumed, Time: {time.time()}", file=sys.stderr)

    def stop(self):
        self.running = False
        print("Recognizer stopped.", file=sys.stderr)

# Helper function to detect sound levels
def detect_sound(audio_chunk, threshold=THRESHOLD):
    """Return True if audio chunk above volume threshold."""
    # Decode byte data to int16
    try:
        audio_chunk = np.frombuffer(audio_chunk, dtype=np.int16)
    except ValueError as e:
        print(f"Error decoding audio chunk: {e}", file=sys.stderr)
        return False
    # Compute the volume
    volume = np.abs(audio_chunk).max()  # Use absolute to handle both +ve and -ve peaks
    return volume > threshold

# Communication functions
def send_message(name, string, direction=None):
    msg = {f"{name}": f"{string}"}
    if direction is not None:
        msg["direction"] = direction
    print(json.dumps(msg))
    sys.stdout.flush()

def STTCallBack(text, partial):
    direction = None
    # Try to get DoA if mic has get_doa or get_direction
    if (
        hasattr(mic, "speaker")
        and hasattr(mic.speaker, "is_voice_active")
        and callable(getattr(mic.speaker, "is_voice_active", None))
        and mic.speaker.is_voice_active()
        and hasattr(mic.speaker, "get_doa")
        and callable(getattr(mic.speaker, "get_doa", None))
    ):
        try:
            direction = mic.speaker.get_doa()
        except Exception:
            direction = None
    if text:
        print(f"Final Text: {text}", file=sys.stderr)
        send_message("confirmedText", text, direction)
    if partial:
        # print(f"Partial Text: {partial}", file=sys.stderr)
        send_message("interimResult", partial, direction)

def pauseSpeechToText():
    global _recognizer
    global _recognizer_ready
    _recognizer_ready.wait()  # Block until recognizer is ready
    if _recognizer is None:
        print("Recognizer is not initialized!", file=sys.stderr)
        return
    print("Pausing Speech to Text", file=sys.stderr)
    try:
        _recognizer.pause()
    except Exception as e:
        print(f"Error pausing recognizer: {e}", file=sys.stderr)
    return   

def resumeSpeechToText(): 
    global _recognizer 
    global _recognizer_ready 
    _recognizer_ready.wait()  # Block until recognizer is ready
    if _recognizer is None:
        print("Recognizer is not initialized!", file=sys.stderr)
        return
    print("Resuming Speech to Text", file=sys.stderr)
    try:
        _recognizer.resume()
    except Exception as e:
        print(f"Error resuming recognizer: {e}", file=sys.stderr)
    return   

def setUpSpeechToText():
    global _recognizer
    global _recognizer_ready
    global mic
    
    # Initialize microphone
    mic = MicrophoneStream(rate=RATE, chunk=CHUNK)

    # Initialize recognizer
    if stt_backend == "whisper":
        _recognizer = WhisperRecognizer(
            audio_source=mic,
            callback=STTCallBack,
            rate=RATE,
            chunk=CHUNK,
            modelName=str(current_model),
            device=whisper_device,
            compute_type=whisper_compute_type,
            language=whisper_language,
            device_index=whisper_device_index,
        )
    else:
        _recognizer = SpeechRecognizer(audio_source=mic, size="medium", callback=STTCallBack, rate=RATE, chunk=CHUNK)
    _recognizer_ready.set()
    threading.Thread(target=_recognizer.run, daemon=True).start()

def stdin_listener():
    for line in sys.stdin:
        print("received data in python", file=sys.stderr)
        try:
            data = json.loads(line)
            if data.get("STT") == "pause":
                pauseSpeechToText()
            elif data.get("STT") == "resume":
                resumeSpeechToText()
            elif data.get("STT") == "send_message":
                send_message(data.get("name", ""), data.get("message", ""))
            else:
                sys.stdout.flush()
            print(data, file=sys.stderr)
            sys.stdout.flush()
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            sys.stdout.flush()         

def parse_arguments():
    parser = argparse.ArgumentParser(description='Speech to Text Service')
    parser.add_argument('--backend', default='vosk', choices=['vosk', 'whisper'],
                       help='STT backend to use')
    parser.add_argument('--model', default=0,
                       help='Initial STT model to use (int: 0-3 for presets, or string: model name)')
    parser.add_argument('--device', default='auto', choices=['auto', 'cpu', 'cuda'],
                       help='Whisper only: compute device')
    parser.add_argument('--compute-type', default='auto', choices=['auto', 'int8', 'float16', 'float32'],
                       help='Whisper only: CTranslate2 compute type')
    parser.add_argument('--device-index', type=int, default=0,
                       help='Whisper only: GPU index to use when device resolves to cuda')
    parser.add_argument('--language', default='auto',
                       help='Whisper only: force a language (e.g. "en", "de") or "auto" to detect')
    # Convert to int if it's a digit string, otherwise keep as string
    args = parser.parse_args()
    try:
        args.model = int(args.model)
    except ValueError:
        args.model = str(args.model)
    return args


def main():
    try:
        global current_model
        global stt_backend
        global whisper_device, whisper_compute_type, whisper_device_index, whisper_language
        args = parse_arguments()
        stt_backend = args.backend
        current_model = args.model
        whisper_device = args.device
        whisper_compute_type = args.compute_type
        whisper_device_index = args.device_index
        whisper_language = args.language

        if stt_backend == 'whisper' and not whisper_available:
            print("❌ faster-whisper is required for the whisper backend but is not installed. "
                  "Install with: pip install faster-whisper. Falling back to Vosk.", file=sys.stderr)
            stt_backend = 'vosk'

        print("\n🎤 Current STT Model:", current_model, file=sys.stderr)
        
        # Vosk is only required when it's the active backend.
        if stt_backend == 'vosk' and not vosk_available:
            print("❌ Vosk is required but not installed. Please install with: pip install vosk", file=sys.stderr)
            send_message("error", "Vosk is not installed", None)
            return
        
        # Create models directory if it doesn't exist
        os.makedirs(MODEL_PATH, exist_ok=True)
        
        # Initialize STT
        setUpSpeechToText()
        
        # Start listening for commands
        stdin_listener()
        
    except KeyboardInterrupt:
        print("\nTerminating the program.", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"Error in main function: {e}", file=sys.stderr)
        send_message("error", str(e), None)
        sys.exit(1)

if __name__ == "__main__":
    main()
    try:
        stdin_listener()
    except KeyboardInterrupt:
        print("Interrupted by user. Exiting cleanly.")
    finally:
        # Clean up resources
        if mic:
            mic.close()
        print("Resources released.")