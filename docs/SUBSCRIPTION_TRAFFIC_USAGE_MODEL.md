# Модель учёта трафика подписок

## Идея

- **Лимит** считается только на уровне **подписки**, не по отдельным ключам.
- На **ключе** хранится только монотонный снимок счётчика с панели: **`panel_total_bytes_observed`**.
  - После успешного GET: `new = max(stored, api_total)` (если с панели не выросло — оставляем stored).
  - При ошибке GET поле **не** меняется.
- На **подписке** хранится **`traffic_baseline_bytes`** (`B`) — одна точка отсчёта периода для всей подписки.

## Формулы

Пусть **`S = SUM(panel_total_bytes_observed)`** по всем ключам подписки, **`B = subscriptions.traffic_baseline_bytes`**, **`L`** — лимит подписки в байтах (`get_subscription_traffic_limit`).

- **Израсходовано за период:** `used = max(0, S - B)`.
- **Остаток:** `remaining = max(0, L - used)`.

**Кэш** `subscriptions.traffic_usage_bytes` дублирует `used` для быстрых запросов (обновляется монитором).

## Мониторинг

Фоновая задача `monitor_subscription_traffic_limits` (интервал ~30 минут):

1. Для каждого активного V2Ray-ключа с API запрашивает `total_bytes` с панели.
2. Обновляет `panel_total_bytes_observed` монотонно.
3. Пересчитывает `used` по подписке и кэш, проверяет лимит, уведомления, grace.

## Продление / сброс периода

`reset_subscription_traffic` (без POST на панель) выставляет:

- `subscriptions.traffic_baseline_bytes = S` (сумма текущих `panel_total_bytes_observed` по ключам),
- сбрасывает кэш и флаги превышения на подписке.

Тогда `used = max(0, S - S) = 0` до нового роста счётчиков.

## Разовая калибровка

Скрипт `scripts/snap_subscription_traffic_baselines.py` может выставить `B = S` для всех подписок (см. `--dry-run`).

## UI / отчёты

- Суммарный расход подписки: `SubscriptionRepository.get_subscription_traffic_sum` (формула выше).
- По ключу в интерфейсах для «накопленного с панели» используется **`panel_total_bytes_observed`**.
