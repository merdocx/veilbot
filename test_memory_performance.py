#!/usr/bin/env python3
"""
Тест производительности оптимизации памяти VeilBot
"""

import time
import sys
import gc
import psutil
import os
from memory_optimizer import memory_optimizer, optimize_memory, get_memory_stats

def test_memory_performance():
    """Тест производительности оптимизации памяти"""
    print("🧪 Тестирование производительности оптимизации памяти...")
    
    # Получаем начальную статистику памяти
    initial_stats = get_memory_stats()
    initial_memory = psutil.Process().memory_info()
    
    print(f"📊 Начальное использование памяти: {initial_memory.rss / 1024 / 1024:.2f} MB")
    print(f"📊 Начальная статистика: {initial_stats}")
    
    # Создаем много объектов для тестирования
    print("\n🔧 Создание тестовых объектов...")
    start_time = time.time()
    
    objects_created = 0
    for i in range(1000):
        # Создаем объекты через lazy loading
        result = memory_optimizer.lazy_load(
            f"test_object_{i}", 
            lambda x=i: {
                "id": x,
                "data": "x" * 1000,  # 1KB данных на объект
                "timestamp": time.time()
            }
        )
        if result:
            objects_created += 1
    
    creation_time = time.time() - start_time
    mid_stats = get_memory_stats()
    mid_memory = psutil.Process().memory_info()
    
    print(f"✅ Создано объектов: {objects_created}")
    print(f"⏱️  Время создания: {creation_time:.3f} секунд")
    print(f"📊 Промежуточное использование памяти: {mid_memory.rss / 1024 / 1024:.2f} MB")
    print(f"📊 Промежуточная статистика: {mid_stats}")
    
    # Тестируем кэширование
    print("\n🔄 Тестирование кэширования...")
    cache_start_time = time.time()
    
    cache_hits = 0
    for i in range(1000):
        # Повторно запрашиваем те же объекты
        result = memory_optimizer.lazy_load(
            f"test_object_{i}", 
            lambda x=i: {"id": x, "data": "new_data"}
        )
        if result and result.get("data") != "new_data":  # Данные из кэша
            cache_hits += 1
    
    cache_time = time.time() - cache_start_time
    print(f"✅ Попаданий в кэш: {cache_hits}")
    print(f"⏱️  Время кэширования: {cache_time:.3f} секунд")
    
    # Тестируем оптимизацию памяти
    print("\n🧹 Тестирование оптимизации памяти...")
    optimize_start_time = time.time()
    
    collected = optimize_memory()
    
    optimize_time = time.time() - optimize_start_time
    final_stats = get_memory_stats()
    final_memory = psutil.Process().memory_info()
    
    print(f"✅ Собрано объектов: {collected}")
    print(f"⏱️  Время оптимизации: {optimize_time:.3f} секунд")
    print(f"📊 Финальное использование памяти: {final_memory.rss / 1024 / 1024:.2f} MB")
    print(f"📊 Финальная статистика: {final_stats}")
    
    # Анализ результатов
    print("\n📈 Анализ результатов:")
    memory_change = final_memory.rss - initial_memory.rss
    memory_change_mb = memory_change / 1024 / 1024
    
    print(f"💾 Изменение памяти: {memory_change_mb:+.2f} MB")
    print(f"🚀 Скорость создания: {objects_created / creation_time:.0f} объектов/сек")
    print(f"⚡ Скорость кэширования: {cache_hits / cache_time:.0f} попаданий/сек")
    print(f"🧹 Эффективность сборки мусора: {collected} объектов")
    
    # Проверяем эффективность
    cache_efficiency = cache_hits / 1000 * 100 if cache_hits > 0 else 0
    print(f"📊 Эффективность кэша: {cache_efficiency:.1f}%")
    
    # Оценка производительности
    print("\n🏆 Оценка производительности:")
    if cache_efficiency > 95:
        print("✅ Отличная эффективность кэширования")
    elif cache_efficiency > 80:
        print("✅ Хорошая эффективность кэширования")
    else:
        print("⚠️  Низкая эффективность кэширования")
    
    if memory_change_mb < 10:
        print("✅ Отличное управление памятью")
    elif memory_change_mb < 50:
        print("✅ Хорошее управление памятью")
    else:
        print("⚠️  Высокое потребление памяти")
    
    if creation_time < 1.0:
        print("✅ Отличная скорость создания объектов")
    elif creation_time < 5.0:
        print("✅ Хорошая скорость создания объектов")
    else:
        print("⚠️  Медленная скорость создания объектов")
    
    return {
        "objects_created": objects_created,
        "cache_hits": cache_hits,
        "cache_efficiency": cache_efficiency,
        "memory_change_mb": memory_change_mb,
        "creation_time": creation_time,
        "cache_time": cache_time,
        "optimize_time": optimize_time,
        "objects_collected": collected
    }

def test_memory_pressure():
    """Тест под нагрузкой"""
    print("\n🔥 Тестирование под нагрузкой...")
    
    # Создаем большую нагрузку на память
    large_objects = []
    for i in range(100):
        large_obj = {
            "id": i,
            "data": "x" * 10000,  # 10KB на объект
            "nested": {"deep": {"data": "y" * 5000}}
        }
        large_objects.append(large_obj)
    
    # Измеряем память под нагрузкой
    pressure_memory = psutil.Process().memory_info()
    print(f"📊 Память под нагрузкой: {pressure_memory.rss / 1024 / 1024:.2f} MB")
    
    # Принудительная оптимизация
    collected = optimize_memory()
    after_optimize_memory = psutil.Process().memory_info()
    
    print(f"🧹 Собрано под нагрузкой: {collected} объектов")
    print(f"📊 Память после оптимизации: {after_optimize_memory.rss / 1024 / 1024:.2f} MB")
    
    # Очищаем
    del large_objects
    gc.collect()
    
    return collected

if __name__ == "__main__":
    print("🚀 Запуск тестов производительности оптимизации памяти VeilBot")
    print("=" * 60)
    
    try:
        # Основной тест производительности
        results = test_memory_performance()
        
        # Тест под нагрузкой
        pressure_results = test_memory_pressure()
        
        print("\n" + "=" * 60)
        print("🎉 Тесты производительности завершены успешно!")
        print("📊 Итоговая статистика:")
        print(f"   • Создано объектов: {results['objects_created']}")
        print(f"   • Эффективность кэша: {results['cache_efficiency']:.1f}%")
        print(f"   • Изменение памяти: {results['memory_change_mb']:+.2f} MB")
        print(f"   • Собрано объектов: {results['objects_collected'] + pressure_results}")
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
        sys.exit(1)
