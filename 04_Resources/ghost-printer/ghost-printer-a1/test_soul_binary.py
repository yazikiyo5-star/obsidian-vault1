"""
Ghost-Printer A6 — `.soul` バイナリのラウンドトリップとベンチ

走らせ方:
    python -m pytest test_soul_binary.py -v
    python test_soul_binary.py             # 直接実行で bench レポート表示
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import soul_binary as sb
from soul_schema import create_empty_soul, create_episode

HERE = Path(__file__).parent
SOUL_JSON = HERE / "data" / "soul.json"


# ════════════════════════════════════════════════════════════════════════════
# ヘルパ
# ════════════════════════════════════════════════════════════════════════════


def _load_real_soul() -> dict:
    if not SOUL_JSON.exists():
        pytest.skip(f"{SOUL_JSON} not found")
    with open(SOUL_JSON, encoding="utf-8") as f:
        return json.load(f)


def _build_synthetic_soul(n_episodes: int = 10) -> dict:
    soul = create_empty_soul("test_user")
    for i in range(n_episodes):
        ep = create_episode(
            text=f"テストエピソード {i}: 今日はカフェで本を読んだり考え事をしたり。",
            importance=0.5 + 0.05 * (i % 5),
            emotion={"name": ["calm", "joy", "excitement"][i % 3], "intensity": 0.6},
            personality_signals=[
                {"dimension": "openness", "value": 0.7, "confidence": 0.7},
                {"dimension": "curiosity", "value": 0.65, "confidence": 0.6},
            ],
            topics=["reading", "coffee", f"topic_{i}"],
            values=["self-care", "growth"],
            summary=f"summary {i}",
            context={"date": "2026-05-01", "time_of_day": "昼"},
        )
        soul["episodic_memory"]["recent"].append(ep)
    soul["semantic_map"]["interests"] = {"reading": 5, "coffee": 5, "ai": 3}
    soul["semantic_map"]["values"] = {"self-care": 4, "growth": 6}
    soul["stats"]["total_episodes"] = n_episodes
    soul["stats"]["total_updates"] = n_episodes
    return soul


# ════════════════════════════════════════════════════════════════════════════
# テスト
# ════════════════════════════════════════════════════════════════════════════


class TestRoundTrip:
    def test_empty_soul_round_trip(self) -> None:
        """空の SOUL がそのまま帰ってくる"""
        soul = create_empty_soul("alice")
        binary = sb.encode_soul(soul)
        decoded = sb.decode_soul(binary)

        for dim in sb.DIM_ORDER:
            assert decoded["core_identity"][dim]["mu"] == pytest.approx(0.5, abs=1e-3)
            assert decoded["core_identity"][dim]["sigma"] == pytest.approx(0.3, abs=1e-3)
        assert decoded["episodic_memory"]["recent"] == []
        assert decoded["semantic_map"]["interests"] == {}
        assert decoded["stats"]["total_episodes"] == 0

    def test_synthetic_soul_round_trip(self) -> None:
        soul = _build_synthetic_soul(n_episodes=10)
        binary = sb.encode_soul(soul)
        decoded = sb.decode_soul(binary)

        # core_identity 全次元一致
        for dim in sb.DIM_ORDER:
            assert decoded["core_identity"][dim]["mu"] == pytest.approx(
                soul["core_identity"][dim]["mu"], abs=1e-3
            )
        # episode 件数一致
        assert len(decoded["episodic_memory"]["recent"]) == 10
        # 1件目の内容を抜き打ちで照合
        ep_orig = soul["episodic_memory"]["recent"][0]
        ep_decoded = decoded["episodic_memory"]["recent"][0]
        assert ep_decoded["raw_text"] == ep_orig["raw_text"]
        assert ep_decoded["importance"] == pytest.approx(ep_orig["importance"], abs=1e-3)
        # personality_signals: dimension 名が復元される
        assert ep_decoded["personality_signals"][0]["dimension"] == "openness"
        # topics: 文字列テーブル経由で復元
        assert "reading" in ep_decoded["topics"]
        # semantic_map
        assert decoded["semantic_map"]["interests"]["reading"] == 5
        assert decoded["semantic_map"]["values"]["growth"] == 6

    def test_real_soul_json_round_trip(self) -> None:
        """既存 data/soul.json (混在スキーマ) のラウンドトリップ"""
        soul = _load_real_soul()
        binary = sb.encode_soul(soul)
        decoded = sb.decode_soul(binary)

        # core_identity の主要値が保持
        assert decoded["core_identity"]["openness"]["mu"] == pytest.approx(
            soul["core_identity"]["openness"]["mu"], abs=1e-3
        )
        # 件数一致
        assert len(decoded["episodic_memory"]["recent"]) == len(
            soul["episodic_memory"]["recent"]
        )
        # 旧/新 emotion フィールドの両方が存在することを確認
        emotions_seen = set()
        for ep in decoded["episodic_memory"]["recent"]:
            if "emotion" in ep:
                emotions_seen.add("legacy")
            if "emotion_distribution" in ep:
                emotions_seen.add("modern")
        # 実データには両方 ある (data/soul.json の内容より)
        assert "legacy" in emotions_seen, "旧 emotion フィールドが落ちた"


class TestHeader:
    def test_magic_bytes(self) -> None:
        soul = create_empty_soul()
        binary = sb.encode_soul(soul)
        assert binary[0:4] == b"GPSL"

    def test_format_version(self) -> None:
        soul = create_empty_soul()
        binary = sb.encode_soul(soul)
        decoded = sb.decode_soul(binary)
        assert decoded["_meta"]["format_version"] == (sb.FORMAT_MAJOR, sb.FORMAT_MINOR)

    def test_header_crc(self) -> None:
        """ヘッダ1bit反転で CRC エラーが起きる"""
        soul = create_empty_soul()
        binary = bytearray(sb.encode_soul(soul))
        # version バイトを破壊
        binary[5] ^= 0xFF
        with pytest.raises(ValueError, match="CRC mismatch"):
            sb.decode_soul(bytes(binary))


class TestMerkleIntegrity:
    def test_section_tampering_detected(self) -> None:
        """セクション本体の1バイトを反転すると Merkle が一致しない"""
        soul = _build_synthetic_soul(5)
        binary = bytearray(sb.encode_soul(soul))
        # CORE_IDENTITY セクション開始位置を取得
        sections = sb.list_sections(bytes(binary))
        core = next(s for s in sections if s[0] == sb.SEC_CORE_IDENTITY)
        offset = core[2]
        # 1バイト反転
        binary[offset] ^= 0xFF
        with pytest.raises(ValueError, match="Merkle"):
            sb.decode_soul(bytes(binary))

    def test_merkle_root_deterministic(self) -> None:
        """同じ SOUL は同じ merkle root を生む (タイムスタンプを合わせれば)"""
        soul1 = _build_synthetic_soul(3)
        soul1["created_at"] = "2026-01-01T00:00:00+00:00"
        soul2 = json.loads(json.dumps(soul1))  # deep copy
        b1 = sb.encode_soul(soul1)
        b2 = sb.encode_soul(soul2)
        # Merkle Root は section 内容のみ依存なので一致
        assert b1[72:104] == b2[72:104]


class TestPartialRead:
    def test_read_section_only(self) -> None:
        """Permission Gateway 想定: CORE_IDENTITY だけ取り出せる"""
        soul = _build_synthetic_soul(5)
        binary = sb.encode_soul(soul)
        core_bytes = sb.read_section(binary, sb.SEC_CORE_IDENTITY)
        assert core_bytes is not None
        assert len(core_bytes) == 80  # 10 dims × 8B

    def test_read_missing_section_returns_none(self) -> None:
        soul = create_empty_soul()
        binary = sb.encode_soul(soul)
        assert sb.read_section(binary, 0x99) is None

    def test_list_sections(self) -> None:
        soul = _build_synthetic_soul(3)
        binary = sb.encode_soul(soul)
        sections = sb.list_sections(binary)
        section_ids = {s[0] for s in sections}
        # 必須セクション
        assert sb.SEC_CORE_IDENTITY in section_ids
        assert sb.SEC_STATS in section_ids
        assert sb.SEC_EPISODIC_RECENT in section_ids
        assert sb.SEC_STRING_TABLE in section_ids


class TestSchemaResilience:
    def test_legacy_emotion_format_preserved(self) -> None:
        """旧 emotion: {name, intensity} 形式が壊れない"""
        soul = create_empty_soul()
        ep = create_episode(
            text="t",
            importance=0.5,
            emotion={"name": "calm", "intensity": 0.6},
            personality_signals=[],
            topics=[],
            values=[],
            summary="",
        )
        soul["episodic_memory"]["recent"].append(ep)
        decoded = sb.decode_soul(sb.encode_soul(soul))
        assert decoded["episodic_memory"]["recent"][0]["emotion"]["name"] == "calm"

    def test_modern_emotion_distribution_preserved(self) -> None:
        """新 emotion_distribution: {joy: ..., sadness: ...} 形式が壊れない"""
        soul = create_empty_soul()
        soul["episodic_memory"]["recent"].append(
            {
                "id": "ep_modern",
                "timestamp": "2026-04-16T08:40:45+00:00",
                "importance": 0.5,
                "emotion_distribution": {"joy": 0.3, "calm": 0.6},
                "personality_signals": [],
                "topics": [],
                "values": [],
                "weight": 1.0,
            }
        )
        decoded = sb.decode_soul(sb.encode_soul(soul))
        ed = decoded["episodic_memory"]["recent"][0]["emotion_distribution"]
        assert ed["joy"] == pytest.approx(0.3, abs=1e-3)


class TestEncryption:
    """AES-256-GCM セクション暗号化のラウンドトリップ・耐タンパ性"""

    MASTER = b"\x42" * 32  # テスト用マスタ鍵
    OTHER_MASTER = b"\x99" * 32

    def _encrypted_soul(self, **kw) -> bytes:
        soul = _build_synthetic_soul(5)
        return sb.encode_soul(soul, master_key=self.MASTER, epoch_counter=1, **kw)

    def test_encrypted_round_trip(self) -> None:
        soul = _build_synthetic_soul(5)
        binary = sb.encode_soul(soul, master_key=self.MASTER, epoch_counter=7)
        decoded = sb.decode_soul(binary, master_key=self.MASTER)
        # rawText 含むエピソードが取り戻せる
        assert decoded["episodic_memory"]["recent"][0]["raw_text"].startswith("テスト")
        assert decoded["_meta"]["is_encrypted_archive"] is True
        assert decoded["_meta"]["redacted_sections"] == []

    def test_redacted_when_no_key(self) -> None:
        binary = self._encrypted_soul()
        decoded = sb.decode_soul(binary)  # 鍵を渡さず復号
        # 暗号化されたセクションは Redacted リストに上がる
        redacted = set(decoded["_meta"]["redacted_sections"])
        # EPISODIC_RECENT は default で暗号化される
        assert sb.SEC_EPISODIC_RECENT in redacted
        # core_identity 等の非暗号化セクションは普通に読めている
        assert decoded["core_identity"]["openness"]["mu"] is not None

    def test_wrong_master_key_fails(self) -> None:
        binary = self._encrypted_soul()
        # 別マスタ鍵では復号失敗 (GCM tag mismatch → ValueError)
        with pytest.raises(ValueError, match="decryption failed"):
            sb.decode_soul(binary, master_key=self.OTHER_MASTER)

    def test_encrypted_body_tampering_detected(self) -> None:
        """暗号文 1 バイト反転 → GCM tag 不一致で復号失敗"""
        binary = bytearray(self._encrypted_soul())
        sections = sb.list_sections(bytes(binary))
        encrypted = next(s for s in sections if s[1] == sb.ENC_AES_GCM)
        offset = encrypted[2]
        binary[offset + 5] ^= 0x01
        # Merkle は ciphertext を含んでいるので merkle 検証より先に
        # (or 同時に) GCM tag mismatch で落ちる
        with pytest.raises(ValueError):
            sb.decode_soul(bytes(binary), master_key=self.MASTER)

    def test_section_key_derivation_consistent(self) -> None:
        """同じ master + section_id → 同じ鍵"""
        k1 = sb.derive_section_key(self.MASTER, sb.SEC_HEALTH_VITALS)
        k2 = sb.derive_section_key(self.MASTER, sb.SEC_HEALTH_VITALS)
        assert k1 == k2
        # 異なる section_id → 異なる鍵
        k3 = sb.derive_section_key(self.MASTER, sb.SEC_LOCATION_TRACE)
        assert k1 != k3
        # 異なる master → 異なる鍵
        k4 = sb.derive_section_key(self.OTHER_MASTER, sb.SEC_HEALTH_VITALS)
        assert k1 != k4

    def test_iv_unique_across_epochs(self) -> None:
        """epoch が異なれば IV も異なる (key+IV 衝突防止)"""
        iv1 = sb.derive_section_iv(1, sb.SEC_EPISODIC_RECENT)
        iv2 = sb.derive_section_iv(2, sb.SEC_EPISODIC_RECENT)
        assert iv1 != iv2
        # 同じ epoch でも section が違えば IV も違う
        iv3 = sb.derive_section_iv(1, sb.SEC_HEALTH_VITALS)
        assert iv1 != iv3

    def test_per_section_keys(self) -> None:
        """master_key を渡さず、必要セクションの鍵だけ渡して復号"""
        binary = self._encrypted_soul()
        # EPISODIC_RECENT の鍵だけ渡す
        keys = {
            sb.SEC_EPISODIC_RECENT: sb.derive_section_key(
                self.MASTER, sb.SEC_EPISODIC_RECENT
            ),
        }
        decoded = sb.decode_soul(binary, section_keys=keys)
        # episodic_recent は読める
        assert len(decoded["episodic_memory"]["recent"]) == 5
        # 他暗号化セクションはあれば redacted のはず (今回は空なので何も来ない)


class TestCapabilityTokenBridge:
    """capability_token.CapabilityToken → 鍵セット → 開示の連結"""

    MASTER = b"\xCC" * 32

    def test_keys_for_categories_filters_hidden(self) -> None:
        """granted_categories に含まれないカテゴリは鍵が出ない"""
        keys = sb.derive_keys_for_categories(
            self.MASTER,
            granted_categories={"core_identity", "interests_values"},
        )
        # core_identity → SEC_CORE_IDENTITY (0x01)
        assert sb.SEC_CORE_IDENTITY in keys
        # interests_values → SEC_SEMANTIC_MAP (0x04)
        assert sb.SEC_SEMANTIC_MAP in keys
        # health_vitals は granted されていないので鍵なし
        assert sb.SEC_HEALTH_VITALS not in keys
        assert sb.SEC_LOCATION_TRACE not in keys

    def test_health_coach_scope_only_unlocks_health_sections(self) -> None:
        """capability_token モジュールの実 scope を使った統合テスト"""
        from capability_token import SCOPE_TEMPLATES

        scope = SCOPE_TEMPLATES["health_coach"]

        class MockToken:
            pass

        token = MockToken()
        token.scope = scope

        keys = sb.derive_keys_for_token(self.MASTER, token)
        # health_coach: core_identity SUMMARY / emotional_state FULL /
        # behavioral_patterns FULL / health_vitals FULL /
        # interests_values SUMMARY (HIDDEN ではない)
        assert sb.SEC_HEALTH_VITALS in keys
        assert sb.SEC_EMOTIONAL_STATE in keys
        assert sb.SEC_TEMPORAL_PATTERNS in keys  # behavioral_patterns
        assert sb.SEC_WATCHPOINTS in keys        # behavioral_patterns
        assert sb.SEC_CORE_IDENTITY in keys
        # health_coach は episodic_memory / location / social は HIDDEN
        assert sb.SEC_EPISODIC_RECENT not in keys
        assert sb.SEC_LOCATION_TRACE not in keys
        assert sb.SEC_SOCIAL_GRAPH not in keys

    def test_minimal_scope_minimal_keys(self) -> None:
        from capability_token import SCOPE_TEMPLATES

        class MockToken:
            pass

        token = MockToken()
        token.scope = SCOPE_TEMPLATES["minimal"]

        keys = sb.derive_keys_for_token(self.MASTER, token)
        # minimal: core_identity SUMMARY のみ非HIDDEN
        assert keys.keys() == {sb.SEC_CORE_IDENTITY}


class TestPartialView:
    """granted_sections + 暗号化で部分開示用ビューを作る"""

    MASTER = b"\x77" * 32

    def test_partial_view_only_writes_granted(self) -> None:
        soul = _build_synthetic_soul(5)
        # core_identity と semantic_map のみ開示する partial view
        binary = sb.encode_soul(
            soul,
            master_key=self.MASTER,
            granted_sections={sb.SEC_CORE_IDENTITY, sb.SEC_SEMANTIC_MAP},
        )
        section_ids = {s[0] for s in sb.list_sections(binary)}
        # 開示対象 + ALWAYS_DISCLOSED (STATS, STRING_TABLE) のみが書かれる
        assert sb.SEC_CORE_IDENTITY in section_ids
        assert sb.SEC_SEMANTIC_MAP in section_ids
        assert sb.SEC_STATS in section_ids
        # EPISODIC は書かれていない
        assert sb.SEC_EPISODIC_RECENT not in section_ids
        # HEADER の partial flag が立つ
        decoded = sb.decode_soul(binary, master_key=self.MASTER)
        assert decoded["_meta"]["is_partial"] is True


# ════════════════════════════════════════════════════════════════════════════
# ベンチマーク (pytest 実行では skip、 直接実行で動く)
# ════════════════════════════════════════════════════════════════════════════


def _bench(name: str, fn, iters: int) -> float:
    # warmup
    for _ in range(3):
        fn()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    elapsed = time.perf_counter() - t0
    avg_us = (elapsed / iters) * 1e6
    print(f"  {name:<32s} {avg_us:>10.1f} µs/op  ({iters} iters)")
    return avg_us


def run_bench() -> None:
    print("\n══════════ SOUL Binary Benchmark ══════════")

    MASTER = b"\xAA" * 32

    for n in [4, 30, 100]:
        soul = _build_synthetic_soul(n)

        # JSON 比較用
        json_bytes = json.dumps(soul, ensure_ascii=False).encode("utf-8")
        bin_bytes = sb.encode_soul(soul)
        enc_bytes = sb.encode_soul(soul, master_key=MASTER, epoch_counter=1)

        print(f"\n── {n} episodes ──")
        print(f"  JSON size:        {len(json_bytes):>8d} B")
        print(f"  Binary size:      {len(bin_bytes):>8d} B  ({len(bin_bytes)/len(json_bytes):.1%} of JSON)")
        print(f"  Encrypted size:   {len(enc_bytes):>8d} B  (+{len(enc_bytes)-len(bin_bytes):>4d} B vs plain)")

        iters = max(50, 2000 // max(n, 1))
        _bench(
            "encode_soul (plain)",
            lambda s=soul: sb.encode_soul(s),
            iters,
        )
        _bench(
            "encode_soul (encrypted)",
            lambda s=soul, e=[1]: sb.encode_soul(s, master_key=MASTER, epoch_counter=e.__setitem__(0, e[0]+1) or e[0]),
            iters,
        )
        _bench(
            "json.dumps + encode utf-8",
            lambda s=soul: json.dumps(s, ensure_ascii=False).encode("utf-8"),
            iters,
        )
        _bench(
            "decode_soul (plain)",
            lambda b=bin_bytes: sb.decode_soul(b),
            iters,
        )
        _bench(
            "decode_soul (encrypted, full key)",
            lambda b=enc_bytes: sb.decode_soul(b, master_key=MASTER),
            iters,
        )
        _bench(
            "decode_soul (encrypted, redacted)",
            lambda b=enc_bytes: sb.decode_soul(b),
            iters,
        )
        _bench(
            "json.loads (UTF-8)",
            lambda b=json_bytes: json.loads(b),
            iters,
        )
        _bench(
            "read_section CORE_IDENTITY only",
            lambda b=bin_bytes: sb.read_section(b, sb.SEC_CORE_IDENTITY),
            iters * 10,
        )

    # Real soul.json でのサイズ計測
    if SOUL_JSON.exists():
        soul = _load_real_soul()
        json_size = SOUL_JSON.stat().st_size
        bin_bytes = sb.encode_soul(soul)
        print(f"\n── Real data/soul.json ({len(soul['episodic_memory']['recent'])} episodes) ──")
        print(f"  JSON size:      {json_size:>8d} B")
        print(f"  Binary size:    {len(bin_bytes):>8d} B  ({len(bin_bytes)/json_size:.1%} of JSON)")

    print("\n═══════════════════════════════════════════")


if __name__ == "__main__":
    # ユニットテストを呼ぶ前に、まずベンチを表示
    run_bench()
    print("\n--- Running pytest ---")
    raise SystemExit(pytest.main([__file__, "-v"]))
