"""Soxta modullarni o'rnatish. Firmware import qilinishidan OLDIN chaqiriladi.

CPython da ham, Pico da ham bir xil ishlaydi.
"""

import sys

import simtime
import fakemachine


def install():
    """Firmware `time` va `machine` ni soxtasidan oladi.

    Haqiqiy `time` modulini qaytaradi - CPython da uni keyin tiklash kerak,
    aks holda standart kutubxona buziladi.
    """
    real_time = sys.modules.get("time")
    sys.modules["time"] = simtime
    sys.modules["machine"] = fakemachine
    return real_time


def restore_time(real_time):
    if real_time is not None:
        sys.modules["time"] = real_time
