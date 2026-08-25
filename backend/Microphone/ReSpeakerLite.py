import usb.core
import usb.util
import pyaudio
import time
import sys

class ReSpeakerLite:
    VENDOR_ID = 0x2886
    PRODUCT_ID = 0x0019

    def __init__(self, rate=16000, chunk=1024, format=pyaudio.paInt16, verbose=True):
        self.verbose = verbose
        self.dev = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)
        if self.dev is None:
            raise ValueError("ReSpeakerLite not found.")
        self._init_device()
        # PyAudio setup
        self.p = pyaudio.PyAudio()
        self.rate = rate
        self.chunk = chunk
        self.format = format
        self.input_device_index = self._find_respeaker_device_index()
        self.stream = None


    def _init_device(self):
         print(f"⚠️ init device is not set up", file=sys.stderr)
       # try:
       #     if self.dev.is_kernel_driver_active(0):
               # self.dev.detach_kernel_driver(0)
      #  except Exception:
       #     pass
        # self.dev.set_configuration()

    def _find_respeaker_device_index(self):
        for i in range(self.p.get_device_count()):
            device_info = self.p.get_device_info_by_index(i)
            if "ReSpeaker" in device_info['name']:
                if self.verbose:
                    print("ReSpeaker device found for PyAudio at index", i)
                return i
        raise ValueError("ReSpeaker device not found for PyAudio.")

    def open_stream(self):
        """Open an audio stream for the ReSpeaker Lite."""
        try:
            self.stream = self.p.open(
                format=self.format,
                channels=1,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk,
                input_device_index=self.input_device_index
            )
            return self.stream
        except Exception as e:
                print(f"Fatal error opening ReSpeaker Lite stream: {e}", file=sys.stderr)
                raise

    def read(self, chunk=None):
        if chunk is None:
            chunk = self.chunk
    
        data = self.stream.read(chunk, exception_on_overflow=False)
        
        # Convert back to bytes
        return data

    def close_stream(self):
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

    def terminate(self):
        self.close_stream()
        self.p.terminate()

    def inspect_usb_device(self):
        """Get detailed information about the USB device without using Tuning"""
        if self.dev is None:
            print("Device not found")
            return
        
        print("\n=== USB Device Information ===", file=sys.stderr)
        print(f"Vendor ID: 0x{self.dev.idVendor:04x}", file=sys.stderr)
        print(f"Product ID: 0x{self.dev.idProduct:04x}", file=sys.stderr)
        
        # Try to get manufacturer and product strings
        try:
            print(f"Manufacturer: {usb.util.get_string(self.dev, self.dev.iManufacturer)}", file=sys.stderr)
        except:
            print("Manufacturer: Unknown", file=sys.stderr)
        
        try:
            print(f"Product: {usb.util.get_string(self.dev, self.dev.iProduct)}", file=sys.stderr)
        except:
            print("Product: Unknown", file=sys.stderr)
        
        # Configuration and interfaces
        print("\nConfigurations:")
        for cfg in self.dev:
            print(f"  Configuration {cfg.bConfigurationValue}", file=sys.stderr)
            for intf in cfg:
                print(f"    Interface {intf.bInterfaceNumber}", file=sys.stderr)
                for ep in intf:
                    print(f"      Endpoint {ep.bEndpointAddress:02x}, " +
                        f"{'IN' if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN else 'OUT'}, " +
                        f"Type: {ep.bmAttributes & 0x03}", file=sys.stderr)
        
        print("============================")

    def scan_parameters(self, max_params=50):
        """Scan for available parameters by trying different indices"""
        print("\n=== Parameter Scan ===", file=sys.stderr)
        print("This is experimental and may cause unexpected behavior!", file=sys.stderr)
        
        # Command 0x80 is typically "get parameter"
        for i in range(max_params):
            try:
                result = self.raw_parameter_request(0x80, index=i, data_or_length=8)
                if result is not None and len(result) > 0:
                    print(f"Parameter {i}: {result}")
            except Exception as e:
                print(f"Error scanning parameter {i}: {e}")
        
        print("=====================")
    
    def raw_parameter_request(self, cmd, index=0, value=0, data_or_length=0):
        # Commands:
        # 0x80: Get parameter
        # 0x00: Set parameter
        # Other commands may be device-specific
        
        # For most ReSpeaker devices:
        # bmRequestType 0xC0 = Device-to-host, Vendor, Device
        # bmRequestType 0x40 = Host-to-device, Vendor, Device
        
        try:
            if cmd & 0x80:  # Read
                return self.dev.ctrl_transfer(
                    bmRequestType=0xC0,  # Device to Host, Vendor, Device
                    bRequest=cmd,
                    wValue=value,
                    wIndex=index,
                    data_or_wLength=data_or_length
                )
            else:  # Write
                return self.dev.ctrl_transfer(
                    bmRequestType=0x40,  # Host to Device, Vendor, Device 
                    bRequest=cmd,
                    wValue=value,
                    wIndex=index,
                    data_or_wLength=data_or_length
                )
        except usb.core.USBError as e:
            print(f"USB Error in raw parameter request: {e}", file=sys.stderr)
            return None
    
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