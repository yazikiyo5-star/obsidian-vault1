"""
Ghost-Printer A1 — テキスト → SOUL Delta 抽出エンジン

Ollamaで動くローカルLLMに構造化プロンプトを送り、
テキストから性格シグナル・感情・重要度・トピックを抽出する。
"""

import json
import re
import httpx
from datetime import datetime

# Ollamaのデフォルトエンドポイント
OLLAMA_BASE_URL = "http://localhost:11434"

# 抽出用システムプロンプト
EXTRACTION_PROMPT = """\
あなたは人間の行動・発言・日記から、その人の性格特性と感情状態を抽出する専門家です。

以下の入力テキストを分析し、**必ず以下のJSON形式のみ**で回答してください。
説明文や前置きは一切不要です。JSONだけを出力してください。

```json
{
  "importance": 0.0〜1.0の数値（この出来事がその人の人生でどれだけ重要か）,
  "emotion": {
    "name": "感情名（joy, sadness, anxiety, calm, excitement, anger, gratitude, loneliness, pride, curiosity など）",
    "intensity": 0.0〜1.0の数値
  },
  "personality_signals": [
    {
      "dimension": "Big5の次元名またはcuriosity/creativity/empathy/risk_tolerance/independence",
      "value": 0.0〜1.0の数値（0=低い、1=高い）,
      "confidence": 0.0〜1.0（この推定にどれだけ自信があるか）
    }
  ],
  "topics": ["関連トピック1", "関連トピック2"],
  "values": ["この行動から読み取れる価値観"],
  "summary": "1文の要約"
}
```

## 判定基準

**importance（重要度）:**
- 0.1-0.3: 日常的な行動（食事、移動、ルーティン）
- 0.4-0.6: 感情を伴う体験、他者との有意義な交流
- 0.7-0.8: 人生の方向性に関わる決断、強い感情体験
- 0.9-1.0: 人生を変えるイベント（転職、引越し、喪失）

**personality_signals:**
- openness: 新しい体験への開放性（芸術、知的好奇心、冒険）
- conscientiousness: 計画性、規律、目標志向
- extraversion: 社交性、活動性、外向き（0に近い=内向的）
- agreeableness: 協調性、思いやり、信頼
- neuroticism: 情緒不安定性、不安、ストレス反応
- curiosity: 知的好奇心、探究心
- creativity: 創造性、独自の発想
- empathy: 共感力、他者理解
- risk_tolerance: リスクへの許容度
- independence: 自立性、自分の判断への信頼

**confidence:**
- テキストから明確に読み取れる → 0.7-1.0
- 間接的に推測できる → 0.4-0.6
- 弱い手がかりしかない → 0.1-0.3
"""


def extract_soul_delta(
    text: str,
    context: dict | None = None,
    model: str = "gemma3:4b",
    base_url: str = OLLAMA_BASE_URL,
) -> dict:
    """
    テキストからSOUL deltaを抽出する。

    Args:
        text: 入力テキスト（日記、メモなど）
        context: 追加コンテキスト（location, time_of_day など）
        model: 使用するOllamaモデル
        base_url: Ollama APIのベースURL

    Returns:
        抽出されたdelta辞書
    """
    # コンテキスト情報を組み立てる
    context_str = ""
    if context:
        parts = []
        if "location" in context:
            parts.append(f"場所: {context['location']}")
        if "time_of_day" in context:
            parts.append(f"時間帯: {context['time_of_day']}")
        if "date" in context:
            parts.append(f"日付: {context['date']}")
        if parts:
            context_str = f"\n\nコンテキスト情報:\n" + "\n".join(parts)

    user_message = f"/no_think\n入力テキスト:\n{text}{context_str}"

    # Ollama API呼び出し
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "options": {
            "temperature": 0.3,    # 安定した出力のために低め
            "num_predict": 1024,
        },
    }

    try:
        resp = httpx.post(
            f"{base_url}/api/chat",
            json=payload,
            timeout=120.0,
        )
        resp.raise_for_status()
        raw = resp.json()["message"]["content"]
        return _parse_llm_response(raw)

    except httpx.ConnectError:
        raise ConnectionError(
            "Ollamaに接続できません。`ollama serve` が起動しているか確認してください。"
        )
    except Exception as e:
        raise RuntimeError(f"抽出エラー: {e}")


def _parse_llm_response(raw: str) -> dict:
    """LLMのレスポンスからJSONを抽出してバリデーションする"""
    # qwen3の<think>...</think>タグを除去
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    # 閉じタグがない場合（途中で切れた場合）も除去
    raw = re.sub(r"<think>.*", "", raw, flags=re.DOTALL).strip()
    # ```json ... ``` ブロックを探す
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
    else:
        # JSONブロックがなければ全体をパースしてみる
        # 先頭/末尾の非JSON文字を除去
        json_str = raw.strip()
        # { ... } を探す
        brace_match = re.search(r"\{.*\}", json_str, re.DOTALL)
        if brace_match:
            json_str = brace_match.group(0)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLMの出力をJSONとしてパースできません:\n{raw}\n\nError: {e}")

    # バリデーション & デフォルト値
    return {
        "importance": _clamp(data.get("importance", 0.3)),
        "emotion": {
            "name": data.get("emotion", {}).get("name", "neutral"),
            "intensity": _clamp(data.get("emotion", {}).get("intensity", 0.3)),
        },
        "personality_signals": [
            {
                "dimension": sig.get("dimension", "openness"),
                "value": _clamp(sig.get("value", 0.5)),
                "confidence": _clamp(sig.get("confidence", 0.3)),
            }
            for sig in data.get("personality_signals", [])
            if sig.get("dimension") in VALID_DIMENSIONS
        ],
        "topics": data.get("topics", []),
        "values": data.get("values", []),
        "summary": data.get("summary", ""),
    }


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return 0.5


VALID_DIMENSIONS = {
    "openness", "conscientiousness", "extraversion",
    "agreeableness", "neuroticism",
    "curiosity", "creativity", "empathy",
    "risk_tolerance", "independence",
}


def check_ollama_connection(base_url: str = OLLAMA_BASE_URL) -> dict:
    """Ollamaの接続状態とモデル一覧を確認する"""
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        return {"connected": True, "models": models}
    except Exception:
        return {"connected": False, "models": []}
