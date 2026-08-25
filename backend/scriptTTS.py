import sys
import json
import numpy as np
import sounddevice as sd
from piper.voice import PiperVoice
import threading
import queue
import time  # Add this at the top with other imports
import os
from model_downloader import download_piper_voice

# Global variables
CYAN = '\033[96m'
RESET = '\033[0m'
tts_volume = 100
MODEL_PATH = "TTSmodels/"
TTS_MODELS = {
    "en_GB-cori-high.onnx": {
        "model": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/cori/high/en_GB-cori-high.onnx",
        "config": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/cori/high/en_GB-cori-high.onnx.json"
    },
    "en_GB-alan-medium.onnx": {
        "model": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/medium/en_GB-alan-medium.onnx",
        "config": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json"
    },
     "en_GB-alan-low.onnx": {
        "model": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/low/en_GB-alan-low.onnx",
        "config": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/low/en_GB-alan-low.onnx.json"
    },
    "en_US-lessac-medium.onnx": {
        "model": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        "config": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
    },
    "de_DE-thorsten-medium.onnx": {
        "model": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx",
        "config": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json"
    }
   
}

MODEL_NAMES = list(TTS_MODELS.keys())
playback_thread = None
stop_event = threading.Event()
pause_event = threading.Event()
voice_cache = {}
output_device = None

def send_message(name, string):
    """Send JSON message to stdout"""
    msg = {f"{name}": f"{string}"}
    print(json.dumps(msg))
    sys.stdout.flush()

def get_voice(model_name):
    """Get voice model, downloading if necessary"""
    model_path = os.path.join(MODEL_PATH, model_name)
    
    if model_path not in voice_cache:
        # Download if missing
        if not (os.path.exists(model_path) and os.path.exists(model_path + ".json")):
            model_base = os.path.splitext(os.path.basename(model_name))[0]
            success = download_piper_voice(model_base, MODEL_PATH, 
                                         model_url=TTS_MODELS[model_name]['model'],
                                         config_url=TTS_MODELS[model_name]['config'])
            if not success:
                raise FileNotFoundError(f"Failed to download voice model: {model_base}")
        
        # Load model
        try:
            voice_cache[model_path] = PiperVoice.load(model_path)
        except FileNotFoundError as e:
            print(f"TTS Voice loading error: {e}", file=sys.stderr)
            send_message("tts", f"error: Missing voice data files. Please reinstall piper-tts: pip install piper-tts --force-reinstall")
            raise
    
    return voice_cache[model_path]

def extract_audio_from_chunk(audio_chunk):
    """Extract audio bytes from AudioChunk object"""
    # Try audio_float_array first (most reliable)
    if hasattr(audio_chunk, 'audio_float_array') and audio_chunk.audio_float_array is not None:
        float_array = audio_chunk.audio_float_array
        if float_array.dtype != np.float32:
            float_array = float_array.astype(np.float32)
        int16_array = (float_array * 32767).astype(np.int16)
        return int16_array.tobytes()
    
    # Fallback to other attributes
    for attr in ['_audio_int16_bytes', '_audio_int16_array']:
        if hasattr(audio_chunk, attr):
            value = getattr(audio_chunk, attr)
            if value is not None:
                return value.tobytes() if hasattr(value, 'tobytes') else value
    
    return None

def get_supported_sample_rate(device=None):
    """Find a supported sample rate for the device"""
    # Common sample rates to try, in order of preference
    rates_to_try = [22050, 16000, 44100, 48000, 8000]
    
    for rate in rates_to_try:
        try:
            # Test if this rate works
            sd.check_output_settings(device=device, samplerate=rate, channels=1, dtype='int16')
            print(f"Using sample rate: {rate}Hz", file=sys.stderr)
            return rate
        except Exception as e:
            print(f"Sample rate {rate}Hz not supported: {e}", file=sys.stderr)
            continue
    
    # If nothing works, try the default
    print("No specific sample rate worked, trying default", file=sys.stderr)
    return None

import queue
import threading

def play_stream(voice, text, stop_event, pause_event, device=None):
    """Play TTS audio stream"""
    try:
        send_message("tts", "started")
        synthesis_start = time.time()  # Start timing
        
        # Find a supported sample rate
        target_sample_rate = get_supported_sample_rate(device)
        
        # Set buffer size for lower latency
        buffer_size = 1024  # Smaller buffer = less latency but more CPU
        
        if target_sample_rate is None:
            stream = sd.OutputStream(channels=1, dtype='int16', device=device, blocksize=buffer_size,latency='low',)
            target_sample_rate = stream.samplerate
        else:
            stream = sd.OutputStream(
                    samplerate=target_sample_rate, 
                    channels=1,
                    dtype='int16',
                    device=device,
                    blocksize=buffer_size,
                    latency='low',
            )
        
        # Get original sample rate for resampling
        original_rate = voice.config.sample_rate
        actual_target_rate = stream.samplerate
        
        # Calculate resampling ratio
        needs_resampling = original_rate != actual_target_rate
        ratio = actual_target_rate / original_rate if needs_resampling else 1.0
        
        if needs_resampling:
            print(f"Will resample from {original_rate}Hz to {actual_target_rate}Hz", file=sys.stderr)
        
        # Helper function to process and play a chunk
        def process_chunk(chunk_data, force_split=False):
            if not chunk_data:
                return
                
            int_data = np.frombuffer(chunk_data, dtype=np.int16)
            
            # Force split large chunks into smaller pieces for more responsive playback
            # This helps when Piper generates the entire audio in one large chunk
            if force_split and len(int_data) > 8000:  # ~0.5 seconds at 16kHz
                print(f"Splitting large chunk ({len(int_data)} samples) for faster playback start", file=sys.stderr)
                
                # Use a smaller first chunk for immediate playback
                first_chunk_size = 1600  # ~0.1 seconds - just enough for immediate feedback
                first_sub = int_data[:first_chunk_size].copy()  # Take a small first piece
                
                # Skip intensive processing for first chunk to get audio started ASAP
                # Apply only minimal volume scaling if needed
                if tts_volume != 100:
                    # Simple and fast volume adjustment
                    first_sub = (first_sub * (tts_volume / 100.0)).astype(np.int16)
                
                # Play first sub-chunk immediately without resampling
                if len(first_sub) > 0 and not stop_event.is_set():
                    print("About to write first sub-chunk", file=sys.stderr)
                    stream.write(first_sub)
                    print(f"First sub-chunk of {len(first_sub)} samples playing immediately", file=sys.stderr)
                
                # Process the rest of the audio in larger chunks
                chunk_size = 8000  # Larger chunks for the rest of the audio
                
                # Start from where the first sub-chunk ended
                remaining_data = int_data[first_chunk_size:]
                
                # Process remaining audio in chunks without creating a full list up front
                # This avoids memory spikes and is more efficient
                for i in range(0, len(remaining_data), chunk_size):
                    if stop_event.is_set():
                        break
                    
                    end_idx = min(i + chunk_size, len(remaining_data))
                    sub_chunk = remaining_data[i:end_idx]
                    
                    if needs_resampling and len(sub_chunk) > 0:
                        # More efficient resampling for large chunks
                        old_indices = np.arange(len(sub_chunk))
                        new_length = int(len(sub_chunk) * ratio)
                        if new_length > 0:
                            new_indices = np.linspace(0, len(sub_chunk) - 1, new_length)
                            sub_chunk = np.interp(new_indices, old_indices, sub_chunk).astype(np.int16)
                    
                    # Apply volume scaling
                    if tts_volume != 100:
                        float_data = sub_chunk.astype(np.float32)
                        float_data = float_data * (tts_volume / 100.0)
                        sub_chunk = np.clip(float_data, -32768, 32767).astype(np.int16)
                    
                    # Play this sub-chunk
                    if len(sub_chunk) > 0 and not stop_event.is_set():
                        # Handle pause/resume
                        while pause_event.is_set() and not stop_event.is_set():
                            sd.sleep(100)
                        
                        if not stop_event.is_set():
                            stream.write(sub_chunk)
                
                return  # We've handled the chunk, so return
            
            # Process normal-sized chunks as before
            # Resample if needed
            if needs_resampling and len(int_data) > 0:
                old_indices = np.arange(len(int_data))
                new_length = int(len(int_data) * ratio)
                if new_length > 0:
                    new_indices = np.linspace(0, len(int_data) - 1, new_length)
                    int_data = np.interp(new_indices, old_indices, int_data).astype(np.int16)
            
            # Apply volume scaling
            if tts_volume != 100:
                float_data = int_data.astype(np.float32)
                float_data = float_data * (tts_volume / 100.0)
                int_data = np.clip(float_data, -32768, 32767).astype(np.int16)
            
            # Play this chunk
            if len(int_data) > 0 and not stop_event.is_set():
                # Handle pause/resume
                while pause_event.is_set() and not stop_event.is_set():
                    sd.sleep(100)
                
                if not stop_event.is_set():
                    stream.write(int_data)
        
        # Start the stream
        stream.start()
        print(f"Audio stream started with {stream.samplerate}Hz", file=sys.stderr)
        
        try:
            # Create a generator but don't start full synthesis yet
            print("Trying generator synthesis...", file=sys.stderr)
            
            # Process just the first chunk to get audio started ASAP
            first_chunk_start = time.time()
            try:
                # Start the generator
                synthesis_gen = voice.synthesize(text)
                
                # Get first chunk and process it immediately
                first_chunk = next(synthesis_gen)
                first_chunk_time = time.time() - first_chunk_start
                print(f"First chunk generated in {CYAN}{first_chunk_time:.2f}{RESET} seconds", file=sys.stderr)
                
                # Start measuring time for processing
                process_start = time.time()
                
                # Extract and immediately start processing the first chunk
                first_chunk_data = extract_audio_from_chunk(first_chunk)
                
                # Force split the first chunk since it likely contains all audio on Pi
                print(f"Starting audio playback...", file=sys.stderr)
                process_chunk(first_chunk_data, force_split=True)
                
                process_time = time.time() - process_start
                print(f"First chunk processed and playing in {CYAN}{process_time:.2f}{RESET} seconds", file=sys.stderr)
                
                # Now process the rest of the chunks in the background
                rest_start = time.time()
                for audio_chunk in synthesis_gen:
                    if stop_event.is_set():
                        break
                    
                    chunk_data = extract_audio_from_chunk(audio_chunk)
                    process_chunk(chunk_data)
                
                rest_time = time.time() - rest_start
                total_time = time.time() - synthesis_start
                print(f"Remaining chunks processed in {CYAN}{rest_time:.2f}{RESET} seconds", file=sys.stderr)
                print(f"Total TTS pipeline: {CYAN}{total_time:.2f}{RESET} seconds", file=sys.stderr)
                
            except StopIteration:
                # Handle case with only one chunk
                print("Synthesis produced only one audio chunk", file=sys.stderr)
                
        except TypeError as e:
            # Fallback to other method if needed
            if "missing 1 required positional argument: 'wav_file'" in str(e):
                print("Falling back to generator with wav_file=None...", file=sys.stderr)
                
                # Similar approach with the alternate method but optimized for first chunk
                first_chunk_start = time.time()
                try:
                    # Start the generator with wav_file=None
                    synthesis_gen = voice.synthesize(text, wav_file=None)
                    
                    # Get and process first chunk immediately
                    first_chunk = next(synthesis_gen)
                    first_chunk_time = time.time() - first_chunk_start
                    print(f"First chunk generated in {CYAN}{first_chunk_time:.2f}{RESET} seconds", file=sys.stderr)
                    
                    process_start = time.time()
                    
                    # Extract and immediately start processing the first chunk
                    first_chunk_data = extract_audio_from_chunk(first_chunk)
                    
                    # Force split the first chunk for faster playback start
                    print(f"Starting audio playback (fallback method)...", file=sys.stderr)
                    process_chunk(first_chunk_data, force_split=True)
                    
                    process_time = time.time() - process_start
                    print(f"First chunk processed and playing in {CYAN}{process_time:.2f}{RESET} seconds", file=sys.stderr)
                    
                    # Process remaining chunks
                    rest_start = time.time()
                    for audio_chunk in synthesis_gen:
                        if stop_event.is_set():
                            break
                        
                        chunk_data = extract_audio_from_chunk(audio_chunk)
                        process_chunk(chunk_data)
                    
                    rest_time = time.time() - rest_start
                    total_time = time.time() - synthesis_start
                    print(f"Remaining chunks processed in {CYAN}{rest_time:.2f}{RESET} seconds", file=sys.stderr)
                    print(f"Total TTS pipeline: {CYAN}{total_time:.2f}{RESET} seconds", file=sys.stderr)
                    
                except StopIteration:
                    print("Fallback synthesis produced only one audio chunk", file=sys.stderr)
            else:
                raise

        
        # Clean up
        stream.stop()
        stream.close()
        sd.sleep(600)  # Small delay to ensure audio is fully played
        send_message("tts", "stopped")
        
    except Exception as e:
        print(f"TTS playback error: {e}", file=sys.stderr)
        send_message("tts", f"error: {e}")

def set_tts_volume(volume_level):
    """Set TTS volume (0-100)"""
    global tts_volume
    if 0 <= volume_level <= 100:
        tts_volume = volume_level
        print(f"TTS output volume set to {tts_volume}%", file=sys.stderr)
        return tts_volume
    return None

def handle_command(cmd):
    """Handle TTS commands"""
    global pause_event, tts_volume
    
    if isinstance(cmd, str):
        if cmd == "pause":
            pause_event.set()
        elif cmd == "resume":
            pause_event.clear()
            send_message("tts", "resumed")
    elif isinstance(cmd, dict):
        if "volume" in cmd:
            try:
                result = set_tts_volume(int(cmd["volume"]))
                if result is not None:
                    send_message("volume", str(result))
            except ValueError:
                pass
        elif "volume_change" in cmd:
            try:
                change = int(cmd["volume_change"])
                new_volume = max(0, min(100, tts_volume + change))
                result = set_tts_volume(new_volume)
                if result is not None:
                    send_message("volume", str(result))
            except ValueError:
                pass
        elif "volume_get" in cmd:
            send_message("volume", str(tts_volume))

def find_respeaker_device():
    """Find ReSpeaker audio device"""
    try:
        devices = sd.query_devices()
        for i, device in enumerate(devices):
            device_name = str(device['name']).lower()
            if 'respeaker' in device_name and device['max_output_channels'] > 0:
                print(f"Found ReSpeaker output device: {device['name']}", file=sys.stderr)
                return i
        print("No ReSpeaker output device found, using default", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error finding audio devices: {e}", file=sys.stderr)
        return None

def resolve_tts_model_name(model_value):
    """Resolve model name from either string model id or legacy numeric index."""
    if isinstance(model_value, int):
        return MODEL_NAMES[model_value] if 0 <= model_value < len(MODEL_NAMES) else None

    if isinstance(model_value, str):
        candidate = model_value.strip()
        if not candidate:
            return None

        # Legacy index as string
        if candidate.isdigit():
            idx = int(candidate)
            return MODEL_NAMES[idx] if 0 <= idx < len(MODEL_NAMES) else None

        if candidate in TTS_MODELS:
            return candidate

        if not candidate.endswith('.onnx') and f"{candidate}.onnx" in TTS_MODELS:
            return f"{candidate}.onnx"

        return None

    return None

def main():
    """Main TTS loop"""
    global playback_thread, stop_event, pause_event, output_device
    
    output_device = find_respeaker_device()
    print("Ready for text input...", file=sys.stderr)
    
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
            
        try:
            # Parse JSON or use raw text
            try:
                msg = json.loads(line)
            except:
                msg = {}
            
            # Handle commands
            if isinstance(msg, dict):
                if "tts" in msg:
                    handle_command(msg["tts"])
                    continue
                if any(key in msg for key in ["volume", "volume_change", "volume_get"]):
                    handle_command(msg)
                    continue
            
            # Handle TTS request
            text = msg.get("text", "") if isinstance(msg, dict) else line
            raw_model = msg.get("model", MODEL_NAMES[0]) if isinstance(msg, dict) else MODEL_NAMES[0]
            model_name = resolve_tts_model_name(raw_model)

            if not text or not model_name:
                continue

            try:
                voice = get_voice(model_name)
            except (FileNotFoundError, Exception) as e:
                print(f"Failed to load voice model {model_name}: {e}", file=sys.stderr)
                send_message("tts", f"error: Could not load voice model. Try: pip install piper-tts --force-reinstall")
                continue
            
            print(f"Synthesizing: {text} (model: {model_name})", file=sys.stderr)
            
            # Stop current playback and start new one
            if playback_thread and playback_thread.is_alive():
                stop_event.set()
                playback_thread.join()
            
            stop_event = threading.Event()
            pause_event.clear()
            playback_thread = threading.Thread(
                target=play_stream, 
                args=(voice, text, stop_event, pause_event, output_device)
            )
            playback_thread.start()
            
            
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            send_message("tts", f"error: {e}")

if __name__ == "__main__":
    main()