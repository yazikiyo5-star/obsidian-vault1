#!/usr/bin/env python3
"""
Ghost-Printer A2 — 抽出精度の検証

多様なテキストに対して「期待される性格シグナル」を定義し、
LLMの抽出結果が期待と一致するかを自動評価する。

評価基準:
- 方向一致: 期待が「高い(>0.6)」なら抽出値も>0.5、「低い(<0.4)」なら<0.5
- 感情一致: 期待する感情カテゴリと一致するか
- 重要度範囲: 期待する重要度レンジに収まるか
"""

import json
import sys
import time
from datetime import datetime
from extractor import extract_soul_delta, check_ollama_connection

# ═══ テストケース定義 ═══
# 各ケースに「期待される方向」を定義
# dimension_expectations: {"dimension": "high" or "low"} — 抽出値が0.5より高いか低いか
# emotion_expected: 期待する感情カテゴリのリスト（いずれか一致でOK）
# importance_range: (min, max)

TEST_CASES = [
    {
        "name": "内向的な一人時間",
        "text": "カフェで一人でゆっくりコーヒーを飲みながら本を読んだ。周りに人はいたけど、自分の世界に没頭できた。",
        "dimension_expectations": {
            "extraversion": "low",
            "openness": "high",
            "independence": "high",
        },
        "emotion_expected": ["calm", "contentment", "peace", "relaxation", "serenity"],
        "importance_range": (0.1, 0.5),
    },
    {
        "name": "チームでの成功体験",
        "text": "プレゼンが大成功だった。チームのみんなから拍手をもらい、上司にも褒められた。懇親会でも積極的に話した。",
        "dimension_expectations": {
            "extraversion": "high",
            "agreeableness": "high",
            "neuroticism": "low",
        },
        "emotion_expected": ["joy", "pride", "excitement", "happiness", "satisfaction"],
        "importance_range": (0.5, 0.8),
    },
    {
        "name": "深夜の創作活動",
        "text": "気づいたら深夜3時。新しいプロトタイプのコードを書き続けていた。誰にも頼まれていないけど、このアイデアは絶対にうまくいく。",
        "dimension_expectations": {
            "openness": "high",
            "creativity": "high",
            "independence": "high",
            "risk_tolerance": "high",
        },
        "emotion_expected": ["excitement", "passion", "flow", "curiosity", "determination"],
        "importance_range": (0.5, 0.9),
    },
    {
        "name": "不安と締め切りのストレス",
        "text": "締め切りが明日なのに全然終わっていない。胃が痛い。なんで早く始めなかったんだろう。みんなに迷惑をかけてしまう。",
        "dimension_expectations": {
            "neuroticism": "high",
            "conscientiousness": "low",
        },
        "emotion_expected": ["anxiety", "stress", "guilt", "worry", "panic", "fear"],
        "importance_range": (0.5, 0.8),
    },
    {
        "name": "他者への共感",
        "text": "友人が失恋したと連絡してきた。すぐに電話して2時間話を聞いた。自分のことのように辛くなったけど、少しでも支えになれたならよかった。",
        "dimension_expectations": {
            "empathy": "high",
            "agreeableness": "high",
            "extraversion": "high",
        },
        "emotion_expected": ["sadness", "empathy", "compassion", "concern", "gratitude"],
        "importance_range": (0.4, 0.7),
    },
    {
        "name": "ルーティンの朝",
        "text": "いつも通り7時に起きて、コーヒーを淹れて、ニュースを読んだ。特に何もない普通の朝。",
        "dimension_expectations": {
            "conscientiousness": "high",
        },
        "emotion_expected": ["calm", "neutral", "contentment", "peace"],
        "importance_range": (0.0, 0.3),
    },
    {
        "name": "冒険的な旅行",
        "text": "初めてのバンジージャンプ。怖くて足が震えたけど飛んだ。叫び声をあげながら落ちていく感覚は人生で一番の体験だった。",
        "dimension_expectations": {
            "openness": "high",
            "risk_tolerance": "high",
            "neuroticism": "low",
        },
        "emotion_expected": ["excitement", "fear", "thrill", "joy", "exhilaration"],
        "importance_range": (0.6, 0.9),
    },
    {
        "name": "知的好奇心",
        "text": "量子コンピュータの論文を3本読んだ。まだ理解できない部分も多いけど、この分野の可能性に興奮している。週末もっと調べたい。",
        "dimension_expectations": {
            "curiosity": "high",
            "openness": "high",
            "conscientiousness": "high",
        },
        "emotion_expected": ["curiosity", "excitement", "fascination", "interest"],
        "importance_range": (0.3, 0.7),
    },
    {
        "name": "対立と自己主張",
        "text": "会議で上司の方針に反対した。根拠を示して自分の意見を貫いた。気まずい空気になったけど、正しいことを言うべきだと思った。",
        "dimension_expectations": {
            "independence": "high",
            "agreeableness": "low",
            "risk_tolerance": "high",
        },
        "emotion_expected": ["determination", "anxiety", "pride", "tension", "courage", "anger"],
        "importance_range": (0.5, 0.8),
    },
    {
        "name": "人生を変える決断",
        "text": "会社を辞めて起業することを決めた。妻にも話した。貯金は1年分しかないけど、このまま後悔する人生は嫌だ。怖いけど、ワクワクしている。",
        "dimension_expectations": {
            "risk_tolerance": "high",
            "openness": "high",
            "independence": "high",
        },
        "emotion_expected": ["excitement", "fear", "determination", "anxiety", "hope"],
        "importance_range": (0.8, 1.0),
    },
]


def evaluate_case(case: dict, result: dict) -> dict:
    """1つのテストケースを評価する"""
    scores = {
        "name": case["name"],
        "dimension_checks": [],
        "emotion_match": False,
        "importance_in_range": False,
        "details": {},
    }

    # 1. 性格次元の方向チェック
    extracted_dims = {s["dimension"]: s["value"] for s in result.get("personality_signals", [])}
    for dim, expected_dir in case["dimension_expectations"].items():
        actual = extracted_dims.get(dim)
        if actual is None:
            scores["dimension_checks"].append({"dim": dim, "pass": False, "reason": "未抽出"})
        elif expected_dir == "high" and actual > 0.5:
            scores["dimension_checks"].append({"dim": dim, "pass": True, "actual": actual})
        elif expected_dir == "low" and actual < 0.5:
            scores["dimension_checks"].append({"dim": dim, "pass": True, "actual": actual})
        else:
            scores["dimension_checks"].append({
                "dim": dim, "pass": False,
                "reason": f"期待={expected_dir}, 実際={actual:.2f}"
            })

    # 2. 感情カテゴリチェック
    detected_emotion = result.get("emotion", {}).get("name", "").lower()
    expected_emotions = [e.lower() for e in case["emotion_expected"]]
    scores["emotion_match"] = detected_emotion in expected_emotions
    scores["details"]["detected_emotion"] = detected_emotion
    scores["details"]["expected_emotions"] = expected_emotions

    # 3. 重要度レンジチェック
    importance = result.get("importance", 0)
    lo, hi = case["importance_range"]
    scores["importance_in_range"] = lo <= importance <= hi
    scores["details"]["importance"] = importance
    scores["details"]["importance_range"] = case["importance_range"]

    return scores


def run_accuracy_test(model: str = "qwen3:14b"):
    """全テストケースを実行して精度を評価する"""
    print("╔══════════════════════════════════════════════╗")
    print("║  Ghost-Printer A2 — 抽出精度テスト           ║")
    print(f"║  モデル: {model:<36s} ║")
    print(f"║  テストケース: {len(TEST_CASES)}件{' ' * 28}║")
    print("╚══════════════════════════════════════════════╝")
    print()

    # 接続確認
    status = check_ollama_connection()
    if not status["connected"]:
        print("❌ Ollamaに接続できません")
        sys.exit(1)

    results = []
    total_dim_checks = 0
    passed_dim_checks = 0
    emotion_matches = 0
    importance_matches = 0

    for i, case in enumerate(TEST_CASES):
        print(f"[{i+1}/{len(TEST_CASES)}] {case['name']}...")
        t0 = time.time()

        try:
            delta = extract_soul_delta(case["text"], model=model)
            elapsed = time.time() - t0
            scores = evaluate_case(case, delta)

            # 集計
            for check in scores["dimension_checks"]:
                total_dim_checks += 1
                if check["pass"]:
                    passed_dim_checks += 1
            if scores["emotion_match"]:
                emotion_matches += 1
            if scores["importance_in_range"]:
                importance_matches += 1

            # 表示
            dim_pass = sum(1 for c in scores["dimension_checks"] if c["pass"])
            dim_total = len(scores["dimension_checks"])
            em_icon = "✅" if scores["emotion_match"] else "❌"
            imp_icon = "✅" if scores["importance_in_range"] else "❌"

            print(f"  ⏱  {elapsed:.1f}s")
            print(f"  🧠 性格次元: {dim_pass}/{dim_total}")
            for check in scores["dimension_checks"]:
                icon = "✅" if check["pass"] else "❌"
                if check["pass"]:
                    print(f"     {icon} {check['dim']} = {check['actual']:.2f}")
                else:
                    print(f"     {icon} {check['dim']}: {check['reason']}")
            print(f"  {em_icon} 感情: {scores['details']['detected_emotion']} (期待: {', '.join(scores['details']['expected_emotions'][:3])}...)")
            print(f"  {imp_icon} 重要度: {scores['details']['importance']:.2f} (期待: {scores['details']['importance_range']})")
            print()

            results.append({"case": case["name"], "scores": scores, "elapsed": elapsed})

        except Exception as e:
            print(f"  ❌ エラー: {e}\n")
            results.append({"case": case["name"], "error": str(e)})

    # ═══ 総合レポート ═══
    print("═" * 50)
    print("📊 総合結果")
    print("═" * 50)
    n = len(TEST_CASES)
    errors = sum(1 for r in results if "error" in r)
    dim_pct = (passed_dim_checks / total_dim_checks * 100) if total_dim_checks > 0 else 0
    em_pct = emotion_matches / (n - errors) * 100 if (n - errors) > 0 else 0
    imp_pct = importance_matches / (n - errors) * 100 if (n - errors) > 0 else 0

    print(f"  性格次元の方向一致率: {passed_dim_checks}/{total_dim_checks} ({dim_pct:.0f}%)")
    print(f"  感情カテゴリ一致率:   {emotion_matches}/{n - errors} ({em_pct:.0f}%)")
    print(f"  重要度レンジ一致率:   {importance_matches}/{n - errors} ({imp_pct:.0f}%)")
    if errors:
        print(f"  エラー:               {errors}/{n}")

    avg_time = sum(r.get("elapsed", 0) for r in results) / max(len(results), 1)
    print(f"  平均処理時間:         {avg_time:.1f}s")
    print()

    # 精度判定
    overall = (dim_pct + em_pct + imp_pct) / 3
    if overall >= 80:
        print(f"  🎯 総合精度: {overall:.0f}% — 良好。A3に進めます。")
    elif overall >= 60:
        print(f"  ⚠️  総合精度: {overall:.0f}% — プロンプト改善の余地あり。")
    else:
        print(f"  ❌ 総合精度: {overall:.0f}% — プロンプトの大幅な見直しが必要。")

    # 結果をJSONで保存
    report = {
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "summary": {
            "dimension_accuracy": f"{dim_pct:.0f}%",
            "emotion_accuracy": f"{em_pct:.0f}%",
            "importance_accuracy": f"{imp_pct:.0f}%",
            "overall": f"{overall:.0f}%",
            "avg_time_sec": round(avg_time, 1),
        },
        "results": results,
    }
    report_path = "data/a2_accuracy_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  📄 詳細レポート: {report_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:14b")
    args = parser.parse_args()
    run_accuracy_test(model=args.model)
