#!/bin/bash
# Скрипт для проверки V2Ray API с подробным логированием

V2RAY_SERVER="38.180.192.10:8000"
API_KEY="5a5-lxUnQF75TzeoObRGLZuHe27Qh_PnfxKPuxnplXA"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
LOG_FILE="/tmp/v2ray_check_$(date +%Y%m%d_%H%M%S).log"

echo "==========================================" | tee -a "$LOG_FILE"
echo "Проверка V2Ray API - $TIMESTAMP" | tee -a "$LOG_FILE"
echo "Сервер: $V2RAY_SERVER" | tee -a "$LOG_FILE"
echo "Исходящий IP: $(curl -s --max-time 3 ifconfig.me || echo 'не определен')" | tee -a "$LOG_FILE"
echo "==========================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

echo "[1] Проверка TCP соединения..." | tee -a "$LOG_FILE"
if timeout 3 nc -zv ${V2RAY_SERVER%:*} ${V2RAY_SERVER#*:} 2>&1 | grep -q "succeeded"; then
    echo "✅ TCP соединение установлено" | tee -a "$LOG_FILE"
else
    echo "❌ TCP соединение не установлено" | tee -a "$LOG_FILE"
fi
echo "" | tee -a "$LOG_FILE"

echo "[2] GET /api (без проверки IP)..." | tee -a "$LOG_FILE"
echo "Выполняю: curl -v --max-time 10 http://${V2RAY_SERVER}/api" | tee -a "$LOG_FILE"
curl -v --max-time 10 "http://${V2RAY_SERVER}/api" 2>&1 | tee -a "$LOG_FILE" | head -30
echo "" | tee -a "$LOG_FILE"

echo "[3] POST /api/keys (требует проверки IP и токен)..." | tee -a "$LOG_FILE"
echo "Выполняю: curl -X POST http://${V2RAY_SERVER}/api/keys" | tee -a "$LOG_FILE"
curl -v --max-time 10 -X POST "http://${V2RAY_SERVER}/api/keys" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name":"test_veilbot_'$(date +%s)'"}' 2>&1 | tee -a "$LOG_FILE" | head -40
echo "" | tee -a "$LOG_FILE"

echo "[4] Проверка активных соединений..." | tee -a "$LOG_FILE"
FIN_WAIT_COUNT=$(ss -tnp state fin-wait-2 | grep "${V2RAY_SERVER%:*}" | wc -l)
echo "Соединений в состоянии FIN-WAIT-2: $FIN_WAIT_COUNT" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

echo "==========================================" | tee -a "$LOG_FILE"
echo "Проверка завершена" | tee -a "$LOG_FILE"
echo "Лог сохранен в: $LOG_FILE" | tee -a "$LOG_FILE"
echo "==========================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

echo "ВАЖНО: Проверьте логи на V2Ray сервере (38.180.192.10):"
echo "  sudo journalctl -u veil-xray-api -f | grep -E 'Request from IP|Access|46.151|POST|GET|Incoming'"
echo ""
echo "В логах должны появиться записи о запросах с IP 46.151.31.105"
