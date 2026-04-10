#!/usr/bin/env python3
"""
Полный анализ неиспользуемых, устаревших и дублирующихся файлов
"""
import os
from pathlib import Path
from typing import List, Set, Tuple
import re

PROJECT_ROOT = Path(__file__).parent.parent

def _keep_walk_dir(name: str, exclude_dirs: Set[str]) -> bool:
    if name in exclude_dirs:
        return False
    if name.startswith("venv.backup") or name.startswith(".pytest"):
        return False
    return True


def find_all_python_files() -> Set[Path]:
    """Найти все Python файлы в проекте"""
    python_files = set()
    exclude_dirs = {'__pycache__', 'node_modules', '.git', 'venv', 'env', 'db_backups', 'logs'}

    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if _keep_walk_dir(d, exclude_dirs)]

        for file in files:
            if file.endswith('.py'):
                python_files.add(Path(root) / file)

    return python_files

def find_all_md_files() -> Set[Path]:
    """Найти все MD файлы в проекте"""
    md_files = set()
    exclude_dirs = {'__pycache__', 'node_modules', '.git', 'venv', 'env', 'db_backups', 'logs'}

    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if _keep_walk_dir(d, exclude_dirs)]

        for file in files:
            if file.endswith('.md'):
                md_files.add(Path(root) / file)

    return md_files

def extract_imports(content: str) -> Set[str]:
    """Извлечь импорты из Python кода"""
    imports = set()
    
    # Импорты вида: from scripts.xxx import, import scripts.xxx
    patterns = [
        r'from\s+scripts\.(\w+)\s+import',
        r'import\s+scripts\.(\w+)',
        r'from\s+scripts\s+import\s+(\w+)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, content)
        imports.update(matches)
    
    # Также ищем прямые пути к скриптам
    script_patterns = [
        r'scripts/(\w+\.py)',
        r'scripts/(\w+)',
    ]
    
    for pattern in script_patterns:
        matches = re.findall(pattern, content)
        imports.update([m.replace('.py', '') for m in matches])
    
    return imports

def check_script_usage(script_name: str, all_python_files: Set[Path]) -> Tuple[bool, List[str]]:
    """Проверить, используется ли скрипт где-то в коде"""
    script_base = script_name.replace('.py', '').replace('_', '')
    references = []
    
    for py_file in all_python_files:
        if py_file.name == script_name:
            continue
        
        try:
            content = py_file.read_text(encoding='utf-8', errors='ignore')
            imports = extract_imports(content)
            
            # Проверка импортов
            if script_base in [imp.replace('_', '') for imp in imports]:
                references.append(str(py_file.relative_to(PROJECT_ROOT)))
            
            # Проверка строковых ссылок
            if script_name in content or script_base in content:
                # Игнорируем комментарии и документацию
                if 'scripts/' + script_name in content or f'scripts/{script_base}' in content:
                    references.append(str(py_file.relative_to(PROJECT_ROOT)))
        except Exception:
            pass
    
    # Проверка shell скриптов и конфигов
    for ext in ['.sh', '.service', '.timer', '.md']:
        for file_path in PROJECT_ROOT.rglob(f'*{ext}'):
            try:
                content = file_path.read_text(encoding='utf-8', errors='ignore')
                if script_name in content or script_base in content:
                    references.append(str(file_path.relative_to(PROJECT_ROOT)))
            except Exception:
                pass
    
    return len(references) > 0, references

def main():
    print("=" * 80)
    print("АНАЛИЗ НЕИСПОЛЬЗУЕМЫХ И УСТАРЕВШИХ ФАЙЛОВ")
    print("=" * 80)
    print()
    
    scripts_dir = PROJECT_ROOT / 'scripts'
    scripts = [f.name for f in scripts_dir.glob('*.py') if not f.name.startswith('__')]
    
    all_python_files = find_all_python_files()
    
    unused_scripts = []
    used_scripts = []
    potentially_obsolete = []
    
    print("📋 АНАЛИЗ СКРИПТОВ")
    print("-" * 80)
    
    for script in sorted(scripts):
        if script in ['check_unused_files.py', 'analyze_unused_files.py']:
            continue
        
        is_used, refs = check_script_usage(script, all_python_files)
        
        # Проверка на одноразовые/миграционные скрипты
        script_lower = script.lower()
        is_migration = any(keyword in script_lower for keyword in [
            'migrate', 'fix_', 'recover', 'create_subscriptions_for_users_without',
            'link_keys_to_subscriptions'
        ])
        
        if is_used:
            used_scripts.append((script, refs))
        elif is_migration:
            potentially_obsolete.append((script, "Миграционный/одноразовый скрипт"))
        else:
            unused_scripts.append((script, []))
    
    print(f"\n✅ Используемые скрипты ({len(used_scripts)}):")
    for script, refs in used_scripts[:10]:  # Показываем первые 10
        print(f"  - {script}")
        for ref in refs[:3]:
            print(f"    → {ref}")
        if len(refs) > 3:
            print(f"    ... и еще {len(refs) - 3} ссылок")
    
    print(f"\n⚠️  Потенциально устаревшие ({len(potentially_obsolete)}):")
    for script, reason in potentially_obsolete:
        print(f"  - {script} ({reason})")
    
    print(f"\n❌ Неиспользуемые скрипты ({len(unused_scripts)}):")
    for script, _ in unused_scripts:
        print(f"  - {script}")
    
    # Анализ MD файлов
    print("\n" + "=" * 80)
    print("📚 АНАЛИЗ ДОКУМЕНТАЦИИ")
    print("-" * 80)
    
    md_files = find_all_md_files()
    docs_dir = PROJECT_ROOT / 'docs'
    
    duplicate_names = {}
    for md_file in md_files:
        name = md_file.stem
        if name not in duplicate_names:
            duplicate_names[name] = []
        duplicate_names[name].append(md_file)
    
    duplicates = {k: v for k, v in duplicate_names.items() if len(v) > 1}
    
    if duplicates:
        print(f"\n🔄 Дубликаты ({len(duplicates)}):")
        for name, files in duplicates.items():
            print(f"  {name}:")
            for f in files:
                print(f"    - {f.relative_to(PROJECT_ROOT)}")
    
    # Старые документы в корне docs (не в archive)
    main_docs = [f for f in docs_dir.glob('*.md') if f.is_file()]
    old_keywords = ['ANALYSIS', 'REPORT', 'PROGRESS', 'COMPLETED', 'MIGRATION', 'REFACTORING']
    
    potentially_old = []
    for doc in main_docs:
        name_upper = doc.stem.upper()
        if any(keyword in name_upper for keyword in old_keywords):
            # Проверяем, есть ли такой же в archive
            archive_version = docs_dir / 'archive' / doc.name
            if not archive_version.exists():
                potentially_old.append(doc)
    
    if potentially_old:
        print(f"\n📦 Потенциально устаревшие документы ({len(potentially_old)}):")
        for doc in potentially_old:
            print(f"  - {doc.relative_to(PROJECT_ROOT)}")
    
    # Проблемные зависимости
    print("\n" + "=" * 80)
    print("🔗 ПРОБЛЕМНЫЕ ЗАВИСИМОСТИ")
    print("-" * 80)
    
    print("\n" + "=" * 80)
    print("📊 ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 80)
    print(f"Всего скриптов: {len(scripts)}")
    print(f"  Используются: {len(used_scripts)}")
    print(f"  Устаревшие: {len(potentially_obsolete)}")
    print(f"  Неиспользуемые: {len(unused_scripts)}")
    print(f"Всего MD файлов: {len(md_files)}")
    print(f"  Дубликаты: {len(duplicates)}")
    print(f"  Потенциально устаревшие: {len(potentially_old)}")

if __name__ == '__main__':
    main()

