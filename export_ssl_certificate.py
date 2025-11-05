#!/usr/bin/env python3
"""
Скрипт для экспорта SSL сертификата для CryptoBot webhook
"""
import os
import ssl
import socket
from cryptography import x509
from cryptography.hazmat.backends import default_backend

def get_certificate(hostname, port=443):
    """Получить SSL сертификат с сервера"""
    try:
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert_der = ssock.getpeercert(binary_form=True)
                cert = x509.load_der_x509_certificate(cert_der, default_backend())
                return cert
    except Exception as e:
        print(f"❌ Ошибка получения сертификата: {e}")
        return None

def export_certificate_pem(cert):
    """Экспортировать сертификат в формате PEM"""
    from cryptography.hazmat.primitives import serialization
    pem = cert.public_bytes(serialization.Encoding.PEM)
    return pem.decode('utf-8')

def main():
    hostname = "veil-bot.ru"
    port = 443
    
    print(f"🔐 Получение SSL сертификата для {hostname}...")
    
    cert = get_certificate(hostname, port)
    if not cert:
        print("\n❌ Не удалось получить сертификат")
        print("\n💡 Альтернативный способ:")
        print("   Выполните на сервере:")
        print("   openssl s_client -showcerts -connect veil-bot.ru:443 </dev/null 2>/dev/null | openssl x509 -outform PEM > certificate.pem")
        return
    
    print("✅ Сертификат получен")
    
    # Экспортируем в PEM
    pem_cert = export_certificate_pem(cert)
    
    # Сохраняем в файл
    cert_file = "veil-bot-ru-certificate.pem"
    with open(cert_file, 'w') as f:
        f.write(pem_cert)
    
    print(f"\n✅ Сертификат сохранен в: {cert_file}")
    print(f"\n📋 Информация о сертификате:")
    print(f"   Subject: {cert.subject.rfc4514_string()}")
    print(f"   Issuer: {cert.issuer.rfc4514_string()}")
    print(f"   Действителен до: {cert.not_valid_after}")
    
    print(f"\n📤 Инструкция для CryptoBot:")
    print(f"   1. Откройте бота @CryptoBot в Telegram")
    print(f"   2. Отправьте команду /webhook или найдите раздел Webhooks")
    print(f"   3. Укажите URL: https://veil-bot.ru/cryptobot/webhook")
    print(f"   4. Загрузите файл: {cert_file}")
    print(f"   5. Или скопируйте содержимое файла и вставьте в поле сертификата")
    
    print(f"\n📄 Содержимое сертификата (первые 3 строки):")
    lines = pem_cert.split('\n')[:3]
    for line in lines:
        print(f"   {line}")

if __name__ == "__main__":
    main()

