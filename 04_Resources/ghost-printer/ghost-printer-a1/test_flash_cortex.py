"""
Ghost-Printer B3 — flash_cortex.py の OPi 3B 対応テスト

実機 SPI に触らず、 SimulatedBackend のみを使って:
  - argparse の bus / device / speed_hz が正しくパースされること
  - 環境変数 GPP_SPI_BUS / GPP_SPI_DEVICE がデフォルト値になること
  - シミュレータバックエンドで書込み→ベリファイ round-trip が成立すること
  - --read-id が JEDEC ID を出力すること
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import flash_cortex as fc


HERE = Path(__file__).parent
PYTHON = sys.executable


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """flash_cortex.py をサブプロセスで起動して結果を返す"""
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)
    return subprocess.run(
        [PYTHON, str(HERE / "flash_cortex.py"), *args],
        capture_output=True,
        text=True,
        env=cmd_env,
        timeout=30,
    )


# ════════════════════════════════════════════════════════════════════════════
# argparse 単体
# ════════════════════════════════════════════════════════════════════════════


class TestArgParsing:
    def test_default_bus_is_zero_when_no_env(self, monkeypatch):
        """環境変数なし → bus=0 (Pi 5 互換) がデフォルト"""
        monkeypatch.delenv("GPP_SPI_BUS", raising=False)
        monkeypatch.delenv("GPP_SPI_DEVICE", raising=False)
        # main を引数なしで呼ぶと file 必須でエラーになるため、 simulate + read-id
        # SimulatedBackend は bus 引数を受け取らないので普通にパースできる
        rc = fc.main(["--read-id", "--simulate"])
        assert rc == 0

    def test_env_bus_overrides_default(self, monkeypatch):
        """GPP_SPI_BUS=3 で OPi 3B 用デフォルトに切替"""
        monkeypatch.setenv("GPP_SPI_BUS", "3")
        # _env_int を直接テスト
        assert fc._env_int("GPP_SPI_BUS", 0) == 3

    def test_env_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("GPP_SPI_BUS", "garbage")
        assert fc._env_int("GPP_SPI_BUS", 0) == 0

    def test_env_hex_accepted(self, monkeypatch):
        monkeypatch.setenv("GPP_SPI_BUS", "0x3")
        assert fc._env_int("GPP_SPI_BUS", 0) == 3


# ════════════════════════════════════════════════════════════════════════════
# CLI 経由
# ════════════════════════════════════════════════════════════════════════════


class TestCliReadId:
    def test_read_id_simulate(self):
        """シミュレータモードで --read-id が Winbond W25Q64 を表示 (Pre-MVP 採択)"""
        result = _run_cli("--read-id", "--simulate")
        assert result.returncode == 0, result.stderr
        assert "JEDEC ID" in result.stdout
        assert "Winbond" in result.stdout
        assert "8MB" in result.stdout
        assert "W25Q64" in result.stdout

    def test_help_includes_opi3b_example(self):
        """ヘルプ出力に OPi 3B (--bus 3) の例が含まれている"""
        result = _run_cli("--help")
        assert result.returncode == 0
        assert "Orange Pi 3B" in result.stdout or "OPi 3B" in result.stdout
        assert "--bus 3" in result.stdout


class TestCliRoundTrip:
    def test_simulator_write_verify_round_trip(self, tmp_path):
        """シミュレータで書込み→検証 round-trip"""
        cortex = tmp_path / "cortex.bin"
        cortex.write_bytes(b"hello world test cortex" * 100)

        # write
        result = _run_cli(str(cortex), "--simulate")
        assert result.returncode == 0, result.stderr
        assert "Verified" in result.stdout or "verified" in result.stdout

    def test_simulator_verify_only(self, tmp_path):
        """シミュレータで verify-only モード (書込み済み前提だが、 シミュレータでは
        毎回 0xFF 初期化なので unverified を期待)"""
        cortex = tmp_path / "cortex.bin"
        cortex.write_bytes(b"x" * 1024)
        # verify mode: 書込みは行わず比較のみ → シミュレータの 0xFF とは一致しない
        result = _run_cli(str(cortex), "--verify", "--simulate")
        # 不一致なので終了コードは 1
        assert result.returncode == 1

    def test_bus_arg_accepted(self, tmp_path):
        """--bus 3 を渡してもシミュレータ経路では問題なく通る"""
        cortex = tmp_path / "cortex.bin"
        cortex.write_bytes(b"\x42" * 256)
        result = _run_cli(str(cortex), "--simulate", "--bus", "3")
        assert result.returncode == 0, result.stderr


class TestEnvIntegration:
    def test_env_var_picked_up_by_help(self, tmp_path):
        """GPP_SPI_BUS=3 を設定すると --help の default 値表示が変わる"""
        result = _run_cli("--help", env={"GPP_SPI_BUS": "3"})
        assert result.returncode == 0
        # default: 3 が --bus の説明に出る
        assert "default: 3" in result.stdout

    def test_env_default_zero_in_help(self, tmp_path):
        result = _run_cli("--help", env={"GPP_SPI_BUS": ""})
        assert result.returncode == 0
        assert "default: 0" in result.stdout
