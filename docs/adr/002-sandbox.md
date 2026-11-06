# 002. Один статический контейнер Compose

Статус: accepted для v0; hardening не завершён.

Compose запускает sandbox заранее; provision логически выделяет его одной сессии.
Приложение не получает Docker socket. Rootful Docker socket означает практически
root на хосте; отдавать его gateway ради динамического provisioning сейчас не нужно.
Альтернативы: rootless Docker с отдельным менеджером, gVisor, запуск shell на хосте.
Последнее не обеспечивает изоляцию. gVisor сложнее устанавливать и проверять.

Есть uid 10001, cap-drop, read-only root, no-new-privileges, tmpfs и resource limits.
Цена: одна сессия, supervisor и команды одного uid, общий control network.
Process group cleanup недостаточен против setsid; до ЧП2 нужны отдельный supervisor
и гарантированное уничтожение workload/cgroup. Канарейка env не доказывает защиту /proc.
