import pyaudio
import wave
import os
import sys
import numpy as np
import platform

def record_audio_sample(filename="test_recording.wav", seconds=5, rate=16000):
    """Record audio for specified number of seconds and save to file"""
    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1  # Mono recording for speech recognition
    
    print(f"Recording {seconds} seconds of audio using default device...", file=sys.stderr)
    
    p = pyaudio.PyAudio()
    
    # Open stream with default device (device_index=None)
    try:
        stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=rate,
                        input=True,
                        frames_per_buffer=CHUNK)
    except Exception as e:
        print(f"Error opening audio stream: {e}", file=sys.stderr)
        p.terminate()
        return None
    
    frames = []
    
    # Record audio
    for i in range(0, int(rate / CHUNK * seconds)):
        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)
            # Show progress
            if i % 10 == 0:
                sys.stderr.write('.')
                sys.stderr.flush()
        except Exception as e:
            print(f"\nError reading from stream: {e}", file=sys.stderr)
            break
    
    print("\nFinished recording!", file=sys.stderr)
    
    # Stop and close the stream
    stream.stop_stream()
    stream.close()
    p.terminate()
    
    if not frames:
        print("No audio data captured!", file=sys.stderr)
        return None
    
    # Save the recorded audio to a WAV file
    wf = wave.open(filename, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(rate)
    wf.writeframes(b''.join(frames))
    wf.close()
    
    print(f"Audio saved to {filename}", file=sys.stderr)
    return filename

def play_audio_file(filename):
    """Play back an audio file using platform-appropriate method"""
    if not os.path.exists(filename):
        print(f"File not found: {filename}", file=sys.stderr)
        return False
        
    system = platform.system()
    
    try:
        if system == "Darwin":  # macOS
            print(f"Playing {filename} using afplay...", file=sys.stderr)
            os.system(f"afplay {filename}")
        elif system == "Linux":  # Linux/Raspberry Pi
            print(f"Playing {filename} using aplay...", file=sys.stderr)
            os.system(f"aplay -q {filename}")
        elif system == "Windows":  # Windows
            print(f"Playing {filename} using PowerShell...", file=sys.stderr)
            os.system(f'powershell -c (New-Object Media.SoundPlayer "{filename}").PlaySync()')
        else:
            # Fall back to PyAudio for unknown systems
            print(f"Playing {filename} using PyAudio...", file=sys.stderr)
            play_with_pyaudio(filename)
            
        print("Playback complete!", file=sys.stderr)
        return True
    except Exception as e:
        print(f"Error with system playback: {e}", file=sys.stderr)
        # Try PyAudio as fallback
        return play_with_pyaudio(filename)

def play_with_pyaudio(filename):
    """Play audio file using PyAudio (works on all platforms)"""
    try:
        wf = wave.open(filename, 'rb')
        p = pyaudio.PyAudio()
        
        # Open stream with default output device
        stream = p.open(format=p.get_format_from_width(wf.getsampwidth()),
                        channels=wf.getnchannels(),
                        rate=wf.getframerate(),
                        output=True)
        
        # Read data in chunks and play
        chunk = 1024
        data = wf.readframes(chunk)
        
        while data:
            stream.write(data)
            data = wf.readframes(chunk)
        
        # Close everything
        stream.stop_stream()
        stream.close()
        p.terminate()
        
        print("PyAudio playback complete!", file=sys.stderr)
        return True
    except Exception as e:
        print(f"Error with PyAudio playback: {e}", file=sys.stderr)
        return False

def analyze_audio(filename):
    """Analyze audio file to check levels and quality"""
    if not filename or not os.path.exists(filename):
        print("No valid audio file to analyze", file=sys.stderr)
        return
        
    try:
        wf = wave.open(filename, 'rb')
        n_frames = wf.getnframes()
        sample_width = wf.getsampwidth()
        
        if sample_width == 2:  # 16-bit audio
            dtype = np.int16
            max_value = 32768.0
        elif sample_width == 4:  # 32-bit audio
            dtype = np.int32
            max_value = 2147483648.0
        else:
            print(f"Unsupported sample width: {sample_width}", file=sys.stderr)
            return
            
        data = np.frombuffer(wf.readframes(n_frames), dtype=dtype)
        wf.close()
        
        # Calculate stats
        abs_data = np.abs(data)
        max_amplitude = np.max(abs_data) / max_value * 100
        avg_amplitude = np.mean(abs_data) / max_value * 100
        
        print("\nAudio Analysis:", file=sys.stderr)
        print(f"Max amplitude: {max_amplitude:.1f}% (of maximum)", file=sys.stderr)
        print(f"Average amplitude: {avg_amplitude:.1f}% (of maximum)", file=sys.stderr)
        
        # Check if audio is too quiet
        if max_amplitude < 10:
            print("WARNING: Audio is very quiet. Microphone may not be working properly.", file=sys.stderr)
        elif max_amplitude > 90:
            print("WARNING: Audio may be clipping. Try speaking more softly.", file=sys.stderr)
        
    except Exception as e:
        print(f"Error analyzing audio: {e}", file=sys.stderr)

if __name__ == "__main__":
    # Show which platform we're running on
    print(f"Running on {platform.system()} {platform.release()}", file=sys.stderr)
    
    # List default devices
    p = pyaudio.PyAudio()
    default_input = p.get_default_input_device_info()
    default_output = p.get_default_output_device_info()
    print(f"Default input device: {default_input['name']} (index: {default_input['index']})", file=sys.stderr)
    print(f"Default output device: {default_output['name']} (index: {default_output['index']})", file=sys.stderr)
    p.terminate()
    
    # Record a 5-second audio sample
    filename = record_audio_sample(seconds=5)
    
    if filename:
        # Analyze the audio quality
        analyze_audio(filename)
        
        # Play back the recorded audio
        print("\nPlaying back recorded audio...", file=sys.stderr)
        play_audio_file(filename)
    
    print("\nAudio test complete!", file=sys.stderr)