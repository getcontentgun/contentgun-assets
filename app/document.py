from io import BytesIO

from pypdf import PdfWriter


def merge_pdfs(pages: list[bytes]) -> bytes:
    writer = PdfWriter()
    for page in pages:
        writer.append(BytesIO(page))
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
