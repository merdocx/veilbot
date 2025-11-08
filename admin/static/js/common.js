let confirmHandlersAttached = false;

const debounce = (fn, wait = 300) => {
    let timeoutId;
    return (...args) => {
        const invoke = () => {
            timeoutId = undefined;
            fn(...args);
        };
        clearTimeout(timeoutId);
        timeoutId = window.setTimeout(invoke, wait);
    };
};

const createTableFilter = (tableId, debounceMs = 300) => {
    const table = document.getElementById(tableId);
    if (!table) {
        console.warn(`Table with id "${tableId}" not found`);
        return () => undefined;
    }

    let cachedRows = null;

    const performFilter = (searchTerm) => {
        if (!cachedRows) {
            cachedRows = Array.from(table.querySelectorAll('tbody tr'));
        }

        const normalized = (searchTerm || '').toLowerCase().trim();
        if (!normalized) {
            cachedRows.forEach((row) => {
                row.style.display = '';
            });
            return;
        }

        cachedRows.forEach((row) => {
            const cells = Array.from(row.querySelectorAll('td'));
            const searchable = cells.slice(0, -1);
            const found = searchable.some((cell) => {
                const text = (cell.textContent || '').toLowerCase();
                return text.includes(normalized);
            });
            row.style.display = found ? '' : 'none';
        });
    };

    return debounce(performFilter, debounceMs);
};

const ensureNotificationStyles = () => {
    if (document.getElementById('veilbot-notification-styles')) {
        return;
    }
    const style = document.createElement('style');
    style.id = 'veilbot-notification-styles';
    style.textContent = `
        @keyframes vb-slide-in {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
    `;
    document.head.appendChild(style);
};

const ensureNotificationContainer = () => {
    let container = document.querySelector('.veilbot-notification-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'veilbot-notification-container';
        container.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            z-index: 10001;
        `;
        container.setAttribute('role', 'region');
        container.setAttribute('aria-live', 'polite');
        container.setAttribute('aria-atomic', 'false');
        document.body.appendChild(container);
    }
    return container;
};

const showNotification = (message, type = 'info', duration = 3000) => {
    ensureNotificationStyles();
    const container = ensureNotificationContainer();

    const colors = {
        success: '#4caf50',
        error: '#f44336',
        warning: '#ff9800',
        info: '#2196f3',
    };

    const notification = document.createElement('div');
    notification.className = 'veilbot-notification';
    const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    notification.style.cssText = `
        display: inline-flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        min-width: 220px;
        max-width: 420px;
        background: ${colors[type] || colors.info};
        color: #fff;
        padding: 12px 16px;
        border-radius: 6px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.2);
        font-size: 14px;
        font-weight: 500;
        ${reducedMotion ? '' : 'animation: vb-slide-in 0.25s ease;'}
    `;
    notification.textContent = message;
    notification.setAttribute('role', type === 'error' ? 'alert' : 'status');
    notification.setAttribute('aria-live', type === 'error' ? 'assertive' : 'polite');
    notification.setAttribute('aria-atomic', 'true');
    notification.tabIndex = 0;

    const dismiss = () => notification.remove();
    notification.addEventListener('click', dismiss);

    container.appendChild(notification);
    if (duration > 0) {
        window.setTimeout(dismiss, duration);
    }
};

const postForm = async (url, formElementOrData) => {
    const body = formElementOrData instanceof FormData ? formElementOrData : new FormData(formElementOrData);
    const response = await fetch(url, {
        method: 'POST',
        body,
    });
    return response;
};

const showPageLoader = (show = true) => {
    let loader = document.getElementById('page-loader');

    if (show && !loader) {
        loader = document.createElement('div');
        loader.id = 'page-loader';
        loader.className = 'page-loader';
        loader.style.cssText = `
            position: fixed;
            inset: 0;
            background: rgba(255, 255, 255, 0.92);
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            gap: 16px;
            z-index: 10000;
        `;
        loader.innerHTML = `
            <div class="material-icons" style="font-size: 48px; color: #1976d2; animation: vb-spin 1s linear infinite;">refresh</div>
            <div style="font-size: 16px; color: #666;">Загрузка...</div>
        `;

        if (!document.getElementById('vb-spin-styles')) {
            const style = document.createElement('style');
            style.id = 'vb-spin-styles';
            style.textContent = `
                @keyframes vb-spin {
                    from { transform: rotate(0deg); }
                    to { transform: rotate(360deg); }
                }
            `;
            document.head.appendChild(style);
        }

        document.body.appendChild(loader);
    }

    if (loader) {
        if (show) {
            loader.style.display = 'flex';
        } else {
            loader.style.display = 'none';
            window.setTimeout(() => loader.remove(), 300);
        }
    }
};

const ensureSpinnerStyles = () => {
    if (document.getElementById('vb-spinner-styles')) {
        return;
    }
    const style = document.createElement('style');
    style.id = 'vb-spinner-styles';
    style.textContent = `
        @keyframes vb-spinner {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
    `;
    document.head.appendChild(style);
};

const showLoadingIndicator = (element) => {
    if (!element) return null;
    ensureSpinnerStyles();
    element.dataset.previousContent = element.innerHTML;
    element.innerHTML = `
        <span class="material-icons icon-small" style="animation: vb-spinner 1s linear infinite; display: inline-flex;">refresh</span>
    `;
    return element;
};

const restorePreviousContent = (element) => {
    if (!element || typeof element.dataset.previousContent === 'undefined') {
        return;
    }
    element.innerHTML = element.dataset.previousContent;
    delete element.dataset.previousContent;
};

const handleError = (error, context = '') => {
    const message = error instanceof Error ? error.message : String(error);
    const formatted = context ? `${context}: ${message}` : message;
    console.error('VeilBot error:', formatted);
    showNotification(formatted, 'error', 5000);
};

const updateProgressBars = (root = document) => {
    const bars = root.querySelectorAll('[data-progress]');
    bars.forEach((bar) => {
        const value = Number(bar.dataset.progress || 0);
        const normalized = Math.min(Math.max(value, 0), 100);
        bar.style.setProperty('--progress', `${normalized}%`);
        const fill = bar.querySelector('.progress-bar__fill');
        if (fill) {
            fill.style.width = `${normalized}%`;
        }
    });
};

const updateKeyRow = (key) => {
    if (!key) return;
    const row = document.querySelector(`.keys-table__row[data-key-id="${key.id}"]`);
    if (!row) return;

    row.dataset.protocol = key.protocol || '';
    row.dataset.status = key.status || '';
    row.dataset.expiryTs = key.expiry_at || '';
    row.dataset.expiryIso = key.expiry_iso || '';

    const statusIcon = row.querySelector('[data-field="status-icon"]');
    if (statusIcon) {
        statusIcon.textContent = key.status_icon;
        statusIcon.setAttribute('title', key.status_label);
        statusIcon.classList.remove('status-icon--active', 'status-icon--expired');
        statusIcon.classList.add(key.status_class);
    }

    const editButton = row.querySelector('[data-action="edit-key"]');
    if (editButton) {
        editButton.dataset.expiry = key.expiry_iso || '';
    }

    const trafficCell = row.querySelector('.traffic-cell');
    if (trafficCell && key.traffic) {
        trafficCell.dataset.trafficState = key.traffic.state || 'na';
        trafficCell.dataset.overLimit = key.traffic.over_limit ? '1' : '0';
        trafficCell.dataset.overLimitDeadline = key.traffic.over_limit_deadline || '';
        trafficCell.classList.toggle('traffic-cell--over-limit', Boolean(key.traffic.over_limit));

        const display = trafficCell.querySelector('[data-field="traffic-display"]');
        if (display) {
            display.textContent = key.traffic.display;
        }
        const limit = trafficCell.querySelector('[data-field="traffic-limit"]');
        if (limit) {
            limit.textContent = key.traffic.limit_display && key.traffic.limit_display !== '—'
                ? `Лимит: ${key.traffic.limit_display}`
                : 'Лимит не задан';
            if (!key.traffic.limit_display || key.traffic.limit_display === '—') {
                limit.classList.add('text-muted');
            } else {
                limit.classList.remove('text-muted');
            }
        }
        const warning = trafficCell.querySelector('[data-field="traffic-warning"]');
        if (warning) {
            if (key.traffic.over_limit) {
                const deadlineText = key.traffic.over_limit_deadline_display || '';
                warning.textContent = deadlineText
                    ? `Превышен лимит. ${deadlineText}`
                    : 'Превышен лимит. Ключ будет отключён без продления.';
                warning.classList.remove('hidden');
            } else {
                warning.textContent = '';
                warning.classList.add('hidden');
            }
        }

        const trafficBar = trafficCell.querySelector('.progress-bar');
        if (trafficBar) {
            const percent = key.traffic.usage_percent != null
                ? Math.round(key.traffic.usage_percent * 100)
                : 0;
            trafficBar.dataset.progress = percent;
        }
    }

    if (key.access_url) {
        const copyButton = row.querySelector('[data-action="copy-key"]');
        if (copyButton) {
            copyButton.dataset.key = key.access_url;
        }
    }

    updateProgressBars(row);
};

const loadTraffic = async (keyId) => {
    const cell = document.querySelector(`.traffic-cell[data-key-id="${keyId}"]`);
    if (!cell) return;

    showLoadingIndicator(cell);

    try {
        const response = await fetch(`/api/keys/${keyId}/traffic`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || 'Ошибка загрузки трафика');
        }

        restorePreviousContent(cell);
        if (data.key) {
            updateKeyRow(data.key);
        } else {
            const display = cell.querySelector('[data-field="traffic-display"]');
            if (display) {
                display.textContent = data.traffic;
            } else {
                cell.textContent = data.traffic;
            }
            updateProgressBars(cell);
        }
    } catch (error) {
        handleError(error, 'Ошибка загрузки трафика');
        restorePreviousContent(cell);
        cell.innerHTML = '';
        const retryButton = document.createElement('button');
        retryButton.className = 'btn btn-small';
        retryButton.type = 'button';
        retryButton.innerHTML = '<span class="material-icons icon-small">refresh</span> Повторить';
        retryButton.addEventListener('click', () => loadTraffic(keyId));
        cell.appendChild(retryButton);
    }
};

const initTableSearch = () => {
    const searchInput = document.getElementById('global-search');
    if (!searchInput) return;

    const tableId = searchInput.dataset.tableId
        || searchInput.closest('.card')?.querySelector('table')?.id
        || 'keys-table';

    const filter = createTableFilter(tableId, 250);
    searchInput.addEventListener('input', (event) => {
        filter(event.target.value);
    });

    const resetButton = document.getElementById('reset-search-btn');
    if (resetButton) {
        resetButton.addEventListener('click', () => {
            searchInput.value = '';
            filter('');
            searchInput.focus();
        });
    }
};

const initLazyTrafficLoading = () => {
    // Placeholder for future IntersectionObserver integration.
};

const highlightActiveNavigation = () => {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach((link) => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        }
    });
};

const initNavigationLoader = () => {
    document.querySelectorAll('a.nav-link').forEach((link) => {
        link.addEventListener('click', (event) => {
            const href = link.getAttribute('href') || '';
            if (!href.startsWith('/')) {
                return;
            }
            if (event.metaKey || event.ctrlKey || event.shiftKey) {
                return;
            }
            showPageLoader(true);
        });
    });
};

class ModalController {
    constructor(element) {
        this.element = element;
        this.focusTrap = null;
        this.closeButtons = [];
        this.previouslyFocused = null;
        this.handleKeyDown = this.handleKeyDown.bind(this);
        this.onBackdropClick = this.onBackdropClick.bind(this);
    }

    open() {
        if (!this.element) return;
        this.previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        this.element.classList.add('vb-modal--open');
        this.element.setAttribute('aria-hidden', 'false');
        this.element.setAttribute('aria-modal', 'true');
        document.body.classList.add('vb-modal-open');
        this.bindEvents();
        const focusTarget = this.element.querySelector('[data-modal-focus]')
            || this.element.querySelector('input, button, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (focusTarget && typeof focusTarget.focus === 'function') {
            focusTarget.focus();
        }
    }

    close() {
        if (!this.element) return;
        this.element.classList.remove('vb-modal--open');
        this.element.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('vb-modal-open');
        if (this.previouslyFocused && typeof this.previouslyFocused.focus === 'function') {
            this.previouslyFocused.focus();
            this.previouslyFocused = null;
        }
        this.unbindEvents();
    }

    bindEvents() {
        this.element.addEventListener('click', this.onBackdropClick);
        this.element.addEventListener('keydown', this.handleKeyDown);
        this.closeButtons = Array.from(this.element.querySelectorAll('[data-modal-close]'));
        this.closeButtons.forEach((btn) => btn.addEventListener('click', this.onBackdropClick));
    }

    unbindEvents() {
        this.element.removeEventListener('click', this.onBackdropClick);
        this.element.removeEventListener('keydown', this.handleKeyDown);
        if (this.closeButtons) {
            this.closeButtons.forEach((btn) => btn.removeEventListener('click', this.onBackdropClick));
            this.closeButtons = null;
        }
    }

    onBackdropClick(event) {
        if (event.target === this.element || event.target.closest('[data-modal-close]')) {
            this.close();
        }
    }

    handleKeyDown(event) {
        if (event.key === 'Escape') {
            this.close();
        }
    }
}

const attachConfirmHandlers = () => {
    if (confirmHandlersAttached) {
        return;
    }
    confirmHandlersAttached = true;

    document.addEventListener('click', (event) => {
        const target = event.target.closest('[data-confirm]');
        if (!target) {
            return;
        }
        if (target.tagName.toLowerCase() === 'form') {
            return;
        }
        const message = target.dataset.confirm;
        if (!message) {
            return;
        }
        if (!window.confirm(message)) {
            event.preventDefault();
            event.stopPropagation();
        }
    });

    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) {
            return;
        }
        if (!form.matches('[data-confirm]')) {
            return;
        }
        const message = form.dataset.confirm;
        if (!message) {
            return;
        }
        if (!window.confirm(message)) {
            event.preventDefault();
        }
    });
};

const attachClearHandlers = () => {
    document.querySelectorAll('[data-clear-target]:not([data-clear-bound])').forEach((button) => {
        button.dataset.clearBound = '1';
        button.addEventListener('click', () => {
            const selector = button.dataset.clearTarget || '';
            const eventType = button.dataset.clearEvent || 'input';
            if (!selector) {
                return;
            }
            selector.split(',').forEach((raw) => {
                const trimmed = raw.trim();
                if (!trimmed) {
                    return;
                }
                const element = document.querySelector(trimmed);
                if (!element) {
                    return;
                }
                if ('value' in element) {
                    element.value = '';
                }
                if (eventType) {
                    element.dispatchEvent(new Event(eventType, { bubbles: true }));
                }
            });
        });
    });
};

const attachBackHandlers = () => {
    document.querySelectorAll('[data-go-back]:not([data-back-bound])').forEach((element) => {
        element.dataset.backBound = '1';
        element.addEventListener('click', (event) => {
            event.preventDefault();
            if (window.history.length > 1) {
                window.history.back();
            } else {
                window.location.href = element.getAttribute('href') || '/dashboard';
            }
        });
    });
};

const initializeCommon = () => {
    initTableSearch();
    initLazyTrafficLoading();
    highlightActiveNavigation();
    initNavigationLoader();
    updateProgressBars();
    showPageLoader(false);
    attachConfirmHandlers();
    attachClearHandlers();
    attachBackHandlers();
};

const VeilBotCommon = {
    debounce,
    createTableFilter,
    showNotification,
    initTableSearch,
    showLoadingIndicator,
    showPageLoader,
    handleError,
    loadTraffic,
    updateKeyRow,
    updateProgressBars,
    updateKeyRow,
    ModalController,
    postForm,
    attachConfirmHandlers,
    attachClearHandlers,
    attachBackHandlers,
};

if (typeof window !== 'undefined') {
    window.VeilBotCommon = VeilBotCommon;
    window.addEventListener('error', (event) => {
        if (!event) return;
        handleError(event.error || event.message, 'JavaScript error');
    });
    window.addEventListener('unhandledrejection', (event) => {
        handleError(event.reason, 'Promise rejection');
        event.preventDefault();
    });
    document.addEventListener('DOMContentLoaded', initializeCommon);
}

export {
    debounce,
    createTableFilter,
    showNotification,
    initTableSearch,
    showLoadingIndicator,
    showPageLoader,
    handleError,
    loadTraffic,
    updateProgressBars,
    updateKeyRow,
    updateKeyRow,
    ModalController,
    postForm,
    VeilBotCommon,
};

export default VeilBotCommon;
