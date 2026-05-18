"""
Ghost-Printer A6 — `.soul.log` 追記専用ジャーナル + コンパクション

仕様: specs/a6_soul_binary_format.md §4.9 + Q5 採択
採択方針 (Q5): コンパクションは毎日 02:00 + ログ 64KB 超で即時。

設計:
  - 1 update につき 1 エントリ追記。ファイル全体は再書き込みしない
  - エントリは [u32 length][CBOR record]。fsync で耐久性
  - 途中で電源断しても、最後の length-prefix が完結していなければ
    そのエントリは破棄して replay 可能 (前方互換のクラッシュ耐性)
  - replay_fn は呼出し側が渡す (soul_engine への直接依存を切り離す)
  - コンパクションは tmp ファイル → os.replace の原子交換
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path
from typing import Any, Callable, Iterator

import cbor2

from soul_binary import decode_soul, encode_soul

# ════════════════════════════════════════════════════════════════════════════
# ログファイルフォーマット
# ════════════════════════════════════════════════════════════════════════════

LOG_MAGIC = b"GPLG"
LOG_HEADER_SIZE = 16
LOG_VERSION = 1

# Header flags (bit 0..15)
LOG_FLAG_ENCRYPTED = 1 << 0   # 各エントリが AES-256-GCM で封印済み

# Op codes (CBOR record の "op" フィールド)
OP_INPUT = 1            # 完全入力 replay (raw_text + delta from extractor + context)
OP_CORE_UPDATE = 2      # core_identity の dim を直接更新 (デバッグ用)
OP_EPISODE_ADD = 3      # エピソードを追加 (extractor 通さず)
OP_DECAY = 4            # 重み減衰 1 サイクル
OP_DISTILL = 5          # 特定エピソードを distill
OP_WATCHPOINT = 6       # WP 関連オペ
OP_STRING_TABLE_EXTEND = 7  # interning 拡張


# ════════════════════════════════════════════════════════════════════════════
# SoulLog
# ════════════════════════════════════════════════════════════════════════════


class SoulLog:
    """`.soul.log` の追記・読出し・クリアを担う。

    File format:
        [4B  magic "GPLG"]
        [2B  version (LE u16)]
        [2B  flags (LE u16)] — bit0 = encrypted
        [4B  snapshot_epoch — 直近コンパクション時の epoch_counter (LE u32)]
        [4B  reserved]
        ─── 以下、エントリの繰り返し ───
        [4B  body length (LE u32)]
        [N   body — 平文 CBOR、または AES-GCM 暗号化済 CBOR (ciphertext+tag)]

    暗号化モード (master_key 指定時):
        - 鍵: HKDF-SHA256(master_key, info="soul_log:epoch_{snapshot_epoch}") → 32B
        - IV: SHA256("soul_log_iv:" + epoch:LE32 + index:LE32)[:12] (決定論)
        - AAD: "soul_log:" + epoch:LE32 + index:LE32 (位置・log間 swap 防御)
        - 鍵は snapshot_epoch ごとに更新されるので、コンパクション後の log は別鍵。
        - 不変条件: 同一 epoch では index が単調増加 (= IV 一意)。 log.clear()
          は必ず epoch を進める (compact_with_log / ShadowStorage が保証する)。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        master_key: bytes | None = None,
    ) -> None:
        self.path = Path(path)
        self.master_key = master_key

    # ── 初期化 ──

    def init(self, snapshot_epoch: int = 0) -> None:
        """空のログをディスクに作る。既存があれば上書き。"""
        flags = LOG_FLAG_ENCRYPTED if self.master_key is not None else 0
        header = bytearray(LOG_HEADER_SIZE)
        header[0:4] = LOG_MAGIC
        struct.pack_into("<H", header, 4, LOG_VERSION)
        struct.pack_into("<H", header, 6, flags)
        struct.pack_into("<I", header, 8, snapshot_epoch & 0xFFFFFFFF)
        # 12:16 reserved
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "wb") as f:
            f.write(bytes(header))
            f.flush()
            os.fsync(f.fileno())

    # ── ステータス ──

    def exists(self) -> bool:
        return self.path.exists()

    def size(self) -> int:
        return self.path.stat().st_size if self.exists() else 0

    def header(self) -> dict:
        if not self.exists():
            raise FileNotFoundError(self.path)
        with open(self.path, "rb") as f:
            data = f.read(LOG_HEADER_SIZE)
        if len(data) < LOG_HEADER_SIZE or data[0:4] != LOG_MAGIC:
            raise ValueError(f"Bad SoulLog magic: {data[0:4]!r}")
        flags = struct.unpack_from("<H", data, 6)[0]
        return {
            "version": struct.unpack_from("<H", data, 4)[0],
            "flags": flags,
            "encrypted": bool(flags & LOG_FLAG_ENCRYPTED),
            "snapshot_epoch": struct.unpack_from("<I", data, 8)[0],
        }

    def entry_count(self) -> int:
        """エントリ数を返す (暗号化 log でも復号せずに高速カウント)。"""
        return self._raw_entry_count()

    def _raw_entry_count(self) -> int:
        if not self.exists():
            return 0
        with open(self.path, "rb") as f:
            data = f.read()
        if len(data) < LOG_HEADER_SIZE:
            return 0
        cur = LOG_HEADER_SIZE
        count = 0
        while cur + 4 <= len(data):
            length = struct.unpack_from("<I", data, cur)[0]
            cur += 4
            if cur + length > len(data):
                break
            count += 1
            cur += length
        return count

    # ── 暗号化ヘルパ ──

    def _derive_log_key(self, snapshot_epoch: int) -> bytes:
        from soul_binary import _require_crypto

        _require_crypto()
        from cryptography.hazmat.primitives import hashes as _hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        if self.master_key is None or len(self.master_key) < 32:
            raise ValueError("master_key (>=32B) required for encrypted log")
        info = f"soul_log:epoch_{snapshot_epoch}".encode("ascii")
        return HKDF(
            algorithm=_hashes.SHA256(),
            length=32,
            salt=None,
            info=info,
        ).derive(self.master_key)

    @staticmethod
    def _entry_iv(snapshot_epoch: int, index: int) -> bytes:
        h = hashlib.sha256()
        h.update(b"soul_log_iv:")
        h.update(snapshot_epoch.to_bytes(4, "little"))
        h.update(index.to_bytes(4, "little"))
        return h.digest()[:12]

    @staticmethod
    def _entry_aad(snapshot_epoch: int, index: int) -> bytes:
        return (
            b"soul_log:"
            + snapshot_epoch.to_bytes(4, "little")
            + index.to_bytes(4, "little")
        )

    # ── 追記 ──

    def append(self, record: dict) -> int:
        """エントリを追記して fsync。返り値はファイルサイズ (B)。

        master_key がセットされていればエントリは AES-256-GCM で封印される。
        既存ログと整合性が取れない master_key を渡した場合は ValueError。
        """
        if not self.exists():
            self.init()
        hdr = self.header()
        body = cbor2.dumps(record)

        if hdr["encrypted"]:
            if self.master_key is None:
                raise ValueError(
                    "Log is encrypted but no master_key provided to SoulLog"
                )
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            snapshot_epoch = hdr["snapshot_epoch"]
            index = self._raw_entry_count()
            key = self._derive_log_key(snapshot_epoch)
            iv = self._entry_iv(snapshot_epoch, index)
            aad = self._entry_aad(snapshot_epoch, index)
            body = AESGCM(key).encrypt(iv, body, aad)
        else:
            if self.master_key is not None:
                # ヘッダは平文だが master_key を渡されている → 整合性が取れない
                # (encrypted フラグが立っていないので append しても平文のまま)
                # 安全側: 拒否して呼出側に再 init を促す
                raise ValueError(
                    "Existing log is plaintext but master_key was given. "
                    "Re-init() the log with master_key to enable encryption."
                )

        framed = struct.pack("<I", len(body)) + body
        with open(self.path, "ab") as f:
            f.write(framed)
            f.flush()
            os.fsync(f.fileno())
        return self.path.stat().st_size

    # ── 読出し ──

    def iter_records(self) -> Iterator[dict]:
        """ログを先頭から順に yield。途中の length が不完全なら break。

        暗号化ログの場合は master_key 必須。鍵が不足/誤りなら ValueError。
        """
        if not self.exists():
            return
        with open(self.path, "rb") as f:
            data = f.read()
        if len(data) < LOG_HEADER_SIZE:
            raise ValueError("SoulLog file too short")
        if data[0:4] != LOG_MAGIC:
            raise ValueError(f"Bad SoulLog magic: {data[0:4]!r}")

        flags = struct.unpack_from("<H", data, 6)[0]
        snapshot_epoch = struct.unpack_from("<I", data, 8)[0]
        is_encrypted = bool(flags & LOG_FLAG_ENCRYPTED)

        if is_encrypted and self.master_key is None:
            raise ValueError(
                "SoulLog is encrypted but no master_key was provided"
            )

        if is_encrypted:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            key = self._derive_log_key(snapshot_epoch)
            aesgcm = AESGCM(key)

        cur = LOG_HEADER_SIZE
        index = 0
        while cur + 4 <= len(data):
            length = struct.unpack_from("<I", data, cur)[0]
            cur += 4
            if cur + length > len(data):
                # 途中切断 → 最後のエントリは破棄
                break
            body = data[cur : cur + length]
            cur += length

            if is_encrypted:
                iv = self._entry_iv(snapshot_epoch, index)
                aad = self._entry_aad(snapshot_epoch, index)
                try:
                    body = aesgcm.decrypt(iv, body, aad)
                except Exception as exc:  # InvalidTag 等
                    raise ValueError(
                        f"SoulLog entry {index} decryption failed: {exc}"
                    ) from exc

            yield cbor2.loads(body)
            index += 1

    def records(self) -> list[dict]:
        return list(self.iter_records())

    # ── クリア ──

    def clear(self, snapshot_epoch: int) -> None:
        """ログを空に戻す (コンパクション後に呼ぶ)。

        snapshot_epoch は単調増加でなければならない (= 同一 epoch+index で
        IV を再利用しないための不変条件)。clear 呼出側はこれを保証する。
        """
        self.init(snapshot_epoch=snapshot_epoch)


# ════════════════════════════════════════════════════════════════════════════
# 高レベル: 入力ベースのログ追記
# ════════════════════════════════════════════════════════════════════════════


def append_input_record(
    log: SoulLog,
    *,
    raw_text: str,
    delta: dict,
    context: dict | None = None,
    epoch: int = 0,
    ts_ms: int | None = None,
) -> int:
    """
    soul_engine.update_soul に渡す入力一式をログに残す。
    コンパクション時にこのレコードを `update_soul()` に再供給して状態を再構築する。
    """
    if ts_ms is None:
        from datetime import datetime, timezone

        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    record = {
        "op": OP_INPUT,
        "ts_ms": ts_ms,
        "epoch": epoch,
        "raw_text": raw_text,
        "delta": delta,
    }
    if context:
        record["context"] = context
    return log.append(record)


# ════════════════════════════════════════════════════════════════════════════
# コンパクション
# ════════════════════════════════════════════════════════════════════════════

# replay_fn の型: (soul_dict, record) -> updated_soul_dict
ReplayFn = Callable[[dict, dict], dict]


def compact_with_log(
    snapshot_path: str | Path,
    log: SoulLog,
    replay_fn: ReplayFn,
    *,
    encode_kwargs: dict | None = None,
    decode_kwargs: dict | None = None,
    initial_soul: dict | None = None,
) -> dict:
    """
    snapshot を読み込み、ログを順番に replay して新 snapshot を書き、
    ログをクリアする。原子交換 (.tmp → os.replace) で部分書込みを防ぐ。

    Args:
        snapshot_path: `.soul` ファイル
        log: 対応する SoulLog
        replay_fn: (soul_dict, record) -> updated_soul_dict の関数
        encode_kwargs / decode_kwargs: encode_soul / decode_soul に渡す追加引数
            (master_key, encrypt_sections など)
        initial_soul: snapshot が無い場合の初期 SOUL。 None なら例外

    Returns:
        統計情報 dict (records_replayed / new_epoch / new_size など)
    """
    snapshot_path = Path(snapshot_path)
    encode_kwargs = dict(encode_kwargs or {})
    decode_kwargs = dict(decode_kwargs or {})

    # 1) スナップショット読込 (or 初期 SOUL)
    if snapshot_path.exists():
        soul = decode_soul(snapshot_path.read_bytes(), **decode_kwargs)
        # _meta は decode 由来のメタなので、再 encode 時に持ち越さない
        soul.pop("_meta", None)
        base_epoch = (
            decode_kwargs.get("_meta_epoch")
            if decode_kwargs.get("_meta_epoch") is not None
            else _read_epoch(snapshot_path)
        )
    elif initial_soul is not None:
        soul = dict(initial_soul)
        base_epoch = encode_kwargs.get("epoch_counter", 0)
    else:
        raise FileNotFoundError(
            f"snapshot {snapshot_path} not found and initial_soul not provided"
        )

    # 2) ログ replay
    records_replayed = 0
    new_epoch = base_epoch
    if log.exists():
        for record in log.iter_records():
            soul = replay_fn(soul, record)
            records_replayed += 1
            new_epoch += 1

    # 3) 新 snapshot をアトミックに書込み
    encode_kwargs["epoch_counter"] = new_epoch
    new_bytes = encode_soul(soul, **encode_kwargs)

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(new_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, snapshot_path)

    # 4) ログをクリア
    log.clear(snapshot_epoch=new_epoch)

    return {
        "records_replayed": records_replayed,
        "new_epoch": new_epoch,
        "new_size": len(new_bytes),
    }


def _read_epoch(snapshot_path: Path) -> int:
    """snapshot ヘッダから epoch_counter だけ高速に取り出す"""
    with open(snapshot_path, "rb") as f:
        data = f.read(128)
    return struct.unpack_from("<I", data, 104)[0]


# ════════════════════════════════════════════════════════════════════════════
# Q5 採択: コンパクション判断 (毎日 02:00 + 64KB 超で即時)
# ════════════════════════════════════════════════════════════════════════════

DEFAULT_COMPACTION_SIZE_THRESHOLD = 64 * 1024  # 64 KB


def should_compact(
    log: SoulLog,
    *,
    size_threshold: int = DEFAULT_COMPACTION_SIZE_THRESHOLD,
    daily_local_hour: int | None = 2,
    last_compact_iso: str | None = None,
) -> tuple[bool, str]:
    """
    コンパクションすべきか判定する。

    - size_threshold を超えていれば即時実行
    - daily_local_hour が指定されていて last_compact から 24h 経過していて
      現在時刻がその時刻台に入っていれば実行
    """
    size = log.size()
    if size > size_threshold:
        return True, f"size {size}B > {size_threshold}B"

    if daily_local_hour is None or last_compact_iso is None:
        return False, "no daily trigger"

    from datetime import datetime, timezone

    try:
        last = datetime.fromisoformat(last_compact_iso)
    except ValueError:
        return True, "invalid last_compact_iso"
    now = datetime.now(timezone.utc).astimezone()
    elapsed = (now - last).total_seconds()
    if elapsed >= 23 * 3600 and now.hour == daily_local_hour:
        return True, f"daily window @ {daily_local_hour}:00 local"
    return False, "no trigger"
