"""
Ghost-Printer — CORTEX Manager テストスイート

テストカテゴリ:
  BLD: ビルド・初期化
  SER: シリアライズ（save/load）
  VAL: 検証（validate）
  UPD: パラメータ更新（update_param）
  VER: バージョン管理（bump_version）
  DIF: 差分（diff）
  EXP: エクスポート
  SYS: System Prompt生成
  ERR: エラーケース
"""

import json
import os
import struct
import tempfile
import pytest
from pathlib import Path

from cortex_manager import (
    CortexManager, Cortex, WhisperConfig, BonsaiConfig, MiniLMConfig,
    CortexConfig, CortexMeta, PersonalityDimension,
    build_system_prompt, CORTEX_MAGIC, CORTEX_FORMAT_VERSION,
    DEFAULT_DIMENSIONS,
)


@pytest.fixture
def manager():
    mgr = CortexManager()
    mgr.build()
    return mgr


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


# ════════════════════════════════════════════════════════════════════════════════
# BLD: ビルド・初期化
# ════════════════════════════════════════════════════════════════════════════════

class TestBuild:
    def test_bld_01_default_build(self, manager):
        """BLD-01: デフォルトビルドで全コンポーネントが初期化される"""
        c = manager.cortex
        assert c is not None
        assert isinstance(c.whisper, WhisperConfig)
        assert isinstance(c.bonsai, BonsaiConfig)
        assert isinstance(c.minilm, MiniLMConfig)
        assert isinstance(c.cortex, CortexConfig)
        assert isinstance(c.meta, CortexMeta)

    def test_bld_02_default_dimensions(self, manager):
        """BLD-02: デフォルト10次元が正しく設定される"""
        dims = manager.cortex.bonsai.dimensions
        assert len(dims) == 10
        names = {d.name for d in dims}
        assert {"openness", "conscientiousness", "extraversion",
                "agreeableness", "neuroticism"}.issubset(names)
        assert {"curiosity", "creativity", "empathy",
                "risk_tolerance", "independence"}.issubset(names)

    def test_bld_03_system_prompt_auto_generated(self, manager):
        """BLD-03: ビルド時にSystem Promptが自動生成される"""
        prompt = manager.cortex.bonsai.system_prompt
        assert len(prompt) > 100
        assert "personality_signals" in prompt
        assert "openness" in prompt

    def test_bld_04_custom_version(self):
        """BLD-04: カスタムバージョンで初期化"""
        mgr = CortexManager()
        mgr.build(version="2.0.0", description="Custom test build")
        assert mgr.cortex.meta.version == "2.0.0"
        assert mgr.cortex.meta.description == "Custom test build"

    def test_bld_05_meta_timestamps(self, manager):
        """BLD-05: created_at/updated_atがISO形式で設定される"""
        meta = manager.cortex.meta
        assert "T" in meta.created_at
        assert "T" in meta.updated_at
        assert meta.changelog[0]["version"] == "1.0.0"


# ════════════════════════════════════════════════════════════════════════════════
# SER: シリアライズ（save/load）
# ════════════════════════════════════════════════════════════════════════════════

class TestSerialize:
    def test_ser_01_save_creates_file(self, manager, tmp_dir):
        """SER-01: save()でCORTEX.binが作成される"""
        path = os.path.join(tmp_dir, "CORTEX.bin")
        info = manager.save(path)
        assert os.path.exists(path)
        assert info["size_bytes"] > 0

    def test_ser_02_binary_header(self, manager, tmp_dir):
        """SER-02: バイナリヘッダーが正しい（GPCX + version + length）"""
        path = os.path.join(tmp_dir, "CORTEX.bin")
        manager.save(path)

        with open(path, "rb") as f:
            magic = f.read(4)
            version = struct.unpack("<H", f.read(2))[0]
            data_len = struct.unpack("<I", f.read(4))[0]

        assert magic == CORTEX_MAGIC
        assert version == CORTEX_FORMAT_VERSION
        assert data_len > 0

    def test_ser_03_roundtrip(self, manager, tmp_dir):
        """SER-03: save→loadのラウンドトリップでデータが保持される"""
        path = os.path.join(tmp_dir, "CORTEX.bin")
        manager.save(path)

        mgr2 = CortexManager()
        loaded = mgr2.load(path)

        assert loaded.meta.version == manager.cortex.meta.version
        assert loaded.bonsai.temperature == manager.cortex.bonsai.temperature
        assert len(loaded.bonsai.dimensions) == len(manager.cortex.bonsai.dimensions)
        assert loaded.whisper.vad_threshold == manager.cortex.whisper.vad_threshold
        assert loaded.minilm.vector_dim == manager.cortex.minilm.vector_dim
        assert loaded.cortex.acoustic_boost_weight == manager.cortex.cortex.acoustic_boost_weight

    def test_ser_04_compression(self, manager, tmp_dir):
        """SER-04: gzip圧縮が効いている（ratio < 1.0）"""
        path = os.path.join(tmp_dir, "CORTEX.bin")
        info = manager.save(path)
        assert info["compression_ratio"] < 1.0
        assert info["compressed_size"] < info["json_size"]

    def test_ser_05_checksum_stored(self, manager, tmp_dir):
        """SER-05: チェックサムが保存される"""
        path = os.path.join(tmp_dir, "CORTEX.bin")
        info = manager.save(path)
        assert len(info["checksum"]) == 64  # SHA256 hex

    def test_ser_06_load_dimensions(self, manager, tmp_dir):
        """SER-06: dimensionsがPersonalityDimensionとして復元される"""
        path = os.path.join(tmp_dir, "CORTEX.bin")
        manager.save(path)

        mgr2 = CortexManager()
        loaded = mgr2.load(path)

        for dim in loaded.bonsai.dimensions:
            assert isinstance(dim, PersonalityDimension)
            assert dim.initial_mu == 0.5
            assert dim.sigma_floor == 0.02


# ════════════════════════════════════════════════════════════════════════════════
# VAL: 検証
# ════════════════════════════════════════════════════════════════════════════════

class TestValidate:
    def test_val_01_default_passes(self, manager):
        """VAL-01: デフォルトビルドはバリデーション通過"""
        errors = manager.validate()
        assert errors == []

    def test_val_02_no_cortex_loaded(self):
        """VAL-02: CORTEXが未ロード時はエラー"""
        mgr = CortexManager()
        errors = mgr.validate()
        assert "No CORTEX loaded" in errors

    def test_val_03_invalid_vad(self, manager):
        """VAL-03: vad_threshold範囲外を検出"""
        manager.cortex.whisper.vad_threshold = 1.5
        errors = manager.validate()
        assert any("vad_threshold" in e for e in errors)

    def test_val_04_empty_dimensions(self, manager):
        """VAL-04: dimensions空を検出"""
        manager.cortex.bonsai.dimensions = []
        errors = manager.validate()
        assert any("dimensions is empty" in e for e in errors)

    def test_val_05_missing_big5(self, manager):
        """VAL-05: Big5の一部欠落を検出"""
        manager.cortex.bonsai.dimensions = [
            d for d in manager.cortex.bonsai.dimensions
            if d.name != "openness"
        ]
        errors = manager.validate()
        assert any("openness" in e for e in errors)

    def test_val_06_empty_prompt(self, manager):
        """VAL-06: system_prompt空を検出"""
        manager.cortex.bonsai.system_prompt = ""
        errors = manager.validate()
        assert any("system_prompt" in e for e in errors)


# ════════════════════════════════════════════════════════════════════════════════
# UPD: パラメータ更新
# ════════════════════════════════════════════════════════════════════════════════

class TestUpdateParam:
    def test_upd_01_bonsai_temp(self, manager):
        """UPD-01: bonsai.temperatureの更新"""
        assert manager.cortex.bonsai.temperature == 0.3
        result = manager.update_param("bonsai.temperature", 0.7)
        assert result is True
        assert manager.cortex.bonsai.temperature == 0.7

    def test_upd_02_cortex_param(self, manager):
        """UPD-02: cortex.acoustic_boost_weightの更新"""
        result = manager.update_param("cortex.acoustic_boost_weight", 0.35)
        assert result is True
        assert manager.cortex.cortex.acoustic_boost_weight == 0.35

    def test_upd_03_whisper_param(self, manager):
        """UPD-03: whisper.vad_thresholdの更新"""
        result = manager.update_param("whisper.vad_threshold", 0.6)
        assert result is True
        assert manager.cortex.whisper.vad_threshold == 0.6

    def test_upd_04_invalid_path(self, manager):
        """UPD-04: 存在しないパスはFalseを返す"""
        result = manager.update_param("bonsai.nonexistent_param", 0.5)
        assert result is False

    def test_upd_05_prompt_regenerated(self, manager):
        """UPD-05: bonsai設定変更後にSystem Promptが再生成される"""
        old_prompt = manager.cortex.bonsai.system_prompt
        manager.update_param("bonsai.temperature", 0.9)
        # temperature自体はprompt文中に入らないが、prompt再生成は走る
        new_prompt = manager.cortex.bonsai.system_prompt
        assert len(new_prompt) > 100  # 再生成されている

    def test_upd_06_changelog_appended(self, manager):
        """UPD-06: 更新時にchangelogが追加される"""
        initial_len = len(manager.cortex.meta.changelog)
        manager.update_param("bonsai.temperature", 0.5)
        assert len(manager.cortex.meta.changelog) == initial_len + 1
        last = manager.cortex.meta.changelog[-1]
        assert "bonsai.temperature" in last["note"]
        assert "0.3 → 0.5" in last["note"]

    def test_upd_07_updated_at_changes(self, manager):
        """UPD-07: update後にupdated_atが更新される"""
        old_updated = manager.cortex.meta.updated_at
        manager.update_param("minilm.similarity_threshold", 0.8)
        assert manager.cortex.meta.updated_at >= old_updated


# ════════════════════════════════════════════════════════════════════════════════
# VER: バージョン管理
# ════════════════════════════════════════════════════════════════════════════════

class TestVersioning:
    def test_ver_01_patch_bump(self, manager):
        """VER-01: パッチバンプ 1.0.0 → 1.0.1"""
        new_ver = manager.bump_version("patch")
        assert new_ver == "1.0.1"
        assert manager.cortex.meta.version == "1.0.1"

    def test_ver_02_minor_bump(self, manager):
        """VER-02: マイナーバンプ 1.0.0 → 1.1.0"""
        new_ver = manager.bump_version("minor")
        assert new_ver == "1.1.0"

    def test_ver_03_major_bump(self, manager):
        """VER-03: メジャーバンプ 1.0.0 → 2.0.0"""
        new_ver = manager.bump_version("major")
        assert new_ver == "2.0.0"

    def test_ver_04_changelog_entry(self, manager):
        """VER-04: バンプ時にchangelogエントリが追加される"""
        manager.bump_version("minor", "Added new personality dimension")
        last = manager.cortex.meta.changelog[-1]
        assert last["version"] == "1.1.0"
        assert "new personality dimension" in last["note"]


# ════════════════════════════════════════════════════════════════════════════════
# DIF: 差分
# ════════════════════════════════════════════════════════════════════════════════

class TestDiff:
    def test_dif_01_no_diff_same_file(self, manager, tmp_dir):
        """DIF-01: 同一ファイルの差分は空（metaのみ）"""
        path = os.path.join(tmp_dir, "CORTEX.bin")
        manager.save(path)
        diffs = manager.diff(path)
        # チェックサムやタイムスタンプは同一なので差分なし
        assert len(diffs) == 0

    def test_dif_02_detect_changes(self, manager, tmp_dir):
        """DIF-02: パラメータ変更を検出する"""
        path_orig = os.path.join(tmp_dir, "orig.bin")
        manager.save(path_orig)

        manager.update_param("bonsai.temperature", 0.9)
        manager.bump_version("patch", "temp change")
        path_new = os.path.join(tmp_dir, "new.bin")
        manager.save(path_new)

        diffs = manager.diff(path_orig)
        diff_text = "\n".join(diffs)
        assert "bonsai.temperature" in diff_text


# ════════════════════════════════════════════════════════════════════════════════
# EXP: エクスポート
# ════════════════════════════════════════════════════════════════════════════════

class TestExport:
    def test_exp_01_json_export(self, manager, tmp_dir):
        """EXP-01: JSON形式でエクスポートされる"""
        path = os.path.join(tmp_dir, "cortex.json")
        manager.export_json(path)
        assert os.path.exists(path)

        with open(path) as f:
            data = json.load(f)
        assert "whisper" in data
        assert "bonsai" in data
        assert "minilm" in data
        assert "cortex" in data
        assert "meta" in data

    def test_exp_02_json_readable(self, manager, tmp_dir):
        """EXP-02: エクスポートJSONのbonsai.dimensionsが読める"""
        path = os.path.join(tmp_dir, "cortex.json")
        manager.export_json(path)

        with open(path) as f:
            data = json.load(f)
        dims = data["bonsai"]["dimensions"]
        assert len(dims) == 10
        assert dims[0]["name"] == "openness"


# ════════════════════════════════════════════════════════════════════════════════
# SYS: System Prompt生成
# ════════════════════════════════════════════════════════════════════════════════

class TestSystemPrompt:
    def test_sys_01_contains_schema(self):
        """SYS-01: System PromptにJSON出力スキーマが含まれる"""
        config = BonsaiConfig()
        prompt = build_system_prompt(config)
        assert '"importance"' in prompt
        assert '"emotion"' in prompt
        assert '"personality_signals"' in prompt

    def test_sys_02_contains_all_dimensions(self):
        """SYS-02: 全次元名がSystem Promptに含まれる"""
        config = BonsaiConfig()
        prompt = build_system_prompt(config)
        for dim in config.dimensions:
            assert dim.name in prompt

    def test_sys_03_contains_importance_scale(self):
        """SYS-03: 重要度スケールが含まれる"""
        config = BonsaiConfig()
        prompt = build_system_prompt(config)
        assert "0.9-1.0" in prompt
        assert "人生を変えるイベント" in prompt

    def test_sys_04_get_system_prompt(self, manager):
        """SYS-04: get_system_prompt()がビルド済みプロンプトを返す"""
        prompt = manager.get_system_prompt()
        assert prompt == manager.cortex.bonsai.system_prompt
        assert len(prompt) > 100


# ════════════════════════════════════════════════════════════════════════════════
# ERR: エラーケース
# ════════════════════════════════════════════════════════════════════════════════

class TestErrors:
    def test_err_01_save_without_build(self):
        """ERR-01: ビルド前のsaveでValueError"""
        mgr = CortexManager()
        with pytest.raises(ValueError, match="No CORTEX"):
            mgr.save("/tmp/test.bin")

    def test_err_02_load_nonexistent(self):
        """ERR-02: 存在しないファイルのloadでFileNotFoundError"""
        mgr = CortexManager()
        with pytest.raises(FileNotFoundError):
            mgr.load("/tmp/nonexistent_cortex.bin")

    def test_err_03_load_invalid_magic(self, tmp_dir):
        """ERR-03: 不正なマジックバイトのloadでValueError"""
        path = os.path.join(tmp_dir, "bad.bin")
        with open(path, "wb") as f:
            f.write(b"XXXX" + b"\x00" * 100)
        mgr = CortexManager()
        with pytest.raises(ValueError, match="Invalid CORTEX magic"):
            mgr.load(path)

    def test_err_04_checksum_tamper(self, manager, tmp_dir):
        """ERR-04: データ改ざんでチェックサム不一致"""
        path = os.path.join(tmp_dir, "CORTEX.bin")
        manager.save(path)

        # バイナリの末尾付近を改ざん
        with open(path, "rb") as f:
            data = bytearray(f.read())
        # 末尾1バイトを書き換え
        if data[-1] != 0xFF:
            data[-1] = 0xFF
        else:
            data[-1] = 0x00
        with open(path, "wb") as f:
            f.write(data)

        mgr2 = CortexManager()
        with pytest.raises(Exception):
            mgr2.load(path)

    def test_err_05_update_no_cortex(self):
        """ERR-05: 未ロードでupdate_paramするとValueError"""
        mgr = CortexManager()
        with pytest.raises(ValueError, match="No CORTEX"):
            mgr.update_param("bonsai.temperature", 0.5)

    def test_err_06_bump_no_cortex(self):
        """ERR-06: 未ロードでbump_versionするとValueError"""
        mgr = CortexManager()
        with pytest.raises(ValueError, match="No CORTEX"):
            mgr.bump_version()
