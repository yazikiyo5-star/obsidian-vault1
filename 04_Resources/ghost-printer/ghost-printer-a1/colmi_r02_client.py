"""
Ghost-Printer B4 — COLMI R02 BLE クライアント

仕様: specs/b4_colmi_r02_protocol.md

Backend 抽象 + ColmiClient (高レベル async API)。 実 BLE は bleak を遅延 import、
SimulatedBackend で Mac/Linux の実機なし開発が可能。

使い方::

    import asyncio
    from colmi_r02_client import ColmiClient, SimulatedBackend

    async def main():
        client = ColmiClient(SimulatedBackend())
        await client.connect()
        bat = await client.get_battery()
        print(bat)
        samples = await client.stream_heart_rate(10.0)
        await client.disconnect()

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

from colmi_r02_protocol import (
    Cmd,
    HeartRateSample,
    BatteryStatus,
    SleepSummary,
    SERVICE_UUID,
    TX_CHAR_UUID,
    RX_CHAR_UUID,
    build_battery_request,
    build_get_sleep,
    build_packet,
    build_ping,
    build_set_time,
    build_start_realtime_hr,
    build_stop_realtime_hr,
    parse_battery_response,
    parse_packet,
    parse_realtime_hr_packet,
    parse_sleep_response,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Backend ABC
# ════════════════════════════════════════════════════════════════════════════


NotifyHandler = Callable[[bytes], None]


class ColmiBackend(ABC):
    """BLE バックエンドの抽象。 実 BLE と擬似 BLE を切替可能に"""

    @abstractmethod
    async def scan(self, timeout_s: float = 5.0) -> list[str]:
        """COLMI R02 系のリングをスキャン。 アドレスのリストを返す"""

    @abstractmethod
    async def connect(self, address: str) -> bool: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def write(self, packet: bytes) -> None: ...

    @abstractmethod
    async def subscribe(self, handler: NotifyHandler) -> None:
        """notify チャネルにハンドラを登録"""

    @property
    @abstractmethod
    def connected(self) -> bool: ...


# ════════════════════════════════════════════════════════════════════════════
# BleakBackend (実 BLE)
# ════════════════════════════════════════════════════════════════════════════


class BleakBackend(ColmiBackend):
    """実 BLE 実装。 bleak ライブラリは遅延 import するので、 インストールされて
    いない環境でも import エラーにならない。 connect 時に必要になる。
    """

    def __init__(self, *, name_filter: str = "R02"):
        self.name_filter = name_filter
        self._client = None
        self._handler: Optional[NotifyHandler] = None

    def _require_bleak(self):
        try:
            import bleak  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "bleak is required for BleakBackend. Install with: pip install bleak"
            ) from exc

    async def scan(self, timeout_s: float = 5.0) -> list[str]:
        self._require_bleak()
        from bleak import BleakScanner

        devices = await BleakScanner.discover(timeout=timeout_s)
        addresses = []
        for d in devices:
            name = (d.name or "").upper()
            if self.name_filter.upper() in name:
                addresses.append(d.address)
        return addresses

    async def connect(self, address: str) -> bool:
        self._require_bleak()
        from bleak import BleakClient

        self._client = BleakClient(address)
        await self._client.connect()
        return self._client.is_connected

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def write(self, packet: bytes) -> None:
        if self._client is None:
            raise RuntimeError("not connected")
        await self._client.write_gatt_char(TX_CHAR_UUID, packet)

    async def subscribe(self, handler: NotifyHandler) -> None:
        if self._client is None:
            raise RuntimeError("not connected")
        self._handler = handler

        def _bleak_callback(sender, data):
            handler(bytes(data))

        await self._client.start_notify(RX_CHAR_UUID, _bleak_callback)

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected


# ════════════════════════════════════════════════════════════════════════════
# SimulatedBackend (擬似 BLE)
# ════════════════════════════════════════════════════════════════════════════


class SimulatedBackend(ColmiBackend):
    """In-memory simulator。 リアル風の応答を合成する。

    使い道:
      - 実機リング未到着でも開発可能
      - CI/CD で BLE スタック不要
      - 実機が壊れたとき / 電池切れの代替

    HR ストリームは sin 波 + ジッターで擬似生成、 battery は固定値、
    sleep は固定サマリ。
    """

    FAKE_ADDRESS = "00:11:22:33:44:55"

    def __init__(
        self,
        *,
        battery_level: int = 85,
        battery_charging: bool = False,
        hr_baseline_bpm: int = 70,
        hr_amplitude: float = 8.0,
        hr_period_s: float = 30.0,
        hr_interval_s: float = 1.0,
        seed: int | None = 42,
    ):
        self.battery_level = battery_level
        self.battery_charging = battery_charging
        self.hr_baseline_bpm = hr_baseline_bpm
        self.hr_amplitude = hr_amplitude
        self.hr_period_s = hr_period_s
        self.hr_interval_s = hr_interval_s
        self._rng = random.Random(seed)
        self._connected = False
        self._handler: Optional[NotifyHandler] = None
        self._stream_task: Optional[asyncio.Task] = None
        self._stream_started_at: float = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    async def scan(self, timeout_s: float = 5.0) -> list[str]:
        # わずかに待つ (現実の scan の所要時間を擬似)
        await asyncio.sleep(min(0.05, timeout_s))
        return [self.FAKE_ADDRESS]

    async def connect(self, address: str) -> bool:
        await asyncio.sleep(0.01)
        if address != self.FAKE_ADDRESS:
            return False
        self._connected = True
        return True

    async def disconnect(self) -> None:
        if self._stream_task is not None:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stream_task = None
        self._connected = False

    async def subscribe(self, handler: NotifyHandler) -> None:
        if not self._connected:
            raise RuntimeError("not connected")
        self._handler = handler

    async def write(self, packet: bytes) -> None:
        if not self._connected:
            raise RuntimeError("not connected")
        try:
            cmd, payload = parse_packet(packet)
        except ValueError:
            return  # 不正パケットは黙って捨てる

        # コマンドに応じた擬似応答
        if cmd == Cmd.BATTERY:
            asyncio.get_event_loop().call_later(
                0.02, self._emit_battery_response
            )
        elif cmd == Cmd.START_REALTIME_HR:
            self._start_hr_stream()
        elif cmd == Cmd.STOP_REALTIME_HR:
            self._stop_hr_stream()
        elif cmd == Cmd.GET_SLEEP:
            asyncio.get_event_loop().call_later(0.02, self._emit_sleep_response)
        elif cmd == Cmd.PING:
            # echo back
            asyncio.get_event_loop().call_later(0.01, self._emit_ping_response)

    # ── 擬似応答エミッタ ──

    def _emit_battery_response(self):
        if self._handler is None:
            return
        payload = bytes([self.battery_level, 1 if self.battery_charging else 0])
        packet = build_packet(Cmd.BATTERY, payload)
        self._handler(packet)

    def _emit_sleep_response(self):
        if self._handler is None:
            return
        # 擬似値: 7時間=420分、 深い120分、 浅い280分、 quality 80
        payload = (
            (420).to_bytes(2, "little")
            + (120).to_bytes(2, "little")
            + (280).to_bytes(2, "little")
            + bytes([80])
        )
        packet = build_packet(Cmd.GET_SLEEP, payload)
        self._handler(packet)

    def _emit_ping_response(self):
        if self._handler is None:
            return
        self._handler(build_packet(Cmd.PING))

    # ── HR ストリーム ──

    def _start_hr_stream(self):
        if self._stream_task is not None:
            return
        self._stream_started_at = time.time()
        self._stream_task = asyncio.get_event_loop().create_task(self._hr_stream_loop())

    def _stop_hr_stream(self):
        if self._stream_task is not None:
            self._stream_task.cancel()
            self._stream_task = None

    async def _hr_stream_loop(self):
        try:
            while self._connected:
                if self._handler is None:
                    await asyncio.sleep(self.hr_interval_s)
                    continue
                t = time.time() - self._stream_started_at
                bpm = int(
                    self.hr_baseline_bpm
                    + self.hr_amplitude * math.sin(t * 2 * math.pi / self.hr_period_s)
                    + self._rng.uniform(-2, 2)
                )
                bpm = max(40, min(200, bpm))
                # RR interval = 60000 / bpm + ジッター (ms)
                rri_ms = int(60000 / max(bpm, 1) + self._rng.uniform(-30, 30))
                rri_ms = max(200, min(2000, rri_ms))
                ts_s = int(time.time())
                payload = (
                    bpm.to_bytes(2, "little")
                    + rri_ms.to_bytes(2, "little")
                    + ts_s.to_bytes(4, "little")
                )
                self._handler(build_packet(Cmd.START_REALTIME_HR, payload))
                await asyncio.sleep(self.hr_interval_s)
        except asyncio.CancelledError:
            return


# ════════════════════════════════════════════════════════════════════════════
# ColmiClient — 高レベル API
# ════════════════════════════════════════════════════════════════════════════


class ColmiClient:
    """COLMI R02 リングへのクライアント。 Backend に対する高レベルラッパ。"""

    def __init__(self, backend: ColmiBackend):
        self.backend = backend
        self._battery: Optional[BatteryStatus] = None
        self._sleep: Optional[SleepSummary] = None
        self._hr_samples: list[HeartRateSample] = []
        self._battery_event = asyncio.Event()
        self._sleep_event = asyncio.Event()
        self._streaming = False

    async def connect(self, address: Optional[str] = None) -> str:
        """スキャン + 接続 + 通知購読を一括"""
        if address is None:
            addresses = await self.backend.scan(timeout_s=5.0)
            if not addresses:
                raise RuntimeError("no COLMI device found")
            address = addresses[0]
        ok = await self.backend.connect(address)
        if not ok:
            raise RuntimeError(f"connect failed: {address}")
        await self.backend.subscribe(self._on_packet)
        logger.info(f"connected to {address}")
        return address

    async def disconnect(self) -> None:
        await self.backend.disconnect()

    # ── 通知ハンドラ ──

    def _on_packet(self, packet: bytes) -> None:
        try:
            cmd, payload = parse_packet(packet)
        except ValueError:
            return

        try:
            if cmd == Cmd.BATTERY:
                self._battery = parse_battery_response(payload)
                self._battery_event.set()
            elif cmd == Cmd.START_REALTIME_HR:
                if self._streaming:
                    sample = parse_realtime_hr_packet(payload)
                    self._hr_samples.append(sample)
            elif cmd == Cmd.GET_SLEEP:
                self._sleep = parse_sleep_response(payload)
                self._sleep_event.set()
        except ValueError as e:
            logger.warning(f"parse failed for cmd={cmd}: {e}")

    # ── 公開 API ──

    async def get_battery(self, timeout_s: float = 3.0) -> BatteryStatus:
        self._battery_event.clear()
        await self.backend.write(build_battery_request())
        try:
            await asyncio.wait_for(self._battery_event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("battery response timeout") from exc
        if self._battery is None:
            raise RuntimeError("battery state not populated")
        return self._battery

    async def get_sleep_summary(self, timeout_s: float = 3.0) -> SleepSummary:
        self._sleep_event.clear()
        await self.backend.write(build_get_sleep())
        try:
            await asyncio.wait_for(self._sleep_event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("sleep response timeout") from exc
        if self._sleep is None:
            raise RuntimeError("sleep state not populated")
        return self._sleep

    async def stream_heart_rate(self, duration_s: float) -> list[HeartRateSample]:
        """N 秒間 HR ストリーミングして全サンプルを返す"""
        self._hr_samples = []
        self._streaming = True
        await self.backend.write(build_start_realtime_hr())
        try:
            await asyncio.sleep(duration_s)
        finally:
            self._streaming = False
            await self.backend.write(build_stop_realtime_hr())
        return list(self._hr_samples)

    async def set_time(self, ts_unix_s: Optional[int] = None) -> None:
        if ts_unix_s is None:
            ts_unix_s = int(time.time())
        await self.backend.write(build_set_time(ts_unix_s))

    async def ping(self) -> None:
        await self.backend.write(build_ping())
