#!/usr/bin/env python3
"""
Ghost-Printer — CORTEX.bin を Winbond W25Q シリーズ SPI NOR Flash に焼くツール

対応チップ (JEDEC ID で自動識別、 コマンドセットは共通):
  - W25Q64  (8MB, 64Mbit, capacity_id=0x17)  ← Pre-MVP 採択 (2026-05-17)
  - W25Q128 (16MB, 128Mbit, capacity_id=0x18) ← 当初想定 (国内品薄のため後回し)
  - その他 W25Q08/16/32/256 も JEDEC ID 解釈表で表示は可能

共通仕様:
  - セクタ: 4KB 単位で消去 (Sector Erase 0x20)
  - ページ: 256バイト 単位で書き込み (Page Program 0x02)
  - 読み出し: 任意サイズ (Read Data 0x03)
  - SPI mode 0, max 104MHz (安全のため10MHzで駆動)

SPI バス番号 (重要):
  - Raspberry Pi 5 / Pi 4: SPI0 → /dev/spidev0.0    → --bus 0
  - Orange Pi 3B (RK3566): SPI3 (M0) → /dev/spidev3.0 → --bus 3

依存:
  spidev (実機動作時のみ)
  本ファイル単体ではシミュレータモードで動作し、spidev が無くても使える

使い方:
  # 書き込み (消去→書込→ベリファイ) — Pi 5
  python flash_cortex.py data/CORTEX.bin --bus 0

  # 書き込み — OPi 3B
  python flash_cortex.py data/CORTEX.bin --bus 3

  # 環境変数でも指定可能 (CLI 引数より優先順位は低い)
  GPP_SPI_BUS=3 python flash_cortex.py data/CORTEX.bin

  # 検証のみ (すでに焼いたファイルと SHA256 比較)
  python flash_cortex.py --verify data/CORTEX.bin --bus 3

  # 読み出し (Flash 内容をダンプ)
  python flash_cortex.py --read --out dumped.bin --size 16384 --bus 3

  # JEDEC ID だけ取得 (配線確認用)
  python flash_cortex.py --read-id --bus 3

  # シミュレータモード (spidev が無くても動く; テスト用)
  python flash_cortex.py --simulate data/CORTEX.bin
"""

import argparse
import hashlib
import sys
import time
import struct
from pathlib import Path
from typing import Optional, List


# ════════════════════════════════════════════════════════════════════════════════
# W25Q シリーズ コマンド・定数 (W25Q64/W25Q128 で共通)
# ════════════════════════════════════════════════════════════════════════════════

# コマンドバイト
CMD_WRITE_ENABLE      = 0x06
CMD_WRITE_DISABLE     = 0x04
CMD_READ_STATUS_1     = 0x05
CMD_READ_STATUS_2     = 0x35
CMD_WRITE_STATUS      = 0x01
CMD_PAGE_PROGRAM      = 0x02
CMD_READ_DATA         = 0x03
CMD_FAST_READ         = 0x0B
CMD_SECTOR_ERASE_4K   = 0x20
CMD_BLOCK_ERASE_32K   = 0x52
CMD_BLOCK_ERASE_64K   = 0xD8
CMD_CHIP_ERASE        = 0xC7
CMD_READ_ID           = 0x9F   # JEDEC ID
CMD_MANUFACTURER_ID   = 0x90

# サイズ定数
PAGE_SIZE     = 256
SECTOR_SIZE   = 4 * 1024        # 4KB
BLOCK_SIZE    = 64 * 1024       # 64KB
CHIP_CAPACITY = 8 * 1024 * 1024   # 8MB (W25Q64) ← Pre-MVP 採択。 CORTEX.bin 実サイズは 数KB なので余裕

# W25Q シリーズ JEDEC ID (Pre-MVP は W25Q64)
EXPECTED_MANUFACTURER = 0xEF    # Winbond
EXPECTED_MEMORY_TYPE  = 0x40
EXPECTED_CAPACITY_ID  = 0x17    # 64Mbit = 8MB (W25Q64) ※ W25Q128 に戻す場合は 0x18
EXPECTED_CHIP_NAME    = "W25Q64"  # ログ/メッセージ表示用

# ステータスレジスタ bit
STATUS_BUSY = 0x01
STATUS_WEL  = 0x02   # Write Enable Latch


# ════════════════════════════════════════════════════════════════════════════════
# SPI バックエンド（実機 + シミュレータ）
# ════════════════════════════════════════════════════════════════════════════════

class SpiBackend:
    """SPI通信の抽象。実機(spidev)とシミュレータを切り替え可能に"""

    def xfer(self, data: List[int]) -> List[int]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SpidevBackend(SpiBackend):
    """実機用: python-spidev経由でGPIO SPI0と通信する"""

    def __init__(self, bus: int = 0, device: int = 0, speed_hz: int = 10_000_000):
        try:
            import spidev  # type: ignore
        except ImportError:
            raise RuntimeError(
                "spidev が見つかりません。Pi上で `pip install spidev` を実行してください。\n"
                "非実機環境でテストする場合は --simulate を使ってください。"
            )
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = speed_hz
        self.spi.mode = 0

    def xfer(self, data: List[int]) -> List[int]:
        return self.spi.xfer2(data)

    def close(self) -> None:
        self.spi.close()


class SimulatedBackend(SpiBackend):
    """
    テスト用シミュレータ: メモリ内に W25Q シリーズ Flash (Pre-MVP は W25Q64 8MB) の状態を再現する。

    - JEDEC ID を返す
    - セクタ単位の消去（0xFF化）
    - ページ単位の書き込み（AND演算で 1→0 のみ）
    - 読み出し
    - BUSYフラグを模擬（演算中は True、xferコール回数で自動解除）
    """

    def __init__(self):
        self.memory = bytearray(b"\xFF" * CHIP_CAPACITY)
        self.write_enabled = False
        self.busy_until = 0
        self.call_counter = 0

    def _is_busy(self) -> bool:
        self.call_counter += 1
        return self.call_counter < self.busy_until

    def xfer(self, data: List[int]) -> List[int]:
        cmd = data[0]

        # JEDEC ID
        if cmd == CMD_READ_ID:
            return [0, EXPECTED_MANUFACTURER, EXPECTED_MEMORY_TYPE, EXPECTED_CAPACITY_ID]

        # Manufacturer/Device ID
        if cmd == CMD_MANUFACTURER_ID:
            # dummy 3 bytes + mfr + dev
            return [0, 0, 0, 0, EXPECTED_MANUFACTURER, 0x17]

        # ステータス読み出し
        if cmd == CMD_READ_STATUS_1:
            status = 0
            if self._is_busy():
                status |= STATUS_BUSY
            if self.write_enabled:
                status |= STATUS_WEL
            return [0, status]

        # Write Enable
        if cmd == CMD_WRITE_ENABLE:
            self.write_enabled = True
            return [0]

        # Sector Erase 4KB
        if cmd == CMD_SECTOR_ERASE_4K:
            if not self.write_enabled:
                return [0]
            addr = (data[1] << 16) | (data[2] << 8) | data[3]
            sector_start = addr & ~(SECTOR_SIZE - 1)
            for i in range(sector_start, sector_start + SECTOR_SIZE):
                self.memory[i] = 0xFF
            self.write_enabled = False
            self.busy_until = self.call_counter + 2  # 数回BUSYを模擬
            return [0]

        # Chip Erase
        if cmd == CMD_CHIP_ERASE:
            if not self.write_enabled:
                return [0]
            self.memory = bytearray(b"\xFF" * CHIP_CAPACITY)
            self.write_enabled = False
            self.busy_until = self.call_counter + 5
            return [0]

        # Page Program
        if cmd == CMD_PAGE_PROGRAM:
            if not self.write_enabled:
                return [0] * len(data)
            addr = (data[1] << 16) | (data[2] << 8) | data[3]
            payload = data[4:]
            # NORフラッシュは 1→0 のみ書ける（ANDで模擬）
            for i, b in enumerate(payload):
                if addr + i >= CHIP_CAPACITY:
                    break
                self.memory[addr + i] &= b
            self.write_enabled = False
            self.busy_until = self.call_counter + 2
            return [0] * len(data)

        # Read Data
        if cmd == CMD_READ_DATA:
            addr = (data[1] << 16) | (data[2] << 8) | data[3]
            length = len(data) - 4
            result = [0, 0, 0, 0]
            for i in range(length):
                result.append(self.memory[addr + i] if addr + i < CHIP_CAPACITY else 0xFF)
            return result

        # Fast Read (1 dummy byte after address)
        if cmd == CMD_FAST_READ:
            addr = (data[1] << 16) | (data[2] << 8) | data[3]
            length = len(data) - 5
            result = [0, 0, 0, 0, 0]
            for i in range(length):
                result.append(self.memory[addr + i] if addr + i < CHIP_CAPACITY else 0xFF)
            return result

        # 未知コマンド
        return [0] * len(data)


# ════════════════════════════════════════════════════════════════════════════════
# W25Q シリーズ ドライバ (クラス名は歴史的経緯で W25Q128 のまま)
# ════════════════════════════════════════════════════════════════════════════════

class W25Q128:
    """
    W25Q シリーズ NOR Flash 操作クラス (Pre-MVP は W25Q64、 当初は W25Q128 想定)。
    クラス名は歴史的経緯で W25Q128 のまま (実体は EXPECTED_CHIP_NAME 定数で切替)。

    低レベルコマンドを隠蔽し、read/write/erase/verify を提供する。
    """

    def __init__(self, backend: SpiBackend):
        self.spi = backend

    def close(self) -> None:
        self.spi.close()

    # ── 低レベル ──

    def read_jedec_id(self) -> tuple:
        """JEDEC ID (製造者, メモリタイプ, 容量ID) を返す"""
        resp = self.spi.xfer([CMD_READ_ID, 0, 0, 0])
        return (resp[1], resp[2], resp[3])

    def verify_chip(self) -> bool:
        """チップが想定の W25Q シリーズ (EXPECTED_CHIP_NAME) か検証"""
        mfr, mem_type, cap = self.read_jedec_id()
        if mfr != EXPECTED_MANUFACTURER:
            print(f"⚠️  Unexpected manufacturer: 0x{mfr:02X} (expected 0x{EXPECTED_MANUFACTURER:02X})")
            return False
        if cap != EXPECTED_CAPACITY_ID:
            print(f"⚠️  Unexpected capacity ID: 0x{cap:02X} (expected 0x{EXPECTED_CAPACITY_ID:02X} = {EXPECTED_CHIP_NAME})")
            return False
        return True

    def read_status(self) -> int:
        resp = self.spi.xfer([CMD_READ_STATUS_1, 0])
        return resp[1]

    def is_busy(self) -> bool:
        return bool(self.read_status() & STATUS_BUSY)

    def wait_ready(self, timeout_s: float = 30.0) -> None:
        deadline = time.time() + timeout_s
        while self.is_busy():
            if time.time() > deadline:
                raise TimeoutError("Flash busy timeout")
            time.sleep(0.001)

    def write_enable(self) -> None:
        self.spi.xfer([CMD_WRITE_ENABLE])

    # ── 中レベル ──

    def read(self, addr: int, length: int) -> bytes:
        """任意バイト数を読み出す"""
        cmd = [CMD_READ_DATA, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF]
        cmd.extend([0] * length)
        resp = self.spi.xfer(cmd)
        return bytes(resp[4:])

    def erase_sector(self, addr: int) -> None:
        """4KB セクタ消去（addrを含むセクタ全体）"""
        self.wait_ready()
        self.write_enable()
        self.spi.xfer([CMD_SECTOR_ERASE_4K,
                       (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF])
        self.wait_ready()

    def erase_range(self, addr: int, length: int, progress=None) -> None:
        """指定範囲を4KBセクタ単位で消去"""
        start_sector = addr & ~(SECTOR_SIZE - 1)
        end_addr = addr + length
        sectors = []
        cur = start_sector
        while cur < end_addr:
            sectors.append(cur)
            cur += SECTOR_SIZE

        for i, sa in enumerate(sectors):
            self.erase_sector(sa)
            if progress:
                progress(i + 1, len(sectors))

    def write_page(self, addr: int, data: bytes) -> None:
        """256バイトページに書き込む（ページ境界を跨がないこと）"""
        if len(data) > PAGE_SIZE:
            raise ValueError(f"Page write > {PAGE_SIZE} bytes: {len(data)}")
        self.wait_ready()
        self.write_enable()
        cmd = [CMD_PAGE_PROGRAM,
               (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF]
        cmd.extend(list(data))
        self.spi.xfer(cmd)
        self.wait_ready()

    def write_bytes(self, addr: int, data: bytes, progress=None) -> None:
        """任意バイト列を書き込む（ページ境界を自動で跨ぐ）"""
        offset = 0
        total = len(data)
        while offset < total:
            page_addr = addr + offset
            page_offset = page_addr % PAGE_SIZE
            chunk_size = min(PAGE_SIZE - page_offset, total - offset)
            chunk = data[offset:offset + chunk_size]
            self.write_page(page_addr, chunk)
            offset += chunk_size
            if progress:
                progress(offset, total)

    def verify(self, addr: int, expected: bytes) -> bool:
        """読み出して一致を検証"""
        actual = self.read(addr, len(expected))
        return actual == expected


# ════════════════════════════════════════════════════════════════════════════════
# 高レベル操作
# ════════════════════════════════════════════════════════════════════════════════

def flash_cortex(
    data: bytes,
    flash: W25Q128,
    addr: int = 0,
    verify: bool = True,
) -> dict:
    """
    CORTEX.bin を W25Q シリーズ Flash に書き込む統合フロー。

    Steps:
      1. チップID検証
      2. 書き込み範囲を消去
      3. ページ単位で書き込み
      4. ベリファイ（SHA256比較）
    """
    print(f"─── CORTEX Flash Write ({len(data)} bytes at 0x{addr:06X}) ───")

    # 1. チップ検証
    if not flash.verify_chip():
        raise RuntimeError("Chip verification failed — abort")
    mfr, mem, cap = flash.read_jedec_id()
    print(f"  Chip: {EXPECTED_CHIP_NAME} (JEDEC {mfr:02X}-{mem:02X}-{cap:02X})  ✅")

    # 2. 消去
    def erase_progress(done, total):
        print(f"\r  Erasing:  [{done}/{total}] sectors", end="", flush=True)

    print(f"  Erasing {((len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE)} sectors...")
    flash.erase_range(addr, len(data), progress=erase_progress)
    print("  ✅")

    # 3. 書き込み
    def write_progress(done, total):
        pct = 100 * done / total
        print(f"\r  Writing:  [{done}/{total}] bytes ({pct:.1f}%)", end="", flush=True)

    print(f"  Writing {len(data)} bytes...")
    flash.write_bytes(addr, data, progress=write_progress)
    print("  ✅")

    # 4. ベリファイ
    result = {
        "bytes_written": len(data),
        "addr": addr,
        "sha256_input": hashlib.sha256(data).hexdigest(),
        "verified": False,
    }
    if verify:
        print("  Verifying...")
        readback = flash.read(addr, len(data))
        sha_readback = hashlib.sha256(readback).hexdigest()
        result["sha256_readback"] = sha_readback
        if readback == data:
            print(f"  ✅ Verified (SHA256: {sha_readback[:16]}...)")
            result["verified"] = True
        else:
            # 最初の不一致位置を探す
            mismatch_idx = next(
                (i for i in range(len(data)) if i >= len(readback) or readback[i] != data[i]),
                -1,
            )
            print(f"  ❌ Verify failed at offset {mismatch_idx}")
            result["verified"] = False

    return result


def verify_only(data: bytes, flash: W25Q128, addr: int = 0) -> bool:
    """既存の書き込みを検証するだけ"""
    print(f"─── CORTEX Verify ({len(data)} bytes at 0x{addr:06X}) ───")
    if not flash.verify_chip():
        return False
    readback = flash.read(addr, len(data))
    match = readback == data
    if match:
        print(f"  ✅ SHA256 match: {hashlib.sha256(data).hexdigest()[:16]}...")
    else:
        print(f"  ❌ Mismatch")
    return match


def dump_flash(flash: W25Q128, out_path: str, addr: int = 0, size: int = 16384) -> None:
    """Flash内容をファイルに読み出す"""
    print(f"─── Reading 0x{addr:06X}..0x{addr+size:06X} ({size} bytes) ───")
    if not flash.verify_chip():
        return
    data = flash.read(addr, size)
    Path(out_path).write_bytes(data)
    print(f"  ✅ Dumped to {out_path} (SHA256: {hashlib.sha256(data).hexdigest()[:16]}...)")


# ════════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════════

def open_backend(
    simulate: bool,
    bus: int = 0,
    device: int = 0,
    speed_hz: int = 10_000_000,
) -> SpiBackend:
    if simulate:
        print("🧪 Using SIMULATED backend (no real hardware)")
        return SimulatedBackend()
    try:
        print(f"🔌 Opening /dev/spidev{bus}.{device} (max {speed_hz/1e6:.0f} MHz)")
        return SpidevBackend(bus=bus, device=device, speed_hz=speed_hz)
    except RuntimeError as e:
        print(f"⚠️  {e}")
        print("ℹ️  Falling back to --simulate mode. Use --simulate to silence this.")
        return SimulatedBackend()


def _env_int(name: str, default: int) -> int:
    """環境変数から整数を読む。 不正値や未設定時は default。"""
    import os

    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw, 0)  # 0x prefix 等も解釈
    except ValueError:
        print(f"⚠️  {name}={raw!r} は整数として解釈できないので無視 (default={default})")
        return default


def main(argv: list[str] | None = None) -> int:
    default_bus = _env_int("GPP_SPI_BUS", 0)
    default_device = _env_int("GPP_SPI_DEVICE", 0)
    default_speed = _env_int("GPP_SPI_SPEED_HZ", 10_000_000)

    parser = argparse.ArgumentParser(
        description="W25Q series SPI Flash writer for Ghost-Printer CORTEX.bin (Pre-MVP: W25Q64)",
        epilog=(
            "examples:\n"
            "  # Pi 5 / Pi 4 (SPI0):\n"
            "  python flash_cortex.py data/CORTEX.bin --bus 0\n"
            "  # Orange Pi 3B (SPI3 M0):\n"
            "  python flash_cortex.py data/CORTEX.bin --bus 3\n"
            "  # 配線確認 (JEDEC ID のみ):\n"
            "  python flash_cortex.py --read-id --bus 3\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", nargs="?", help="CORTEX.bin (書き込みまたは検証対象)")
    parser.add_argument("--addr", type=lambda x: int(x, 0), default=0,
                        help="Flash上の書き込み/読み出しアドレス (default: 0x000000)")
    parser.add_argument("--verify", action="store_true",
                        help="書き込みせず、既存内容との比較のみ行う")
    parser.add_argument("--read", action="store_true",
                        help="Flashの内容を読み出してファイルに保存する")
    parser.add_argument("--read-id", action="store_true",
                        help="JEDEC ID だけ取得して終了 (配線確認用)")
    parser.add_argument("--out", type=str, help="--read 時の出力先ファイル")
    parser.add_argument("--size", type=int, default=16384,
                        help="--read 時のサイズ (default: 16384)")
    parser.add_argument("--simulate", action="store_true",
                        help="シミュレータモード（spidev不要）")
    parser.add_argument("--no-verify", action="store_true",
                        help="書き込み後のベリファイをスキップ")

    # SPI バス指定 (Pi 5 = 0, OPi 3B = 3)
    parser.add_argument(
        "--bus",
        type=lambda x: int(x, 0),
        default=default_bus,
        help=f"SPI bus 番号 (default: {default_bus} / env GPP_SPI_BUS)",
    )
    parser.add_argument(
        "--device",
        type=lambda x: int(x, 0),
        default=default_device,
        help=f"SPI device 番号 (default: {default_device} / env GPP_SPI_DEVICE)",
    )
    parser.add_argument(
        "--speed-hz",
        type=lambda x: int(x, 0),
        default=default_speed,
        help=f"SPI クロック (default: {default_speed} / env GPP_SPI_SPEED_HZ)",
    )

    args = parser.parse_args(argv)

    backend = open_backend(
        args.simulate,
        bus=args.bus,
        device=args.device,
        speed_hz=args.speed_hz,
    )
    flash = W25Q128(backend)

    try:
        # 配線確認: JEDEC ID だけ
        if args.read_id:
            mfr, mem_type, cap = flash.read_jedec_id()
            print(f"JEDEC ID: 0x{mfr:02X} 0x{mem_type:02X} 0x{cap:02X}")
            mfr_name = "Winbond" if mfr == EXPECTED_MANUFACTURER else f"unknown (0x{mfr:02X})"
            cap_name = {
                0x14: "1MB (W25Q08)",
                0x15: "2MB (W25Q16)",
                0x16: "4MB (W25Q32)",
                0x17: "8MB (W25Q64)",
                0x18: "16MB (W25Q128)",
                0x19: "32MB (W25Q256)",
            }.get(cap, f"unknown (0x{cap:02X})")
            print(f"  Manufacturer: {mfr_name}")
            print(f"  Capacity:     {cap_name}")
            if mfr == EXPECTED_MANUFACTURER and cap == EXPECTED_CAPACITY_ID:
                print(f"  ✅ Expected {EXPECTED_CHIP_NAME} detected")
                return 0
            else:
                print(f"  ⚠️  Not the expected {EXPECTED_CHIP_NAME}. 配線/電源/プルアップを確認")
                return 1

        if args.read:
            if not args.out:
                parser.error("--read には --out が必要です")
            dump_flash(flash, args.out, addr=args.addr, size=args.size)
            return 0

        if not args.file:
            parser.error("CORTEX.binのパスが必要です")

        data = Path(args.file).read_bytes()
        print(f"📥 Loaded {args.file}: {len(data)} bytes "
              f"(SHA256: {hashlib.sha256(data).hexdigest()[:16]}...)")

        if args.verify:
            ok = verify_only(data, flash, addr=args.addr)
            return 0 if ok else 1

        result = flash_cortex(data, flash, addr=args.addr, verify=not args.no_verify)
        print()
        print(f"─── Result ───")
        print(f"  bytes_written: {result['bytes_written']}")
        print(f"  addr:          0x{result['addr']:06X}")
        print(f"  sha256(input): {result['sha256_input'][:32]}...")
        if "sha256_readback" in result:
            print(f"  sha256(read):  {result['sha256_readback'][:32]}...")
        print(f"  verified:      {'✅' if result['verified'] else '❌'}")
        return 0 if result["verified"] or args.no_verify else 1

    finally:
        flash.close()


if __name__ == "__main__":
    sys.exit(main())
