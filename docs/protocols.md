# Контракты v0

Машиночитаемые OpenAPI 3.1:
`schemas/client-server-v0.openapi.json`, `schemas/harness-sandbox-v0.openapi.json`.
Версия в URL `/v0`, JSON UTF-8, Bearer token; GET /health без токена.
POST body ≤1 MB. Ошибки HTTP имеют `{ "error": "..." }`.
Схемы описывают контракт; сервер проверяет входы вручную, полной OpenAPI-валидации нет.

## Клиент ↔ gateway

`connect` возвращает версию, backend, модель и настройки памяти.
`session` создаёт/возвращает сессию по session_id. Вторая сессия получает 409.
`message` принимает session_id, request_id, prompt и сразу возвращает run_id.
Повтор request_id с тем же prompt возвращает прежний run_id; другой prompt — 409.
`events` принимает session_id и after (seq), возвращает новые события по порядку.
Клиент опрашивает их каждые 100 мс. Это поток через polling, без SSE/WebSocket.
`cancel` сигнализирует агенту и sandbox. `release` разрешён только без активного run.
`tool` — вспомогательный endpoint для подготовки/проверки БАФ, запрещён во время run.

События: run_start, llm (usage, duration_ms), tool_start, tool_result, result.
Один worker публикует ровно один result на run; повторное чтение возвращает то же
событие — доставка не exactly-once, клиент дедуплицирует по seq. После падения процесса
gateway незавершённый run не восстанавливается: гарантия ограничена живым процессом.

## Харнес ↔ sandbox

`provision`, `exec`, `collect`, `release`, `cancel`, служебный `resume` перед новым run.
`exec` принимает session_id, operation_id, tool, arguments, timeout.
Ответ — один финальный объект `type=result`, operation_id, status, duration_ms,
при необходимости output, exit_code, truncated, error.
**В v0 exec синхронный: отдельный стрим sandbox ещё не реализован (часть A-6.2).**
Итоговый результат кешируется по operation_id до release; повтор после окончания
не исполняется заново. Идентификатор с другим payload — 409. Пока операция активна,
любая другая exec получает 409. collect возвращает список файлов, не копирует их.

## Ошибки и ретраи

| Класс | Представление | Политика |
|---|---|---|
| Ошибка команды | command_error + ненулевой exit_code | Результат получает модель, автоматического retry нет |
| Таймаут команды | timeout, process group уничтожена | Нет автоматического retry |
| Ошибка sandbox | sandbox_error | Результат получает модель; HTTP 5xx завершает run |
| Транспорт | transport_error на run | Не повторять команды вслепую; проверить прежний operation_id. При HTTP-ошибке показывается код без тела ответа провайдера |
| Невалидный tool call | tool_error | Сообщить модели, продолжить до лимита |
| Cancel | cancelled | До нового run не исполнять команды |

Автоматически повторяется только read-only polling событий при сетевой ошибке.
Прерванная отправка message восстанавливается вручную тем же request_id.
Таймауты: клиент 45с, sandbox HTTP 40с, LLM 60с, exec 0.1–30с; ожидание run 240с.
Секрет LLM есть только в gateway; env shell очищен. API-токен sandbox пока находится
в supervisor того же контейнера и не защищён от чтения через /proc его uid —
это известный пробел hardening, не выполненная гарантия A-6.4 для всех секретов.

Тесты в `tests/test_runtime.py`: HTTP auth, конфликт сессий, идемпотентность,
один финал, переподключение по курсору, таймаут, cancel process group,
отсутствие canary в env команд. Нет тестов аварии supervisor/mount, диска и сети.
