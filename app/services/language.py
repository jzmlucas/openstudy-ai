"""Language preferences shared by the API and LLM orchestration."""

SUPPORTED_LANGUAGES = {
    "pt-BR": "Portuguese (Brazil)",
    "es": "Spanish",
    "en": "English",
}


def normalize_language(language: str | None) -> str:
    value = (language or "pt-BR").strip()
    aliases = {
        "pt": "pt-BR",
        "pt-br": "pt-BR",
        "es-ES": "es",
        "es-es": "es",
        "en-US": "en",
        "en-us": "en",
        "en-GB": "en",
        "en-gb": "en",
    }
    value = aliases.get(value, value)
    return value if value in SUPPORTED_LANGUAGES else "pt-BR"


def language_instruction(language: str | None) -> str:
    locale = normalize_language(language)
    label = SUPPORTED_LANGUAGES[locale]
    extra = {
        "pt-BR": (
            "Escreva todo o conteúdo apresentado ao estudante em português do Brasil. "
            "Mantenha nomes próprios, siglas técnicas e trechos citados na forma original."
        ),
        "es": (
            "Escreva todo el contenido dirigido al estudiante en español claro y neutro. "
            "Conserve los nombres propios, las siglas técnicas y las citas de las fuentes en su forma original."
        ),
        "en": (
            "Write all student-facing content in clear English. "
            "Keep proper names, technical acronyms, and quoted source excerpts in their original form."
        ),
    }[locale]
    return (
        f"LANGUAGE REQUIREMENT: The output language is {label} ({locale}). {extra} "
        "Keep JSON property names, machine-readable enum values, IDs, and numeric fields exactly "
        "as specified by the schema. Do not translate JSON keys or enum identifiers."
    )
