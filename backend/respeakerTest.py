"""
ReSpeaker 4-Mic Array Test Script
This script tests various features of the ReSpeaker 4-Mic Array:
- LED control
- Direction of Arrival (DoA)
- Voice Activity Detection
- Audio recording
- Gain control
"""

import usb.core
import usb.util
import time
import pyaudio
import numpy as np
import os
from Microphone.tuning import Tuning  # Import if available
from Microphone.pixel_ring import PixelRing
class ReSpeaker4MicArray:
    VENDOR_ID = 0x2886
    PRODUCT_ID = 0x0018

    def __init__(self, verbose=True):
        """
        Initialize ReSpeaker with enhanced error checking and device recovery.
        """
        self.verbose = verbose
        self.audio_available = False  # Flag for audio availability
        self.dev = None
        self.tuning_available = False
        self.p = None
        self.stream = None
        
        # Step 1: Find the device
        try:
            self.dev = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)
            if self.dev:
                self.Mic_tuning = Tuning(self.dev)
                self.pixel_ring = PixelRing(self.dev)
                print("ℹ️ tunding.direction: " + str(self.Mic_tuning.direction))
                print("ℹ️ led test ")       
                print("ℹ️ led wakeup")
                self.pixel_ring.wakeup(180)
                time.sleep(3)
                print("ℹ️ led think")
                self.pixel_ring.think()
                time.sleep(3)
                self.pixel_ring.listen()
                print("ℹ️ led test finished ")
            if self.dev is None:
                print("⚠️ ReSpeaker device not found! Please check connections.")
                return
            if self.verbose: 
                print("✅ ReSpeaker device detected.")
        except usb.core.NoBackendError:
            print("⚠️ USB backend not available. Please install libusb.")
            print("   macOS: brew install libusb")
            print("   Linux: sudo apt-get install libusb-1.0-0-dev")
            return
        except Exception as e:
            print(f"⚠️ USB error while finding device: {e}")
            return
            
                    
        # Step 1.5: Initialize tuning if available
        try:
            self.tuning = Tuning(self.dev)
            self.tuning_available = True
            print("✅ Tuning module available!")
        except Exception as e:
            self.tuning_available = False
            print(f"ℹ️ Tuning module not available: {e}")
            print("   Voice activity detection will be limited.")
            
            
        # Step 2: Try to reset and initialize the device
        try:
            # Try to reset the device to clear any previous state
            try:
                self.dev.reset()
                if self.verbose:
                    print("✅ Device reset successful.")
                time.sleep(0.5)  # Give device time to stabilize
            except Exception as e:
                if self.verbose:
                    print(f"ℹ️ Could not reset device (not critical): {e}")
                
            # Detach kernel driver if active
            try:
                if self.dev.is_kernel_driver_active(0):
                    self.dev.detach_kernel_driver(0)
                    if self.verbose:
                        print("✅ Kernel driver detached.")
            except Exception as e:
                if self.verbose:
                    print(f"ℹ️ Kernel driver handling error (may be normal): {e}")
            
                
                
        except Exception as e:
            print(f"⚠️ Failed to initialize USB device: {e}")
            return
                
        # Step 3: Initialize audio
        try:
            self.p = pyaudio.PyAudio()
            if self.verbose:
                print("✅ PyAudio initialized.")
            
            # Find device index
            try:
                self.device_index = self._find_respeaker_device_index()
                self.audio_available = True
                if self.verbose:
                    print(f"✅ Audio device found at index {self.device_index}")
            except Exception as e:
                print(f"⚠️ Could not find audio device: {e}")
                self.audio_available = False
        except Exception as e:
            print(f"⚠️ Failed to initialize audio: {e}")
            self.p = None

        # Summary
        if self.audio_available:
            if self.verbose:
                print("\n=== Device Status ===")
                print(f"Audio: {'✅ Available' if self.audio_available else '❌ Unavailable'}")
                print(f"Tuning: {'✅ Available' if self.tuning_available else '❌ Unavailable'}")
        else:
            print("❌ Device initialization failed! No features are available.")

    def reset_device(self):
        """Try to reset the USB device to recover from errors"""
        if not self.dev:
            print("❌ No device to reset")
            return False
            
        try:
            # Force release any interface that might be claimed
            for config in self.dev:
                for interface in config:
                    try:
                        self.dev.detach_kernel_driver(interface.bInterfaceNumber)
                    except:
                        pass
                    
            # Reset and wait longer
            self.dev.reset()
            time.sleep(2.0)  # Wait longer for device to stabilize
            
            # Re-initialize configuration
            self.dev.set_configuration()
            time.sleep(0.5)
    
            print("✅ Device reset!")
            return True
        except Exception as e:
            print(f"❌ Device reset failed: {e}")
            return False

    def get_direction(self):
            """Get Direction of Arrival (0-359 degrees)"""

            try:
                angle = self.Mic_tuning.direction
                return angle
            except usb.core.USBError as e:
                if e.errno == 5:  # Input/Output Error
                    print("⚠️ I/O Error getting DoA. Trying to reset device...")
                    if self.reset_device():
                        try:
                            # Try again after reset
                            angle = self.Mic_tuning.direction
                            return angle
                        except Exception:
                            pass
                print(f"❌ Error getting DoA: {e}")
                return None
        
    def _find_respeaker_device_index(self):
            # Find the ReSpeaker device index
            for i in range(self.p.get_device_count()):
                dev_info = self.p.get_device_info_by_index(i)
                if 'ReSpeaker' in dev_info['name']:
                    print(f"Found ReSpeaker at index {i}: {dev_info['name']}")
                    return i
            print("Warning: ReSpeaker not found in audio devices, using default input")
            return self.p.get_default_input_device_info()['index']
        
    def set_led(self, index, r, g, b):
            """Set a single LED's color (index: 0-11)"""
            try:
                self.pixel_ring.write(1, [r, g, b, 0])
                return True
            except Exception as e:
                print(f"Error setting LED: {e}")
                return False
        
    def set_all_leds(self, r, g, b):
            """Set all LEDs to the same color"""
            success = True
            for i in range(12):
                if not self.set_led(i, r, g, b): 
                    success = False
            return success
        
    def set_gain(self, gain):
        """Set microphone gain (0-255) using tuning.py if available."""
        if self.tuning_available:
            try:
                self.tuning.gain = gain
                print(f"Gain set to {gain} using tuning.py")
                return True
            except Exception as e:
                print(f"Error setting gain with tuning.py: {e}")
                return False
        else:
            # Fallback to direct USB if tuning.py is not available
            try:
                self.dev.ctrl_transfer(0x40, 0x03, gain, 0, None)
                print(f"Gain set to {gain} using ctrl_transfer")
                return True
            except Exception as e:
                print(f"Error setting gain: {e}")
                return False
        
    def is_voice_active(self):
            """Check for voice activity (requires tuning module)"""
            if self.tuning_available:
                try:
                    return bool(self.tuning.is_voice())
                except Exception as e:
                    print(f"Error checking voice: {e}")
            return None
        
    def record_audio(self, seconds=3):
            """Record audio for a few seconds and return the samples"""
            if self.stream is not None:
                print("Stream already open, close it first")
                return None
                
            RATE = 16000
            CHUNK = 1024
            
            try:
                self.stream = self.p.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK,
                    input_device_index=self.device_index
                )
                
                print(f"Recording for {seconds} seconds...")
                frames = []
                for _ in range(0, int(RATE / CHUNK * seconds)):
                    data = self.stream.read(CHUNK, exception_on_overflow=False)
                    frames.append(data)
                
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
                
                # Convert to numpy array for analysis
                audio_data = np.frombuffer(b''.join(frames), dtype=np.int16)
                return audio_data
                
            except Exception as e:
                print(f"Error recording: {e}")
                if self.stream:
                    self.stream.close()
                    self.stream = None
                return None
        
    def terminate(self):
            """Clean up resources"""
            if self.stream:
                try:
                    self.stream.stop_stream()
                    self.stream.close()
                except:
                    pass
            self.p.terminate()


def clear_screen():
        """Clear the terminal screen"""
        os.system('cls' if os.name == 'nt' else 'clear')


def main():
        try:
            # Initialize ReSpeaker
            print("Initializing ReSpeaker 4-Mic Array...")
            respeaker = ReSpeaker4MicArray()
            print("ReSpeaker initialized successfully!")
            
            while True:
                clear_screen()
                print("\n=== ReSpeaker 4-Mic Array Test Menu ===")
                print("1. Test LED control")
                print("2. Test Direction of Arrival (DoA)")
                print("3. Test Voice Activity Detection")
                print("4. Test Audio Recording")
                print("5. Test Microphone Gain")
                print("6. Run LED Animation")
                print("0. Exit")
                
                choice = input("\nEnter your choice (0-6): ")
                
                if choice == '1':
                    test_leds(respeaker)
                elif choice == '2':
                    test_doa(respeaker)
                elif choice == '3':
                    test_vad(respeaker)
                elif choice == '4':
                    test_audio(respeaker)
                elif choice == '5':
                    test_gain(respeaker)
                elif choice == '6':
                    test_led_animation(respeaker)
                elif choice == '0':
                    break
                else:
                    print("Invalid choice. Press Enter to continue...")
                    input()
        
        except Exception as e:
            print(f"Error: {e}")
        finally:
            if 'respeaker' in locals():
                respeaker.terminate()
                print("ReSpeaker resources released.")


def test_leds(respeaker):
            """Test LED functionality"""
            clear_screen()
            print("\n=== LED Test ===")
                
            # Colors to test
            colors = [
                (255, 0, 0, "RED"),
                (0, 255, 0, "GREEN"),
                (0, 0, 255, "BLUE"),
                (255, 255, 0, "YELLOW"),
                (0, 255, 255, "CYAN"),
                (255, 0, 255, "MAGENTA"),
                (255, 255, 255, "WHITE"),
                (0, 0, 0, "OFF")
            ]
            
            for r, g, b, color_name in colors:
                print(f"Setting all LEDs to {color_name}")
                respeaker.set_all_leds(r, g, b)
                time.sleep(1)
            
            respeaker.pixel_ring.listen()
            print("\nLED test complete! Press Enter to continue...")
            input()


def test_doa(respeaker):
            """Test Direction of Arrival functionality"""
            clear_screen()
            print("\n=== Direction of Arrival Test ===")
            try:
                for _ in range(50):  # Read for about 5 seconds
                    angle = respeaker.get_direction()
                    if angle is not None:
                        print(f"Direction: {angle} degrees ", end='\r')
                    else:
                        print("Direction: Not available ", end='\r')
                    time.sleep(0.1)
            except KeyboardInterrupt:
                pass
            
            print("\nDoA test complete! Press Enter to continue...")
            input()


def test_vad(respeaker):
            """Test Voice Activity Detection"""
            clear_screen()
            print("\n=== Voice Activity Detection Test ===")
            
            if not respeaker.tuning_available:
                print("Tuning module not available. This test requires tuning.py.")
                print("\nPress Enter to continue...")
                input()
                return
            
            print("Speak and see if voice activity is detected.")
            print("Press Ctrl+C to stop the test.")
            
            try:
                for _ in range(50):  # Check for about 5 seconds
                    is_voice = respeaker.is_voice_active()
                    status = "🗣️ VOICE DETECTED" if is_voice else "🔇 Silence"
                    print(f"{status}", end='\r')
                    time.sleep(0.1)
            except KeyboardInterrupt:
                pass
            
            print("\nVAD test complete! Press Enter to continue...")
            input()


def test_audio(respeaker):
        """Test audio recording"""
        clear_screen()
        print("\n=== Audio Recording Test ===")
        
        print("Recording 3 seconds of audio...")
        audio_data = respeaker.record_audio(3)
        
        if audio_data is not None:
            # Calculate statistics to verify recording
            rms = np.sqrt(np.mean(audio_data**2))
            peak = np.max(np.abs(audio_data))
            print(f"Recording complete!")
            print(f"Audio statistics:")
            print(f"- RMS level: {rms:.2f}")
            print(f"- Peak level: {peak:.2f}")
            print(f"- Sample count: {len(audio_data)}")
        
        print("\nAudio test complete! Press Enter to continue...")
        input()


def test_gain(respeaker):
        """Test microphone gain control"""
        clear_screen()
        print("\n=== Microphone Gain Test ===")
        
        for gain in [0, 64, 128, 192, 255]:
            print(f"Setting gain to {gain}/255")
            respeaker.set_gain(gain)
            time.sleep(1)
        
        # Reset to mid-level
        respeaker.set_gain(128)
        print("Gain reset to mid-level (128)")
        
        print("\nGain test complete! Press Enter to continue...")
        input()


def test_led_animation(respeaker):
    """Run a LED animation"""
    clear_screen()
    print("\n=== LED Animation Test ===")
    print("Press Ctrl+C to stop the animation.")
    
    try:
        # Rotating single LED animation
        for _ in range(5):  # 5 full rotations
            for i in range(12):
                # Turn all LEDs off
                respeaker.set_all_leds(0, 0, 0)
                # Light up just one LED
                respeaker.set_led(i, 0, 64, 255)  # Blue
                time.sleep(0.1)
                
        # Pulse animation
        for _ in range(3):  # 3 pulses
            for brightness in range(0, 255, 15):
                respeaker.set_all_leds(0, brightness, brightness)
                time.sleep(0.05)
            for brightness in range(255, 0, -15):
                respeaker.set_all_leds(0, brightness, brightness)
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        # Turn set back to listening mode
        respeaker.pixel_ring.listen()
    
    print("\nAnimation test complete! Press Enter to continue...")
    input()


if __name__ == "__main__":
    main()