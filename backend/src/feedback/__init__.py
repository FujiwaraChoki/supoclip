"""Feedback inbox: stored submissions, admin triage and webhook fan-out (docs/feedback.md)."""

CATEGORIES = ("bug", "feature", "general", "sales", "cancellation")
STATUSES = ("open", "in_progress", "resolved")
# Kinds an admin can create. "hermes" is configured on the server only (webhooks.sync_hermes_webhook).
WEBHOOK_KINDS = ("discord", "json")
HERMES_KIND = "hermes"
HERMES_NAME = "Hermes assistant"

CATEGORY_LABELS = {
    "bug": "Bug Report",
    "feature": "Feature Request",
    "general": "General Feedback",
    "sales": "Sales Inquiry",
    "cancellation": "Cancellation Reason",
}

CATEGORY_COLORS = {
    "bug": 0xEF4444,
    "feature": 0x3B82F6,
    "general": 0xA855F7,
    "sales": 0x22C55E,
    "cancellation": 0xF59E0B,
}

# Environment webhooks from before the inbox, and the categories each one received.
ENV_WEBHOOKS = (
    ("DISCORD_FEEDBACK_WEBHOOK_URL", "Feedback (Discord)", ("bug", "feature", "general", "cancellation")),
    ("DISCORD_SALES_WEBHOOK_URL", "Sales (Discord)", ("sales",)),
)
