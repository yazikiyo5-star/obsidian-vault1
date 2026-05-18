"""
Ghost-Printer A7 — 適応型 Watch Point: LlmWpProposer + AdaptiveWatchPointPolicy
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest

from soul_schema import create_empty_soul, create_episode
from watchpoint import WatchPointManager, WatchPointRules, WPTrigger
from watchpoint_llm import (
    AdaptiveWatchPointPolicy,
    ChainedLlmCall,
    LlmHealthState,
    LlmHealthTracker,
    LlmProposal,
    LlmRestartManager,
    LlmWpProposer,
    Policy,
    RestartAttempt,
    build_prompt,
    parse_proposals,
    probe_llm_health,
)


# ════════════════════════════════════════════════════════════════════════════
# フィクスチャ
# ════════════════════════════════════════════════════════════════════════════


def _make_soul() -> dict:
    soul = create_empty_soul("test")
    soul["core_identity"]["openness"] = {"mu": 0.78, "sigma": 0.10}
    soul["core_identity"]["curiosity"] = {"mu": 0.59, "sigma": 0.15}
    soul["core_identity"]["extraversion"] = {"mu": 0.40, "sigma": 0.18}
    soul["semantic_map"]["interests"] = {
        "ai": 8, "hardware": 5, "reading": 4, "coffee": 2, "presentation": 3
    }
    soul["semantic_map"]["values"] = {"creativity": 5, "growth": 3, "perseverance": 2}
    for i, (txt, imp) in enumerate(
        [
            ("深夜まで試作品を作っていた", 0.75),
            ("チームでアイデアを発表", 0.6),
            ("カフェで読書", 0.4),
        ]
    ):
        soul["episodic_memory"]["recent"].append(
            create_episode(
                text=txt,
                importance=imp,
                emotion={"name": "excitement", "intensity": 0.7},
                personality_signals=[],
                topics=["ai" if "試作" in txt else "team"],
                values=[],
                summary=txt[:30],
            )
        )
    return soul


VALID_LLM_RESPONSE = """
ここに提案を書きます:

```json
[
  {
    "target": "weekend_socializing",
    "description": "週末に社交イベントが増える兆候",
    "priority": 0.6,
    "affects_dimensions": ["extraversion", "agreeableness"],
    "rationale": "平日内向 / 週末で会話が増える"
  },
  {
    "target": "Late Night Work",
    "description": "深夜作業の頻度",
    "priority": 0.7,
    "affects_dimensions": ["conscientiousness", "neuroticism"],
    "rationale": "重要度の高い深夜エピソードが目立つ"
  },
  {
    "target": "tech_innovation",
    "description": "ハードウェア+AIへの没入",
    "priority": 1.5,
    "affects_dimensions": ["openness", "creativity", "unknown_dim"],
    "rationale": "AI と hardware が頻出"
  }
]
```
"""


# ════════════════════════════════════════════════════════════════════════════
# プロンプト構築
# ════════════════════════════════════════════════════════════════════════════


class TestPromptBuild:
    def test_prompt_includes_soul_state(self):
        soul = _make_soul()
        wps = []
        prompt = build_prompt(soul, wps, max_new=3)
        assert "openness" in prompt
        assert "0.78" in prompt
        assert "ai" in prompt
        # 既存 WP セクション (空でも見出しが出る)
        assert "Watch Points" in prompt

    def test_prompt_lists_existing_wps(self):
        soul = _make_soul()
        mgr = WatchPointManager()
        wp = mgr.propose(
            target="career_progress",
            trigger=WPTrigger.MANUAL,
            priority=0.8,
            description="career test",
        )
        prompt = build_prompt(soul, [wp], max_new=3)
        assert "career_progress" in prompt
        assert "0.80" in prompt or "0.8" in prompt

    def test_prompt_includes_recent_episodes(self):
        soul = _make_soul()
        prompt = build_prompt(soul, [], max_new=3)
        assert "深夜" in prompt or "試作" in prompt

    def test_prompt_max_new_appears_in_directives(self):
        soul = _make_soul()
        prompt = build_prompt(soul, [], max_new=2)
        assert "最大 2 件" in prompt


# ════════════════════════════════════════════════════════════════════════════
# 応答パース
# ════════════════════════════════════════════════════════════════════════════


class TestParseProposals:
    def test_parse_code_block_response(self):
        proposals = parse_proposals(VALID_LLM_RESPONSE)
        assert len(proposals) == 3
        # 1 件目
        assert proposals[0].target == "weekend_socializing"
        assert "extraversion" in proposals[0].affects_dimensions
        # 2 件目: target 正規化 ('Late Night Work' → 'late_night_work')
        assert proposals[1].target == "late_night_work"
        # 3 件目: priority クランプ (1.5 → 0.9)、 unknown_dim フィルタ
        assert proposals[2].priority == pytest.approx(0.9)
        assert "unknown_dim" not in proposals[2].affects_dimensions
        assert "openness" in proposals[2].affects_dimensions

    def test_parse_array_without_code_block(self):
        raw = '[{"target": "x", "description": "d", "priority": 0.5}]'
        proposals = parse_proposals(raw)
        assert len(proposals) == 1
        assert proposals[0].target == "x"

    def test_parse_invalid_json_returns_empty(self):
        assert parse_proposals("not json at all") == []
        assert parse_proposals("```json\n{not valid}\n```") == []

    def test_parse_drops_invalid_entries(self):
        raw = """[
          {"target": "valid_one", "description": "ok", "priority": 0.5},
          {"target": null, "description": "no name"},
          {"description": "missing target", "priority": 0.5},
          {"target": "valid_two", "description": "ok", "priority": "not_a_num"}
        ]"""
        proposals = parse_proposals(raw)
        assert [p.target for p in proposals] == ["valid_one"]

    def test_parse_excludes_existing_targets(self):
        raw = """[
          {"target": "career_progress", "description": "dup", "priority": 0.6},
          {"target": "new_one", "description": "fresh", "priority": 0.5}
        ]"""
        proposals = parse_proposals(raw, existing_targets={"career_progress"})
        assert [p.target for p in proposals] == ["new_one"]

    def test_parse_excludes_recent_targets(self):
        raw = """[
          {"target": "old_idea", "description": "tried before", "priority": 0.5},
          {"target": "fresh_idea", "description": "new", "priority": 0.5}
        ]"""
        proposals = parse_proposals(raw, recent_targets={"old_idea"})
        assert [p.target for p in proposals] == ["fresh_idea"]

    def test_priority_clamped_low(self):
        raw = '[{"target": "t", "description": "d", "priority": -0.5}]'
        proposals = parse_proposals(raw)
        assert len(proposals) == 1
        assert proposals[0].priority == pytest.approx(0.3)

    def test_target_normalization(self):
        raw = '[{"target": "  Some MIXED-target!! ", "description": "d", "priority": 0.5}]'
        proposals = parse_proposals(raw)
        assert len(proposals) == 1
        # 空白→_ , 大文字→小文字、 記号は剥がれる
        assert proposals[0].target == "some_mixed-target"[:30].replace("-", "")


# ════════════════════════════════════════════════════════════════════════════
# LlmWpProposer
# ════════════════════════════════════════════════════════════════════════════


class TestLlmWpProposer:
    def test_propose_round_trip(self):
        proposer = LlmWpProposer(llm_call=lambda p: VALID_LLM_RESPONSE)
        soul = _make_soul()
        result = proposer.propose_watchpoints(soul, [], max_new=3)
        assert len(result) == 3
        assert all(isinstance(p, LlmProposal) for p in result)

    def test_max_new_truncation(self):
        proposer = LlmWpProposer(llm_call=lambda p: VALID_LLM_RESPONSE)
        soul = _make_soul()
        result = proposer.propose_watchpoints(soul, [], max_new=2)
        assert len(result) == 2

    def test_zero_max_new_skips_call(self):
        called = [0]

        def llm(p: str) -> str:
            called[0] += 1
            return VALID_LLM_RESPONSE

        proposer = LlmWpProposer(llm_call=llm)
        result = proposer.propose_watchpoints(_make_soul(), [], max_new=0)
        assert result == []
        assert called[0] == 0

    def test_llm_exception_returns_empty(self):
        def bad_llm(p: str) -> str:
            raise RuntimeError("ollama down")

        proposer = LlmWpProposer(llm_call=bad_llm)
        result = proposer.propose_watchpoints(_make_soul(), [], max_new=3)
        assert result == []

    def test_recent_targets_suppress_resuggestion(self):
        proposer = LlmWpProposer(
            llm_call=lambda p: VALID_LLM_RESPONSE,
            recent_suppress_days=7.0,
        )
        soul = _make_soul()
        # 1 回目: 3 件採用される
        first = proposer.propose_watchpoints(soul, [], max_new=3)
        assert len(first) == 3
        # 2 回目: 同じ LLM 応答だが、 すでに recent に入っているので除外される
        second = proposer.propose_watchpoints(soul, [], max_new=3)
        assert second == []

    def test_existing_wps_excluded(self):
        # 1 件目の target を既存 WP として渡す → 除外される
        mgr = WatchPointManager()
        existing = mgr.propose(
            target="weekend_socializing",
            trigger=WPTrigger.MANUAL,
            priority=0.5,
            description="dup",
        )
        proposer = LlmWpProposer(llm_call=lambda p: VALID_LLM_RESPONSE)
        result = proposer.propose_watchpoints(_make_soul(), [existing], max_new=3)
        assert all(p.target != "weekend_socializing" for p in result)
        assert len(result) == 2  # 残り 2 件


# ════════════════════════════════════════════════════════════════════════════
# AdaptiveWatchPointPolicy
# ════════════════════════════════════════════════════════════════════════════


def _topic_history_concentrated() -> list[dict]:
    """ai トピックが過半数を占める履歴"""
    return [{"ai": 0.8, "coffee": 0.2}] * 4


def _episodes_with_high_importance() -> list[dict]:
    return [
        {"importance": 0.85, "topic_distribution": {"career": 0.8, "stress": 0.2}},
        {"importance": 0.80, "topic_distribution": {"career": 0.7, "stress": 0.3}},
        {"importance": 0.90, "topic_distribution": {"career": 0.9}},
    ]


class TestAdaptivePolicy:
    def test_rules_only_does_not_call_llm(self):
        called = [0]
        proposer = LlmWpProposer(llm_call=lambda p: (called.__setitem__(0, called[0]+1), VALID_LLM_RESPONSE)[1])
        policy = AdaptiveWatchPointPolicy(
            rules=WatchPointRules(),
            proposer=proposer,
            policy=Policy.RULES_ONLY,
            max_new_per_cycle=3,
        )
        proposals = policy.gather_proposals(
            soul=_make_soul(),
            existing_wps=[],
            topic_history=_topic_history_concentrated(),
            recent_episodes=_episodes_with_high_importance(),
        )
        assert called[0] == 0  # LLM は呼ばれない
        # ルール由来の候補が 1 件以上
        assert len(proposals) >= 1
        # ルール経路はトピック由来 / importance 由来のいずれかを返す
        assert all(p.get("target") for p in proposals)

    def test_llm_only_does_not_use_rules(self):
        proposer = LlmWpProposer(llm_call=lambda p: VALID_LLM_RESPONSE)
        policy = AdaptiveWatchPointPolicy(
            rules=WatchPointRules(),
            proposer=proposer,
            policy=Policy.LLM_ONLY,
            max_new_per_cycle=3,
        )
        proposals = policy.gather_proposals(
            soul=_make_soul(),
            existing_wps=[],
            topic_history=_topic_history_concentrated(),
            recent_episodes=_episodes_with_high_importance(),
        )
        assert len(proposals) == 3
        # すべて LLM 由来 (trigger=LLM_SUGGESTED)
        assert all(p["trigger"] == WPTrigger.LLM_SUGGESTED for p in proposals)

    def test_hybrid_rules_first_then_llm(self):
        proposer = LlmWpProposer(llm_call=lambda p: VALID_LLM_RESPONSE)
        policy = AdaptiveWatchPointPolicy(
            rules=WatchPointRules(),
            proposer=proposer,
            policy=Policy.HYBRID,
            max_new_per_cycle=3,
        )
        proposals = policy.gather_proposals(
            soul=_make_soul(),
            existing_wps=[],
            topic_history=_topic_history_concentrated(),
            recent_episodes=_episodes_with_high_importance(),
        )
        # 合計 3 件
        assert len(proposals) == 3
        triggers = [p["trigger"] for p in proposals]
        # ルール由来が少なくとも 1 件
        rule_triggers = {
            WPTrigger.TOPIC_CONCENTRATION,
            WPTrigger.IMPORTANCE_SPIKE,
            WPTrigger.PERSONALITY_UNCERTAINTY,
        }
        assert any(t in rule_triggers for t in triggers)
        # LLM 由来も含まれている (枠が余ったため)
        assert WPTrigger.LLM_SUGGESTED in triggers

    def test_hybrid_falls_back_to_rules_on_llm_failure(self):
        proposer = LlmWpProposer(llm_call=lambda p: (_ for _ in ()).throw(RuntimeError("down")))
        policy = AdaptiveWatchPointPolicy(
            rules=WatchPointRules(),
            proposer=proposer,
            policy=Policy.HYBRID,
            max_new_per_cycle=3,
        )
        proposals = policy.gather_proposals(
            soul=_make_soul(),
            existing_wps=[],
            topic_history=_topic_history_concentrated(),
            recent_episodes=_episodes_with_high_importance(),
        )
        # ルール由来だけで継続している (例外で落ちない)
        assert len(proposals) >= 1
        # LLM 由来は無い
        assert all(p["trigger"] != WPTrigger.LLM_SUGGESTED for p in proposals)

    def test_existing_wp_dedup(self):
        # 既に "career_decisions" がある状態で IMPORTANCE_SPIKE がそれを提案 → 除外
        mgr = WatchPointManager()
        existing = mgr.propose(
            target="career_decisions",
            trigger=WPTrigger.MANUAL,
            priority=0.7,
            description="already there",
        )
        proposer = LlmWpProposer(llm_call=lambda p: "[]")
        policy = AdaptiveWatchPointPolicy(
            rules=WatchPointRules(),
            proposer=proposer,
            policy=Policy.RULES_ONLY,
            max_new_per_cycle=3,
        )
        proposals = policy.gather_proposals(
            soul=_make_soul(),
            existing_wps=[existing],
            recent_episodes=_episodes_with_high_importance(),
        )
        assert all(p["target"] != "career_decisions" for p in proposals)

    def test_proposer_missing_falls_back_to_rules_only(self):
        # proposer=None なら HYBRID/LLM_ONLY 指定でも RULES_ONLY に降格
        policy = AdaptiveWatchPointPolicy(
            rules=WatchPointRules(),
            proposer=None,
            policy=Policy.HYBRID,
            max_new_per_cycle=3,
        )
        assert policy.policy == Policy.RULES_ONLY


# ════════════════════════════════════════════════════════════════════════════
# WatchPointManager との統合
# ════════════════════════════════════════════════════════════════════════════


class TestManagerIntegration:
    def test_propose_kwargs_reaches_manager(self):
        """LlmProposal.to_propose_kwargs を WatchPointManager.propose に渡せる"""
        mgr = WatchPointManager()
        proposer = LlmWpProposer(llm_call=lambda p: VALID_LLM_RESPONSE)
        soul = _make_soul()
        proposals = proposer.propose_watchpoints(soul, [], max_new=3)
        assert len(proposals) == 3

        for p in proposals:
            wp = mgr.propose(**p.to_propose_kwargs())
            assert wp is not None
            assert wp.trigger == WPTrigger.LLM_SUGGESTED

        assert len(mgr.active()) == 3


# ════════════════════════════════════════════════════════════════════════════
# テスト: LlmHealthTracker (LLM 永久死亡からの自己治癒)
# ════════════════════════════════════════════════════════════════════════════


class TestHealthTracker:
    def test_initial_state_healthy(self):
        t = LlmHealthTracker()
        assert t.state == LlmHealthState.HEALTHY
        assert t.consecutive_failures == 0
        assert t.is_healthy()
        assert not t.is_dead()

    def test_failures_below_threshold_stay_healthy(self):
        t = LlmHealthTracker(degraded_threshold=3)
        t.record_failure(reason="test")
        t.record_failure(reason="test")
        assert t.state == LlmHealthState.HEALTHY
        assert t.consecutive_failures == 2

    def test_three_consecutive_failures_degrade(self):
        t = LlmHealthTracker(degraded_threshold=3)
        for _ in range(3):
            t.record_failure(reason="test")
        assert t.state == LlmHealthState.DEGRADED
        assert t.consecutive_failures == 3
        assert not t.is_dead()

    def test_success_resets_to_healthy_from_degraded(self):
        t = LlmHealthTracker(degraded_threshold=2)
        t.record_failure()
        t.record_failure()
        assert t.state == LlmHealthState.DEGRADED
        t.record_success()
        assert t.state == LlmHealthState.HEALTHY
        assert t.consecutive_failures == 0

    def test_dead_when_long_since_success(self):
        """過去成功あり + 24h経過 + 連続失敗 → DEAD"""
        t = LlmHealthTracker(degraded_threshold=2, dead_threshold_hours=24.0)
        # 成功は 25h 前にあった
        t.last_success_at = datetime.now(timezone.utc) - timedelta(hours=25)
        for _ in range(2):
            t.record_failure(reason="bonsai_oom")
        assert t.state == LlmHealthState.DEAD
        assert t.is_dead()

    def test_dead_when_never_succeeded_and_old_creation(self):
        """一度も成功なし + 起動から 24h+ + 連続失敗 → DEAD"""
        t = LlmHealthTracker(degraded_threshold=2, dead_threshold_hours=24.0)
        t.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        for _ in range(2):
            t.record_failure(reason="never_started")
        assert t.state == LlmHealthState.DEAD

    def test_success_after_dead_recovers(self):
        """DEAD 状態からの復帰: 1 回成功で HEALTHY に戻る"""
        t = LlmHealthTracker(degraded_threshold=1, dead_threshold_hours=0.0)
        # すぐ DEAD にする
        t.record_failure()
        assert t.state == LlmHealthState.DEAD
        t.record_success()
        assert t.state == LlmHealthState.HEALTHY
        assert t.consecutive_failures == 0

    def test_status_summary_format(self):
        t = LlmHealthTracker()
        t.record_failure(reason="connection refused")
        s = t.status_summary()
        assert s["state"] == "healthy"  # 1 件では DEGRADED にならない
        assert s["consecutive_failures"] == 1
        assert s["last_failure_reason"] == "connection refused"
        assert s["total_invocations"] == 1
        assert s["total_failures"] == 1
        assert s["failure_rate"] == 1.0

    def test_to_dict_from_dict_round_trip(self):
        t = LlmHealthTracker(degraded_threshold=5, dead_threshold_hours=12.0)
        for _ in range(2):
            t.record_failure(reason="x")
        d = t.to_dict()
        t2 = LlmHealthTracker.from_dict(d)
        assert t2.state == t.state
        assert t2.consecutive_failures == 2
        assert t2.degraded_threshold == 5
        assert t2.dead_threshold_hours == 12.0
        assert t2.last_failure_reason == "x"


# ════════════════════════════════════════════════════════════════════════════
# テスト: LlmWpProposer × HealthTracker
# ════════════════════════════════════════════════════════════════════════════


class TestProposerHealth:
    def test_records_success_on_call(self):
        proposer = LlmWpProposer(llm_call=lambda p: VALID_LLM_RESPONSE)
        proposer.propose_watchpoints(_make_soul(), [], max_new=3)
        assert proposer.health.state == LlmHealthState.HEALTHY
        assert proposer.health.consecutive_failures == 0
        assert proposer.health.total_invocations == 1

    def test_records_failure_on_exception(self):
        def bad_llm(p: str) -> str:
            raise RuntimeError("ollama unreachable")

        proposer = LlmWpProposer(llm_call=bad_llm)
        proposer.propose_watchpoints(_make_soul(), [], max_new=3)
        assert proposer.health.consecutive_failures == 1
        assert "ollama unreachable" in proposer.health.last_failure_reason

    def test_skip_when_dead(self):
        """DEAD 状態では LLM 呼出が完全に skip される"""
        called = [0]

        def llm(p: str) -> str:
            called[0] += 1
            return VALID_LLM_RESPONSE

        # 2 失敗で DEAD になる設定
        health = LlmHealthTracker(degraded_threshold=1, dead_threshold_hours=0.0)
        proposer = LlmWpProposer(llm_call=llm, health=health)
        # 1 件目: HEALTHY → 成功
        proposer.propose_watchpoints(_make_soul(), [], max_new=1)
        # わざと DEAD に倒す
        proposer.health.record_failure()
        assert proposer.health.is_dead()
        called[0] = 0
        # DEAD 以降は呼ばれない
        result = proposer.propose_watchpoints(_make_soul(), [], max_new=3)
        assert result == []
        assert called[0] == 0

    def test_skip_when_dead_disabled(self):
        """skip_when_dead=False なら DEAD でも呼出を試みる"""
        health = LlmHealthTracker(degraded_threshold=1, dead_threshold_hours=0.0)
        health.record_failure()  # DEAD
        called = [0]

        def llm(p: str) -> str:
            called[0] += 1
            raise RuntimeError("still down")

        proposer = LlmWpProposer(llm_call=llm, health=health, skip_when_dead=False)
        proposer.propose_watchpoints(_make_soul(), [], max_new=3)
        assert called[0] == 1


# ════════════════════════════════════════════════════════════════════════════
# テスト: probe_llm_health
# ════════════════════════════════════════════════════════════════════════════


class TestProbe:
    def test_probe_success_recovers_state(self):
        """DEAD 状態のプロキシに probe で復帰させる"""
        health = LlmHealthTracker(degraded_threshold=1, dead_threshold_hours=0.0)
        health.record_failure()
        assert health.is_dead()

        proposer = LlmWpProposer(
            llm_call=lambda p: "OK\n",
            health=health,
        )
        ok = probe_llm_health(proposer)
        assert ok is True
        assert proposer.health.state == LlmHealthState.HEALTHY
        assert proposer.health.consecutive_failures == 0

    def test_probe_failure_records(self):
        proposer = LlmWpProposer(
            llm_call=lambda p: (_ for _ in ()).throw(ConnectionError("offline"))
        )
        ok = probe_llm_health(proposer)
        assert ok is False
        assert proposer.health.consecutive_failures == 1
        assert "probe:" in proposer.health.last_failure_reason

    def test_probe_empty_response_treated_as_failure(self):
        proposer = LlmWpProposer(llm_call=lambda p: "   ")
        ok = probe_llm_health(proposer)
        assert ok is False
        assert "empty_response" in proposer.health.last_failure_reason


# ════════════════════════════════════════════════════════════════════════════
# テスト: ChainedLlmCall
# ════════════════════════════════════════════════════════════════════════════


class TestChainedLlmCall:
    def test_primary_used_when_healthy(self):
        primary_called = [0]
        secondary_called = [0]

        def primary(p: str) -> str:
            primary_called[0] += 1
            return "primary_response"

        def secondary(p: str) -> str:
            secondary_called[0] += 1
            return "secondary_response"

        chain = ChainedLlmCall(primary=primary, secondary=secondary)
        result = chain("hello")
        assert result == "primary_response"
        assert primary_called[0] == 1
        assert secondary_called[0] == 0
        assert chain.fallback_count == 0
        assert chain.primary_health.state == LlmHealthState.HEALTHY

    def test_fallback_to_secondary_on_primary_failure(self):
        secondary_called = [0]

        def primary(p: str) -> str:
            raise RuntimeError("bonsai oom")

        def secondary(p: str) -> str:
            secondary_called[0] += 1
            return "fallback_response"

        chain = ChainedLlmCall(primary=primary, secondary=secondary)
        result = chain("hello")
        assert result == "fallback_response"
        assert secondary_called[0] == 1
        assert chain.fallback_count == 1
        assert chain.primary_health.consecutive_failures == 1

    def test_skip_primary_when_dead(self):
        primary_called = [0]

        def primary(p: str) -> str:
            primary_called[0] += 1
            return "should_not_be_called"

        def secondary(p: str) -> str:
            return "secondary"

        chain = ChainedLlmCall(primary=primary, secondary=secondary)
        # primary を強制的に DEAD にする
        chain.primary_health.degraded_threshold = 1
        chain.primary_health.dead_threshold_hours = 0.0
        chain.primary_health.record_failure()
        assert chain.primary_health.is_dead()
        primary_called[0] = 0
        result = chain("hello")
        assert result == "secondary"
        assert primary_called[0] == 0
        assert chain.fallback_count == 1

    def test_chained_used_as_proposer_call(self):
        """ChainedLlmCall を LlmWpProposer の llm_call に渡せる E2E"""

        def primary(p: str) -> str:
            raise TimeoutError("bonsai slow")

        def secondary(p: str) -> str:
            return VALID_LLM_RESPONSE

        chain = ChainedLlmCall(primary=primary, secondary=secondary)
        proposer = LlmWpProposer(llm_call=chain)
        result = proposer.propose_watchpoints(_make_soul(), [], max_new=3)
        # primary は失敗したが secondary が JSON を返したので提案が取れる
        assert len(result) == 3
        assert chain.fallback_count == 1
        # proposer 自身の health は (chained 全体としては) 成功
        assert proposer.health.state == LlmHealthState.HEALTHY
        # primary の健康度は別軌道で劣化を記録
        assert chain.primary_health.consecutive_failures == 1

    def test_primary_recovers_after_probe(self):
        """primary が DEAD 状態でも、 probe 経由で primary_health を復帰できる"""
        primary_calls = [0]
        primary_alive = [False]

        def primary(p: str) -> str:
            primary_calls[0] += 1
            if primary_alive[0]:
                return "primary OK"
            raise RuntimeError("not yet")

        def secondary(p: str) -> str:
            return "secondary"

        chain = ChainedLlmCall(primary=primary, secondary=secondary)
        chain.primary_health.degraded_threshold = 1
        chain.primary_health.dead_threshold_hours = 0.0

        # 1 度呼んで primary 失敗 → DEAD
        chain("first")
        assert chain.primary_health.is_dead()

        # primary が復活した想定
        primary_alive[0] = True

        # primary を probe するための独立 Proposer を作る
        primary_only_proposer = LlmWpProposer(
            llm_call=primary, health=chain.primary_health
        )
        ok = probe_llm_health(primary_only_proposer)
        assert ok is True
        assert chain.primary_health.state == LlmHealthState.HEALTHY

        # 次の chain 呼出は primary を使う
        result = chain("second")
        assert result == "primary OK"


# ════════════════════════════════════════════════════════════════════════════
# テスト: LlmRestartManager (Tier 1 — DEAD 永続化への対処)
# ════════════════════════════════════════════════════════════════════════════


def _make_dead_health(*, dead_minutes_ago: float = 10.0) -> LlmHealthTracker:
    """指定分前から DEAD 状態の health を作る"""
    h = LlmHealthTracker(degraded_threshold=1, dead_threshold_hours=0.0)
    h.record_failure(reason="setup")
    assert h.is_dead()
    h.state_changed_at = datetime.now(timezone.utc) - timedelta(
        minutes=dead_minutes_ago
    )
    return h


class TestLlmRestartManager:
    def test_does_not_restart_when_healthy(self):
        h = LlmHealthTracker()
        mgr = LlmRestartManager(["echo", "ok"], health=h, dry_run=True)
        ok, reason = mgr.should_restart()
        assert not ok
        assert "not dead" in reason
        assert mgr.maybe_restart() is None

    def test_does_not_restart_until_dead_threshold_met(self):
        h = _make_dead_health(dead_minutes_ago=2.0)
        mgr = LlmRestartManager(
            ["echo", "ok"], health=h, min_dead_minutes=5.0, dry_run=True
        )
        ok, reason = mgr.should_restart()
        assert not ok
        assert "dead only" in reason
        assert mgr.maybe_restart() is None

    def test_restarts_after_dead_threshold(self):
        h = _make_dead_health(dead_minutes_ago=10.0)
        mgr = LlmRestartManager(
            ["echo", "ok"], health=h, min_dead_minutes=5.0, dry_run=True
        )
        attempt = mgr.maybe_restart()
        assert attempt is not None
        assert attempt.success is True
        assert "DRY RUN" in attempt.output
        assert len(mgr.history) == 1

    def test_dry_run_does_not_execute_command(self, tmp_path):
        marker = tmp_path / "should_not_exist.txt"
        # 実行されたら touch で marker ができる
        h = _make_dead_health(dead_minutes_ago=10.0)
        mgr = LlmRestartManager(
            ["touch", str(marker)],
            health=h,
            min_dead_minutes=5.0,
            dry_run=True,
        )
        attempt = mgr.maybe_restart()
        assert attempt.success is True
        assert not marker.exists()

    def test_real_command_execution(self, tmp_path):
        """実コマンド (echo) を実行して exit=0 を確認"""
        h = _make_dead_health(dead_minutes_ago=10.0)
        mgr = LlmRestartManager(
            ["echo", "restarted"],
            health=h,
            min_dead_minutes=5.0,
            dry_run=False,
        )
        attempt = mgr.maybe_restart()
        assert attempt is not None
        assert attempt.success is True
        assert attempt.exit_code == 0
        assert "restarted" in attempt.output

    def test_failed_command_recorded(self):
        h = _make_dead_health(dead_minutes_ago=10.0)
        mgr = LlmRestartManager(
            ["false"],   # exit=1
            health=h,
            min_dead_minutes=5.0,
            dry_run=False,
        )
        attempt = mgr.maybe_restart()
        assert attempt is not None
        assert attempt.success is False
        assert attempt.exit_code == 1

    def test_command_not_found(self):
        h = _make_dead_health(dead_minutes_ago=10.0)
        mgr = LlmRestartManager(
            ["/nonexistent/command/that/cannot/exist_xyz"],
            health=h,
            min_dead_minutes=5.0,
            dry_run=False,
        )
        attempt = mgr.maybe_restart()
        assert attempt is not None
        assert attempt.success is False
        assert "command not found" in attempt.error.lower() or "not found" in attempt.error.lower()

    def test_command_timeout(self):
        h = _make_dead_health(dead_minutes_ago=10.0)
        mgr = LlmRestartManager(
            ["sleep", "10"],
            health=h,
            min_dead_minutes=5.0,
            timeout_s=0.5,
            dry_run=False,
        )
        attempt = mgr.maybe_restart()
        assert attempt is not None
        assert attempt.success is False
        assert "timed out" in attempt.error.lower()

    def test_cooldown_prevents_rapid_restart(self):
        h = _make_dead_health(dead_minutes_ago=20.0)
        mgr = LlmRestartManager(
            ["echo", "ok"],
            health=h,
            min_dead_minutes=5.0,
            cooldown_minutes=10.0,
            dry_run=True,
        )
        first = mgr.maybe_restart()
        assert first is not None
        # 直後に再度試そうとすると cooldown で skip される
        ok, reason = mgr.should_restart()
        assert not ok
        assert "cooldown" in reason

    def test_rate_limit_per_hour(self):
        h = _make_dead_health(dead_minutes_ago=120.0)
        mgr = LlmRestartManager(
            ["echo", "ok"],
            health=h,
            min_dead_minutes=5.0,
            cooldown_minutes=0.0,        # cooldown 無効化してテスト
            max_restarts_per_hour=2,
            dry_run=True,
        )
        # 2 回までは通る
        a1 = mgr.maybe_restart()
        a2 = mgr.maybe_restart()
        assert a1 is not None and a2 is not None
        # 3 回目は rate limit で skip
        ok, reason = mgr.should_restart()
        assert not ok
        assert "rate limit" in reason

    def test_history_truncates_at_limit(self):
        h = _make_dead_health(dead_minutes_ago=120.0)
        mgr = LlmRestartManager(
            ["echo", "ok"],
            health=h,
            min_dead_minutes=0.0,
            cooldown_minutes=0.0,
            max_restarts_per_hour=999,
            dry_run=True,
            history_limit=5,
        )
        for _ in range(10):
            mgr.restart_now(reason="stress")
        assert len(mgr.history) == 5

    def test_stats_summary(self):
        h = _make_dead_health(dead_minutes_ago=120.0)
        mgr = LlmRestartManager(
            ["echo", "ok"],
            health=h,
            min_dead_minutes=0.0,
            cooldown_minutes=0.0,
            max_restarts_per_hour=999,
            dry_run=True,
        )
        for _ in range(3):
            mgr.restart_now(reason="t")
        s = mgr.stats()
        assert s["total_attempts"] == 3
        assert s["attempts_24h"] == 3
        assert s["successes_24h"] == 3
        assert s["last_success_at"] is not None
        assert s["dry_run"] is True

    def test_recovery_after_successful_restart(self):
        """restart 成功後 probe で health が HEALTHY に戻る統合シナリオ"""
        # primary は最初は失敗、 restart 後に成功する想定
        alive = [False]

        def primary(p: str) -> str:
            if alive[0]:
                return "OK"
            raise RuntimeError("dead")

        h = _make_dead_health(dead_minutes_ago=10.0)
        proposer = LlmWpProposer(llm_call=primary, health=h)

        # restart で alive を True にする (mock の代わりに dry_run + 後で alive 切替)
        mgr = LlmRestartManager(
            ["echo", "restarted"],
            health=h,
            min_dead_minutes=5.0,
            dry_run=False,
        )
        attempt = mgr.maybe_restart()
        assert attempt and attempt.success
        # 模擬: restart によって LLM が起きた
        alive[0] = True
        # probe で復帰
        ok = probe_llm_health(proposer)
        assert ok is True
        assert h.state == LlmHealthState.HEALTHY

    def test_empty_command_rejected(self):
        h = LlmHealthTracker()
        with pytest.raises(ValueError, match="non-empty"):
            LlmRestartManager([], health=h)
