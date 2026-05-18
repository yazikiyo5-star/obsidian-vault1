#!/usr/bin/env python3
"""
Ghost-Printer A5 — Claude連携体験検証

同じ質問を「SOULなし（初対面）」と「SOULあり」の2パターンでClaudeに送り、
応答の違いを比較する。Ollama上のqwen3:14bを使用。

比較する質問:
1. 汎用的なアドバイス質問
2. 休日の過ごし方の提案
3. キャリアの悩み相談
"""

import json
import time
import httpx
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"

TEST_QUESTIONS = [
    {
        "id": "advice",
        "question": "最近ちょっと疲れ気味です。なにかアドバイスはありますか？",
        "evaluate": "SOULありでは、一人の時間やカフェでの読書など、この人に合ったリフレッシュ方法を提案するか",
    },
    {
        "id": "weekend",
        "question": "今度の週末、何しようかな。おすすめある？",
        "evaluate": "SOULありでは、知的好奇心やハードウェア/AI開発の趣味を踏まえた提案をするか",
    },
    {
        "id": "career",
        "question": "新しいプロジェクトを始めたいんだけど、周りに理解されなさそうで迷ってる。",
        "evaluate": "SOULありでは、独立心が高くリスク許容度が高い性格を踏まえて背中を押すような助言をするか",
    },
]


def ask_llm(system_prompt: str, user_message: str, model: str = "qwen3:14b") -> str:
    """Ollamaにリクエストを送る"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"/no_think\n{user_message}"},
        ],
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 512},
    }
    resp = httpx.post(OLLAMA_URL, json=payload, timeout=120.0)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def run_comparison(model: str = "qwen3:14b"):
    print("╔══════════════════════════════════════════════════════╗")
    print("║  Ghost-Printer A5 — SOUL → Claude 体験検証          ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    # System Prompt読み込み
    soul_prompt_path = Path("data/system_prompt.txt")
    if not soul_prompt_path.exists():
        print("❌ data/system_prompt.txt が見つかりません。先にsoul_to_prompt.pyを実行してください。")
        return
    soul_prompt = soul_prompt_path.read_text(encoding="utf-8")

    generic_prompt = "あなたは親切なアシスタントです。ユーザーの質問に丁寧に答えてください。"

    results = []

    for i, q in enumerate(TEST_QUESTIONS):
        print(f"{'═' * 60}")
        print(f"質問 {i+1}: {q['question']}")
        print(f"評価観点: {q['evaluate']}")
        print(f"{'═' * 60}")

        # (A) SOULなし
        print(f"\n── (A) 初対面のAI（SOULなし） ──")
        t0 = time.time()
        try:
            response_a = ask_llm(generic_prompt, q["question"], model=model)
            time_a = time.time() - t0
            print(f"  ⏱ {time_a:.1f}s")
            print(f"  {response_a[:500]}")
        except Exception as e:
            print(f"  ❌ エラー: {e}")
            response_a = f"ERROR: {e}"
            time_a = 0

        # (B) SOULあり
        print(f"\n── (B) SOULを知っているAI ──")
        t0 = time.time()
        try:
            response_b = ask_llm(soul_prompt, q["question"], model=model)
            time_b = time.time() - t0
            print(f"  ⏱ {time_b:.1f}s")
            print(f"  {response_b[:500]}")
        except Exception as e:
            print(f"  ❌ エラー: {e}")
            response_b = f"ERROR: {e}"
            time_b = 0

        results.append({
            "question": q["question"],
            "evaluate_criteria": q["evaluate"],
            "response_without_soul": response_a,
            "response_with_soul": response_b,
        })
        print()

    # 結果保存
    report_path = "data/a5_claude_comparison.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n📄 詳細レポート: {report_path}")
    print("\n✅ A5体験検証完了。上記の応答を比較して、SOULの効果を評価してください。")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:14b")
    args = parser.parse_args()
    run_comparison(model=args.model)
