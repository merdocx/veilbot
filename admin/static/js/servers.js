const state = {
    outlineFields: null,
    v2rayFields: null,
};

const toggleProtocolFields = () => {
    const protocolSelect = document.getElementById('protocol');
    if (!protocolSelect) return;

    const protocol = protocolSelect.value;
    const certSha256 = document.getElementById('cert_sha256');
    const apiKey = document.getElementById('api_key');

    if (protocol === 'v2ray') {
        state.outlineFields?.classList.add('hidden');
        state.v2rayFields?.classList.remove('hidden');
        if (certSha256) certSha256.required = false;
        if (apiKey) apiKey.required = true;
    } else {
        state.outlineFields?.classList.remove('hidden');
        state.v2rayFields?.classList.add('hidden');
        if (certSha256) certSha256.required = false;
        if (apiKey) apiKey.required = false;
    }
};

const initServersPage = () => {
    state.outlineFields = document.getElementById('outline-fields');
    state.v2rayFields = document.getElementById('v2ray-fields');

    const protocolSelect = document.getElementById('protocol');
    if (protocolSelect) {
        protocolSelect.addEventListener('change', toggleProtocolFields);
        toggleProtocolFields();
    }

    if (window.VeilBotCommon && typeof window.VeilBotCommon.initTableSearch === 'function') {
        window.VeilBotCommon.initTableSearch({
            tableSelector: '#servers-table',
        });
    } else {
        console.warn('[VeilBot][servers] initTableSearch недоступен');
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initServersPage);
} else {
    initServersPage();
}

export {};
