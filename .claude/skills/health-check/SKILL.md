---
name: health-check
description: Проверяет статус всех сервисов Agent Navigator Pro (Document Server, Legal Server, UMS, Chainlit) и выводит сводную таблицу. Используй когда нужно убедиться что система запущена перед работой с документами.
---

Проверь доступность всех сервисов Agent Navigator Pro:

```bash
# Document Server
curl -s http://localhost:8001/health

# Legal Server
curl -s http://localhost:8002/health

# UMS — health + статус модели
curl -s http://localhost:8090/health
curl -s http://localhost:8090/status

# Chainlit UI
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
```

Выведи результат в виде таблицы:

| Сервис | Порт | Статус | Детали |
|--------|------|--------|--------|
| Document Server | 8001 | ✅/❌ | версия или ошибка |
| Legal Server | 8002 | ✅/❌ | версия или ошибка |
| UMS | 8090 | ✅/❌ | tier, модель загружена? |
| Chainlit | 3000 | ✅/❌ | HTTP код |

Если сервис недоступен — предложи команду диагностики:
- Сервис на хосте: `tmux attach-session -t agent-navigator` → посмотреть нужное окно
- Chainlit Docker: `docker compose logs chainlit --tail=50`
- Полный перезапуск: `./scripts/run_all.sh`
