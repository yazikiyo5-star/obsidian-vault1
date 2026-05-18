# Ghost-Printer Specs Directory

This directory contains the technical specifications for the Ghost-Printer project components.

## Current Specifications

### C1: Selective Self-Disclosure System

**Files:**
- `c1_disclosure_spec.py` — Python implementation module (dataclasses, enums, Permission Gateway logic)
- `c1_disclosure_spec.md` — Comprehensive human-readable specification document

**Overview:**
The C1 specification defines how Ghost-Printer users can selectively control what personal data (SOUL) is shared with external AIs and applications.

**Key Components:**

1. **8 Disclosure Categories**
   - Core Identity (personality traits, values)
   - Episodic Memory (recent events)
   - Emotional State (mood, feelings)
   - Behavioral Patterns (habits, routines)
   - Health/Vitals (sensor data)
   - Location/Movement (GPS, visited places)
   - Social Graph (relationships)
   - Interests/Values (semantic interests)

2. **4 Granularity Levels** — for each category
   - Full: Complete uncompressed data
   - Summary: Aggregated/averaged data
   - Anonymized: PII removed, hashed IDs
   - Hidden: Not disclosed

3. **5 Scope Templates** — Pre-built permission sets
   - Claude Personal: Full identity + recent memory, no location/health
   - Work Assistant: Work context only, no personal/health data
   - Health Coach: Vitals + emotional state, no social/location data
   - Minimal: Core identity summary only
   - Emergency: All data with 1-hour auto-expiry

4. **Boundary Case Analysis** — 7 documented cases
   - How to classify data that spans multiple categories
   - Rule: Apply most restrictive category's permissions

5. **Capability Token Structure**
   - HMAC-SHA256 signed tokens for AI/app authorization
   - Fields: issuer, subject, scope, granted_at, expires_at, nonce, signature
   - Revocable, time-limited, JWT-compatible

6. **Permission Gateway**
   - Central access control component
   - Token verification → Scope extraction → Data filtering
   - Applies granularity transformations (summary, anonymize, etc.)

## Usage

### Python Module Import

```python
from specs.c1_disclosure_spec import (
    DisclosureCategory,
    GranularityLevel,
    DisclosureScope,
    SCOPE_TEMPLATES,
    CapabilityToken,
    TokenManager,
    PermissionGateway,
)

# Create a token for Claude
manager = TokenManager(secret_key="your_secret_key")
token = manager.generate_token(
    issuer="user_id",
    subject="claude",
    scope=SCOPE_TEMPLATES["claude_personal"],
    expires_in_hours=24,
)

# Filter SOUL data through Permission Gateway
gateway = PermissionGateway(manager)
filtered_soul = gateway.filter_soul_by_token(soul_dict, token)
```

### Run Tests

```bash
python3 specs/c1_disclosure_spec.py
```

This will:
1. Generate a sample Capability Token
2. Filter a sample SOUL through Claude Personal scope
3. Demonstrate boundary case analysis

## Integration Points

### With soul_engine.py
The Permission Gateway should be called after SOUL updates:
```python
# After soul_engine.update_soul()
filtered = gateway.filter_soul_by_token(soul, token)
# Return filtered_soul to external AI
```

### With soul_to_prompt.py
Use filtered SOUL to generate System Prompts:
```python
filtered_soul = gateway.filter_soul_by_token(soul, token)
system_prompt = generate_system_prompt(user_name, filtered_soul)
```

### With Management UI (C4)
The spec defines:
- Scope template selection
- Custom scope creation
- Token revocation
- One-time scopes

## Security Model

**Token Verification:**
- HMAC-SHA256 signature on token payload
- Verification on every API call
- Nonce prevents replay attacks

**Data Anonymization:**
- SHA256 hashing with user-specific salt
- Consistent hashes within user (Alice → hash_abc123 always)
- Incomparable across users (different salts)

**Period Limits:**
- Episodic memory filtered by days_limit
- Token auto-expires at expires_at
- Scope can also have independent expiry

## Boundary Cases Reference

Quick lookup for ambiguous multi-category data:

| Data | Primary Category | Rule |
|---|---|---|
| "Tired from work" | health_vitals | Health data is sensitive |
| "Talked with Alice" | social_graph | Personal names/IDs |
| "Daily commute pattern" | location_movement | Location is identifying |
| "Sleep + bad dreams" | health_vitals | Biometric data priority |
| "Alone on weekends" | social_graph | Social relationship pattern |
| "Shared playlist" | social_graph | When shared; interests_values when solo |
| "Recent stress signals" | health_vitals + emotional_state | Most restrictive applies |

**Rule:** When data spans multiple categories, apply the most restrictive category's permissions.

## Future Work (Track C2-C5)

- **C2:** Extended capability token spec with nested permissions
- **C3:** Experiential testing with Claude conversation variations
- **C4:** Management UI design (3 core features)
- **C5:** Peer-to-peer consent & sharing protocol (later track)

## Files in This Repository

```
specs/
├── c1_disclosure_spec.py       # Python implementation (27 KB)
├── c1_disclosure_spec.md       # Full specification (26 KB)
└── README.md                   # This file
```

## References

- Ghost-Printer Handoff Doc: `/sessions/.../ghost_printer_handoff.md`
- SOUL Format: Section 2 of handoff doc
- Hardware Design: Section 3 of handoff doc
- Soul Protocol: Section 4 of handoff doc
- Original Self-Disclosure Design: Section 5 of handoff doc

## Authors & Status

- **Design Lead:** Ghost-Printer Project Team
- **Spec Document:** 2026-04-15
- **Status:** Track C1 (Early Priority) - Ready for C2 planning
- **Test Status:** ✓ Module runs, token generation works, filtering logic tested

---

For detailed implementation guidance, see `c1_disclosure_spec.md` sections 6-9 (Permission Gateway implementation, UI/UX, technical details).
