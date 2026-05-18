"""
Ghost-Printer A6 — `.soul` バイナリエンコーダ/デコーダ (v1, prototype)

仕様: specs/a6_soul_binary_format.md
採択: 2026-05-03 qvp 承認 (推奨案で全項採択)

実装範囲 (v1 prototype):
  ✅ HEADER 128B / TOC / セクション分割
  ✅ CORE_IDENTITY / STATS の raw struct
  ✅ EPISODIC / SEMANTIC / TEMPORAL / WATCHPOINTS の CBOR (+ zstd)
  ✅ STRING_TABLE による topics/values の重複排除
  ✅ Merkle root による改ざん検知
  ✅ JSON ⇄ binary ラウンドトリップ
  ⏳ AES-256-GCM 暗号化 (フラグは予約済み、本実装は次フェーズ)
  ⏳ APPEND_LOG / コンパクション (次フェーズ)
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cbor2

try:
    import zstandard as _zstd

    _ZSTD_COMPRESSOR = _zstd.ZstdCompressor(level=10)
    _ZSTD_DECOMPRESSOR = _zstd.ZstdDecompressor()
    _HAVE_ZSTD = True
except ImportError:  # pragma: no cover
    import gzip as _gz

    _HAVE_ZSTD = False

# AES-GCM は cryptography ライブラリ。インストールされていなくても暗号化なしの
# encode/decode は動くようにする (ImportError は実際に暗号化を要求された時に投げる)。
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes as _crypto_hashes

    _HAVE_CRYPTO = True
except ImportError:  # pragma: no cover
    _HAVE_CRYPTO = False


# ════════════════════════════════════════════════════════════════════════════
# 定数
# ════════════════════════════════════════════════════════════════════════════

MAGIC = b"GPSL"
HEADER_SIZE = 128
TOC_ENTRY_SIZE = 12
FORMAT_MAJOR = 0
FORMAT_MINOR = 1  # v0.1 — prototype。Approved 後に v1.0 へ昇格予定

# format_version エンコーディング: major(5bit) | minor(11bit) — Q7採択
FORMAT_VERSION = (FORMAT_MAJOR << 11) | FORMAT_MINOR

# 性格次元の正準順序 (soul_schema.py:create_empty_soul と一致させること)
DIM_ORDER = [
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
    "curiosity",
    "creativity",
    "empathy",
    "risk_tolerance",
    "independence",
]

# セクション ID
SEC_CORE_IDENTITY = 0x01
SEC_EPISODIC_RECENT = 0x02
SEC_EPISODIC_COMPRESSED = 0x03
SEC_SEMANTIC_MAP = 0x04
SEC_TEMPORAL_PATTERNS = 0x05
SEC_WATCHPOINTS = 0x06
SEC_STATS = 0x07
SEC_EMOTIONAL_STATE = 0x08
SEC_HEALTH_VITALS = 0x09
SEC_LOCATION_TRACE = 0x0A
SEC_SOCIAL_GRAPH = 0x0B
SEC_MERKLE_PROOF = 0x0C  # 部分ビュー用の Merkle 証明バンドル (CBOR)
SEC_STRING_TABLE = 0xFE
SEC_APPEND_LOG = 0xFF

# エンコーディング種別
ENC_RAW = 0
ENC_CBOR = 1
ENC_CBOR_ZSTD = 2
ENC_AES_GCM = 3       # AES-256-GCM。中身は [inner_enc:1B][inner_payload]
ENC_CBOR_GZIP = 4     # zstandard 不在環境用フォールバック

# ヘッダ flags ビット
HEADER_FLAG_ENCRYPTED = 1 << 0
HEADER_FLAG_COMPRESSED_BODY = 1 << 1
HEADER_FLAG_SEALED = 1 << 2
HEADER_FLAG_PARTIAL = 1 << 3

# master_key を渡しただけのときに既定で暗号化するセクション。
# Q6 採択: raw_text を含む EPISODIC_RECENT は暗号化セクション扱い。
# §3 採択: HEALTH/LOCATION/SOCIAL は機微カテゴリとして既定暗号化。
DEFAULT_ENCRYPTED_SECTIONS: set[int] = {
    SEC_EPISODIC_RECENT,
    SEC_EPISODIC_COMPRESSED,
    SEC_EMOTIONAL_STATE,
    SEC_HEALTH_VITALS,
    SEC_LOCATION_TRACE,
    SEC_SOCIAL_GRAPH,
}

# Disclosure Category (capability_token.py の文字列値) → section ID の対応。
# Permission Gateway がこのマップで「読みたいカテゴリ」→「必要な鍵セット」に変換する。
CATEGORY_TO_SECTIONS: dict[str, list[int]] = {
    "core_identity":       [SEC_CORE_IDENTITY],
    "episodic_memory":     [SEC_EPISODIC_RECENT, SEC_EPISODIC_COMPRESSED],
    "emotional_state":     [SEC_EMOTIONAL_STATE],
    "behavioral_patterns": [SEC_TEMPORAL_PATTERNS, SEC_WATCHPOINTS],
    "health_vitals":       [SEC_HEALTH_VITALS],
    "location_movement":   [SEC_LOCATION_TRACE],
    "social_graph":        [SEC_SOCIAL_GRAPH],
    "interests_values":    [SEC_SEMANTIC_MAP],
}

# 常に開示されるセクション (system metadata, 暗号化対象外)
ALWAYS_DISCLOSED_SECTIONS: set[int] = {SEC_STATS, SEC_STRING_TABLE}


# ════════════════════════════════════════════════════════════════════════════
# ヘルパ
# ════════════════════════════════════════════════════════════════════════════


def _compress(data: bytes) -> tuple[bytes, int]:
    """zstd 優先、無ければ gzip。返り値は (圧縮済み, encoding)"""
    if _HAVE_ZSTD:
        return _ZSTD_COMPRESSOR.compress(data), ENC_CBOR_ZSTD
    return _gz.compress(data), ENC_CBOR_GZIP


def _decompress(data: bytes, encoding: int) -> bytes:
    if encoding == ENC_CBOR_ZSTD:
        if not _HAVE_ZSTD:
            raise RuntimeError(
                "zstd セクションを読むには zstandard が必要です: pip install zstandard"
            )
        return _ZSTD_DECOMPRESSOR.decompress(data)
    if encoding == ENC_CBOR_GZIP:
        return _gz.decompress(data)
    raise ValueError(f"Unknown compression encoding: {encoding}")


def _iso_to_ms(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _ms_to_iso(ms: int) -> str | None:
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


# ════════════════════════════════════════════════════════════════════════════
# 鍵導出と暗号化 (AES-256-GCM, セクション単位)
# ════════════════════════════════════════════════════════════════════════════


def _require_crypto() -> None:
    if not _HAVE_CRYPTO:
        raise RuntimeError(
            "cryptography が必要です: pip install cryptography>=42.0.0"
        )


def derive_section_key(master_key: bytes, section_id: int) -> bytes:
    """
    マスタ鍵からセクション専用鍵を HKDF-SHA256 で導出する (32B 出力)。
    info ラベル: `soul:0xNN` (仕様 §7.1 採択)。
    """
    _require_crypto()
    if len(master_key) < 32:
        raise ValueError("master_key must be at least 32 bytes")
    info = f"soul:0x{section_id:02x}".encode("ascii")
    return HKDF(
        algorithm=_crypto_hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
    ).derive(master_key)


def derive_section_iv(epoch_counter: int, section_id: int) -> bytes:
    """
    決定論 IV (12B) = SHA256(epoch_counter:LE32 || section_id:U8)[:12]。

    GCM では (key, IV) ペアのユニーク性が必須。epoch_counter は書込みごとに
    +1 する規約により、 (section_key, IV) の組合せが衝突しないことを保証する。
    """
    h = hashlib.sha256()
    h.update(epoch_counter.to_bytes(4, "little"))
    h.update(bytes([section_id]))
    return h.digest()[:12]


def _aad_for_section(section_id: int, epoch_counter: int) -> bytes:
    """AAD: ciphertext を別セクションに付け替える攻撃を防ぐためのバインド情報"""
    return bytes([section_id]) + epoch_counter.to_bytes(4, "little")


def encrypt_section_payload(
    inner_encoding: int,
    plaintext: bytes,
    master_key: bytes,
    section_id: int,
    epoch_counter: int,
) -> bytes:
    """
    セクション本体を AES-256-GCM で封印する。

    封印対象は [inner_encoding:1B][plaintext] の連結。これにより復号後に元の
    エンコーディング (raw / cbor / cbor_zstd 等) を復元できる。
    """
    _require_crypto()
    key = derive_section_key(master_key, section_id)
    iv = derive_section_iv(epoch_counter, section_id)
    aad = _aad_for_section(section_id, epoch_counter)
    blob = bytes([inner_encoding & 0xFF]) + plaintext
    return AESGCM(key).encrypt(iv, blob, aad)


def decrypt_section_payload(
    ciphertext: bytes,
    master_key: bytes,
    section_id: int,
    epoch_counter: int,
) -> tuple[int, bytes]:
    """
    暗号化セクション本体を復号して (inner_encoding, plaintext) を返す。
    """
    _require_crypto()
    key = derive_section_key(master_key, section_id)
    iv = derive_section_iv(epoch_counter, section_id)
    aad = _aad_for_section(section_id, epoch_counter)
    blob = AESGCM(key).decrypt(iv, ciphertext, aad)
    if not blob:
        raise ValueError("Empty plaintext after decryption")
    return blob[0], blob[1:]


def derive_keys_for_categories(
    master_key: bytes,
    granted_categories: set[str],
) -> dict[int, bytes]:
    """
    開示が許されたカテゴリ集合から、必要なセクション鍵だけを導出する。

    Permission Gateway / Capability Token から呼ぶことを想定。
    HIDDEN なカテゴリの鍵は決して導出されないので、そのセクションは
    暗号学的に開示できない。
    """
    keys: dict[int, bytes] = {}
    for cat in granted_categories:
        for sid in CATEGORY_TO_SECTIONS.get(cat, []):
            keys[sid] = derive_section_key(master_key, sid)
    return keys


def derive_keys_for_token(master_key: bytes, token: Any) -> dict[int, bytes]:
    """
    CapabilityToken (capability_token.CapabilityToken) から鍵セットを導出する。

    token.scope.categories の各エントリで granularity が HIDDEN でないものだけ
    対象にする。token そのものは依存を避けるため duck typing で扱う。
    """
    granted: set[str] = set()
    scope = getattr(token, "scope", None)
    if scope is None:
        return {}
    cats = getattr(scope, "categories", {})
    for key, rule in cats.items():
        # rule は CategoryRule (新) / tuple (旧) / dict (シリアライズ後) を許容
        gran_value = None
        if hasattr(rule, "granularity"):
            gran = rule.granularity
            gran_value = gran.value if hasattr(gran, "value") else gran
        elif isinstance(rule, tuple) and len(rule) >= 1:
            g = rule[0]
            gran_value = g.value if hasattr(g, "value") else g
        elif isinstance(rule, dict):
            gran_value = rule.get("granularity")
        if gran_value and gran_value != "hidden":
            cat_name = key.value if hasattr(key, "value") else str(key)
            granted.add(cat_name)
    return derive_keys_for_categories(master_key, granted)


_EMPTY_LEAF = hashlib.sha256(b"").digest()


class MerkleTree:
    """SHA256 ベースのバイナリ Merkle Tree。

    部分開示ビュー (partial view) で「ある section が確かに原本 SOUL に
    含まれていた」ことを暗号学的に証明するために使う。

    使い方:
        tree = MerkleTree([sha256(s) for s in sections])
        root = tree.root
        proof = tree.proof_for(leaf_index)
        ok = MerkleTree.verify(leaf_hash, leaf_index, proof, root)

    - 奇数ノード時のパディングは常に SHA256(b"") (= _EMPTY_LEAF)
    - 葉が空なら root = bytes(32) (32 バイトのゼロ)
    """

    def __init__(self, leaves: list[bytes]) -> None:
        self.leaves: list[bytes] = list(leaves)
        self.levels: list[list[bytes]] = self._build()

    def _build(self) -> list[list[bytes]]:
        if not self.leaves:
            return [[bytes(32)]]
        levels: list[list[bytes]] = [list(self.leaves)]
        nodes = list(self.leaves)
        while len(nodes) > 1:
            if len(nodes) % 2 == 1:
                nodes.append(_EMPTY_LEAF)
                # パディングはこのレベルにも反映 (proof_for で参照するため)
                levels[-1] = list(nodes)
            new_nodes = [
                hashlib.sha256(nodes[i] + nodes[i + 1]).digest()
                for i in range(0, len(nodes), 2)
            ]
            levels.append(new_nodes)
            nodes = new_nodes
        return levels

    @property
    def root(self) -> bytes:
        return self.levels[-1][0]

    def proof_for(self, leaf_index: int) -> list[bytes]:
        """指定リーフの兄弟ハッシュ列 (root までの上り順) を返す。"""
        if not self.leaves:
            return []
        if leaf_index < 0 or leaf_index >= len(self.leaves):
            raise IndexError(f"leaf_index {leaf_index} out of range")
        proof: list[bytes] = []
        idx = leaf_index
        for level in self.levels[:-1]:
            sibling_index = idx ^ 1
            if sibling_index < len(level):
                proof.append(level[sibling_index])
            else:
                proof.append(_EMPTY_LEAF)
            idx //= 2
        return proof

    @staticmethod
    def verify(
        leaf_hash: bytes,
        leaf_index: int,
        proof: list[bytes],
        expected_root: bytes,
    ) -> bool:
        """葉と siblings から root を再構成して expected_root と比較する。"""
        node = leaf_hash
        idx = leaf_index
        for sibling in proof:
            if idx % 2 == 0:
                node = hashlib.sha256(node + sibling).digest()
            else:
                node = hashlib.sha256(sibling + node).digest()
            idx //= 2
        return node == expected_root


def _merkle_root(leaves: list[bytes]) -> bytes:
    """後方互換: 単に root だけ返す。内部で MerkleTree を使う。"""
    return MerkleTree(leaves).root


# ════════════════════════════════════════════════════════════════════════════
# 文字列テーブル
# ════════════════════════════════════════════════════════════════════════════


class StringTable:
    """topics / values などの繰り返し文字列を ID で参照するための辞書"""

    def __init__(self) -> None:
        self._table: list[str] = []
        self._index: dict[str, int] = {}

    def intern(self, s: str) -> int:
        if s not in self._index:
            self._index[s] = len(self._table)
            self._table.append(s)
        return self._index[s]

    def to_bytes(self) -> bytes:
        out = struct.pack("<I", len(self._table))
        for s in self._table:
            encoded = s.encode("utf-8")
            if len(encoded) > 0xFFFF:
                raise ValueError(f"String too long for table: {len(encoded)} bytes")
            out += struct.pack("<H", len(encoded)) + encoded
        return out

    @classmethod
    def from_bytes(cls, data: bytes) -> StringTable:
        st = cls()
        if len(data) < 4:
            return st
        cnt = struct.unpack_from("<I", data, 0)[0]
        cur = 4
        for _ in range(cnt):
            ln = struct.unpack_from("<H", data, cur)[0]
            cur += 2
            s = data[cur : cur + ln].decode("utf-8")
            st._table.append(s)
            st._index[s] = len(st._table) - 1
            cur += ln
        return st

    def get(self, idx: int) -> str | None:
        if 0 <= idx < len(self._table):
            return self._table[idx]
        return None


# ════════════════════════════════════════════════════════════════════════════
# Encode
# ════════════════════════════════════════════════════════════════════════════


def encode_soul(
    soul: dict,
    *,
    owner_id_hash: bytes | None = None,
    device_id: bytes | None = None,
    epoch_counter: int = 0,
    cortex_link: int = 0,
    flags: int = 0,
    master_key: bytes | None = None,
    encrypt_sections: set[int] | None = None,
    granted_sections: set[int] | None = None,
) -> bytes:
    """SOUL辞書を `.soul` バイナリにエンコードする

    Args:
        master_key: マスタ鍵 (32B 以上)。指定すると AES-256-GCM 暗号化が有効。
        encrypt_sections: 暗号化対象 section_id 集合。
            None かつ master_key 指定時は DEFAULT_ENCRYPTED_SECTIONS を使う。
        granted_sections: 部分開示ビュー用。指定するとこの ID 集合 (+
            ALWAYS_DISCLOSED_SECTIONS) のみが書かれ、HEADER_FLAG_PARTIAL が立つ。
    """

    if master_key is not None and len(master_key) < 32:
        raise ValueError("master_key must be at least 32 bytes")
    if encrypt_sections and master_key is None:
        raise ValueError("encrypt_sections requires master_key")
    if master_key is not None and encrypt_sections is None:
        encrypt_sections = set(DEFAULT_ENCRYPTED_SECTIONS)
    if encrypt_sections is None:
        encrypt_sections = set()

    string_table = StringTable()

    # ── セクション本体を構築 ──
    sections: list[tuple[int, int, bytes]] = []

    # 0x01 CORE_IDENTITY (raw struct, 80B)
    core = soul.get("core_identity", {})
    core_bytes = b""
    for dim in DIM_ORDER:
        d = core.get(dim, {"mu": 0.5, "sigma": 0.30})
        core_bytes += struct.pack("<ff", float(d["mu"]), float(d["sigma"]))
    sections.append((SEC_CORE_IDENTITY, ENC_RAW, core_bytes))

    # 0x07 STATS (raw struct, 16B)
    stats = soul.get("stats", {})
    stats_bytes = struct.pack(
        "<IIQ",
        int(stats.get("total_episodes", 0)),
        int(stats.get("total_updates", 0)),
        int(stats.get("last_evolve_ts_ms", 0)),
    )
    sections.append((SEC_STATS, ENC_RAW, stats_bytes))

    # 0x02 EPISODIC_RECENT (CBOR + zstd)
    episodes_cbor: list[dict[str, Any]] = []
    for ep in soul.get("episodic_memory", {}).get("recent", []):
        ep_cbor = _episode_to_cbor(ep, string_table)
        episodes_cbor.append(ep_cbor)

    if episodes_cbor:
        compressed, enc = _compress(cbor2.dumps(episodes_cbor))
        sections.append((SEC_EPISODIC_RECENT, enc, compressed))

    # 0x03 EPISODIC_COMPRESSED (CBOR + zstd)
    compressed_eps = soul.get("episodic_memory", {}).get("compressed", [])
    if compressed_eps:
        compressed, enc = _compress(cbor2.dumps(compressed_eps))
        sections.append((SEC_EPISODIC_COMPRESSED, enc, compressed))

    # 0x04 SEMANTIC_MAP (CBOR)
    semantic = soul.get("semantic_map", {"interests": {}, "values": {}})
    sem_payload = {
        "interests": [
            [string_table.intern(k), int(v)]
            for k, v in semantic.get("interests", {}).items()
        ],
        "values": [
            [string_table.intern(k), int(v)]
            for k, v in semantic.get("values", {}).items()
        ],
    }
    sections.append((SEC_SEMANTIC_MAP, ENC_CBOR, cbor2.dumps(sem_payload)))

    # 0x05 TEMPORAL_PATTERNS (CBOR)
    temporal = soul.get("temporal_patterns", {"active_hours": [], "routines": []})
    sections.append((SEC_TEMPORAL_PATTERNS, ENC_CBOR, cbor2.dumps(temporal)))

    # 0x06 WATCHPOINTS (CBOR) — 空でも書く（reader 側の分岐を減らす）
    wps = soul.get("watchpoints", [])
    sections.append((SEC_WATCHPOINTS, ENC_CBOR, cbor2.dumps(wps)))

    # 0x08-0x0B EMOTIONAL_STATE / HEALTH_VITALS / LOCATION_TRACE / SOCIAL_GRAPH
    # 現行 soul_schema には無いが、外部入力で乗ってきた場合は CBOR で書く。
    for top_key, sid in (
        ("emotional_state", SEC_EMOTIONAL_STATE),
        ("health_vitals", SEC_HEALTH_VITALS),
        ("location_movement", SEC_LOCATION_TRACE),
        ("social_graph", SEC_SOCIAL_GRAPH),
    ):
        payload = soul.get(top_key)
        if payload not in (None, {}, []):
            sections.append((sid, ENC_CBOR, cbor2.dumps(payload)))

    # 0xFE STRING_TABLE — 最後に確定
    sections.append((SEC_STRING_TABLE, ENC_RAW, string_table.to_bytes()))

    # ── 部分開示フィルタ (granted_sections) ──
    if granted_sections is not None:
        kept = set(granted_sections) | ALWAYS_DISCLOSED_SECTIONS
        sections = [s for s in sections if s[0] in kept]
        flags |= HEADER_FLAG_PARTIAL

    # ── 暗号化パス (encrypt_sections に含まれるセクションを AES-GCM 封印) ──
    if encrypt_sections:
        encrypted_sections: list[tuple[int, int, bytes]] = []
        for sid, enc, body in sections:
            if sid in encrypt_sections and sid not in ALWAYS_DISCLOSED_SECTIONS:
                ciphertext = encrypt_section_payload(
                    enc, body, master_key, sid, epoch_counter
                )
                encrypted_sections.append((sid, ENC_AES_GCM, ciphertext))
            else:
                encrypted_sections.append((sid, enc, body))
        sections = encrypted_sections
        flags |= HEADER_FLAG_ENCRYPTED

    # ── レイアウト計算 ──
    sections.sort(key=lambda x: x[0])
    toc_offset = HEADER_SIZE
    toc_size = len(sections) * TOC_ENTRY_SIZE
    section_offsets: list[int] = []
    cursor = toc_offset + toc_size
    for _, _, body in sections:
        section_offsets.append(cursor)
        cursor += len(body)
    body_end = cursor

    # ── Merkle Root ──
    leaves = [hashlib.sha256(body).digest() for _, _, body in sections]
    merkle = _merkle_root(leaves)

    # ── TOC ──
    toc_bytes = b""
    for i, (sid, enc, body) in enumerate(sections):
        toc_bytes += struct.pack(
            "<BBIIH",
            sid,
            enc,
            section_offsets[i],
            len(body),
            0,  # flags
        )
    assert len(toc_bytes) == toc_size

    # ── HEADER ──
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    created_ms = _iso_to_ms(soul.get("created_at")) or now_ms
    updated_ms = now_ms

    if owner_id_hash is None:
        owner_id_hash = bytes(32)
    if device_id is None:
        device_id = bytes(16)
    if len(owner_id_hash) != 32:
        raise ValueError("owner_id_hash must be 32 bytes")
    if len(device_id) != 16:
        raise ValueError("device_id must be 16 bytes")

    body_length = body_end - toc_offset

    header = bytearray(HEADER_SIZE)
    header[0:4] = MAGIC
    struct.pack_into("<H", header, 4, FORMAT_VERSION)
    struct.pack_into("<H", header, 6, flags)
    struct.pack_into("<q", header, 8, created_ms)
    struct.pack_into("<q", header, 16, updated_ms)
    header[24:56] = owner_id_hash
    struct.pack_into("<I", header, 56, toc_offset)
    struct.pack_into("<H", header, 60, len(sections))
    struct.pack_into("<H", header, 62, cortex_link)
    struct.pack_into("<I", header, 64, body_length)
    crc = zlib.crc32(bytes(header[0:68])) & 0xFFFFFFFF
    struct.pack_into("<I", header, 68, crc)
    header[72:104] = merkle
    struct.pack_into("<I", header, 104, epoch_counter)
    header[108:124] = device_id
    # 124-127 reserved (zero)

    out = bytes(header) + toc_bytes
    for _, _, body in sections:
        out += body
    return out


def _episode_to_cbor(ep: dict, st: StringTable) -> dict[str, Any]:
    """エピソードを CBOR ペイロード形式に変換する (旧/新 emotion 両対応)"""
    ts_ms = _iso_to_ms(ep.get("timestamp"))

    psigs: list[dict[str, Any]] = []
    for sig in ep.get("personality_signals", []):
        dim_name = sig.get("dimension")
        if dim_name in DIM_ORDER:
            psigs.append(
                {
                    "dim": DIM_ORDER.index(dim_name),
                    "value": float(sig.get("value", 0.0)),
                    "confidence": float(sig.get("confidence", 0.0)),
                }
            )

    payload: dict[str, Any] = {
        "id": ep.get("id"),
        "ts_ms": ts_ms,
        "raw_text": ep.get("raw_text"),
        "summary": ep.get("summary"),
        "importance": float(ep.get("importance", 0.0)),
        "weight": float(ep.get("weight", 1.0)),
        "personality_signals": psigs,
        "topic_ids": [st.intern(t) for t in ep.get("topics", [])],
        "value_ids": [st.intern(v) for v in ep.get("values", [])],
    }
    # スキーマ揺れ対応: 任意フィールド
    for key in (
        "emotion",
        "emotion_distribution",
        "topic_distribution",
        "value_signals",
        "context",
        "acoustic",
        "embedding_hash",
        "nearest_episodes",
        "cluster_assignment",
    ):
        if key in ep and ep[key] not in (None, {}, []):
            payload[key] = ep[key]
    # None を取り除いてサイズを抑える
    return {k: v for k, v in payload.items() if v is not None}


# ════════════════════════════════════════════════════════════════════════════
# Decode
# ════════════════════════════════════════════════════════════════════════════


def decode_soul(
    data: bytes,
    *,
    verify: bool = True,
    master_key: bytes | None = None,
    section_keys: dict[int, bytes] | None = None,
) -> dict:
    """`.soul` バイナリを SOUL 辞書に戻す

    Args:
        verify: CRC32 と Merkle Root による整合性検査を行う
        master_key: マスタ鍵。暗号化セクションに対し HKDF で鍵を導出して復号
        section_keys: セクション固有鍵。section_keys[sid] が
            あれば master_key より優先

    暗号化セクションについて鍵が無い (or 復号失敗) 場合、そのセクションは
    Redacted として扱われ、出力 dict の `_meta.redacted_sections` に
    section_id が記録される。
    """

    if len(data) < HEADER_SIZE:
        raise ValueError(f"Too short: {len(data)} < {HEADER_SIZE}")
    if bytes(data[0:4]) != MAGIC:
        raise ValueError(f"Bad magic: {bytes(data[0:4])!r}")

    # CRC を最優先で検証する: ヘッダが破損していたら他フィールドの解釈は当てにならない
    crc_stored = struct.unpack_from("<I", data, 68)[0]
    if verify:
        crc_calc = zlib.crc32(bytes(data[0:68])) & 0xFFFFFFFF
        if crc_calc != crc_stored:
            raise ValueError(
                f"Header CRC mismatch: stored={crc_stored:08x} calc={crc_calc:08x}"
            )

    fv = struct.unpack_from("<H", data, 4)[0]
    fv_major = (fv >> 11) & 0x1F
    fv_minor = fv & 0x7FF
    if fv_major > FORMAT_MAJOR:
        raise ValueError(
            f"Unsupported format major: {fv_major} (this reader supports up to {FORMAT_MAJOR})"
        )

    flags = struct.unpack_from("<H", data, 6)[0]
    created_ms = struct.unpack_from("<q", data, 8)[0]
    updated_ms = struct.unpack_from("<q", data, 16)[0]
    owner_id_hash = bytes(data[24:56])
    toc_offset = struct.unpack_from("<I", data, 56)[0]
    section_count = struct.unpack_from("<H", data, 60)[0]
    cortex_link = struct.unpack_from("<H", data, 62)[0]
    body_length = struct.unpack_from("<I", data, 64)[0]
    merkle_stored = bytes(data[72:104])
    epoch_counter = struct.unpack_from("<I", data, 104)[0]
    device_id = bytes(data[108:124])

    # ── TOC ──
    toc: list[tuple[int, int, int, int, int]] = []
    for i in range(section_count):
        off = toc_offset + i * TOC_ENTRY_SIZE
        sid, enc, sec_off, sec_len, sec_flags = struct.unpack_from("<BBIIH", data, off)
        toc.append((sid, enc, sec_off, sec_len, sec_flags))

    # ── セクション読み出し + Merkle 検証 ──
    # Merkle 検証は ciphertext のまま行う (暗号化前の本文ではなく、ファイルに
    # 書かれているバイト列を対象にする)。これにより鍵を持たない受信者でも
    # 「自分が見ている部分は改ざんされていない」ことを確認できる。
    section_data: dict[int, tuple[int, bytes]] = {}
    redacted_sections: list[int] = []
    leaves: list[bytes] = []
    for sid, enc, sec_off, sec_len, _ in toc:
        body = bytes(data[sec_off : sec_off + sec_len])
        if len(body) != sec_len:
            raise ValueError(f"Section 0x{sid:02X} truncated")
        leaves.append(hashlib.sha256(body).digest())

        if enc == ENC_AES_GCM:
            # 復号鍵を解決
            key: bytes | None = None
            if section_keys is not None and sid in section_keys:
                key = section_keys[sid]
            elif master_key is not None:
                key = derive_section_key(master_key, sid)

            if key is None:
                # 鍵なし → Redacted。ciphertext は捨てて section_data に置かない
                redacted_sections.append(sid)
                continue

            try:
                _require_crypto()
                iv = derive_section_iv(epoch_counter, sid)
                aad = _aad_for_section(sid, epoch_counter)
                blob = AESGCM(key).decrypt(iv, body, aad)
                if not blob:
                    raise ValueError("Empty plaintext")
                inner_enc = blob[0]
                inner_body = blob[1:]
                section_data[sid] = (inner_enc, inner_body)
            except Exception as exc:
                # 鍵が違う / 改ざんされている場合
                raise ValueError(
                    f"Section 0x{sid:02X} decryption failed: {exc}"
                ) from exc
        else:
            section_data[sid] = (enc, body)

    if verify:
        merkle_calc = _merkle_root(leaves)
        if merkle_calc != merkle_stored:
            raise ValueError(
                f"Merkle root mismatch:\n  stored={merkle_stored.hex()}\n  calc={merkle_calc.hex()}"
            )

    # ── STRING_TABLE を先に復元 ──
    string_table = StringTable()
    if SEC_STRING_TABLE in section_data:
        _, st_bytes = section_data[SEC_STRING_TABLE]
        string_table = StringTable.from_bytes(st_bytes)

    # ── SOUL 辞書を組み立て ──
    soul: dict[str, Any] = {
        "version": f"{fv_major}.{fv_minor}.0",
        "owner_hash": owner_id_hash.hex() if any(owner_id_hash) else "anonymous",
        "created_at": _ms_to_iso(created_ms) or "",
        "updated_at": _ms_to_iso(updated_ms) or "",
        "_meta": {
            "epoch_counter": epoch_counter,
            "device_id": device_id.hex() if any(device_id) else None,
            "cortex_link": cortex_link,
            "flags": flags,
            "format_version": (fv_major, fv_minor),
            "merkle_root": merkle_stored.hex(),
            "redacted_sections": redacted_sections,
            "is_partial": bool(flags & HEADER_FLAG_PARTIAL),
            "is_encrypted_archive": bool(flags & HEADER_FLAG_ENCRYPTED),
        },
    }

    # CORE_IDENTITY
    if SEC_CORE_IDENTITY in section_data:
        _, body = section_data[SEC_CORE_IDENTITY]
        ci: dict[str, Any] = {}
        for i, dim in enumerate(DIM_ORDER):
            mu, sigma = struct.unpack_from("<ff", body, i * 8)
            ci[dim] = {"mu": round(mu, 4), "sigma": round(sigma, 4)}
        soul["core_identity"] = ci
    else:
        soul["core_identity"] = {}

    # STATS
    if SEC_STATS in section_data:
        _, body = section_data[SEC_STATS]
        te, tu, last_evolve = struct.unpack_from("<IIQ", body, 0)
        soul["stats"] = {"total_episodes": te, "total_updates": tu}
        if last_evolve:
            soul["stats"]["last_evolve_ts_ms"] = last_evolve
    else:
        soul["stats"] = {"total_episodes": 0, "total_updates": 0}

    # EPISODIC_RECENT
    recent: list[dict[str, Any]] = []
    if SEC_EPISODIC_RECENT in section_data:
        enc, body = section_data[SEC_EPISODIC_RECENT]
        if enc in (ENC_CBOR_ZSTD, ENC_CBOR_GZIP):
            body = _decompress(body, enc)
        eps_cbor = cbor2.loads(body)
        for ep_cbor in eps_cbor:
            recent.append(_episode_from_cbor(ep_cbor, string_table))

    compressed: list[Any] = []
    if SEC_EPISODIC_COMPRESSED in section_data:
        enc, body = section_data[SEC_EPISODIC_COMPRESSED]
        if enc in (ENC_CBOR_ZSTD, ENC_CBOR_GZIP):
            body = _decompress(body, enc)
        compressed = cbor2.loads(body)

    soul["episodic_memory"] = {"recent": recent, "compressed": compressed}

    # SEMANTIC_MAP
    if SEC_SEMANTIC_MAP in section_data:
        _, body = section_data[SEC_SEMANTIC_MAP]
        sem = cbor2.loads(body)
        interests: dict[str, int] = {}
        for tid, v in sem.get("interests", []):
            s = string_table.get(tid)
            if s is not None:
                interests[s] = v
        values: dict[str, int] = {}
        for vid, v in sem.get("values", []):
            s = string_table.get(vid)
            if s is not None:
                values[s] = v
        soul["semantic_map"] = {"interests": interests, "values": values}
    else:
        soul["semantic_map"] = {"interests": {}, "values": {}}

    # TEMPORAL_PATTERNS
    if SEC_TEMPORAL_PATTERNS in section_data:
        _, body = section_data[SEC_TEMPORAL_PATTERNS]
        soul["temporal_patterns"] = cbor2.loads(body)
    else:
        soul["temporal_patterns"] = {"active_hours": [], "routines": []}

    # WATCHPOINTS
    if SEC_WATCHPOINTS in section_data:
        _, body = section_data[SEC_WATCHPOINTS]
        soul["watchpoints"] = cbor2.loads(body)
    else:
        soul["watchpoints"] = []

    # 拡張カテゴリ (EMOTIONAL_STATE / HEALTH_VITALS / LOCATION / SOCIAL)
    for top_key, sid in (
        ("emotional_state", SEC_EMOTIONAL_STATE),
        ("health_vitals", SEC_HEALTH_VITALS),
        ("location_movement", SEC_LOCATION_TRACE),
        ("social_graph", SEC_SOCIAL_GRAPH),
    ):
        if sid in section_data:
            _, body = section_data[sid]
            soul[top_key] = cbor2.loads(body)

    return soul


def _episode_from_cbor(ep_cbor: dict, st: StringTable) -> dict[str, Any]:
    ep: dict[str, Any] = dict(ep_cbor)

    # timestamp 復元
    ts_ms = ep.pop("ts_ms", 0)
    if ts_ms:
        ep["timestamp"] = _ms_to_iso(ts_ms)

    # personality_signals: dim → dimension に戻す
    psigs = ep.get("personality_signals", [])
    out_psigs = []
    for sig in psigs:
        dim_idx = sig.get("dim")
        out = dict(sig)
        out.pop("dim", None)
        if dim_idx is not None and 0 <= dim_idx < len(DIM_ORDER):
            out["dimension"] = DIM_ORDER[dim_idx]
        out_psigs.append(out)
    ep["personality_signals"] = out_psigs

    # topics / values 復元
    topic_ids = ep.pop("topic_ids", [])
    ep["topics"] = [st.get(i) for i in topic_ids if st.get(i) is not None]
    value_ids = ep.pop("value_ids", [])
    ep["values"] = [st.get(i) for i in value_ids if st.get(i) is not None]

    return ep


# ════════════════════════════════════════════════════════════════════════════
# 部分読み出し (Permission Gateway 連携の土台)
# ════════════════════════════════════════════════════════════════════════════


def read_section(data: bytes, section_id: int) -> bytes | None:
    """指定セクションの本体バイトだけを返す。Merkle 検証はしない。"""
    if len(data) < HEADER_SIZE or bytes(data[0:4]) != MAGIC:
        raise ValueError("Not a .soul file")
    toc_offset = struct.unpack_from("<I", data, 56)[0]
    section_count = struct.unpack_from("<H", data, 60)[0]
    for i in range(section_count):
        off = toc_offset + i * TOC_ENTRY_SIZE
        sid, _, sec_off, sec_len, _ = struct.unpack_from("<BBIIH", data, off)
        if sid == section_id:
            return bytes(data[sec_off : sec_off + sec_len])
    return None


# ════════════════════════════════════════════════════════════════════════════
# 部分ビュー (Merkle 部分証明付き)
# ════════════════════════════════════════════════════════════════════════════


def extract_partial_view_bytes(
    original_bytes: bytes,
    granted_section_ids: set[int],
    *,
    include_proofs: bool = True,
) -> bytes:
    """元の SOUL バイナリから granted セクションのみを取り出した部分ビューを作る。

    - **section bytes は verbatim でコピー** (再エンコードしない) → 元 SOUL の
      Merkle Tree のリーフハッシュが変わらないので proof が成立する。
    - epoch_counter / device_id / owner_id_hash を元から保持 → 受信者が
      section_keys (= HKDF(master, "soul:0xNN")) で各セクションを復号可能。
    - `include_proofs=True` のとき SEC_MERKLE_PROOF (0x0C) を1セクション追加し、
      その中に CBOR で {original_root, total_leaves, proofs[{sid, leaf_index, siblings}]}
      を持つ。
    - HEADER_FLAG_PARTIAL を立てる。 ヘッダの merkle_root は **新部分ビューの
      ルート** (自己整合性用) であって、原本ルートは proof セクション内にある。
    """
    if len(original_bytes) < HEADER_SIZE or original_bytes[0:4] != MAGIC:
        raise ValueError("Not a .soul file")

    # ── 元ヘッダ解析 ──
    fv = struct.unpack_from("<H", original_bytes, 4)[0]
    flags_orig = struct.unpack_from("<H", original_bytes, 6)[0]
    created_ms = struct.unpack_from("<q", original_bytes, 8)[0]
    updated_ms = struct.unpack_from("<q", original_bytes, 16)[0]
    owner_id_hash = bytes(original_bytes[24:56])
    toc_offset = struct.unpack_from("<I", original_bytes, 56)[0]
    section_count = struct.unpack_from("<H", original_bytes, 60)[0]
    cortex_link = struct.unpack_from("<H", original_bytes, 62)[0]
    epoch_counter = struct.unpack_from("<I", original_bytes, 104)[0]
    device_id = bytes(original_bytes[108:124])

    # ── 元セクション + Merkle Tree (原本) ──
    original_sections: list[tuple[int, int, bytes]] = []
    leaves: list[bytes] = []
    for i in range(section_count):
        off = toc_offset + i * TOC_ENTRY_SIZE
        sid, enc, sec_off, sec_len, _sf = struct.unpack_from(
            "<BBIIH", original_bytes, off
        )
        body = bytes(original_bytes[sec_off : sec_off + sec_len])
        original_sections.append((sid, enc, body))
        leaves.append(hashlib.sha256(body).digest())

    tree = MerkleTree(leaves)
    original_root = tree.root

    # ── granted (+ 常時開示) を選別、元 index を残す ──
    kept: list[tuple[int, int, bytes, int | None]] = []
    for i, (sid, enc, body) in enumerate(original_sections):
        if sid in granted_section_ids or sid in ALWAYS_DISCLOSED_SECTIONS:
            kept.append((sid, enc, body, i))

    # ── proof bundle セクション ──
    if include_proofs:
        proof_records: list[dict] = []
        for sid, enc, body, original_index in kept:
            assert original_index is not None
            siblings = tree.proof_for(original_index)
            proof_records.append(
                {
                    "sid": sid,
                    "leaf_index": original_index,
                    "siblings": siblings,
                }
            )
        proof_bundle = {
            "original_root": original_root,
            "total_leaves": len(leaves),
            "proofs": proof_records,
        }
        kept.append(
            (SEC_MERKLE_PROOF, ENC_CBOR, cbor2.dumps(proof_bundle), None)
        )

    # ── レイアウト計算 ──
    kept.sort(key=lambda x: x[0])
    new_count = len(kept)
    new_toc_size = new_count * TOC_ENTRY_SIZE
    new_toc_offset = HEADER_SIZE
    section_offsets: list[int] = []
    cursor = new_toc_offset + new_toc_size
    for _sid, _enc, body, _ in kept:
        section_offsets.append(cursor)
        cursor += len(body)
    body_length = cursor - new_toc_offset

    # ── 部分ビュー自身の Merkle Root (自己整合性用) ──
    new_leaves = [hashlib.sha256(body).digest() for _s, _e, body, _i in kept]
    new_merkle = MerkleTree(new_leaves).root

    # ── TOC ──
    toc_bytes = b""
    for i, (sid, enc, body, _orig_idx) in enumerate(kept):
        toc_bytes += struct.pack(
            "<BBIIH",
            sid,
            enc,
            section_offsets[i],
            len(body),
            0,  # entry flags
        )

    # ── HEADER ──
    header = bytearray(HEADER_SIZE)
    header[0:4] = MAGIC
    struct.pack_into("<H", header, 4, fv)
    new_flags = flags_orig | HEADER_FLAG_PARTIAL
    struct.pack_into("<H", header, 6, new_flags)
    struct.pack_into("<q", header, 8, created_ms)
    struct.pack_into("<q", header, 16, updated_ms)
    header[24:56] = owner_id_hash
    struct.pack_into("<I", header, 56, new_toc_offset)
    struct.pack_into("<H", header, 60, new_count)
    struct.pack_into("<H", header, 62, cortex_link)
    struct.pack_into("<I", header, 64, body_length)
    crc = zlib.crc32(bytes(header[0:68])) & 0xFFFFFFFF
    struct.pack_into("<I", header, 68, crc)
    header[72:104] = new_merkle
    # 重要: epoch_counter は元と同じ → 受信者が IV 派生で復号可能
    struct.pack_into("<I", header, 104, epoch_counter)
    header[108:124] = device_id
    # 124-127 reserved (zero)

    out = bytes(header) + toc_bytes
    for _sid, _enc, body, _ in kept:
        out += body
    return out


def verify_partial_view(
    view_bytes: bytes,
    *,
    expected_original_root: bytes | None = None,
) -> dict:
    """部分ビューに同梱された Merkle proof を検証する。

    Returns:
        {
            "valid": bool,                   # すべての proof が原本 root に到達したか
            "original_root": bytes | None,   # proof bundle が主張する原本 root
            "verified_sections": list[int],  # 検証成功した section_id
            "errors": list[str],
        }

    Args:
        expected_original_root: 既知の信頼できる root と一致するか追加チェックする。
            None なら proof 内の `original_root` をそのまま信用 (= 自己整合性のみ)。
    """
    result: dict = {
        "valid": False,
        "original_root": None,
        "verified_sections": [],
        "errors": [],
    }
    if len(view_bytes) < HEADER_SIZE or view_bytes[0:4] != MAGIC:
        result["errors"].append("Not a .soul file")
        return result

    proof_body = read_section(view_bytes, SEC_MERKLE_PROOF)
    if proof_body is None:
        result["errors"].append("No SEC_MERKLE_PROOF section")
        return result

    try:
        bundle = cbor2.loads(proof_body)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"Bad proof CBOR: {exc}")
        return result

    original_root = bytes(bundle["original_root"])
    proofs = bundle["proofs"]
    result["original_root"] = original_root

    if expected_original_root is not None and expected_original_root != original_root:
        result["errors"].append(
            f"Root mismatch: expected={expected_original_root.hex()[:16]}…"
            f" got={original_root.hex()[:16]}…"
        )
        return result

    # 部分ビュー内のセクション本体 (sid -> bytes)
    view_section_bytes: dict[int, bytes] = {}
    for sid, _enc, off, length in list_sections(view_bytes):
        if sid == SEC_MERKLE_PROOF:
            continue
        view_section_bytes[sid] = bytes(view_bytes[off : off + length])

    verified: list[int] = []
    for proof in proofs:
        sid = int(proof["sid"])
        if sid == SEC_MERKLE_PROOF:
            continue
        leaf_index = int(proof["leaf_index"])
        siblings = [bytes(s) for s in proof["siblings"]]
        body = view_section_bytes.get(sid)
        if body is None:
            result["errors"].append(
                f"Section 0x{sid:02X} in proofs but not in view"
            )
            continue
        leaf_hash = hashlib.sha256(body).digest()
        if MerkleTree.verify(leaf_hash, leaf_index, siblings, original_root):
            verified.append(sid)
        else:
            result["errors"].append(
                f"Section 0x{sid:02X} proof verification failed"
            )

    result["verified_sections"] = verified
    result["valid"] = len(result["errors"]) == 0 and len(verified) > 0
    return result


def list_sections(data: bytes) -> list[tuple[int, int, int, int]]:
    """TOC を (section_id, encoding, offset, length) のリストで返す"""
    if len(data) < HEADER_SIZE or bytes(data[0:4]) != MAGIC:
        raise ValueError("Not a .soul file")
    toc_offset = struct.unpack_from("<I", data, 56)[0]
    section_count = struct.unpack_from("<H", data, 60)[0]
    out = []
    for i in range(section_count):
        off = toc_offset + i * TOC_ENTRY_SIZE
        sid, enc, sec_off, sec_len, _ = struct.unpack_from("<BBIIH", data, off)
        out.append((sid, enc, sec_off, sec_len))
    return out


# ════════════════════════════════════════════════════════════════════════════
# JSON ⇄ Binary ファイルブリッジ
# ════════════════════════════════════════════════════════════════════════════


def encode_soul_file(soul_json_path: str | Path, soul_bin_path: str | Path) -> dict:
    """soul.json → soul.bin。サイズ等のメタ情報を返す"""
    soul_json_path = Path(soul_json_path)
    soul_bin_path = Path(soul_bin_path)
    with open(soul_json_path, "r", encoding="utf-8") as f:
        soul = json.load(f)
    binary = encode_soul(soul)
    soul_bin_path.parent.mkdir(parents=True, exist_ok=True)
    soul_bin_path.write_bytes(binary)
    return {
        "json_size": soul_json_path.stat().st_size,
        "bin_size": len(binary),
        "ratio": len(binary) / max(soul_json_path.stat().st_size, 1),
        "section_count": struct.unpack_from("<H", binary, 60)[0],
        "merkle_root": binary[72:104].hex(),
    }


def decode_soul_file(soul_bin_path: str | Path, soul_json_path: str | Path) -> dict:
    """soul.bin → soul.json"""
    soul_bin_path = Path(soul_bin_path)
    soul_json_path = Path(soul_json_path)
    binary = soul_bin_path.read_bytes()
    soul = decode_soul(binary)
    soul_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(soul_json_path, "w", encoding="utf-8") as f:
        json.dump(soul, f, ensure_ascii=False, indent=2)
    return {"sections": len(list_sections(binary)), "bin_size": len(binary)}


# ════════════════════════════════════════════════════════════════════════════
# CLI (簡易ダンプ用)
# ════════════════════════════════════════════════════════════════════════════


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Ghost-Printer .soul binary tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enc = sub.add_parser("encode", help="soul.json → soul.bin")
    p_enc.add_argument("input")
    p_enc.add_argument("output")

    p_dec = sub.add_parser("decode", help="soul.bin → soul.json")
    p_dec.add_argument("input")
    p_dec.add_argument("output")

    p_inspect = sub.add_parser("inspect", help="soul.bin の TOC を表示")
    p_inspect.add_argument("input")

    args = parser.parse_args()

    if args.cmd == "encode":
        info = encode_soul_file(args.input, args.output)
        print(
            f"✅ {args.output}: {info['bin_size']} B "
            f"(JSON {info['json_size']} B → ratio {info['ratio']:.2%}), "
            f"{info['section_count']} sections, "
            f"merkle={info['merkle_root'][:16]}..."
        )
    elif args.cmd == "decode":
        info = decode_soul_file(args.input, args.output)
        print(f"✅ {args.output}: {info['bin_size']} B, {info['sections']} sections")
    elif args.cmd == "inspect":
        data = Path(args.input).read_bytes()
        print(f"file: {args.input} ({len(data)} bytes)")
        print(f"magic: {data[0:4]!r}")
        fv = struct.unpack_from('<H', data, 4)[0]
        print(f"version: {(fv >> 11) & 0x1F}.{fv & 0x7FF}  flags=0x{struct.unpack_from('<H', data, 6)[0]:04x}")
        print(f"merkle: {data[72:104].hex()}")
        print(f"sections:")
        names = {
            0x01: "CORE_IDENTITY",
            0x02: "EPISODIC_RECENT",
            0x03: "EPISODIC_COMPRESSED",
            0x04: "SEMANTIC_MAP",
            0x05: "TEMPORAL_PATTERNS",
            0x06: "WATCHPOINTS",
            0x07: "STATS",
            0x08: "EMOTIONAL_STATE",
            0x09: "HEALTH_VITALS",
            0x0A: "LOCATION_TRACE",
            0x0B: "SOCIAL_GRAPH",
            0xFE: "STRING_TABLE",
            0xFF: "APPEND_LOG",
        }
        encs = {0: "raw", 1: "cbor", 2: "cbor+zstd", 3: "aes-gcm", 4: "cbor+gzip"}
        for sid, enc, off, ln in list_sections(data):
            name = names.get(sid, f"0x{sid:02X}")
            print(f"  0x{sid:02X} {name:<22s}  enc={encs.get(enc, '?'):<10s}  @0x{off:06x}  {ln} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
