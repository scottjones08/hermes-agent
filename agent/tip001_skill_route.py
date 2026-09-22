"""
TIP-001: Lightweight Jev hop classification and skill injection.

This module provides:
- Lightweight regex-based classification (Jev cheap hop)
- Evidence/receipt recording to SQLite for Trust/Evidence
- Route-based skill injection for Focus asks
"""

import re
import sqlite3
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============================================================================
# Configuration
# ============================================================================

# Route definitions with regex patterns and associated skills
ROUTE_PATTERNS = {
    "coding": {
        "patterns": [
            r'\b(code|program|debug|fix|implement|write)\b',
            r'\b(python|javascript|typescript|java|c\+\+|go|rust)\b',
            r'\b(function|class|module|import|export)\b',
            r'\b(bug|error|exception|stack trace)\b',
        ],
        "skills_to_inject": ["coding"],
        "confidence_boost": 0.1,
    },
    "github": {
        "patterns": [
            r'\b(github|git|pull request|pr|branch|commit|merge)\b',
            r'\b(issue|repository|repo|fork|clone)\b',
            r'\b(code review|review|approve|reject)\b',
        ],
        "skills_to_inject": ["github"],
        "confidence_boost": 0.1,
    },
    "kanban": {
        "patterns": [
            r'\b(kanban|task|board|card|todo)\b',
            r'\b(assign|priority|status|done)\b',
        ],
        "skills_to_inject": ["kanban"],
        "confidence_boost": 0.1,
    },
    "research": {
        "patterns": [
            r'\b(search|find|lookup|research)\b',
            r'\b(what is|how does|explain)\b',
            r'\b(paper|article|document)\b',
        ],
        "skills_to_inject": ["research"],
        "confidence_boost": 0.1,
    },
    "skills": {
        "patterns": [
            r'\b(skill|command|tool|feature)\b',
            r'\b(load|use|enable|disable)\b',
        ],
        "skills_to_inject": ["skills"],
        "confidence_boost": 0.1,
    },
}

DEFAULT_ROUTE = "general"
DEFAULT_SKILLS: List[str] = []
BASE_CONFIDENCE = 0.5


# ============================================================================
# Database for Trust/Evidence receipts
# ============================================================================

def _get_db_path() -> str:
    """Get the path to the TIP-001 evidence database."""
    # Store in hermes data directory
    hermes_home = os.path.expanduser("~/.hermes")
    db_dir = os.path.join(hermes_home, "data")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "tip001_evidence.db")


def _get_connection() -> sqlite3.Connection:
    """Get a database connection, creating the schema if needed."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Create schema if needed
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tip001_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            turn_id INTEGER NOT NULL,
            timestamp REAL NOT NULL,
            user_message TEXT NOT NULL,
            route TEXT NOT NULL,
            confidence REAL NOT NULL,
            skills_injected TEXT NOT NULL,
            main_model TEXT NOT NULL,
            classification_json TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_receipts_session ON tip001_receipts(session_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_receipts_route ON tip001_receipts(route)
    """)
    conn.commit()
    
    return conn


# ============================================================================
# Classification Logic
# ============================================================================

def classify_route(user_message: str) -> Dict[str, Any]:
    """
    Lightweight Jev hop classification using regex patterns.
    
    Returns a dict with:
    - route: The identified route (e.g., "coding", "github")
    - confidence: Confidence score (0.0-1.0)
    - skills_to_inject: List of skill names to inject
    """
    user_message_lower = user_message.lower()
    
    best_route = DEFAULT_ROUTE
    best_score = 0.0
    
    for route_name, route_config in ROUTE_PATTERNS.items():
        score = 0.0
        patterns = route_config.get("patterns", [])
        
        for pattern in patterns:
            if re.search(pattern, user_message_lower):
                score += 1.0
        
        # Normalize score
        if patterns:
            score = score / len(patterns)
        
        # Apply confidence boost only if there was at least one pattern match
        if score > 0:
            score += route_config.get("confidence_boost", 0.0)
        
        if score > best_score:
            best_score = score
            best_route = route_name
    
    # Calculate final confidence - base only if no matches
    if best_score > 0:
        confidence = min(BASE_CONFIDENCE + best_score * 0.5, 1.0)
    else:
        confidence = BASE_CONFIDENCE
    
    # Get skills to inject
    if best_route in ROUTE_PATTERNS:
        skills = ROUTE_PATTERNS[best_route].get("skills_to_inject", DEFAULT_SKILLS)
    else:
        skills = DEFAULT_SKILLS
    
    return {
        "route": best_route,
        "confidence": confidence,
        "skills_to_inject": skills,
    }


def get_skill_injection_context(user_message: str) -> Dict[str, Any]:
    """
    Main entry point for TIP-001 classification.
    
    Classifies the user message and returns context for skill injection.
    """
    return classify_route(user_message)


# ============================================================================
# Evidence Recording
# ============================================================================

def record_tip001_receipt(
    session_id: str,
    turn_id: int,
    user_message: str,
    classification: Dict[str, Any],
    main_model: str,
) -> None:
    """
    Record a TIP-001 receipt for Trust/Evidence.
    
    This creates an immutable record of the classification decision
    for audit and verification purposes.
    """
    try:
        conn = _get_connection()
        import json
        conn.execute(
            """
            INSERT INTO tip001_receipts 
            (session_id, turn_id, timestamp, user_message, route, confidence, 
             skills_injected, main_model, classification_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                turn_id,
                time.time(),
                user_message[:1000],  # Truncate for storage
                classification.get("route", "general"),
                classification.get("confidence", 0.0),
                json.dumps(classification.get("skills_to_inject", [])),
                main_model,
                json.dumps(classification),
            )
        )
        conn.commit()
        conn.close()
    except Exception as e:
        # Evidence recording should never block the main flow
        import logging
        logging.getLogger(__name__).debug("Failed to record TIP-001 receipt: %s", e)


def get_receipts_by_route(route: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Query TIP-001 receipts, optionally filtered by route.
    
    Used for Trust/Evidence UI filtering by skill-route.
    """
    conn = _get_connection()
    import json
    
    if route:
        rows = conn.execute(
            """
            SELECT * FROM tip001_receipts 
            WHERE route = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
            """,
            (route, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM tip001_receipts 
            ORDER BY timestamp DESC 
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
    
    conn.close()
    
    results = []
    for row in rows:
        results.append({
            "id": row["id"],
            "session_id": row["session_id"],
            "turn_id": row["turn_id"],
            "timestamp": row["timestamp"],
            "user_message": row["user_message"],
            "route": row["route"],
            "confidence": row["confidence"],
            "skills_injected": json.loads(row["skills_injected"]),
            "main_model": row["main_model"],
        })
    
    return results


def get_route_stats() -> Dict[str, Any]:
    """
    Get aggregate statistics about TIP-001 routing.
    
    Used for Trust/Evidence dashboard.
    """
    conn = _get_connection()
    
    # Count by route
    route_counts = conn.execute(
        """
        SELECT route, COUNT(*) as count, AVG(confidence) as avg_confidence
        FROM tip001_receipts
        GROUP BY route
        """
    ).fetchall()
    
    conn.close()
    
    stats = {
        "total_receipts": sum(r["count"] for r in route_counts),
        "by_route": {},
    }
    
    for row in route_counts:
        stats["by_route"][row["route"]] = {
            "count": row["count"],
            "avg_confidence": row["avg_confidence"],
        }
    
    return stats


# ============================================================================
# Golden Tests
# ============================================================================

def run_golden_tests() -> Dict[str, Any]:
    """
    Run golden tests to verify classification accuracy.
    
    Returns a dict with test results.
    """
    test_cases = [
        # (user_message, expected_route)
        ("write a python function to sort a list", "coding"),
        ("create a pull request on github", "github"),
        ("add a task to my kanban board", "kanban"),
        ("search for information about AI", "research"),
        ("how do I use the skills command", "skills"),
        ("hello, how are you?", "general"),
    ]
    
    passed = 0
    failed = 0
    results = []
    
    for message, expected_route in test_cases:
        result = classify_route(message)
        actual_route = result["route"]
        
        if actual_route == expected_route:
            passed += 1
            results.append({
                "message": message,
                "expected": expected_route,
                "actual": actual_route,
                "status": "PASS",
            })
        else:
            failed += 1
            results.append({
                "message": message,
                "expected": expected_route,
                "actual": actual_route,
                "status": "FAIL",
            })
    
    return {
        "passed": passed,
        "failed": failed,
        "total": len(test_cases),
        "results": results,
    }


if __name__ == "__main__":
    # Run golden tests when executed directly
    import json
    results = run_golden_tests()
    print(json.dumps(results, indent=2))
