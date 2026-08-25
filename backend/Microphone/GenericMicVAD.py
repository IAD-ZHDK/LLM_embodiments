import os
import sys
import time
import numpy as np
import pyaudio

from Microphone.vad_utils import VAD

class GenericMicVAD:
    """Generic microphone class with Voice Activity Detection."""

    def __init__(self, rate=16000, chunk=1024, format=pyaudio.paInt16, device_index=None, verbose=True):
        self.verbose = verbose
        self.rate = rate
        self.chunk = chunk
        self.format = format
        self.device_index = device_index
        self.stream = None
        self.p = pyaudio.PyAudio()
        
        # Find an appropriate device if not specified
        if self.device_index is None:
            self.device_index = self._find_suitable_device()
        
        # VAD-related attributes
        self.vad_enabled = True
        self.audio_buffer = b''  # Store the most recent audio data
        self.frame_duration_ms = int((self.chunk / self.rate) * 1000)  # Convert chunk size to milliseconds
        self.vad = VAD(aggressiveness=3, sampling_rate=rate, frame_duration_ms=self.frame_duration_ms, energy_threshold=200)
        
        # Print device info
        if self.verbose:
            device_info = self.p.get_device_info_by_index(self.device_index)
            print(f"Using audio device: {device_info['name']} (index {self.device_index})", file=sys.stderr)
            print(f"Sample rate: {self.rate}, Chunk size: {self.chunk}", file=sys.stderr)
            
        # Print VAD status
        if self.vad.enabled:
            print(" VAD initialized successfully", file=sys.stderr)
        else:
            print("Warning:  VAD failed to initialize. Voice detection will be disabled.", file=sys.stderr)
            self.vad_enabled = False

    def _find_suitable_device(self):
        """Find a suitable input device."""
        default_input = self.p.get_default_input_device_info()['index']
        
        if self.verbose:
            print("Available audio devices:", file=sys.stderr)
            for i in range(self.p.get_device_count()):
                device_info = self.p.get_device_info_by_index(i)
                if device_info['maxInputChannels'] > 0:  # Only show input devices
                    print(f"{i}: {device_info['name']} (channels: {device_info['maxInputChannels']})", file=sys.stderr)
        
        return default_input

    def open_stream(self):
        """Open the audio input stream."""
        if self.stream is None:
            try:
                device_info = self.p.get_device_info_by_index(self.device_index)
                channels = min(1, int(device_info['maxInputChannels']))  # Use mono
                
                self.stream = self.p.open(
                    format=self.format,
                    channels=channels,
                    rate=self.rate,
                    input=True,
                    frames_per_buffer=self.chunk,
                    input_device_index=self.device_index
                )
                
                if self.verbose:
                    print(f"Audio stream opened successfully with {channels} channels", file=sys.stderr)
                    
            except Exception as e:
                if self.verbose:
                    print(f"Error opening audio stream: {e}", file=sys.stderr)
                raise
        
        return self.stream

    def read(self, chunk=None):
        """Read audio data from the stream."""
        if chunk is None:
            chunk = self.chunk
            
        try:
            data = self.stream.read(chunk, exception_on_overflow=False)
            
            # Store the audio data for VAD
            self.audio_buffer = data
            
            return data
            
        except Exception as e:
            if self.verbose:
                print(f"Error reading audio: {e}", file=sys.stderr)
            return b'\x00' * chunk * 2  # Return silence on error

    def is_voice_active(self):
        """Check if voice activity is detected."""
        if not self.vad_enabled or not self.vad.enabled:
            return True  # Default to assuming voice is active if VAD is disabled
            
        # Process the most recent audio data with VAD
        if len(self.audio_buffer) > 0:
            return self.vad.process(self.audio_buffer)
        return False

    def set_vad_threshold(self, threshold):
        """Set the VAD threshold."""
        if hasattr(self.vad, 'adjust_threshold'):
            return self.vad.adjust_threshold(threshold)

    def close_stream(self):
        """Close the audio stream."""
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

    def terminate(self):
        """Clean up resources."""
        self.close_stream()
        if self.p is not None:
            self.p.terminate()
            self.p = None

    # Method aliases to match ReSpeaker4MicArray interface
    def get_doa(self):
        """Direction of Arrival - not supported but included for compatibility."""
        return None

    # Additional useful methods
    def get_input_level(self, audio_data=None):
        """Get current audio input level (RMS) in dB."""
        if audio_data is None:
            audio_data = self.read()
            
        audio_np = np.frombuffer(audio_data, dtype=np.int16)
        if len(audio_np) == 0:
            return -120.0  # Silent
            
        rms = np.sqrt(np.mean(np.square(audio_np.astype(np.float32))))
        if rms > 0:
            db = 20 * np.log10(rms / 32768.0)
            return db
        else:
            return -120.0