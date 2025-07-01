#!/bin/bash

# VeilBot Service Management Script

case "$1" in
    start)
        echo "Starting VeilBot services..."
        sudo systemctl start veilbot.service
        sudo systemctl start veilbot-admin.service
        echo "Services started!"
        ;;
    stop)
        echo "Stopping VeilBot services..."
        sudo systemctl stop veilbot.service
        sudo systemctl stop veilbot-admin.service
        echo "Services stopped!"
        ;;
    restart)
        echo "Restarting VeilBot services..."
        sudo systemctl restart veilbot.service
        sudo systemctl restart veilbot-admin.service
        echo "Services restarted!"
        ;;
    status)
        echo "=== VeilBot Bot Status ==="
        sudo systemctl status veilbot.service --no-pager
        echo ""
        echo "=== VeilBot Admin Panel Status ==="
        sudo systemctl status veilbot-admin.service --no-pager
        ;;
    logs)
        echo "=== VeilBot Bot Logs ==="
        sudo journalctl -u veilbot.service -f --no-pager
        ;;
    admin-logs)
        echo "=== VeilBot Admin Panel Logs ==="
        sudo journalctl -u veilbot-admin.service -f --no-pager
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs|admin-logs}"
        echo ""
        echo "Commands:"
        echo "  start      - Start both services"
        echo "  stop       - Stop both services"
        echo "  restart    - Restart both services"
        echo "  status     - Show status of both services"
        echo "  logs       - Show bot logs (follow mode)"
        echo "  admin-logs - Show admin panel logs (follow mode)"
        exit 1
        ;;
esac 