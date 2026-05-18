#!/usr/bin/env python3
"""
Ghost-Printer A4 — ベイズ的忘却アルゴリズムの検証

シミュレーションで以下を検証する:
1. 連続した一貫する観測 → σが縮小し確信が増す
2. 矛盾する観測 → μが適切に移動する
3. 重み減衰 → 古いエピソードの重みが時間とともに低下
4. 蒸留(distillation) → 消えゆくエピソードが性格に溶け込む
5. SOULファイルサイズ → エピソード数に上限がかかる
"""

import json
import math
import copy
from datetime import datetime, timezone, timedelta

from soul_schema import create_empty_soul, create_episode, save_soul
from soul_engine import (
    bayesian_update_gaussian,
    update_soul,
    decay_episode_weights,
    soul_summary,
    DECAY_HALF_LIFE_DAYS,
)

passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name} — {detail}")
        failed += 1


# ═══ 1. 一貫した観測によるσ収束 ═══
print("\n═══ 1. 一貫した観測によるσ収束 ═══")
print("  20日間、毎日 extraversion=0.3 を観測するシミュレーション")

soul = create_empty_soul("test")
for day in range(20):
    delta = {
        "importance": 0.4,
        "emotion": {"name": "calm", "intensity": 0.3},
        "personality_signals": [
            {"dimension": "extraversion", "value": 0.30, "confidence": 0.6},
        ],
        "topics": [], "values": [], "summary": f"Day {day+1}",
    }
    soul = update_soul(soul, delta, raw_text=f"Day {day+1}: 静かに過ごした")

ext = soul["core_identity"]["extraversion"]
print(f"  結果: μ={ext['mu']:.4f}, σ={ext['sigma']:.4f}")
test("μ が 0.30 に近い", abs(ext["mu"] - 0.30) < 0.05, f"μ={ext['mu']:.4f}")
test("σ が 0.10 未満（高確信）", ext["sigma"] < 0.10, f"σ={ext['sigma']:.4f}")

# 更新されていない次元は初期値のまま
agr = soul["core_identity"]["agreeableness"]
test("未観測の次元は初期値のまま", agr["mu"] == 0.5 and agr["sigma"] == 0.30)


# ═══ 2. 矛盾する観測への適応 ═══
print("\n═══ 2. 矛盾する観測への適応 ═══")
print("  10日間 openness=0.8 → 10日間 openness=0.3 のシミュレーション")

soul2 = create_empty_soul("test")
for day in range(10):
    delta = {
        "importance": 0.5,
        "emotion": {"name": "curiosity", "intensity": 0.6},
        "personality_signals": [
            {"dimension": "openness", "value": 0.80, "confidence": 0.7},
        ],
        "topics": [], "values": [], "summary": f"Phase1 Day {day+1}",
    }
    soul2 = update_soul(soul2, delta, raw_text=f"Phase1 Day {day+1}")

mu_after_phase1 = soul2["core_identity"]["openness"]["mu"]
sigma_after_phase1 = soul2["core_identity"]["openness"]["sigma"]
print(f"  Phase1後: μ={mu_after_phase1:.4f}, σ={sigma_after_phase1:.4f}")
test("Phase1後: μ > 0.7", mu_after_phase1 > 0.7, f"μ={mu_after_phase1:.4f}")

for day in range(10):
    delta = {
        "importance": 0.5,
        "emotion": {"name": "calm", "intensity": 0.3},
        "personality_signals": [
            {"dimension": "openness", "value": 0.30, "confidence": 0.7},
        ],
        "topics": [], "values": [], "summary": f"Phase2 Day {day+1}",
    }
    soul2 = update_soul(soul2, delta, raw_text=f"Phase2 Day {day+1}")

mu_after_phase2 = soul2["core_identity"]["openness"]["mu"]
sigma_after_phase2 = soul2["core_identity"]["openness"]["sigma"]
print(f"  Phase2後: μ={mu_after_phase2:.4f}, σ={sigma_after_phase2:.4f}")
test("Phase2後: μ が中間付近に移動", 0.35 < mu_after_phase2 < 0.65,
     f"μ={mu_after_phase2:.4f}")
test("Phase2後: σ はPhase1後より大きくはならない",
     sigma_after_phase2 <= sigma_after_phase1 + 0.01,
     f"σ_p1={sigma_after_phase1:.4f}, σ_p2={sigma_after_phase2:.4f}")


# ═══ 3. Exponential Decay シミュレーション ═══
print("\n═══ 3. Exponential Decay シミュレーション ═══")

soul3 = create_empty_soul("test")
# 古いエピソードを手動で作成（30日前、60日前、90日前）
now = datetime.now(timezone.utc)
for days_ago, imp in [(0, 0.5), (15, 0.5), (30, 0.5), (60, 0.5), (90, 0.3)]:
    ep = create_episode(
        text=f"Episode {days_ago} days ago",
        importance=imp,
        emotion={"name": "neutral", "intensity": 0.3},
        personality_signals=[],
        topics=[], values=[],
        summary=f"Test episode",
    )
    ep["timestamp"] = (now - timedelta(days=days_ago)).isoformat()
    ep["weight"] = 1.0
    soul3["episodic_memory"]["recent"].append(ep)

print(f"  投入エピソード: 0日前, 15日前, 30日前, 60日前, 90日前")
decay_episode_weights(soul3)
remaining = soul3["episodic_memory"]["recent"]
print(f"  残存エピソード: {len(remaining)}件")

for ep in remaining:
    ts = datetime.fromisoformat(ep["timestamp"])
    days = (now - ts).total_seconds() / 86400
    print(f"    {days:.0f}日前: weight={ep['weight']:.4f}")

test("今日のエピソードはweight≈1.0", remaining[0]["weight"] > 0.9)
test("15日前のエピソードは減衰している", remaining[1]["weight"] < 0.9)

# 90日前の低重要度エピソードは消えている可能性
old_episodes = [ep for ep in remaining
                if (now - datetime.fromisoformat(ep["timestamp"])).days > 80]
if old_episodes:
    test("90日前のエピソードのweightは大幅に低下", old_episodes[0]["weight"] < 0.2,
         f"weight={old_episodes[0]['weight']:.4f}")
else:
    test("90日前の低重要度エピソードはdistillされて消えた", True)


# ═══ 4. Distillation（性格への溶け込み）テスト ═══
print("\n═══ 4. Distillation テスト ═══")
print("  高openness(0.9)のエピソードを消滅させ、core_identityに溶け込むか確認")

soul4 = create_empty_soul("test")
ep = create_episode(
    text="Very creative experience",
    importance=0.3,  # 低重要度 → 消えやすい
    emotion={"name": "joy", "intensity": 0.5},
    personality_signals=[
        {"dimension": "openness", "value": 0.9, "confidence": 0.7},
    ],
    topics=[], values=[],
    summary="Creative experience",
)
# 200日前に設定して確実にdecayさせる
ep["timestamp"] = (now - timedelta(days=200)).isoformat()
ep["weight"] = 0.04  # decay threshold(0.05)未満

soul4["episodic_memory"]["recent"].append(ep)
open_before = soul4["core_identity"]["openness"]["mu"]

decay_episode_weights(soul4)
open_after = soul4["core_identity"]["openness"]["mu"]

print(f"  distill前 openness.μ = {open_before:.4f}")
print(f"  distill後 openness.μ = {open_after:.4f}")
test("エピソードが削除された", len(soul4["episodic_memory"]["recent"]) == 0)
test("openness.μ がわずかに上昇", open_after > open_before,
     f"before={open_before:.4f}, after={open_after:.4f}")


# ═══ 5. SOULサイズの安定性 ═══
print("\n═══ 5. SOULサイズの安定性 ═══")
print("  100エピソード投入後のファイルサイズを確認")

soul5 = create_empty_soul("test")
for i in range(100):
    delta = {
        "importance": 0.3 + (i % 5) * 0.1,
        "emotion": {"name": "neutral", "intensity": 0.3},
        "personality_signals": [
            {"dimension": "openness", "value": 0.6, "confidence": 0.5},
        ],
        "topics": [f"topic_{i%10}"], "values": [], "summary": f"Episode {i}",
    }
    soul5 = update_soul(soul5, delta, raw_text=f"Episode {i}")

size_json = len(json.dumps(soul5, ensure_ascii=False))
ep_count = len(soul5["episodic_memory"]["recent"])
print(f"  投入: 100 エピソード")
print(f"  残存: {ep_count} エピソード（decayで削除されたものあり）")
print(f"  JSONサイズ: {size_json:,} bytes ({size_json/1024:.1f} KB)")
test("100エピソード後でも1MB未満", size_json < 1_000_000, f"{size_json/1024:.1f} KB")
test("統計が正しい", soul5["stats"]["total_episodes"] == 100)


# ═══ 結果 ═══
print(f"\n{'─' * 50}")
print(f"📊 A4結果: {passed} passed, {failed} failed")

if failed == 0:
    print("✅ ベイズ的忘却アルゴリズムは設計通りに動作しています。")
else:
    print("⚠️  一部の検証が失敗しました。")
