## Модель учёта трафика подписок

- **Единственный источник правды по использованному трафику подписки** — это агрегированное значение по ключам в таблице `v2ray_keys`:
  - поле `v2ray_keys.traffic_usage_bytes`,
  - которое считается как разница `max(0, total_bytes - traffic_baseline_bytes)` в джобе `monitor_subscription_traffic_limits`.
- Поле `subscriptions.traffic_usage_bytes` используется **только** как служебный кэш для:
  - ускорения проверок лимитов,
  - вычисления и хранения флагов `traffic_over_limit_at` и `traffic_over_limit_notified`,
  - формирования рассылок и фоновых уведомлений.
- Любые UI, боты и админка при отображении текущего трафика и остатка должны опираться на:
  - `SubscriptionRepository.get_subscription_traffic_sum(subscription_id)` — сумма `traffic_usage_bytes` по всем ключам подписки,
  - `SubscriptionRepository.get_subscription_traffic_limit(subscription_id)` — эффективный лимит подписки (в байтах).

### Продление подписки и сброс трафика

- При продлении подписки:
  - срок действия (`expires_at`) продлевается в `SubscriptionPurchaseService` согласно тарифу и правилам для VIP/ручных подписок;
  - лимит трафика обновляется через `_update_subscription_traffic_limit_safe`, сохраняя реферальные бонусы и безлимит.
- Сброс трафика реализован через **сдвиг baseline**:
  - в `subscription_traffic_reset.reset_subscription_traffic`:
    - `traffic_baseline_bytes += traffic_usage_bytes`,
    - `traffic_usage_bytes = 0` в `v2ray_keys`,
    - `subscriptions.traffic_usage_bytes` и флаги превышения обнуляются.
  - фактическое значение usage после продления считается как новая разница `max(0, total_bytes - traffic_baseline_bytes)` — это надёжно даже при недоступности или сбоях API Xray.

### Окно защиты после сброса

- Джоб `monitor_subscription_traffic_limits` использует `TRAFFIC_RESET_PROTECTION_WINDOW`:
  - в течение окна после `last_traffic_reset_at` большие значения `total_bytes` из API считаются устаревшими и не переносятся в usage;
  - это защищает от ситуации, когда Xray не успел/не смог обнулить счётчики на своей стороне.

### Рекомендации по использованию

- Для показа пользователю:
  - используйте только агрегированный usage по ключам (через `get_subscription_traffic_sum`);
  - лимит и остаток считайте от `get_subscription_traffic_limit`.
- Для фоновых задач и уведомлений:
  - можно использовать `subscriptions.traffic_usage_bytes` как кэш/слепок актуального usage,
  - но при расхождениях приоритет всегда за пересчитанным значением из `v2ray_keys`.

