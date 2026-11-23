"""Simulated head for testing without servos: a still camera, but we only *look at* a
smaller window of its frame, and that window slides as the virtual pan/tilt angles change.
So the centering loop sees the person move in the frame exactly as if the head turned."""
from __future__ import annotations

from head.config import HeadConfig
from head.servo import Head

WINDOW = 0.6   # the virtual camera sees this fraction of the real frame (width and height)


class SimHead(Head):
    def __init__(self, cfg: HeadConfig):
        super().__init__(cfg)

        self.reach = None   # (pan_deg, tilt_deg) the window can slide each way; known after the first frame

    def _write(self, pan, tilt):
        pass

    def clamp(self, pan, tilt):
        pan, tilt = super().clamp(pan, tilt)
        if self.reach:   # the crop can't slide past the real frame edge, so neither can the virtual head
            c = self.cfg
            pan = min(max(pan, c.home_pan - self.reach[0]), c.home_pan + self.reach[0])
            tilt = min(max(tilt, c.home_tilt - self.reach[1]), c.home_tilt + self.reach[1])
        return pan, tilt

    @property
    def at_limit(self):
        if super().at_limit:
            return True
        if not self.reach:
            return False
        c = self.cfg
        return (abs(self.pan - c.home_pan) >= self.reach[0] - 1e-6
                or abs(self.tilt - c.home_tilt) >= self.reach[1] - 1e-6)

    def view(self, frame):
        """Crop the real frame to what the virtual head would see at the current angles."""
        h, w = frame.shape[:2]
        ww, wh = int(w * WINDOW), int(h * WINDOW)
        c = self.cfg
        if self.reach is None:
            self.reach = ((w - ww) / 2 / (ww / c.hfov_deg), (h - wh) / 2 / (wh / c.vfov_deg))
        # the window *is* the virtual camera, so its field of view is hfov x vfov:
        # one degree of pan slides it by ww / hfov pixels (same scale the tracker assumes)
        dx = (c.home_pan - self.pan) * ww / c.hfov_deg   # higher pan = look left = window moves left
        dy = (self.tilt - c.home_tilt) * wh / c.vfov_deg  # higher tilt = look down = window moves down
        if c.pan_invert:
            dx = -dx
        if c.tilt_invert:
            dy = -dy
        x0 = int(min(max((w - ww) / 2 + dx, 0), w - ww))
        y0 = int(min(max((h - wh) / 2 + dy, 0), h - wh))
        return frame[y0:y0 + wh, x0:x0 + ww]
