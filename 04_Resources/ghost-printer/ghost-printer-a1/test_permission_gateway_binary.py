"""
Ghost-Printer C4 × A6 — Permission Gateway バイナリパス E2E テスト

filter_soul_bytes / create_partial_view は CapabilityToken と master_key を
組合せて SOUL バイナリにアクセス制御をかける。HIDDEN カテゴリは鍵が
導出されないので、アプリ層フィルタを信頼しなくても暗号学的に隔離される。
"""

from __future__ import annotations

import pytest

import soul_binary as sb
from capability_token import (
    SCOPE_TEMPLATES,
    DisclosureCategory,
    GranularityLevel,
    TokenManager,
)
from permission_gateway import PermissionGateway
from soul_schema import create_empty_soul, create_episode


# ════════════════════════════════════════════════════════════════════════════
# フィクスチャ
# ════════════════════════════════════════════════════════════════════════════


MASTER = b"\x42" * 32
SECRET = "ghost-printer-binary-test-secret-2026"


@pytest.fixture
def soul_dict() -> dict:
    soul = create_empty_soul("test_user")
    # 性格に偏りを付ける
    soul["core_identity"]["openness"] = {"mu": 0.8, "sigma": 0.1}
    soul["core_identity"]["curiosity"] = {"mu": 0.75, "sigma": 0.12}
    # エピソード 3 件
    for i, txt in enumerate(["コーヒーで読書", "AIプロジェクトに集中", "深夜に試作品を作る"]):
        soul["episodic_memory"]["recent"].append(
            create_episode(
                text=txt,
                importance=0.6 + i * 0.1,
                emotion={"name": "joy", "intensity": 0.7},
                personality_signals=[],
                topics=["ai", "reading", f"topic_{i}"],
                values=["growth"],
                summary=f"summary {i}",
                context={"date": "2026-04-15"},
            )
        )
    soul["semantic_map"]["interests"] = {"ai": 5, "reading": 3, "coffee": 2}
    soul["semantic_map"]["values"] = {"growth": 4, "creativity": 2}
    soul["stats"]["total_episodes"] = 3
    soul["stats"]["total_updates"] = 3
    # 機微カテゴリも乗せておく (健康バイタル + 位置)
    soul["health_vitals"] = {
        "heart_rate_avg": 68,
        "sleep_quality": 0.8,
        "stress_level": 0.4,
    }
    soul["location_movement"] = {
        "visited_locations": [
            {"location_id": "tokyo_station", "name": "東京駅", "category": "transit", "visit_count": 12},
            {"location_id": "home", "name": "自宅", "category": "residence", "visit_count": 30},
        ]
    }
    soul["social_graph"] = {
        "relationships": [
            {"person_id": "alice_001", "name": "Alice", "relationship_type": "friend", "strength": 0.9},
            {"person_id": "bob_002", "name": "Bob", "relationship_type": "colleague", "strength": 0.5},
        ]
    }
    return soul


@pytest.fixture
def soul_bytes(soul_dict) -> bytes:
    """暗号化された SOUL バイナリ (機微セクションは AES-GCM 封印)"""
    return sb.encode_soul(soul_dict, master_key=MASTER, epoch_counter=42)


@pytest.fixture
def token_manager() -> TokenManager:
    return TokenManager(secret_key=SECRET)


@pytest.fixture
def gateway(token_manager) -> PermissionGateway:
    return PermissionGateway(token_manager)


# ════════════════════════════════════════════════════════════════════════════
# filter_soul_bytes
# ════════════════════════════════════════════════════════════════════════════


class TestFilterSoulBytes:
    def test_health_coach_unlocks_only_health_categories(
        self, gateway, token_manager, soul_bytes
    ):
        token = token_manager.issue(
            issuer="device_gp",
            subject="ai_health_coach",
            scope=SCOPE_TEMPLATES["health_coach"],
        )
        result = gateway.filter_soul_bytes(soul_bytes, token, MASTER)

        assert result.success
        # health_coach: core_identity SUMMARY / emotional_state FULL /
        # behavioral_patterns FULL / health_vitals FULL / interests_values SUMMARY
        cats = set(result.categories.keys())
        assert "health_vitals" in cats
        assert "core_identity" in cats
        assert "interests_values" in cats
        # episodic_memory / location / social は HIDDEN なのでない
        assert "episodic_memory" not in cats
        assert "location_movement" not in cats
        assert "social_graph" not in cats

    def test_minimal_scope_returns_only_core_summary(
        self, gateway, token_manager, soul_bytes
    ):
        token = token_manager.issue(
            issuer="device_gp",
            subject="ai_minimal",
            scope=SCOPE_TEMPLATES["minimal"],
        )
        result = gateway.filter_soul_bytes(soul_bytes, token, MASTER)

        assert result.success
        assert set(result.categories.keys()) == {"core_identity"}
        # SUMMARY なので σ が拡大されている
        ci = result.categories["core_identity"]["data"]
        assert ci["openness"]["sigma"] >= 0.1  # 元 0.1 → 約 0.15

    def test_invalid_token_fails(self, gateway, token_manager, soul_bytes):
        token = token_manager.issue(
            issuer="device_gp",
            subject="ai_bad",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        # トークンを失効
        token_manager.revoke(token.token_id)
        result = gateway.filter_soul_bytes(soul_bytes, token, MASTER)
        assert not result.success
        assert "verification failed" in result.error.lower()

    def test_wrong_master_key_decodes_only_unencrypted_sections(
        self, gateway, token_manager, soul_bytes
    ):
        """master_key が違うと暗号化セクションが復号失敗 → エラー"""
        wrong_master = b"\x99" * 32
        token = token_manager.issue(
            issuer="device_gp",
            subject="ai_test",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        result = gateway.filter_soul_bytes(soul_bytes, token, wrong_master)
        # episodic_memory が暗号化されており、Claude Personal 是 episodic 開示するので
        # 鍵が違うと decode 段階で失敗する
        assert not result.success
        assert "decode failed" in result.error.lower()

    def test_cryptographic_enforcement_no_hidden_data(
        self, gateway, token_manager, soul_bytes
    ):
        """
        暗号学的強制: minimal スコープでは episodic / health / location 鍵が出ない。
        gateway の filter_soul (dict 版) と異なり、もし dict-only ロジックに
        バグがあって HIDDEN を漏らしてしまっても、バイナリパスでは復号できない。
        """
        token = token_manager.issue(
            issuer="device_gp",
            subject="ai_minimal",
            scope=SCOPE_TEMPLATES["minimal"],
        )
        # 直接デコードしてみる: minimal スコープ由来の鍵だけ
        keys = sb.derive_keys_for_token(MASTER, token)
        soul = sb.decode_soul(soul_bytes, section_keys=keys)
        # 暗号化セクション (EPISODIC/HEALTH/LOCATION/SOCIAL/EMOTIONAL) はすべて redacted
        redacted = set(soul["_meta"]["redacted_sections"])
        assert sb.SEC_EPISODIC_RECENT in redacted
        assert sb.SEC_HEALTH_VITALS in redacted
        assert sb.SEC_LOCATION_TRACE in redacted
        assert sb.SEC_SOCIAL_GRAPH in redacted
        # core_identity は暗号化されていないので見える (SUMMARY 変換は dict 層)
        assert "core_identity" in soul

    def test_stats_match_scope_visibility(
        self, gateway, token_manager, soul_bytes
    ):
        """stats が scope の可視/不可視カテゴリと整合する"""
        token = token_manager.issue(
            issuer="device_gp",
            subject="ai_minimal",
            scope=SCOPE_TEMPLATES["minimal"],
        )
        result = gateway.filter_soul_bytes(soul_bytes, token, MASTER)
        assert result.success
        # minimal: core_identity のみ非HIDDEN、他 7 カテゴリは HIDDEN
        assert result.stats["disclosed"] == 1
        assert result.stats["hidden"] == 7
        # cryptographically_redacted は「scope 許可だが鍵なし」のレアケース計測。
        # minimal では HIDDEN 判定が先なので 0 のまま (= 整合)。
        assert result.stats["cryptographically_redacted"] == 0

    def test_crypto_redaction_triggers_when_key_missing(
        self, gateway, token_manager, soul_bytes
    ):
        """scope が許可していても section_keys に鍵が無ければ crypto-redact される

        これは Permission Gateway を間接的に呼ぶレイヤ (例えば部分鍵だけ
        渡された下流コンポーネント) で起きる想定のシナリオ。
        ここでは sb.decode_soul を直接叩いて挙動を検証する。
        """
        # claude_personal では episodic_memory FULL だが、 keys に EPISODIC を
        # 入れずに decode するシナリオ
        token = token_manager.issue(
            issuer="device_gp",
            subject="ai_partial_keys",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        keys = sb.derive_keys_for_token(MASTER, token)
        # 故意に EPISODIC_RECENT 鍵を抜く
        keys.pop(sb.SEC_EPISODIC_RECENT, None)
        soul = sb.decode_soul(soul_bytes, section_keys=keys)
        assert sb.SEC_EPISODIC_RECENT in soul["_meta"]["redacted_sections"]


# ════════════════════════════════════════════════════════════════════════════
# create_partial_view
# ════════════════════════════════════════════════════════════════════════════


class TestCreatePartialView:
    def test_partial_view_has_partial_flag(
        self, gateway, token_manager, soul_bytes
    ):
        token = token_manager.issue(
            issuer="device_gp",
            subject="claude",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        view_bytes, result = gateway.create_partial_view(soul_bytes, token, MASTER)
        assert result.success
        assert len(view_bytes) > 0
        decoded = sb.decode_soul(view_bytes)
        assert decoded["_meta"]["is_partial"] is True
        # partial view は平文なので暗号化フラグは立たない
        assert decoded["_meta"]["is_encrypted_archive"] is False

    def test_partial_view_omits_hidden_sections(
        self, gateway, token_manager, soul_bytes
    ):
        token = token_manager.issue(
            issuer="device_gp",
            subject="claude",
            scope=SCOPE_TEMPLATES["work_assistant"],
        )
        view_bytes, _ = gateway.create_partial_view(soul_bytes, token, MASTER)
        # work_assistant: emotional_state / health / location / social は HIDDEN
        section_ids = {s[0] for s in sb.list_sections(view_bytes)}
        assert sb.SEC_HEALTH_VITALS not in section_ids
        assert sb.SEC_LOCATION_TRACE not in section_ids
        assert sb.SEC_SOCIAL_GRAPH not in section_ids
        # core_identity / interests_values 等は含まれる
        assert sb.SEC_CORE_IDENTITY in section_ids
        assert sb.SEC_SEMANTIC_MAP in section_ids

    def test_partial_view_decodable_without_master_key(
        self, gateway, token_manager, soul_bytes
    ):
        """外部AIが master_key を持たなくても partial view は読める (= 平文)"""
        token = token_manager.issue(
            issuer="device_gp",
            subject="external_ai",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        view_bytes, _ = gateway.create_partial_view(soul_bytes, token, MASTER)

        # 鍵を渡さず decode できる
        decoded = sb.decode_soul(view_bytes)
        assert decoded["_meta"]["redacted_sections"] == []
        # claude_personal は episodic_memory FULL なので 3 件入っている
        assert len(decoded["episodic_memory"]["recent"]) == 3
        # raw_text も復元 (平文化された)
        assert "AIプロジェクト" in decoded["episodic_memory"]["recent"][1]["raw_text"]

    def test_partial_view_for_minimal_is_smallest(
        self, gateway, token_manager, soul_bytes
    ):
        """minimal partial view は claude_personal より大幅に小さい"""
        cp_token = token_manager.issue(
            issuer="device_gp",
            subject="claude_full",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        min_token = token_manager.issue(
            issuer="device_gp",
            subject="claude_min",
            scope=SCOPE_TEMPLATES["minimal"],
        )
        cp_view, _ = gateway.create_partial_view(soul_bytes, cp_token, MASTER)
        min_view, _ = gateway.create_partial_view(soul_bytes, min_token, MASTER)
        assert len(min_view) < len(cp_view)
        assert len(min_view) < 500  # core_identity 80B + STATS + STRING_TABLE 等

    def test_partial_view_for_health_coach_no_episodic(
        self, gateway, token_manager, soul_bytes
    ):
        """health_coach は episodic_memory が HIDDEN なので raw_text が出てこない"""
        token = token_manager.issue(
            issuer="device_gp",
            subject="health_app",
            scope=SCOPE_TEMPLATES["health_coach"],
        )
        view_bytes, _ = gateway.create_partial_view(soul_bytes, token, MASTER)
        decoded = sb.decode_soul(view_bytes)
        # episodic_memory.recent は空 (セクションがない)
        assert decoded["episodic_memory"]["recent"] == []
        # health_vitals は入っている
        assert "health_vitals" in decoded
        assert decoded["health_vitals"]["heart_rate_avg"] == 68

    def test_partial_view_revoked_token_fails(
        self, gateway, token_manager, soul_bytes
    ):
        token = token_manager.issue(
            issuer="device_gp",
            subject="external_ai",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        token_manager.revoke(token.token_id)
        view_bytes, result = gateway.create_partial_view(soul_bytes, token, MASTER)
        assert not result.success
        assert view_bytes == b""

    def test_partial_view_one_time_consumed(
        self, gateway, token_manager, soul_bytes
    ):
        """ワンタイム token は 1 度の partial view 生成で消費される"""
        token = token_manager.issue(
            issuer="device_gp",
            subject="one_shot_ai",
            scope=SCOPE_TEMPLATES["claude_personal"],
            one_time=True,
        )
        # 1 回目: 成功
        view1, r1 = gateway.create_partial_view(soul_bytes, token, MASTER)
        assert r1.success
        assert len(view1) > 0
        # 2 回目: ワンタイム消費済みで失敗
        view2, r2 = gateway.create_partial_view(soul_bytes, token, MASTER)
        assert not r2.success
        assert "already_consumed" in r2.error.lower() or "consumed" in r2.error.lower()


# ════════════════════════════════════════════════════════════════════════════
# E2E シナリオ
# ════════════════════════════════════════════════════════════════════════════


class TestEndToEnd:
    def test_owner_to_claude_round_trip(self, gateway, token_manager, soul_bytes):
        """
        E2E: オーナー → claude_personal token → partial view → 外部 Claude の視界
        """
        token = token_manager.issue(
            issuer="haru_device",
            subject="claude.ai",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        # Step 1: オーナー側で partial view 生成
        view_bytes, send_result = gateway.create_partial_view(
            soul_bytes, token, MASTER, consume_if_onetime=False
        )
        assert send_result.success

        # Step 2: 外部 Claude (master_key を持たない) が受信して decode
        claude_view = sb.decode_soul(view_bytes)
        # 自分が見ていいセクションだけ復元できる
        assert claude_view["core_identity"]["openness"]["mu"] > 0
        assert len(claude_view["episodic_memory"]["recent"]) > 0
        # HIDDEN だったセクションは存在しない
        assert "health_vitals" not in claude_view
        assert "location_movement" not in claude_view
        # ヘルスデータが Claude に漏れていないことを直接確認
        assert "heart_rate" not in str(claude_view)

    def test_two_recipients_get_different_views(
        self, gateway, token_manager, soul_bytes
    ):
        """同じ SOUL から異なる Token で異なる view を作る (互いに見えないデータ)"""
        claude_token = token_manager.issue(
            issuer="haru_device",
            subject="claude",
            scope=SCOPE_TEMPLATES["claude_personal"],
        )
        health_token = token_manager.issue(
            issuer="haru_device",
            subject="health_app",
            scope=SCOPE_TEMPLATES["health_coach"],
        )

        claude_view, _ = gateway.create_partial_view(soul_bytes, claude_token, MASTER)
        health_view, _ = gateway.create_partial_view(soul_bytes, health_token, MASTER)

        c = sb.decode_soul(claude_view)
        h = sb.decode_soul(health_view)

        # claude は episodic を持ち、 health は持たない
        assert len(c["episodic_memory"]["recent"]) > 0
        assert h["episodic_memory"]["recent"] == []
        # health は heart_rate を持ち、 claude は持たない
        assert "health_vitals" in h
        assert "health_vitals" not in c
