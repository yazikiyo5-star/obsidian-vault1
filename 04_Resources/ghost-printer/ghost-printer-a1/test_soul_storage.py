"""
Ghost-Printer A6 — ShadowStorage E2E テスト
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import soul_binary as sb
from soul_engine import update_soul
from soul_log import SoulLog
from soul_schema import create_empty_soul
from soul_storage import ShadowStorage


# ════════════════════════════════════════════════════════════════════════════
# 共通ヘルパ
# ════════════════════════════════════════════════════════════════════════════


def _delta(text: str = "x", importance: float = 0.6) -> dict:
    return {
        "importance": importance,
        "emotion": {"name": "calm", "intensity": 0.5},
        "personality_signals": [
            {"dimension": "openness", "value": 0.7, "confidence": 0.6},
            {"dimension": "curiosity", "value": 0.65, "confidence": 0.55},
        ],
        "topics": ["test"],
        "values": ["growth"],
        "summary": text[:30],
    }


@pytest.fixture
def storage_paths(tmp_path) -> dict:
    return {
        "json_path": tmp_path / "soul.json",
        "bin_path": tmp_path / "soul.bin",
        "log_path": tmp_path / "soul.log",
    }


# ════════════════════════════════════════════════════════════════════════════
# 初期動作
# ════════════════════════════════════════════════════════════════════════════


class TestInitialWrite:
    def test_save_full_writes_all_paths(self, storage_paths):
        store = ShadowStorage(**storage_paths)
        soul = create_empty_soul("test")
        stats = store.save_full(soul)

        assert storage_paths["json_path"].exists()
        assert storage_paths["bin_path"].exists()
        assert storage_paths["log_path"].exists()
        # bin がデコード可能
        decoded = sb.decode_soul(storage_paths["bin_path"].read_bytes())
        assert "core_identity" in decoded
        # log が空 (header のみ)
        log = SoulLog(storage_paths["log_path"])
        assert log.entry_count() == 0

    def test_first_append_creates_bin_and_log(self, storage_paths):
        store = ShadowStorage(**storage_paths)
        soul = create_empty_soul()
        # update_soul で更新
        soul = update_soul(soul, _delta("hello"), "hello world")
        stats = store.append_update(
            soul, raw_text="hello world", delta=_delta("hello")
        )
        # 初回 append は bin 作成 (compaction 扱い)
        assert stats["compacted"] is True
        assert stats["compact_reason"] == "bin_missing"
        assert storage_paths["json_path"].exists()
        assert storage_paths["bin_path"].exists()
        # bin 作成と同時に log は clear される
        assert SoulLog(storage_paths["log_path"]).entry_count() == 0


# ════════════════════════════════════════════════════════════════════════════
# 増分書込み (log だけ追記、bin は触らない)
# ════════════════════════════════════════════════════════════════════════════


class TestIncrementalAppend:
    def test_subsequent_appends_only_grow_log(self, storage_paths):
        store = ShadowStorage(**storage_paths)
        soul = create_empty_soul()
        # 初回で bin を作る
        soul = update_soul(soul, _delta("first"), "first")
        store.append_update(soul, raw_text="first", delta=_delta("first"))
        bin_size_after_init = storage_paths["bin_path"].stat().st_size
        bin_mtime_after_init = storage_paths["bin_path"].stat().st_mtime_ns

        # 2 回目以降は log だけ伸びる
        for i in range(3):
            soul = update_soul(
                soul, _delta(f"text_{i}"), f"text_{i}"
            )
            stats = store.append_update(
                soul, raw_text=f"text_{i}", delta=_delta(f"text_{i}")
            )
            assert stats["compacted"] is False
            assert stats["wrote_json"] is True
            assert stats["log_size"] > 0

        # bin は変わっていない
        assert storage_paths["bin_path"].stat().st_size == bin_size_after_init
        assert storage_paths["bin_path"].stat().st_mtime_ns == bin_mtime_after_init
        # log には 3 エントリ
        assert SoulLog(storage_paths["log_path"]).entry_count() == 3


# ════════════════════════════════════════════════════════════════════════════
# Compaction 発火
# ════════════════════════════════════════════════════════════════════════════


class TestCompactionTrigger:
    def test_size_threshold_triggers_compaction(self, storage_paths):
        # 小さい閾値で発火させる
        store = ShadowStorage(**storage_paths, compact_size_threshold=2000)
        soul = create_empty_soul()
        # 初回
        soul = update_soul(soul, _delta("x"), "x" * 50)
        store.append_update(soul, raw_text="x" * 50, delta=_delta())

        # 大きいテキストを追加して log を太らせる
        compaction_seen = False
        for i in range(20):
            big = "あ" * 200  # UTF-8 で 600 B 程度
            soul = update_soul(soul, _delta(big), big)
            stats = store.append_update(soul, raw_text=big, delta=_delta(big))
            if stats["compacted"]:
                compaction_seen = True
                assert "size" in (stats.get("compact_reason") or "")
                # compaction 後 log は空
                assert SoulLog(storage_paths["log_path"]).entry_count() == 0
                break
        assert compaction_seen, "size 閾値で compaction が発火しなかった"


# ════════════════════════════════════════════════════════════════════════════
# クラッシュリカバリ (load = bin + log の fold)
# ════════════════════════════════════════════════════════════════════════════


class TestCrashRecovery:
    def test_load_replays_log_on_top_of_snapshot(self, storage_paths):
        """compaction 前にプロセスが落ちても load() で完全状態が復元できる"""
        store = ShadowStorage(**storage_paths)
        soul = create_empty_soul("alice")

        # 初回 append (bin 作成)
        soul = update_soul(soul, _delta("e0"), "episode 0")
        store.append_update(soul, raw_text="episode 0", delta=_delta("e0"))

        # 増分 3 件 (bin は触られない)
        for i in range(1, 4):
            soul = update_soul(soul, _delta(f"e{i}"), f"episode {i}")
            store.append_update(
                soul, raw_text=f"episode {i}", delta=_delta(f"e{i}")
            )

        # ストレージを再構築 (= プロセス再起動)
        store2 = ShadowStorage(**storage_paths)
        recovered = store2.load()
        # 全エピソードが復元されている (bin 1 件 + log 3 件)
        assert len(recovered["episodic_memory"]["recent"]) == 4
        # 順序も保たれている
        texts = [ep["raw_text"] for ep in recovered["episodic_memory"]["recent"]]
        assert texts == ["episode 0", "episode 1", "episode 2", "episode 3"]

    def test_load_falls_back_to_json_when_no_bin(self, tmp_path):
        # bin 未設定、JSON のみ
        store = ShadowStorage(json_path=tmp_path / "soul.json")
        soul = create_empty_soul()
        soul = update_soul(soul, _delta("solo"), "solo entry")
        store.save_full(soul)
        # 別インスタンスで load
        store2 = ShadowStorage(json_path=tmp_path / "soul.json")
        recovered = store2.load()
        assert recovered["episodic_memory"]["recent"][0]["raw_text"] == "solo entry"

    def test_load_returns_empty_when_nothing(self, tmp_path):
        store = ShadowStorage(json_path=tmp_path / "soul.json")
        soul = store.load()
        assert soul["episodic_memory"]["recent"] == []
        assert "core_identity" in soul


# ════════════════════════════════════════════════════════════════════════════
# 暗号化付き
# ════════════════════════════════════════════════════════════════════════════


class TestEncrypted:
    MASTER = b"\xCC" * 32

    def test_encrypted_round_trip(self, storage_paths):
        store = ShadowStorage(**storage_paths, master_key=self.MASTER)
        soul = create_empty_soul()
        soul = update_soul(soul, _delta("secret"), "secret note")
        store.append_update(
            soul, raw_text="secret note", delta=_delta("secret")
        )

        # 別インスタンス + master_key で load
        store2 = ShadowStorage(**storage_paths, master_key=self.MASTER)
        recovered = store2.load()
        assert "secret note" in [
            ep["raw_text"] for ep in recovered["episodic_memory"]["recent"]
        ]

    def test_load_without_key_fails_completely(self, storage_paths):
        """master_key 付きで書込まれた SOUL は、鍵なしでは bin も log も読めない。

        これが「Q6 採択 (raw_text 暗号化) を完全に満たす」状態:
        - bin の EPISODIC_RECENT は AES-GCM 封印 → 鍵が無いと redact
        - log エントリも AES-GCM 封印 → 鍵が無いと復号失敗で例外
        - 結果として平文の raw_text はディスク上のどこにも残らない
        """
        store = ShadowStorage(**storage_paths, master_key=self.MASTER)
        soul = create_empty_soul()

        # 1 回目: 初回 append → bin 作成、 log は (encrypted) clear
        soul = update_soul(soul, _delta("first"), "first secret")
        store.append_update(
            soul, raw_text="first secret", delta=_delta("first")
        )
        # 2 回目: log に 1 件 (暗号化済) 残る
        soul = update_soul(soul, _delta("second"), "second secret")
        store.append_update(
            soul, raw_text="second secret", delta=_delta("second")
        )

        # 鍵なしロード: log を replay しようとして復号失敗で例外
        store_no_key = ShadowStorage(**storage_paths)
        with pytest.raises(ValueError, match="encrypted"):
            store_no_key.load()

        # 鍵ありロードなら両方読める
        store_with_key = ShadowStorage(**storage_paths, master_key=self.MASTER)
        recovered = store_with_key.load()
        texts = [
            ep.get("raw_text")
            for ep in recovered["episodic_memory"]["recent"]
            if ep.get("raw_text")
        ]
        assert "first secret" in texts
        assert "second secret" in texts

    def test_log_file_does_not_contain_raw_text(self, storage_paths):
        """log バイナリを直接読んでも raw_text 文字列が含まれていないこと"""
        store = ShadowStorage(**storage_paths, master_key=self.MASTER)
        soul = create_empty_soul()
        # 初回でいきなり compact しないように、 size 閾値を高めに
        store.compact_size_threshold = 1024 * 1024
        soul = update_soul(soul, _delta(), "absolutely_unique_marker_xyz")
        store.append_update(
            soul, raw_text="absolutely_unique_marker_xyz",
            delta=_delta("absolutely_unique_marker_xyz"),
        )
        # 2 回目以降の append は log にだけ追記される
        for i in range(3):
            txt = f"sensitive_payload_{i}_qpr"
            soul = update_soul(soul, _delta(txt), txt)
            store.append_update(soul, raw_text=txt, delta=_delta(txt))

        log_bytes = storage_paths["log_path"].read_bytes()
        # log に "sensitive_payload" や "absolutely_unique_marker" が
        # 平文で含まれていないこと
        assert b"sensitive_payload" not in log_bytes
        assert b"absolutely_unique_marker" not in log_bytes
        # ただし magic はある
        assert log_bytes[0:4] == b"GPLG"


# ════════════════════════════════════════════════════════════════════════════
# 構成 / バリデーション
# ════════════════════════════════════════════════════════════════════════════


class TestConfiguration:
    def test_bin_without_log_raises(self, tmp_path):
        with pytest.raises(ValueError, match="log_path"):
            ShadowStorage(bin_path=tmp_path / "x.bin")

    def test_compact_explicit(self, storage_paths):
        store = ShadowStorage(**storage_paths)
        soul = create_empty_soul()
        # 初回 + 2 件追加 (log に 2 件残る)
        soul = update_soul(soul, _delta(), "init")
        store.append_update(soul, raw_text="init", delta=_delta())
        for i in range(2):
            soul = update_soul(soul, _delta(), f"add_{i}")
            store.append_update(soul, raw_text=f"add_{i}", delta=_delta())
        assert SoulLog(storage_paths["log_path"]).entry_count() == 2

        # 明示 compact
        result = store.compact(soul=soul)
        assert result["compacted_entries"] == 2
        assert SoulLog(storage_paths["log_path"]).entry_count() == 0
        # 再 load で全件回復
        recovered = ShadowStorage(**storage_paths).load()
        assert len(recovered["episodic_memory"]["recent"]) == 3
