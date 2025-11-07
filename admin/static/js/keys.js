(() => {
    const notify = (message, type = 'info') => {
        if (window.VeilBotCommon && typeof window.VeilBotCommon.showNotification === 'function') {
            window.VeilBotCommon.showNotification(message, type);
        } else {
            alert(message);
        }
    };

    const hidePageLoader = () => {
        if (window.VeilBotCommon && typeof window.VeilBotCommon.showPageLoader === 'function') {
            window.VeilBotCommon.showPageLoader(false);
        }
    };

    const showPageLoader = () => {
        if (window.VeilBotCommon && typeof window.VeilBotCommon.showPageLoader === 'function') {
            window.VeilBotCommon.showPageLoader(true);
        }
    };

    const copyToClipboard = async (value) => {
        const text = value || '';
        if (!text) {
            return;
        }

        try {
            if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                await navigator.clipboard.writeText(text);
            } else {
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.focus();
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
            }
            notify('Ключ скопирован!', 'success');
        } catch (error) {
            console.error('Не удалось скопировать ключ', error);
            notify('Не удалось скопировать ключ', 'error');
        }
    };

    document.addEventListener('DOMContentLoaded', () => {
        const modal = document.getElementById('editModal');
        const editForm = document.getElementById('editForm');
        const expiryInput = document.getElementById('new_expiry');
        const searchInput = document.getElementById('global-search');
        const resetSearchBtn = document.getElementById('reset-search-btn');

        const closeModal = () => {
            if (modal) {
                modal.style.display = 'none';
            }
        };

        const openModal = (keyId, expiryValue) => {
            if (!modal || !editForm || !expiryInput) {
                return;
            }
            editForm.action = `/keys/edit/${keyId}`;
            expiryInput.value = expiryValue || '';
            modal.style.display = 'flex';
        };

        document.querySelectorAll('[data-action="copy-key"]').forEach((button) => {
            button.addEventListener('click', () => {
                copyToClipboard(button.dataset.key || '');
            });
        });

        document.querySelectorAll('[data-action="edit-key"]').forEach((button) => {
            button.addEventListener('click', () => {
                const keyId = button.dataset.keyId;
                if (!keyId) {
                    return;
                }
                openModal(keyId, button.dataset.expiry || '');
            });
        });

        document.querySelectorAll('[data-action="close-edit-modal"]').forEach((button) => {
            button.addEventListener('click', () => {
                closeModal();
            });
        });

        if (modal) {
            modal.addEventListener('click', (event) => {
                if (event.target === modal) {
                    closeModal();
                }
            });
        }

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && modal && modal.style.display === 'flex') {
                closeModal();
            }
        });

        if (resetSearchBtn && searchInput) {
            resetSearchBtn.addEventListener('click', () => {
                searchInput.value = '';
                const inputEvent = new Event('input', { bubbles: true });
                searchInput.dispatchEvent(inputEvent);
            });
        }

        document.querySelectorAll('[data-action="delete-key"]').forEach((link) => {
            link.addEventListener('click', (event) => {
                const keyId = link.dataset.keyId || '';
                const confirmed = window.confirm(keyId ? `Удалить ключ ${keyId}?` : 'Удалить ключ?');
                if (!confirmed) {
                    event.preventDefault();
                    return;
                }
                showPageLoader();
            });
        });

        hidePageLoader();
    });
})();


