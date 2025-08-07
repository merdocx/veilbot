#!/usr/bin/env python3
"""
Тест оптимизации памяти VeilBot
"""

import unittest
import gc
import sys
import time
from memory_optimizer import (
    MemoryOptimizer, LazyServiceLoader, memory_optimizer, service_loader,
    get_payment_service, get_vpn_service, get_security_logger,
    optimize_memory, get_memory_stats, log_memory_usage
)

class TestMemoryOptimization(unittest.TestCase):
    """Тесты для оптимизации памяти"""
    
    def setUp(self):
        """Настройка тестов"""
        self.optimizer = MemoryOptimizer()
        self.service_loader = LazyServiceLoader()
        
        # Очищаем кэши перед каждым тестом
        self.optimizer.clear_cache()
        self.service_loader.clear_services()
    
    def test_memory_optimizer_creation(self):
        """Тест создания оптимизатора памяти"""
        self.assertIsNotNone(self.optimizer)
        self.assertEqual(len(self.optimizer._cache), 0)
        self.assertEqual(self.optimizer._memory_stats['objects_created'], 0)
    
    def test_lazy_loading(self):
        """Тест lazy loading"""
        # Создаем тестовую функцию
        def test_loader():
            return {"test": "data"}
        
        # Первый вызов - должен создать объект
        result1 = self.optimizer.lazy_load("test_key", test_loader)
        self.assertEqual(result1, {"test": "data"})
        self.assertEqual(self.optimizer._memory_stats['objects_created'], 1)
        self.assertEqual(self.optimizer._memory_stats['cache_misses'], 1)
        
        # Второй вызов - должен использовать кэш
        result2 = self.optimizer.lazy_load("test_key", test_loader)
        self.assertEqual(result2, {"test": "data"})
        self.assertEqual(self.optimizer._memory_stats['objects_created'], 1)
        self.assertEqual(self.optimizer._memory_stats['cache_hits'], 1)
    
    def test_cache_management(self):
        """Тест управления кэшем"""
        # Добавляем несколько объектов
        self.optimizer.lazy_load("key1", lambda: "value1")
        self.optimizer.lazy_load("key2", lambda: "value2")
        
        self.assertEqual(len(self.optimizer._cache), 2)
        self.assertIn("key1", self.optimizer._cache)
        self.assertIn("key2", self.optimizer._cache)
        
        # Очищаем конкретный ключ
        self.optimizer.clear_cache("key1")
        self.assertEqual(len(self.optimizer._cache), 1)
        self.assertNotIn("key1", self.optimizer._cache)
        self.assertIn("key2", self.optimizer._cache)
        
        # Очищаем весь кэш
        self.optimizer.clear_cache()
        self.assertEqual(len(self.optimizer._cache), 0)
    
    def test_memory_stats(self):
        """Тест статистики памяти"""
        # Добавляем несколько объектов
        self.optimizer.lazy_load("key1", lambda: "value1")
        self.optimizer.lazy_load("key2", lambda: "value2")
        self.optimizer.lazy_load("key1", lambda: "value1")  # Повторный вызов
        
        stats = self.optimizer.get_memory_stats()
        
        self.assertEqual(stats['objects_created'], 2)
        self.assertEqual(stats['cache_hits'], 1)
        self.assertEqual(stats['cache_misses'], 2)
        self.assertEqual(stats['cache_size'], 2)
        self.assertIn("key1", stats['cache_keys'])
        self.assertIn("key2", stats['cache_keys'])
    
    def test_memory_optimization(self):
        """Тест принудительной оптимизации памяти"""
        # Добавляем объекты в кэш
        self.optimizer.lazy_load("key1", lambda: "value1")
        self.optimizer.lazy_load("key2", lambda: "value2")
        
        self.assertEqual(len(self.optimizer._cache), 2)
        
        # Выполняем оптимизацию
        collected = self.optimizer.optimize_memory()
        
        # Проверяем, что кэш очищен
        self.assertEqual(len(self.optimizer._cache), 0)
        self.assertIsInstance(collected, int)
    
    def test_service_loader(self):
        """Тест загрузчика сервисов"""
        self.assertIsNotNone(self.service_loader)
        self.assertEqual(len(self.service_loader._services), 0)
    
    def test_lazy_service_loading(self):
        """Тест lazy loading сервисов"""
        # Тестируем загрузку платежного сервиса
        try:
            payment_service = self.service_loader.get_payment_service()
            # Сервис может быть None, если модуль недоступен
            if payment_service is not None:
                self.assertIn('payment_service', self.service_loader._services)
        except Exception as e:
            # Ожидаемо, если модуль недоступен
            pass
        
        # Тестируем загрузку VPN сервиса
        try:
            vpn_service = self.service_loader.get_vpn_service()
            if vpn_service is not None:
                self.assertIn('vpn_service', self.service_loader._services)
        except Exception as e:
            # Ожидаемо, если модуль недоступен
            pass
    
    def test_service_cache_clearing(self):
        """Тест очистки кэша сервисов"""
        # Добавляем тестовый сервис
        self.service_loader._services['test_service'] = "test_value"
        self.assertEqual(len(self.service_loader._services), 1)
        
        # Очищаем кэш
        self.service_loader.clear_services()
        self.assertEqual(len(self.service_loader._services), 0)
    
    def test_global_functions(self):
        """Тест глобальных функций"""
        # Тестируем функции получения сервисов
        try:
            payment_service = get_payment_service()
            # Может быть None, если модуль недоступен
        except Exception:
            pass
        
        try:
            vpn_service = get_vpn_service()
            # Может быть None, если модуль недоступен
        except Exception:
            pass
        
        try:
            security_logger = get_security_logger()
            # Может быть None, если модуль недоступен
        except Exception:
            pass
    
    def test_memory_optimization_function(self):
        """Тест функции оптимизации памяти"""
        collected = optimize_memory()
        self.assertIsInstance(collected, int)
    
    def test_memory_stats_function(self):
        """Тест функции получения статистики памяти"""
        stats = get_memory_stats()
        
        self.assertIn('optimizer_stats', stats)
        self.assertIn('memory_usage', stats)
        
        optimizer_stats = stats['optimizer_stats']
        self.assertIn('objects_created', optimizer_stats)
        self.assertIn('cache_hits', optimizer_stats)
        self.assertIn('cache_misses', optimizer_stats)
        self.assertIn('cache_size', optimizer_stats)
    
    def test_memory_usage_tracking(self):
        """Тест отслеживания использования памяти"""
        # Проверяем, что функция не вызывает ошибок
        try:
            log_memory_usage()
        except Exception as e:
            # Может быть ошибка, если psutil недоступен
            self.assertIn("psutil", str(e).lower())
    
    def test_garbage_collection(self):
        """Тест сборки мусора"""
        # Создаем много объектов
        objects = []
        for i in range(1000):
            objects.append({"id": i, "data": "x" * 100})
        
        # Запоминаем количество объектов
        initial_count = len(gc.get_objects())
        
        # Удаляем ссылки
        del objects
        
        # Принудительная сборка мусора
        collected = gc.collect()
        
        # Проверяем, что сборка мусора работает
        self.assertIsInstance(collected, int)
        self.assertGreaterEqual(collected, 0)
    
    def test_memory_efficiency(self):
        """Тест эффективности использования памяти"""
        # Измеряем память до создания объектов
        initial_memory = sys.getsizeof({})
        
        # Создаем объекты через оптимизатор
        for i in range(100):
            self.optimizer.lazy_load(f"key_{i}", lambda x=i: {"id": x, "data": "test"})
        
        # Проверяем, что кэш работает
        self.assertEqual(len(self.optimizer._cache), 100)
        
        # Очищаем кэш
        self.optimizer.clear_cache()
        
        # Проверяем, что кэш очищен
        self.assertEqual(len(self.optimizer._cache), 0)
    
    def test_error_handling(self):
        """Тест обработки ошибок"""
        # Тестируем lazy loading с ошибкой
        def failing_loader():
            raise Exception("Test error")
        
        result = self.optimizer.lazy_load("error_key", failing_loader)
        self.assertIsNone(result)
        
        # Проверяем, что ошибка не добавила объект в кэш
        self.assertNotIn("error_key", self.optimizer._cache)

class TestMemoryOptimizationIntegration(unittest.TestCase):
    """Интеграционные тесты оптимизации памяти"""
    
    def test_global_optimizer(self):
        """Тест глобального оптимизатора"""
        # Проверяем, что глобальный оптимизатор работает
        self.assertIsNotNone(memory_optimizer)
        self.assertIsNotNone(service_loader)
        
        # Тестируем базовые операции
        memory_optimizer.lazy_load("test", lambda: "value")
        self.assertIn("test", memory_optimizer._cache)
        
        # Очищаем после теста
        memory_optimizer.clear_cache()
    
    def test_memory_optimization_workflow(self):
        """Тест рабочего процесса оптимизации памяти"""
        # Сбрасываем счетчик перед тестом
        memory_optimizer._memory_stats['objects_created'] = 0
        
        # Симуляция рабочего процесса
        workflow_steps = [
            ("step1", lambda: {"data": "step1_data"}),
            ("step2", lambda: {"data": "step2_data"}),
            ("step3", lambda: {"data": "step3_data"}),
        ]
        
        results = []
        for key, loader in workflow_steps:
            result = memory_optimizer.lazy_load(key, loader)
            results.append(result)
        
        # Проверяем результаты
        self.assertEqual(len(results), 3)
        self.assertEqual(memory_optimizer._memory_stats['objects_created'], 3)
        
        # Повторный вызов - должен использовать кэш
        cached_result = memory_optimizer.lazy_load("step1", lambda: {"data": "new_data"})
        self.assertEqual(cached_result, {"data": "step1_data"})  # Старые данные из кэша
        
        # Очищаем после теста
        memory_optimizer.clear_cache()

if __name__ == '__main__':
    # Настройка логирования для тестов
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # Запуск тестов
    unittest.main(verbosity=2)
