-- Subscription system migration
-- Run once against the railway DB.
-- stripe_customer_id already exists on users — skip if present.

-- 1. Add subscription columns to users (idempotent)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS subscription_tier       TEXT;

-- 2. Subscription plans catalogue
CREATE TABLE IF NOT EXISTS subscription_plans (
    id        TEXT PRIMARY KEY,
    name      TEXT        NOT NULL,
    is_active BOOLEAN     NOT NULL DEFAULT true
);

INSERT INTO subscription_plans (id, name) VALUES ('max', 'Moddy Max')
    ON CONFLICT (id) DO NOTHING;

-- 3. Servers linked to a user subscription (max 5 per user)
CREATE TABLE IF NOT EXISTS subscription_servers (
    user_id   TEXT        NOT NULL REFERENCES users(id),
    server_id TEXT        NOT NULL REFERENCES servers(id),
    added_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, server_id)
);
