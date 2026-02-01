import os
import sys
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
import threading
import traceback
import logging

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz  # PyMuPDF
from PIL import Image, ImageTk
import pandas as pd

from openpyxl.utils import get_column_letter  # for autosizing
from docling.document_converter import DocumentConverter

# Ensure UTF-8 console on Windows (best effort)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}

OUTPUT_FORMATS = [
    "Excel (.xlsx)",
    "Markdown (.md)",
    "Plain Text (.txt)",
    "CSV (.csv)",
]


class CombinedResult:
    """Holds combined multi-page OCR results (texts + tables)."""
    def __init__(self, texts=None, tables=None):
        self.texts = texts or []
        self.tables = tables or []


def setup_logger_to_temp():
    """
    Create a logger that writes to a temp file first.
    We will retarget it to the chosen output folder once available.
    """
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_log = Path(tempfile.gettempdir()) / f"docling_session_{session_id}.log"

    logger = logging.getLogger("docling_gui")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # File handler to temp (will be replaced later)
    fh = logging.FileHandler(temp_log, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Optional console (ignored in --windowed builds)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)
    logger.addHandler(ch)

    logger.info("Logger initialized (temp). Log file: %s", temp_log)
    return logger, temp_log, session_id


class DoclingOCRTool:
    def __init__(self, root):
        self.root = root
        self.root.title("Docling OCR Utility")
        self.root.geometry("1200x840")

        # logger (starts in temp; retarget to output dir when known)
        self.logger, self.log_file, self.session_id = setup_logger_to_temp()

        # State
        self.input_file: Path | None = None
        self.output_folder: Path | None = None

        # UI variables
        self.rotation = tk.StringVar(value="0")        # rotation for preview, images, and (PDF→image) path
        self.ocr_dpi = tk.StringVar(value="300")       # target DPI for images & scanned PDFs
        self.is_scanned = tk.BooleanVar(value=False)   # scanned PDF indicator
        self.use_grayscale = tk.BooleanVar(value=False)  # grayscale during PDF rendering only
        self.output_format = tk.StringVar(value=OUTPUT_FORMATS[0])  # output format

        # Excel layout controls
        self.tables_single_sheet = tk.BooleanVar(value=True)  # toggle stacked layout
        self.blank_lines = tk.IntVar(value=1)                 # blank spacer rows between tables (stacked)

        # Preview
        self.original_image = None
        self.preview_image = None

        # Progress helpers
        self.total_steps = 0
        self.current_step = 0

        # Temp dirs to cleanup (always deleted)
        self._temp_dirs: list[Path] = []

        self.build_ui()
        self._info_banner(f"Logs (temp until output chosen): {self.log_file}")

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
        self.canvas.bind("<Configure>", lambda e: self.update_preview())

        side = tk.Frame(body, width=360)
        side.pack(side="right", fill="y", padx=10)

        # Rotation
        tk.Label(side, text="Rotation (Preview & OCR)").pack(anchor="w")
        rot = ttk.Combobox(side, textvariable=self.rotation, values=["0", "90", "180", "270"], state="readonly")
        rot.pack(fill="x")
        rot.bind("<<ComboboxSelected>>", lambda e: self.update_preview())

        # DPI
        tk.Label(side, text="Target DPI (Images & Scanned PDFs)").pack(anchor="w", pady=(10, 0))
        dpi_box = ttk.Combobox(side, textvariable=self.ocr_dpi, values=["200", "300", "400", "600"], state="readonly")
        dpi_box.pack(fill="x")

        # Scanned PDF toggle
        tk.Checkbutton(side, text="Scanned PDF (Image-based OCR)", variable=self.is_scanned).pack(anchor="w", pady=(15, 0))

        # Grayscale (PDF rendering only)
        tk.Checkbutton(side, text="Save Grayscale (PDF rendering only)", variable=self.use_grayscale)\
            .pack(anchor="w", pady=(4, 10))

        # Output format dropdown
        tk.Label(side, text="Output Format").pack(anchor="w", pady=(8, 0))
        fmt_box = ttk.Combobox(side, textvariable=self.output_format, values=OUTPUT_FORMATS, state="readonly")
        fmt_box.pack(fill="x")

        # Excel layout controls
        tk.Checkbutton(side, text="Stack all tables into one sheet (Excel)", variable=self.tables_single_sheet)\
            .pack(anchor="w", pady=(10, 4))

        spacer = tk.Frame(side)
        spacer.pack(anchor="w", pady=(0, 8), fill="x")
        tk.Label(spacer, text="Spacer blank rows between tables (Excel):").pack(anchor="w")
        tk.Spinbox(spacer, from_=0, to=10, textvariable=self.blank_lines, width=6).pack(anchor="w")

        # Progress area (determinate)
        prog_frame = tk.LabelFrame(side, text="Progress")
        prog_frame.pack(fill="x", pady=(10, 6))
        self.progress = ttk.Progressbar(prog_frame, mode="determinate")
        self.progress.pack(fill="x", padx=8, pady=(6, 2))
        self.progress_label = tk.Label(prog_frame, text="Idle", anchor="w")
        self.progress_label.pack(fill="x", padx=8, pady=(0, 8))

        self.start_btn = tk.Button(side, text="Start OCR", height=2, command=self.start_ocr)
        self.start_btn.pack(fill="x", pady=12)

        # Hint
        tk.Label(
            side,
            text=(
                "Images: apply Rotation + DPI (with upscaling if needed), then OCR.\n"
                "Scanned PDFs: render pages at target DPI.\n"
                "Digital PDFs: processed directly.\n"
                "Excel: choose stacked vs multi-sheet; autosizing applied."
            ),
            foreground="#444",
            justify="left",
            wraplength=330
        ).pack(anchor="w", pady=8)

    # ---------------- UX helpers ---------------- #
    def _info_banner(self, text: str):
        self.logger.info(text)
        if hasattr(self, "_info_label"):
            self._info_label.config(text=text)
        else:
            self._info_label = tk.Label(self.root, text=text, fg="#555", anchor="w")
            self._info_label.pack(fill="x", padx=10)

    def show_error_dialog(self, title: str, message: str, details: str | None = None):
        self.logger.error("%s: %s", title, message)
        if details:
            self.logger.error("Details:\n%s", details)

        win = tk.Toplevel(self.root)
        win.title(title)
        win.grab_set()
        win.transient(self.root)
        win.geometry("720x420")

        frm = tk.Frame(win)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        tk.Label(frm, text=message, fg="red", anchor="w", justify="left", wraplength=680).pack(fill="x")

        if details:
            details_frame = tk.LabelFrame(frm, text="Details")
            details_frame.pack(fill="both", expand=True, pady=(10, 0))
            txt = tk.Text(details_frame, height=14, wrap="word")
            txt.pack(fill="both", expand=True)
            txt.insert("1.0", details)
            txt.config(state="disabled")

        tk.Button(frm, text="OK", width=10, command=win.destroy).pack(pady=10)
        tk.Label(frm, text=f"Log file: {self.log_file}", fg="#555", anchor="w").pack(fill="x", pady=(4, 0))

    # ---------------- Progress (thread-safe) ---------------- #
    def _set_progress_total(self, total: int):
        self.root.after(0, lambda: self._apply_progress_total(total))

    def _apply_progress_total(self, total: int):
        self.total_steps = max(1, total)
        self.current_step = 0
        self.progress["maximum"] = self.total_steps
        self.progress["value"] = 0
        self.progress_label.config(text=f"Starting (0/{self.total_steps})")

    def _advance_progress(self, step: int = 1, status: str | None = None):
        self.root.after(0, lambda: self._apply_advance(step, status))

    def _apply_advance(self, step: int, status: str | None):
        self.current_step = min(self.total_steps, self.current_step + step)
        self.progress["value"] = self.current_step
        self.progress_label.config(text=f"{status or 'Working...'} ({self.current_step}/{self.total_steps})")

    def _set_status(self, status: str):
        self.logger.info("STATUS: %s", status)
        self.root.after(0, lambda: self.progress_label.config(text=status))

    # ---------------- File Selection ---------------- #
    def pick_input(self):
        path = filedialog.askopenfilename(
            filetypes=[("PDF / Images", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff")]
        )
        if path:
            self.input_file = Path(path)
            self.lbl_input.config(text=f"Input File: {path}")
            self.logger.info("Selected input: %s", path)
            try:
                self.load_preview()
            except Exception:
                self.logger.exception("Preview loading failed")

            # If output folder is already chosen, retarget log now (so log sits next to outputs)
            self._maybe_retarget_log_to_output()

    def pick_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_folder = Path(path)
            self.lbl_output.config(text=f"Output Folder: {path}")
            self.logger.info("Selected output folder: %s", path)
            # If input is already chosen, retarget log now
            self._maybe_retarget_log_to_output()

    # ---------------- Preview ---------------- #
    def load_preview(self):
        if not self.input_file:
            return
        try:
            if self.input_file.suffix.lower() in PDF_EXTS:
                doc = fitz.open(self.input_file)
                try:
                    page = doc[0]
                    pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                finally:
                    doc.close()
            else:
                img = Image.open(self.input_file)

            self.original_image = img
            self.update_preview()
        except Exception as e:
            self.logger.exception("Preview error")
            self.show_error_dialog("Preview Error", str(e), traceback.format_exc())

    def update_preview(self):
        if not self.original_image:
            return
        try:
            img = self.original_image.copy()
            angle = self._safe_int(self.rotation.get(), default=0, clamp=(0, 270))
            if angle:
                img = img.rotate(-angle, expand=True)

            w = self.canvas.winfo_width() or 800
            h = self.canvas.winfo_height() or 600
            img.thumbnail((w, h))
            self.preview_image = ImageTk.PhotoImage(img)

            self.canvas.delete("all")
            self.canvas.create_image(w // 2, h // 2, image=self.preview_image, anchor="center")
        except Exception:
            self.logger.exception("Update preview failed")

    # ---------------- OCR ---------------- #
    def start_ocr(self):
        if not self.input_file or not self.output_folder:
            messagebox.showerror("Missing Input", "Select input file and output folder")
            return

        if not self.output_folder.exists():
            try:
                self.output_folder.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.logger.exception("Cannot create output folder")
                self.show_error_dialog("Folder Error", f"Cannot create output folder:\n{e}", traceback.format_exc())
                return

        # Retarget logger to output dir (final location)
        self._retarget_log_to_output()

        # Validate numeric inputs (won't raise)
        self._safe_int(self.ocr_dpi.get(), default=300, clamp=(72, 1200))

        self.start_btn.config(state="disabled")
        self._set_status("Preparing…")
        self.logger.info("OCR job started. Input: %s | Output dir: %s | Format: %s",
                         self.input_file, self.output_folder, self.output_format.get())

        worker = threading.Thread(target=self.run_ocr_worker, daemon=True)
        worker.start()

    def run_ocr_worker(self):
        created_files = []
        try:
            converter = DocumentConverter()
            suffix = self.input_file.suffix.lower()

            total_steps = self._estimate_steps(suffix)
            self._set_progress_total(total_steps)

            if suffix in IMAGE_EXTS:
                self._set_status("Preparing image (rotation + DPI)…")
                temp_dir, prepped_image = self.prepare_image_temp(self.input_file)
                self._register_temp_dir(temp_dir)
                self._advance_progress(1, "Prepared image")

                self._set_status("Running OCR on image…")
                result = converter.convert(str(prepped_image))
                self._advance_progress(3, "OCR complete")

                self._set_status("Exporting output…")
                created_files = self.export_by_selected_format(result)
                self._advance_progress(2, "Export complete")

            elif suffix in PDF_EXTS and self.is_scanned.get():
                self._set_status("Rendering scanned PDF pages…")
                created_files = self._process_scanned_pdf(converter)

            else:
                self._set_status("Running OCR on PDF…")
                result = converter.convert(str(self.input_file))
                self._advance_progress(3, "OCR complete")

                self._set_status("Exporting output…")
                created_files = self.export_by_selected_format(result)
                self._advance_progress(2, "Export complete")

            file_list = "\n".join(str(p) for p in created_files)
            self.logger.info("Job completed. Generated files:\n%s", file_list)

            self.root.after(0, lambda: messagebox.showinfo(
                "Completed",
                f"OCR Completed Successfully!\n\n"
                f"Input File:\n{self.input_file}\n\n"
                f"Generated File(s):\n{file_list}\n\n"
                f"Log file:\n{self.log_file}"
            ))

        except Exception as err:
            self.logger.exception("OCR error")
            self.root.after(0, lambda: self.show_error_dialog("OCR Error", str(err), traceback.format_exc()))

        finally:
            # Always delete temps to prevent exposure
            try:
                self._cleanup_temp_dirs()
            except Exception:
                pass

            def _restore():
                self.start_btn.config(state="normal")
                if self.current_step < self.total_steps:
                    self.progress["value"] = self.total_steps
                    self.progress_label.config(text=f"Done ({self.total_steps}/{self.total_steps})")
            self.root.after(0, _restore)

    def _process_scanned_pdf(self, converter: DocumentConverter):
        created_files = []
        dpi = self._safe_int(self.ocr_dpi.get(), default=300, clamp=(72, 1200))
        angle = self._safe_int(self.rotation.get(), default=0, clamp=(0, 270))
        to_gray = self.use_grayscale.get()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir = Path(tempfile.gettempdir()) / f"OCR_{timestamp}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._register_temp_dir(temp_dir)

        doc = fitz.open(self.input_file)
        try:
            n_pages = len(doc)
            zoom = dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)

            for i, page in enumerate(doc, start=1):
                try:
                    try:
                        pix = page.get_pixmap(dpi=dpi, alpha=False)  # PyMuPDF >= 1.23
                    except TypeError:
                        pix = page.get_pixmap(matrix=matrix, alpha=False)

                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    if angle:
                        img = img.rotate(-angle, expand=True)
                    if to_gray:
                        img = img.convert("L")

                    img.save(
                        temp_dir / f"page_{i:03}.tiff",
                        format="TIIF" if False else "TIFF",  # harmless placeholder to show structure
                        compression="tiff_lzw",
                        dpi=(dpi, dpi),
                    )
                    try:
                        img.close()
                    except Exception:
                        pass
                finally:
                    self._advance_progress(1, f"Rendered page {i}/{n_pages}")

        finally:
            doc.close()

        texts, tables = [], []
        image_files = sorted(temp_dir.glob("*.tiff"))
        if not image_files:
            raise RuntimeError("No TIFF images generated for OCR")

        for idx, img_path in enumerate(image_files, start=1):
            self._set_status(f"OCR on page {idx}/{len(image_files)}…")
            r = converter.convert(str(img_path))
            texts.append(r.document.export_to_markdown())
            if r.document.tables:
                tables.extend(r.document.tables)
            self._advance_progress(1, f"OCR page {idx}/{len(image_files)}")

        combined_result = CombinedResult(texts=texts, tables=tables)

        self._set_status("Exporting output…")
        created_files = self.export_by_selected_format(combined_result)
        self._advance_progress(2, "Export complete")
        return created_files

    # ---------------- Image prep: Rotation + DPI ---------------- #
    def prepare_image_temp(self, image_path: Path):
        """
        Apply ONLY rotation and target DPI to an image.
        - If image lacks DPI, assume 72 and upscale pixels to target DPI.
        - If DPI < target, upscale (LANCZOS) so OCR gets more pixels.
        - Save as TIFF (LZW) with embedded DPI.
        Returns (temp_dir, prepared_image_path).
        """
        img = Image.open(image_path)
        try:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            angle = self._safe_int(self.rotation.get(), default=0, clamp=(0, 270))
            if angle:
                img = img.rotate(-angle, expand=True)

            target_dpi = self._safe_int(self.ocr_dpi.get(), default=300, clamp=(72, 1200))

            cur_dpi = img.info.get("dpi", (72, 72))
            if isinstance(cur_dpi, (list, tuple)) and len(cur_dpi) == 2:
                xdpi, ydpi = cur_dpi
            else:
                xdpi = ydpi = 72
            try:
                xdpi = float(xdpi) if xdpi else 72.0
                ydpi = float(ydpi) if ydpi else 72.0
            except Exception:
                xdpi = ydpi = 72.0

            base_dpi = min(xdpi, ydpi) if min(xdpi, ydpi) > 0 else 72.0
            scale = target_dpi / base_dpi
            if scale > 1.01:
                new_w = int(round(img.width * scale))
                new_h = int(round(img.height * scale))
                img = img.resize((new_w, new_h), Image.LANCZOS)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            temp_dir = Path(tempfile.gettempdir()) / f"OCR_IMG_{timestamp}"
            temp_dir.mkdir(parents=True, exist_ok=True)
            out_path = temp_dir / f"{image_path.stem}_prepped.tiff"

            img.save(out_path, format="TIFF", compression="tiff_lzw", dpi=(target_dpi, target_dpi))
        finally:
            try:
                img.close()
            except Exception:
                pass

        return temp_dir, out_path

    # ---------------- Temp registry & cleanup (always delete) ---------------- #
    def _register_temp_dir(self, p: Path):
        self._temp_dirs.append(p)
        self.logger.info("Registered temp dir: %s", p)

    def _cleanup_temp_dirs(self):
        for p in self._temp_dirs:
            if p and p.exists():
                try:
                    shutil.rmtree(p, ignore_errors=False)
                    self.logger.info("Temp dir removed: %s", p)
                except Exception as e:
                    self.logger.exception("Failed to remove temp dir %s: %s", p, e)
        self._temp_dirs.clear()

    # ---------------- Export Routing (by dropdown) ---------------- #
    def export_by_selected_format(self, result):
        fmt = self.output_format.get()
        if fmt.startswith("Excel"):
            if self.tables_single_sheet.get():
                path = self.export_to_excel_stacked(result)
            else:
                path = self.export_to_excel_multi(result)
            return [path]
        elif fmt.startswith("Markdown"):
            return [self.export_to_markdown(result)]
        elif fmt.startswith("Plain Text"):
            return [self.export_to_text(result)]
        elif fmt.startswith("CSV"):
            return self.export_to_csv(result)
        else:
            return [self.export_to_excel_stacked(result)]

    # ---------------- Helpers: Formatting ---------------- #
    @staticmethod
    def _df_to_markdown(df: pd.DataFrame) -> str:
        def esc(x: str) -> str:
            return ("" if x is None else str(x)).replace("|", "\\|")
        cols = [esc(c) for c in df.columns]
        header = "| " + " | ".join(cols) + " |\n"
        align = "| " + " | ".join(["---"] * len(cols)) + " |\n"
        rows = []
        for _, row in df.iterrows():
            cells = [esc(v) for v in row.tolist()]
            rows.append("| " + " | ".join(cells) + " |")
        return header + align + "\n".join(rows)

    @staticmethod
    def _autosize_columns(ws, max_width=60):
        try:
            for col_idx in range(1, ws.max_column + 1):
                max_len = 0
                for row in range(1, ws.max_row + 1):
                    cell = ws.cell(row=row, column=col_idx)
                    val = "" if cell.value is None else str(cell.value)
                    if len(val) > max_len:
                        max_len = len(val)
                ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, max_width)
        except Exception:
            logging.getLogger("docling_gui").exception("Autosize columns failed")

    # ---------------- Exporters: Excel ---------------- #
    def _default_output_dir(self) -> Path:
        return self.output_folder if (self.output_folder and self.output_folder.exists()) else (Path.home() / "Documents")

    def export_to_excel_multi(self, result: CombinedResult | object):
        output_dir = self._default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{self.input_file.stem}.xlsx"
        self.logger.info("Exporting Excel (multi-sheet): %s", out_file)

        with pd.ExcelWriter(out_file, engine="openpyxl") as writer:
            if isinstance(result, CombinedResult):
                if result.tables:
                    for i, table in enumerate(result.tables, start=1):
                        df = table.export_to_dataframe()
                        sheet = f"Table_{i}"
                        df.to_excel(writer, sheet_name=sheet, index=False)
                        self._autosize_columns(writer.sheets[sheet])
                all_text = "\n\n".join(result.texts) if result.texts else ""
                pd.DataFrame({"Document Text": [all_text]}).to_excel(writer, sheet_name="Text", index=False)
                self._autosize_columns(writer.sheets["Text"])
            else:
                if result.document.tables:
                    for i, table in enumerate(result.document.tables, start=1):
                        df = table.export_to_dataframe()
                        sheet = f"Table_{i}"
                        df.to_excel(writer, sheet_name=sheet, index=False)
                        self._autosize_columns(writer.sheets[sheet])
                    text = result.document.export_to_markdown()
                    pd.DataFrame({"Document Text": [text]}).to_excel(writer, sheet_name="Text", index=False)
                    self._autosize_columns(writer.sheets["Text"])
                else:
                    text = result.document.export_to_markdown()
                    pd.DataFrame({"Document Text": [text]}).to_excel(writer, sheet_name="Text", index=False)
                    self._autosize_columns(writer.sheets["Text"])

        return out_file

    def export_to_excel_stacked(self, result: CombinedResult | object):
        output_dir = self._default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{self.input_file.stem}.xlsx"
        self.logger.info("Exporting Excel (stacked): %s", out_file)

        def write_table_block(writer, df: pd.DataFrame, sheet_name: str, startrow: int, table_index: int, spacer_rows: int):
            col_name = "TableId"
            if col_name in df.columns:
                suffix = 1
                while f"{col_name}_{suffix}" in df.columns:
                    suffix += 1
                col_name = f"{col_name}_{suffix}"
            df_block = df.copy()
            df_block.insert(0, col_name, table_index)

            title_text = f"Table {table_index}"
            pd.DataFrame({title_text: [""]}).to_excel(writer, sheet_name=sheet_name, startrow=startrow, index=False, header=False)
            df_block.to_excel(writer, sheet_name=sheet_name, startrow=startrow + 1, index=False)
            return startrow + 1 + 1 + len(df_block.index) + spacer_rows

        blank = max(0, int(self.blank_lines.get()))

        with pd.ExcelWriter(out_file, engine="openpyxl") as writer:
            if isinstance(result, CombinedResult):
                all_text = "\n\n".join(result.texts) if result.texts else ""
            else:
                all_text = result.document.export_to_markdown()
            pd.DataFrame({"Document Text": [all_text]}).to_excel(writer, sheet_name="Text", index=False)

            any_tables = False
            cur_row = 0
            sheet_name = "Tables"

            if isinstance(result, CombinedResult):
                if result.tables:
                    any_tables = True
                    for i, table in enumerate(result.tables, start=1):
                        df = table.export_to_dataframe()
                        cur_row = write_table_block(writer, df, sheet_name, cur_row, i, blank)
            else:
                if result.document.tables:
                    any_tables = True
                    for i, table in enumerate(result.document.tables, start=1):
                        df = table.export_to_dataframe()
                        cur_row = write_table_block(writer, df, sheet_name, cur_row, i, blank)

            if not any_tables:
                pd.DataFrame({"Info": ["No tables detected"]}).to_excel(writer, sheet_name=sheet_name, index=False)

            self._autosize_columns(writer.sheets["Text"])
            self._autosize_columns(writer.sheets[sheet_name])

        return out_file

    # ---------------- Exporters: Other Formats ---------------- #
    def export_to_markdown(self, result):
        output_dir = self._default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{self.input_file.stem}.md"
        self.logger.info("Exporting Markdown: %s", out_file)

        parts = [f"# {self.input_file.stem}\n"]
        if isinstance(result, CombinedResult):
            if result.texts:
                parts.append("\n".join(result.texts))
            if result.tables:
                for i, table in enumerate(result.tables, start=1):
                    df = table.export_to_dataframe()
                    parts.append(f"\n\n## Table {i}\n")
                    parts.append(self._df_to_markdown(df))
        else:
            parts.append(result.document.export_to_markdown())
            if result.document.tables:
                for i, table in enumerate(result.document.tables, start=1):
                    df = table.export_to_dataframe()
                    parts.append(f"\n\n## Table {i}\n")
                    parts.append(self._df_to_markdown(df))

        out_file.write_text("\n".join(parts), encoding="utf-8")
        return out_file

    def export_to_text(self, result):
        output_dir = self._default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{self.input_file.stem}.txt"
        self.logger.info("Exporting Text: %s", out_file)

        parts = [f"{self.input_file.stem}\n", "=" * len(self.input_file.stem), "\n"]
        if isinstance(result, CombinedResult):
            if result.texts:
                parts.append("\n".join(result.texts))
            if result.tables:
                for i, table in enumerate(result.tables, start=1):
                    df = table.export_to_dataframe()
                    parts.append(f"\n\nTable {i}\n" + "-" * (6 + len(str(i))) + "\n")
                    parts.append(self._df_to_markdown(df))
        else:
            parts.append(result.document.export_to_markdown())
            if result.document.tables:
                for i, table in enumerate(result.document.tables, start=1):
                    df = table.export_to_dataframe()
                    parts.append(f"\n\nTable {i}\n" + "-" * (6 + len(str(i))) + "\n")
                    parts.append(self._df_to_markdown(df))

        out_file.write_text("\n".join(parts), encoding="utf-8")
        return out_file

    def export_to_csv(self, result):
        output_dir = self._default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)

        created = []
        self.logger.info("Exporting CSV set in %s", output_dir)

        if isinstance(result, CombinedResult):
            all_text = "\n\n".join(result.texts) if result.texts else ""
            text_csv = output_dir / f"{self.input_file.stem}_Text.csv"
            pd.DataFrame({"Document Text": [all_text]}).to_csv(text_csv, index=False, encoding="utf-8")
            created.append(text_csv)

            for i, table in enumerate(result.tables, start=1):
                df = table.export_to_dataframe()
                table_csv = output_dir / f"{self.input_file.stem}_Table_{i}.csv"
                df.to_csv(table_csv, index=False, encoding="utf-8")
                created.append(table_csv)
            return created

        if result.document.tables:
            for i, table in enumerate(result.document.tables, start=1):
                df = table.export_to_dataframe()
                table_csv = output_dir / f"{self.input_file.stem}_Table_{i}.csv"
                df.to_csv(table_csv, index=False, encoding="utf-8")
                created.append(table_csv)
            text = result.document.export_to_markdown()
            text_csv = output_dir / f"{self.input_file.stem}_Text.csv"
            pd.DataFrame({"Document Text": [text]}).to_csv(text_csv, index=False, encoding="utf-8")
            created.append(text_csv)
        else:
            text = result.document.export_to_markdown()
            text_csv = output_dir / f"{self.input_file.stem}_Text.csv"
            pd.DataFrame({"Document Text": [text]}).to_csv(text_csv, index=False, encoding="utf-8")
            created.append(text_csv)

        return created

    # ---------------- Utils ---------------- #
    @staticmethod
    def _safe_int(v: str, default: int, clamp: tuple[int, int] | None = None) -> int:
        try:
            x = int(v)
        except Exception:
            x = default
        if clamp:
            lo, hi = clamp
            x = max(lo, min(hi, x))
        return x

    def _estimate_steps(self, suffix: str) -> int:
        if suffix in IMAGE_EXTS:
            return 6  # prep(1) + ocr(3) + export(2)
        if suffix in PDF_EXTS and self.is_scanned.get():
            try:
                doc = fitz.open(self.input_file)
                n_pages = len(doc)
            except Exception:
                n_pages = 1
            finally:
                try:
                    doc.close()
                except Exception:
                    pass
            return n_pages + n_pages + 2
        return 5  # digital PDFs: ocr(3) + export(2)

    # ------------- Logger retargeting ------------- #
    def _maybe_retarget_log_to_output(self):
        """Retarget only if both input & output are known (so we can name the log)."""
        if self.input_file and self.output_folder:
            self._retarget_log_to_output()

    def _retarget_log_to_output(self):
        """Move/replace the file handler so logging writes into output folder."""
        if not (self.input_file and self.output_folder):
            return
        new_path = self.output_folder / f"{self.input_file.stem}_{self.session_id}.log"

        logger = self.logger
        # Close existing file handlers
        for h in list(logger.handlers):
            if isinstance(h, logging.FileHandler):
                try:
                    h.flush()
                    h.close()
                except Exception:
                    pass
                logger.removeHandler(h)

        # Append the existing temp log content, if any
        try:
            if Path(self.log_file).exists():
                # If new_path already exists, append; else copy
                with open(self.log_file, "r", encoding="utf-8", errors="replace") as src, \
                     open(new_path, "a", encoding="utf-8", errors="replace") as dst:
                    dst.write(src.read())
        except Exception:
            # Non-fatal
            pass

        # New file handler at destination
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh = logging.FileHandler(new_path, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        self.log_file = new_path
        self._info_banner(f"Logs: {self.log_file}")
        logger.info("Logger retargeted to: %s", new_path)


# ---------------- MAIN ---------------- #
if __name__ == "__main__":
    root = tk.Tk()
    app = DoclingOCRTool(root)
    root.mainloop()