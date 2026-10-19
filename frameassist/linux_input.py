"""Tiny pure-Python layer over Linux uinput: a virtual USB keyboard.

No compiled packages, so it installs on the Steam Frame's ARM chip without
build tools. Needs write access to /dev/uinput (the installer sets that up).

The key table and codec are portable (used by tests on any OS); only creating
the actual device needs Linux + fcntl + /dev/uinput.
"""

import os
import struct
import time

try:
    import fcntl
except ImportError:  # non-Linux: only the portable key table is available
    fcntl = None

EV_SYN, EV_KEY = 0x00, 0x01
SYN_REPORT = 0

# Key codes from linux/input-event-codes.h
KEY = {
    "ESC": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9,
    "9": 10, "0": 11, "MINUS": 12, "EQUAL": 13, "BACKSPACE": 14, "TAB": 15,
    "Q": 16, "W": 17, "E": 18, "R": 19, "T": 20, "Y": 21, "U": 22, "I": 23,
    "O": 24, "P": 25, "LEFTBRACE": 26, "RIGHTBRACE": 27, "ENTER": 28,
    "LEFTCTRL": 29, "A": 30, "S": 31, "D": 32, "F": 33, "G": 34, "H": 35,
    "J": 36, "K": 37, "L": 38, "SEMICOLON": 39, "APOSTROPHE": 40, "GRAVE": 41,
    "LEFTSHIFT": 42, "BACKSLASH": 43, "Z": 44, "X": 45, "C": 46, "V": 47,
    "B": 48, "N": 49, "M": 50, "COMMA": 51, "DOT": 52, "SLASH": 53,
    "SPACE": 57, "HOME": 102, "UP": 103, "LEFT": 105, "RIGHT": 106,
    "END": 107, "DOWN": 108, "DELETE": 111,
}
KEY.update({f"F{i}": 58 + i for i in range(1, 11)})          # F1-F10 = 59-68
KEY.update({"F11": 87, "F12": 88})
KEY.update({f"F{i}": 183 + i - 13 for i in range(13, 25)})   # F13-F24 = 183-194


def key_code(name):
    """'F13', 'KEY_F13' or 'f13' -> 183."""
    name = name.upper()
    if name.startswith("KEY_"):
        name = name[4:]
    if name not in KEY:
        raise ValueError(f"Unknown key {name!r}")
    return KEY[name]


# struct input_event: struct timeval (two longs), u16 type, u16 code, s32 value
_EVENT = struct.Struct("llHHi")


def _ioc(direction, nr, size, type_=ord("U")):
    return (direction << 30) | (size << 16) | (type_ << 8) | nr


_UINPUT_SETUP = struct.Struct("HHHH80sI")  # input_id + name + ff_effects_max
UI_DEV_CREATE = _ioc(0, 1, 0)
UI_DEV_DESTROY = _ioc(0, 2, 0)
UI_DEV_SETUP = _ioc(1, 3, _UINPUT_SETUP.size)
UI_SET_EVBIT = _ioc(1, 100, 4)
UI_SET_KEYBIT = _ioc(1, 101, 4)
BUS_USB = 0x03

ALL_KEYS = sorted(set(KEY.values()))
ALL_KEYS.append(KEY["LEFTSHIFT"])  # always able to shift


class UInputKeyboard:
    """A virtual USB keyboard. Needs write access to /dev/uinput on Linux."""

    def __init__(self, keys=None, name="frameassist keyboard", path="/dev/uinput"):
        if fcntl is None:
            raise RuntimeError("uinput is only available on Linux")
        self.name = name
        self._fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_KEY)
            for code in sorted(set(keys or ALL_KEYS)):
                fcntl.ioctl(self._fd, UI_SET_KEYBIT, code)
            setup = _UINPUT_SETUP.pack(BUS_USB, 0x1209, 0xFC02, 1, name.encode()[:79], 0)
            fcntl.ioctl(self._fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(self._fd, UI_DEV_CREATE)
        except OSError:
            os.close(self._fd)
            raise
        time.sleep(0.3)  # let the desktop notice the new keyboard

    def emit(self, code, value):
        os.write(self._fd, _EVENT.pack(0, 0, EV_KEY, code, value))
        os.write(self._fd, _EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def close(self):
        try:
            fcntl.ioctl(self._fd, UI_DEV_DESTROY)
        finally:
            os.close(self._fd)


def device_name(fd, length=256):
    try:
        buf = bytearray(length)
        fcntl.ioctl(fd, _ioc(2, 0x06, length, ord("E")), buf)
        return buf.split(b"\0", 1)[0].decode(errors="replace")
    except OSError:
        return ""
