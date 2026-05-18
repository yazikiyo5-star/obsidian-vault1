"""
Ghost-Printer C1 Integration Example

Demonstrates how the Permission Gateway integrates with:
- soul_engine.py (SOUL updates)
- soul_to_prompt.py (System prompt generation)
- External AI (Claude, etc.)

This is a reference implementation for Track C implementation.
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

# Hypothetical imports (assumes c1_disclosure_spec is in the project)
from c1_disclosure_spec import (
    TokenManager,
    PermissionGateway,
    DisclosureCategory,
    GranularityLevel,
    DisclosureScope,
    SCOPE_TEMPLATES,
)


class SoulDisclosureManager:
    """
    High-level manager that coordinates SOUL updates with disclosure control.

    Acts as a bridge between soul_engine and external AI integrations.
    """

    def __init__(self, secret_key: str):
        self.token_manager = TokenManager(secret_key)
        self.gateway = PermissionGateway(self.token_manager)

        # Store active tokens (in production, use database)
        self.active_tokens = {}

    def grant_scope_to_ai(
        self,
        user_id: str,
        ai_name: str,
        scope_template: str,
        expires_in_hours: int = 24,
    ):
        """
        Grant a scope to an AI/app.

        Args:
            user_id: User identifier
            ai_name: "claude", "work_assistant", "health_coach", etc.
            scope_template: Key in SCOPE_TEMPLATES dict
            expires_in_hours: Token validity period

        Returns:
            token_id (for storage/revocation)
        """
        scope = SCOPE_TEMPLATES[scope_template]

        token = self.token_manager.generate_token(
            issuer=user_id,
            subject=ai_name,
            scope=scope,
            expires_in_hours=expires_in_hours,
        )

        token_id = token.nonce
        self.active_tokens[token_id] = token

        print(f"✓ Granted '{scope_template}' to {ai_name}")
        print(f"  Token expires: {token.expires_at}")

        return token_id

    def get_filtered_soul_for_ai(
        self,
        soul: Dict[str, Any],
        ai_name: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get SOUL data filtered for a specific AI.

        Looks up the token for that AI and applies Permission Gateway.

        Args:
            soul: Complete SOUL dictionary
            ai_name: AI identifier
            user_id: User identifier

        Returns:
            Filtered SOUL (only accessible categories)
            None if no valid token exists
        """
        # Find token for this AI
        token = self._find_token_for_ai(user_id, ai_name)

        if token is None:
            print(f"✗ No valid token for {ai_name}")
            return None

        if not self.token_manager.verify_token(token):
            print(f"✗ Token invalid or expired for {ai_name}")
            return None

        # Filter through gateway
        filtered = self.gateway.filter_soul_by_token(soul, token)

        return filtered

    def generate_system_prompt_for_ai(
        self,
        user_name: str,
        soul: Dict[str, Any],
        ai_name: str,
        user_id: str,
    ) -> Optional[str]:
        """
        Generate a personalized System Prompt for an AI.

        Uses filtered SOUL data to construct the prompt.
        """
        filtered_soul = self.get_filtered_soul_for_ai(soul, ai_name, user_id)

        if filtered_soul is None:
            return self._fallback_prompt(user_name)

        # Build prompt from filtered categories
        categories = filtered_soul.get("categories", {})

        prompt_parts = [
            f"You are a personal assistant for {user_name}.",
            "",
            "Below is their SOUL profile (shared data within their disclosure scope):",
            "",
        ]

        # Core Identity
        if "core_identity" in categories:
            ci_data = categories["core_identity"]["data"]
            prompt_parts.append("=== Personality Profile ===")
            for trait, distribution in ci_data.items():
                mu = distribution.get("mu", 0.5)
                prompt_parts.append(f"- {trait}: {mu:.2f}/1.0")
            prompt_parts.append("")

        # Episodic Memory
        if "episodic_memory" in categories:
            em_data = categories["episodic_memory"]["data"]
            recent_count = len(em_data.get("recent", []))
            prompt_parts.append("=== Recent Events ===")
            prompt_parts.append(
                f"Recent episodes (last {categories['episodic_memory']['days_limit']} days): {recent_count}"
            )
            # Could expand to actual summaries here
            prompt_parts.append("")

        # Behavioral Patterns
        if "behavioral_patterns" in categories:
            bp_data = categories["behavioral_patterns"]["data"]
            prompt_parts.append("=== Behavioral Patterns ===")
            if "avg_sleep_hours" in bp_data:
                prompt_parts.append(f"- Avg sleep: {bp_data['avg_sleep_hours']} hours")
            if "sleep_regularity" in bp_data:
                prompt_parts.append(f"- Sleep regularity: {bp_data['sleep_regularity']:.2f}")
            prompt_parts.append("")

        # Interests
        if "interests_values" in categories:
            iv_data = categories["interests_values"]["data"]
            prompt_parts.append("=== Interests & Values ===")
            if "interests" in iv_data:
                for interest, weight in iv_data["interests"].items():
                    prompt_parts.append(f"- {interest}: {weight:.2f}")
            prompt_parts.append("")

        # Social Graph (if anonymized)
        if "social_graph" in categories:
            sg_data = categories["social_graph"]["data"]
            granularity = categories["social_graph"]["granularity"]
            prompt_parts.append("=== Social Connections ===")
            prompt_parts.append(
                f"Social graph available (granularity: {granularity})"
            )
            if "relationships" in sg_data:
                prompt_parts.append(
                    f"Active relationships: {len(sg_data['relationships'])}"
                )
            prompt_parts.append("")

        # Note hidden categories
        hidden_cats = []
        for cat_name in DisclosureCategory.__members__:
            if cat_name not in categories:
                hidden_cats.append(cat_name)

        if hidden_cats:
            prompt_parts.append("=== Scope Limitations ===")
            prompt_parts.append(
                f"Not shared: {', '.join(hidden_cats)}"
            )
            prompt_parts.append("")

        prompt_parts.append("Use this information to provide personalized, context-aware assistance.")
        prompt_parts.append("Always respect privacy — never ask for non-shared categories.")

        return "\n".join(prompt_parts)

    def revoke_token(self, token_id: str):
        """Revoke a token immediately."""
        if token_id in self.active_tokens:
            del self.active_tokens[token_id]
            print(f"✓ Token {token_id[:8]}... revoked")
        else:
            print(f"✗ Token {token_id[:8]}... not found")

    def revoke_all_tokens_for_ai(self, user_id: str, ai_name: str):
        """Revoke all tokens for a specific AI."""
        tokens_to_remove = [
            tid for tid, token in self.active_tokens.items()
            if token.subject == ai_name and token.issuer == user_id
        ]

        for tid in tokens_to_remove:
            self.revoke_token(tid)

        if tokens_to_remove:
            print(f"✓ Revoked {len(tokens_to_remove)} token(s) for {ai_name}")
        else:
            print(f"✗ No tokens found for {ai_name}")

    def _find_token_for_ai(self, user_id: str, ai_name: str):
        """Find active token for a given AI."""
        for token in self.active_tokens.values():
            if token.issuer == user_id and token.subject == ai_name:
                if token.is_valid():
                    return token
        return None

    def _fallback_prompt(self, user_name: str) -> str:
        """Generate minimal prompt when no SOUL is available."""
        return f"You are a helpful assistant for {user_name}."


# ════════════════════════════════════════════════════════════════════════════════
# Example: Multi-AI Integration
# ════════════════════════════════════════════════════════════════════════════════

def demo_multi_ai_integration():
    """
    Demonstrates a user with 3 different AIs, each with different scope levels.
    """

    print("=" * 70)
    print("Ghost-Printer C1 Integration Demo")
    print("=" * 70)
    print()

    # Initialize manager
    manager = SoulDisclosureManager(secret_key="demo_secret_key_12345")
    user_id = "user_12345"
    user_name = "Alice"

    # Sample SOUL (simplified)
    soul = {
        "version": "0.1.0",
        "owner_hash": user_id,
        "core_identity": {
            "openness": {"mu": 0.72, "sigma": 0.15},
            "extraversion": {"mu": 0.35, "sigma": 0.20},
            "conscientiousness": {"mu": 0.68, "sigma": 0.18},
        },
        "episodic_memory": {
            "recent": [
                {
                    "timestamp": (
                        datetime.now(timezone.utc) - timedelta(days=5)
                    ).isoformat(),
                    "summary": "Had productive meeting with team",
                    "importance": 0.7,
                    "context": {"location_category": "office"},
                },
                {
                    "timestamp": (
                        datetime.now(timezone.utc) - timedelta(days=10)
                    ).isoformat(),
                    "summary": "Went hiking with friend",
                    "importance": 0.5,
                    "context": {"location_category": "nature"},
                },
            ],
            "compressed": [],
        },
        "behavioral_patterns": {
            "avg_sleep_hours": 7.2,
            "sleep_regularity": 0.82,
            "exercise_frequency": "4x per week",
        },
        "health_vitals": {
            "resting_heart_rate": 65,
            "sleep_quality_avg": 0.75,
            "stress_level_avg": 0.4,
        },
        "interests_values": {
            "interests": {
                "technology": 0.9,
                "music": 0.7,
                "hiking": 0.6,
            },
        },
        "social_graph": {
            "relationships": [
                {"person_id": "alice_friend_1", "name": "Bob", "strength": 0.9},
                {"person_id": "alice_colleague_1", "name": "Charlie", "strength": 0.6},
            ],
        },
    }

    # ─── Grant scopes to different AIs ───
    print("STEP 1: Granting Scopes")
    print("-" * 70)

    manager.grant_scope_to_ai(user_id, "claude", "claude_personal", 24)
    manager.grant_scope_to_ai(user_id, "work_assistant", "work_assistant", 24)
    manager.grant_scope_to_ai(user_id, "health_coach", "health_coach", 24)

    print()

    # ─── Get filtered SOUL for each AI ───
    print("STEP 2: Fetching Filtered SOUL for Each AI")
    print("-" * 70)

    ais = ["claude", "work_assistant", "health_coach"]

    for ai_name in ais:
        print(f"\n{ai_name.upper()}:")
        filtered = manager.get_filtered_soul_for_ai(soul, ai_name, user_id)

        if filtered:
            categories = filtered.get("categories", {})
            print(f"  Accessible categories: {list(categories.keys())}")

            # Show granularity levels
            for cat, data in categories.items():
                granularity = data.get("granularity", "?")
                days_limit = data.get("days_limit", "∞")
                print(f"    - {cat}: {granularity} ({days_limit} days)")

    print()

    # ─── Generate System Prompts ───
    print("STEP 3: Generating System Prompts")
    print("-" * 70)

    for ai_name in ais:
        print(f"\n--- System Prompt for {ai_name} ---")
        prompt = manager.generate_system_prompt_for_ai(
            user_name, soul, ai_name, user_id
        )
        print(prompt)
        print()

    # ─── Demonstrate revocation ───
    print("STEP 4: Token Revocation (Emergency Stop)")
    print("-" * 70)
    print("Revoking all tokens for 'work_assistant'...")
    manager.revoke_all_tokens_for_ai(user_id, "work_assistant")

    print("\nAttempting to fetch SOUL for work_assistant...")
    filtered = manager.get_filtered_soul_for_ai(soul, "work_assistant", user_id)

    print()
    print("=" * 70)
    print("Demo complete!")


if __name__ == "__main__":
    try:
        from c1_disclosure_spec import DisclosureCategory
        demo_multi_ai_integration()
    except ImportError:
        print("Note: c1_disclosure_spec module needs to be in PYTHONPATH")
        print("Run from ghost-printer-a1 directory:")
        print("  python3 specs/integration_example.py")
