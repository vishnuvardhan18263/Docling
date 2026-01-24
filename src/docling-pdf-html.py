from docling.document_converter import DocumentConverter
import fitz  # PyMuPDF
import tempfile
import shutil
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")


def ExtractInvData(source: str):
    """
    Pipeline:
    PDF -> High-res images -> Docling OCR
    Output:
    - Markdown (best for LLM / RAG)
    - HTML (keeps images/icons visually)
    """

    converter = DocumentConverter()

    temp_dir = tempfile.mkdtemp(prefix="docling_imgs_")
    md_pages = []
    html_pages = []

    try:
        # --- PDF → Images (300 DPI) ---
        dpi = 300
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)

        pdf = fitz.open(source)

        for idx, page in enumerate(pdf, start=1):
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_path = os.path.join(temp_dir, f"page_{idx}.png")
            pix.save(img_path)

            # --- Docling OCR ---
            result = converter.convert(img_path)

            # Markdown → text, tables, structure
            md_pages.append(result.document.export_to_markdown())

            # HTML → keeps images/icons/layout visually
            html_pages.append(result.document.export_to_html())

        pdf.close()

        final_md = "\n\n".join(md_pages)
        final_html = "\n<hr/>\n".join(html_pages)

        # --- Outputs ---
        base_out = r"C:\Users\GollapalliVishnuVard\OneDrive - vishnuvardhang\docling\Docling-main\output"

        md_file = os.path.join(base_out, "invoice_extracted.md")
        html_file = os.path.join(base_out, "invoice_extracted.html")

        with open(md_file, "w", encoding="utf-8") as f:
            f.write(final_md)

        with open(html_file, "w", encoding="utf-8") as f:
            f.write(final_html)

        return {
            "markdown": md_file,
            "html": html_file
        }

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# -------------------------
# USAGE
# -------------------------
ExtractInvData(
    r"C:\Users\GollapalliVishnuVard\OneDrive - vishnuvardhang\docling\Docling-main\sourcefiles\langflow\bk-1\04168962.pdf"
)
