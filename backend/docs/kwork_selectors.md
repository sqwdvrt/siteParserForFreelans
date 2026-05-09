# Kwork селекторы (задача 2.7)

Структура страницы kwork.ru/projects — требуется ручная проверка.

**Список проектов:** ссылки вида `/projects/{id}/view` или `https://kwork.ru/projects/{id}/view`

**Детали проекта:**
- Заголовок: `.project-title` или `h1`
- Описание: `.project-description`
- Бюджет: `.project-budget`
- Навыки: `.project-skills`
- Дата: `.project-date`

**Примечание:** Kwork может загружать контент через JS. Проверить: HTML приходит с сервера или рендерится на клиенте.
