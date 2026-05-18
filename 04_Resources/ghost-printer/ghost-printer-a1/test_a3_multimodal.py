#!/usr/bin/env python3
"""
Ghost-Printer A3 — 多元入力統合実験

同じテキストに対して:
  (a) テキストのみ
  (b) テキスト + 位置 + 時刻のコンテキスト付き
で抽出結果を比較し、多元データが精度に与える影響を測定する。
"""

import json
import time
import sys
from extractor import extract_soul_delta, check_ollama_connection

# ═══ テストケース: 同じテキストにコンテキストの有無で比較 ═══
TEST_CASES = [
    {
        "name": "曖昧なテキスト — コンテキストで意味が変わる",
        "text": "今日は一人で静かに過ごした。",
        "context": {"location": "自宅", "time_of_day": "夜", "date": "2026-04-15"},
        "note": "自宅×夜→リラックス/内省 vs テキストだけだと孤独にも解釈可能",
    },
    {
        "name": "活動的なテキスト — 場所で印象が変わる",
        "text": "3時間ぶっ通しで集中して作業した。すごく捗った。",
        "context": {"location": "コワーキングスペース", "time_of_day": "朝", "date": "2026-04-15"},
        "note": "コワーキング×朝→conscientiousnessが強調されるか",
    },
    {
        "name": "感情的なテキスト — 時間帯で重みが変わる",
        "text": "将来のことを考えて不安になった。",
        "context": {"location": "自宅ベッド", "time_of_day": "深夜3時", "date": "2026-04-15"},
        "note": "深夜×ベッド→neuroticismがより強く出るか",
    },
    {
        "name": "社交的なテキスト — 場所で性質が変わる",
        "text": "大勢の人と話して楽しかった。新しい出会いもあった。",
        "context": {"location": "技術カンファレンス", "time_of_day": "昼", "date": "2026-04-15"},
        "note": "カンファレンス→openness/curiosityも加わるか",
    },
    {
        "name": "日常テキスト — コンテキストで重要度が変わる",
        "text": "おいしいご飯を食べた。",
        "context": {"location": "ミシュラン三つ星レストラン", "time_of_day": "夕方", "date": "2026-04-15"},
        "note": "ミシュラン→importanceが上がるか。テキストだけだと低重要度",
    },
]


def run_comparison(model: str = "qwen3:14b"):
    print("╔══════════════════════════════════════════════╗")
    print("║  Ghost-Printer A3 — 多元入力統合実験         ║")
    print(f"║  モデル: {model:<36s} ║")
    print("╚══════════════════════════════════════════════╝")
    print()

    status = check_ollama_connection()
    if not status["connected"]:
        print("❌ Ollamaに接続できません")
        sys.exit(1)

    results = []
    improvements = {"importance": 0, "emotion": 0, "personality": 0}
    total = len(TEST_CASES)

    for i, case in enumerate(TEST_CASES):
        print(f"[{i+1}/{total}] {case['name']}")
        print(f"  テキスト: {case['text']}")
        print(f"  期待: {case['note']}")

        # (a) テキストのみ
        print(f"  ── (a) テキストのみ ──")
        t0 = time.time()
        try:
            result_a = extract_soul_delta(case["text"], context=None, model=model)
            time_a = time.time() - t0
            _print_result(result_a, time_a)
        except Exception as e:
            print(f"  ❌ エラー: {e}")
            result_a = None
            time_a = 0

        # (b) テキスト + コンテキスト
        print(f"  ── (b) テキスト + コンテキスト ──")
        print(f"       {case['context']}")
        t0 = time.time()
        try:
            result_b = extract_soul_delta(case["text"], context=case["context"], model=model)
            time_b = time.time() - t0
            _print_result(result_b, time_b)
        except Exception as e:
            print(f"  ❌ エラー: {e}")
            result_b = None
            time_b = 0

        # 差分分析
        if result_a and result_b:
            diff = _analyze_diff(result_a, result_b)
            print(f"  ── 差分 ──")
            print(f"  重要度変化: {diff['importance_delta']:+.2f}")
            print(f"  感情変化: {diff['emotion_change']}")
            print(f"  性格シグナル変化: {diff['personality_changes']}")
            if abs(diff["importance_delta"]) > 0.1:
                improvements["importance"] += 1
            if diff["emotion_change"] != "同一":
                improvements["emotion"] += 1
            if diff["personality_changes"]:
                improvements["personality"] += 1

        results.append({
            "case": case["name"],
            "text_only": result_a,
            "with_context": result_b,
            "context": case["context"],
        })
        print()

    # 総合レポート
    print("═" * 50)
    print("📊 A3 総合結果")
    print("═" * 50)
    print(f"  重要度が変わったケース:     {improvements['importance']}/{total}")
    print(f"  感情が変わったケース:       {improvements['emotion']}/{total}")
    print(f"  性格シグナルが変わったケース: {improvements['personality']}/{total}")
    total_changes = sum(improvements.values())
    print(f"  → コンテキストの影響度: {total_changes}/{total * 3} ({total_changes / (total * 3) * 100:.0f}%)")

    # 保存
    report_path = "data/a3_multimodal_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"results": results, "improvements": improvements}, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  📄 詳細レポート: {report_path}")


def _print_result(result: dict, elapsed: float):
    em = result["emotion"]
    sigs = result.get("personality_signals", [])[:3]
    sig_strs = [f"{s['dimension']}={s['value']:.2f}" for s in sigs]
    print(f"    ⏱ {elapsed:.1f}s | 重要度={result['importance']:.2f} | 感情={em['name']}({em['intensity']:.1f})")
    if sig_strs:
        print(f"    🧠 {', '.join(sig_strs)}")


def _analyze_diff(a: dict, b: dict) -> dict:
    imp_delta = b["importance"] - a["importance"]
    em_a = a["emotion"]["name"]
    em_b = b["emotion"]["name"]
    emotion_change = "同一" if em_a == em_b else f"{em_a} → {em_b}"

    dims_a = {s["dimension"]: s["value"] for s in a.get("personality_signals", [])}
    dims_b = {s["dimension"]: s["value"] for s in b.get("personality_signals", [])}
    all_dims = set(dims_a.keys()) | set(dims_b.keys())
    changes = []
    for dim in all_dims:
        va = dims_a.get(dim, 0.5)
        vb = dims_b.get(dim, 0.5)
        if abs(vb - va) > 0.1:
            changes.append(f"{dim}: {va:.2f}→{vb:.2f}")

    return {
        "importance_delta": imp_delta,
        "emotion_change": emotion_change,
        "personality_changes": changes,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:14b")
    args = parser.parse_args()
    run_comparison(model=args.model)
