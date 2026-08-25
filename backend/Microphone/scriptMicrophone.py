import pyaudio
import sounddevice as sd
import sys
from Microphone.seeedReSpeaker4mic import ReSpeaker4MicArray 
from Microphone.ReSpeakerLite import ReSpeakerLite 
from Microphone.GenericMicVAD import GenericMicVAD

class MicrophoneStream:
    def __init__(self, rate=16000, chunk=1024, format=pyaudio.paInt16):
        # List all available audio devices
        devices = sd.query_devices()
        print("Available audio devices:", file=sys.stderr)
        for i, device in enumerate(devices):
            print(f"{i}: {device['name']} (Input Channels: {device['max_input_channels']})", file=sys.stderr)    

        self.VAD_active = True
        self.speaker = False
        self.respeakerActive = False
        self.respeakerID = None

        # Try to find ReSpeaker device
        
        
        for i, device in enumerate(devices):
            if "ReSpeaker Lite" in device['name']:
                #get product ID
                print(device, file=sys.stderr) 
                print("ReSpeaker Lite device found", file=sys.stderr) 
                self.respeakerID = 0
                self.respeakerActive = True
                break
            elif "ReSpeaker" in device['name']:
                #get product ID
                print(device, file=sys.stderr) 
                print("ReSpeaker device found", file=sys.stderr) 
                self.respeakerID = 1
                self.respeakerActive = True
                break

        if self.respeakerActive:
            # Always use ReSpeakerfor audio if available
            if (self.respeakerID == 0):
                 #  self.respeaker = ReSpeakerLite(rate=rate, chunk=chunk, format=format)
                self.speaker = GenericMicVAD(rate=rate, chunk=chunk, format=format)
            else:
                self.speaker = ReSpeaker4MicArray(rate=rate, chunk=chunk, format=format)
            # fist close any existing stream  
        else:
            # Fallback to default input device using PyAudio
            self.speaker = GenericMicVAD(rate=rate, chunk=chunk, format=format)
        self.stream = self.speaker.open_stream()
    
    def is_voice_active(self):
        """Check if voice is active using ReSpeaker's tuning module."""
        if self.VAD_active and self.speaker:
            return self.speaker.is_voice_active()
        return True 
    
    def is_voice_active_enabled(self):
        """check if voice activity detection is enabled."""
        if self.VAD_active and self.speaker:
            return True
        return False 


    def read(self, chunk=None):  
        if self.VAD_active and self.speaker:
            #direction = self.speaker.get_doa()
            #print(f"Current direction: {direction}")
            #print("active:", self.speaker.is_voice_active()) 
            return self.speaker.read(chunk)
        return self.stream.read(chunk or 1024, exception_on_overflow=False)

    def close(self):
            #Closes the audio stream and terminates PyAudio cleanly.
            if self.VAD_active and self.speaker:
                try:
                    self.speaker.close_stream()
                except Exception as e:
                    print(f"Error closing ReSpeaker stream: {e}", file=sys.stderr) 
                try:
                    self.speaker.p.terminate()
                except Exception as e:
                    print(f"Error terminating ReSpeaker PyAudio: {e}", file=sys.stderr) 
            else:
                try:
                    if self.stream.is_active():
                        self.stream.stop_stream()
                    self.stream.close()
                except Exception as e:
                    print(f"Error closing default stream: {e}", file=sys.stderr) 
                try:
                    self.p.terminate()
                except Exception as e:
                    print(f"Error terminating default PyAudio: {e}", file=sys.stderr) 