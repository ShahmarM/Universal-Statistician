"""Natural-language planning layer: turns a question into a
QuestionInterpretation (core/query_plan.py), never into data.

Kept as its own top-level package, parallel to providers/, because it
depends on an optional external LLM client — isolated here per section 22's
requirement that core statistical functionality stay operational without
one (see rule_based_planner.py).
"""
