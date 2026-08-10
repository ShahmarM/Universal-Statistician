"""Natural-language planning layer: turns a question into a
QuestionInterpretation (core/query_plan.py), never into data.

Its own package, parallel to providers/, because it depends on an optional
LLM client — isolated so core functionality stays operational without one.
"""
