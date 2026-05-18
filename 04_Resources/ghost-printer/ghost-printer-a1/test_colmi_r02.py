"""
Ghost-Printer B4 — COLMI R02 BLE プロトコル + クライアント + HRV のテスト

実 BLE は触らず、 SimulatedBackend のみで完結する。
"""

from __future__ import annotations

import asyncio

import pytest

import colmi_r02_protocol as proto
from colmi_r02_client import ColmiClient, SimulatedBackend
from hrv_calculator import (
    HrvMetrics,
    compute_hrv,
    filter_rri,
    mean_hr,
    pnn50,
    rmssd,
    sdnn,
    stress_score,
)


# ════════════════════════════════════════════════════════════════════════════
# プロトコル: パケット組立/分解
# ════════════════════════════════════════════════════════════════════════════


class TestPacket:
    def test_empty_payload_round_trip(self):
        pkt = proto.build_packet(0x42)
        assert len(pkt) == 16
        cmd, payload = proto.parse_packet(pkt)
        assert cmd == 0x42
        assert payload == bytes(14)

    def test_full_payload_round_trip(self):
        payload = bytes(range(14))
        pkt = proto.build_packet(0x21, payload)
        cmd, decoded = proto.parse_packet(pkt)
        assert cmd == 0x21
        assert decoded == payload

    def test_partial_payload_zero_padded(self):
        pkt = proto.build_packet(0x03, b"\x55\x01")
        cmd, payload = proto.parse_packet(pkt)
        assert cmd == 0x03
        assert payload == b"\x55\x01" + bytes(12)

    def test_payload_too_long_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            proto.build_packet(0x01, bytes(15))

    def test_cmd_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="out of range"):
            proto.build_packet(0x100)

    def test_bad_size_rejected(self):
        with pytest.raises(ValueError, match="must be 16 bytes"):
            proto.parse_packet(b"\x01\x02\x03")

    def test_bad_checksum_rejected(self):
        pkt = bytearray(proto.build_packet(0x01, b"hi"))
        pkt[15] ^= 0x01
        with pytest.raises(ValueError, match="checksum mismatch"):
            proto.parse_packet(bytes(pkt))


# ════════════════════════════════════════════════════════════════════════════
# プロトコル: コマンド builder
# ════════════════════════════════════════════════════════════════════════════


class TestBuilders:
    def test_battery_request(self):
        pkt = proto.build_battery_request()
        cmd, _ = proto.parse_packet(pkt)
        assert cmd == proto.Cmd.BATTERY

    def test_set_time(self):
        pkt = proto.build_set_time(1_700_000_000)
        cmd, payload = proto.parse_packet(pkt)
        assert cmd == proto.Cmd.SET_TIME
        ts = int.from_bytes(payload[0:4], "little")
        assert ts == 1_700_000_000

    def test_set_time_out_of_range(self):
        with pytest.raises(ValueError):
            proto.build_set_time(-1)

    def test_start_and_stop_realtime_hr(self):
        start = proto.build_start_realtime_hr()
        stop = proto.build_stop_realtime_hr()
        assert proto.parse_packet(start)[0] == proto.Cmd.START_REALTIME_HR
        assert proto.parse_packet(stop)[0] == proto.Cmd.STOP_REALTIME_HR

    def test_get_hr_history_count_validated(self):
        with pytest.raises(ValueError):
            proto.build_get_hr_history(0)
        with pytest.raises(ValueError):
            proto.build_get_hr_history(256)
        ok = proto.build_get_hr_history(10)
        cmd, payload = proto.parse_packet(ok)
        assert cmd == proto.Cmd.GET_HR_HISTORY
        assert payload[0] == 10


# ════════════════════════════════════════════════════════════════════════════
# プロトコル: 応答 parser
# ════════════════════════════════════════════════════════════════════════════


class TestParsers:
    def test_battery_response(self):
        bs = proto.parse_battery_response(b"\x55\x01" + bytes(12))
        assert bs.level == 85
        assert bs.charging is True

    def test_battery_clamped_to_100(self):
        bs = proto.parse_battery_response(b"\xFF\x00" + bytes(12))
        assert bs.level == 100

    def test_realtime_hr_packet(self):
        # bpm=72, rri=830ms, ts=1700000000s
        payload = (
            (72).to_bytes(2, "little")
            + (830).to_bytes(2, "little")
            + (1_700_000_000).to_bytes(4, "little")
            + bytes(6)
        )
        sample = proto.parse_realtime_hr_packet(payload)
        assert sample.bpm == 72
        assert sample.rri_ms == 830
        assert sample.timestamp_ms == 1_700_000_000_000

    def test_realtime_hr_filters_garbage_values(self):
        payload = (
            (300).to_bytes(2, "little")     # > 250 bpm = 不正
            + (10000).to_bytes(2, "little")  # > 5000 ms = 不正
            + (0).to_bytes(4, "little")
            + bytes(6)
        )
        sample = proto.parse_realtime_hr_packet(payload)
        assert sample.bpm == 0
        assert sample.rri_ms == 0

    def test_sleep_response(self):
        payload = (
            (420).to_bytes(2, "little")
            + (120).to_bytes(2, "little")
            + (280).to_bytes(2, "little")
            + bytes([80])
            + bytes(7)
        )
        s = proto.parse_sleep_response(payload)
        assert s.total_min == 420
        assert s.deep_min == 120
        assert s.light_min == 280
        assert s.quality == 80

    def test_dispatch_unknown_cmd(self):
        pkt = proto.build_packet(0x99, b"\x01\x02")
        info = proto.dispatch_response(pkt)
        assert info["kind"] == "unknown"
        assert info["cmd"] == 0x99

    def test_dispatch_battery(self):
        pkt = proto.build_packet(proto.Cmd.BATTERY, b"\x55\x01")
        info = proto.dispatch_response(pkt)
        assert info["kind"] == "battery"
        assert info["data"]["level"] == 85

    def test_dispatch_corrupted_returns_none(self):
        pkt = bytearray(proto.build_packet(proto.Cmd.BATTERY, b"\x55\x01"))
        pkt[15] ^= 0xFF
        assert proto.dispatch_response(bytes(pkt)) is None


# ════════════════════════════════════════════════════════════════════════════
# クライアント: SimulatedBackend + ColmiClient (async)
# ════════════════════════════════════════════════════════════════════════════


class TestSimulatedClient:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def setup_method(self):
        # 各テストで新規 event loop
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def teardown_method(self):
        self.loop.close()

    def test_scan_returns_fake_address(self):
        backend = SimulatedBackend()
        addresses = self.loop.run_until_complete(backend.scan(0.1))
        assert addresses == [SimulatedBackend.FAKE_ADDRESS]

    def test_connect_disconnect(self):
        backend = SimulatedBackend()
        assert not backend.connected
        ok = self.loop.run_until_complete(backend.connect(SimulatedBackend.FAKE_ADDRESS))
        assert ok is True
        assert backend.connected is True
        self.loop.run_until_complete(backend.disconnect())
        assert backend.connected is False

    def test_connect_wrong_address_fails(self):
        backend = SimulatedBackend()
        ok = self.loop.run_until_complete(backend.connect("aa:bb:cc:dd:ee:ff"))
        assert ok is False

    def test_client_battery(self):
        client = ColmiClient(SimulatedBackend(battery_level=42, battery_charging=True))

        async def go():
            await client.connect()
            bat = await client.get_battery()
            await client.disconnect()
            return bat

        bat = self.loop.run_until_complete(go())
        assert bat.level == 42
        assert bat.charging is True

    def test_client_sleep(self):
        client = ColmiClient(SimulatedBackend())

        async def go():
            await client.connect()
            s = await client.get_sleep_summary()
            await client.disconnect()
            return s

        s = self.loop.run_until_complete(go())
        assert s.total_min == 420
        assert s.deep_min == 120
        assert s.quality == 80

    def test_client_hr_stream_collects_samples(self):
        client = ColmiClient(SimulatedBackend(hr_interval_s=0.05))

        async def go():
            await client.connect()
            samples = await client.stream_heart_rate(0.4)
            await client.disconnect()
            return samples

        samples = self.loop.run_until_complete(go())
        # 0.4s / 0.05s = 約 8 サンプル (取りこぼし含めて 4-10 件程度)
        assert len(samples) >= 3
        for s in samples:
            assert 40 <= s.bpm <= 200
            assert 200 <= s.rri_ms <= 2000

    def test_client_battery_timeout_raises(self):
        """書込みは届くが notify を返さない backend で timeout"""

        class BrokenBackend(SimulatedBackend):
            def _emit_battery_response(self):
                pass  # わざと応答しない

        client = ColmiClient(BrokenBackend())

        async def go():
            await client.connect()
            await client.get_battery(timeout_s=0.1)

        with pytest.raises(TimeoutError):
            self.loop.run_until_complete(go())


# ════════════════════════════════════════════════════════════════════════════
# HRV 計算
# ════════════════════════════════════════════════════════════════════════════


class TestHrv:
    def test_mean_hr(self):
        # 60bpm ⇄ rri=1000ms
        assert mean_hr([1000, 1000, 1000]) == pytest.approx(60.0)
        # 80bpm ⇄ rri=750ms
        assert mean_hr([750, 750]) == pytest.approx(80.0)

    def test_mean_hr_empty(self):
        assert mean_hr([]) == 0.0

    def test_rmssd_constant_zero(self):
        # 全部同じ → 差分ゼロ → RMSSD = 0
        assert rmssd([800, 800, 800, 800]) == 0.0

    def test_rmssd_known_value(self):
        # rri = [800, 850, 800, 850]
        # diffs = [50, -50, 50] → sq = [2500, 2500, 2500] → mean=2500 → rmssd=50
        assert rmssd([800, 850, 800, 850]) == pytest.approx(50.0)

    def test_rmssd_short_input(self):
        assert rmssd([800]) == 0.0
        assert rmssd([]) == 0.0

    def test_sdnn_known_value(self):
        # rri = [800, 1200] → mean=1000, var=40000, std=200
        assert sdnn([800, 1200]) == pytest.approx(200.0)

    def test_pnn50(self):
        # rri = [800, 870, 800, 810]
        # |diffs| = [70, 70, 10] → 2/3 over 50ms
        assert pnn50([800, 870, 800, 810]) == pytest.approx(2 / 3)

    def test_stress_score_clamping(self):
        assert stress_score(100, baseline_rmssd=50) == 0.0
        assert stress_score(50, baseline_rmssd=50) == 0.0
        assert stress_score(25, baseline_rmssd=50) == pytest.approx(0.5)
        assert stress_score(0, baseline_rmssd=50) == 1.0
        assert stress_score(-5, baseline_rmssd=50) == 1.0

    def test_compute_hrv_full(self):
        rri = [800, 850, 800, 850, 820, 870, 820]
        m = compute_hrv(rri, baseline_rmssd=50.0)
        assert isinstance(m, HrvMetrics)
        assert m.sample_count == 7
        assert m.mean_hr > 0
        assert m.rmssd_ms > 0
        assert m.sdnn_ms > 0
        assert 0.0 <= m.stress_score <= 1.0
        d = m.to_dict()
        assert "hr_avg" in d
        assert "stress_level" in d

    def test_compute_hrv_empty(self):
        m = compute_hrv([])
        assert m.sample_count == 0
        assert m.mean_hr == 0.0
        assert m.rmssd_ms == 0.0
        assert m.stress_score == 1.0  # 計算不能 → 高ストレス側に倒す

    def test_filter_rri_removes_outliers(self):
        raw = [800, 100, 850, 5000, 820]  # 100 と 5000 は範囲外
        out = filter_rri(raw)
        assert 100 not in out
        assert 5000 not in out
        assert 800 in out

    def test_filter_rri_removes_jumps(self):
        # 隣接比 20% 超を除外
        raw = [800, 810, 820, 1100]  # 1100 は 820 から 34% jump → 除外
        out = filter_rri(raw, max_jump_ratio=0.20)
        assert 1100 not in out
        assert 800 in out

    def test_e2e_simulator_hr_stream_to_hrv(self):
        """Simulator で HR ストリーム → filter → HRV 計算 の E2E"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = ColmiClient(SimulatedBackend(hr_interval_s=0.02, hr_baseline_bpm=70))

        async def go():
            await client.connect()
            samples = await client.stream_heart_rate(0.2)
            await client.disconnect()
            return samples

        samples = loop.run_until_complete(go())
        loop.close()
        assert len(samples) >= 3
        rri = filter_rri([s.rri_ms for s in samples])
        m = compute_hrv(rri)
        # 擬似データでも妥当な範囲に収まる
        assert 40 < m.mean_hr < 120
        assert m.rmssd_ms >= 0
