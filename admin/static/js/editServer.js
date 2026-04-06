const showProtocolFields = () => {
    const v2rayFields = document.querySelectorAll('.v2ray-fields');
    v2rayFields.forEach((field) => {
        field.style.display = 'block';
    });
};

const initEditServerPage = () => {
    showProtocolFields();
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initEditServerPage);
} else {
    initEditServerPage();
}

export {};
