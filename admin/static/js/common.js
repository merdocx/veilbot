/**
 * Общие JavaScript функции для админки VeilBot
 */

/**
 * Дебаунс функция - откладывает выполнение до истечения времени ожидания
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Создает универсальную функцию фильтрации таблиц с дебаунсом и кэшированием
 * @param {string} tableId - ID таблицы для фильтрации
 * @param {number} debounceMs - Задержка дебаунса в миллисекундах (по умолчанию 300)
 * @returns {Function} Функция фильтрации с дебаунсом
 */
function createTableFilter(tableId, debounceMs = 300) {
    const table = document.getElementById(tableId);
    if (!table) {
        console.warn(`Table with id "${tableId}" not found`);
        return () => {};
    }
    
    let cachedRows = null;
    
    const filterFunction = (searchTerm) => {
        // Кэшируем строки при первом вызове
        if (!cachedRows) {
            cachedRows = Array.from(table.querySelectorAll('tbody tr'));
        }
        
        const term = searchTerm.toLowerCase().trim();
        
        if (term === '') {
            // Показываем все строки если поиск пустой
            cachedRows.forEach(row => {
                row.style.display = '';
            });
            return;
        }
        
        // Фильтруем строки
        cachedRows.forEach(row => {
            const cells = Array.from(row.querySelectorAll('td'));
            // Исключаем последнюю колонку (Actions)
            const searchableCells = cells.slice(0, -1);
            const found = searchableCells.some(cell => {
                const text = (cell.textContent || cell.innerText || '').toLowerCase();
                return text.includes(term);
            });
            row.style.display = found ? '' : 'none';
        });
    };
    
    // Возвращаем функцию с дебаунсом
    return debounce(filterFunction, debounceMs);
}

/**
 * Универсальная функция показа уведомлений
 * @param {string} message - Сообщение для отображения
 * @param {string} type - Тип уведомления: 'success', 'error', 'warning', 'info'
 * @param {number} duration - Длительность отображения в мс (по умолчанию 3000)
 */
function showNotification(message, type = 'info', duration = 3000) {
    // Удаляем существующие уведомления
    const existing = document.querySelectorAll('.veilbot-notification');
    existing.forEach(el => el.remove());
    
    const notification = document.createElement('div');
    notification.className = 'veilbot-notification';
    notification.textContent = message;
    
    // Цвета в зависимости от типа
    const colors = {
        'success': '#4caf50',
        'error': '#f44336',
        'warning': '#ff9800',
        'info': '#2196f3'
    };
    
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: ${colors[type] || colors.info};
        color: white;
        padding: 12px 16px;
        border-radius: 4px;
        z-index: 10001;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        font-size: 14px;
        font-weight: 500;
        max-width: 400px;
        word-wrap: break-word;
        animation: slideIn 0.3s ease-out;
    `;
    
    // Добавляем анимацию
    const style = document.createElement('style');
    style.textContent = `
        @keyframes slideIn {
            from {
                transform: translateX(100%);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }
    `;
    if (!document.querySelector('#veilbot-notification-styles')) {
        style.id = 'veilbot-notification-styles';
        document.head.appendChild(style);
    }
    
    document.body.appendChild(notification);
    
    // Удаляем через duration
    setTimeout(() => {
        notification.style.animation = 'slideIn 0.3s ease-out reverse';
        setTimeout(() => notification.remove(), 300);
    }, duration);
}

/**
 * Инициализация поиска для страницы
 * Автоматически находит input с id="global-search" и привязывает фильтр
 */
function initTableSearch() {
    const searchInput = document.getElementById('global-search');
    if (!searchInput) {
        return;
    }
    
    // Получаем ID таблицы из data-атрибута или используем значение по умолчанию
    const tableId = searchInput.dataset.tableId || 
                   searchInput.closest('.card')?.querySelector('table')?.id || 
                   'keys-table';
    
    const filterFn = createTableFilter(tableId, 300);
    
    // Обработчик ввода с дебаунсом
    searchInput.addEventListener('input', (e) => {
        filterFn(e.target.value);
    });
    
    // Обработчик очистки
    const clearBtn = document.querySelector('[onclick*="clearSearch"]');
    if (clearBtn) {
        clearBtn.addEventListener('click', (e) => {
            e.preventDefault();
            searchInput.value = '';
            filterFn('');
        });
    }
}

/**
 * Показывает глобальный индикатор загрузки страницы
 * @param {boolean} show - Показать или скрыть
 */
function showPageLoader(show = true) {
    let loader = document.getElementById('page-loader');
    
    if (show && !loader) {
        loader = document.createElement('div');
        loader.id = 'page-loader';
        loader.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(255, 255, 255, 0.9);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 10000;
            flex-direction: column;
            gap: 16px;
        `;
        loader.innerHTML = `
            <div class="material-icons" style="font-size: 48px; color: #1976d2; animation: spin 1s linear infinite;">refresh</div>
            <div style="font-size: 16px; color: #666;">Загрузка...</div>
        `;
        document.body.appendChild(loader);
    } else if (loader) {
        loader.style.display = show ? 'flex' : 'none';
        if (!show) {
            setTimeout(() => loader.remove(), 300);
        }
    }
}

/**
 * Показывает индикатор загрузки для элемента
 * @param {HTMLElement} element - Элемент для показа индикатора
 */
function showLoadingIndicator(element) {
    if (!element) return;
    
    const spinner = document.createElement('span');
    spinner.className = 'loading-spinner';
    spinner.innerHTML = '<span class="material-icons icon-small" style="animation: spin 1s linear infinite;">refresh</span>';
    spinner.style.cssText = 'display: inline-flex; align-items: center;';
    
    const style = document.createElement('style');
    style.id = 'spinner-styles';
    if (!document.getElementById('spinner-styles')) {
        style.textContent = `
            @keyframes spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
        `;
        document.head.appendChild(style);
    }
    
    element.innerHTML = '';
    element.appendChild(spinner);
    return spinner;
}

/**
 * Централизованная обработка ошибок
 * @param {Error|string} error - Ошибка для обработки
 * @param {string} context - Контекст ошибки (опционально)
 */
function handleError(error, context = '') {
    const errorMessage = error instanceof Error ? error.message : String(error);
    const fullMessage = context ? `${context}: ${errorMessage}` : errorMessage;
    
    console.error('VeilBot Error:', fullMessage);
    showNotification(fullMessage, 'error', 5000);
}

/**
 * Загрузка трафика для ключа по требованию
 * @param {number} keyId - ID ключа
 */
async function loadTraffic(keyId) {
    const cell = document.querySelector(`.traffic-cell[data-key-id="${keyId}"]`);
    if (!cell) return;
    
    // Показываем индикатор загрузки
    showLoadingIndicator(cell);
    
    try {
        const response = await fetch(`/api/keys/${keyId}/traffic`);
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || 'Ошибка загрузки трафика');
        }
        
        // Показываем результат
        cell.innerHTML = `<span class="traffic-value">${data.traffic}</span>`;
        
    } catch (error) {
        handleError(error, 'Ошибка загрузки трафика');
        // Восстанавливаем кнопку для повторной попытки
        cell.innerHTML = `
            <button class="btn btn-small btn-load-traffic" onclick="loadTraffic(${keyId})" title="Повторить загрузку">
                <span class="material-icons icon-small">refresh</span>
                Повторить
            </button>
        `;
    }
}

/**
 * Инициализация всех общих функций при загрузке DOM
 */
document.addEventListener('DOMContentLoaded', function() {
    // Инициализация поиска
    initTableSearch();
    
    // Инициализация ленивой загрузки трафика
    initLazyTrafficLoading();
    
    // Выделение активной страницы в навигации
    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll('.nav-link');
    navLinks.forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        }
    });
    
    // Показываем индикатор загрузки при переходе на другие страницы
    document.querySelectorAll('a.nav-link').forEach(link => {
        link.addEventListener('click', () => {
            if (link.getAttribute('href').startsWith('/')) {
                showPageLoader(true);
            }
        });
    });
});

// Глобальный обработчик ошибок
window.addEventListener('error', (event) => {
    handleError(event.error || event.message, 'JavaScript Error');
});

// Обработка необработанных промисов
window.addEventListener('unhandledrejection', (event) => {
    handleError(event.reason, 'Promise Rejection');
    event.preventDefault();
});

/**
 * Инициализация ленивой загрузки трафика при скролле (опционально)
 */
function initLazyTrafficLoading() {
    // Можно добавить Intersection Observer для автозагрузки при скролле
    // Пока загрузка по требованию через кнопку
}

// Экспорт функций для использования в других скриптах
window.VeilBotCommon = {
    debounce,
    createTableFilter,
    showNotification,
    initTableSearch,
    showLoadingIndicator,
    showPageLoader,
    handleError,
    loadTraffic
};

