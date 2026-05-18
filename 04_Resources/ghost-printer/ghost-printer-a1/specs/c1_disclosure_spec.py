"""
Ghost-Printer C1 — 選択的自己開示システム仕様

設計思想:
- ユーザーが「誰に・何を・どこまで」開示するかを完全に制御
- 開示はカテゴリ・期間・粒度の3軸で制御可能
- Capability Tokenで複数のAI/アプリへの権限を統一管理
- 境界ケースを明示的に定義し、曖昧性を排除

実装レベル:
- Tier 0: データクラス（カテゴリ・粒度・スコープ定義）
- Tier 1: Token生成・検証ロジック
- Tier 2: Permission Gateway（SOULデータの開示制御）
- Tier 3: UI連携用API
"""

from enum import Enum, auto
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Set, Dict, Any
from datetime import datetime, timezone, timedelta
import json
import hashlib
import hmac
from abc import ABC, abstractmethod


# ════════════════════════════════════════════════════════════════════════════════
# 1. 開示カテゴリ（8カテゴリ）
# ════════════════════════════════════════════════════════════════════════════════

class DisclosureCategory(Enum):
    """SOULの8大カテゴリ。各カテゴリは独立して開示制御できる。"""

    # 安定した性格・価値観 — long-term, central self
    CORE_IDENTITY = "core_identity"

    # 最近の出来事・感情エピソード — short-term events
    EPISODIC_MEMORY = "episodic_memory"

    # 現在の気分・感情状態 — present emotional state
    EMOTIONAL_STATE = "emotional_state"

    # 日常行動のパターン（睡眠時間、運動習慣、コーヒー頻度など）
    BEHAVIORAL_PATTERNS = "behavioral_patterns"

    # バイタルサイン（心拍数、睡眠品質、ストレスレベル等）
    HEALTH_VITALS = "health_vitals"

    # 位置情報・移動履歴（GPS、訪問地点等）
    LOCATION_MOVEMENT = "location_movement"

    # 人間関係グラフ（誰と繋がっているか、関係の強度）
    SOCIAL_GRAPH = "social_graph"

    # 興味・関心・価値観の分布（semantic map）
    INTERESTS_VALUES = "interests_values"


# ════════════════════════════════════════════════════════════════════════════════
# 2. 粒度レベル（Granularity）
# ════════════════════════════════════════════════════════════════════════════════

class GranularityLevel(Enum):
    """各カテゴリのデータ粒度。開示はこの4段階から選択。"""

    # Full: 圧縮なしの生データ。個人識別性が最も高い
    FULL = "full"

    # Summary: 集計・平均化。時系列を集約したもの
    # 例：「過去7日の平均心拍数」「今月訪れた場所のカテゴリ」
    SUMMARY = "summary"

    # Anonymized: 名前や個人識別子を削除。関係性は保持
    # 例：「Aさん（→person_id_hash_abc123）」「東京駅（→location_hash_def456）」
    ANONYMIZED = "anonymized"

    # Hidden: データを開示しない
    HIDDEN = "hidden"


# ════════════════════════════════════════════════════════════════════════════════
# 3. スコープテンプレート
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class DisclosureScope:
    """
    開示範囲を定義するテンプレート。

    カテゴリごとに (粒度, 期間制限) を指定する。
    期間制限: None = 制限なし、days=N = 直近N日分のみ
    """

    name: str  # スコープの名前（"Claude Personal"等）

    # 各カテゴリの (粒度, 日数制限) タプル
    # 日数制限 None = 無制限（ただしFULLは常に時系列制限あり）
    categories: Dict[DisclosureCategory, tuple[GranularityLevel, Optional[int]]] = field(
        default_factory=dict
    )

    description: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON シリアライズ用"""
        return {
            "name": self.name,
            "description": self.description,
            "categories": {
                cat.value: [gran.value, days]
                for cat, (gran, days) in self.categories.items()
            },
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


# デフォルトスコープテンプレート集

SCOPE_CLAUDE_PERSONAL = DisclosureScope(
    name="Claude Personal",
    description="パーソナルアシスタント向け。性格と最近の文脈を共有。位置情報・健康データなし。",
    categories={
        DisclosureCategory.CORE_IDENTITY: (GranularityLevel.FULL, None),
        DisclosureCategory.EPISODIC_MEMORY: (GranularityLevel.FULL, 90),  # 直近90日
        DisclosureCategory.EMOTIONAL_STATE: (GranularityLevel.FULL, 7),   # 直近7日
        DisclosureCategory.BEHAVIORAL_PATTERNS: (GranularityLevel.SUMMARY, None),
        DisclosureCategory.INTERESTS_VALUES: (GranularityLevel.FULL, None),
        # これ以下は開示しない
        DisclosureCategory.HEALTH_VITALS: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.LOCATION_MOVEMENT: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.SOCIAL_GRAPH: (GranularityLevel.ANONYMIZED, 30),
    }
)

SCOPE_WORK_ASSISTANT = DisclosureScope(
    name="Work Assistant",
    description="仕事効率化向け。キャリア・仕事の文脈のみ。私生活は非開示。",
    categories={
        DisclosureCategory.CORE_IDENTITY: (GranularityLevel.SUMMARY, None),
        DisclosureCategory.EPISODIC_MEMORY: (GranularityLevel.SUMMARY, 30),  # 仕事関連のみ（フィルタ必須）
        DisclosureCategory.EMOTIONAL_STATE: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.BEHAVIORAL_PATTERNS: (GranularityLevel.SUMMARY, None),  # 仕事時間帯のみ
        DisclosureCategory.INTERESTS_VALUES: (GranularityLevel.SUMMARY, None),  # 仕事関連
        # これ以下は開示しない
        DisclosureCategory.HEALTH_VITALS: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.LOCATION_MOVEMENT: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.SOCIAL_GRAPH: (GranularityLevel.HIDDEN, None),
    }
)

SCOPE_HEALTH_COACH = DisclosureScope(
    name="Health Coach",
    description="健康管理向け。バイタル・行動パターン・感情。社会関係は非開示。",
    categories={
        DisclosureCategory.CORE_IDENTITY: (GranularityLevel.SUMMARY, None),
        DisclosureCategory.EPISODIC_MEMORY: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.EMOTIONAL_STATE: (GranularityLevel.FULL, 30),
        DisclosureCategory.BEHAVIORAL_PATTERNS: (GranularityLevel.FULL, None),
        DisclosureCategory.HEALTH_VITALS: (GranularityLevel.FULL, None),
        DisclosureCategory.INTERESTS_VALUES: (GranularityLevel.SUMMARY, None),  # 運動・食事関連
        # これ以下は開示しない
        DisclosureCategory.LOCATION_MOVEMENT: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.SOCIAL_GRAPH: (GranularityLevel.HIDDEN, None),
    }
)

SCOPE_MINIMAL = DisclosureScope(
    name="Minimal",
    description="最小限の開示。コアアイデンティティの要約のみ。",
    categories={
        DisclosureCategory.CORE_IDENTITY: (GranularityLevel.SUMMARY, None),
        # これ以下は開示しない
        DisclosureCategory.EPISODIC_MEMORY: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.EMOTIONAL_STATE: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.BEHAVIORAL_PATTERNS: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.HEALTH_VITALS: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.LOCATION_MOVEMENT: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.SOCIAL_GRAPH: (GranularityLevel.HIDDEN, None),
        DisclosureCategory.INTERESTS_VALUES: (GranularityLevel.HIDDEN, None),
    }
)

SCOPE_EMERGENCY = DisclosureScope(
    name="Emergency",
    description="緊急用一時スコープ。すべての情報を一時的に開示（自動期限付き）。",
    categories={
        cat: (GranularityLevel.FULL, None)
        for cat in DisclosureCategory
    },
    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),  # 1時間で自動失効
)

# スコープ辞書
SCOPE_TEMPLATES = {
    "claude_personal": SCOPE_CLAUDE_PERSONAL,
    "work_assistant": SCOPE_WORK_ASSISTANT,
    "health_coach": SCOPE_HEALTH_COACH,
    "minimal": SCOPE_MINIMAL,
    "emergency": SCOPE_EMERGENCY,
}


# ════════════════════════════════════════════════════════════════════════════════
# 4. 境界ケース定義
# ════════════════════════════════════════════════════════════════════════════════

class BoundaryCaseAnalysis:
    """
    曖昧な開示境界を明示的に定義する。

    問題: データの複数カテゴリへの帰属が不確定な場合、
    どのカテゴリのルールを適用するか？

    方針: 以下のマトリックスで順位付けし、最も制限的なカテゴリを適用。
    """

    @staticmethod
    def analyze_case(case_description: str) -> tuple[DisclosureCategory, str]:
        """
        特定の境界ケースを分析して、主カテゴリを決定。

        Returns:
            (primary_category, reasoning)
        """

        cases = {
            # ケース 1: 仕事の会話から推測される健康状態
            "仕事の会話から推測される健康状態": (
                DisclosureCategory.HEALTH_VITALS,
                "仕事という文脈があっても、健康データの推測は HEALTH_VITALS 扱い。"
                "Work Assistant では HIDDEN にすべき。"
                "推測可能性が高い情報ほど制限的に。"
            ),

            # ケース 2: Aさんとの会話
            "Aさんとの会話": (
                DisclosureCategory.SOCIAL_GRAPH,
                "エピソード記憶（何を話したか）と社会関係グラフ（誰と繋がっているか）の両方を含む。"
                "社会関係が関わるため、最も制限的な SOCIAL_GRAPH ルールを適用。"
                "つまり SOCIAL_GRAPH が ANONYMIZED なら、会話内容も Aさんの匿名化版のみ。"
            ),

            # ケース 3: 場所から推測される行動パターン
            "場所から推測される行動パターン": (
                DisclosureCategory.LOCATION_MOVEMENT,
                "行動パターン（起床・就寝時刻）と位置情報の関連。"
                "LOCATION_MOVEMENT が HIDDEN なら、位置推測可能な行動パターンも非開示。"
            ),

            # ケース 4: 睡眠ログ（感情 + 健康）
            "睡眠ログ（感情 + 健康）": (
                DisclosureCategory.HEALTH_VITALS,
                "感情状態と睡眠品質の両方を記録しているが、バイタルデータが主。"
                "HEALTH_VITALS >= EMOTIONAL_STATE の制限を適用。"
            ),

            # ケース 5: 休日に一人でいる傾向（行動 + 性格 + 社会関係）
            "休日に一人でいる傾向": (
                DisclosureCategory.SOCIAL_GRAPH,
                "行動パターン・内向性・人間関係の強度が絡み合う。"
                "社会関係情報が含まれるため、SOCIAL_GRAPH が制御権を持つ。"
            ),

            # ケース 6: Spotifyプレイリスト（興味 + 感情 + 社会関係）
            "Spotifyプレイリスト": (
                DisclosureCategory.INTERESTS_VALUES,
                "主には interests_values だが、友人と共有している場合は SOCIAL_GRAPH も関わる。"
                "最も制限的な側（SOCIAL_GRAPH HIDDEN）を適用すべき。"
            ),
        }

        if case_description in cases:
            cat, reasoning = cases[case_description]
            return cat, reasoning

        return None, "Undefined boundary case"


# ════════════════════════════════════════════════════════════════════════════════
# 5. Capability Token 構造
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class CapabilityToken:
    """
    AIやアプリケーションに対して発行される権限トークン。

    構造:
    - issuer: トークン発行者（ユーザー自身）
    - subject: トークンの対象（"claude", "notion-export", "health-coach-app" 等）
    - scope: 開示スコープオブジェクト
    - granted_at: 発行日時
    - expires_at: 失効日時
    - one_time: True なら1回限りの使用後に失効
    - signature: HMAC-SHA256 署名（改ざん防止）

    JWT互換設計（将来的に jwt.encode() で標準化可能）
    """

    issuer: str  # ユーザーID / デバイスID
    subject: str  # AI / アプリケーション名
    scope: DisclosureScope
    granted_at: datetime
    expires_at: datetime
    one_time: bool = False
    nonce: str = ""  # 再利用防止用ワンタイムID

    # シグネチャ（秘密鍵で生成）
    signature: str = ""

    def to_jwt_payload(self) -> Dict[str, Any]:
        """JWT形式でシリアライズ（将来の標準化用）"""
        return {
            "iss": self.issuer,
            "sub": self.subject,
            "scope": self.scope.to_dict(),
            "iat": int(self.granted_at.timestamp()),
            "exp": int(self.expires_at.timestamp()),
            "one_time": self.one_time,
            "nonce": self.nonce,
        }

    def to_dict(self) -> Dict[str, Any]:
        """辞書形式でシリアライズ"""
        return {
            "issuer": self.issuer,
            "subject": self.subject,
            "scope": self.scope.to_dict(),
            "granted_at": self.granted_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "one_time": self.one_time,
            "nonce": self.nonce,
            "signature": self.signature,
        }

    def is_valid(self) -> bool:
        """トークンが有効か確認"""
        now = datetime.now(timezone.utc)
        return now < self.expires_at and (self.scope.expires_at is None or now < self.scope.expires_at)


@dataclass
class TokenManager:
    """Capability Token の生成・検証・管理"""

    secret_key: str  # HMAC署名用秘密鍵

    def generate_token(
        self,
        issuer: str,
        subject: str,
        scope: DisclosureScope,
        expires_in_hours: int = 24,
        one_time: bool = False,
    ) -> CapabilityToken:
        """新しいCapability Tokenを生成"""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=expires_in_hours)
        nonce = hashlib.sha256(
            f"{issuer}{subject}{now.isoformat()}".encode()
        ).hexdigest()[:16]

        token = CapabilityToken(
            issuer=issuer,
            subject=subject,
            scope=scope,
            granted_at=now,
            expires_at=expires_at,
            one_time=one_time,
            nonce=nonce,
        )

        # HMAC署名を生成
        payload = json.dumps(token.to_jwt_payload(), sort_keys=True)
        signature = hmac.new(
            self.secret_key.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        token.signature = signature

        return token

    def verify_token(self, token: CapabilityToken) -> bool:
        """トークンの署名を検証"""
        if not token.is_valid():
            return False

        payload = json.dumps(token.to_jwt_payload(), sort_keys=True)
        expected_signature = hmac.new(
            self.secret_key.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        return token.signature == expected_signature


# ════════════════════════════════════════════════════════════════════════════════
# 6. Permission Gateway
# ════════════════════════════════════════════════════════════════════════════════

class PermissionGateway:
    """
    SOULデータに対するアクセス制御を行う中核コンポーネント。

    フロー:
    1. Capability Token を受け取る
    2. Token が有効か確認
    3. スコープのルールに従ってSOULデータをフィルタリング
    4. 粒度に応じて適切に変換（Full/Summary/Anonymized/Hidden）
    5. 開示許可データのみ返す
    """

    def __init__(self, token_manager: TokenManager):
        self.token_manager = token_manager

    def filter_soul_by_token(
        self,
        soul: Dict[str, Any],
        token: CapabilityToken,
    ) -> Dict[str, Any]:
        """
        Capability Token に基づいてSOULデータをフィルタリング。

        Returns:
            開示許可されたカテゴリのみを含むSOUL（部分版）
        """

        # トークン署名を検証
        if not self.token_manager.verify_token(token):
            return {"error": "Invalid token", "filtered_soul": {}}

        # スコープのカテゴリルールを反映
        filtered = {
            "version": soul.get("version"),
            "owner_hash": soul.get("owner_hash"),  # ハッシュは常に開示（個人識別のため）
            "token_subject": token.subject,
            "filtered_at": datetime.now(timezone.utc).isoformat(),
            "categories": {},
        }

        for category, (granularity, days_limit) in token.scope.categories.items():
            cat_key = category.value

            if granularity == GranularityLevel.HIDDEN:
                continue  # このカテゴリは開示しない

            # SOULから対応するデータを抽出
            soul_data = soul.get(cat_key, {})
            if not soul_data:
                continue

            # 粒度に応じて変換
            processed = self._apply_granularity(
                soul_data,
                category,
                granularity,
                days_limit,
            )

            if processed is not None:
                filtered["categories"][cat_key] = {
                    "granularity": granularity.value,
                    "days_limit": days_limit,
                    "data": processed,
                }

        return filtered

    def _apply_granularity(
        self,
        data: Dict[str, Any],
        category: DisclosureCategory,
        granularity: GranularityLevel,
        days_limit: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """粒度に応じてデータを変換"""

        if granularity == GranularityLevel.FULL:
            # 期間制限を適用
            if days_limit:
                return self._apply_time_limit(data, category, days_limit)
            return data

        elif granularity == GranularityLevel.SUMMARY:
            # 集計・平均化
            return self._summarize_data(data, category)

        elif granularity == GranularityLevel.ANONYMIZED:
            # 個人識別子を削除
            return self._anonymize_data(data, category)

        else:
            return None

    def _apply_time_limit(
        self,
        data: Dict[str, Any],
        category: DisclosureCategory,
        days: int,
    ) -> Dict[str, Any]:
        """期間制限を適用（直近N日のみ）"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # episodic_memory の場合、recentエピソードをフィルタ
        if category == DisclosureCategory.EPISODIC_MEMORY:
            recent = data.get("recent", [])
            filtered_recent = [
                ep for ep in recent
                if datetime.fromisoformat(ep["timestamp"]) > cutoff
            ]
            return {"recent": filtered_recent, "compressed": []}

        # 他のカテゴリは時間メタデータで判定
        return data

    def _summarize_data(
        self,
        data: Dict[str, Any],
        category: DisclosureCategory,
    ) -> Dict[str, Any]:
        """集計・平均化（実装例）"""

        # 各カテゴリごとに異なるサマリー方法
        if category == DisclosureCategory.CORE_IDENTITY:
            # 人格の確信度を落とす（σを大きくする）
            return {
                k: {
                    "mu": v.get("mu", 0.5),
                    "sigma": v.get("sigma", 0.3) * 1.5,  # 不確かさを増す
                }
                for k, v in data.items()
            }

        elif category == DisclosureCategory.EPISODIC_MEMORY:
            # エピソード数を報告するのみ
            return {
                "recent_count": len(data.get("recent", [])),
                "summary": "Recent episodic memories (detailed content hidden)",
            }

        elif category == DisclosureCategory.BEHAVIORAL_PATTERNS:
            # 時間帯の分布のみ
            return {
                "active_hours_summary": "Pattern-based summary",
                "routines_count": len(data.get("routines", [])),
            }

        return data

    def _anonymize_data(
        self,
        data: Dict[str, Any],
        category: DisclosureCategory,
    ) -> Dict[str, Any]:
        """個人識別子を削除（ハッシュ化）"""

        if category == DisclosureCategory.SOCIAL_GRAPH:
            # 名前を削除、person_idをハッシュ化
            if "relationships" in data:
                anon_rels = []
                for rel in data["relationships"]:
                    person_id = rel.get("person_id", "")
                    hashed_id = hashlib.sha256(person_id.encode()).hexdigest()[:12]
                    anon_rels.append({
                        "person_id_hash": hashed_id,
                        "relationship_type": rel.get("relationship_type"),
                        "strength": rel.get("strength"),
                        # 名前は削除
                    })
                return {"relationships": anon_rels}

        elif category == DisclosureCategory.LOCATION_MOVEMENT:
            # 座標をジオハッシュ化
            if "visited_locations" in data:
                anon_locs = []
                for loc in data["visited_locations"]:
                    loc_id = loc.get("location_id", "")
                    hashed_id = hashlib.sha256(loc_id.encode()).hexdigest()[:12]
                    anon_locs.append({
                        "location_id_hash": hashed_id,
                        "visit_count": loc.get("visit_count"),
                        "category": loc.get("category"),  # 「カフェ」「駅」等（具体地点ではなく）
                    })
                return {"visited_locations": anon_locs}

        return data


# ════════════════════════════════════════════════════════════════════════════════
# 7. 使用例・テストコード
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # サンプルSOULデータ（簡略版）
    sample_soul = {
        "version": "0.1.0",
        "owner_hash": "user_12345",
        "core_identity": {
            "openness": {"mu": 0.72, "sigma": 0.15},
            "extraversion": {"mu": 0.35, "sigma": 0.20},
        },
        "episodic_memory": {
            "recent": [
                {
                    "timestamp": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
                    "summary": "Had coffee with a friend",
                    "importance": 0.6,
                }
            ]
        },
        "social_graph": {
            "relationships": [
                {"person_id": "alice_001", "name": "Alice", "strength": 0.9},
                {"person_id": "bob_002", "name": "Bob", "strength": 0.5},
            ]
        },
    }

    # TokenManager を初期化
    manager = TokenManager(secret_key="demo_secret_key_12345")

    # Claude Personal スコープで Token を生成
    token = manager.generate_token(
        issuer="user_12345",
        subject="claude",
        scope=SCOPE_CLAUDE_PERSONAL,
        expires_in_hours=24,
    )

    print("Generated Token:")
    print(json.dumps(token.to_dict(), indent=2, ensure_ascii=False))
    print()

    # Permission Gateway を通してフィルタリング
    gateway = PermissionGateway(manager)
    filtered = gateway.filter_soul_by_token(sample_soul, token)

    print("Filtered SOUL (Claude Personal scope):")
    print(json.dumps(filtered, indent=2, ensure_ascii=False))
    print()

    # 境界ケース分析
    print("Boundary Case Analysis:")
    for case_name in [
        "仕事の会話から推測される健康状態",
        "Aさんとの会話",
        "場所から推測される行動パターン",
    ]:
        cat, reasoning = BoundaryCaseAnalysis.analyze_case(case_name)
        print(f"\n{case_name}:")
        print(f"  → Primary Category: {cat.value if cat else 'N/A'}")
        print(f"  → Reasoning: {reasoning}")
