"""
Ghost-Printer A6 — `.soul.log` のテスト + コンパクション動作確認
"""

from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path

import cbor2
import pytest

import soul_binary as sb
import soul_log as sl
from soul_schema import create_empty_soul, create_episode


# ════════════════════════════════════════════════════════════════════════════
# ヘルパ
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_log(tmp_path) -> sl.SoulLog:
    return sl.SoulLog(tmp_path / "soul.log")


@pytest.fixture
def tmp_snapshot(tmp_path) -> Path:
    return tmp_path / "soul.bin"


def _make_input_record(text: str, importance: float = 0.6, epoch: int = 0) -> dict:
    """合成 input record (extractor の delta を擬似的に作る)"""
    return {
        "op": sl.OP_INPUT,
        "ts_ms": 1700000000000 + epoch * 1000,
        "epoch": epoch,
        "raw_text": text,
        "delta": {
            "importance": importance,
            "emotion": {"name": "calm", "intensity": 0.5},
            "personality_signals": [
                {"dimension": "openness", "value": 0.7, "confidence": 0.6},
                {"dimension": "curiosity", "value": 0.65, "confidence": 0.55},
            ],
            "topics": ["test_topic"],
            "values": ["test_value"],
            "summary": text[:30],
        },
    }


def _replay_input_simple(soul: dict, record: dict) -> dict:
    """soul_engine 非依存の最小 replay (テスト用): episode 追加だけする"""
    if record["op"] != sl.OP_INPUT:
        return soul
    delta = record["delta"]
    ep = create_episode(
        text=record["raw_text"],
        importance=delta["importance"],
        emotion=delta["emotion"],
        personality_signals=delta["personality_signals"],
        topics=delta.get("topics", []),
        values=delta.get("values", []),
        summary=delta.get("summary", ""),
        context=record.get("context"),
    )
    soul["episodic_memory"]["recent"].append(ep)
    soul["stats"]["total_episodes"] = soul["stats"].get("total_episodes", 0) + 1
    soul["stats"]["total_updates"] = soul["stats"].get("total_updates", 0) + 1
    return soul


# ════════════════════════════════════════════════════════════════════════════
# テスト: SoulLog 基礎
# ════════════════════════════════════════════════════════════════════════════


class TestSoulLogBasics:
    def test_init_creates_header(self, tmp_log):
        tmp_log.init(snapshot_epoch=42)
        assert tmp_log.exists()
        assert tmp_log.size() == sl.LOG_HEADER_SIZE
        h = tmp_log.header()
        assert h["version"] == sl.LOG_VERSION
        assert h["snapshot_epoch"] == 42

    def test_append_and_iter(self, tmp_log):
        tmp_log.init()
        for i in range(5):
            tmp_log.append(_make_input_record(f"text {i}", epoch=i))
        records = tmp_log.records()
        assert len(records) == 5
        for i, r in enumerate(records):
            assert r["op"] == sl.OP_INPUT
            assert r["raw_text"] == f"text {i}"
            assert r["epoch"] == i

    def test_append_creates_log_if_missing(self, tmp_log):
        # init を呼ばずに append しても自動で初期化される
        assert not tmp_log.exists()
        tmp_log.append(_make_input_record("auto-init"))
        assert tmp_log.exists()
        assert tmp_log.entry_count() == 1

    def test_persists_across_processes(self, tmp_log):
        """SoulLog インスタンスを作り直しても読める"""
        tmp_log.init()
        for i in range(3):
            tmp_log.append(_make_input_record(f"persist {i}"))

        # 別インスタンス (= 別プロセスを擬似)
        log2 = sl.SoulLog(tmp_log.path)
        assert log2.entry_count() == 3
        assert log2.records()[1]["raw_text"] == "persist 1"

    def test_clear_resets_to_header_only(self, tmp_log):
        tmp_log.init(snapshot_epoch=10)
        for i in range(4):
            tmp_log.append(_make_input_record(f"x{i}"))
        assert tmp_log.entry_count() == 4

        tmp_log.clear(snapshot_epoch=20)
        assert tmp_log.size() == sl.LOG_HEADER_SIZE
        assert tmp_log.entry_count() == 0
        assert tmp_log.header()["snapshot_epoch"] == 20

    def test_truncated_entry_safely_dropped(self, tmp_log):
        """電源断シミュレーション: 最後の length-prefix が完結していない"""
        tmp_log.init()
        tmp_log.append(_make_input_record("entry 1"))
        tmp_log.append(_make_input_record("entry 2"))
        # ファイル末尾に "途中まで書かれた長さ prefix" を足す
        with open(tmp_log.path, "ab") as f:
            f.write(b"\x99\x99\x99\x99")  # length=2576953241 → 続きが無いので破棄される
        records = tmp_log.records()
        # 完結している 2 件だけ得られる
        assert len(records) == 2
        assert records[1]["raw_text"] == "entry 2"

    def test_bad_magic_raises(self, tmp_path):
        path = tmp_path / "fake.log"
        path.write_bytes(b"BADMAGIC" + b"\x00" * 100)
        log = sl.SoulLog(path)
        with pytest.raises(ValueError, match="magic"):
            log.records()


# ════════════════════════════════════════════════════════════════════════════
# テスト: コンパクション
# ════════════════════════════════════════════════════════════════════════════


class TestCompaction:
    def test_compact_replays_log_to_snapshot(self, tmp_log, tmp_snapshot):
        # 初期 snapshot
        soul = create_empty_soul("alice")
        tmp_snapshot.write_bytes(sb.encode_soul(soul, epoch_counter=0))

        # ログに 3 件追加
        for i in range(3):
            tmp_log.append(_make_input_record(f"compact_test_{i}", epoch=i))

        stats = sl.compact_with_log(
            tmp_snapshot,
            tmp_log,
            replay_fn=_replay_input_simple,
        )
        assert stats["records_replayed"] == 3
        assert stats["new_epoch"] == 3

        # 新 snapshot を読み戻すとエピソード 3 件
        decoded = sb.decode_soul(tmp_snapshot.read_bytes())
        assert len(decoded["episodic_memory"]["recent"]) == 3
        assert decoded["episodic_memory"]["recent"][0]["raw_text"] == "compact_test_0"
        # ログはクリア済み
        assert tmp_log.size() == sl.LOG_HEADER_SIZE
        assert tmp_log.header()["snapshot_epoch"] == 3

    def test_compact_atomic_replace(self, tmp_log, tmp_snapshot):
        """compact 中に .tmp が一時的にできて、最終的には消えている"""
        soul = create_empty_soul()
        tmp_snapshot.write_bytes(sb.encode_soul(soul))
        tmp_log.append(_make_input_record("atomic"))
        sl.compact_with_log(tmp_snapshot, tmp_log, _replay_input_simple)
        # .tmp が残っていない
        tmp_path = tmp_snapshot.with_suffix(tmp_snapshot.suffix + ".tmp")
        assert not tmp_path.exists()

    def test_compact_preserves_existing_state(self, tmp_log, tmp_snapshot):
        """既存 SOUL の state が compact 後も保持される (上書きされない)"""
        soul = create_empty_soul()
        # 既存エピソードを 1 件
        soul["episodic_memory"]["recent"].append(
            create_episode(
                text="pre-existing",
                importance=0.7,
                emotion={"name": "joy", "intensity": 0.7},
                personality_signals=[],
                topics=[],
                values=[],
                summary="pre",
            )
        )
        tmp_snapshot.write_bytes(sb.encode_soul(soul, epoch_counter=5))

        # ログに 2 件追加
        tmp_log.append(_make_input_record("new_1"))
        tmp_log.append(_make_input_record("new_2"))

        sl.compact_with_log(tmp_snapshot, tmp_log, _replay_input_simple)

        decoded = sb.decode_soul(tmp_snapshot.read_bytes())
        # 既存 1 + 追加 2 = 3 件
        assert len(decoded["episodic_memory"]["recent"]) == 3
        assert decoded["episodic_memory"]["recent"][0]["raw_text"] == "pre-existing"
        assert decoded["episodic_memory"]["recent"][2]["raw_text"] == "new_2"

    def test_compact_with_no_snapshot_uses_initial(self, tmp_log, tmp_snapshot):
        # snapshot は未作成
        assert not tmp_snapshot.exists()
        tmp_log.append(_make_input_record("first"))

        sl.compact_with_log(
            tmp_snapshot,
            tmp_log,
            _replay_input_simple,
            initial_soul=create_empty_soul(),
        )
        # snapshot ができている
        assert tmp_snapshot.exists()
        decoded = sb.decode_soul(tmp_snapshot.read_bytes())
        assert len(decoded["episodic_memory"]["recent"]) == 1

    def test_compact_with_encryption(self, tmp_log, tmp_snapshot):
        """master_key を encode_kwargs に渡して暗号化付き compact"""
        master = b"\xAB" * 32
        soul = create_empty_soul()
        tmp_snapshot.write_bytes(
            sb.encode_soul(soul, master_key=master, epoch_counter=0)
        )
        tmp_log.append(_make_input_record("secret"))

        sl.compact_with_log(
            tmp_snapshot,
            tmp_log,
            _replay_input_simple,
            encode_kwargs={"master_key": master},
            decode_kwargs={"master_key": master},
        )
        # 鍵ありで読める
        decoded = sb.decode_soul(tmp_snapshot.read_bytes(), master_key=master)
        assert decoded["episodic_memory"]["recent"][0]["raw_text"] == "secret"
        # 鍵なしでは raw_text が読めない
        decoded_redacted = sb.decode_soul(tmp_snapshot.read_bytes())
        assert sb.SEC_EPISODIC_RECENT in decoded_redacted["_meta"]["redacted_sections"]


# ════════════════════════════════════════════════════════════════════════════
# テスト: should_compact 判定
# ════════════════════════════════════════════════════════════════════════════


class TestShouldCompact:
    def test_size_trigger(self, tmp_log):
        tmp_log.init()
        # 64KB を超えるダミーレコード
        big_record = {"op": sl.OP_INPUT, "blob": "x" * 70000}
        tmp_log.append(big_record)
        ok, reason = sl.should_compact(tmp_log)
        assert ok
        assert "size" in reason

    def test_no_trigger_small(self, tmp_log):
        tmp_log.init()
        tmp_log.append(_make_input_record("small"))
        ok, reason = sl.should_compact(tmp_log)
        assert not ok


# ════════════════════════════════════════════════════════════════════════════
# テスト: append_input_record ヘルパ
# ════════════════════════════════════════════════════════════════════════════


class TestAppendInputHelper:
    def test_append_input_creates_log(self, tmp_log):
        sl.append_input_record(
            tmp_log,
            raw_text="hello",
            delta={"importance": 0.5, "emotion": {"name": "calm", "intensity": 0.3},
                   "personality_signals": [], "topics": [], "values": [], "summary": "hi"},
            epoch=1,
        )
        records = tmp_log.records()
        assert len(records) == 1
        assert records[0]["op"] == sl.OP_INPUT
        assert records[0]["raw_text"] == "hello"
        assert records[0]["epoch"] == 1
        assert "ts_ms" in records[0]


# ════════════════════════════════════════════════════════════════════════════
# テスト: 暗号化 log (per-entry AES-256-GCM)
# ════════════════════════════════════════════════════════════════════════════


class TestEncryptedLog:
    MASTER = b"\x42" * 32
    OTHER = b"\x99" * 32

    def test_encrypted_round_trip(self, tmp_path):
        log = sl.SoulLog(tmp_path / "enc.log", master_key=self.MASTER)
        log.init(snapshot_epoch=0)
        # ヘッダの暗号化フラグが立つ
        assert log.header()["encrypted"] is True

        for i in range(5):
            log.append({"op": sl.OP_INPUT, "epoch": i, "secret": f"hidden_{i}"})

        records = log.records()
        assert len(records) == 5
        for i, r in enumerate(records):
            assert r["secret"] == f"hidden_{i}"

    def test_no_plaintext_payload_on_disk(self, tmp_path):
        log = sl.SoulLog(tmp_path / "enc.log", master_key=self.MASTER)
        for i in range(3):
            log.append({
                "op": sl.OP_INPUT,
                "raw_text": f"distinctive_secret_token_{i}_xyz",
            })
        raw = (tmp_path / "enc.log").read_bytes()
        assert b"distinctive_secret_token" not in raw
        # header magic だけは平文
        assert raw[0:4] == b"GPLG"

    def test_unreadable_without_master_key(self, tmp_path):
        log_w = sl.SoulLog(tmp_path / "enc.log", master_key=self.MASTER)
        log_w.append({"op": sl.OP_INPUT, "secret": "abc"})

        log_r = sl.SoulLog(tmp_path / "enc.log")  # 鍵なし
        with pytest.raises(ValueError, match="encrypted"):
            log_r.records()

    def test_wrong_master_key_fails(self, tmp_path):
        log_w = sl.SoulLog(tmp_path / "enc.log", master_key=self.MASTER)
        log_w.append({"op": sl.OP_INPUT, "secret": "abc"})

        log_r = sl.SoulLog(tmp_path / "enc.log", master_key=self.OTHER)
        with pytest.raises(ValueError, match="decryption failed"):
            log_r.records()

    def test_entry_tampering_detected(self, tmp_path):
        path = tmp_path / "enc.log"
        log = sl.SoulLog(path, master_key=self.MASTER)
        log.append({"op": sl.OP_INPUT, "secret": "untampered"})

        # ファイルの最後のあたり (ciphertext+tag) を 1 ビット反転
        data = bytearray(path.read_bytes())
        data[-1] ^= 0x01
        path.write_bytes(bytes(data))

        log2 = sl.SoulLog(path, master_key=self.MASTER)
        with pytest.raises(ValueError, match="decryption failed"):
            log2.records()

    def test_swap_entries_detected(self, tmp_path):
        """位置 (index) に AAD でバインドしているので、エントリの順序入れ替え
        も復号失敗する。
        """
        path = tmp_path / "enc.log"
        log = sl.SoulLog(path, master_key=self.MASTER)
        log.append({"op": sl.OP_INPUT, "id": 1})
        log.append({"op": sl.OP_INPUT, "id": 2})

        data = path.read_bytes()
        # ヘッダ後の 2 エントリを取り出して順序を入れ替えた新ファイルを作る
        cur = sl.LOG_HEADER_SIZE
        import struct as _s
        entries = []
        while cur + 4 <= len(data):
            length = _s.unpack_from("<I", data, cur)[0]
            cur += 4
            entries.append((length, data[cur : cur + length]))
            cur += length
        assert len(entries) == 2

        # 入れ替えて書き戻す
        new = bytearray(data[: sl.LOG_HEADER_SIZE])
        for length, body in reversed(entries):
            new += _s.pack("<I", length) + body
        path.write_bytes(bytes(new))

        log2 = sl.SoulLog(path, master_key=self.MASTER)
        with pytest.raises(ValueError, match="decryption failed"):
            log2.records()

    def test_plaintext_log_still_works(self, tmp_path):
        """暗号化対応後も平文 log は引き続き読み書き可能 (後方互換)"""
        log = sl.SoulLog(tmp_path / "plain.log")  # 鍵なし
        log.init()
        assert log.header()["encrypted"] is False
        log.append({"op": sl.OP_INPUT, "value": "plaintext"})
        records = log.records()
        assert len(records) == 1
        assert records[0]["value"] == "plaintext"

    def test_mixing_modes_rejected(self, tmp_path):
        """既存平文 log に master_key 付きで append しようとすると拒否"""
        log = sl.SoulLog(tmp_path / "plain.log")
        log.init()
        log.append({"op": sl.OP_INPUT, "value": "x"})
        # 同じファイルを master_key 付きで開いて append
        log_enc = sl.SoulLog(tmp_path / "plain.log", master_key=self.MASTER)
        with pytest.raises(ValueError, match="plaintext"):
            log_enc.append({"op": sl.OP_INPUT, "value": "y"})

    def test_compact_with_encrypted_log(self, tmp_path):
        """compact_with_log が暗号化 log でも動く"""
        snapshot = tmp_path / "soul.bin"
        log = sl.SoulLog(tmp_path / "enc.log", master_key=self.MASTER)

        # snapshot は無し、 initial_soul で起動
        soul = create_empty_soul()
        log.append({
            "op": sl.OP_INPUT,
            "ts_ms": 1700000000000,
            "epoch": 1,
            "raw_text": "encrypted journal entry",
            "delta": {
                "importance": 0.5,
                "emotion": {"name": "calm", "intensity": 0.3},
                "personality_signals": [],
                "topics": [],
                "values": [],
                "summary": "x",
            },
        })

        def replay(s, r):
            s["episodic_memory"]["recent"].append(
                create_episode(
                    text=r["raw_text"],
                    importance=r["delta"]["importance"],
                    emotion=r["delta"]["emotion"],
                    personality_signals=[],
                    topics=[],
                    values=[],
                    summary="x",
                )
            )
            return s

        stats = sl.compact_with_log(
            snapshot, log, replay,
            initial_soul=soul,
            encode_kwargs={"master_key": self.MASTER},
            decode_kwargs={"master_key": self.MASTER},
        )
        assert stats["records_replayed"] == 1
        # snapshot に反映されている
        from soul_binary import decode_soul
        decoded = decode_soul(snapshot.read_bytes(), master_key=self.MASTER)
        assert decoded["episodic_memory"]["recent"][0]["raw_text"] == "encrypted journal entry"
        # log は clear されている (新 epoch で再 init)
        assert log.entry_count() == 0
        # クリア後も encrypted フラグは保たれる (master_key を持つ SoulLog だから)
        assert log.header()["encrypted"] is True

    def test_epoch_change_uses_different_key(self, tmp_path):
        """異なる snapshot_epoch では HKDF info が変わって鍵も変わる。

        snapshot_epoch=0 で書いたログを epoch=5 として読もうとしても復号失敗する。
        """
        path = tmp_path / "epoch.log"
        log = sl.SoulLog(path, master_key=self.MASTER)
        log.init(snapshot_epoch=0)
        log.append({"op": sl.OP_INPUT, "v": "epoch0"})

        # ヘッダの snapshot_epoch を 5 に書き換える (= IV と鍵の両方が変わる)
        data = bytearray(path.read_bytes())
        import struct as _s
        _s.pack_into("<I", data, 8, 5)
        path.write_bytes(bytes(data))

        log2 = sl.SoulLog(path, master_key=self.MASTER)
        with pytest.raises(ValueError, match="decryption failed"):
            log2.records()
