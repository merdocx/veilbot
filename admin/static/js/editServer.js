const showProtocolFields = (protocol) => {
    const outlineFields = document.querySelectorAll('.outline-fields');
    const v2rayFields = document.querySelectorAll('.v2ray-fields');

    outlineFields.forEach((field) => {
        field.style.display = protocol === 'outline' ? 'block' : 'none';
    });

    v2rayFields.forEach((field) => {
        field.style.display = protocol === 'v2ray' ? 'block' : 'none';
    });
};

const initEditServerPage = () => {
    const protocolField = document.querySelector('input[name="protocol"]');
    const protocol = protocolField?.value || 'outline';
    showProtocolFields(protocol);
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initEditServerPage);
} else {
    initEditServerPage();
}

export {};
