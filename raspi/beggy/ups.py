"""Waveshare UPS HAT (E) over I2C (address 0x2D). Register map: docs.waveshare.com/UPS-HAT-E-Tutorials-Register"""
from __future__ import annotations

ADDR = 0x2D
ACTIVITY = ["standby", "trickle", "constant_current", "constant_voltage", "pending", "full", "timeout", "invalid"]


def _u16(b, i):
    return int.from_bytes(b[i:i + 2], "little")


def _s16(b, i):
    return int.from_bytes(b[i:i + 2], "little", signed=True)


def parse(status: int, vbus: bytes, batt: bytes, cells: bytes) -> dict:
    """Turn raw register bytes into a reading (pure function, unit-tested)."""
    current = _s16(batt, 2)
    cell_mv = [_u16(cells, i) for i in (0, 2, 4, 6)]
    return {
        "percent": _u16(batt, 4),
        "voltage_v": round(_u16(batt, 0) / 1000, 3),
        "current_ma": current,                          # + charging, - discharging
        "remaining_mah": _u16(batt, 6),
        "minutes_to_empty": _u16(batt, 8) if current < 0 else None,
        "minutes_to_full": _u16(batt, 10) if current >= 0 else None,
        "cell_v": [round(c / 1000, 3) for c in cell_mv],
        "min_cell_v": round(min(cell_mv) / 1000, 3),
        "charging": bool(status & 0x80),
        "fast_charge": bool(status & 0x40),
        "plugged_in": bool(status & 0x20),
        "charger": ACTIVITY[status & 0x07],
        "input_v": round(_u16(vbus, 0) / 1000, 2),
        "input_w": round(_u16(vbus, 4) / 1000, 2),
    }


class Ups:
    def __init__(self, bus: int = 1):
        from smbus2 import SMBus
        self.bus = SMBus(bus)

    def _rd(self, reg, n):
        return bytes(self.bus.read_i2c_block_data(ADDR, reg, n))

    def read(self) -> dict:
        return parse(self._rd(0x02, 1)[0], self._rd(0x10, 6), self._rd(0x20, 12), self._rd(0x30, 8))

    def power_off(self):
        """Ask the HAT to cut power in ~30 s (the caller must shut Linux down right after)."""
        self.bus.write_byte_data(ADDR, 0x01, 0x55)
