"""
ProjectConfig ORM model — placeholder for the future DB-backed registry.

This module will hold the SQLAlchemy model for persisting project configurations
in the database once the DB-backed registry approach is adopted
(see docs/architecture/02_architecture_patterns.md §3 'Configuration Source').

For the current prototype, all project configurations are code-based and live
entirely in app/scoring/registry.py. No database migration is needed at this
stage. When the DB-backed approach is introduced:
  - This model stores weights, thresholds, trust-scale bounds, and governance
    tier definitions per project_id as JSONB columns.
  - app/scoring/registry.py is updated to load entries from DB at startup.
  - No other layer needs to change (Rule 7: Iterative Implementation).
"""
