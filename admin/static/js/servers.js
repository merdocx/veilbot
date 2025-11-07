const state = {
    outlineFields: null,
    v2rayFields: null,
    filters: {
        id: null,
        name: null,
        protocol: null,
        country: null,
    },
};

const applyFilters = () => {
    const table = document.getElementById('servers-table');
    if (!table) return;

    const rows = table.querySelectorAll('tbody tr');
    const idFilter = (state.filters.id?.value || '').toLowerCase();
    const nameFilter = (state.filters.name?.value || '').toLowerCase();
    const protocolFilter = (state.filters.protocol?.value || '').toLowerCase();
    const countryFilter = (state.filters.country?.value || '').toLowerCase();

    rows.forEach((row) => {
        const cells = row.querySelectorAll('td');
        if (cells.length < 8) {
            return;
        }

        const id = cells[0].textContent.toLowerCase();
        const name = cells[1].textContent.toLowerCase();
        const protocol = cells[2].textContent.toLowerCase();
        const country = cells[7].textContent.toLowerCase();

        const matches = (!idFilter || id.includes(idFilter)) &&
            (!nameFilter || name.includes(nameFilter)) &&
            (!protocolFilter || protocol.includes(protocolFilter)) &&
            (!countryFilter || country.includes(countryFilter));

        row.style.display = matches ? '' : 'none';
    });
};

const clearFilters = () => {
    Object.values(state.filters).forEach((input) => {
        if (input) {
            input.value = '';
        }
    });
    applyFilters();
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

    state.filters.id = document.getElementById('filter-id');
    state.filters.name = document.getElementById('filter-name');
    state.filters.protocol = document.getElementById('filter-protocol');
    state.filters.country = document.getElementById('filter-country');

    const filterInputs = Object.values(state.filters).filter(Boolean);
    filterInputs.forEach((input) => {
        input.addEventListener('input', applyFilters);
    });

    document.querySelectorAll('[data-action="clear-server-filters"]').forEach((button) => {
        button.addEventListener('click', clearFilters);
    });

    const protocolSelect = document.getElementById('protocol');
    if (protocolSelect) {
        protocolSelect.addEventListener('change', toggleProtocolFields);
        toggleProtocolFields();
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initServersPage);
} else {
    initServersPage();
}

export {};
