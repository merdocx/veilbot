module.exports = {
    tabWidth: 4,
    printWidth: 100,
    singleQuote: false,
    trailingComma: "es5",
    bracketSpacing: true,
    htmlWhitespaceSensitivity: "ignore",
    overrides: [
        {
            files: "*.html",
            options: {
                parser: "html",
            },
        },
        {
            files: "*.md",
            options: {
                proseWrap: "always",
            },
        },
    ],
};

