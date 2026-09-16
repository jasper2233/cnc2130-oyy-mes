"""micro USB aloqasining soxta varianti (sinov uchun).

Bir tomonda Pico (Port - firmware dagi UsbPort o'rniga), ikkinchi tomonda
kompyuterdagi MES sahifasi (mesmock.MES). Holatlar:
  pc_on=False  - kompyuter o'chiq yoki port yopiq: yozilgan narsa yo'qoladi
  stalled=True - port ochiq, lekin brauzer o'qimayapti: har yozish 1 s
                 bloklanadi (Pico W da o'lchangan)
"""

import simtime


class Usb:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pc_on = True
        self.stalled = False
        self.to_pico = bytearray()
        self.from_pico = bytearray()
        self.log = []              # (ms, yo'nalish, qator)
        self.writes = 0

    # ---------- kompyuter tomoni ----------

    def pc_send(self, line):
        if not self.pc_on:
            return
        if isinstance(line, str):
            line = line.encode()
        self.to_pico.extend(line + b"\n")
        self._trace("PC>", line)

    def pc_read_lines(self):
        if not self.pc_on or self.stalled:
            return []
        data = bytes(self.from_pico)
        self.from_pico = bytearray()
        lines = data.split(b"\n")
        rest = lines.pop()
        self.from_pico.extend(rest)
        return [l for l in lines if l]

    def set_pc(self, on):
        if not on:
            self.to_pico = bytearray()
            self.from_pico = bytearray()
        self.pc_on = bool(on)

    def _trace(self, d, line):
        self.log.append((simtime.CLOCK.now_ms, d, line))
        if len(self.log) > 300:
            del self.log[:100]


USB = Usb()


class Port:
    """Pico tomoni: firmware dagi UsbPort bilan bir xil interfeys."""

    def read(self, limit):
        if not USB.pc_on:
            return b""
        data = bytes(USB.to_pico[:limit])
        USB.to_pico = USB.to_pico[limit:]
        return data

    def writable(self):
        return USB.pc_on

    def write(self, text):
        USB.writes += 1
        if not USB.pc_on:
            return
        if USB.stalled:
            simtime.sleep_ms(1000)     # TX timeout, ma'lumot yo'qoladi
            return
        line = text.encode() if isinstance(text, str) else text
        USB.from_pico.extend(line)
        USB._trace("PICO>", line.rstrip(b"\n"))
