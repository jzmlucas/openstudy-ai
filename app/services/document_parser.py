from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md"}


def validate_extension(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Formato não suportado. Utilize: {allowed}.")
    return extension


def extract_text(file_bytes: bytes, extension: str) -> str:
    extension = extension.lower()

    if extension == ".md":
        text = file_bytes.decode("utf-8-sig", errors="replace")

    elif extension == ".docx":
        document = DocxDocument(BytesIO(file_bytes))
        parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        text = "\n".join(parts)

    elif extension == ".pdf":
        reader = PdfReader(BytesIO(file_bytes))
        pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(page_text)
        text = "\n\n".join(pages)
        if not text.strip():
            raise ValueError(
                "Não foi possível extrair texto deste PDF. "
                "Ele pode ser digitalizado como imagem; OCR ainda não está habilitado."
            )

    else:
        raise ValueError("Extensão de arquivo não suportada.")

    text = text.replace("\x00", "").strip()
    if not text:
        raise ValueError("O arquivo não contém texto legível.")
    return text


def extract_text_pages(file_bytes: bytes, extension: str) -> list[tuple[int, str]]:
    """Extrai texto preservando número de página quando a fonte é PDF."""
    extension = extension.lower()
    if extension == ".pdf":
        reader = PdfReader(BytesIO(file_bytes))
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            content = (page.extract_text() or "").replace("\x00", "").strip()
            if content:
                pages.append((index, content))
        if not pages:
            raise ValueError(
                "Não foi possível extrair texto deste PDF. Ele pode ser digitalizado como imagem; OCR ainda não está habilitado."
            )
        return pages
    return [(1, extract_text(file_bytes, extension))]
