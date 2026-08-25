import numpy as np
import collections
import time
import os
import sys
import importlib.util

class VAD:
    """Voice Activity Detection using Google's WebRTC VAD"""
    
    def __init__(self, 
                aggressiveness=3, 
                sampling_rate=16000,
                frame_duration_ms=30,
                energy_threshold=500):
        """
        Initialize WebRTC VAD.
        
        Args:
            aggressiveness: Integer between 0 and 3. 0 is least aggressive about filtering
                out non-speech, 3 is most aggressive.
            sampling_rate: Audio sample rate in Hz (8000, 16000, 32000, 48000)
            frame_duration_ms: Duration of each frame in ms (10, 20, or 30)
        """
        self.aggressiveness = aggressiveness
        self.sampling_rate = sampling_rate
        self.frame_duration_ms = frame_duration_ms
        self.vad = None
        self.enabled = False
        self.is_speech = False
        self.last_speech_time = None
        self.smoothing = True  # Enable smoothing to prevent choppy detection
        
        # Frame history for smoothing
        self.frame_history = collections.deque(maxlen=12)  # Increased from 8 to 12
        self.positive_frames_threshold = 4  # Increased from 2 to 4 - need more positive frames
        
        # Energy threshold to filter out low energy sounds
        self.energy_threshold = energy_threshold  # Adjust based on your environment
        
        # Sustained detection requirements
        self.min_speech_frames = 3  # Need this many consecutive frames of speech
        self.consecutive_speech_frames = 0
        
        # Try to load the WebRTC VAD module
        self._load_webrtc_vad()
    
    def _load_webrtc_vad(self):
        """Try to load WebRTC VAD module"""
        try:
            # Check if webrtcvad is available
            if importlib.util.find_spec("webrtcvad") is None:
                print("WebRTC VAD not available. Install with: pip install webrtcvad", file=sys.stderr)
                self.enabled = False
                return
                
            import webrtcvad
            
            # Initialize VAD with maximum aggressiveness
            self.vad = webrtcvad.Vad(self.aggressiveness)
            
            # Validate sample rate
            valid_rates = [8000, 16000, 32000, 48000]
            if self.sampling_rate not in valid_rates:
                closest_rate = min(valid_rates, key=lambda x: abs(x - self.sampling_rate))
                print(f"Warning: {self.sampling_rate} Hz is not supported by WebRTC VAD. Using {closest_rate} Hz instead.", file=sys.stderr)
                self.sampling_rate = closest_rate
            
            # Validate frame duration
            valid_durations = [10, 20, 30]
            if self.frame_duration_ms not in valid_durations:
                closest_duration = min(valid_durations, key=lambda x: abs(x - self.frame_duration_ms))
                print(f"Warning: {self.frame_duration_ms}ms frames not supported by WebRTC VAD. Using {closest_duration}ms instead.", file=sys.stderr)
                self.frame_duration_ms = closest_duration
            
            # Calculate bytes per frame
            self.frame_size = int(self.sampling_rate * self.frame_duration_ms / 1000)
            
            print(f"✅ WebRTC VAD initialized (aggressiveness={self.aggressiveness}, rate={self.sampling_rate}Hz, frame_duration={self.frame_duration_ms}ms)", file=sys.stderr)
            self.enabled = True
            
        except Exception as e:
            print(f"Error initializing WebRTC VAD: {e}", file=sys.stderr)
            self.enabled = False
    
    def process(self, audio_data):
        """
        Process audio data to detect speech.
        
        Args:
            audio_data: Audio data as bytes or numpy array
            
        Returns:
            bool: True if speech is detected, False otherwise
        """
        if not self.enabled or self.vad is None:
            return False
            
        try:
            # Convert audio_data to bytes if it's a numpy array
            if isinstance(audio_data, np.ndarray):
                if audio_data.dtype == np.int16:
                    audio_bytes = audio_data.tobytes()
                else:
                    # Convert to int16 first
                    audio_bytes = (audio_data * 32767).astype(np.int16).tobytes()
            elif isinstance(audio_data, bytes):
                audio_bytes = audio_data
            else:
                raise ValueError("Unsupported audio data format")
            
            # Process frames
            frames = self._frame_generator(audio_bytes)
            result = False
            frame_processed = False
            
            for frame in frames:
                frame_processed = True
                # Skip frames that aren't the right size
                if len(frame) != self.frame_size * 2:  # 2 bytes per sample for int16
                    continue
                
                # Calculate energy to filter out quiet sounds
                frame_np = np.frombuffer(frame, dtype=np.int16)
                energy = np.mean(np.abs(frame_np))
                
                # Skip low energy frames
                if energy < self.energy_threshold:
                    self.frame_history.append(False)
                    self.consecutive_speech_frames = 0
                    continue
                
                # Ask WebRTC VAD if this is speech
                is_speech = self.vad.is_speech(frame, self.sampling_rate)
                self.frame_history.append(is_speech)
                
                # Track consecutive speech frames to filter out short sounds like claps
                if is_speech:
                    self.consecutive_speech_frames += 1
                else:
                    self.consecutive_speech_frames = 0
                
                # Apply smoothing to avoid choppy detection
                speech_frames = sum(self.frame_history)
                
                # Require both enough positive frames in history AND some consecutive frames
                if speech_frames >= self.positive_frames_threshold and self.consecutive_speech_frames >= self.min_speech_frames:
                   # print("Speech detected", file=sys.stderr)
                    result = True
                    self.last_speech_time = time.time()
                elif self.smoothing and self.last_speech_time is not None:
                    # Only set to False after a delay to prevent choppy detection
                    if time.time() - self.last_speech_time > 0.3:  # 300ms delay
                        result = False
                        self.last_speech_time = None
            
            # No frames were processed, maintain current state
            if not frame_processed:
                return self.is_speech
                
            self.is_speech = result
            return result
            
        except Exception as e:
            print(f"Error in WebRTC VAD processing: {e}", file=sys.stderr)
            return False  # Return False on error
    
    def _frame_generator(self, audio_bytes):
        """Generate frames of the correct size for WebRTC VAD"""
        # Calculate required frame size in bytes (2 bytes per sample for int16)
        bytes_per_frame = self.frame_size * 2
        
        # Generate frames of the correct size
        offset = 0
        while offset + bytes_per_frame <= len(audio_bytes):
            yield audio_bytes[offset:offset + bytes_per_frame]
            offset += bytes_per_frame
    
    def reset(self):
        """Reset the VAD state"""
        self.frame_history.clear()
        self.is_speech = False
        self.last_speech_time = None
        self.consecutive_speech_frames = 0
    
    def adjust_threshold(self, new_threshold=None, new_aggressiveness=None):
        """
        Adjust the VAD settings
        
        Args:
            new_threshold: Energy threshold (higher = less sensitive)
            new_aggressiveness: WebRTC aggressiveness (0-3, higher = more aggressive filtering)
        """
        changes_made = False
        
        # Update energy threshold if provided
        if new_threshold is not None:
            self.energy_threshold = new_threshold
            changes_made = True
            print(f"Energy threshold set to {new_threshold}", file=sys.stderr)
            
        # Update aggressiveness if provided
        if new_aggressiveness is not None and 0 <= new_aggressiveness <= 3:
            self.aggressiveness = new_aggressiveness
            
            # Update VAD instance
            if self.enabled and self.vad is not None:
                import webrtcvad
                self.vad = webrtcvad.Vad(self.aggressiveness)
                changes_made = True
                print(f"Aggressiveness set to {new_aggressiveness}", file=sys.stderr)
                
        return changes_made

    def set_sensitivity(self, sensitivity):
        """
        Set overall sensitivity (0-100, where 0 is least sensitive, 100 is most sensitive)
        
        Args:
            sensitivity: Value from 0-100
        """
        # Clamp sensitivity to 0-100 range
        sensitivity = max(0, min(100, sensitivity))
        
        # Convert sensitivity to our parameters
        # Lower sensitivity = higher thresholds and aggressiveness
        
        # Energy threshold: 100-1000 range (0 sensitivity = 1000, 100 sensitivity = 100)
        energy = int(1000 - sensitivity * 9)
        
        # Positive frames: 2-8 range (0 sensitivity = 8, 100 sensitivity = 2)
        self.positive_frames_threshold = int(8 - (sensitivity / 100.0) * 6)
        
        # Minimum consecutive frames: 1-5 range (0 sensitivity = 5, 100 sensitivity = 1)
        self.min_speech_frames = int(5 - (sensitivity / 100.0) * 4)
        
        # Aggressiveness: 0-3 (0 sensitivity = 3, 100 sensitivity = 0)
        aggr = 3 - int((sensitivity / 100.0) * 3)
        
        # Apply the changes
        self.adjust_threshold(energy, aggr)
        
        print(f"VAD sensitivity set to {sensitivity}% (energy={energy}, frames={self.positive_frames_threshold}, consecutive={self.min_speech_frames}, aggressiveness={aggr})", file=sys.stderr)
        
        return True

#
