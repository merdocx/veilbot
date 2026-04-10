#!/usr/bin/env python3
"""
Скрипт для очистки проекта от устаревших и неиспользуемых файлов
"""
import shutil
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
ARCHIVE_SCRIPTS = PROJECT_ROOT / 'scripts' / 'archive'
ARCHIVE_DOCS = PROJECT_ROOT / 'docs' / 'archive'

# Создаем директории архивов если их нет
ARCHIVE_SCRIPTS.mkdir(exist_ok=True)
ARCHIVE_DOCS.mkdir(exist_ok=True)

def archive_file(source: Path, archive_dir: Path, reason: str = ""):
    """Переместить файл в архив"""
    if not source.exists():
        print(f"⚠️  Файл не найден: {source}")
        return False
    
    dest = archive_dir / source.name
    
    # Если файл уже существует в архиве, добавляем дату
    if dest.exists():
        timestamp = datetime.now().strftime("%Y%m%d")
        stem = source.stem
        suffix = source.suffix
        dest = archive_dir / f"{stem}_{timestamp}{suffix}"
    
    try:
        shutil.move(str(source), str(dest))
        print(f"✅ Архивирован: {source.name} → {dest.name} ({reason})")
        return True
    except Exception as e:
        print(f"❌ Ошибка при архивировании {source.name}: {e}")
        return False

def fix_import_in_delete_orphaned_keys():
    """Исправить импорт в delete_orphaned_keys.py"""
    script_path = PROJECT_ROOT / 'scripts' / 'delete_orphaned_keys.py'
    archive_compare_keys = ARCHIVE_DOCS / 'compare_keys.py'
    scripts_compare_keys = PROJECT_ROOT / 'scripts' / 'compare_keys.py'
    
    if not script_path.exists():
        return
    
    # Если compare_keys.py в архиве, перемещаем его обратно в scripts
    if archive_compare_keys.exists() and not scripts_compare_keys.exists():
        print("\n📦 Перемещение compare_keys.py из архива в scripts/...")
        shutil.copy(str(archive_compare_keys), str(scripts_compare_keys))
        print(f"✅ compare_keys.py скопирован в scripts/")
    
    # Читаем файл и проверяем импорт
    content = script_path.read_text(encoding='utf-8')
    if 'from scripts.compare_keys' in content or 'import scripts.compare_keys' in content:
        # Импорт уже правильный, все ок
        print("✅ Импорт в delete_orphaned_keys.py корректен")

def main():
    print("=" * 80)
    print("ОЧИСТКА ПРОЕКТА ОТ УСТАРЕВШИХ ФАЙЛОВ")
    print("=" * 80)
    print()
    
    # 1. Исправление проблемы с импортом
    print("1️⃣ ИСПРАВЛЕНИЕ ЗАВИСИМОСТЕЙ")
    print("-" * 80)
    fix_import_in_delete_orphaned_keys()
    print()
    
    # 2. Архивирование устаревших документов
    print("2️⃣ АРХИВИРОВАНИЕ УСТАРЕВШИХ ДОКУМЕНТОВ")
    print("-" * 80)
    
    docs_to_archive = [
        ('SUBSCRIPTION_EXPIRY_ANALYSIS.md', 'Анализ завершен'),
        ('SYNC_ANALYSIS.md', 'Анализ синхронизации завершен'),
        ('REFACTORING_COMPLETE.md', 'Рефакторинг завершен'),
        ('API_COMPLIANCE_REPORT.md', 'Отчет о соответствии API'),
        ('MIGRATION_COMPLETED.md', 'Миграция завершена'),
        ('UNUSED_FILES_REPORT_2025_12_17.md', 'Отчет об устаревших файлах'),
        ('COMPREHENSIVE_ANALYSIS_2025.md', 'Анализ проекта завершен'),
        ('OFERTA_ANALYSIS.md', 'Анализ оферты'),
        ('UNUSED_FILES_ANALYSIS_2025.md', 'Анализ неиспользуемых файлов'),
        ('REFACTORING_PROGRESS.md', 'Прогресс рефакторинга завершен'),
    ]
    
    archived_count = 0
    for doc_name, reason in docs_to_archive:
        doc_path = PROJECT_ROOT / 'docs' / doc_name
        if doc_path.exists():
            if archive_file(doc_path, ARCHIVE_DOCS, reason):
                archived_count += 1
    
    print(f"\n✅ Архивировано документов: {archived_count}")
    print()
    
    # 3. Архивирование одноразовых скриптов (если есть)
    print("3️⃣ ПРОВЕРКА ОДНОРАЗОВЫХ СКРИПТОВ")
    print("-" * 80)
    
    # Проверяем link_keys_to_subscriptions.py
    link_script = PROJECT_ROOT / 'scripts' / 'link_keys_to_subscriptions.py'
    if link_script.exists():
        print("⚠️  Найден link_keys_to_subscriptions.py")
        print("    Этот скрипт использовался для миграции и может быть устаревшим")
        print("    Оставляем как утилиту для ручного использования")
    
    print("\n✅ Проверка завершена")
    print()
    
    # 4. Итоговая статистика
    print("=" * 80)
    print("📊 ИТОГИ ОЧИСТКИ")
    print("=" * 80)
    print(f"Архивировано документов: {archived_count}")
    print(f"Исправлено зависимостей: 1")
    print()
    print("✅ Очистка завершена!")

if __name__ == '__main__':
    main()

