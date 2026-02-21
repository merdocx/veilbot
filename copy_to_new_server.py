#!/usr/bin/env python3
"""
Скрипт для копирования проекта VeilBot на новый сервер с использованием пароля
"""
import pexpect
import sys
import os

SERVER = "95.142.47.150"
USER = "root"
PASSWORD = "5Uq34973rZjA38WB5WQm"
PROJECT_DIR = "/root/veilbot"

def run_command(cmd, password=None, timeout=300):
    """Выполняет команду через SSH с автоматическим вводом пароля"""
    print(f"\nВыполняется: {cmd}")
    child = pexpect.spawn(cmd, encoding='utf-8', timeout=timeout)
    
    if password:
        child.logfile_read = sys.stdout
    
    index = child.expect(['password:', 'yes/no', pexpect.EOF, pexpect.TIMEOUT], timeout=timeout)
    
    if index == 0:  # password prompt
        child.sendline(password)
        child.expect(pexpect.EOF, timeout=timeout)
    elif index == 1:  # yes/no prompt
        child.sendline('yes')
        if password:
            child.expect('password:', timeout=timeout)
            child.sendline(password)
        child.expect(pexpect.EOF, timeout=timeout)
    elif index == 2:  # EOF - команда завершилась
        pass
    elif index == 3:  # TIMEOUT
        print("Ошибка: таймаут выполнения команды")
        child.close()
        return False
    
    output = child.before + (str(child.after) if hasattr(child, 'after') and child.after else '')
    exit_status = child.exitstatus
    
    child.close()
    
    if exit_status != 0:
        print(f"Команда завершилась с кодом: {exit_status}")
        return False
    
    return True

def main():
    print("=" * 50)
    print("Миграция VeilBot на новый сервер")
    print(f"Новый сервер: {USER}@{SERVER}")
    print("=" * 50)
    
    # Проверка подключения
    print("\n[1/5] Проверка подключения к новому серверу...")
    if not run_command(f"ssh -o StrictHostKeyChecking=no {USER}@{SERVER} 'echo Connection OK'", PASSWORD, timeout=30):
        print("✗ Не удалось подключиться")
        return 1
    print("✓ Подключение успешно")
    
    # Создание директории
    print("\n[2/5] Создание директории на новом сервере...")
    if not run_command(f"ssh {USER}@{SERVER} 'mkdir -p {PROJECT_DIR} && echo Directory ready'", PASSWORD):
        print("✗ Не удалось создать директорию")
        return 1
    print("✓ Директория создана")
    
    # Информация о проекте
    print("\n[3/5] Информация о проекте:")
    try:
        os.chdir(PROJECT_DIR)
        db_path = os.popen("python3 -c 'from app.settings import settings; print(settings.DATABASE_PATH)' 2>/dev/null").read().strip() or "vpn.db"
        project_size = os.popen(f"du -sh {PROJECT_DIR} | cut -f1").read().strip()
        db_size = os.popen(f"du -h {PROJECT_DIR}/{db_path} 2>/dev/null | cut -f1").read().strip() or "не найдена"
        print(f"  - Путь к БД: {db_path}")
        print(f"  - Размер проекта: {project_size}")
        print(f"  - Размер БД: {db_size}")
    except Exception as e:
        print(f"  ⚠ Не удалось получить информацию: {e}")
    
    # Копирование через rsync
    print("\n[4/5] Копирование проекта через rsync...")
    print("Это может занять несколько минут...")
    
    rsync_cmd = (
        f"rsync -avz --progress "
        f"--exclude='.git' "
        f"--exclude='venv' "
        f"--exclude='__pycache__' "
        f"--exclude='*.pyc' "
        f"--exclude='.pytest_cache' "
        f"--exclude='htmlcov' "
        f"--exclude='.coverage' "
        f"--exclude='*.log' "
        f"{PROJECT_DIR}/ {USER}@{SERVER}:{PROJECT_DIR}/"
    )
    
    if not run_command(rsync_cmd, PASSWORD, timeout=600):
        print("✗ Ошибка при копировании")
        return 1
    print("✓ Проект скопирован успешно")
    
    # Проверка скопированных файлов
    print("\n[5/5] Проверка скопированных файлов на новом сервере...")
    check_cmd = (
        f"ssh {USER}@{SERVER} 'cd {PROJECT_DIR} && "
        f"if [ -f .env ]; then echo ✓ .env скопирован; else echo ✗ .env НЕ найден!; fi && "
        f"if [ -f {db_path} ]; then echo ✓ БД скопирована: {db_path}; else echo ⚠ БД не найдена; fi && "
        f"echo Размер проекта: && du -sh {PROJECT_DIR}'"
    )
    run_command(check_cmd, PASSWORD)
    
    print("\n" + "=" * 50)
    print("Копирование завершено!")
    print("=" * 50)
    print("\nСледующие шаги на новом сервере:")
    print(f"  1. cd {PROJECT_DIR}")
    print("  2. python3.11 -m pip install -r requirements.txt")
    print("  3. Настроить systemd сервисы (см. инструкцию)")
    print("  4. На этапе финальной синхронизации скопировать финальный бэкап БД")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
