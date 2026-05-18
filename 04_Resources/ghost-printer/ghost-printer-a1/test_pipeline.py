#!/usr/bin/env python3
"""
Ghost-Printer A1 — パイプラインテスト

Ollamaなしでテスト可能な部分:
- SOULスキーマの生成・保存・読み込み
- ベイズ更新のロジック
- LLMレスポンスのパーサー
- エピソードの重み減衰
- SOUL更新フロー（モックdeltaで）
"""

import json
import math
import tempfile
import os
import sys

# テスト対象をインポート
from soul_schema import create_empty_soul, create_episode, load_soul, save_soul
from soul_engine import (
    bayesian_update_gaussian,
    update_soul,
    decay_episode_weights,
    soul_summary,
)
from extractor import _parse_llm_response

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


# ═══ 1. SOULスキーマ ═══
print("\n═══ 1. SOULスキーマ ═══")

soul = create_empty_soul("test_user")
test("空のSOUL生成", soul is not None)
test("バージョン存在", soul["version"] == "0.1.0")
test("core_identityに10次元", len(soul["core_identity"]) == 10)
test("各次元の初期μ=0.5", all(d["mu"] == 0.5 for d in soul["core_identity"].values()))
test("各次元の初期σ=0.30", all(d["sigma"] == 0.30 for d in soul["core_identity"].values()))
test("episodic_memory.recent は空リスト", soul["episodic_memory"]["recent"] == [])


# ═══ 2. 保存・読み込み ═══
print("\n═══ 2. 保存・読み込み ═══")

with tempfile.TemporaryDirectory() as tmpdir:
    path = os.path.join(tmpdir, "test_soul.json")
    save_soul(soul, path)
    test("ファイル保存", os.path.exists(path))

    loaded = load_soul(path)
    test("読み込み成功", loaded is not None)
    test("内容一致（version）", loaded["version"] == soul["version"])
    test("内容一致（core_identity）",
         loaded["core_identity"]["openness"]["mu"] == soul["core_identity"]["openness"]["mu"])

    # 存在しないパスの読み込み → 空のSOUL
    empty = load_soul(os.path.join(tmpdir, "nonexistent.json"))
    test("存在しないパス → 空のSOUL", empty["stats"]["total_episodes"] == 0)


# ═══ 3. ベイズ更新 ═══
print("\n═══ 3. ベイズ更新 ═══")

# ケース1: 高信頼度の観測
mu, sigma = bayesian_update_gaussian(0.5, 0.30, 0.8, 0.9)
test("高信頼観測でμが観測側に移動", mu > 0.5, f"μ={mu:.4f}")
test("高信頼観測でσが縮小", sigma < 0.30, f"σ={sigma:.4f}")

# ケース2: 低信頼度の観測
mu2, sigma2 = bayesian_update_gaussian(0.5, 0.30, 0.8, 0.1)
test("低信頼観測でμの移動が小さい", mu2 < mu, f"μ_low={mu2:.4f} vs μ_high={mu:.4f}")

# ケース3: σが小さい（確信が高い）事前分布は動きにくい
mu3, sigma3 = bayesian_update_gaussian(0.5, 0.05, 0.8, 0.5)
test("確信高い事前分布はμの移動が小さい", abs(mu3 - 0.5) < abs(mu - 0.5),
     f"μ_certain={mu3:.4f} vs μ_uncertain={mu:.4f}")

# ケース4: σの下限
_, sigma4 = bayesian_update_gaussian(0.5, 0.02, 0.5, 1.0)
test("σの下限が0.02", sigma4 >= 0.02, f"σ={sigma4:.4f}")

# ケース5: 連続更新でσが縮小していく
mu_seq, sigma_seq = 0.5, 0.30
for _ in range(20):
    mu_seq, sigma_seq = bayesian_update_gaussian(mu_seq, sigma_seq, 0.72, 0.6)
test("20回の一貫した観測でσが大幅縮小", sigma_seq < 0.10, f"σ={sigma_seq:.4f}")
test("20回の一貫した観測でμが0.72に近づく", abs(mu_seq - 0.72) < 0.05, f"μ={mu_seq:.4f}")


# ═══ 4. LLMレスポンスパーサー ═══
print("\n═══ 4. LLMレスポンスパーサー ═══")

# ケース1: 正常なJSONブロック
normal_response = '''```json
{
  "importance": 0.6,
  "emotion": {"name": "calm", "intensity": 0.4},
  "personality_signals": [
    {"dimension": "extraversion", "value": 0.3, "confidence": 0.7}
  ],
  "topics": ["コーヒー", "一人時間"],
  "values": ["内省"],
  "summary": "カフェで一人でコーヒーを飲んだ"
}
```'''
parsed = _parse_llm_response(normal_response)
test("正常JSON: importance", parsed["importance"] == 0.6)
test("正常JSON: emotion.name", parsed["emotion"]["name"] == "calm")
test("正常JSON: personality_signals数", len(parsed["personality_signals"]) == 1)
test("正常JSON: topics", "コーヒー" in parsed["topics"])

# ケース2: ```なしの裸のJSON
bare_json = '{"importance": 0.5, "emotion": {"name": "joy", "intensity": 0.7}, "personality_signals": [], "topics": [], "values": [], "summary": "test"}'
parsed2 = _parse_llm_response(bare_json)
test("裸のJSON: パース成功", parsed2["importance"] == 0.5)

# ケース3: 前後にテキストがあるJSON
messy_response = 'Here is the analysis:\n{"importance": 0.8, "emotion": {"name": "excitement", "intensity": 0.9}, "personality_signals": [{"dimension": "openness", "value": 0.9, "confidence": 0.8}], "topics": ["AI"], "values": ["innovation"], "summary": "AI project"}\nThank you!'
parsed3 = _parse_llm_response(messy_response)
test("前後テキスト付き: パース成功", parsed3["importance"] == 0.8)
test("前後テキスト付き: 不正な次元は除外", all(
    s["dimension"] in {"openness", "conscientiousness", "extraversion", "agreeableness",
                        "neuroticism", "curiosity", "creativity", "empathy",
                        "risk_tolerance", "independence"}
    for s in parsed3["personality_signals"]
))

# ケース4: 値のクランプ
clamped_response = '{"importance": 1.5, "emotion": {"name": "anger", "intensity": -0.3}, "personality_signals": [], "topics": [], "values": [], "summary": ""}'
parsed4 = _parse_llm_response(clamped_response)
test("クランプ: importance > 1 → 1.0", parsed4["importance"] == 1.0)
test("クランプ: intensity < 0 → 0.0", parsed4["emotion"]["intensity"] == 0.0)


# ═══ 5. SOUL更新フロー ═══
print("\n═══ 5. SOUL更新フロー（モックdelta） ═══")

soul = create_empty_soul("test_user")
mock_delta = {
    "importance": 0.6,
    "emotion": {"name": "curiosity", "intensity": 0.7},
    "personality_signals": [
        {"dimension": "openness", "value": 0.85, "confidence": 0.7},
        {"dimension": "curiosity", "value": 0.9, "confidence": 0.8},
    ],
    "topics": ["プログラミング", "AI"],
    "values": ["学習", "探究"],
    "summary": "新しいAIフレームワークを試した",
}

soul = update_soul(soul, mock_delta, raw_text="新しいAIフレームワークを試してみた。面白い。")
test("エピソード追加", len(soul["episodic_memory"]["recent"]) == 1)
test("total_episodes更新", soul["stats"]["total_episodes"] == 1)
test("openness.μ が0.5から上昇", soul["core_identity"]["openness"]["mu"] > 0.5)
test("curiosity.μ が0.5から上昇", soul["core_identity"]["curiosity"]["mu"] > 0.5)
test("interests に 'プログラミング'", "プログラミング" in soul["semantic_map"]["interests"])
test("values に '学習'", "学習" in soul["semantic_map"]["values"])

# 2回目の更新
mock_delta2 = {
    "importance": 0.4,
    "emotion": {"name": "calm", "intensity": 0.3},
    "personality_signals": [
        {"dimension": "extraversion", "value": 0.2, "confidence": 0.6},
    ],
    "topics": ["読書", "一人時間"],
    "values": ["内省"],
    "summary": "静かに本を読んだ",
}
soul = update_soul(soul, mock_delta2, raw_text="家で一人で本を読んだ。静かな時間。")
test("2回目: エピソード2件", len(soul["episodic_memory"]["recent"]) == 2)
test("2回目: extraversion.μ が0.5から低下", soul["core_identity"]["extraversion"]["mu"] < 0.5)

# ═══ 6. サマリー表示 ═══
print("\n═══ 6. サマリー表示 ═══")
summary = soul_summary(soul)
test("サマリー生成成功", len(summary) > 0)
test("サマリーにCore Identity含む", "Core Identity" in summary)
test("サマリーにRecent Emotions含む", "Recent Emotions" in summary)

print(f"\n{'─' * 40}")
print(f"📊 結果: {passed} passed, {failed} failed")

if failed > 0:
    print("\n⚠️  一部テストが失敗しました。")
    sys.exit(1)
else:
    print("\n✅ 全テスト成功！")
    print("\n── サマリー出力プレビュー ──\n")
    print(summary)
