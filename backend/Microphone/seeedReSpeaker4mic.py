import usb.core
import usb.util
import pyaudio
import time
from Microphone.tuning import Tuning
from Microphone.pixel_ring import PixelRing 

class ReSpeaker4MicArray:
    VENDOR_ID = 0x2886
    PRODUCT_ID = 0x0018

    def __init__(self, rate=16000, chunk=1024, format=pyaudio.paInt16, verbose=True):
        self.verbose = verbose
        self.dev = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)
        if self.dev is None:
            raise ValueError("ReSpeaker 4-Mic Array not found.")
        self._init_device()
        # self.inspect_usb_device()
        # PyAudio setup
        self.p = pyaudio.PyAudio()
        self.rate = rate
        self.chunk = chunk
        self.format = format
        self.input_device_index = self._find_respeaker_device_index()
        self.stream = None

        # Tuning (for DoA, gain, VAD, etc.)
        try:
            self.tuning = Tuning(self.dev)
            self.tuning_available = True
            if self.verbose:
                print("✅ Tuning module available")
        except Exception as e:
            self.tuning_available = False
            print(f"⚠️ Tuning module not available: {e}")

        # # Pixel ring (optional, USB-only version)
        try:
            self.pixel_ring = PixelRing(self.dev)
            self.pixel_ring.think()
            time.sleep(1)
            self.pixel_ring.listen()
        except Exception as e:
             self.pixel_ring = None
             if self.verbose:
                 print(f"⚠️ PixelRing not available: {e}")

    def _init_device(self):
        try:
            if self.dev.is_kernel_driver_active(0):
                self.dev.detach_kernel_driver(0)
        except Exception:
            pass
        self.dev.set_configuration()

    def _find_respeaker_device_index(self):
        for i in range(self.p.get_device_count()):
            device_info = self.p.get_device_info_by_index(i)
            if "ReSpeaker" in device_info['name']:
                if self.verbose:
                    print("ReSpeaker device found for PyAudio at index", i)
                return i
        raise ValueError("ReSpeaker device not found for PyAudio.")

    def open_stream(self):
        if self.stream is None:
            if self.verbose:
                print(f"Opening audio stream with rate: {self.rate} and chunk size: {self.chunk}")
            self.stream = self.p.open(
                format=self.format,
                channels=1,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk,
                input_device_index=self.input_device_index
            )
        return self.stream

    def read(self, chunk=None):
        return self.stream.read(chunk or self.chunk, exception_on_overflow=False)

    def close_stream(self):
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

    def terminate(self):
        self.close_stream()
        self.p.terminate()

    def get_doa(self):
        """Get Direction of Arrival using tuning.py"""
        try:
            return self.tuning.direction
        except Exception as e:
            print(f"Error getting DoA: {e}")
            return None

    def is_voice_active(self):
        """Check for voice activity using tuning.py"""
        try:
            return bool(self.tuning.is_voice())
        except Exception as e:
            print(f"Error checking voice activity: {e}")
            return None

    def set_vad_threshold(self, db):
        try:
            self.tuning.set_vad_threshold(db)
        except Exception as e:
            print(f"Error setting VAD threshold: {e}")

    # --- LED control (optional, if you have a USB-only PixelRing class) ---
    # def set_led(self, r, g, b):
    #     try:
    #         if self.pixel_ring:
    #             self.pixel_ring.set_color(r=r, g=g, b=b)
    #     except Exception as e:
    #         print(f"Error setting LED: {e}")

    # def set_all_leds(self, r, g, b):
    #     self.set_led(r, g, b)

    def set_gain(self, gain):
        """Set microphone gain using tuning.py (recommended)"""
        try:
            self.tuning.gain = gain
        except Exception as e:
            print(f"Error setting gain: {e}")
    
# Example usage:
"""
if __name__ == "__main__":
    respeaker = ReSpeaker4MicArray()
    respeaker.open_stream()
    print("DoA:", respeaker.get_doa())
    respeaker.set_gain(128)
    data = respeaker.read()
    respeaker.terminate()
"""