"""Frame sources. All of them return BGR numpy frames (OpenCV order).

- "picam": Raspberry Pi Camera Module 3 through Picamera2 (on the Pi)
- an integer like "0": USB webcam through OpenCV (for testing on a laptop)
- a file path: a recorded video (for repeatable tests)
"""
from __future__ import annotations

import cv2


class PiCamera:
    realtime = True

    def __init__(self, width: int, height: int):
        from picamera2 import Picamera2  # only available on the Pi
        from libcamera import controls

        if not Picamera2.global_camera_info():
            raise RuntimeError(
                "no Pi camera detected (`rpicam-hello --list-cameras` is empty) - check the ribbon "
                "cable: Pi 5 needs the 22-pin mini cable, contacts facing the right way, latch closed; "
                "then reboot")
        self.cam = Picamera2()
        # Picamera2's "RGB888" is stored as B,G,R in memory, i.e. what OpenCV expects
        cfg = self.cam.create_video_configuration(main={"size": (width, height), "format": "RGB888"})
        self.cam.configure(cfg)
        self.cam.start()
        try:
            self.cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})  # Camera Module 3 autofocus
        except Exception:
            pass

    def read(self):
        return self.cam.capture_array()

    def close(self):
        self.cam.stop()
        self.cam.close()


class OpenCVSource:
    def __init__(self, source, width: int, height: int):
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open video source {source!r}")
        # a video file is read as fast as possible, so time comes from its own fps
        self.realtime = isinstance(source, int)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        if isinstance(source, int):
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    def read(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
        self.cap.release()


def open_source(name: str, width: int, height: int):
    if name == "picam":
        return PiCamera(width, height)
    if name.isdigit():
        return OpenCVSource(int(name), width, height)
    return OpenCVSource(name, width, height)
