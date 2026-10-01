from __future__ import annotations

import base64
from typing import Any, Dict


def capture_image(device_index: int = 0) -> Dict[str, Any]:
    try:
        import cv2
    except ImportError:
        return {
            "role": "error",
            "message": "Camera support is unavailable. Install backend/requirements.txt and restart the backend.",
        }

    camera = None
    try:
        camera = cv2.VideoCapture(int(device_index))
        if not camera.isOpened():
            return {
                "role": "error",
                "message": f"Could not open camera {device_index}. Check camera permissions and close other camera apps.",
            }

        success, frame = camera.read()
        if not success or frame is None:
            return {"role": "error", "message": "The camera opened but did not return an image."}

        height, width = frame.shape[:2]
        largest_dimension = max(height, width)
        if largest_dimension > 1280:
            scale = 1280 / largest_dimension
            frame = cv2.resize(frame, (round(width * scale), round(height * scale)))

        success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            return {"role": "error", "message": "The camera image could not be encoded."}

        return {
            "role": "functionReturnValue",
            "message": "Captured a photo with the camera.",
            "value": "Camera photo captured; inspect the attached image.",
            "image_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
            "mime_type": "image/jpeg",
        }
    except Exception as exc:
        return {"role": "error", "message": f"Camera capture failed: {exc}"}
    finally:
        if camera is not None:
            camera.release()