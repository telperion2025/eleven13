"""
Container formats, read-and-written losslessly (spec §16):

| Format            | Bytes/sector | Layout                                        |
|-------------------|--------------|-----------------------------------------------|
| RK05 (SIMH)       | 512          | raw sector stream, 4879 sectors, no header    |
| RX01              | 128          | raw, 77 tracks x 26 sectors                   |
| RP06/RP07 (RH70)  | 512          | raw, optional .hp-style headers               |
| SIMH magtape .tap | variable     | 32-bit LE header, raw record, trailing 32-bit |
|                   |              | length; zero-length = EOF, zero header = EOM  |
| PC11 paper tape   | variable     | SIMH .pt or raw byte stream (read-only v1)    |

The .tap layout above is a hard requirement: tm11.py/tqk11.py must
round-trip SIMH-created images byte-compatibly (§11.10).

M3 (disk) / M8 (tape round-trip tests) stub per §19.10.
"""

from __future__ import annotations


def _container_not_implemented() -> None:
    """Container I/O lands in M3 (disks) and M8 (SIMH round-trip tests, §11.10)."""
    raise NotImplementedError("§16 container formats — M3/M8")
