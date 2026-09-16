"""Apparatsiz sinov (VS Code da ishga tushiring).

Pico kerak emas. Signallar dasturiy beriladi, vaqt tezlashtiriladi.
Firmware kodi o'zgartirilmasdan, soxta `machine`, `time` modullari va
soxta micro USB (kompyuterdagi MES sahifasi) bilan ishlaydi.

Ishga tushirish:
    python tools/simulate.py            # hammasi
    python tools/simulate.py s07 s18    # tanlanganlari
"""

import os
import shutil
import sys
import tempfile
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools", "stubs"))
sys.path.insert(0, os.path.join(_ROOT, "firmware"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import simenv  # noqa: E402

_real_time = simenv.install()
import config  # noqa: E402
import scenarios  # noqa: E402  (firmware shu yerda import qilinadi)
simenv.restore_time(_real_time)

_TMP = tempfile.mkdtemp(prefix="cnc-sim-")
config.LOG_PATH = os.path.join(_TMP, "events.jsonl")
config.STATE_PATH = os.path.join(_TMP, "state.json")


def main():
    print("CNC 2130 - apparatsiz sinov (kompyuterda)")
    print("=" * 64)
    try:
        fail = scenarios.run_all(lambda e: traceback.print_exc(),
                                 only=sys.argv[1:] or None)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
