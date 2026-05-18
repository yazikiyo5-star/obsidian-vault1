#!/usr/bin/env python3
"""
Ghost-Printer C2/C4 テストスイート
Capability Token & Permission Gateway の統合テスト

テスト項目:
  C2: Token生成・署名検証・失効・ワンタイム・永続化
  C4: SOULフィルタリング・粒度変換・境界ケース・Prompt生成
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capability_token import (
    CapabilityToken,
    CategoryRule,
    DisclosureCategory,
    DisclosureScope,
    GranularityLevel,
    SCOPE_TEMPLATES,
    TokenManager,
)
from permission_gateway import (
    BoundaryCaseResolver,
    FilterResult,
    PermissionGateway,
)
from soul_schema import create_empty_soul, create_episode


# ════════════════════════════════════════════════════════════════════════════════
# テスト用データ
# ════════════════════════════════════════════════════════════════════════════════

def make_test_soul() -> dict:
    """テスト用SOULデータ"""
    soul = create_empty_soul("test_user")

    # Core Identity を更新
    soul["core_identity"]["openness"]["mu"] = 0.78
    soul["core_identity"]["openness"]["sigma"] = 0.10
    soul["core_identity"]["extraversion"]["mu"] = 0.32
    soul["core_identity"]["extraversion"]["sigma"] = 0.12
    soul["core_identity"]["curiosity"]["mu"] = 0.85
    soul["core_identity"]["curiosity"]["sigma"] = 0.08

    # エピソードを追加
    now = datetime.now(timezone.utc)
    episodes = [
        {
            "id": "ep_001",
            "timestamp": (now - timedelta(days=5)).isoformat(),
            "raw_text": "新しいプロジェクトのアイデアが浮かんでワクワクしている",
            "summary": "新プロジェクトのアイデアに興奮",
            "importance": 0.8,
            "emotion": {"name": "excitement", "intensity": 0.9},
            "personality_signals": [
                {"dimension": "openness", "value": 0.85, "confidence": 0.7},
                {"dimension": "curiosity", "value": 0.90, "confidence": 0.8},
            ],
            "topics": ["プロジェクト", "アイデア", "テクノロジー"],
            "values": ["創造性", "革新"],
            "context": {"location": "自宅", "time_of_day": "evening"},
            "weight": 0.95,
        },
        {
            "id": "ep_002",
            "timestamp": (now - timedelta(days=2)).isoformat(),
            "raw_text": "一人で静かに読書を楽しんだ",
            "summary": "静かに読書を楽しんだ",
            "importance": 0.4,
            "emotion": {"name": "contentment", "intensity": 0.6},
            "personality_signals": [
                {"dimension": "extraversion", "value": 0.25, "confidence": 0.6},
            ],
            "topics": ["読書", "リラックス"],
            "values": ["内省"],
            "context": {"location": "自宅", "time_of_day": "afternoon"},
            "weight": 0.98,
        },
        {
            "id": "ep_003",
            "timestamp": (now - timedelta(days=100)).isoformat(),
            "raw_text": "古いエピソード（期間制限テスト用）",
            "summary": "古いエピソード",
            "importance": 0.3,
            "emotion": {"name": "neutral", "intensity": 0.3},
            "personality_signals": [],
            "topics": ["テスト"],
            "values": [],
            "context": {},
            "weight": 0.1,
        },
    ]
    soul["episodic_memory"]["recent"] = episodes

    # Semantic Map
    soul["semantic_map"]["interests"] = {
        "テクノロジー": 5, "ai": 4, "読書": 3, "音楽": 2, "料理": 1
    }
    soul["semantic_map"]["values"] = {
        "創造性": 3, "革新": 2, "内省": 2, "自律": 1
    }

    # Stats
    soul["stats"]["total_episodes"] = 3
    soul["stats"]["total_updates"] = 3

    return soul


# ════════════════════════════════════════════════════════════════════════════════
# C2: Capability Token テスト
# ════════════════════════════════════════════════════════════════════════════════

def test_token_issue():
    """トークン発行テスト"""
    manager = TokenManager(secret_key="test-key-001")
    scope = SCOPE_TEMPLATES["claude_personal"]
    token = manager.issue("dev001", "claude", scope, expires_in_hours=24)

    assert token.token_id, "Token ID should not be empty"
    assert token.issuer == "dev001"
    assert token.subject == "claude"
    assert token.signature, "Signature should not be empty"
    assert token.is_valid(), "New token should be valid"
    assert not token.is_expired(), "New token should not be expired"
    assert not token.is_consumed(), "New token should not be consumed"
    return True


def test_token_verify():
    """トークン署名検証テスト"""
    manager = TokenManager(secret_key="test-key-002")
    scope = SCOPE_TEMPLATES["minimal"]
    token = manager.issue("dev001", "ai_minimal", scope)

    valid, reason = manager.verify(token)
    assert valid, f"Valid token should verify: {reason}"
    assert reason == "valid"
    return True


def test_token_tamper_detection():
    """改ざん検知テスト"""
    manager = TokenManager(secret_key="test-key-003")
    scope = SCOPE_TEMPLATES["claude_personal"]
    token = manager.issue("dev001", "claude", scope)

    # サブジェクトを改ざん
    token.subject = "hacker"
    valid, reason = manager.verify(token)
    assert not valid, "Tampered token should fail verification"
    assert reason == "invalid_signature"
    return True


def test_token_revocation():
    """トークン失効テスト"""
    manager = TokenManager(secret_key="test-key-004")
    scope = SCOPE_TEMPLATES["claude_personal"]
    token = manager.issue("dev001", "claude", scope)

    # 失効前は有効
    valid1, _ = manager.verify(token)
    assert valid1, "Pre-revoke should be valid"

    # 失効
    result = manager.revoke(token.token_id)
    assert result, "Revoke should succeed"

    # 失効後は無効
    valid2, reason = manager.verify(token)
    assert not valid2, "Post-revoke should fail"
    assert reason == "revoked"
    return True


def test_token_onetime():
    """ワンタイムトークンテスト"""
    manager = TokenManager(secret_key="test-key-005")
    scope = SCOPE_TEMPLATES["minimal"]
    token = manager.issue("dev001", "temp_ai", scope, one_time=True)

    # 1回目の消費は成功
    ok1, r1 = manager.consume(token)
    assert ok1, f"First consume should succeed: {r1}"

    # 2回目は失敗
    ok2, r2 = manager.consume(token)
    assert not ok2, "Second consume should fail"
    assert r2 == "already_consumed"
    return True


def test_token_expiry():
    """有効期限テスト"""
    manager = TokenManager(secret_key="test-key-006")
    scope = SCOPE_TEMPLATES["minimal"]

    # 過去に期限切れするトークンを手動作成
    now = datetime.now(timezone.utc)
    token = CapabilityToken(
        token_id="expired-test",
        issuer="dev001",
        subject="old_ai",
        scope=scope,
        issued_at=now - timedelta(hours=48),
        expires_at=now - timedelta(hours=1),  # 1時間前に失効
    )
    token.signature = manager._sign(token)
    manager._tokens[token.token_id] = token

    valid, reason = manager.verify(token)
    assert not valid, "Expired token should fail"
    assert reason == "expired"
    return True


def test_token_persistence():
    """永続化テスト"""
    manager = TokenManager(secret_key="persist-key")

    for name, scope in SCOPE_TEMPLATES.items():
        manager.issue("dev001", f"ai_{name}", scope)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        manager.save(path)
        assert os.path.exists(path), "Store file should exist"

        # 復元
        manager2 = TokenManager(secret_key="persist-key")
        manager2.load(path)

        assert manager2.token_count == manager.token_count, \
            f"Token count mismatch: {manager2.token_count} vs {manager.token_count}"

        # 復元後のトークンが検証できるか
        for token in manager2.list_active():
            valid, reason = manager2.verify(token)
            assert valid, f"Restored token {token.token_id} should verify: {reason}"
    finally:
        os.unlink(path)

    return True


def test_token_list_active():
    """アクティブトークン一覧テスト"""
    manager = TokenManager(secret_key="list-key")

    t1 = manager.issue("dev001", "ai_1", SCOPE_TEMPLATES["claude_personal"])
    t2 = manager.issue("dev001", "ai_2", SCOPE_TEMPLATES["minimal"])
    t3 = manager.issue("dev001", "ai_3", SCOPE_TEMPLATES["work_assistant"])

    assert manager.active_count == 3

    # 1つ失効
    manager.revoke(t2.token_id)
    assert manager.active_count == 2

    active_ids = {t.token_id for t in manager.list_active()}
    assert t1.token_id in active_ids
    assert t2.token_id not in active_ids
    assert t3.token_id in active_ids
    return True


def test_different_key_fails():
    """異なる鍵で検証失敗テスト"""
    manager1 = TokenManager(secret_key="key-A")
    manager2 = TokenManager(secret_key="key-B")

    token = manager1.issue("dev001", "claude", SCOPE_TEMPLATES["claude_personal"])
    manager2._tokens[token.token_id] = token

    valid, reason = manager2.verify(token)
    assert not valid, "Different key should fail"
    assert reason == "invalid_signature"
    return True


# ════════════════════════════════════════════════════════════════════════════════
# C4: Permission Gateway テスト
# ════════════════════════════════════════════════════════════════════════════════

def test_gateway_filter_claude_personal():
    """Claude Personal スコープのフィルタリング"""
    soul = make_test_soul()
    manager = TokenManager(secret_key="gw-key-001")
    gateway = PermissionGateway(manager)

    token = manager.issue("dev001", "claude", SCOPE_TEMPLATES["claude_personal"])
    result = gateway.filter_soul(soul, token)

    assert result.success, f"Filter should succeed: {result.error}"
    assert "core_identity" in result.categories, "core_identity should be disclosed"
    assert "episodic_memory" in result.categories, "episodic_memory should be disclosed"
    assert "interests_values" in result.categories, "interests should be disclosed"
    assert "health_vitals" not in result.categories, "health_vitals should be hidden"
    assert "location_movement" not in result.categories, "location should be hidden"
    return True


def test_gateway_filter_minimal():
    """Minimal スコープのフィルタリング"""
    soul = make_test_soul()
    manager = TokenManager(secret_key="gw-key-002")
    gateway = PermissionGateway(manager)

    token = manager.issue("dev001", "minimal_ai", SCOPE_TEMPLATES["minimal"])
    result = gateway.filter_soul(soul, token)

    assert result.success
    assert len(result.categories) == 1, f"Minimal should disclose only 1 category, got {len(result.categories)}"
    assert "core_identity" in result.categories
    assert result.categories["core_identity"]["granularity"] == "summary"
    return True


def test_gateway_time_limit():
    """期間制限テスト（90日制限で100日前のエピソードが除外される）"""
    soul = make_test_soul()
    manager = TokenManager(secret_key="gw-key-003")
    gateway = PermissionGateway(manager)

    token = manager.issue("dev001", "claude", SCOPE_TEMPLATES["claude_personal"])
    result = gateway.filter_soul(soul, token)

    assert result.success
    eps = result.categories.get("episodic_memory", {}).get("data", {})
    recent = eps.get("recent", [])
    # 90日制限なので、100日前のep_003は除外されるべき
    ep_ids = [ep.get("id") for ep in recent]
    assert "ep_003" not in ep_ids, f"100-day-old episode should be filtered out, got {ep_ids}"
    assert "ep_001" in ep_ids, "5-day-old episode should be included"
    assert "ep_002" in ep_ids, "2-day-old episode should be included"
    return True


def test_gateway_summary_granularity():
    """Summary粒度: core_identityのσが1.5倍になる"""
    soul = make_test_soul()
    manager = TokenManager(secret_key="gw-key-004")
    gateway = PermissionGateway(manager)

    token = manager.issue("dev001", "work_ai", SCOPE_TEMPLATES["work_assistant"])
    result = gateway.filter_soul(soul, token)

    assert result.success
    ci = result.categories.get("core_identity", {}).get("data", {})
    # work_assistantではcore_identityがsummary → σ * 1.5
    original_sigma = soul["core_identity"]["openness"]["sigma"]  # 0.10
    summary_sigma = ci.get("openness", {}).get("sigma", 0)
    expected = min(original_sigma * 1.5, 0.5)
    assert abs(summary_sigma - expected) < 0.01, \
        f"Summary sigma should be {expected}, got {summary_sigma}"
    return True


def test_gateway_anonymization():
    """Anonymized粒度: social_graphの名前がハッシュ化される"""
    soul = make_test_soul()
    # social_graphデータを追加
    soul["social_graph"] = {
        "relationships": [
            {"person_id": "alice_001", "name": "Alice", "relationship_type": "friend", "strength": 0.9},
            {"person_id": "bob_002", "name": "Bob", "relationship_type": "colleague", "strength": 0.5},
        ]
    }

    manager = TokenManager(secret_key="gw-key-005")
    gateway = PermissionGateway(manager)

    token = manager.issue("dev001", "claude", SCOPE_TEMPLATES["claude_personal"])
    result = gateway.filter_soul(soul, token)

    assert result.success
    sg = result.categories.get("social_graph", {}).get("data", {})
    rels = sg.get("relationships", [])
    assert len(rels) == 2, f"Should have 2 relationships, got {len(rels)}"

    # 名前ではなくハッシュが含まれる
    for rel in rels:
        assert "person_hash" in rel, "Should have person_hash"
        assert "name" not in rel, "Name should not be in anonymized data"
        assert len(rel["person_hash"]) == 12, "Hash should be 12 chars"
    return True


def test_gateway_invalid_token():
    """無効なトークンでフィルタリング拒否"""
    soul = make_test_soul()
    manager = TokenManager(secret_key="gw-key-006")
    gateway = PermissionGateway(manager)

    token = manager.issue("dev001", "claude", SCOPE_TEMPLATES["claude_personal"])
    # 改ざん
    token.subject = "hacker"

    result = gateway.filter_soul(soul, token)
    assert not result.success
    assert "invalid_signature" in result.error
    return True


def test_gateway_revoked_token():
    """失効トークンでフィルタリング拒否"""
    soul = make_test_soul()
    manager = TokenManager(secret_key="gw-key-007")
    gateway = PermissionGateway(manager)

    token = manager.issue("dev001", "claude", SCOPE_TEMPLATES["claude_personal"])
    manager.revoke(token.token_id)

    result = gateway.filter_soul(soul, token)
    assert not result.success
    assert "revoked" in result.error
    return True


def test_gateway_soul_to_prompt():
    """System Prompt生成テスト"""
    soul = make_test_soul()
    manager = TokenManager(secret_key="gw-key-008")
    gateway = PermissionGateway(manager)

    token = manager.issue("dev001", "claude", SCOPE_TEMPLATES["claude_personal"])
    prompt = gateway.soul_to_prompt(soul, token)

    assert len(prompt) > 100, f"Prompt too short: {len(prompt)}"
    assert "パーソナルアシスタント" in prompt
    assert "Claude Personal" in prompt
    # 性格特性が含まれる
    assert "性格特性" in prompt
    return True


def test_gateway_prompt_scope_difference():
    """スコープによるPrompt差分テスト"""
    soul = make_test_soul()
    manager = TokenManager(secret_key="gw-key-009")
    gateway = PermissionGateway(manager)

    # Full scope
    t_full = manager.issue("dev001", "claude", SCOPE_TEMPLATES["claude_personal"])
    prompt_full = gateway.soul_to_prompt(soul, t_full)

    # Minimal scope
    t_min = manager.issue("dev001", "min", SCOPE_TEMPLATES["minimal"])
    prompt_min = gateway.soul_to_prompt(soul, t_min)

    assert len(prompt_full) > len(prompt_min), \
        f"Full prompt ({len(prompt_full)}) should be longer than minimal ({len(prompt_min)})"
    return True


def test_gateway_disclosure_stats():
    """開示統計テスト"""
    soul = make_test_soul()
    manager = TokenManager(secret_key="gw-key-010")
    gateway = PermissionGateway(manager)

    token = manager.issue("dev001", "claude", SCOPE_TEMPLATES["claude_personal"])
    result = gateway.filter_soul(soul, token)

    assert result.stats["total_categories"] == 8
    assert result.stats["disclosed"] > 0
    assert result.stats["hidden"] > 0
    # disclosed + hidden = total (some non-hidden categories may have no data)
    assert result.stats["disclosed"] + result.stats["hidden"] <= 8
    assert 0 < result.stats["disclosure_ratio"] < 1
    return True


# ════════════════════════════════════════════════════════════════════════════════
# Boundary Case テスト
# ════════════════════════════════════════════════════════════════════════════════

def test_boundary_most_restrictive():
    """境界ケース: 最も制限的なカテゴリが適用される"""
    scope = SCOPE_TEMPLATES["claude_personal"]

    # health_vitals (HIDDEN) + episodic_memory (FULL) → HIDDEN
    rule = BoundaryCaseResolver.resolve(["episodic_memory", "health_vitals"], scope)
    assert rule.granularity == GranularityLevel.HIDDEN, \
        f"Should be HIDDEN, got {rule.granularity}"
    return True


def test_boundary_social_graph_priority():
    """境界ケース: social_graphが関わると匿名化が適用される"""
    scope = SCOPE_TEMPLATES["claude_personal"]

    # episodic_memory (FULL) + social_graph (ANONYMIZED) → ANONYMIZED
    rule = BoundaryCaseResolver.resolve(["episodic_memory", "social_graph"], scope)
    assert rule.granularity == GranularityLevel.ANONYMIZED, \
        f"Should be ANONYMIZED, got {rule.granularity}"
    return True


def test_boundary_all_full():
    """境界ケース: 全カテゴリFULLなら結果もFULL"""
    # カスタムスコープ（全FULL）
    scope = DisclosureScope(
        name="All Full",
        categories={
            cat.value: CategoryRule(GranularityLevel.FULL)
            for cat in DisclosureCategory
        },
    )
    rule = BoundaryCaseResolver.resolve(
        ["core_identity", "episodic_memory", "interests_values"], scope
    )
    assert rule.granularity == GranularityLevel.FULL
    return True


# ════════════════════════════════════════════════════════════════════════════════
# テストランナー
# ════════════════════════════════════════════════════════════════════════════════

def run_all_tests():
    tests = [
        # C2: Token
        ("C2-01: Token Issue",            test_token_issue),
        ("C2-02: Token Verify",           test_token_verify),
        ("C2-03: Tamper Detection",       test_token_tamper_detection),
        ("C2-04: Token Revocation",       test_token_revocation),
        ("C2-05: One-Time Token",         test_token_onetime),
        ("C2-06: Token Expiry",           test_token_expiry),
        ("C2-07: Persistence",            test_token_persistence),
        ("C2-08: List Active",            test_token_list_active),
        ("C2-09: Different Key Fails",    test_different_key_fails),
        # C4: Gateway
        ("C4-01: Filter Claude Personal", test_gateway_filter_claude_personal),
        ("C4-02: Filter Minimal",         test_gateway_filter_minimal),
        ("C4-03: Time Limit",             test_gateway_time_limit),
        ("C4-04: Summary Granularity",    test_gateway_summary_granularity),
        ("C4-05: Anonymization",          test_gateway_anonymization),
        ("C4-06: Invalid Token Rejected", test_gateway_invalid_token),
        ("C4-07: Revoked Token Rejected", test_gateway_revoked_token),
        ("C4-08: SOUL to Prompt",         test_gateway_soul_to_prompt),
        ("C4-09: Prompt Scope Diff",      test_gateway_prompt_scope_difference),
        ("C4-10: Disclosure Stats",       test_gateway_disclosure_stats),
        # Boundary Cases
        ("BC-01: Most Restrictive",       test_boundary_most_restrictive),
        ("BC-02: Social Graph Priority",  test_boundary_social_graph_priority),
        ("BC-03: All Full",               test_boundary_all_full),
    ]

    print("═══ Ghost-Printer C2/C4 Test Suite ═══\n")

    passed = 0
    failed = 0
    errors = []

    for name, test_fn in tests:
        try:
            result = test_fn()
            if result:
                print(f"  ✅ {name}")
                passed += 1
            else:
                print(f"  ❌ {name} — returned False")
                failed += 1
                errors.append((name, "Returned False"))
        except Exception as e:
            print(f"  ❌ {name} — {e}")
            failed += 1
            errors.append((name, str(e)))

    print(f"\n═══ Results: {passed}/{passed + failed} passed ═══")

    if errors:
        print("\nFailures:")
        for name, err in errors:
            print(f"  - {name}: {err}")

    return passed, failed


if __name__ == "__main__":
    passed, failed = run_all_tests()
    sys.exit(0 if failed == 0 else 1)
