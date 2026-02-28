-- Добавляем статус доставки для надёжной retry-логики.
-- pending: записано, но ещё не доставлено в Telegram.
-- sent:    успешно доставлено (устанавливается через MarkSent).
-- Существующие строки помечаются как 'sent' — они были записаны при успешной отправке.
SELECT pg_advisory_lock(20260216, 3);

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'sent';

COMMENT ON COLUMN notifications.status IS 'pending: записано, не доставлено; sent: доставлено в Telegram';

SELECT pg_advisory_unlock(20260216, 3);
