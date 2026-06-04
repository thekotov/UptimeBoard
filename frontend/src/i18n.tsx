import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Lang = "ru" | "en";

const STORAGE_KEY = "md_lang";

type Dict = Record<string, string>;

const ru: Dict = {
  // generic
  "app.title": "Мониторинг",
  loading: "Загрузка…",
  connecting: "Подключение…",
  delete: "Удалить",
  remove: "Убрать",
  add: "Добавить",
  create: "Создать",
  edit: "Изменить",
  cancel: "Отмена",
  save: "Сохранить",
  optional: "необязательно",
  none: "—",
  search: "Поиск…",
  ungrouped: "Без группы",
  expandAll: "Развернуть всё",
  collapseAll: "Свернуть всё",
  onlyProblems: "Только проблемы",
  nothingFound: "Ничего не найдено",

  // status labels
  "status.up": "Работает",
  "status.degraded": "Деградация",
  "status.down": "Недоступен",
  "status.unknown": "Неизвестно",
  "status.paused": "На паузе",
  "probe.pause": "Поставить на паузу",
  "probe.resume": "Возобновить",

  // overall headlines
  "overall.up": "Все системы работают штатно",
  "overall.degraded": "Частичная деградация",
  "overall.down": "Серьёзный сбой",
  "overall.unknown": "Статус неизвестен",
  "overall.subAllUp": "Под наблюдением: {total} сервис(ов)",
  "overall.subProblems": "{n} из {total} сервисов с проблемами",
  "overall.subUnknown": "Статус пока не определён",

  // summary counts
  "summary.up": "работают",
  "summary.degraded": "деградация",
  "summary.down": "недоступны",

  // dashboard
  "dash.updated": "обновлено {time}",
  "dash.live": "В реальном времени",
  "dash.reconnecting": "Переподключение…",
  "dash.now": "сейчас",
  "dash.activeProblems": "Активные сбои: {n}",
  "dash.density": "Плотность отображения",
  "cmd.placeholder": "Перейти к странице… (Ctrl/⌘+K)",
  "dash.uptime24": "аптайм за 24ч",
  "dash.lastCheck": "проверено {time}",
  "dash.noData": "нет данных",
  "dash.ms": "мс",
  "dash.notFound": "Страница не найдена или не опубликована.",
  "dash.empty": "На этой странице пока нет сервисов.",
  "dash.overallUptime": "общий аптайм за {range}",

  // ranges
  "range.15m": "15м",
  "range.24h": "24ч",
  "range.7d": "7д",
  "range.30d": "30д",
  "range.90d": "90д",

  // incidents
  "incidents.title": "Последние сбои",
  "incidents.empty": "Сбоев не зафиксировано 🎉",
  "incidents.ongoing": "идёт сейчас",
  "incidents.ack": "Принять",
  "incidents.unack": "Снять",
  "incidents.ackHint": "Заглушить повторные и эскалационные алерты, пока чините",
  "incidents.acknowledged": "принят",
  "incidents.resolved": "Восстановлен",
  "incidents.resolvedAt": "восстановлено {time}",
  "incidents.started": "начало {time}",

  // probe modal / drill-down
  "modal.history": "История пробы",
  "modal.recent": "Последние проверки",
  "modal.latencyTitle": "Время ответа, мс",
  "modal.noLatency": "Нет данных о времени ответа за период.",
  "modal.noData": "Нет данных",
  "modal.latencyApprox": "Приблизительно: за длинный период перцентили считаются по агрегатам (роллапам).",
  "modal.uptime": "Аптайм",
  "modal.checks": "проверок",

  // check now / toasts
  "probe.checkNow": "Проверить",
  "probe.checking": "Проверка…",
  "probe.checkFail": "Не удалось выполнить проверку",
  "toast.saved": "Сохранено",
  "toast.deleted": "Удалено",
  "toast.error": "Произошла ошибка",
  "toast.cloned": "Скопировано",

  // probe extras
  "probe.tls": "TLS",
  "probe.warnDays": "Предупреждать за (дней)",
  "probe.failureThreshold": "Сбоев подряд до алерта",
  "probe.failureThresholdHint": "«Сбой» — неудачная проверка (проба ответила degraded или down). «Подряд» — идущие друг за другом без единой успешной между ними: первая же успешная проверка обнуляет счётчик. После стольких сбоев подряд по этой пробе уходит оповещение в каналы алертов (Telegram, e-mail, webhook). 1 — алерт при первом же сбое; больше — чтобы не реагировать на разовые сетевые всплески.",
  "probe.latencyDegraded": "Порог латентности (мс)",
  "probe.degradedThreshold": "Сбоев подряд до «degraded»",
  "probe.degradedThresholdHint": "Сколько неудачных проверок подряд нужно, чтобы проба стала жёлтой (degraded — «работает с проблемами»). Счётчик сбрасывается при первой же успешной проверке. 1 — желтеет сразу при первом сбое.",
  "probe.downThreshold": "Сбоев подряд до «down»",
  "probe.downThresholdHint": "Сколько неудачных проверок подряд нужно, чтобы проба стала красной (down — «недоступна»). Счётчик сбрасывается при первой же успешной проверке. 1 — краснеет сразу при первом сбое.",
  "probe.tolerance": "Допуск погрешности (проверок)",
  "probe.toleranceHint": "Первые N неудачных проверок подряд (любой тяжести) считаются погрешностью мониторинга и записываются как «работает» — не влияют на аптайм, графики и таймлайн. Реальный сбой длиннее N учитывается полностью. 0 — учитывать все проверки. Полезно гасить единичные блипы (скачок латентности, разовый packet loss).",
  "probe.test": "Тест",
  "probe.testing": "Тестирование…",
  clone: "Клонировать",
  "probe.heartbeat": "Heartbeat",
  "probe.grace": "Грейс, с",
  "probe.period": "Ожидаемый период, с",
  "probe.pushUrl": "URL для пинга",
  "probe.pushHint": "URL появится после сохранения пробы.",
  "probe.advanced": "Дополнительно (заголовки, авторизация, проверки тела)",
  "probe.headers": "Заголовки (по строке «Name: Value»)",
  "probe.basicUser": "Basic-логин",
  "probe.basicPass": "Basic-пароль",
  "probe.bearer": "Bearer-токен",
  "probe.bodyRegex": "Regex тела ответа",
  "probe.jsonPath": "JSON-путь (напр. data.status)",
  "probe.jsonExpected": "Ожидаемое значение",
  "alerts.escalateAfter": "Эскалация через, мин (0 = обычный)",
  "alerts.proxy": "SOCKS5-прокси (необязательно)",
  "alerts.test": "Тест",
  "alerts.testing": "Проверка…",
  "alerts.events": "События",
  "alerts.eventsHint": "Не отмечено = все события",
  "alerts.event.opened": "Падение",
  "alerts.event.ongoing": "Всё ещё лежит",
  "alerts.event.escalated": "Эскалация",
  "alerts.event.resolved": "Восстановление",
  "alerts.template": "Шаблон сообщения (необязательно)",
  "alerts.templateHint": "Плейсхолдеры: {probe} {type} {host} {server} {service} {page} {status} {error} {latency} {duration} {url} {time} {verb} {event}. Пусто = подробное стандартное сообщение (с HTML-форматированием в Telegram).",

  // staleness / maintenance
  "dash.stale": "данные устарели",
  "dash.showProbes": "Показать пробы сервера",
  "dash.hideProbes": "Свернуть пробы сервера",
  "maintenance.title": "Обслуживание",
  "maintenance.add": "Добавить окно",
  "maintenance.from": "Начало",
  "maintenance.to": "Окончание",
  "maintenance.empty": "Окон обслуживания нет.",
  "maintenance.confirmDelete": "Удалить окно обслуживания?",
  "maintenance.banner": "🛠 Идут плановые работы: {title}",

  // announcements
  "ann.title": "Объявления",
  "ann.add": "Добавить объявление",
  "ann.heading": "Заголовок",
  "ann.body": "Текст (необязательно)",
  "ann.level": "Уровень",
  "ann.info": "Инфо",
  "ann.warning": "Предупреждение",
  "ann.empty": "Объявлений нет.",
  "ann.confirmDelete": "Удалить объявление?",

  // inline validation
  "valid.slugTaken": "Идентификатор занят",
  "valid.slugFormat": "Только строчные буквы, цифры и дефис",
  "valid.slugFree": "Свободно",
  "valid.url": "URL должен начинаться с http:// или https://",
  "valid.port": "Порт должен быть 1–65535",

  // alert routing
  "alerts.scope": "Применять к",
  "alerts.allPages": "Все страницы",
  "alerts.smtpHost": "SMTP-хост",
  "alerts.smtpPort": "Порт",
  "alerts.smtpUser": "Логин",
  "alerts.smtpPass": "Пароль",
  "alerts.from": "От кого",
  "alerts.to": "Кому",
  "alerts.hintEmail": "Письмо через SMTP при инцидентах.",
  "io.import": "Импорт",
  "io.export": "Экспорт",
  "metrics.clear": "Очистить метрики",
  "metrics.hint": "Удалить всю сохранённую историю проверок (таймлайн и аптайм) по всем страницам",
  "metrics.confirm": "Удалить всю сохранённую историю метрик? Таймлайн и аптайм обнулятся и начнут собираться заново. Текущие статусы и инциденты не затрагиваются.",
  "metrics.confirmPage": "Удалить историю метрик страницы «{title}»? Таймлайн и аптайм её проб обнулятся и начнут собираться заново. Текущие статусы и инциденты не затрагиваются.",
  "metrics.hintPage": "Удалить сохранённую историю проверок (таймлайн и аптайм) только проб этой страницы",
  "metrics.cleared": "Метрики очищены: удалено {n} записей",
  "io.copyLink": "Копировать публичную ссылку",
  "io.copied": "Ссылка скопирована",
  "auth.changePassword": "Сменить пароль",
  "auth.current": "Текущий пароль",
  "auth.new": "Новый пароль",
  "auth.newRepeat": "Повторите новый пароль",
  "auth.mismatch": "Пароли не совпадают",
  "auth.changed": "Пароль изменён",
  "auth.weakWarning": "Используется слабый пароль по умолчанию — смените его.",
  "bulk.selected": "Выбрано: {n}",
  "bulk.enable": "Включить",
  "bulk.disable": "Выключить",
  "bulk.tolerance": "Допуск",
  "bulk.toleranceTitle": "Допуск погрешности для выбранных проб",
  "bulk.toleranceHint": "Будет применено к {n} выбранным пробам. Первые N неудачных проверок подряд считаются погрешностью и не влияют на статистику.",
  "bulk.toleranceApply": "Применить к {n}",
  "bulk.checkAll": "Проверить все",
  "bulk.confirmDelete": "Удалить выбранные пробы ({n})?",
  "bulk.import": "Списком",
  "bulk.importTitle": "Импорт проб списком",
  "bulk.importBtn": "Импортировать ({n})",
  "bulk.importHint": "По одной пробе на строку. Хост берётся у сервера. Примеры:\nhttps://host/health  ·  http /health  ·  tcp 5432  ·  tls 443  ·  icmp  ·  heartbeat",
  "confirm.title": "Подтверждение",
  "confirm.ok": "Подтвердить",
  "incidents.manage": "Сбои",
  "incidents.clearAll": "Удалить все",
  "incidents.clearResolved": "Удалить решённые",
  "incidents.confirmDelete": "Удалить этот инцидент из истории?",
  "incidents.confirmClearAll": "Удалить все инциденты этой страницы?",
  "incidents.confirmClearResolved": "Удалить все решённые инциденты этой страницы?",
  "incidents.showAll": "Вся история",

  // home
  "home.subtitle": "Сервис мониторинга доступности",
  "home.openStatus": "Откройте статус-страницу по адресу",
  "home.demoHint": "Демо-страница доступна здесь:",
  "home.toAdmin": "В админку",
  "home.empty": "Пока нет опубликованных страниц.",

  // admin nav
  "nav.admin": "Администрирование",
  "nav.pages": "Страницы",
  "nav.alerts": "Каналы оповещений",
  "nav.logout": "Выйти",
  "worker.ok": "Монитор активен",
  "worker.down": "Воркер не отвечает",
  "worker.stale": "Воркер молчит {n} с",
  "worker.unknown": "Статус воркера неизвестен",
  "incidents.acked": "в работе",

  // login
  "login.title": "Вход в админку",
  "login.email": "Эл. почта",
  "login.password": "Пароль",
  "login.submit": "Войти",
  "login.submitting": "Вход…",
  "login.error": "Неверная почта или пароль",

  // pages list
  "pages.create": "Создать страницу",
  "pages.slug": "Идентификатор (slug)",
  "pages.title": "Название",
  "pages.group": "Группа / папка",
  "pages.published": "Опубликована",
  "pages.yes": "да",
  "pages.no": "нет",
  "pages.empty": "Пока нет страниц.",
  "pages.createError": "Не удалось создать страницу",
  "pages.confirmDelete": "Удалить страницу и всё её содержимое?",
  "pages.count": "{n} сервис(ов)",

  // page editor
  "editor.publish": "Опубликовать",
  "editor.unpublish": "Снять с публикации",
  "editor.newService": "Новый сервис",
  "editor.serviceName": "Название сервиса",
  "editor.addService": "Добавить сервис",
  "editor.deleteService": "Удалить сервис",
  "editor.confirmDeleteService": "Удалить сервис?",
  "editor.newServerName": "Имя сервера",
  "editor.host": "Хост / адрес",
  "editor.addServer": "Добавить сервер",
  "editor.confirmDeleteServer": "Удалить сервер?",
  "editor.noServers": "В сервисе пока нет серверов.",
  "editor.noProbes": "Нет проб — добавьте ниже.",
  "editor.viewPublic": "Открыть публичную страницу",
  "editor.group": "Группа",
  "editor.private": "Приватная страница",
  "editor.privateHint": "Не отображается в общем списке страниц, но доступна по прямой ссылке.",
  "editor.defaultView": "Отображение по умолчанию",
  "editor.viewCollapsed": "Свёрнутый (серверы свёрнуты)",
  "editor.viewExpanded": "Полный (всё развёрнуто)",
  "editor.defaultViewHint": "Как открывается публичная страница: в свёрнутом виде серверы показаны кратко (клик раскрывает пробы), проблемные — раскрыты сразу; в полном — все пробы видны сразу.",
  "editor.defaultTolerance": "Допуск погрешности по умолчанию (проверок)",
  "editor.defaultToleranceHint": "Значение, которое автоматически подставляется новым пробам этой страницы (в т.ч. при массовом импорте). У существующих проб не меняет — для них используйте массовое применение. 0 — без допуска.",
  "editor.description": "Описание",
  "editor.pageSettings": "Настройки страницы",
  "editor.noServices": "Пока нет сервисов — добавьте первый.",
  "editor.editService": "Изменить сервис",
  "editor.editServer": "Изменить сервер",
  "editor.editProbe": "Изменить пробу",
  "editor.addProbe": "Добавить пробу",
  "editor.saveError": "Не удалось сохранить (возможно, slug уже занят)",
  "editor.confirmDeleteProbe": "Удалить пробу?",
  "editor.dblclickRename": "Двойной клик — переименовать",
  "editor.counts": "серверов: {servers} · проб: {probes}",

  // probe form
  "probe.type": "Тип",
  "probe.typeLocked": "Тип нельзя изменить после создания",
  "probe.name": "Название",
  "probe.url": "URL",
  "probe.method": "Метод",
  "probe.expected": "Ожидаемый код",
  "probe.body": "Тело содержит",
  "probe.redirects": "Редиректы",
  "probe.port": "Порт",
  "probe.count": "Пакетов",
  "probe.packetSize": "Размер пакета",
  "probe.interval": "Интервал, с",
  "probe.timeout": "Таймаут, с",
  "probe.enabled": "Включена",
  "probe.off": "выкл",
  "probe.add": "Добавить пробу",
  "probe.every": "каждые {n} с",

  // alert channels
  "alerts.add": "Добавить канал оповещений",
  "alerts.type": "Тип",
  "alerts.name": "Название",
  "alerts.botToken": "Токен бота",
  "alerts.chatId": "ID чата",
  "alerts.webhookUrl": "URL вебхука",
  "alerts.enabled": "Включён",
  "alerts.on": "вкл",
  "alerts.off": "выкл",
  "alerts.empty": "Каналов пока нет.",
  "alerts.confirmDelete": "Удалить канал?",
  "alerts.hintTelegram": "Бот уведомит в Telegram при инцидентах.",
  "alerts.hintWebhook": "POST-запрос с JSON при инцидентах.",
};

const en: Dict = {
  "app.title": "Monitoring",
  loading: "Loading…",
  connecting: "Connecting…",
  delete: "Delete",
  remove: "Remove",
  add: "Add",
  create: "Create",
  edit: "Edit",
  cancel: "Cancel",
  save: "Save",
  optional: "optional",
  none: "—",
  search: "Search…",
  ungrouped: "Ungrouped",
  expandAll: "Expand all",
  collapseAll: "Collapse all",
  onlyProblems: "Only problems",
  nothingFound: "Nothing found",

  "status.up": "Operational",
  "status.degraded": "Degraded",
  "status.down": "Down",
  "status.unknown": "Unknown",
  "status.paused": "Paused",
  "probe.pause": "Pause",
  "probe.resume": "Resume",

  "overall.up": "All systems operational",
  "overall.degraded": "Partial degradation",
  "overall.down": "Major outage",
  "overall.unknown": "Status unknown",
  "overall.subAllUp": "Monitoring {total} service(s)",
  "overall.subProblems": "{n} of {total} services affected",
  "overall.subUnknown": "Status not yet determined",

  "summary.up": "operational",
  "summary.degraded": "degraded",
  "summary.down": "down",

  "dash.updated": "updated {time}",
  "dash.live": "Live",
  "dash.reconnecting": "Reconnecting…",
  "dash.now": "now",
  "dash.activeProblems": "Active incidents: {n}",
  "dash.density": "Display density",
  "cmd.placeholder": "Jump to a page… (Ctrl/⌘+K)",
  "dash.uptime24": "uptime 24h",
  "dash.lastCheck": "checked {time}",
  "dash.noData": "no data",
  "dash.ms": "ms",
  "dash.notFound": "Page not found or not published.",
  "dash.empty": "This page has no services yet.",
  "dash.overallUptime": "overall uptime for {range}",

  "range.15m": "15m",
  "range.24h": "24h",
  "range.7d": "7d",
  "range.30d": "30d",
  "range.90d": "90d",

  "incidents.title": "Recent incidents",
  "incidents.empty": "No incidents recorded 🎉",
  "incidents.ongoing": "ongoing",
  "incidents.ack": "Ack",
  "incidents.unack": "Unack",
  "incidents.ackHint": "Silence repeat and escalation alerts while you fix it",
  "incidents.acknowledged": "acked",
  "incidents.resolved": "Resolved",
  "incidents.resolvedAt": "resolved {time}",
  "incidents.started": "started {time}",

  "modal.history": "Probe history",
  "modal.recent": "Recent checks",
  "modal.latencyTitle": "Response time, ms",
  "modal.noLatency": "No response-time data for this range.",
  "modal.noData": "No data",
  "modal.latencyApprox": "Approximate: over long ranges percentiles are computed from aggregates (rollups).",
  "modal.uptime": "Uptime",
  "modal.checks": "checks",

  "probe.checkNow": "Check",
  "probe.checking": "Checking…",
  "probe.checkFail": "Check failed",
  "toast.saved": "Saved",
  "toast.deleted": "Deleted",
  "toast.error": "Something went wrong",
  "toast.cloned": "Cloned",

  "probe.tls": "TLS",
  "probe.warnDays": "Warn before (days)",
  "probe.failureThreshold": "Failures before alert",
  "probe.failureThresholdHint": "A “failure” is a failed check (the probe came back degraded or down). “In a row” means back-to-back with no successful check between them — the first successful check resets the counter. After this many failures in a row an alert is sent to your notification channels (Telegram, e-mail, webhook). 1 = alert on the very first failure; higher = ignore one-off network blips.",
  "probe.latencyDegraded": "Latency threshold (ms)",
  "probe.degradedThreshold": "Failures before “degraded”",
  "probe.degradedThresholdHint": "How many failed checks in a row before the probe turns amber (degraded — “up with problems”). The counter resets on the first successful check. 1 = turns amber on the first failure.",
  "probe.downThreshold": "Failures before “down”",
  "probe.downThresholdHint": "How many failed checks in a row before the probe turns red (down — “unavailable”). The counter resets on the first successful check. 1 = turns red on the first failure.",
  "probe.tolerance": "Noise tolerance (checks)",
  "probe.toleranceHint": "The first N failed checks in a row (any severity) are treated as monitoring noise and recorded as “up” — they don't affect uptime, graphs or the timeline. A real outage longer than N counts fully. 0 = count every check. Great for smoothing one-off blips (a latency spike, a single packet-loss sample).",
  "probe.test": "Test",
  "probe.testing": "Testing…",
  clone: "Clone",
  "probe.heartbeat": "Heartbeat",
  "probe.grace": "Grace, s",
  "probe.period": "Expected period, s",
  "probe.pushUrl": "Ping URL",
  "probe.pushHint": "The URL appears after the probe is saved.",
  "probe.advanced": "Advanced (headers, auth, body checks)",
  "probe.headers": "Headers (one «Name: Value» per line)",
  "probe.basicUser": "Basic user",
  "probe.basicPass": "Basic password",
  "probe.bearer": "Bearer token",
  "probe.bodyRegex": "Response body regex",
  "probe.jsonPath": "JSON path (e.g. data.status)",
  "probe.jsonExpected": "Expected value",
  "alerts.escalateAfter": "Escalate after, min (0 = normal)",
  "alerts.proxy": "SOCKS5 proxy (optional)",
  "alerts.test": "Test",
  "alerts.testing": "Testing…",
  "alerts.events": "Events",
  "alerts.eventsHint": "None checked = all events",
  "alerts.event.opened": "Down",
  "alerts.event.ongoing": "Still down",
  "alerts.event.escalated": "Escalated",
  "alerts.event.resolved": "Resolved",
  "alerts.template": "Message template (optional)",
  "alerts.templateHint": "Placeholders: {probe} {type} {host} {server} {service} {page} {status} {error} {latency} {duration} {url} {time} {verb} {event}. Empty = detailed default message (HTML-formatted on Telegram).",

  "dash.stale": "data is stale",
  "dash.showProbes": "Show server probes",
  "dash.hideProbes": "Collapse server probes",
  "maintenance.title": "Maintenance",
  "maintenance.add": "Add window",
  "maintenance.from": "Start",
  "maintenance.to": "End",
  "maintenance.empty": "No maintenance windows.",
  "maintenance.confirmDelete": "Delete maintenance window?",
  "maintenance.banner": "🛠 Scheduled maintenance in progress: {title}",

  "ann.title": "Announcements",
  "ann.add": "Add announcement",
  "ann.heading": "Title",
  "ann.body": "Body (optional)",
  "ann.level": "Level",
  "ann.info": "Info",
  "ann.warning": "Warning",
  "ann.empty": "No announcements.",
  "ann.confirmDelete": "Delete announcement?",

  "valid.slugTaken": "Slug is taken",
  "valid.slugFormat": "Lowercase letters, digits and dashes only",
  "valid.slugFree": "Available",
  "valid.url": "URL must start with http:// or https://",
  "valid.port": "Port must be 1–65535",

  "alerts.scope": "Applies to",
  "alerts.allPages": "All pages",
  "alerts.smtpHost": "SMTP host",
  "alerts.smtpPort": "Port",
  "alerts.smtpUser": "Username",
  "alerts.smtpPass": "Password",
  "alerts.from": "From",
  "alerts.to": "To",
  "alerts.hintEmail": "Sends an email via SMTP on incidents.",
  "io.import": "Import",
  "io.export": "Export",
  "metrics.clear": "Clear metrics",
  "metrics.hint": "Delete all stored check history (timeline & uptime) across every page",
  "metrics.confirm": "Delete all stored metric history? Timeline and uptime will reset and start collecting again. Current statuses and incidents are not affected.",
  "metrics.confirmPage": "Delete metric history for page “{title}”? Timeline and uptime for its probes will reset and start collecting again. Current statuses and incidents are not affected.",
  "metrics.hintPage": "Delete stored check history (timeline & uptime) for this page's probes only",
  "metrics.cleared": "Metrics cleared: {n} rows removed",
  "io.copyLink": "Copy public link",
  "io.copied": "Link copied",
  "auth.changePassword": "Change password",
  "auth.current": "Current password",
  "auth.new": "New password",
  "auth.newRepeat": "Repeat new password",
  "auth.mismatch": "Passwords do not match",
  "auth.changed": "Password changed",
  "auth.weakWarning": "A weak/default password is in use — please change it.",
  "bulk.selected": "Selected: {n}",
  "bulk.enable": "Enable",
  "bulk.disable": "Disable",
  "bulk.tolerance": "Tolerance",
  "bulk.toleranceTitle": "Noise tolerance for selected probes",
  "bulk.toleranceHint": "Will be applied to {n} selected probe(s). The first N failed checks in a row are treated as noise and don't affect statistics.",
  "bulk.toleranceApply": "Apply to {n}",
  "bulk.checkAll": "Check all",
  "bulk.confirmDelete": "Delete selected probes ({n})?",
  "bulk.import": "Bulk",
  "bulk.importTitle": "Bulk import probes",
  "bulk.importBtn": "Import ({n})",
  "bulk.importHint": "One probe per line. Host is taken from the server. Examples:\nhttps://host/health  ·  http /health  ·  tcp 5432  ·  tls 443  ·  icmp  ·  heartbeat",
  "confirm.title": "Please confirm",
  "confirm.ok": "Confirm",
  "incidents.manage": "Incidents",
  "incidents.clearAll": "Delete all",
  "incidents.clearResolved": "Delete resolved",
  "incidents.confirmDelete": "Delete this incident from history?",
  "incidents.confirmClearAll": "Delete all incidents for this page?",
  "incidents.confirmClearResolved": "Delete all resolved incidents for this page?",
  "incidents.showAll": "Full history",

  "home.subtitle": "Availability monitoring service",
  "home.openStatus": "Open a status page at",
  "home.demoHint": "The demo page is available here:",
  "home.toAdmin": "Admin",
  "home.empty": "No published pages yet.",

  "nav.admin": "Admin",
  "nav.pages": "Pages",
  "nav.alerts": "Alert channels",
  "nav.logout": "Logout",
  "worker.ok": "Monitor live",
  "worker.down": "Worker down",
  "worker.stale": "Worker silent for {n}s",
  "worker.unknown": "Worker status unknown",
  "incidents.acked": "being handled",

  "login.title": "Admin login",
  "login.email": "Email",
  "login.password": "Password",
  "login.submit": "Sign in",
  "login.submitting": "Signing in…",
  "login.error": "Invalid email or password",

  "pages.create": "Create page",
  "pages.slug": "Slug",
  "pages.title": "Title",
  "pages.group": "Group / folder",
  "pages.published": "Published",
  "pages.yes": "yes",
  "pages.no": "no",
  "pages.empty": "No pages yet.",
  "pages.createError": "Failed to create page",
  "pages.confirmDelete": "Delete this page and everything under it?",
  "pages.count": "{n} service(s)",

  "editor.publish": "Publish",
  "editor.unpublish": "Unpublish",
  "editor.newService": "New service",
  "editor.serviceName": "Service name",
  "editor.addService": "Add service",
  "editor.deleteService": "Delete service",
  "editor.confirmDeleteService": "Delete service?",
  "editor.newServerName": "Server name",
  "editor.host": "Host / address",
  "editor.addServer": "Add server",
  "editor.confirmDeleteServer": "Delete server?",
  "editor.noServers": "No servers yet.",
  "editor.noProbes": "No probes — add one below.",
  "editor.viewPublic": "Open public page",
  "editor.group": "Group",
  "editor.private": "Private page",
  "editor.privateHint": "Hidden from the public page directory, but reachable via its direct link.",
  "editor.defaultView": "Default view",
  "editor.viewCollapsed": "Collapsed (servers folded)",
  "editor.viewExpanded": "Expanded (everything open)",
  "editor.defaultViewHint": "How the public page opens: collapsed shows servers in brief (click to reveal probes), with problem servers auto-expanded; expanded shows every probe right away.",
  "editor.defaultTolerance": "Default noise tolerance (checks)",
  "editor.defaultToleranceHint": "Pre-filled for new probes on this page (including bulk import). Doesn't change existing probes — use bulk apply for those. 0 = no tolerance.",
  "editor.description": "Description",
  "editor.pageSettings": "Page settings",
  "editor.noServices": "No services yet — add the first one.",
  "editor.editService": "Edit service",
  "editor.editServer": "Edit server",
  "editor.editProbe": "Edit probe",
  "editor.addProbe": "Add probe",
  "editor.saveError": "Failed to save (slug may already be taken)",
  "editor.confirmDeleteProbe": "Delete probe?",
  "editor.dblclickRename": "Double-click to rename",
  "editor.counts": "servers: {servers} · probes: {probes}",

  "probe.type": "Type",
  "probe.typeLocked": "Type can't be changed after creation",
  "probe.name": "Name",
  "probe.url": "URL",
  "probe.method": "Method",
  "probe.expected": "Expected code",
  "probe.body": "Body contains",
  "probe.redirects": "Redirects",
  "probe.port": "Port",
  "probe.count": "Packets",
  "probe.packetSize": "Packet size",
  "probe.interval": "Interval, s",
  "probe.timeout": "Timeout, s",
  "probe.enabled": "Enabled",
  "probe.off": "off",
  "probe.add": "Add probe",
  "probe.every": "every {n}s",

  "alerts.add": "Add alert channel",
  "alerts.type": "Type",
  "alerts.name": "Name",
  "alerts.botToken": "Bot token",
  "alerts.chatId": "Chat ID",
  "alerts.webhookUrl": "Webhook URL",
  "alerts.enabled": "Enabled",
  "alerts.on": "on",
  "alerts.off": "off",
  "alerts.empty": "No channels yet.",
  "alerts.confirmDelete": "Delete channel?",
  "alerts.hintTelegram": "A bot notifies you on Telegram about incidents.",
  "alerts.hintWebhook": "A JSON POST request is sent on incidents.",
};

const DICTS: Record<Lang, Dict> = { ru, en };

interface I18nValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

function detectInitial(): Lang {
  const saved = localStorage.getItem(STORAGE_KEY);
  return saved === "en" ? "en" : "ru"; // Russian by default
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectInitial);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = (l: Lang) => {
    localStorage.setItem(STORAGE_KEY, l);
    setLangState(l);
  };

  const t = (key: string, params?: Record<string, string | number>) => {
    let str = DICTS[lang][key] ?? DICTS.ru[key] ?? key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        str = str.replace(`{${k}}`, String(v));
      }
    }
    return str;
  };

  return <I18nContext.Provider value={{ lang, setLang, t }}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}

/** Ultra-short relative time for dense rows, e.g. "14с" / "3м" / "2ч". */
export function relativeShort(iso: string | null, lang: Lang): string {
  if (!iso) return "";
  const sec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  const u =
    lang === "ru"
      ? { s: "с", m: "м", h: "ч", d: "д" }
      : { s: "s", m: "m", h: "h", d: "d" };
  if (sec < 60) return `${sec}${u.s}`;
  if (sec < 3600) return `${Math.round(sec / 60)}${u.m}`;
  if (sec < 86400) return `${Math.round(sec / 3600)}${u.h}`;
  return `${Math.round(sec / 86400)}${u.d}`;
}

/** Compact duration like "2ч 5м" / "2h 5m" from seconds. */
export function formatDuration(sec: number | null, lang: Lang): string {
  if (sec == null) return "";
  const u = lang === "ru" ? { d: "д", h: "ч", m: "м", s: "с" } : { d: "d", h: "h", m: "m", s: "s" };
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (d > 0) return `${d}${u.d} ${h}${u.h}`;
  if (h > 0) return `${h}${u.h} ${m}${u.m}`;
  if (m > 0) return `${m}${u.m}`;
  return `${s}${u.s}`;
}

/** Localised relative time, e.g. "2 минуты назад" / "2 minutes ago". */
export function relativeTime(iso: string | null, lang: Lang): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const diffSec = Math.round((then - Date.now()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  const abs = Math.abs(diffSec);
  if (abs < 60) return rtf.format(Math.round(diffSec), "second");
  if (abs < 3600) return rtf.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diffSec / 3600), "hour");
  return rtf.format(Math.round(diffSec / 86400), "day");
}
