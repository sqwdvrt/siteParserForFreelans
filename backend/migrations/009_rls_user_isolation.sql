-- Row-Level Security: изоляция данных пользователей по сессионной переменной app.current_user_id.
-- Когда переменная не задана (воркеры, миграции, создание пользователя) — доступ ко всем строкам.
-- Когда задана — только строки текущего пользователя (users.id = app.current_user_id, notifications.user_id = app.current_user_id).

SELECT pg_advisory_lock(20260216, 1);

-- users: при пустой app.current_user_id — полный доступ (бэкенд/воркеры); при заданной — только строка с id = app.current_user_id
ALTER TABLE users ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'users' AND policyname = 'users_isolate'
  ) THEN
    CREATE POLICY users_isolate
    ON users FOR ALL
    USING (
      COALESCE(NULLIF(trim(current_setting('app.current_user_id', true)), ''), '') = ''
      OR id = (NULLIF(trim(current_setting('app.current_user_id', true)), '')::bigint)
    )
    WITH CHECK (
      COALESCE(NULLIF(trim(current_setting('app.current_user_id', true)), ''), '') = ''
      OR id = (NULLIF(trim(current_setting('app.current_user_id', true)), '')::bigint)
    );
  END IF;
END $$;

-- notifications: изоляция по user_id
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'notifications' AND policyname = 'notifications_isolate'
  ) THEN
    CREATE POLICY notifications_isolate
    ON notifications FOR ALL
    USING (
      COALESCE(NULLIF(trim(current_setting('app.current_user_id', true)), ''), '') = ''
      OR user_id = (NULLIF(trim(current_setting('app.current_user_id', true)), '')::bigint)
    )
    WITH CHECK (
      COALESCE(NULLIF(trim(current_setting('app.current_user_id', true)), ''), '') = ''
    );
  END IF;
END $$;

SELECT pg_advisory_unlock(20260216, 1);
