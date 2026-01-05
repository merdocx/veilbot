# План удаления функции безлимита из проекта

## Цель
Полностью удалить функцию безлимита (`unlimited`) из проекта, при этом **не затрагивая текущие значения лимитов по срокам и трафику**.

## Важные замечания
- После удаления колонки `unlimited` все подписки будут работать как обычные подписки
- Текущие значения `expires_at` и `traffic_limit_mb` **сохраняются без изменений**
- Подписки с `unlimited=1` и `expires_at=2100-01-01` продолжат работать до этой даты
- Подписки с `unlimited=1` и `traffic_limit_mb=0` будут работать с безлимитным трафиком (0 = безлимит в системе)

---

## План действий

### 1. База данных
**Файл:** `db.py`

**Действия:**
- [ ] Создать миграцию для удаления колонки `unlimited` из таблицы `subscriptions`
- [ ] Удалить все упоминания `unlimited` в SQL запросах создания таблицы (если есть)

**SQL миграция:**
```sql
-- Удаление колонки unlimited из таблицы subscriptions
-- ВАЖНО: Текущие значения expires_at и traffic_limit_mb сохраняются
ALTER TABLE subscriptions DROP COLUMN unlimited;
```

**Файлы для изменения:**
- `db.py` - добавить функцию миграции

---

### 2. Репозитории
**Файл:** `app/repositories/subscription_repository.py`

**Действия:**
- [ ] Удалить `unlimited` из всех SELECT запросов
- [ ] Удалить параметр `unlimited` из методов `extend_subscription()` и `extend_subscription_async()`
- [ ] Удалить все проверки `COALESCE(unlimited, 0) = 0` из WHERE условий
- [ ] Удалить `unlimited` из методов `get_expired_subscriptions()`, `get_expiring_subscriptions()`, `get_subscriptions_with_traffic_limits()`

**Изменения:**
- Удалить `unlimited` из SELECT в `list_subscriptions()`
- Удалить `unlimited` из SELECT в `get_subscription_by_id()`
- Удалить проверки `AND COALESCE(unlimited, 0) = 0` из всех методов фильтрации
- Удалить параметр `unlimited` из `extend_subscription()` и `extend_subscription_async()`

---

### 3. Админ-панель - Backend
**Файл:** `admin/routes/subscriptions.py`

**Действия:**
- [ ] Удалить `unlimited` из SELECT запроса в `subscriptions_page()`
- [ ] Удалить всю логику обработки `unlimited` в `subscriptions_page()`
- [ ] Удалить обработку чекбокса `unlimited` в `edit_subscription_route()`
- [ ] Удалить автоматическую установку `expires_at=2100-01-01` и `traffic_limit_mb=0` при `unlimited=1`
- [ ] Удалить `unlimited` из JSON ответа в `edit_subscription_route()`
- [ ] Удалить проверки `unlimited == 1` при формировании `expiry_info` и `traffic_info`

**Изменения:**
- Удалить строку `unlimited` из SELECT (строка 210)
- Удалить логику `if unlimited_value == 1:` (строки 227-230, 275, 323, 325)
- Удалить обработку `unlimited` из формы (строки 392-444)
- Удалить передачу `unlimited` в `extend_subscription()` (строка 484)
- Удалить `unlimited` из ответа (строки 338, 548-620)

---

### 4. Админ-панель - Frontend (HTML)
**Файл:** `admin/templates/subscriptions.html`

**Действия:**
- [ ] Удалить столбец "Безлимит" из таблицы (строка 61)
- [ ] Удалить ячейку "Безлимит" из строк таблицы (строки 150-157)
- [ ] Удалить проверки `{% if subscription.unlimited == 1 %}` в столбцах "Истекает" и "Трафик" (строки 108-126, 134-149)
- [ ] Удалить чекбокс `unlimited` из модального окна редактирования
- [ ] Удалить атрибут `data-unlimited` из кнопки редактирования

**Изменения:**
- Удалить `<th class="text-center">Безлимит</th>` (строка 61)
- Удалить `<td>` с галочкой для безлимита (строки 150-157)
- Упростить условия в столбцах "Истекает" и "Трафик" - убрать проверки на `unlimited`
- Удалить `<input type="checkbox" id="unlimited" name="unlimited">` из формы

---

### 5. Админ-панель - Frontend (JavaScript)
**Файл:** `admin/static/js/subscriptions.js`

**Действия:**
- [ ] Удалить обработку чекбокса `unlimited` из `openModal()`
- [ ] Удалить обработку `unlimited` из `handleEditSubmit()`
- [ ] Удалить логику установки `expires_at=2100-01-01` и `traffic_limit_mb=0` при `unlimited=1`
- [ ] Удалить `unlimitedInput` из состояния
- [ ] Удалить функцию `updateUnlimitedFieldsState()`
- [ ] Удалить обработчик `handleUnlimitedChange()`
- [ ] Удалить `unlimited` из `applySubscriptionUpdate()`
- [ ] Удалить все `console.log` связанные с `unlimited`

**Изменения:**
- Удалить `unlimitedInput: null` из `state` (строка 22)
- Удалить получение `unlimitedInput` (строка 682)
- Удалить обработку `unlimited` в `openModal()` (строки 54-86)
- Удалить обработку `unlimited` в `handleEditSubmit()` (строки 281-305)
- Удалить `unlimited` из `applySubscriptionUpdate()` (строки 121-244)

---

### 6. Бот - Обработчики
**Файл:** `bot/handlers/subscriptions.py`

**Действия:**
- [ ] Удалить `unlimited` из SELECT запроса в `format_subscription_info()`
- [ ] Удалить проверки `is_unlimited` и логику отображения "Безлимит"
- [ ] Удалить `COALESCE(unlimited, 0) = 1` из WHERE условий в SQL запросах
- [ ] Удалить `unlimited` из SELECT в `handle_subscriptions_btn()`

**Изменения:**
- Удалить `COALESCE(unlimited, 0) as unlimited` из SELECT (строки 966)
- Удалить `unlimited` из распаковки (строка 109)
- Удалить проверки `if is_unlimited:` (строки 114-120, 134-140)
- Удалить `OR COALESCE(unlimited, 0) = 1` из WHERE (строки 162, 968)

**Файл:** `bot/handlers/keys.py`

**Действия:**
- [ ] Удалить `COALESCE(unlimited, 0) as unlimited` из SELECT запросов
- [ ] Удалить проверки `unlimited == 1` и логику отображения "Безлимит"
- [ ] Удалить `OR unlimited = 1` из WHERE условий
- [ ] Удалить проверки `is_unlimited` и `is_key_unlimited`

**Изменения:**
- Удалить `COALESCE(unlimited, 0) as unlimited` из SELECT (строки 43, 102)
- Удалить `unlimited` из распаковки (строки 52, 122)
- Удалить проверки `if unlimited == 1:` (строки 75-78, 147-150, 200-203)
- Удалить `OR unlimited = 1` из WHERE (строки 45, 106)
- Удалить проверку `is_subscription_unlimited` (строки 216-220)

---

### 7. Фоновые задачи
**Файл:** `bot/services/background_tasks.py`

**Действия:**
- [ ] Удалить `AND COALESCE(sub.unlimited, 0) = 0` из всех SQL запросов
- [ ] Удалить проверки `unlimited` в функциях:
  - `auto_delete_expired_subscriptions()`
  - `monitor_subscription_traffic_limits()`
  - `notify_expiring_subscriptions()`

**Изменения:**
- Удалить `AND COALESCE(sub.unlimited, 0) = 0` (строки 114, 135, 944)

---

### 8. Создание ключей
**Файл:** `bot/services/key_creation.py`

**Действия:**
- [ ] Удалить проверку `unlimited != 1` в `process_referral_bonus()`
- [ ] Удалить получение `unlimited` из подписки

**Изменения:**
- Удалить `SELECT unlimited` из запроса (строка 1028)
- Удалить проверку `if unlimited != 1:` (строка 1038)

---

### 9. Скрипты
**Файлы:**
- `scripts/analyze_referral_traffic_bonus.py`
- `scripts/apply_referral_traffic_bonus.py`

**Действия:**
- [ ] Удалить получение `unlimited` из запросов
- [ ] Удалить проверки `if unlimited == 1:` или `if unlimited != 1:`
- [ ] Удалить упоминания `unlimited` в выводе

**Изменения:**
- Удалить `SELECT unlimited` из запросов
- Удалить проверки на `unlimited`
- Удалить вывод информации о безлимитных подписках

---

### 10. Документация
**Файлы:**
- `docs/UNLIMITED_SUBSCRIPTIONS_TZ.md` - удалить или пометить как устаревший
- `docs/REFERRAL_TRAFFIC_BONUS_TZ.md` - удалить упоминания `unlimited`
- `docs/REFERRAL_TRAFFIC_BONUS_APPLY_LIST.md` - удалить упоминания `unlimited`

**Действия:**
- [ ] Удалить или переместить в архив `docs/UNLIMITED_SUBSCRIPTIONS_TZ.md`
- [ ] Обновить другие документы, удалив упоминания `unlimited`

---

## Порядок выполнения

1. **Сначала:** Создать миграцию БД и применить её
2. **Затем:** Обновить код (репозитории, админ-панель, бот, фоновые задачи)
3. **В конце:** Обновить документацию

---

## Риски и проверки

### Перед применением:
- [ ] Создать резервную копию базы данных
- [ ] Проверить количество подписок с `unlimited=1`
- [ ] Убедиться, что текущие значения `expires_at` и `traffic_limit_mb` не будут затронуты

### После применения:
- [ ] Проверить, что все подписки отображаются корректно
- [ ] Проверить, что фоновые задачи работают корректно
- [ ] Проверить, что админ-панель работает без ошибок
- [ ] Проверить, что бот корректно обрабатывает подписки

---

## Статистика изменений

**Ориентировочное количество файлов для изменения:** ~15-20
**Ориентировочное количество строк кода для удаления/изменения:** ~200-300

---

**Статус:** Ожидает согласования

