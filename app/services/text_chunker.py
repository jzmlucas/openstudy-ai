import re


def chunk_text(text: str, max_chars: int = 6_000, overlap: int = 450) -> list[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not cleaned:
        return []
    if max_chars < 500:
        raise ValueError("max_chars deve ser pelo menos 500.")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap deve ser maior ou igual a zero e menor que max_chars.")
    if len(cleaned) <= max_chars:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + max_chars, len(cleaned))
        if end < len(cleaned):
            boundary = max(
                cleaned.rfind("\n\n", start + max_chars // 2, end),
                cleaned.rfind("\n", start + max_chars // 2, end),
                cleaned.rfind(" ", start + max_chars // 2, end),
            )
            if boundary > start:
                end = boundary
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(cleaned):
            break
        start = max(start + 1, end - overlap)
    return chunks
