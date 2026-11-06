# 007. Отдельная сеть sandbox

Статус: accepted для v0, allowlist proposed для ЧП2.

Sandbox подключён только к internal compose network. Gateway также подключён
к внешней сети для OpenRouter. API key в gateway, env дочерних команд очищается.
Альтернативы: host network (неприемлема), network none (нужен иной control transport),
egress proxy с allowlist (выбран как дальнейшее развитие).

internal network закрывает обычный внешний маршрут, но sandbox всё ещё видит gateway
и соседей control network. Это не полное «сеть запрещена» A-5.4; firewall/allowlist
и проверка доступа к host/LAN/IPv6 нужны до ЧП2. По умолчанию gateway опубликован
только на localhost, токены в .env.example демонстрационные и требуют замены.
