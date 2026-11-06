# 004. Синхронный HTTP-контракт sandbox v0

Статус: accepted для ЧП1, стрим запланирован.

provision/exec/collect/cancel/release через HTTP JSON в private compose network.
exec отдаёт один итоговый result; operation_id позволяет вернуть кешированный результат.
Альтернативы: gRPC streaming, JSONL через stdin контейнера. Первый требует зависимостей,
второй привязывает gateway к способу запуска sandbox и контейнерному API.

Цена: stdout виден после завершения; нет отдельного стрима sandbox, часть A-6.2
остаётся на ЧП2. Нет automatic retry мутаций при потере ответа. Кеш не переживает рестарт.
