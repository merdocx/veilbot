#!/bin/bash
# Скрипт для проверки доступности V2Ray API с VeilBot сервера

V2RAY_SERVER="38.180.192.10:8000"
API_KEY="5a5-lxUnQF75TzeoObRGLZuHe27Qh_PnfxKPuxnplXA"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "=========================================="
echo "Проверка V2Ray API - $TIMESTAMP"
echo "Сервер: $V2RAY_SERVER"
echo "Исходящий IP: $(curl -s ifconfig.me)"
echo "=========================================="
echo ""

echo "[1] Проверка TCP соединения..."
if timeout 3 nc -zv ${V2RAY_SERVER%:*} ${V2RAY_SERVER#*:} 2>&1 | grep -q "succeeded"; then
    echo "✅ TCP соединение установлено"
else
    echo "❌ TCP соединение не установлено"
fi
echo ""

echo "[2] Проверка эндпоинта /api..."
echo "Выполняю: curl -v --max-time 10 http://${V2RAY_SERVER}/api"
curl -v --max-time 10 "http://${V2RAY_SERVER}/api" 2>&1 | head -30
echo ""
echo ""

echo "[3] Проверка эндпоинта /api/keys (создание тестового ключа)..."
echo "Выполняю: curl -X POST http://${V2RAY_SERVER}/api/keys"
curl -v --max-time 10 -X POST "http://${V2RAY_SERVER}/api/keys" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name":"test_veilbot_'$(date +%s)'"}' 2>&1 | head -40
echo ""
echo ""

echo "[4] Проверка активных соединений..."
ss -tnp | grep "${V2RAY_SERVER%:*}" | head -5
echo ""

echo "=========================================="
echo "Проверка завершена"
echo "=========================================="
echo ""
echo "ВАЖНО: Проверьте логи на V2Ray сервере (38.180.192.10):"
echo "  sudo journalctl -u veil-xray-api -f | grep -E 'Request from IP|Access|46.151'"
echo ""
echo "В логах должны появиться записи о запросах с IP 46.151.31.105"
