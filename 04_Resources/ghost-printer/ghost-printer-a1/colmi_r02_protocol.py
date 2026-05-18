"""
Ghost-Printer B4 — COLMI R02 BLE プロトコル層

仕様: specs/b4_colmi_r02_protocol.md

純プロトコル層 (BLE 依存なし、 sync)。 16 バイトパケットの組立/分解、 主要
コマンド builder、 応答 parser を提供する。

参考実装:
  - tahnok/colmi_r02_client (Python OSS)
  - ATC_RF03_Ring (firmware リバースエンジ)
  - Gadgetbridge (Java OSS)

注意: コマンド ID は OSS から推定した値。 実機到着後に scan + パケットダンプで
要検証 (§1.3 of spec)。 検証作業は b4_colmi_r02_protocol.md §8 のステップに従う。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


# ════════════════════════════════════════════════════════════════════════════
# 定数
# ════════════════════════════════════════════════════════════════════════════

PACKET_SIZE = 16
PAYLOAD_SIZE = 14  # 16 - cmd(1) - chk(1)

# BLE GATT
SERVICE_UUID = "6E40FFF0-B5A3-F393-E0A9-E50E24DCCA9E"
TX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # ホスト → リング (write)
RX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # リング → ホスト (notify)


class Cmd(IntEnum):
    """コマンド ID (OSS 推定値、 実機要検証)"""

    SET_TIME = 0x01
    BATTERY = 0x03
    REBOOT = 0x08
    GET_HR_HISTORY = 0x15
    START_REALTIME_HR = 0x21
    STOP_REALTIME_HR = 0x22
    GET_SLEEP = 0x27
    PING = 0xFF


# ════════════════════════════════════════════════════════════════════════════
# パケット組立 / 分解
# ════════════════════════════════════════════════════════════════════════════


def build_packet(cmd: int, payload: bytes = b"") -> bytes:
    """16 バイトパケットを組み立てる。

    Args:
        cmd: コマンド ID (0..255)
        payload: 0..14 バイトのペイロード。 残余は 0 埋め。

    Returns:
        16 バイトのパケット ([cmd][payload + zero pad][checksum])
    """
    if not (0 <= cmd <= 0xFF):
        raise ValueError(f"cmd out of range: {cmd}")
    if len(payload) > PAYLOAD_SIZE:
        raise ValueError(
            f"payload too long: {len(payload)} > {PAYLOAD_SIZE}"
        )
    body = bytes([cmd]) + payload + bytes(PAYLOAD_SIZE - len(payload))
    chk = sum(body) & 0xFF
    return body + bytes([chk])


def parse_packet(packet: bytes) -> tuple[int, bytes]:
    """16 バイトパケットをチェックサム検証して (cmd, payload) に分解する。

    Raises:
        ValueError: サイズ違い / チェックサム不一致
    """
    if len(packet) != PACKET_SIZE:
        raise ValueError(f"packet must be {PACKET_SIZE} bytes, got {len(packet)}")
    expected = sum(packet[:15]) & 0xFF
    if packet[15] != expected:
        raise ValueError(
            f"checksum mismatch: stored=0x{packet[15]:02X} expected=0x{expected:02X}"
        )
    return packet[0], bytes(packet[1:15])


# ════════════════════════════════════════════════════════════════════════════
# コマンド builder
# ════════════════════════════════════════════════════════════════════════════


def build_set_time(ts_unix_s: int) -> bytes:
    """RTC 同期コマンド。 4 バイト little-endian で時刻を渡す。"""
    if ts_unix_s < 0 or ts_unix_s > 0xFFFFFFFF:
        raise ValueError("ts_unix_s out of u32 range")
    return build_packet(Cmd.SET_TIME, ts_unix_s.to_bytes(4, "little"))


def build_battery_request() -> bytes:
    return build_packet(Cmd.BATTERY)


def build_start_realtime_hr() -> bytes:
    return build_packet(Cmd.START_REALTIME_HR, bytes([0x01]))


def build_stop_realtime_hr() -> bytes:
    return build_packet(Cmd.STOP_REALTIME_HR, bytes([0x00]))


def build_get_sleep() -> bytes:
    return build_packet(Cmd.GET_SLEEP)


def build_get_hr_history(count: int = 10) -> bytes:
    if not (1 <= count <= 255):
        raise ValueError("count out of range")
    return build_packet(Cmd.GET_HR_HISTORY, bytes([count]))


def build_ping() -> bytes:
    return build_packet(Cmd.PING)


def build_reboot() -> bytes:
    """リング再起動 (危険)"""
    return build_packet(Cmd.REBOOT)


# ════════════════════════════════════════════════════════════════════════════
# 応答 parser (型と検証)
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class BatteryStatus:
    level: int       # 0..100
    charging: bool

    def to_dict(self) -> dict:
        return {"level": self.level, "charging": self.charging}


@dataclass
class HeartRateSample:
    timestamp_ms: int
    bpm: int
    rri_ms: int      # R-R interval (HRV 計算で使う)

    def to_dict(self) -> dict:
        return {
            "timestamp_ms": self.timestamp_ms,
            "bpm": self.bpm,
            "rri_ms": self.rri_ms,
        }


@dataclass
class SleepSummary:
    total_min: int
    deep_min: int
    light_min: int
    quality: int     # 0..100

    def to_dict(self) -> dict:
        return {
            "total_min": self.total_min,
            "deep_min": self.deep_min,
            "light_min": self.light_min,
            "quality": self.quality,
        }


def parse_battery_response(payload: bytes) -> BatteryStatus:
    """payload[0] = level (0..100), payload[1] = charging flag"""
    if len(payload) < 2:
        raise ValueError("battery payload too short")
    level = payload[0]
    if level > 100:
        level = 100
    charging = bool(payload[1])
    return BatteryStatus(level=level, charging=charging)


def parse_realtime_hr_packet(payload: bytes) -> HeartRateSample:
    """payload:
    bytes[0:2]  = bpm  (LE u16)
    bytes[2:4]  = rri  (LE u16, ms)
    bytes[4:8]  = ts_s (LE u32)
    """
    if len(payload) < 8:
        raise ValueError("hr payload too short")
    bpm = int.from_bytes(payload[0:2], "little")
    rri_ms = int.from_bytes(payload[2:4], "little")
    ts_s = int.from_bytes(payload[4:8], "little")
    # 不正値の防御
    if bpm > 250:
        bpm = 0
    if rri_ms > 5000:
        rri_ms = 0
    return HeartRateSample(
        timestamp_ms=ts_s * 1000,
        bpm=bpm,
        rri_ms=rri_ms,
    )


def parse_sleep_response(payload: bytes) -> SleepSummary:
    """payload:
    bytes[0:2]  = total_min (LE u16)
    bytes[2:4]  = deep_min  (LE u16)
    bytes[4:6]  = light_min (LE u16)
    bytes[6]    = quality   (0..100)
    """
    if len(payload) < 7:
        raise ValueError("sleep payload too short")
    total = int.from_bytes(payload[0:2], "little")
    deep = int.from_bytes(payload[2:4], "little")
    light = int.from_bytes(payload[4:6], "little")
    quality = min(100, payload[6])
    return SleepSummary(
        total_min=total,
        deep_min=deep,
        light_min=light,
        quality=quality,
    )


# ════════════════════════════════════════════════════════════════════════════
# 安全な dispatch (cmd 不明時にも落ちない)
# ════════════════════════════════════════════════════════════════════════════


def dispatch_response(packet: bytes) -> Optional[dict]:
    """生パケットを解析して、 既知コマンドなら型化された dict を返す。
    未知コマンドや破損パケットは None。

    使い方の例:
        bleak の notify ハンドラから:
        info = dispatch_response(raw_packet)
        if info is None: return
        if info["kind"] == "heart_rate": ...
    """
    try:
        cmd, payload = parse_packet(packet)
    except ValueError:
        return None

    try:
        if cmd == Cmd.BATTERY:
            return {"kind": "battery", "data": parse_battery_response(payload).to_dict()}
        if cmd == Cmd.START_REALTIME_HR:
            return {"kind": "heart_rate", "data": parse_realtime_hr_packet(payload).to_dict()}
        if cmd == Cmd.GET_SLEEP:
            return {"kind": "sleep", "data": parse_sleep_response(payload).to_dict()}
    except ValueError:
        return None

    return {"kind": "unknown", "cmd": cmd, "payload": payload.hex()}
