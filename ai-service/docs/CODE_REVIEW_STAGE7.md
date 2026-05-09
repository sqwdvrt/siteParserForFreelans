# Код-ревью: Этап 7 (Matching)

## SQL параметризация

- **PostgresMatchRepository.find_users_for_job**: все значения передаются через `%s` и кортеж `(vec, vec, threshold, job_id, limit)`. Строковая конкатенация не используется. SQL-инъекции исключены.

## N+1 запросы

- **find_users_for_job**: один SELECT с JOIN-подобной логикой (NOT IN subquery). Один запрос на вызов.
- **ProcessJob.execute**: после `find_users_for_job` — цикл `for c in candidates: enqueue(c)`. Это Redis LPUSH, не SQL. Дополнительных запросов к БД нет.

## NULL embedding

- В `find_users_for_job` добавлена проверка: `embedding is None or len(embedding) == 0 or len(embedding) != 384` → возврат `[]` без обращения к БД.

## Итог

- SQL параметризация: OK
- N+1: отсутствует
- NULL-обработка: OK
