"""
Ghost-Printer C4 — Permission Gateway

SOULデータに対するアクセス制御の中核コンポーネント。
Capability Tokenで認証されたリクエストに対して、
スコープのルールに従ってSOULデータをフィルタリングし、
粒度に応じた変換（Full/Summary/Anonymized/Hidden）を適用する。

フロー:
  1. Capability Token を受け取る
  2. TokenManager で署名・有効期限・失効を検証
  3. スコープのルールに従ってSOULデータをフィルタリング
  4. 粒度に応じて適切に変換
  5. 開示許可データのみ返す（→ 外部AIのSystem Promptに変換可能）

使い方:
    gateway = PermissionGateway(token_manager)
    result = gateway.filter_soul(soul_data, token)
    prompt = gateway.soul_to_prompt(soul_data, token)
"""

import hashlib
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from capability_token import (
    CapabilityToken,
    CategoryRule,
    DisclosureCategory,
    DisclosureScope,
    GranularityLevel,
    TokenManager,
)

# A6 統合用: バイナリパスは soul_binary 経由で暗号学的アクセス制御を行う
import soul_binary as sb


# ════════════════════════════════════════════════════════════════════════════════
# 1. フィルタリング結果
# ════════════════════════════════════════════════════════════════════════════════

class FilterResult:
    """Permission Gatewayのフィルタリング結果"""

    def __init__(self):
        self.success: bool = False
        self.error: str = ""
        self.token_subject: str = ""
        self.scope_name: str = ""
        self.filtered_at: str = ""
        self.categories: Dict[str, dict] = {}
        self.stats: Dict[str, Any] = {}

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "error": self.error,
            "token_subject": self.token_subject,
            "scope_name": self.scope_name,
            "filtered_at": self.filtered_at,
            "categories": self.categories,
            "stats": self.stats,
        }


# ════════════════════════════════════════════════════════════════════════════════
# 2. Permission Gateway 本体
# ════════════════════════════════════════════════════════════════════════════════

class PermissionGateway:
    """
    SOULデータのアクセス制御ゲートウェイ。

    Tokenの検証 → カテゴリごとのフィルタリング → 粒度変換 → 結果返却
    """

    def __init__(self, token_manager: TokenManager):
        self.token_manager = token_manager

    # ── メイン: SOULフィルタリング ──

    def filter_soul(
        self,
        soul: dict,
        token: CapabilityToken,
        consume_if_onetime: bool = True,
    ) -> FilterResult:
        """
        Capability Tokenに基づいてSOULデータをフィルタリング。

        Args:
            soul: 完全なSOULデータ
            token: 検証済みCapability Token
            consume_if_onetime: ワンタイムトークンを自動消費するか

        Returns:
            FilterResult（成功時はcategoriesにフィルタ済みデータ）
        """
        result = FilterResult()
        result.filtered_at = datetime.now(timezone.utc).isoformat()

        # 1. トークン検証
        valid, reason = self.token_manager.verify(token)
        if not valid:
            result.error = f"Token verification failed: {reason}"
            return result

        # 2. ワンタイムトークンの消費
        if consume_if_onetime and token.one_time:
            ok, consume_reason = self.token_manager.consume(token)
            if not ok:
                result.error = f"Token consume failed: {consume_reason}"
                return result

        result.success = True
        result.token_subject = token.subject
        result.scope_name = token.scope.name

        # 3. カテゴリごとにフィルタリング
        hidden_count = 0
        disclosed_count = 0

        for cat_name in DisclosureCategory:
            cat_key = cat_name.value
            rule = token.scope.get_rule(cat_key)

            if rule.granularity == GranularityLevel.HIDDEN:
                hidden_count += 1
                continue

            # SOULからカテゴリデータを取得
            raw_data = self._extract_category_data(soul, cat_key)
            if raw_data is None:
                continue

            # 粒度変換
            processed = self._apply_granularity(raw_data, cat_key, rule)
            if processed is not None:
                result.categories[cat_key] = {
                    "granularity": rule.granularity.value,
                    "days_limit": rule.days_limit,
                    "data": processed,
                }
                disclosed_count += 1

        result.stats = {
            "total_categories": len(DisclosureCategory),
            "disclosed": disclosed_count,
            "hidden": hidden_count,
            "disclosure_ratio": round(disclosed_count / len(DisclosureCategory), 2),
        }

        return result

    # ════════════════════════════════════════════════════════════════════════
    # A6 バイナリ統合: 暗号学的アクセス制御
    # ════════════════════════════════════════════════════════════════════════

    def filter_soul_bytes(
        self,
        soul_bytes: bytes,
        token: CapabilityToken,
        master_key: bytes,
        consume_if_onetime: bool = True,
    ) -> FilterResult:
        """
        `.soul` バイナリ + Token + master_key から FilterResult を生成する。

        鍵導出は `soul_binary.derive_keys_for_token()` 経由。HIDDEN カテゴリ
        の鍵は決して導出されないため、対応セクションは復号できず Redacted。
        その後、既存の dict ベース粒度変換 (SUMMARY/ANONYMIZED) を上から適用。

        この二層構造で:
          - HIDDEN: 暗号学的に隔離 (鍵が無いので復号不能)
          - SUMMARY/ANONYMIZED: 鍵で復号後に粒度変換 (アプリ層)
          - FULL: 鍵で復号して全データ
        """
        result = FilterResult()
        result.filtered_at = datetime.now(timezone.utc).isoformat()

        # 1. Token 検証
        valid, reason = self.token_manager.verify(token)
        if not valid:
            result.error = f"Token verification failed: {reason}"
            return result

        if consume_if_onetime and token.one_time:
            ok, cr = self.token_manager.consume(token)
            if not ok:
                result.error = f"Token consume failed: {cr}"
                return result

        # 2. Token から鍵セット導出 (HIDDEN は出ない)
        try:
            keys = sb.derive_keys_for_token(master_key, token)
        except (ValueError, RuntimeError) as e:
            result.error = f"Key derivation failed: {e}"
            return result

        # 3. デコード (鍵が無いセクションは _meta.redacted_sections に上がる)
        try:
            soul = sb.decode_soul(
                soul_bytes,
                master_key=master_key,
                section_keys=keys,
            )
        except ValueError as e:
            result.error = f"SOUL decode failed: {e}"
            return result

        redacted_sids = set(soul.get("_meta", {}).get("redacted_sections", []))

        result.success = True
        result.token_subject = token.subject
        result.scope_name = token.scope.name

        # 4. カテゴリごとに粒度変換 (既存ロジックを再利用)
        hidden_count = 0
        disclosed_count = 0
        crypto_redacted_count = 0

        for cat_name in DisclosureCategory:
            cat_key = cat_name.value
            rule = token.scope.get_rule(cat_key)

            if rule.granularity == GranularityLevel.HIDDEN:
                hidden_count += 1
                continue

            # 暗号学的に redact されているセクションは触らない (鍵が無い)
            sids = sb.CATEGORY_TO_SECTIONS.get(cat_key, [])
            if sids and all(sid in redacted_sids for sid in sids):
                crypto_redacted_count += 1
                hidden_count += 1
                continue

            raw_data = self._extract_category_data(soul, cat_key)
            if raw_data is None:
                continue

            processed = self._apply_granularity(raw_data, cat_key, rule)
            if processed is not None:
                result.categories[cat_key] = {
                    "granularity": rule.granularity.value,
                    "days_limit": rule.days_limit,
                    "data": processed,
                }
                disclosed_count += 1

        result.stats = {
            "total_categories": len(DisclosureCategory),
            "disclosed": disclosed_count,
            "hidden": hidden_count,
            "cryptographically_redacted": crypto_redacted_count,
            "disclosure_ratio": round(disclosed_count / len(DisclosureCategory), 2),
        }

        return result

    def create_partial_view(
        self,
        soul_bytes: bytes,
        token: CapabilityToken,
        master_key: bytes,
        consume_if_onetime: bool = True,
    ) -> tuple[bytes, FilterResult]:
        """
        Token に応じた自己完結 `.soul` バイナリを生成する。

        - HIDDEN カテゴリのセクションはバイナリレベルで削除 (granted_sections)
        - 残るセクションは平文 (受信側の master_key を持たないため)。
          外部送信時の機密性は TLS 等の transport-level に委ねる
        - HEADER_FLAG_PARTIAL = 1 が立つ
        - 受信側は master_key 不要で `decode_soul()` を呼べる

        粒度変換 (SUMMARY/ANONYMIZED) はバイナリには反映せず、 受信側が
        `filter_soul_bytes` を呼んで適用するか、 dict ベースで処理する。

        Returns:
            (partial_view_bytes, FilterResult — 失敗時は空 bytes)
        """
        result = FilterResult()
        result.filtered_at = datetime.now(timezone.utc).isoformat()

        # 1. Token 検証
        valid, reason = self.token_manager.verify(token)
        if not valid:
            result.error = f"Token verification failed: {reason}"
            return b"", result

        if consume_if_onetime and token.one_time:
            ok, cr = self.token_manager.consume(token)
            if not ok:
                result.error = f"Token consume failed: {cr}"
                return b"", result

        # 2. 鍵導出 + デコード
        try:
            keys = sb.derive_keys_for_token(master_key, token)
            soul = sb.decode_soul(
                soul_bytes,
                master_key=master_key,
                section_keys=keys,
            )
        except (ValueError, RuntimeError) as e:
            result.error = f"Decode failed: {e}"
            return b"", result

        soul.pop("_meta", None)  # _meta は decode 由来なので再エンコードに持ち越さない

        # 3. granted_sections を Token の非 HIDDEN カテゴリから決定
        granted: set[int] = set()
        granted_categories: list[str] = []
        for cat_name in DisclosureCategory:
            cat_key = cat_name.value
            rule = token.scope.get_rule(cat_key)
            if rule.granularity != GranularityLevel.HIDDEN:
                granted.update(sb.CATEGORY_TO_SECTIONS.get(cat_key, []))
                granted_categories.append(cat_key)

        # 4. 再エンコード (平文・部分ビュー)
        partial_bytes = sb.encode_soul(soul, granted_sections=granted)

        result.success = True
        result.token_subject = token.subject
        result.scope_name = token.scope.name
        result.stats = {
            "granted_categories": granted_categories,
            "granted_section_count": len(granted),
            "view_bytes": len(partial_bytes),
        }

        return partial_bytes, result

    def create_verifiable_partial_view(
        self,
        soul_bytes: bytes,
        token: CapabilityToken,
        master_key: bytes,
        consume_if_onetime: bool = True,
    ) -> tuple[bytes, dict[int, bytes], FilterResult]:
        """元バイト保持 + Merkle 部分証明付き の検証可能な partial view を生成する。

        plaintext を返す `create_partial_view` とは異なり、こちらは:
        - section bytes を **元のまま (暗号化されたまま)** コピーする
        - SEC_MERKLE_PROOF を同梱して原本 root への proof を提供
        - 受信者は section_keys で復号 + verify_partial_view で原本との結びつき検証

        Returns:
            (view_bytes, section_keys, FilterResult)
            - view_bytes: 検証可能な部分ビュー (HEADER_FLAG_PARTIAL=1)
            - section_keys: 受信者に渡す per-section 復号鍵 (HIDDEN は出ない)
            - FilterResult: token verification + stats
        """
        result = FilterResult()
        result.filtered_at = datetime.now(timezone.utc).isoformat()

        # 1. Token 検証
        valid, reason = self.token_manager.verify(token)
        if not valid:
            result.error = f"Token verification failed: {reason}"
            return b"", {}, result

        if consume_if_onetime and token.one_time:
            ok, cr = self.token_manager.consume(token)
            if not ok:
                result.error = f"Token consume failed: {cr}"
                return b"", {}, result

        # 2. 鍵セット導出
        try:
            keys = sb.derive_keys_for_token(master_key, token)
        except (ValueError, RuntimeError) as e:
            result.error = f"Key derivation failed: {e}"
            return b"", {}, result

        # 3. granted_section_ids 決定
        granted: set[int] = set()
        granted_categories: list[str] = []
        for cat_name in DisclosureCategory:
            cat_key = cat_name.value
            rule = token.scope.get_rule(cat_key)
            if rule.granularity != GranularityLevel.HIDDEN:
                granted.update(sb.CATEGORY_TO_SECTIONS.get(cat_key, []))
                granted_categories.append(cat_key)

        # 4. 元バイトを保持して部分ビュー作成
        try:
            view_bytes = sb.extract_partial_view_bytes(
                soul_bytes, granted, include_proofs=True
            )
        except ValueError as e:
            result.error = f"Extract failed: {e}"
            return b"", {}, result

        result.success = True
        result.token_subject = token.subject
        result.scope_name = token.scope.name
        result.stats = {
            "granted_categories": granted_categories,
            "granted_section_count": len(granted),
            "view_bytes": len(view_bytes),
            "key_count": len(keys),
            "verifiable": True,
        }
        return view_bytes, keys, result

    # ── SOUL → System Prompt 変換（Token付き） ──

    def soul_to_prompt(self, soul: dict, token: CapabilityToken) -> str:
        """
        TokenでフィルタリングしたSOULデータからSystem Promptを生成。

        これがGhost-Printerの核心フロー:
          SOUL → Permission Gateway → フィルタ済みデータ → System Prompt → 外部AI
        """
        filtered = self.filter_soul(soul, token, consume_if_onetime=False)

        if not filtered.success:
            return f"[Permission Gateway Error: {filtered.error}]"

        sections = []

        # ── ヘッダー ──
        sections.append(
            "あなたはこの人のパーソナルアシスタントです。\n"
            "以下はこの人のSOULデータ（パーソナリティプロファイル）です。\n"
            "このデータを参考に、この人に最も適した応答をしてください。\n"
            "ただし「あなたのSOULデータによると…」のような直接的な言及は避け、\n"
            "自然にこの人を理解している人のように振る舞ってください。\n"
            f"[開示スコープ: {filtered.scope_name}]"
        )

        # ── Core Identity ──
        if "core_identity" in filtered.categories:
            cat = filtered.categories["core_identity"]
            sections.append(self._format_identity(cat["data"], cat["granularity"]))

        # ── Episodic Memory ──
        if "episodic_memory" in filtered.categories:
            cat = filtered.categories["episodic_memory"]
            sections.append(self._format_episodes(cat["data"], cat["granularity"]))

        # ── Emotional State ──
        if "emotional_state" in filtered.categories:
            cat = filtered.categories["emotional_state"]
            sections.append(self._format_emotional_state(cat["data"], cat["granularity"]))

        # ── Interests & Values ──
        if "interests_values" in filtered.categories:
            cat = filtered.categories["interests_values"]
            sections.append(self._format_interests(cat["data"], cat["granularity"]))

        # ── Behavioral Patterns ──
        if "behavioral_patterns" in filtered.categories:
            cat = filtered.categories["behavioral_patterns"]
            sections.append(self._format_behavioral(cat["data"], cat["granularity"]))

        # ── Social Graph ──
        if "social_graph" in filtered.categories:
            cat = filtered.categories["social_graph"]
            sections.append(self._format_social(cat["data"], cat["granularity"]))

        return "\n\n".join(s for s in sections if s)

    # ══════════════════════════════════════════════════════════════════════════
    # 内部: カテゴリデータ抽出
    # ══════════════════════════════════════════════════════════════════════════

    def _extract_category_data(self, soul: dict, cat_key: str) -> Optional[dict]:
        """SOULからカテゴリ対応データを抽出する"""
        mapping = {
            "core_identity":       lambda s: s.get("core_identity"),
            "episodic_memory":     lambda s: s.get("episodic_memory"),
            "emotional_state":     lambda s: self._derive_emotional_state(s),
            "behavioral_patterns": lambda s: s.get("temporal_patterns"),
            "health_vitals":       lambda s: s.get("health_vitals"),
            "location_movement":   lambda s: s.get("location_movement"),
            "social_graph":        lambda s: s.get("social_graph"),
            "interests_values":    lambda s: s.get("semantic_map"),
        }
        extractor = mapping.get(cat_key)
        if extractor:
            return extractor(soul)
        return None

    def _derive_emotional_state(self, soul: dict) -> Optional[dict]:
        """エピソードから感情状態を導出する"""
        recent = soul.get("episodic_memory", {}).get("recent", [])
        if not recent:
            return None

        emotions = {}
        for ep in recent[-10:]:
            em = ep.get("emotion", {})
            name = em.get("name", "neutral")
            intensity = em.get("intensity", 0.3)
            if name not in emotions:
                emotions[name] = {"count": 0, "total_intensity": 0.0}
            emotions[name]["count"] += 1
            emotions[name]["total_intensity"] += intensity

        return {
            "recent_emotions": emotions,
            "episode_count": len(recent),
            "derived_from": "episodic_memory",
        }

    # ══════════════════════════════════════════════════════════════════════════
    # 内部: 粒度変換
    # ══════════════════════════════════════════════════════════════════════════

    def _apply_granularity(
        self,
        data: dict,
        cat_key: str,
        rule: CategoryRule,
    ) -> Optional[dict]:
        """粒度ルールに応じてデータを変換"""

        if rule.granularity == GranularityLevel.FULL:
            if rule.days_limit:
                return self._apply_time_limit(data, cat_key, rule.days_limit)
            return data

        elif rule.granularity == GranularityLevel.SUMMARY:
            return self._summarize(data, cat_key)

        elif rule.granularity == GranularityLevel.ANONYMIZED:
            result = data.copy() if isinstance(data, dict) else data
            if rule.days_limit:
                result = self._apply_time_limit(result, cat_key, rule.days_limit)
            return self._anonymize(result, cat_key)

        return None

    def _apply_time_limit(self, data: dict, cat_key: str, days: int) -> dict:
        """期間制限: 直近N日分のみ保持"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        if cat_key == "episodic_memory":
            recent = data.get("recent", [])
            filtered = []
            for ep in recent:
                try:
                    ts = datetime.fromisoformat(ep["timestamp"])
                    if ts > cutoff:
                        filtered.append(ep)
                except (KeyError, ValueError):
                    pass
            return {"recent": filtered, "compressed": []}

        if cat_key == "emotional_state":
            # 感情状態は元データのエピソード数で判断（既に導出済み）
            return data

        return data

    def _summarize(self, data: dict, cat_key: str) -> dict:
        """Summary粒度: 集計・平均化"""

        if cat_key == "core_identity":
            # σを1.5倍にして不確かさを増す
            return {
                dim: {
                    "mu": round(dist.get("mu", 0.5), 2),
                    "sigma": round(min(dist.get("sigma", 0.3) * 1.5, 0.5), 2),
                }
                for dim, dist in data.items()
                if isinstance(dist, dict) and "mu" in dist
            }

        elif cat_key == "episodic_memory":
            recent = data.get("recent", [])
            return {
                "episode_count": len(recent),
                "summary": "Recent episodic memories available (detailed content hidden)",
            }

        elif cat_key == "interests_values":
            interests = data.get("interests", {})
            values = data.get("values", {})
            top_interests = sorted(interests.items(), key=lambda x: -x[1])[:5]
            top_values = sorted(values.items(), key=lambda x: -x[1])[:3]
            return {
                "top_interests": [t[0] for t in top_interests],
                "top_values": [v[0] for v in top_values],
            }

        elif cat_key == "behavioral_patterns":
            return {
                "active_hours_summary": "Pattern data available (summary mode)",
                "routines_count": len(data.get("routines", [])),
            }

        elif cat_key == "emotional_state":
            emotions = data.get("recent_emotions", {})
            if emotions:
                dominant = max(emotions.items(), key=lambda x: x[1]["count"])
                return {"dominant_emotion": dominant[0], "variety": len(emotions)}
            return {"dominant_emotion": "neutral", "variety": 0}

        return data

    def _anonymize(self, data: dict, cat_key: str) -> dict:
        """Anonymized粒度: 個人識別子をハッシュ化"""

        if cat_key == "social_graph":
            relationships = data.get("relationships", [])
            anon = []
            for rel in relationships:
                pid = rel.get("person_id", rel.get("name", "unknown"))
                hashed = hashlib.sha256(str(pid).encode()).hexdigest()[:12]
                anon.append({
                    "person_hash": hashed,
                    "relationship_type": rel.get("relationship_type", "unknown"),
                    "strength": rel.get("strength", 0.5),
                })
            return {"relationships": anon}

        elif cat_key == "location_movement":
            locations = data.get("visited_locations", data.get("locations", []))
            anon = []
            for loc in locations:
                lid = loc.get("location_id", loc.get("name", "unknown"))
                hashed = hashlib.sha256(str(lid).encode()).hexdigest()[:12]
                anon.append({
                    "location_hash": hashed,
                    "category": loc.get("category", "unknown"),
                    "visit_count": loc.get("visit_count", 1),
                })
            return {"visited_locations": anon}

        elif cat_key == "episodic_memory":
            recent = data.get("recent", [])
            anon = []
            for ep in recent:
                anon.append({
                    "timestamp": ep.get("timestamp"),
                    "emotion": ep.get("emotion"),
                    "importance": ep.get("importance"),
                    "topics": ep.get("topics", []),
                    # raw_text と summary は除去（個人識別性が高い）
                })
            return {"recent": anon, "compressed": []}

        return data

    # ══════════════════════════════════════════════════════════════════════════
    # 内部: System Prompt フォーマッター
    # ══════════════════════════════════════════════════════════════════════════

    _TRAIT_DESC = {
        "openness":          ("新しいアイデアや体験に強い関心を持つ", "慣れ親しんだ方法を好む"),
        "conscientiousness": ("計画的で目標志向", "柔軟で即興的"),
        "extraversion":      ("社交的で活動的", "内省的で一人の時間を大切にする"),
        "agreeableness":     ("協調的で思いやりがある", "自分の意見を率直に言う"),
        "neuroticism":       ("感情の起伏がある", "情緒が安定している"),
        "curiosity":         ("知的探究心が非常に強い", "必要な情報を効率的に得る"),
        "creativity":        ("独自の発想力がある", "実証済みの方法を信頼する"),
        "empathy":           ("他者の感情を深く理解する", "論理的・客観的なアプローチを好む"),
        "risk_tolerance":    ("リスクを恐れず前に進める", "慎重にリスクを評価する"),
        "independence":      ("自分の判断を信頼する", "チームワークを重視する"),
    }

    def _format_identity(self, data: dict, granularity: str) -> str:
        lines = ["## この人の性格特性"]
        if granularity == "summary":
            lines.append("（要約版 — 詳細はスコープ外）")

        for dim, dist in sorted(data.items(), key=lambda x: x[1].get("sigma", 1)):
            if not isinstance(dist, dict) or "mu" not in dist:
                continue
            desc = self._TRAIT_DESC.get(dim)
            if not desc:
                continue
            mu = dist["mu"]
            sigma = dist["sigma"]
            if sigma >= 0.25 and granularity != "summary":
                continue  # 不確かすぎるものはスキップ（fullモードのとき）
            label = desc[0] if mu > 0.55 else desc[1] if mu < 0.45 else None
            if label:
                conf = "（安定）" if sigma < 0.15 else ""
                lines.append(f"- {label}{conf}")
        return "\n".join(lines)

    def _format_episodes(self, data: dict, granularity: str) -> str:
        if granularity == "summary":
            count = data.get("episode_count", 0)
            return f"## 最近の出来事\n{count}件のエピソードが記録されています。"

        recent = data.get("recent", [])
        if not recent:
            return ""
        lines = ["## 最近の出来事"]
        for ep in recent[-5:]:
            summary = ep.get("summary", "")
            if summary:
                lines.append(f"- {summary}")
            elif ep.get("topics"):
                lines.append(f"- トピック: {', '.join(ep['topics'])}")
        return "\n".join(lines)

    def _format_emotional_state(self, data: dict, granularity: str) -> str:
        lines = ["## 感情の傾向"]
        if granularity == "summary":
            dom = data.get("dominant_emotion", "neutral")
            lines.append(f"最近の主な感情: {dom}")
            return "\n".join(lines)

        emotions = data.get("recent_emotions", {})
        for name, info in sorted(emotions.items(), key=lambda x: -x[1]["count"]):
            avg = info["total_intensity"] / max(info["count"], 1)
            lines.append(f"- {name}: {info['count']}回 (平均強度 {avg:.1f})")
        return "\n".join(lines)

    def _format_interests(self, data: dict, granularity: str) -> str:
        if granularity == "summary":
            topics = data.get("top_interests", [])
            vals = data.get("top_values", [])
            lines = ["## 興味・関心（要約）"]
            if topics:
                lines.append(f"主な関心: {', '.join(topics)}")
            if vals:
                lines.append(f"大切な価値観: {', '.join(vals)}")
            return "\n".join(lines)

        lines = ["## 興味・関心"]
        interests = data.get("interests", {})
        if interests:
            top = sorted(interests.items(), key=lambda x: -x[1])[:10]
            lines.append(f"関心領域: {', '.join(t[0] for t in top)}")
        values = data.get("values", {})
        if values:
            top_v = sorted(values.items(), key=lambda x: -x[1])[:5]
            lines.append(f"価値観: {', '.join(v[0] for v in top_v)}")
        return "\n".join(lines)

    def _format_behavioral(self, data: dict, granularity: str) -> str:
        lines = ["## 行動パターン"]
        if granularity == "summary":
            lines.append(data.get("active_hours_summary", "行動パターンデータあり"))
            return "\n".join(lines)
        routines = data.get("routines", [])
        if routines:
            for r in routines[:5]:
                lines.append(f"- {r}")
        else:
            lines.append("パターン分析中...")
        return "\n".join(lines)

    def _format_social(self, data: dict, granularity: str) -> str:
        rels = data.get("relationships", [])
        if not rels:
            return ""
        lines = ["## 人間関係"]
        if granularity == "anonymized":
            lines.append("（匿名化済み）")
            for r in rels[:5]:
                h = r.get("person_hash", "???")
                t = r.get("relationship_type", "unknown")
                s = r.get("strength", 0)
                lines.append(f"- {h[:8]}… ({t}, 強度={s:.1f})")
        else:
            for r in rels[:5]:
                name = r.get("name", r.get("person_id", "unknown"))
                lines.append(f"- {name}")
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════════
# 3. Boundary Case Resolver
# ════════════════════════════════════════════════════════════════════════════════

class BoundaryCaseResolver:
    """
    複数カテゴリにまたがるデータの開示判定。
    原則: 最も制限的なカテゴリのルールを適用。
    """

    # カテゴリの制限優先度（高い = より制限的に扱う）
    RESTRICTION_PRIORITY = {
        DisclosureCategory.HEALTH_VITALS:       7,
        DisclosureCategory.LOCATION_MOVEMENT:   6,
        DisclosureCategory.SOCIAL_GRAPH:        5,
        DisclosureCategory.EPISODIC_MEMORY:     4,
        DisclosureCategory.EMOTIONAL_STATE:     3,
        DisclosureCategory.BEHAVIORAL_PATTERNS: 2,
        DisclosureCategory.INTERESTS_VALUES:    1,
        DisclosureCategory.CORE_IDENTITY:       0,
    }

    GRANULARITY_ORDER = {
        GranularityLevel.HIDDEN:     3,
        GranularityLevel.ANONYMIZED: 2,
        GranularityLevel.SUMMARY:    1,
        GranularityLevel.FULL:       0,
    }

    @classmethod
    def resolve(
        cls,
        involved_categories: List[str],
        scope: DisclosureScope,
    ) -> CategoryRule:
        """
        複数カテゴリに関わるデータの最終開示ルールを決定。
        最も制限的な粒度を適用する。
        """
        most_restrictive = CategoryRule(GranularityLevel.FULL)
        max_restriction = -1

        for cat_key in involved_categories:
            rule = scope.get_rule(cat_key)
            restriction = cls.GRANULARITY_ORDER.get(rule.granularity, 0)
            if restriction > max_restriction:
                max_restriction = restriction
                most_restrictive = rule

        return most_restrictive


# ════════════════════════════════════════════════════════════════════════════════
# 4. CLI デモ
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from capability_token import SCOPE_TEMPLATES
    from soul_schema import load_soul

    print("═══ Ghost-Printer C4: Permission Gateway Demo ═══\n")

    # SOULを読み込み
    soul = load_soul("data/soul.json")
    print(f"SOUL loaded: {soul['stats']['total_episodes']} episodes\n")

    # TokenManagerとGatewayを初期化
    manager = TokenManager(secret_key="ghost-printer-demo-key-2026")
    gateway = PermissionGateway(manager)

    # 各スコープでフィルタリングを実行
    for scope_name, scope in SCOPE_TEMPLATES.items():
        token = manager.issue(
            issuer="device_gp001",
            subject=f"ai_{scope_name}",
            scope=scope,
        )

        result = gateway.filter_soul(soul, token)
        print(f"── {scope.name} ──")
        print(f"   Status: {'✅ OK' if result.success else '❌ ' + result.error}")
        print(f"   Disclosed: {result.stats.get('disclosed', 0)}/{result.stats.get('total_categories', 0)} categories")
        print(f"   Categories: {', '.join(result.categories.keys()) or '(none)'}")
        print()

    # System Prompt生成デモ
    print("═══ System Prompt Generation (Claude Personal) ═══\n")
    cp_scope = SCOPE_TEMPLATES["claude_personal"]
    cp_token = manager.issue(issuer="device_gp001", subject="claude", scope=cp_scope)
    prompt = gateway.soul_to_prompt(soul, cp_token)
    print(prompt)
    print(f"\n═══ Prompt length: {len(prompt)} chars ═══")

    # Boundary Case Resolution デモ
    print("\n═══ Boundary Case Resolution ═══\n")
    test_cases = [
        ("仕事の会話から推測される健康状態", ["episodic_memory", "health_vitals"]),
        ("Aさんとの会話", ["episodic_memory", "social_graph"]),
        ("場所から推測される行動パターン", ["behavioral_patterns", "location_movement"]),
        ("睡眠ログ", ["emotional_state", "health_vitals"]),
    ]
    for case_name, cats in test_cases:
        rule = BoundaryCaseResolver.resolve(cats, cp_scope)
        print(f"  {case_name}:")
        print(f"    Categories: {cats}")
        print(f"    → Resolved: {rule.granularity.value} (days_limit={rule.days_limit})")
        print()
