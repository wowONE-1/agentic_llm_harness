# Минимальный agent runtime — ЧП1

Свой харнес на Python, без LangChain, базы данных и очереди. Три инструмента:
`ReadFile`, `WriteFile`, `RunCommand`. Память — последние **два полных хода**
user → tool calls/results → assistant, вставленные в следующий запрос.
Сервер — gateway + отдельный контейнер sandbox. LLM-прокси — модуль gateway.

## Запуск сервера

Нужны Docker Engine на Linux или Docker Desktop на Mac, Compose v2 с `--wait`.
Из чистого клона достаточно:

```sh
docker compose up --build --wait
```

По умолчанию используется **скриптовый mock**, API и платных запросов нет.
Образ закреплён `python:3.12.8-slim-bookworm`; оба сервиса собираются из исходников.
Первый build скачивает базовый образ. Порт опубликован только на localhost.
Для доступа с другой машины в ЧП1 используй SSH-туннель:
`ssh -L 8000:127.0.0.1:8000 user@server`.

## Клиент и демонстрация

Python ≥3.12, только стандартная библиотека; установки зависимостей и сборки нет.
Пока это CLI/headless-клиент, GUI и выбор монтируемой папки запланированы на ЧП2.

```sh
python3 -m runtime.client connect
python3 -m runtime.client chat 'MOCK {"tool":"RunCommand","arguments":{"command":"pwd; echo hello > hello.txt; cat hello.txt"}}'
python3 -m runtime.client chat 'MOCK {"tool":"ReadFile","arguments":{"path":"hello.txt"}}'
python3 -m runtime.client release
```

Клиент печатает `run_id`, затем JSONL-события и финальный `result`.
Ctrl+C в `chat` отправляет cancel. Из другого терминала:

```sh
python3 -m runtime.client cancel
python3 -m runtime.client events --after 0 --run-id RUN_ID
```

Адрес и сессия: `--url http://localhost:8000 --session demo` **до** подкоманды.
Токен задаётся через `GATEWAY_TOKEN` в окружении клиента или `--token`.
Повтор `chat --request-id ID` с тем же текстом возвращает тот же run, без повторного исполнения.
При переподключении `events --after N` продолжает чтение после последнего `seq`.
Состояние сессии переживает обрыв клиента, но не рестарт gateway.

## Реальная LLM через OpenRouter

Скопируй `.env.example` в `.env` и укажи:

```dotenv
LLM_BACKEND=openrouter
LLM_MODEL=идентификатор-разрешённой-модели
OPENROUTER_API_KEY=свой-ключ
```

Затем `docker compose up --build --wait` и обычный запрос:
`python3 -m runtime.client chat 'Создай hello.py, который печатает hello, и запусти его'`.
Ключ остаётся в gateway, в env команд sandbox не передаётся.
Используется [Chat Completions API](https://openrouter.ai/docs/api_reference/overview)
с [tool calling](https://openrouter.ai/docs/guides/features/tool-calling).
Выбирать нужно модель с поддержкой tools. **Допуск модели и OpenRouter по курсу
требует сверки с разделом 0-1: общие правила пока не предоставлены.**

## Проверки и Б1–Б5

Все локальные тесты без внешней сети (локальные TCP-порты необходимы):

```sh
sh scripts/ci.sh
```

Это unit-тесты, проверки CLI против mock LLM, инфраструктурные проверки протоколов
через два настоящих HTTP-сервера и scripted smoke пяти БАФ.
Они исполняют только известные тестовые команды во временных папках хоста.
Проверки контейнерных лимитов, mount и полной изоляции сюда пока не входят.
GitHub Actions дополнительно собирает compose и выполняет smoke в контейнерах;
скачивание Actions/Python/образов требует сети, сам тестовый набор — нет.

На поднятом сервере с `LLM_BACKEND=mock`:

```sh
python3 -m baf.runner --mode smoke
```

На сервере с реальной моделью (сначала завершите свою сессию командой
`python3 -m runtime.client release`, чтобы освободить единственный sandbox):

```sh
python3 -m baf.runner --mode quality --repeat 1 --output results/quality.jsonl
```

Раннер передаёт fixture через протокол, отправляет задачу, проверяет итоговые файлы
через `check.py`, пишет статус, время, usage и конфигурацию, освобождает сессию.
В smoke ожидаемые действия заданы явно в `smoke_calls` — это **не оценка качества**
и не запись ответов настоящей модели. Режим quality запрещает mock.
Пять задач — местные примеры по мотивам задания, не выданный каталог курса.

## Что готово и что остаётся

| Ориентир ЧП1 | Состояние |
|---|---|
| Compose, отдельный sandbox | Сборка и healthcheck проверены в Docker Desktop на Mac |
| Свой агентный цикл и инструменты | Проверены с mock и реальной моделью OpenRouter |
| Протоколы v0 | HTTP/JSON, OpenAPI, polling событий gateway; sandbox пока синхронный |
| Подключение клиента | CLI, события, cancel, переподключение |
| Первые ADR | 8 коротких записей, будущие решения отмечены proposed |
| Раннер, Б1–Б5 | Итоговый quality-прогон: 5/5 на настроенной модели; [отчёт](docs/checkpoint-1.md) |

Результат проверки ЧП1 и ограничения — в [отчёте](docs/checkpoint-1.md).
Отдельно остаются проверка на Linux-хосте и допуск модели по общим правилам. До ЧП2: GUI, **реальное монтирование**
с другой машины, RO/RW, защита `.git` от shell-команд, network allowlist,
отдельный безопасный sandbox supervisor, тесты контейнерных лимитов/сбоев,
стрим sandbox, настоящий record/replay smoke, полный курс и свои БАФ.
Передача fixtures раннером — тестовая подготовка, **не замена монтированию**.

Снять JSONL-трейс до остановки:

```sh
mkdir -p results
docker compose exec -T gateway sh -c 'cat /traces/*.jsonl' > results/traces.jsonl
docker compose down -v
```

`/work`, `/tmp`, `/traces` находятся в ограниченных tmpfs. `release` удаляет файлы
сессии, `down -v` уничтожает ресурсы compose. В trace остаются метаданные и финальный
ответ до остановки контейнера; ответы могут содержать пользовательские данные.

Подробности: [харнес](docs/harness.md), [протоколы](docs/protocols.md),
[БАФ](docs/baf.md), [ADR](docs/adr/README.md), [демо](docs/checkpoint-1.md).
