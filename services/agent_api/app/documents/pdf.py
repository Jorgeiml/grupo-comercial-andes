"""Purchase-order PDFs: text extraction only. The text is data and goes through the same flow as a chat message."""

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_BYTES = 2 * 1024 * 1024
MAX_PAGES = 5
MAX_CHARS = 6000


class PdfError(Exception):
    """Shown to the user as is (Spanish)."""


def extract_text(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise PdfError("El PDF supera 2 MB")
    if not data.startswith(b"%PDF"):
        raise PdfError("El archivo no es un PDF")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PdfError("El PDF está protegido con contraseña")
        if len(reader.pages) > MAX_PAGES:
            raise PdfError(f"El PDF tiene más de {MAX_PAGES} páginas")
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except PdfReadError as e:
        raise PdfError("No se pudo leer el PDF") from e
    if not text:
        # Scanned images carry no text; OCR is out of scope
        raise PdfError("El PDF no contiene texto (¿es un escaneo?). Envía la orden en PDF digital o escríbela en el chat")
    return text[:MAX_CHARS]


def as_message(filename: str, text: str) -> str:
    # The document is wrapped as data: any instruction inside it is content to evaluate, never to obey
    return f"Orden de compra adjunta (texto extraído del PDF «{filename}»):\n<documento>\n{text}\n</documento>"
