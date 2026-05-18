"""
Ghost-Printer C2 — Capability Token 生成・検証モジュール

HMAC-SHA256署名付きトークンで「誰に・何を・いつまで」開示するかを制御する。
JWT互換ペイロード構造を持ち、将来のOAuth/JWT移行に備える。

使い方:
    manager = TokenManager(secret_key="your-secret")
    token = manager.issue(issuer="device_001", subject="claude", scope_name="claude_personal")
    ok = manager.verify(token)
    revoked = manager.revoke(token.token_id)
"""

import json
import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List, Set


# ════════════════════════════════════════════════════════════════════════════════
# 1. 開示カテゴリ & 粒度（c1_disclosure_spec.py から独立モジュールとして再定義）
# ════════════════════════════════════════════════════════════════════════════════

class DisclosureCategory(str, Enum):
    """SOULの8大カテゴリ"""
    CORE_IDENTITY = "core_identity"
    EPISODIC_MEMORY = "episodic_memory"
    EMOTIONAL_STATE = "emotional_state"
    BEHAVIORAL_PATTERNS = "behavioral_patterns"
    HEALTH_VITALS = "health_vitals"
    LOCATION_MOVEMENT = "location_movement"
    SOCIAL_GRAPH = "social_graph"
    INTERESTS_VALUES = "interests_values"


class GranularityLevel(str, Enum):
    """開示粒度の4段階"""
    FULL = "full"
    SUMMARY = "summary"
    ANONYMIZED = "anonymized"
    HIDDEN = "hidden"


# ════════════════════════════════════════════════════════════════════════════════
# 2. スコープ定義
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class CategoryRule:
    """カテゴリごとの開示ルール"""
    granularity: GranularityLevel
    days_limit: Optional[int] = None  # None = 無制限

    def to_dict(self) -> dict:
        return {"granularity": self.granularity.value, "days_limit": self.days_limit}

    @classmethod
    def from_dict(cls, d: dict) -> "CategoryRule":
        return cls(
            granularity=GranularityLevel(d["granularity"]),
            days_limit=d.get("days_limit"),
        )


@dataclass
class DisclosureScope:
    """開示スコープ: カテゴリごとの (粒度, 期間) を定義"""
    name: str
    description: str = ""
    categories: Dict[str, CategoryRule] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "categories": {k: v.to_dict() for k, v in self.categories.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DisclosureScope":
        cats = {}
        for k, v in d.get("categories", {}).items():
            cats[k] = CategoryRule.from_dict(v)
        return cls(name=d["name"], description=d.get("description", ""), categories=cats)

    def get_rule(self, category: str) -> CategoryRule:
        """指定カテゴリのルールを返す。未定義はHIDDEN"""
        return self.categories.get(category, CategoryRule(GranularityLevel.HIDDEN))


# ── スコープテンプレート ──

SCOPE_TEMPLATES: Dict[str, DisclosureScope] = {
    "claude_personal": DisclosureScope(
        name="Claude Personal",
        description="パーソナルアシスタント向け。性格と最近の文脈を共有。位置情報・健康データなし。",
        categories={
            "core_identity":       CategoryRule(GranularityLevel.FULL),
            "episodic_memory":     CategoryRule(GranularityLevel.FULL, 90),
            "emotional_state":     CategoryRule(GranularityLevel.FULL, 7),
            "behavioral_patterns": CategoryRule(GranularityLevel.SUMMARY),
            "interests_values":    CategoryRule(GranularityLevel.FULL),
            "health_vitals":       CategoryRule(GranularityLevel.HIDDEN),
            "location_movement":   CategoryRule(GranularityLevel.HIDDEN),
            "social_graph":        CategoryRule(GranularityLevel.ANONYMIZED, 30),
        },
    ),
    "work_assistant": DisclosureScope(
        name="Work Assistant",
        description="仕事効率化向け。キャリア・仕事の文脈のみ。私生活は非開示。",
        categories={
            "core_identity":       CategoryRule(GranularityLevel.SUMMARY),
            "episodic_memory":     CategoryRule(GranularityLevel.SUMMARY, 30),
            "emotional_state":     CategoryRule(GranularityLevel.HIDDEN),
            "behavioral_patterns": CategoryRule(GranularityLevel.SUMMARY),
            "interests_values":    CategoryRule(GranularityLevel.SUMMARY),
            "health_vitals":       CategoryRule(GranularityLevel.HIDDEN),
            "location_movement":   CategoryRule(GranularityLevel.HIDDEN),
            "social_graph":        CategoryRule(GranularityLevel.HIDDEN),
        },
    ),
    "health_coach": DisclosureScope(
        name="Health Coach",
        description="健康管理向け。バイタル・行動パターン・感情。社会関係は非開示。",
        categories={
            "core_identity":       CategoryRule(GranularityLevel.SUMMARY),
            "episodic_memory":     CategoryRule(GranularityLevel.HIDDEN),
            "emotional_state":     CategoryRule(GranularityLevel.FULL, 30),
            "behavioral_patterns": CategoryRule(GranularityLevel.FULL),
            "health_vitals":       CategoryRule(GranularityLevel.FULL),
            "interests_values":    CategoryRule(GranularityLevel.SUMMARY),
            "location_movement":   CategoryRule(GranularityLevel.HIDDEN),
            "social_graph":        CategoryRule(GranularityLevel.HIDDEN),
        },
    ),
    "minimal": DisclosureScope(
        name="Minimal",
        description="最小限の開示。コアアイデンティティの要約のみ。",
        categories={
            "core_identity":       CategoryRule(GranularityLevel.SUMMARY),
            "episodic_memory":     CategoryRule(GranularityLevel.HIDDEN),
            "emotional_state":     CategoryRule(GranularityLevel.HIDDEN),
            "behavioral_patterns": CategoryRule(GranularityLevel.HIDDEN),
            "health_vitals":       CategoryRule(GranularityLevel.HIDDEN),
            "location_movement":   CategoryRule(GranularityLevel.HIDDEN),
            "social_graph":        CategoryRule(GranularityLevel.HIDDEN),
            "interests_values":    CategoryRule(GranularityLevel.HIDDEN),
        },
    ),
}


# ════════════════════════════════════════════════════════════════════════════════
# 3. Capability Token
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class CapabilityToken:
    """
    AIやアプリに対して発行される権限トークン。

    JWT互換ペイロード:
      iss: 発行者（デバイスID）
      sub: 対象（"claude", "health-coach-app" 等）
      scope: DisclosureScope
      iat: 発行時刻 (Unix timestamp)
      exp: 失効時刻 (Unix timestamp)
      jti: トークンID（UUID v4）
      one_time: ワンタイム利用フラグ
    """
    token_id: str                   # jti: UUID v4
    issuer: str                     # iss
    subject: str                    # sub
    scope: DisclosureScope
    issued_at: datetime             # iat
    expires_at: datetime            # exp
    one_time: bool = False
    used: bool = False              # ワンタイムトークンの消費フラグ
    signature: str = ""

    def jwt_payload(self) -> dict:
        """JWT互換ペイロード（署名対象）"""
        return {
            "iss": self.issuer,
            "sub": self.subject,
            "scope": self.scope.to_dict(),
            "iat": int(self.issued_at.timestamp()),
            "exp": int(self.expires_at.timestamp()),
            "jti": self.token_id,
            "one_time": self.one_time,
        }

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    def is_consumed(self) -> bool:
        return self.one_time and self.used

    def is_valid(self) -> bool:
        return not self.is_expired() and not self.is_consumed()

    def to_dict(self) -> dict:
        d = self.jwt_payload()
        d["signature"] = self.signature
        d["used"] = self.used
        return d

    @classmethod
    def from_dict(cls, d: dict, scope: DisclosureScope) -> "CapabilityToken":
        return cls(
            token_id=d["jti"],
            issuer=d["iss"],
            subject=d["sub"],
            scope=scope,
            issued_at=datetime.fromtimestamp(d["iat"], tz=timezone.utc),
            expires_at=datetime.fromtimestamp(d["exp"], tz=timezone.utc),
            one_time=d.get("one_time", False),
            used=d.get("used", False),
            signature=d.get("signature", ""),
        )


# ════════════════════════════════════════════════════════════════════════════════
# 4. TokenManager — 生成・検証・失効管理
# ════════════════════════════════════════════════════════════════════════════════

class TokenError(Exception):
    """トークン関連のエラー"""
    pass


class TokenManager:
    """
    Capability Token のライフサイクル管理。

    - issue(): 新規トークン発行
    - verify(): 署名 + 有効期限 + 失効リスト検証
    - revoke(): トークン失効
    - consume(): ワンタイムトークンの消費
    - list_active(): アクティブトークン一覧
    - save/load(): 永続化（JSON）
    """

    def __init__(self, secret_key: str):
        self.secret_key = secret_key
        self._tokens: Dict[str, CapabilityToken] = {}  # token_id → token
        self._revoked: Set[str] = set()                 # 失効済みtoken_id

    # ── 発行 ──

    def issue(
        self,
        issuer: str,
        subject: str,
        scope: DisclosureScope,
        expires_in_hours: int = 24,
        one_time: bool = False,
    ) -> CapabilityToken:
        """新しいCapability Tokenを発行する"""
        now = datetime.now(timezone.utc)
        token = CapabilityToken(
            token_id=str(uuid.uuid4()),
            issuer=issuer,
            subject=subject,
            scope=scope,
            issued_at=now,
            expires_at=now + timedelta(hours=expires_in_hours),
            one_time=one_time,
        )
        token.signature = self._sign(token)
        self._tokens[token.token_id] = token
        return token

    # ── 署名 ──

    def _sign(self, token: CapabilityToken) -> str:
        """HMAC-SHA256でペイロードに署名"""
        payload = json.dumps(token.jwt_payload(), sort_keys=True, ensure_ascii=False)
        return hmac.new(
            self.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ── 検証 ──

    def verify(self, token: CapabilityToken) -> tuple[bool, str]:
        """
        トークンを検証する。

        Returns:
            (is_valid, reason)
        """
        # 1. 失効チェック
        if token.token_id in self._revoked:
            return False, "revoked"

        # 2. 有効期限チェック
        if token.is_expired():
            return False, "expired"

        # 3. ワンタイム消費チェック
        if token.is_consumed():
            return False, "already_consumed"

        # 4. 署名検証
        expected = self._sign(token)
        if not hmac.compare_digest(token.signature, expected):
            return False, "invalid_signature"

        return True, "valid"

    # ── 失効 ──

    def revoke(self, token_id: str) -> bool:
        """トークンを失効させる"""
        if token_id in self._tokens:
            self._revoked.add(token_id)
            return True
        return False

    # ── ワンタイム消費 ──

    def consume(self, token: CapabilityToken) -> tuple[bool, str]:
        """ワンタイムトークンを消費する"""
        valid, reason = self.verify(token)
        if not valid:
            return False, reason

        if token.one_time:
            token.used = True
            if token.token_id in self._tokens:
                self._tokens[token.token_id].used = True

        return True, "consumed"

    # ── 一覧 ──

    def list_active(self) -> List[CapabilityToken]:
        """有効なトークンの一覧"""
        return [
            t for t in self._tokens.values()
            if t.token_id not in self._revoked and t.is_valid()
        ]

    def list_all(self) -> List[Dict[str, Any]]:
        """全トークン（ステータス付き）"""
        result = []
        for t in self._tokens.values():
            status = "active"
            if t.token_id in self._revoked:
                status = "revoked"
            elif t.is_expired():
                status = "expired"
            elif t.is_consumed():
                status = "consumed"
            result.append({"token": t.to_dict(), "status": status})
        return result

    # ── 永続化 ──

    def save(self, path: str) -> None:
        """トークンストアをJSONに保存"""
        data = {
            "tokens": {tid: t.to_dict() for tid, t in self._tokens.items()},
            "revoked": list(self._revoked),
            "scopes": {},
        }
        # スコープもトークンに紐づけて保存
        for tid, t in self._tokens.items():
            data["scopes"][tid] = t.scope.to_dict()

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, path: str) -> None:
        """トークンストアをJSONから復元"""
        p = Path(path)
        if not p.exists():
            return

        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._revoked = set(data.get("revoked", []))
        scopes = data.get("scopes", {})

        for tid, td in data.get("tokens", {}).items():
            scope_dict = scopes.get(tid, td.get("scope", {}))
            scope = DisclosureScope.from_dict(scope_dict)
            token = CapabilityToken.from_dict(td, scope)
            self._tokens[tid] = token

    # ── ユーティリティ ──

    def get_token(self, token_id: str) -> Optional[CapabilityToken]:
        """IDでトークンを取得"""
        return self._tokens.get(token_id)

    @property
    def token_count(self) -> int:
        return len(self._tokens)

    @property
    def active_count(self) -> int:
        return len(self.list_active())


# ════════════════════════════════════════════════════════════════════════════════
# 5. CLI デモ
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═══ Ghost-Printer C2: Capability Token Demo ═══\n")

    # 秘密鍵でマネージャーを初期化
    manager = TokenManager(secret_key="ghost-printer-demo-key-2026")

    # 各スコープでトークンを発行
    for scope_name, scope in SCOPE_TEMPLATES.items():
        token = manager.issue(
            issuer="device_gp001",
            subject=f"ai_{scope_name}",
            scope=scope,
            expires_in_hours=24,
        )
        print(f"✅ Issued: {scope_name}")
        print(f"   Token ID: {token.token_id}")
        print(f"   Subject:  {token.subject}")
        print(f"   Expires:  {token.expires_at.isoformat()}")

        # 検証
        valid, reason = manager.verify(token)
        print(f"   Verify:   {valid} ({reason})")
        print()

    # ワンタイムトークンのテスト
    print("── One-Time Token Test ──")
    ot_token = manager.issue(
        issuer="device_gp001",
        subject="emergency_ai",
        scope=SCOPE_TEMPLATES["claude_personal"],
        expires_in_hours=1,
        one_time=True,
    )
    print(f"Issued one-time token: {ot_token.token_id}")

    ok1, r1 = manager.consume(ot_token)
    print(f"First consume:  {ok1} ({r1})")
    ok2, r2 = manager.consume(ot_token)
    print(f"Second consume: {ok2} ({r2})")
    print()

    # 失効テスト
    print("── Revocation Test ──")
    active = manager.list_active()
    if active:
        target = active[0]
        print(f"Revoking: {target.token_id} ({target.subject})")
        manager.revoke(target.token_id)
        valid, reason = manager.verify(target)
        print(f"After revoke: valid={valid}, reason={reason}")
    print()

    # 永続化テスト
    store_path = "data/token_store.json"
    manager.save(store_path)
    print(f"✅ Token store saved: {store_path}")

    # 復元テスト
    manager2 = TokenManager(secret_key="ghost-printer-demo-key-2026")
    manager2.load(store_path)
    print(f"✅ Token store loaded: {manager2.token_count} tokens, {manager2.active_count} active")

    print(f"\n═══ Summary: {manager.token_count} total, {manager.active_count} active ═══")
