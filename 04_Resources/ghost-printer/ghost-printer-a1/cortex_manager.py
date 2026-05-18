"""
Ghost-Printer — CORTEX Manager

Core基板のフラッシュメモリに焼くLLM指示ファイル(CORTEX)の
ビルド・読み込み・検証・バージョン管理を行う。

CORTEXとは:
  デバイスに物理的に埋め込まれた「脳の動かし方」の定義。
  SOULが「この人の人格」なら、CORTEXは「この脳の回路」。
  各LLMモデルが何をどう処理するかのルールが入っている。

構造:
  CORTEX.bin = MessagePack圧縮されたバイナリ
  ├── header          バージョン・チェックサム・作成日時
  ├── whisper_config   Whisper(音響層)の設定
  ├── bonsai_config    Bonsai(意味層)の設定 ← 抽出プロンプト・次元定義
  ├── minilm_config    MiniLM(埋込層)の設定
  ├── cortex_config    Soul Cortex全体の設定（情報交換パラメータ）
  └── meta             メタ情報（作成者・説明・変更履歴）

更新フロー:
  1. cortex_manager.py でCORTEXをビルド
  2. CORTEX.bin をCore基板のフラッシュに書き込み
  3. 起動時にCORTEXを読み込み、各モデルに設定を渡す
  4. 試行錯誤でパラメータを変え、再ビルド → 再書き込み
"""

import json
import hashlib
import struct
import gzip
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any


# ════════════════════════════════════════════════════════════════════════════════
# 1. CORTEX データ構造
# ════════════════════════════════════════════════════════════════════════════════

# -- CORTEX ヘッダー (固定) --
CORTEX_MAGIC = b"GPCX"        # Ghost-Printer CorteX
CORTEX_FORMAT_VERSION = 2      # フォーマットバージョン


@dataclass
class WhisperConfig:
    """Whisper（音響層）の設定"""
    model_file: str = "whisper-tiny.gguf"
    # VAD (Voice Activity Detection) 設定
    vad_threshold: float = 0.5       # 音声検出しきい値
    vad_min_duration: float = 1.0    # 最小発話区間（秒）
    vad_max_duration: float = 300.0  # 最大録音時間（秒）
    # 音響特徴抽出
    extract_pitch: bool = True
    extract_energy: bool = True
    extract_tempo: bool = True
    # Valence-Arousal推定パラメータ
    valence_pitch_weight: float = 0.3
    valence_tempo_weight: float = 0.2
    valence_energy_weight: float = 0.2
    valence_pause_weight: float = 0.3
    arousal_tempo_weight: float = 0.3
    arousal_energy_var_weight: float = 0.3
    arousal_pitch_var_weight: float = 0.2


@dataclass
class PersonalityDimension:
    """性格次元の定義"""
    name: str                    # 次元名 (例: "openness")
    description: str             # LLMへの説明
    low_label: str               # 低スコア時の意味
    high_label: str              # 高スコア時の意味
    initial_mu: float = 0.5      # 初期μ
    initial_sigma: float = 0.30  # 初期σ
    sigma_floor: float = 0.02    # σの下限


# デフォルト10次元
DEFAULT_DIMENSIONS = [
    PersonalityDimension("openness", "新しい体験への開放性（芸術、知的好奇心、冒険）",
                         "慣れ親しんだ方法を好む", "新しいアイデアに強い関心"),
    PersonalityDimension("conscientiousness", "計画性、規律、目標志向",
                         "柔軟で即興的", "計画的で目標志向"),
    PersonalityDimension("extraversion", "社交性、活動性、外向き（0に近い=内向的）",
                         "内省的で一人の時間を大切にする", "社交的で活動的"),
    PersonalityDimension("agreeableness", "協調性、思いやり、信頼",
                         "自分の意見を率直に言う", "協調的で思いやりがある"),
    PersonalityDimension("neuroticism", "情緒不安定性、不安、ストレス反応",
                         "情緒が安定している", "感情の起伏がある"),
    PersonalityDimension("curiosity", "知的好奇心、探究心",
                         "必要な情報を効率的に得る", "知的探究心が非常に強い"),
    PersonalityDimension("creativity", "創造性、独自の発想",
                         "実証済みの方法を信頼する", "独自の発想力がある"),
    PersonalityDimension("empathy", "共感力、他者理解",
                         "論理的・客観的なアプローチを好む", "他者の感情を深く理解する"),
    PersonalityDimension("risk_tolerance", "リスクへの許容度",
                         "慎重にリスクを評価する", "リスクを恐れず前に進める"),
    PersonalityDimension("independence", "自立性、自分の判断への信頼",
                         "チームワークを重視する", "自分の判断を信頼する"),
]


@dataclass
class BonsaiConfig:
    """Bonsai（意味層）の設定 — LLMへの指示の中核"""
    model_file: str = "bonsai-1.7b.gguf"
    # 推論パラメータ
    temperature: float = 0.3
    max_tokens: int = 1024
    # 性格次元の定義
    dimensions: List[PersonalityDimension] = field(
        default_factory=lambda: list(DEFAULT_DIMENSIONS)
    )
    # 抽出プロンプト（LLMへの指示そのもの）
    system_prompt: str = ""
    # 重要度の判定基準
    importance_scale: Dict[str, str] = field(default_factory=lambda: {
        "0.1-0.3": "日常的な行動（食事、移動、ルーティン）",
        "0.4-0.6": "感情を伴う体験、他者との有意義な交流",
        "0.7-0.8": "人生の方向性に関わる決断、強い感情体験",
        "0.9-1.0": "人生を変えるイベント（転職、引越し、喪失）",
    })
    # 感情カテゴリ
    emotion_categories: List[str] = field(default_factory=lambda: [
        "joy", "sadness", "anxiety", "calm", "excitement",
        "anger", "gratitude", "loneliness", "pride", "curiosity",
        "contentment", "frustration", "hope", "fear", "neutral",
    ])
    # 確信度の基準
    confidence_scale: Dict[str, str] = field(default_factory=lambda: {
        "0.7-1.0": "テキストから明確に読み取れる",
        "0.4-0.6": "間接的に推測できる",
        "0.1-0.3": "弱い手がかりしかない",
    })
    # 出力フォーマット定義
    output_schema: Dict[str, Any] = field(default_factory=lambda: {
        "importance": "float 0.0-1.0",
        "emotion": {"name": "str", "intensity": "float 0.0-1.0"},
        "personality_signals": [{"dimension": "str", "value": "float", "confidence": "float"}],
        "topics": ["str"],
        "values": ["str"],
        "summary": "str (1文)",
    })


@dataclass
class MiniLMConfig:
    """MiniLM（埋込層）の設定"""
    model_file: str = "minilm-l6-v2.onnx"
    vector_dim: int = 512 # 384 for actual MiniLM-L6-v2, 512 for design target
    # クラスタリング設定
    similarity_threshold: float = 0.7    # 類似エピソードとみなす閾値
    max_clusters: int = 20               # 最大クラスタ数
    top_k_similar: int = 5               # 類似検索で返す件数


@dataclass
class CortexConfig:
    """Soul Cortex全体の設定（情報交換パラメータ）"""
    # Whisper→Bonsai: 音響ブーストの重み
    acoustic_boost_weight: float = 0.2
    acoustic_mismatch_penalty: float = 0.15
    acoustic_confidence_threshold: float = 0.3
    # Bonsai→MiniLM: 重要度の伝搬
    importance_propagation: bool = True
    # MiniLM→Bonsai: コンテキストブーストの重み
    context_boost_weight: float = 0.1
    context_similarity_threshold: float = 0.7
    # ベイズ更新
    bayesian_sigma_floor: float = 0.02
    obs_sigma_base: float = 0.05
    obs_sigma_range: float = 0.5
    # エピソード管理
    decay_half_life_days: int = 30
    max_recent_episodes: int = 100
    distillation_threshold: float = 0.05


@dataclass
class WatchPointConfig:
    """Watch Point 生態系の設定（観測ポイントの生成・淘汰パラメータ）"""
    # 管理キャパ
    max_active: int = 20                      # 同時アクティブWP上限
    probation_trials: int = 3                 # 試用期間の観測回数
    min_hits_to_graduate: int = 1             # 試用期間中の最低ヒット数
    # 淘汰圧
    fitness_floor: float = 0.05               # これ未満で淘汰対象
    decay_half_life_days: float = 14.0        # recency半減期
    merge_similarity_threshold: float = 0.85  # targetの統合閾値 (Jaccard)
    observation_window: int = 20              # 観測履歴のサイズ
    # 生成ルール閾値（WatchPointRules）
    topic_concentration_threshold: float = 0.6   # トピック偏りトリガー
    emotion_drift_threshold: float = 0.3         # 感情ドリフトトリガー
    importance_spike_count: int = 3              # 高重要度集中件数
    importance_spike_threshold: float = 0.7      # 高重要度の下限
    personality_sigma_spike: float = 0.1         # σ急拡大トリガー
    # ヒット判定
    hit_info_gain_threshold: float = 0.01     # info_gain これ未満はヒット扱いしない


@dataclass
class CortexMeta:
    """メタ情報"""
    created_at: str = ""
    updated_at: str = ""
    version: str = "1.0.0"
    description: str = ""
    changelog: List[Dict[str, str]] = field(default_factory=list)
    # チェックサム（ビルド時に自動生成）
    checksum: str = ""


@dataclass
class Cortex:
    """CORTEX全体"""
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    bonsai: BonsaiConfig = field(default_factory=BonsaiConfig)
    minilm: MiniLMConfig = field(default_factory=MiniLMConfig)
    cortex: CortexConfig = field(default_factory=CortexConfig)
    watchpoint: WatchPointConfig = field(default_factory=WatchPointConfig)
    meta: CortexMeta = field(default_factory=CortexMeta)


# ════════════════════════════════════════════════════════════════════════════════
# 2. System Prompt ビルダー（BonsaiConfigから自動生成）
# ════════════════════════════════════════════════════════════════════════════════

def build_system_prompt(config: BonsaiConfig) -> str:
    """BonsaiConfigからLLM用System Promptを自動生成する"""
    lines = []

    lines.append("あなたは人間の行動・発言・日記から、その人の性格特性と感情状態を抽出する専門家です。")
    lines.append("")
    lines.append("以下の入力テキストを分析し、**必ず以下のJSON形式のみ**で回答してください。")
    lines.append("説明文や前置きは一切不要です。JSONだけを出力してください。")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(config.output_schema, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")

    # 重要度の判定基準
    lines.append("## 判定基準")
    lines.append("")
    lines.append("**importance（重要度）:**")
    for range_str, desc in config.importance_scale.items():
        lines.append(f"- {range_str}: {desc}")
    lines.append("")

    # 性格次元
    lines.append("**personality_signals:**")
    for dim in config.dimensions:
        lines.append(f"- {dim.name}: {dim.description}")
    lines.append("")

    # 確信度
    lines.append("**confidence:**")
    for range_str, desc in config.confidence_scale.items():
        lines.append(f"- {range_str}: {desc}")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════════
# 3. CORTEX Manager — ビルド・読み込み・検証・更新
# ════════════════════════════════════════════════════════════════════════════════

class CortexManager:
    """
    CORTEXのライフサイクル管理。

    - build(): デフォルト設定からCORTEXを生成
    - save(): CORTEX.bin としてシリアライズ（gzip JSON）
    - load(): CORTEX.bin を読み込み
    - validate(): 整合性チェック
    - update_param(): パラメータの試行錯誤的な更新
    - diff(): 2つのCORTEXの差分を表示
    """

    def __init__(self):
        self.cortex: Optional[Cortex] = None

    # ── ビルド ──

    def build(
        self,
        version: str = "1.0.0",
        description: str = "Ghost-Printer default CORTEX",
    ) -> Cortex:
        """デフォルト設定でCORTEXをビルドする"""
        now = datetime.now(timezone.utc).isoformat()

        cortex = Cortex()
        cortex.meta.created_at = now
        cortex.meta.updated_at = now
        cortex.meta.version = version
        cortex.meta.description = description
        cortex.meta.changelog = [
            {"version": version, "date": now, "note": "Initial build"}
        ]

        # BonsaiのSystem Promptを自動生成
        cortex.bonsai.system_prompt = build_system_prompt(cortex.bonsai)

        self.cortex = cortex
        return cortex

    # ── シリアライズ ──

    def save(self, path: str) -> dict:
        """CORTEXをバイナリ（gzip JSON）として保存"""
        if not self.cortex:
            raise ValueError("No CORTEX built. Call build() first.")

        data = self._to_dict(self.cortex)

        # チェックサム計算（meta.checksumを除いた内容のハッシュ）
        data_for_hash = {k: v for k, v in data.items() if k != "meta"}
        content_bytes = json.dumps(data_for_hash, sort_keys=True, ensure_ascii=False).encode()
        checksum = hashlib.sha256(content_bytes).hexdigest()
        data["meta"]["checksum"] = checksum
        self.cortex.meta.checksum = checksum

        # gzip圧縮
        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        compressed = gzip.compress(json_bytes, compresslevel=9)

        # ヘッダー + 圧縮データ
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            # Magic (4 bytes) + Format Version (2 bytes) + Data Length (4 bytes)
            f.write(CORTEX_MAGIC)
            f.write(struct.pack("<H", CORTEX_FORMAT_VERSION))
            f.write(struct.pack("<I", len(compressed)))
            f.write(compressed)

        return {
            "path": str(p),
            "size_bytes": p.stat().st_size,
            "json_size": len(json_bytes),
            "compressed_size": len(compressed),
            "compression_ratio": round(len(compressed) / len(json_bytes), 2),
            "checksum": checksum,
            "version": self.cortex.meta.version,
        }

    # ── デシリアライズ ──

    def load(self, path: str) -> Cortex:
        """CORTEX.binを読み込む"""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"CORTEX not found: {path}")

        with open(p, "rb") as f:
            # ヘッダー検証
            magic = f.read(4)
            if magic != CORTEX_MAGIC:
                raise ValueError(f"Invalid CORTEX magic: {magic!r} (expected {CORTEX_MAGIC!r})")

            fmt_version = struct.unpack("<H", f.read(2))[0]
            if fmt_version > CORTEX_FORMAT_VERSION:
                raise ValueError(f"Unsupported CORTEX format v{fmt_version}")

            data_len = struct.unpack("<I", f.read(4))[0]
            compressed = f.read(data_len)

        # 解凍
        json_bytes = gzip.decompress(compressed)
        data = json.loads(json_bytes)

        # チェックサム検証
        stored_checksum = data.get("meta", {}).get("checksum", "")
        data_for_hash = {k: v for k, v in data.items() if k != "meta"}
        content_bytes = json.dumps(data_for_hash, sort_keys=True, ensure_ascii=False).encode()
        computed_checksum = hashlib.sha256(content_bytes).hexdigest()

        if stored_checksum and stored_checksum != computed_checksum:
            raise ValueError(
                f"CORTEX checksum mismatch: stored={stored_checksum[:16]}... "
                f"computed={computed_checksum[:16]}..."
            )

        self.cortex = self._from_dict(data)
        return self.cortex

    # ── 検証 ──

    def validate(self) -> List[str]:
        """CORTEXの整合性チェック。問題があればエラーリストを返す"""
        if not self.cortex:
            return ["No CORTEX loaded"]

        errors = []
        c = self.cortex

        # Whisper
        if c.whisper.vad_threshold < 0 or c.whisper.vad_threshold > 1:
            errors.append(f"whisper.vad_threshold out of range: {c.whisper.vad_threshold}")

        # Bonsai
        if not c.bonsai.dimensions:
            errors.append("bonsai.dimensions is empty")
        if not c.bonsai.system_prompt:
            errors.append("bonsai.system_prompt is empty")
        dim_names = {d.name for d in c.bonsai.dimensions}
        expected = {"openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"}
        missing_big5 = expected - dim_names
        if missing_big5:
            errors.append(f"Missing Big5 dimensions: {missing_big5}")

        # MiniLM
        if c.minilm.vector_dim < 1:
            errors.append(f"minilm.vector_dim invalid: {c.minilm.vector_dim}")

        # Cortex
        if c.cortex.bayesian_sigma_floor <= 0:
            errors.append(f"cortex.bayesian_sigma_floor must be >0: {c.cortex.bayesian_sigma_floor}")

        # WatchPoint
        if c.watchpoint.max_active < 1:
            errors.append(f"watchpoint.max_active must be >=1: {c.watchpoint.max_active}")
        if c.watchpoint.fitness_floor < 0 or c.watchpoint.fitness_floor > 1:
            errors.append(f"watchpoint.fitness_floor out of range: {c.watchpoint.fitness_floor}")
        if c.watchpoint.decay_half_life_days <= 0:
            errors.append(f"watchpoint.decay_half_life_days must be >0: {c.watchpoint.decay_half_life_days}")
        if c.watchpoint.merge_similarity_threshold < 0 or c.watchpoint.merge_similarity_threshold > 1:
            errors.append(f"watchpoint.merge_similarity_threshold out of range: {c.watchpoint.merge_similarity_threshold}")

        # Meta
        if not c.meta.version:
            errors.append("meta.version is empty")

        return errors

    # ── パラメータ更新（試行錯誤用） ──

    def update_param(self, path: str, value: Any) -> bool:
        """
        ドット区切りのパスでパラメータを更新する。

        例:
            update_param("bonsai.temperature", 0.5)
            update_param("cortex.acoustic_boost_weight", 0.3)
            update_param("whisper.vad_threshold", 0.6)
        """
        if not self.cortex:
            raise ValueError("No CORTEX loaded")

        parts = path.split(".")
        obj = self.cortex

        # パスをたどる
        for part in parts[:-1]:
            if hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                return False

        final = parts[-1]
        if hasattr(obj, final):
            old_value = getattr(obj, final)
            setattr(obj, final, value)

            # System Promptを再生成（bonsai設定が変わった場合）
            if parts[0] == "bonsai":
                self.cortex.bonsai.system_prompt = build_system_prompt(self.cortex.bonsai)

            # メタ更新
            now = datetime.now(timezone.utc).isoformat()
            self.cortex.meta.updated_at = now
            self.cortex.meta.changelog.append({
                "date": now,
                "note": f"Updated {path}: {old_value} → {value}",
            })
            return True

        return False

    # ── バージョンバンプ ──

    def bump_version(self, bump: str = "patch", note: str = "") -> str:
        """バージョンをバンプする (major/minor/patch)"""
        if not self.cortex:
            raise ValueError("No CORTEX loaded")

        parts = self.cortex.meta.version.split(".")
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

        if bump == "major":
            major += 1; minor = 0; patch = 0
        elif bump == "minor":
            minor += 1; patch = 0
        else:
            patch += 1

        new_version = f"{major}.{minor}.{patch}"
        self.cortex.meta.version = new_version
        self.cortex.meta.updated_at = datetime.now(timezone.utc).isoformat()
        self.cortex.meta.changelog.append({
            "version": new_version,
            "date": self.cortex.meta.updated_at,
            "note": note or f"Version bump to {new_version}",
        })
        return new_version

    # ── 差分表示 ──

    def diff(self, other_path: str) -> List[str]:
        """現在のCORTEXと別のCORTEX.binの差分を返す"""
        other_mgr = CortexManager()
        other = other_mgr.load(other_path)

        diffs = []
        d1 = self._to_dict(self.cortex)
        d2 = self._to_dict(other)

        self._compare_dicts(d1, d2, "", diffs)
        return diffs

    def _compare_dicts(self, d1, d2, prefix, diffs):
        all_keys = set(list(d1.keys()) + list(d2.keys()))
        for key in sorted(all_keys):
            path = f"{prefix}.{key}" if prefix else key
            v1 = d1.get(key)
            v2 = d2.get(key)
            if isinstance(v1, dict) and isinstance(v2, dict):
                self._compare_dicts(v1, v2, path, diffs)
            elif v1 != v2:
                diffs.append(f"{path}: {v1} → {v2}")

    # ── エクスポート（人間可読JSON） ──

    def export_json(self, path: str) -> None:
        """人間が読めるJSONとしてエクスポート（試行錯誤の確認用）"""
        if not self.cortex:
            raise ValueError("No CORTEX loaded")
        data = self._to_dict(self.cortex)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── System Prompt取得 ──

    def get_system_prompt(self) -> str:
        """現在のCORTEXからBonsai用System Promptを取得"""
        if not self.cortex:
            raise ValueError("No CORTEX loaded")
        return self.cortex.bonsai.system_prompt

    # ── 内部: シリアライズ ──

    def _to_dict(self, cortex: Cortex) -> dict:
        return {
            "whisper": asdict(cortex.whisper),
            "bonsai": {
                **{k: v for k, v in asdict(cortex.bonsai).items() if k != "dimensions"},
                "dimensions": [asdict(d) for d in cortex.bonsai.dimensions],
            },
            "minilm": asdict(cortex.minilm),
            "cortex": asdict(cortex.cortex),
            "watchpoint": asdict(cortex.watchpoint),
            "meta": asdict(cortex.meta),
        }

    def _from_dict(self, data: dict) -> Cortex:
        c = Cortex()

        # Whisper
        for k, v in data.get("whisper", {}).items():
            if hasattr(c.whisper, k):
                setattr(c.whisper, k, v)

        # Bonsai
        bonsai_data = data.get("bonsai", {})
        for k, v in bonsai_data.items():
            if k == "dimensions":
                c.bonsai.dimensions = [
                    PersonalityDimension(**d) for d in v
                ]
            elif hasattr(c.bonsai, k):
                setattr(c.bonsai, k, v)

        # MiniLM
        for k, v in data.get("minilm", {}).items():
            if hasattr(c.minilm, k):
                setattr(c.minilm, k, v)

        # Cortex config
        for k, v in data.get("cortex", {}).items():
            if hasattr(c.cortex, k):
                setattr(c.cortex, k, v)

        # WatchPoint config (新規追加フィールド。未設定の古いCORTEXとも互換)
        for k, v in data.get("watchpoint", {}).items():
            if hasattr(c.watchpoint, k):
                setattr(c.watchpoint, k, v)

        # Meta
        for k, v in data.get("meta", {}).items():
            if hasattr(c.meta, k):
                setattr(c.meta, k, v)

        return c


# ════════════════════════════════════════════════════════════════════════════════
# 4. CLI
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═══ Ghost-Printer CORTEX Manager ═══\n")

    mgr = CortexManager()

    # 1. ビルド
    cortex = mgr.build(version="1.0.0", description="Ghost-Printer default CORTEX for Pi 5 MVP")
    print(f"Built CORTEX v{cortex.meta.version}")
    print(f"  Whisper: {cortex.whisper.model_file}")
    print(f"  Bonsai:  {cortex.bonsai.model_file} ({len(cortex.bonsai.dimensions)} dimensions)")
    print(f"  MiniLM:  {cortex.minilm.model_file} ({cortex.minilm.vector_dim}d)")

    # 2. 検証
    errors = mgr.validate()
    print(f"\n  Validation: {'✅ OK' if not errors else '❌ ' + str(errors)}")

    # 3. 保存
    info = mgr.save("data/CORTEX.bin")
    print(f"\n  Saved: {info['path']}")
    print(f"  Size: {info['size_bytes']} bytes (JSON {info['json_size']} → compressed {info['compressed_size']}, ratio={info['compression_ratio']})")
    print(f"  Checksum: {info['checksum'][:24]}...")

    # 4. 読み込み
    mgr2 = CortexManager()
    loaded = mgr2.load("data/CORTEX.bin")
    print(f"\n  Loaded: v{loaded.meta.version}, checksum verified ✅")

    # 5. パラメータ更新（試行錯誤）
    print("\n── Parameter Tuning ──")
    mgr2.update_param("bonsai.temperature", 0.5)
    mgr2.update_param("cortex.acoustic_boost_weight", 0.25)
    new_ver = mgr2.bump_version("patch", "Increased temperature for more varied extraction")
    print(f"  Updated to v{new_ver}")

    info2 = mgr2.save("data/CORTEX_v{}.bin".format(new_ver.replace(".", "_")))
    print(f"  Saved: {info2['path']}")

    # 6. 差分
    diffs = mgr2.diff("data/CORTEX.bin")
    print(f"\n── Diff (v1.0.0 → v{new_ver}) ──")
    for d in diffs[:10]:
        print(f"  {d}")

    # 7. System Prompt確認
    prompt = mgr2.get_system_prompt()
    print(f"\n── System Prompt (first 200 chars) ──")
    print(f"  {prompt[:200]}...")
    print(f"  Total length: {len(prompt)} chars")

    # 8. JSON エクスポート
    mgr2.export_json("data/CORTEX_readable.json")
    print(f"\n  Exported readable JSON: data/CORTEX_readable.json")

    print("\n═══ Done ═══")
