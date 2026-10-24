"""ANVITRACE Case Explainer — offline, read-only, local-model assistant.

Architecture:
    question -> tool selection (model) -> whitelisted tool runs fixed queries
    -> structured result -> model writes explanation -> VALIDATOR checks facts
    -> answer with citations.  Fallback: deterministic template (always works).
"""
