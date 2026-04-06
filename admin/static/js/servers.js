const state = {
    v2rayFields: null,
};

const toggleProtocolFields = () => {
    const protocolSelect = document.getElementById('protocol');
    if (!protocolSelect) return;

    state.v2rayFields?.classList.remove('hidden');
    const apiKey = document.getElementById('api_key');
    if (apiKey) apiKey.required = true;
};

const initServersPage = () => {
    state.v2rayFields = document.getElementById('v2ray-fields');

    const protocolSelect = document.getElementById('protocol');
    if (protocolSelect) {
        protocolSelect.addEventListener('change', toggleProtocolFields);
        toggleProtocolFields();
    }

    if (typeof window.initLiveSearch === 'function') {
        window.initLiveSearch({
            pageUrl: '/servers',
            tableSelector: '#servers-table',
            statsSelector: '.stats-grid',
        });
    } else {
        console.warn('[VeilBot][servers] initLiveSearch недоступен');
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initServersPage);
} else {
    initServersPage();
}

export {};
