CREATE TABLE IF NOT EXISTS email_deliveries (
    message_id VARCHAR(255) PRIMARY KEY,
    recipient VARCHAR(255) NOT NULL,
    subject TEXT NOT NULL,
    region VARCHAR(64) NOT NULL,
    sent_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_email_deliveries_recipient ON email_deliveries(recipient);
