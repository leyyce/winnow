"""
ORM model registry — import all models here so SQLAlchemy's mapper
registry is fully populated before Alembic or the application engine
attempts to resolve relationships or generate schema.

Import order respects dependency direction:
  Base → Submission → SubmissionUserSnapshot
                    → ScoringSnapshot → StatusLedger
                    → SubmissionVote
                    → WebhookOutbox

References
----------
* Database design: docs/architecture/05_database_design.md §5.2, §6 step 2–5
"""
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.scoring_snapshot import ScoringSnapshot
from app.models.status_ledger import (
    StatusLedger,
    StatusLedgerStatus,
    SupersedeReason,
    TERMINAL_STATES,
)
from app.models.submission import Submission
from app.models.submission_user_snapshot import SubmissionUserSnapshot
from app.models.submission_vote import SubmissionVote
from app.models.webhook_outbox import OutboxStatus, WebhookOutbox

__all__ = [
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "Submission",
    "SubmissionUserSnapshot",
    "ScoringSnapshot",
    "StatusLedger",
    "StatusLedgerStatus",
    "SupersedeReason",
    "TERMINAL_STATES",
    "SubmissionVote",
    "OutboxStatus",
    "WebhookOutbox",
]
