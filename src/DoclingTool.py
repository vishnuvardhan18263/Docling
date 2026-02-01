import os
import sys
import shutil
import tempfile
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz  # PyMuPDF
from PIL import Image, ImageTk
import pandas as pd

from docling.document_converter import DocumentConverter

# Ensure UTF-8 console on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class CombinedResult:
    """Holds combined multi-page OCR results (texts + tables)."""
    def __init__(self, texts=None, tables=None):
        self.texts = texts or []
        self.tables = tables or []


class DoclingOCRTool:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Docling OCR Utility")
        self.root.geometry("1200x720")

        # State
        self.input_file: Path | None = None
        self.output_folder: Path | None = None

        self.rotation = tk.StringVar(value="0")
        self.ocr_dpi = tk.StringVar(value="300")  # NEW: DPI selector
        self.is_scanned = tk.BooleanVar(value=False)
        self.use_grayscale = tk.BooleanVar(value=False)  # NEW: grayscale toggle

        # Images for preview
        self.original_image: Image.Image | None = None
        self.preview_image: ImageTk.PhotoImage | None = None

        self.build_ui()

    # ---------------- UI ---------------- #

    def build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=5)

        tk.Button(top, text="Select Input File", command=self.pick_input).pack(side="left")
        tk.Button(top, text="Select Output Folder", command=self.pick_output).pack(side="left", padx=5)

        self.lbl_input = tk.Label(self.root, fg="blue", anchor="w")
        self.lbl_input.pack(fill="x", padx=10)

        self.lbl_output = tk.Label(self.root, fg="green", anchor="w")
        self.lbl_output.pack(fill="x", padx=10)

        body = tk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=10, pady=10)

        preview_frame = tk.LabelFrame(body, text="Preview")
        preview_frame.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(preview_frame, bg="#eeeeee")
        self.canvas.pack(fill="both", expand=True)
        # Re-render preview when the canvas is resized
        self.canvas.bind("<Configure>", lambda e: self.update_preview())

        side = tk.Frame(body, width=280)
        side.pack(side="right", fill="y", padx=10)

        tk.Label(side, text="Rotation (Preview & OCR)").pack(anchor="w")
        rot = ttk.Combobox(
            side,
            textvariable=self.rotation,
            values=["0", "90", "180", "270"],
            state="readonly"
        )
        rot.pack(fill="x")
        rot.bind("<<ComboboxSelected>>", lambda e: self.update_preview())

        tk.Label(side, text="Render DPI (PDF → Image)").pack(anchor="w", pady=(10, 0))
        dpi_box = ttk.Combobox(
            side,
            textvariable=self.ocr_dpi,
            values=["200", "300", "400", "600"],
            state="readonly"
        )
        dpi_box.pack(fill="x")

        tk.Checkbutton(
            side,
            text="Scanned PDF (Image-based OCR)",
            variable=self.is_scanned
        ).pack(anchor="w", pady=(15, 0))

        tk.Checkbutton(
            side,
            text="Save Grayscale (smaller files)",
            variable=self.use_grayscale
        ).pack(anchor="w", pady=(4, 15))

        self.start_btn = tk.Button(
            side,
            text="Start OCR",
            height=2,
            command=self.start_ocr
        )
        self.start_btn.pack(fill="x", pady=10)

        self.progress = ttk.Progressbar(side, mode="indeterminate")
        self.progress.pack(fill="x")

        # Hint
        tk.Label(
            side,
            text="Tip: Higher DPI (400–600) = better OCR for small text,\n"
                 "but larger and slower.",
            foreground="#444"
        ).pack(anchor="w", pady=8)

    # ---------------- File Selection ---------------- #

    def pick_input(self):
        path = filedialog.askopenfilename(
            filetypes=[("PDF / Images", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff")]
        )
        if path:
            self.input_file = Path(path)
            self.lbl_input.config(text=f"Input File: {path}")
            self.load_preview()

    def pick_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_folder = Path(path)
            self.lbl_output.config(text=f"Output Folder: {path}")

    # ---------------- Preview ---------------- #

    def load_preview(self):
        """Load first page / image for UI preview (fast rendering)."""
        try:
            if self.input_file.suffix.lower() == ".pdf":
                doc = fitz.open(self.input_file)
                page = doc[0]
                # ~180 DPI is a good compromise for UI preview (NOT used for OCR)
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            else:
                img = Image.open(self.input_file)

            self.original_image = img
            self.update_preview()

        except Exception as e:
            messagebox.showerror("Preview Error", str(e))

    def update_preview(self):
        if not self.original_image:
            return

        img = self.original_image.copy()
        angle = int(self.rotation.get())

        if angle:
            img = img.rotate(-angle, expand=True)

        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 600
        img.thumbnail((w, h))
        self.preview_image = ImageTk.PhotoImage(img)

        self.canvas.delete("all")
        self.canvas.create_image(w // 2, h // 2, image=self.preview_image, anchor="center")

    # ---------------- OCR ---------------- #

    def start_ocr(self):
        if not self.input_file or not self.output_folder:
            messagebox.showerror("Missing Input", "Select input file and output folder")
            return

        self.progress.start()
        self.start_btn.config(state="disabled")
        # Run after small delay to keep UI responsive
        self.root.after(100, self.run_ocr)

    def run_ocr(self):
        temp_dir = None
        try:
            converter = DocumentConverter()

            # Branch: scanned PDFs → render to high‑DPI images first
            if self.input_file.suffix.lower() == ".pdf" and self.is_scanned.get():
                temp_dir = self.pdf_to_tiff_temp(self.input_file)

                texts: list[str] = []
                tables = []
                image_files = sorted(temp_dir.glob("*.tiff"))
                if not image_files:
                    raise RuntimeError("No TIFF images generated for OCR")

                for img_path in image_files:
                    result = converter.convert(str(img_path))
                    texts.append(result.document.export_to_markdown())
                    if result.document.tables:
                        tables.extend(result.document.tables)

                combined_result = CombinedResult(texts=texts, tables=tables)
                output_file = self.export_to_excel(combined_result)

            else:
                # Non-scanned PDFs or images → let Docling handle directly
                result = converter.convert(str(self.input_file))
                output_file = self.export_to_excel(result)

            messagebox.showinfo(
                "Completed",
                f"OCR Completed Successfully!\n\n"
                f"Input File:\n{self.input_file}\n\n"
                f"Output File:\n{output_file}"
            )

        except Exception as err:
            messagebox.showerror("OCR Error", str(err))

        finally:
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

            self.progress.stop()
            self.start_btn.config(state="normal")

    # ---------------- PDF → TIFF (High‑DPI) ---------------- #

    def pdf_to_tiff_temp(self, pdf_path: Path) -> Path:
        """
        Render each page of a PDF to a high‑DPI, lossless TIFF,
        embedding the DPI so OCR engines can interpret scale correctly.
        """
        doc = fitz.open(pdf_path)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Prefer OS temp dir; on Windows this maps to %LOCALAPPDATA%\Temp
        base_tmp = Path(tempfile.gettempdir())
        temp_dir = base_tmp / f"OCR_{timestamp}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # --- resolution controls ---
        try:
            dpi = int(self.ocr_dpi.get())
        except Exception:
            dpi = 300

        # Fallback for older PyMuPDF: scale by DPI/72
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        angle = int(self.rotation.get())
        to_gray = self.use_grayscale.get()

        for i, page in enumerate(doc):
            # Prefer dpi=... when supported (PyMuPDF >= 1.23); else use matrix
            try:
                pix = page.get_pixmap(dpi=dpi, alpha=False)  # type: ignore[arg-type]
            except TypeError:
                pix = page.get_pixmap(matrix=matrix, alpha=False)

            # Convert Pixmap → PIL
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            if angle:
                # negative angle = clockwise in PIL, matches preview
                img = img.rotate(-angle, expand=True)

            if to_gray:
                img = img.convert("L")  # 8-bit grayscale

            # Save as lossless TIFF with embedded DPI
            save_kwargs = {
                "format": "TIFF",
                "compression": "tiff_lzw",
                "dpi": (dpi, dpi),
            }
            img.save(temp_dir / f"page_{i+1:03}.tiff", **save_kwargs)

        return temp_dir

    # ---------------- Export ---------------- #

    def export_to_excel(self, result):
        """
        Export tables and text to Excel.
        - If result is CombinedResult: stitch multi-page text and tables.
        - Else: handle Docling single-document result.
        """
        # Default output directory if user deselected
        if self.output_folder is None or not self.output_folder.exists():
            # Cross-platform "Documents" fallback
            docs = Path.home() / "Documents"
            output_dir = docs
        else:
            output_dir = self.output_folder

        output_dir.mkdir(parents=True, exist_ok=True)

        output_name = self.input_file.stem + ".xlsx"
        out_file = output_dir / output_name

        if isinstance(result, CombinedResult):
            with pd.ExcelWriter(out_file, engine="openpyxl") as writer:
                if result.tables:
                    for i, table in enumerate(result.tables):
                        df = table.export_to_dataframe()
                        df.to_excel(writer, sheet_name=f"Table_{i+1}", index=False)

                # Put all pages' text in a single sheet
                all_text = "\n\n".join(result.texts) if result.texts else ""
                pd.DataFrame({"Document Text": [all_text]}).to_excel(
                    writer, sheet_name="Text", index=False
                )

        else:
            # Single document result from Docling
            if result.document.tables:
                with pd.ExcelWriter(out_file, engine="openpyxl") as writer:
                    for i, table in enumerate(result.document.tables):
                        df = table.export_to_dataframe()
                        df.to_excel(writer, sheet_name=f"Table_{i+1}", index=False)
                    # Also include the full text on a separate sheet
                    text = result.document.export_to_markdown()
                    pd.DataFrame({"Document Text": [text]}).to_excel(
                        writer, sheet_name="Text", index=False
                    )
            else:
                # Only text
                text = result.document.export_to_markdown()
                pd.DataFrame({"Document Text": [text]}).to_excel(out_file, index=False)

        return out_file


# ---------------- MAIN ---------------- #

if __name__ == "__main__":
    root = tk.Tk()
    app = DoclingOCRTool(root)
    root.mainloop()