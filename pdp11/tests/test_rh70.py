"""
M5: the RH70 Massbus controller + RP06/RP07 drives (spec §7/§16) — register
file at 176700, NPR word-at-a-time transfers through board.massbus_host
(22-bit direct, (BAE<<16)|BA), seek scheduling, the BR5/vector-254 completion
interrupt, error paths, and RP container sniffing in open_disk_image.
"""

from __future__ import annotations

from pdp11.board import SystemBoard
from pdp11.bus.massbus import (
    BR_LEVEL,
    CS1_DONE,
    CS1_FNC_SHIFT,
    CS1_GO,
    CS1_IE,
    CS1_TRE,
    CS2_IR,
    CS2_NED,
    CS2_PGE,
    FNC_READ,
    FNC_SEEK,
    FNC_WRITE,
    RH70_BASE,
    RHBA,
    RHBAE,
    RHCS1,
    RHCS2,
    RHWC,
    RPCC,
    RPDA,
    RPDC,
    RPDS,
    VECTOR_RP,
)
from pdp11.config.profiles import PROFILES
from pdp11.devices.rh70_rp import DS_DREADY, RH70, RP06
from pdp11.disk_images.container import (
    RP06_SECTORS,
    RP07_SECTORS,
    RP_SECTOR,
    open_disk_image,
    open_rp06,
)
from pdp11.system_clock import SystemClock


def make_rh70(tmp_path) -> tuple[RH70, SystemBoard, SystemClock, object]:
    """11/70 board + RH70 at 176700 with an RP06 pack, mirroring main.assemble."""
    clock = SystemClock()
    board = SystemBoard(PROFILES["11/70"], clock)
    image = open_rp06(str(tmp_path / "rp06.dsk"))
    rh = RH70(clock, board.massbus_host, RP06(image))
    board.attach_device(RH70_BASE, rh)
    return rh, board, clock, image


def pack_rp_sector(image, n: int, seed: int = 0o100000) -> None:
    data = bytearray(RP_SECTOR)
    for i in range(RP_SECTOR // 2):
        w = (seed + i) & 0o177777
        data[2 * i] = w & 0o377
        data[2 * i + 1] = (w >> 8) & 0o377
    image.write_sector(n, bytes(data))


def run_dma(board: SystemBoard, rh: RH70, limit: int = 100000) -> int:
    n = 0
    while rh.dma_request() and n < limit:
        board.service_dma()
        n += 1
    return n


def test_rh70_reset_register_file(tmp_path):
    rh, board, _clock, _img = make_rh70(tmp_path)
    assert board.read_word(RH70_BASE + RHCS1) & CS1_DONE
    assert board.read_word(RH70_BASE + RHCS2) & CS2_IR
    assert board.read_word(RH70_BASE + RPDS) & DS_DREADY
    assert board.read_word(RH70_BASE + RPCC) == 0
    assert rh.interrupt_pending() is None


def test_rh70_read_sector_via_npr(tmp_path):
    rh, board, _clock, image = make_rh70(tmp_path)
    pack_rp_sector(image, 0)
    board.write_word(RH70_BASE + RHWC, 0o177400)            # -256 words
    board.write_word(RH70_BASE + RHBA, 0)
    board.write_word(RH70_BASE + RHBAE, 0)
    board.write_word(RH70_BASE + RPDA, 0)                   # cyl 0, surface 0, sector 0
    board.write_word(RH70_BASE + RHCS2, 0)                  # unit 0
    board.write_word(RH70_BASE + RHCS1, CS1_IE | (FNC_READ << CS1_FNC_SHIFT) | CS1_GO)
    assert run_dma(board, rh) == 256
    assert board.ram.read_word(0) == 0o100000
    assert board.ram.read_word(0o776) == 0o100377
    cs1 = board.read_word(RH70_BASE + RHCS1)
    assert cs1 & CS1_DONE and cs1 & CS1_IE and not cs1 & CS1_TRE
    assert rh.interrupt_pending() == (VECTOR_RP, BR_LEVEL)


def test_rh70_write_sector_roundtrips(tmp_path):
    rh, board, _clock, image = make_rh70(tmp_path)
    for i in range(256):
        board.ram.write_word(0o2000 + 2 * i, (0o050000 + i) & 0o177777)
    board.write_word(RH70_BASE + RHWC, 0o177400)
    board.write_word(RH70_BASE + RHBA, 0o2000)
    board.write_word(RH70_BASE + RPDA, 1)                   # sector 1
    board.write_word(RH70_BASE + RHCS2, 0)
    board.write_word(RH70_BASE + RHCS1, CS1_IE | (FNC_WRITE << CS1_FNC_SHIFT) | CS1_GO)
    assert run_dma(board, rh) == 256
    data = image.read_sector(1)
    for i in range(256):
        w = data[2 * i] | (data[2 * i + 1] << 8)
        assert w == (0o050000 + i) & 0o177777


def test_rh70_seek_schedules_completion(tmp_path):
    rh, board, clock, _img = make_rh70(tmp_path)
    board.write_word(RH70_BASE + RPDC, 5)
    board.write_word(RH70_BASE + RHCS1, CS1_IE | (FNC_SEEK << CS1_FNC_SHIFT) | CS1_GO)
    for fn in clock.advance(100_000_000):
        fn()
    assert board.read_word(RH70_BASE + RPCC) == 5
    assert board.read_word(RH70_BASE + RHCS1) & CS1_DONE


def test_rh70_error_paths(tmp_path):
    rh, board, _clock, _img = make_rh70(tmp_path)
    board.write_word(RH70_BASE + RHCS2, 1)                  # unit 1: v1 models unit 0 only
    board.write_word(RH70_BASE + RHCS1, CS1_IE | (FNC_READ << CS1_FNC_SHIFT) | CS1_GO)
    assert board.read_word(RH70_BASE + RHCS2) & CS2_NED
    assert board.read_word(RH70_BASE + RHCS1) & CS1_TRE
    # program error: data-transfer GO while DONE clear (busy)
    board.write_word(RH70_BASE + RHCS2, 0)
    board.write_word(RH70_BASE + RHWC, 0o177400)
    board.write_word(RH70_BASE + RHCS1, CS1_IE | (FNC_READ << CS1_FNC_SHIFT) | CS1_GO)
    board.write_word(RH70_BASE + RHCS1, CS1_IE | (FNC_READ << CS1_FNC_SHIFT) | CS1_GO)
    assert board.read_word(RH70_BASE + RHCS2) & CS2_PGE
    run_dma(board, rh)


def test_rh70_serialize_roundtrip(tmp_path):
    rh, board, _clock, _img = make_rh70(tmp_path)
    board.write_word(RH70_BASE + RHWC, 0o177400)
    board.write_word(RH70_BASE + RPDA, 3)
    state = rh.serialize()
    rh2 = RH70(SystemClock(), board.massbus_host, RP06())
    rh2.deserialize(state)
    assert rh2.wc == 0o177400 and rh2.rpda == 3
    assert rh2.cs1 & CS1_DONE


def test_open_disk_image_sniffs_rp_containers(tmp_path):
    p6 = tmp_path / "rp06.dsk"
    with open(p6, "wb") as f:
        f.truncate(RP_SECTOR * RP06_SECTORS)
    assert open_disk_image(str(p6)).sector_count == RP06_SECTORS
    p7 = tmp_path / "rp07.dsk"
    with open(p7, "wb") as f:
        f.truncate(RP_SECTOR * RP07_SECTORS)
    assert open_disk_image(str(p7)).sector_count == RP07_SECTORS
