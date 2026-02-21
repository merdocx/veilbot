# Доступ по IP 46.151.31.105 (HTTP) - должен быть ПЕРВЫМ
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name 46.151.31.105;

    access_log /var/log/nginx/46.151.31.105.access.log;
    error_log /var/log/nginx/46.151.31.105.error.log;

    # Специальный location для синхронизации ключей (долгая операция)
    location /subscriptions/sync-keys {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Увеличенные таймауты для синхронизации (10 минут)
        proxy_connect_timeout 60s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        
        # Буферизация
        proxy_buffering off;  # Отключаем буферизацию для длительных операций
    }
    
    # Проксирование на админку
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        proxy_buffering on;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
    }

    location /static/ {
        alias /root/veilbot/admin/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
}

# HTTP -> HTTPS redirect для домена (после блоков для IP)
server {
    listen 80;
    listen [::]:80;
    server_name veil-bot.ru www.veil-bot.ru;
    
    # Редирект на HTTPS
    return 301 https://$server_name$request_uri;
}

# HTTPS server
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name veil-bot.ru www.veil-bot.ru;
    
    # SSL Configuration
    ssl_certificate /etc/letsencrypt/live/veil-bot.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/veil-bot.ru/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/veil-bot.ru/chain.pem;
    
    # SSL Security
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # HSTS
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    
    # Логи
    access_log /var/log/nginx/veil-bot.ru.access.log;
    error_log /var/log/nginx/veil-bot.ru.error.log;
    
    # Специальный location для синхронизации ключей (долгая операция)
    location /subscriptions/sync-keys {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Увеличенные таймауты для синхронизации (10 минут)
        proxy_connect_timeout 60s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        
        # Буферизация
        proxy_buffering off;  # Отключаем буферизацию для длительных операций
    }
    
    # Проксирование на админку
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Таймауты
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # Буферизация
        proxy_buffering on;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
    }
    
    # Статические файлы
    location /static/ {
        alias /root/veilbot/admin/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
    
    # Безопасность
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;
    add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
}

