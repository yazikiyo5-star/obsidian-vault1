"""
Ghost-Printer A6 — ShadowStorage: JSON + Binary + Log を協調させる永続化レイヤ

設計:
  - JSON (`.json`): 既存パイプライン互換のため毎回フル書き換え (デバッグ可読)
  - Binary (`.soul`): 暗号化付きスナップショット。普段は触らず、コンパクション時に更新
  - Log (`.soul.log`): 1 update ごとに 1 エントリ追記 (fsync)。SD/eMMC への純 append

更新フロー:
  1. caller が `soul_engine.update_soul()` で in-memory soul を更新
  2. caller が `ShadowStorage.append_update(soul, raw_text=..., delta=...)` を呼ぶ
  3. 内部で:
     a. JSON を全書き
     b. Log に 1 エントリ追記
     c. bin が無い or log が閾値超 → 現在の soul を bin に dump して log を clear

ロードフロー:
  ShadowStorage.load() → bin を decode (鍵あれば暗号化セクションも) → 未消費 log を replay
  (replay は in-memory のみ。disk には書き戻さない。明示的に compact() を呼べば書き戻す)
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from soul_binary import (
    HEADER_SIZE,
    decode_soul,
    encode_soul,
)
from soul_log import (
    OP_INPUT,
    SoulLog,
    append_input_record,
    should_compact,
    DEFAULT_COMPACTION_SIZE_THRESHOLD,
)


# soul_engine への依存は遅延 import (循環回避 + httpx 等の遷延依存を切り離し)
def _update_soul_lazy(soul: dict, delta: dict, raw_text: str, context: dict | None) -> dict:
    from soul_engine import update_soul

    return update_soul(soul, delta, raw_text, context)


def _save_json_lazy(soul: dict, path: str | Path) -> None:
    from soul_schema import save_soul

    save_soul(soul, str(path))


def _load_json_lazy(path: str | Path) -> dict:
    from soul_schema import load_soul

    return load_soul(str(path))


def _create_empty_lazy() -> dict:
    from soul_schema import create_empty_soul

    return create_empty_soul()


# ════════════════════════════════════════════════════════════════════════════
# ShadowStorage
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class ShadowStorage:
    """SOUL を JSON + Binary + Log の3経路で持続化する。"""

    json_path: Path | None = None
    bin_path: Path | None = None
    log_path: Path | None = None
    master_key: bytes | None = None
    compact_size_threshold: int = DEFAULT_COMPACTION_SIZE_THRESHOLD

    def __post_init__(self) -> None:
        if isinstance(self.json_path, str):
            self.json_path = Path(self.json_path)
        if isinstance(self.bin_path, str):
            self.bin_path = Path(self.bin_path)
        if isinstance(self.log_path, str):
            self.log_path = Path(self.log_path)
        if self.bin_path and not self.log_path:
            raise ValueError("bin_path requires log_path (incremental updates need a log)")

    # ── 内部ヘルパ ──

    @property
    def log(self) -> SoulLog | None:
        if self.log_path is None:
            return None
        return SoulLog(self.log_path, master_key=self.master_key)

    def _encode_kwargs(self, epoch: int) -> dict:
        kw: dict[str, Any] = {"epoch_counter": epoch}
        if self.master_key is not None:
            kw["master_key"] = self.master_key
        return kw

    def _decode_kwargs(self) -> dict:
        kw: dict[str, Any] = {}
        if self.master_key is not None:
            kw["master_key"] = self.master_key
        return kw

    def _read_snapshot_epoch(self) -> int:
        if self.bin_path is None or not self.bin_path.exists():
            return 0
        with open(self.bin_path, "rb") as f:
            data = f.read(HEADER_SIZE)
        if len(data) < HEADER_SIZE:
            return 0
        return struct.unpack_from("<I", data, 104)[0]

    def _dump_snapshot(self, soul: dict, epoch: int) -> int:
        """in-memory soul を bin にアトミックに書く。サイズ B を返す。"""
        if self.bin_path is None:
            return 0
        soul_to_encode = dict(soul)
        soul_to_encode.pop("_meta", None)
        bytes_out = encode_soul(soul_to_encode, **self._encode_kwargs(epoch))
        self.bin_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.bin_path.with_suffix(self.bin_path.suffix + ".tmp")
        with open(tmp, "wb") as f:
            f.write(bytes_out)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.bin_path)
        return len(bytes_out)

    # ── 公開API: 書込み ──

    def save_full(self, soul: dict, *, epoch: int | None = None) -> dict:
        """フル書込み: JSON + bin を強制書き換え、log は init する。

        初期化や明示的なフラッシュに使う。
        """
        stats: dict[str, Any] = {}
        if self.json_path:
            _save_json_lazy(soul, self.json_path)
            stats["json_path"] = str(self.json_path)

        snapshot_epoch = epoch if epoch is not None else self._read_snapshot_epoch()
        if self.bin_path:
            stats["bin_size"] = self._dump_snapshot(soul, snapshot_epoch)
            if self.log_path:
                SoulLog(self.log_path, master_key=self.master_key).init(snapshot_epoch=snapshot_epoch)

        stats["snapshot_epoch"] = snapshot_epoch
        return stats

    def append_update(
        self,
        soul: dict,
        *,
        raw_text: str,
        delta: dict,
        context: dict | None = None,
    ) -> dict:
        """1 update を全経路に書込む。

        - JSON: フル書き換え (legacy 互換)
        - Log: append (最も SD/eMMC フレンドリ)
        - Bin: 必要時のみ更新 (初回 or compaction 閾値超)

        Returns: 統計情報
        """
        stats: dict[str, Any] = {
            "wrote_json": False,
            "log_size": 0,
            "compacted": False,
            "compact_reason": None,
        }

        # 1) JSON 全書き
        if self.json_path:
            _save_json_lazy(soul, self.json_path)
            stats["wrote_json"] = True

        # 2) Log 追記
        log_obj: SoulLog | None = None
        if self.log_path:
            log_obj = SoulLog(self.log_path, master_key=self.master_key)
            if not log_obj.exists():
                log_obj.init(snapshot_epoch=self._read_snapshot_epoch())
            current = self._read_snapshot_epoch()
            seq = log_obj.entry_count() + 1
            append_input_record(
                log_obj,
                raw_text=raw_text,
                delta=delta,
                context=context,
                epoch=current + seq,
            )
            stats["log_size"] = log_obj.size()

        # 3) Bin 更新判定
        if self.bin_path is not None:
            bin_missing = not self.bin_path.exists()
            log_overflow = False
            log_reason = ""
            if log_obj is not None:
                log_overflow, log_reason = should_compact(
                    log_obj, size_threshold=self.compact_size_threshold
                )

            if bin_missing or log_overflow:
                # 現在の in-memory soul を新スナップショットとして書く。
                # epoch_counter はこれまでのスナップショット epoch + log エントリ数。
                new_epoch = self._read_snapshot_epoch() + (
                    log_obj.entry_count() if log_obj else 0
                )
                stats["bin_size"] = self._dump_snapshot(soul, new_epoch)
                if log_obj is not None:
                    log_obj.clear(snapshot_epoch=new_epoch)
                stats["compacted"] = True
                stats["compact_reason"] = (
                    "bin_missing" if bin_missing else log_reason
                )
                stats["new_epoch"] = new_epoch

        return stats

    # ── 公開API: 読込み ──

    def load(self) -> dict:
        """bin + log を fold して in-memory soul を返す。

        bin が無い場合は JSON にフォールバック、それも無ければ空 SOUL。
        log エントリは disk には書き戻さず、in-memory にだけ反映する
        (compact() を呼ぶと書き戻す)。
        """
        # 1) bin 優先
        if self.bin_path is not None and self.bin_path.exists():
            soul = decode_soul(self.bin_path.read_bytes(), **self._decode_kwargs())
            if self.log_path:
                log = SoulLog(self.log_path, master_key=self.master_key)
                if log.exists():
                    for record in log.iter_records():
                        if record.get("op") == OP_INPUT:
                            soul = _update_soul_lazy(
                                soul,
                                delta=record["delta"],
                                raw_text=record["raw_text"],
                                context=record.get("context"),
                            )
            return soul

        # 2) JSON フォールバック
        if self.json_path and self.json_path.exists():
            return _load_json_lazy(self.json_path)

        # 3) 空 SOUL
        return _create_empty_lazy()

    # ── 公開API: 明示コンパクション ──

    def compact(self, soul: dict | None = None) -> dict:
        """log を bin に折り畳み、log をクリアする。

        soul を渡せばそれをそのまま新スナップショットとして書く (高速)。
        渡さなければ load() で in-memory state を再構築してから書く。
        """
        if self.bin_path is None:
            raise RuntimeError("bin_path is not configured")

        if soul is None:
            soul = self.load()

        log = self.log
        log_count = log.entry_count() if log and log.exists() else 0
        new_epoch = self._read_snapshot_epoch() + log_count
        bin_size = self._dump_snapshot(soul, new_epoch)
        if log:
            log.clear(snapshot_epoch=new_epoch)
        return {"bin_size": bin_size, "new_epoch": new_epoch, "compacted_entries": log_count}
