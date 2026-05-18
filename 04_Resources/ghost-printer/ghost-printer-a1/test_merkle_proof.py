"""
Ghost-Printer A6 §7.3 — Merkle 部分証明テスト

外部AI が「自分が受け取った partial view は確かに原本 SOUL の一部である」
ことを暗号学的に検証できるかどうかを確かめる。
"""

from __future__ import annotations

import hashlib

import pytest

import soul_binary as sb
from capability_token import SCOPE_TEMPLATES, TokenManager
from permission_gateway import PermissionGateway
from soul_schema import create_empty_soul, create_episode


# ════════════════════════════════════════════════════════════════════════════
# MerkleTree 単体
# ════════════════════════════════════════════════════════════════════════════


class TestMerkleTree:
    def test_empty(self):
        t = sb.MerkleTree([])
        assert t.root == bytes(32)

    def test_single_leaf(self):
        leaf = hashlib.sha256(b"only").digest()
        t = sb.MerkleTree([leaf])
        assert t.root == leaf
        # proof は空 (上りパスなし)
        assert t.proof_for(0) == []
        assert sb.MerkleTree.verify(leaf, 0, [], t.root)

    def test_two_leaves(self):
        a = hashlib.sha256(b"a").digest()
        b = hashlib.sha256(b"b").digest()
        t = sb.MerkleTree([a, b])
        assert t.root == hashlib.sha256(a + b).digest()
        # proof for index 0 = [b], for index 1 = [a]
        assert t.proof_for(0) == [b]
        assert t.proof_for(1) == [a]
        assert sb.MerkleTree.verify(a, 0, t.proof_for(0), t.root)
        assert sb.MerkleTree.verify(b, 1, t.proof_for(1), t.root)

    def test_three_leaves_padding(self):
        leaves = [hashlib.sha256(s).digest() for s in (b"a", b"b", b"c")]
        t = sb.MerkleTree(leaves)
        for i, leaf in enumerate(leaves):
            proof = t.proof_for(i)
            assert sb.MerkleTree.verify(leaf, i, proof, t.root)

    def test_ten_leaves(self):
        leaves = [hashlib.sha256(f"item_{i}".encode()).digest() for i in range(10)]
        t = sb.MerkleTree(leaves)
        for i, leaf in enumerate(leaves):
            assert sb.MerkleTree.verify(leaf, i, t.proof_for(i), t.root), (
                f"leaf {i} failed"
            )

    def test_wrong_leaf_fails(self):
        leaves = [hashlib.sha256(s).digest() for s in (b"a", b"b", b"c", b"d")]
        t = sb.MerkleTree(leaves)
        bad_leaf = hashlib.sha256(b"impostor").digest()
        assert not sb.MerkleTree.verify(bad_leaf, 0, t.proof_for(0), t.root)

    def test_wrong_index_fails(self):
        leaves = [hashlib.sha256(s).digest() for s in (b"a", b"b", b"c", b"d")]
        t = sb.MerkleTree(leaves)
        proof_for_0 = t.proof_for(0)
        # 同じ proof を index 1 として検証 → 失敗
        assert not sb.MerkleTree.verify(leaves[0], 1, proof_for_0, t.root)

    def test_wrong_root_fails(self):
        leaves = [hashlib.sha256(s).digest() for s in (b"a", b"b", b"c", b"d")]
        t = sb.MerkleTree(leaves)
        bad_root = hashlib.sha256(b"fake_root").digest()
        assert not sb.MerkleTree.verify(leaves[0], 0, t.proof_for(0), bad_root)


# ════════════════════════════════════════════════════════════════════════════
# extract_partial_view_bytes
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def encrypted_soul_bytes():
    """暗号化済みの完全な SOUL バイナリ"""
    master = b"\xCC" * 32
    soul = create_empty_soul("alice")
    soul["core_identity"]["openness"] = {"mu": 0.8, "sigma": 0.1}
    for i, txt in enumerate(["コーヒー", "AI開発", "深夜作業"]):
        soul["episodic_memory"]["recent"].append(
            create_episode(
                text=txt,
                importance=0.6,
                emotion={"name": "joy", "intensity": 0.7},
                personality_signals=[],
                topics=[txt],
                values=["growth"],
                summary=f"summary {i}",
            )
        )
    soul["semantic_map"]["interests"] = {"ai": 5, "coffee": 2}
    soul["health_vitals"] = {"hr": 68, "sleep": 0.8}
    soul["social_graph"] = {"relationships": [{"name": "Alice", "strength": 0.9}]}
    return sb.encode_soul(soul, master_key=master, epoch_counter=42), master


class TestExtractPartialView:
    def test_section_bytes_preserved_verbatim(self, encrypted_soul_bytes):
        """元のセクション本体がバイト単位で保持されること"""
        original, _master = encrypted_soul_bytes
        granted = {sb.SEC_CORE_IDENTITY, sb.SEC_SEMANTIC_MAP}
        view = sb.extract_partial_view_bytes(original, granted)

        # 元 CORE_IDENTITY のバイト
        original_core = sb.read_section(original, sb.SEC_CORE_IDENTITY)
        view_core = sb.read_section(view, sb.SEC_CORE_IDENTITY)
        assert original_core == view_core

    def test_view_has_partial_flag(self, encrypted_soul_bytes):
        original, _ = encrypted_soul_bytes
        view = sb.extract_partial_view_bytes(original, {sb.SEC_CORE_IDENTITY})
        decoded = sb.decode_soul(view)
        assert decoded["_meta"]["is_partial"] is True

    def test_view_preserves_epoch_for_decryption(self, encrypted_soul_bytes):
        """epoch_counter が原本と同じなので、 section_keys で復号できる"""
        original, master = encrypted_soul_bytes
        view = sb.extract_partial_view_bytes(
            original, {sb.SEC_EPISODIC_RECENT, sb.SEC_HEALTH_VITALS}
        )
        # master_key ありで decode → 内容が読める
        decoded = sb.decode_soul(view, master_key=master)
        assert len(decoded["episodic_memory"]["recent"]) == 3
        assert "health_vitals" in decoded

    def test_excluded_sections_not_in_view(self, encrypted_soul_bytes):
        original, _ = encrypted_soul_bytes
        view = sb.extract_partial_view_bytes(original, {sb.SEC_CORE_IDENTITY})
        section_ids = {s[0] for s in sb.list_sections(view)}
        assert sb.SEC_HEALTH_VITALS not in section_ids
        assert sb.SEC_SOCIAL_GRAPH not in section_ids
        # ALWAYS_DISCLOSED は残る
        assert sb.SEC_STATS in section_ids
        # PROOF セクションが追加されている
        assert sb.SEC_MERKLE_PROOF in section_ids


# ════════════════════════════════════════════════════════════════════════════
# verify_partial_view
# ════════════════════════════════════════════════════════════════════════════


class TestVerifyPartialView:
    def test_valid_partial_view_verifies(self, encrypted_soul_bytes):
        original, _ = encrypted_soul_bytes
        view = sb.extract_partial_view_bytes(
            original, {sb.SEC_CORE_IDENTITY, sb.SEC_SEMANTIC_MAP}
        )
        result = sb.verify_partial_view(view)
        assert result["valid"]
        assert sb.SEC_CORE_IDENTITY in result["verified_sections"]
        assert sb.SEC_SEMANTIC_MAP in result["verified_sections"]
        assert result["errors"] == []

    def test_verify_with_expected_root_match(self, encrypted_soul_bytes):
        original, _ = encrypted_soul_bytes
        # 原本 SOUL の真の root を計算
        original_sections = sb.list_sections(original)
        leaves = [
            hashlib.sha256(original[off : off + length]).digest()
            for _sid, _enc, off, length in original_sections
        ]
        true_root = sb.MerkleTree(leaves).root

        view = sb.extract_partial_view_bytes(original, {sb.SEC_CORE_IDENTITY})
        result = sb.verify_partial_view(view, expected_original_root=true_root)
        assert result["valid"]
        assert result["original_root"] == true_root

    def test_verify_with_wrong_expected_root_fails(self, encrypted_soul_bytes):
        original, _ = encrypted_soul_bytes
        view = sb.extract_partial_view_bytes(original, {sb.SEC_CORE_IDENTITY})
        bad_root = hashlib.sha256(b"impostor").digest()
        result = sb.verify_partial_view(view, expected_original_root=bad_root)
        assert not result["valid"]
        assert any("Root mismatch" in e for e in result["errors"])

    def test_tampered_section_fails_verification(self, encrypted_soul_bytes):
        original, _ = encrypted_soul_bytes
        view = bytearray(
            sb.extract_partial_view_bytes(original, {sb.SEC_CORE_IDENTITY})
        )
        # CORE_IDENTITY セクションのバイトを 1bit 反転
        for sid, _enc, off, length in sb.list_sections(bytes(view)):
            if sid == sb.SEC_CORE_IDENTITY:
                view[off] ^= 0x01
                break
        result = sb.verify_partial_view(bytes(view), )
        assert not result["valid"]

    def test_no_proof_section_returns_invalid(self, encrypted_soul_bytes):
        original, _ = encrypted_soul_bytes
        # include_proofs=False で proof なし view を作る
        view = sb.extract_partial_view_bytes(
            original, {sb.SEC_CORE_IDENTITY}, include_proofs=False
        )
        result = sb.verify_partial_view(view)
        assert not result["valid"]
        assert any("No SEC_MERKLE_PROOF" in e for e in result["errors"])


# ════════════════════════════════════════════════════════════════════════════
# E2E: PermissionGateway 経由
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def gateway_setup(encrypted_soul_bytes):
    soul_bytes, master = encrypted_soul_bytes
    tm = TokenManager(secret_key="merkle-e2e-secret")
    gw = PermissionGateway(tm)
    return soul_bytes, master, tm, gw


class TestVerifiableViewE2E:
    def test_create_and_verify_round_trip(self, gateway_setup):
        soul_bytes, master, tm, gw = gateway_setup
        token = tm.issue(
            issuer="device",
            subject="claude",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        view, keys, fr = gw.create_verifiable_partial_view(
            soul_bytes, token, master
        )
        assert fr.success
        assert fr.stats["verifiable"] is True
        assert len(view) > 0
        assert len(keys) > 0

        # 受信者の立場で検証 + 復号
        verify = sb.verify_partial_view(view)
        assert verify["valid"]

        # section_keys で復号 (master_key を持たない受信者)
        decoded = sb.decode_soul(view, section_keys=keys)
        # claude_personal は episodic_memory FULL なのでエピソードが見える
        assert len(decoded["episodic_memory"]["recent"]) == 3

    def test_verify_against_known_original_root(self, gateway_setup):
        soul_bytes, master, tm, gw = gateway_setup
        token = tm.issue(
            issuer="device",
            subject="claude",
            scope=SCOPE_TEMPLATES["health_coach"],
        )
        # 受信者は事前に「信頼できる」原本 root を知っている (例えば前のセッションで取得)
        original_sections = sb.list_sections(soul_bytes)
        leaves = [
            hashlib.sha256(soul_bytes[off : off + length]).digest()
            for _sid, _enc, off, length in original_sections
        ]
        trusted_root = sb.MerkleTree(leaves).root

        view, keys, _ = gw.create_verifiable_partial_view(
            soul_bytes, token, master
        )
        result = sb.verify_partial_view(view, expected_original_root=trusted_root)
        assert result["valid"], result["errors"]

    def test_attacker_cannot_forge_view(self, gateway_setup):
        """攻撃者が異なる SOUL から作った view を提示しても、 受信者が信頼する
        原本 root とは結びつかないので拒否される。
        """
        soul_bytes, master, tm, gw = gateway_setup

        # 攻撃者が別の SOUL を作る (異なる内容)
        evil_soul = create_empty_soul("evil")
        evil_soul["core_identity"]["openness"] = {"mu": 0.0, "sigma": 0.05}
        evil_bytes = sb.encode_soul(evil_soul, master_key=master, epoch_counter=42)

        # 攻撃者がそこから view を作る (SC_MERKLE_PROOF も自前で計算可能)
        evil_view = sb.extract_partial_view_bytes(
            evil_bytes, {sb.SEC_CORE_IDENTITY}
        )

        # 受信者は本物の SOUL の root を信頼している
        original_sections = sb.list_sections(soul_bytes)
        leaves = [
            hashlib.sha256(soul_bytes[off : off + length]).digest()
            for _sid, _enc, off, length in original_sections
        ]
        trusted_root = sb.MerkleTree(leaves).root

        # 攻撃者の view は trusted_root では検証できない
        result = sb.verify_partial_view(
            evil_view, expected_original_root=trusted_root
        )
        assert not result["valid"]
        assert any("Root mismatch" in e for e in result["errors"])

    def test_view_size_overhead(self, gateway_setup):
        """proof bundle のサイズオーバーヘッドが許容範囲内"""
        soul_bytes, master, tm, gw = gateway_setup
        token = tm.issue(
            issuer="device",
            subject="claude",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        view, _, _ = gw.create_verifiable_partial_view(soul_bytes, token, master)
        # SEC_MERKLE_PROOF セクションのサイズ
        proof_body = sb.read_section(view, sb.SEC_MERKLE_PROOF)
        assert proof_body is not None
        # 数百 B 程度: 1 proof = 32B(siblings) × log2(N) + meta 程度
        # N=8 程度の section なら proof bundle は数百 B〜1KB 以内に収まるはず
        assert len(proof_body) < 4096, f"proof size too big: {len(proof_body)} B"
