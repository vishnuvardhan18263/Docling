# docling_headless.py
# Headless Docling OCR runner driven by Excel ".xlsm" / ".xlsx" config
# Reads "Exe config" sheet, processes many files, writes outputs and an optional RunLog sheet.
# Requires: docling, PyMuPDF (fitz), Pillow, pandas, openpyxl

import os
import sys
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
import traceback
import logging
import argparse
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import fitz  # PyMuPDF
from PIL import Image
import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

# from docling.document_converter import DocumentConverter


# ---------- Constants ----------
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}
ALL_EXTS = IMAGE_EXTS.union(PDF_EXTS)

VALID_OUTPUT_FORMATS = {"Excel", "Markdown", "Text", "CSV"}

import importlib.metadata

_real_version = importlib.metadata.version

def safe_version(pkg):
    try:
        return _real_version(pkg)
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"

importlib.metadata.version = safe_version

from docling.document_converter import DocumentConverter


# ---------- Data Model ----------
@dataclass
class Config:
    workbook_path: Path
    sheet_name: str = "Exe config"

    # Inputs
    input_mode: str = "file"  # file | folder | list
    input_files: List[Path] = field(default_factory=list)
    input_folder: Optional[Path] = None
    recurse_subfolders: bool = False

    # Output
    output_folder: Optional[Path] = None
    output_formats: List[str] = field(default_factory=lambda: ["Excel"])

    # OCR/Render
    dpi: int = 300
    orientation: int = 0  # 0/90/180/270
    is_scanned_pdf: bool = False
    grayscale: bool = False

    # Excel layout
    tables_single_sheet: bool = True
    blank_lines: int = 1

    # Post-run
    write_back_runlog: bool = True


@dataclass
class CombinedResult:
    texts: List[str] = field(default_factory=list)
    tables: List[object] = field(default_factory=list)  # docling table objects


# ---------- Logging ----------
def setup_logger(temp_prefix="docling_headless"):
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_log = Path(tempfile.gettempdir()) / f"{temp_prefix}_{session_id}.log"

    logger = logging.getLogger("docling_headless")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(temp_log, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)
    logger.addHandler(ch)

    logger.info("Logger initialized (temp): %s", temp_log)
    return logger, temp_log, session_id


def retarget_logger(logger: logging.Logger, old_temp_log: Path, new_log_path: Path):
    # Close existing file handlers
    for h in list(logger.handlers):
        if isinstance(h, logging.FileHandler):
            try:
                h.flush()
                h.close()
            except Exception:
                pass
            logger.removeHandler(h)

    # Append temp log into final log
    try:
        if old_temp_log.exists():
            with open(old_temp_log, "r", encoding="utf-8", errors="replace") as src, \
                 open(new_log_path, "a", encoding="utf-8", errors="replace") as dst:
                dst.write(src.read())
    except Exception:
        pass

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(new_log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.info("Logger retargeted to: %s", new_log_path)


# ---------- Sheet Utilities ----------
def _read_bool(s: Optional[str], default=False) -> bool:
    if s is None:
        return default
    s = str(s).strip().lower()
    return s in {"true", "yes", "1", "y"}


def _read_int(s: Optional[str], default=0, lo=None, hi=None) -> int:
    try:
        x = int(s)
    except Exception:
        x = default
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x


def load_config_from_excel(workbook_path: Path, sheet_name="Exe config") -> Config:
    # keep_vba=True preserves macros if we later save; data_only to read computed values
    wb = load_workbook(workbook_path, data_only=True, keep_vba=workbook_path.suffix.lower() == ".xlsm")
    if sheet_name not in wb.sheetnames:
        raise RuntimeError(f"Config sheet '{sheet_name}' not found in workbook")

    ws = wb[sheet_name]

    # Read Key-Value block (A=Key, B=Value) — stop on empty key
    kv: Dict[str, str] = {}
    for row in ws.iter_rows(min_row=1, max_col=2):
        key_cell, val_cell = row[0], row[1]
        key = (key_cell.value or "").strip() if key_cell.value else ""
        if not key:
            # stop when first empty key is encountered (allows space for other sections below)
            break
        kv[key] = "" if val_cell.value is None else str(val_cell.value).strip()

    cfg = Config(workbook_path=workbook_path, sheet_name=sheet_name)

    cfg.input_mode = kv.get("InputMode", cfg.input_mode).strip().lower()
    if cfg.input_mode not in {"file", "folder", "list"}:
        raise RuntimeError("InputMode must be one of: file, folder, list")

    # Input paths
    input_files_cell = kv.get("InputFiles", "")
    if input_files_cell:
        for part in input_files_cell.split(";"):
            p = part.strip().strip('"')
            if p:
                cfg.input_files.append(Path(p))

    folder_cell = kv.get("InputFolder", "")
    if folder_cell:
        cfg.input_folder = Path(folder_cell.strip().strip('"'))

    cfg.recurse_subfolders = _read_bool(kv.get("RecurseSubfolders", "FALSE"), default=False)

    # Output
    out_cell = kv.get("OutputFolder", "")
    cfg.output_folder = Path(out_cell.strip().strip('"')) if out_cell else None

    formats_cell = kv.get("OutputFormats", "")
    if formats_cell:
        fmts = [f.strip().capitalize() for f in formats_cell.split(",") if f.strip()]
        # Normalize to the expected labels
        norm = []
        for f in fmts:
            if f.lower().startswith("excel"):
                norm.append("Excel")
            elif f.lower() in {"md", "markdown"}:
                norm.append("Markdown")
            elif f.lower() in {"txt", "text"}:
                norm.append("Text")
            elif f.lower() == "csv":
                norm.append("CSV")
        cfg.output_formats = norm or cfg.output_formats

    # OCR/Render
    cfg.dpi = _read_int(kv.get("DPI"), default=300, lo=72, hi=1200)
    cfg.orientation = _read_int(kv.get("Orientation"), default=0)
    if cfg.orientation not in {0, 90, 180, 270}:
        cfg.orientation = 0

    cfg.is_scanned_pdf = _read_bool(kv.get("IsScannedPDF", "FALSE"), default=False)
    cfg.grayscale = _read_bool(kv.get("Grayscale", "FALSE"), default=False)

    # Excel layout
    cfg.tables_single_sheet = _read_bool(kv.get("ExcelTablesSingleSheet", "TRUE"), default=True)
    cfg.blank_lines = _read_int(kv.get("ExcelBlankLines", "1"), default=1, lo=0, hi=50)

    # Post-run
    cfg.write_back_runlog = _read_bool(kv.get("WriteBackRunLog", "TRUE"), default=True)

    # Additionally scan for an "Inputs" table with header "InputPath"
    header_row = None
    for row in ws.iter_rows(min_row=1, max_col=1):
        cell = row[0]
        if cell.value and str(cell.value).strip().lower() == "inputpath":
            header_row = cell.row
            break
    if header_row:
        r = header_row + 1
        while True:
            v = ws.cell(row=r, column=1).value
            if v is None or str(v).strip() == "":
                break
            cfg.input_files.append(Path(str(v).strip().strip('"')))
            r += 1

    # De-duplicate files if any duplicates from both sources
    if cfg.input_files:
        dedup = []
        seen = set()
        for p in cfg.input_files:
            s = str(p)
            if s not in seen:
                dedup.append(p)
                seen.add(s)
        cfg.input_files = dedup

    wb.close()
    return cfg


# ---------- Core Processor ----------
class DoclingHeadless:
    def __init__(self, cfg: Config, logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger
        self.converter = DocumentConverter()
        self._temp_dirs: List[Path] = []

    # ---- Temp management ----
    def _register_temp_dir(self, p: Path):
        self._temp_dirs.append(p)
        self.logger.info("Registered temp dir: %s", p)

    def _cleanup_temp_dirs(self):
        for p in self._temp_dirs:
            if p and p.exists():
                try:
                    shutil.rmtree(p, ignore_errors=False)
                    self.logger.info("Temp dir removed: %s", p)
                except Exception:
                    self.logger.exception("Failed to remove temp dir: %s", p)
        self._temp_dirs.clear()

    # ---- Helpers ----
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
            logging.getLogger("docling_headless").exception("Autosize columns failed")

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

    # ---- Image prep ----
    def prepare_image_temp(self, image_path: Path, rotation: int, dpi: int) -> Path:
        """
        Apply rotation + target DPI; if image DPI < target, upscale using LANCZOS.
        Save as TIFF (LZW) with embedded DPI. Returns path to prepared tiff.
        """
        img = Image.open(image_path)
        try:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            if rotation:
                img = img.rotate(-rotation, expand=True)

            cur_dpi = img.info.get("dpi", (72, 72))
            try:
                xdpi = float(cur_dpi[0]) if cur_dpi and cur_dpi[0] else 72.0
                ydpi = float(cur_dpi[1]) if cur_dpi and cur_dpi[1] else 72.0
            except Exception:
                xdpi = ydpi = 72.0
            base_dpi = min(xdpi, ydpi) if min(xdpi, ydpi) > 0 else 72.0
            scale = dpi / base_dpi
            if scale > 1.01:
                new_w = int(round(img.width * scale))
                new_h = int(round(img.height * scale))
                img = img.resize((new_w, new_h), Image.LANCZOS)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            temp_dir = Path(tempfile.gettempdir()) / f"OCR_IMG_{timestamp}"
            temp_dir.mkdir(parents=True, exist_ok=True)
            self._register_temp_dir(temp_dir)
            out_path = temp_dir / f"{image_path.stem}_prepped.tiff"
            img.save(out_path, format="TIFF", compression="tiff_lzw", dpi=(dpi, dpi))
            return out_path
        finally:
            try:
                img.close()
            except Exception:
                pass

    # ---- Exporters ----
    def export_to_excel_multi(self, result, input_file: Path, output_dir: Path) -> Path:
        out_file = output_dir / f"{input_file.stem}.xlsx"
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
        return out_file

    def export_to_excel_stacked(self, result, input_file: Path, output_dir: Path, blank_lines: int) -> Path:
        out_file = output_dir / f"{input_file.stem}.xlsx"
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
                        cur_row = write_table_block(writer, df, sheet_name, cur_row, i, blank_lines)
            else:
                if result.document.tables:
                    any_tables = True
                    for i, table in enumerate(result.document.tables, start=1):
                        df = table.export_to_dataframe()
                        cur_row = write_table_block(writer, df, sheet_name, cur_row, i, blank_lines)

            if not any_tables:
                pd.DataFrame({"Info": ["No tables detected"]}).to_excel(writer, sheet_name=sheet_name, index=False)

            self._autosize_columns(writer.sheets["Text"])
            self._autosize_columns(writer.sheets[sheet_name])

        return out_file

    def export_to_markdown(self, result, input_file: Path, output_dir: Path) -> Path:
        out_file = output_dir / f"{input_file.stem}.md"
        self.logger.info("Exporting Markdown: %s", out_file)
        parts = [f"# {input_file.stem}\n"]
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

    def export_to_text(self, result, input_file: Path, output_dir: Path) -> Path:
        out_file = output_dir / f"{input_file.stem}.txt"
        self.logger.info("Exporting Text: %s", out_file)
        parts = [f"{input_file.stem}\n", "=" * len(input_file.stem), "\n"]
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

    def export_to_csv(self, result, input_file: Path, output_dir: Path) -> List[Path]:
        created = []
        self.logger.info("Exporting CSV set in %s", output_dir)
        if isinstance(result, CombinedResult):
            all_text = "\n\n".join(result.texts) if result.texts else ""
            text_csv = output_dir / f"{input_file.stem}_Text.csv"
            pd.DataFrame({"Document Text": [all_text]}).to_csv(text_csv, index=False, encoding="utf-8")
            created.append(text_csv)
            for i, table in enumerate(result.tables, start=1):
                df = table.export_to_dataframe()
                table_csv = output_dir / f"{input_file.stem}_Table_{i}.csv"
                df.to_csv(table_csv, index=False, encoding="utf-8")
                created.append(table_csv)
            return created

        if result.document.tables:
            for i, table in enumerate(result.document.tables, start=1):
                df = table.export_to_dataframe()
                table_csv = output_dir / f"{input_file.stem}_Table_{i}.csv"
                df.to_csv(table_csv, index=False, encoding="utf-8")
                created.append(table_csv)
            text = result.document.export_to_markdown()
            text_csv = output_dir / f"{input_file.stem}_Text.csv"
            pd.DataFrame({"Document Text": [text]}).to_csv(text_csv, index=False, encoding="utf-8")
            created.append(text_csv)
        else:
            text = result.document.export_to_markdown()
            text_csv = output_dir / f"{input_file.stem}_Text.csv"
            pd.DataFrame({"Document Text": [text]}).to_csv(text_csv, index=False, encoding="utf-8")
            created.append(text_csv)
        return created

    # ---- Convert: PDF scanned pipeline ----
    def convert_scanned_pdf(self, pdf_path: Path, dpi: int, rotation: int, grayscale: bool):
        created_files: List[Path] = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir = Path(tempfile.gettempdir()) / f"OCR_{timestamp}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._register_temp_dir(temp_dir)

        doc = fitz.open(pdf_path)
        try:
            n_pages = len(doc)
            for i, page in enumerate(doc, start=1):
                try:
                    try:
                        pix = page.get_pixmap(dpi=dpi, alpha=False)  # modern PyMuPDF
                    except TypeError:
                        zoom = dpi / 72.0
                        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    if rotation:
                        img = img.rotate(-rotation, expand=True)
                    if grayscale:
                        img = img.convert("L")
                    img.save(
                        temp_dir / f"page_{i:03}.tiff",
                        format="TIFF",
                        compression="tiff_lzw",
                        dpi=(dpi, dpi),
                    )
                finally:
                    try:
                        img.close()
                    except Exception:
                        pass
        finally:
            doc.close()

        texts, tables = [], []
        image_files = sorted(temp_dir.glob("*.tiff"))
        if not image_files:
            raise RuntimeError("No TIFF images generated for OCR")

        for idx, img_path in enumerate(image_files, start=1):
            self.logger.info("OCR on page %d/%d: %s", idx, len(image_files), img_path.name)
            r = self.converter.convert(str(img_path))
            texts.append(r.document.export_to_markdown())
            if r.document.tables:
                tables.extend(r.document.tables)

        return CombinedResult(texts=texts, tables=tables)

    # ---- Routing & Run per file ----
    def run_for_file(self, input_file: Path, output_root: Path) -> List[Path]:
        input_file = input_file.resolve()
        suffix = input_file.suffix.lower()

        # Ensure output
        output_root.mkdir(parents=True, exist_ok=True)

        self.logger.info("Processing: %s", input_file)
        created: List[Path] = []

        if suffix in IMAGE_EXTS:
            # Prepare and OCR as image
            prepped = self.prepare_image_temp(input_file, rotation=self.cfg.orientation, dpi=self.cfg.dpi)
            res = self.converter.convert(str(prepped))
            created.extend(self._export_all_formats(res, input_file, output_root))

        elif suffix in PDF_EXTS and self.cfg.is_scanned_pdf:
            # Scanned PDF pipeline
            res = self.convert_scanned_pdf(input_file, dpi=self.cfg.dpi, rotation=self.cfg.orientation, grayscale=self.cfg.grayscale)
            created.extend(self._export_all_formats(res, input_file, output_root))

        else:
            # Digital PDF: convert directly
            res = self.converter.convert(str(input_file))
            created.extend(self._export_all_formats(res, input_file, output_root))

        return created

    def _export_all_formats(self, result, input_file: Path, output_dir: Path) -> List[Path]:
        out_paths: List[Path] = []
        for fmt in self.cfg.output_formats:
            if fmt == "Excel":
                if self.cfg.tables_single_sheet:
                    out_paths.append(self.export_to_excel_stacked(result, input_file, output_dir, self.cfg.blank_lines))
                else:
                    out_paths.append(self.export_to_excel_multi(result, input_file, output_dir))
            elif fmt == "Markdown":
                out_paths.append(self.export_to_markdown(result, input_file, output_dir))
            elif fmt == "Text":
                out_paths.append(self.export_to_text(result, input_file, output_dir))
            elif fmt == "CSV":
                out_paths.extend(self.export_to_csv(result, input_file, output_dir))
        return out_paths


# ---------- RunLog writer ----------
def append_runlog_to_workbook(cfg: Config, rows: List[Dict[str, str]]):
    # Preserve macros when saving back to .xlsm
    keep_vba = cfg.workbook_path.suffix.lower() == ".xlsm"
    wb = load_workbook(cfg.workbook_path, keep_vba=keep_vba)
    sheet_name = "RunLog"
    if sheet_name not in wb.sheetnames:
        ws = wb.create_sheet(sheet_name)
        headers = ["Timestamp", "Input", "Status", "Outputs (semicolon-separated)", "Error", "LogFile"]
        ws.append(headers)
    else:
        ws = wb[sheet_name]

    for row in rows:
        ws.append([
            row.get("Timestamp", ""),
            row.get("Input", ""),
            row.get("Status", ""),
            row.get("Outputs", ""),
            row.get("Error", ""),
            row.get("LogFile", ""),
        ])

    # Auto-size columns
    for col_idx in range(1, ws.max_column + 1):
        max_len = 0
        for r in range(1, ws.max_row + 1):
            val = ws.cell(row=r, column=col_idx).value
            s = "" if val is None else str(val)
            max_len = max(max_len, len(s))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 80)

    wb.save(cfg.workbook_path)


# ---------- Build file list ----------
def build_file_list(cfg: Config) -> List[Path]:
    files: List[Path] = []
    mode = cfg.input_mode

    if mode in {"file", "list"} and cfg.input_files:
        for p in cfg.input_files:
            if p.exists() and p.suffix.lower() in ALL_EXTS:
                files.append(p)

    if mode == "folder" and cfg.input_folder and cfg.input_folder.exists():
        if cfg.recurse_subfolders:
            for p in cfg.input_folder.rglob("*"):
                if p.is_file() and p.suffix.lower() in ALL_EXTS:
                    files.append(p)
        else:
            for p in cfg.input_folder.iterdir():
                if p.is_file() and p.suffix.lower() in ALL_EXTS:
                    files.append(p)

    # Deduplicate and sort
    uniq = sorted(set([f.resolve() for f in files]))
    return uniq


# ---------- Main ----------
def main():
    parser = argparse.ArgumentParser(description="Headless Docling OCR runner driven by Excel config.")
    parser.add_argument(
        "--config",
        required=False,
        help="Path to .xlsm/.xlsx with 'Exe config' sheet. If omitted, defaults to ~/Downloads/Data Extraction Tool2.3.xlsm"
    )
    parser.add_argument("--sheet", default="Exe config", help="Sheet name with configuration (default: 'Exe config')")
    args = parser.parse_args()

    # Default to ~/Downloads/Data Extraction Tool2.3.xlsm if --config not supplied
    default_cfg = Path.home() / "Downloads" / "Data Extraction Tool2.3.xlsm"
    cfg_path = Path(args.config) if args.config else default_cfg

    if not cfg_path.exists():
        print(f"Config workbook not found: {cfg_path}", file=sys.stderr)
        sys.exit(2)

    logger, temp_log, session_id = setup_logger()

    try:
        cfg = load_config_from_excel(cfg_path, sheet_name=args.sheet)
        logger.info("Loaded config from '%s'! Sheet: %s", cfg_path, cfg.sheet_name)

        # Decide final run log path
        output_dir = cfg.output_folder or (Path.home() / "Documents" / "DoclingOutputs")
        output_dir.mkdir(parents=True, exist_ok=True)
        final_log = output_dir / f"docling_run_{session_id}.log"
        retarget_logger(logger, temp_log, final_log)

        runner = DoclingHeadless(cfg, logger)

        files = build_file_list(cfg)
        if not files:
            raise RuntimeError("No input files resolved from the provided configuration.")

        logger.info("Total files to process: %d", len(files))

        run_rows = []
        total_ok = 0
        for f in files:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                outputs = runner.run_for_file(f, output_dir)
                outputs_str = ";".join(str(p) for p in outputs)
                logger.info("Completed: %s\nGenerated:\n%s", f, outputs_str)
                run_rows.append({
                    "Timestamp": ts,
                    "Input": str(f),
                    "Status": "OK",
                    "Outputs": outputs_str,
                    "Error": "",
                    "LogFile": str(final_log),
                })
                total_ok += 1
            except Exception as e:
                err = f"{e}\n{traceback.format_exc()}"
                logger.exception("Error processing %s", f)
                run_rows.append({
                    "Timestamp": ts,
                    "Input": str(f),
                    "Status": "ERROR",
                    "Outputs": "",
                    "Error": err,
                    "LogFile": str(final_log),
                })

        # Write back to workbook if configured
        if cfg.write_back_runlog:
            try:
                append_runlog_to_workbook(cfg, run_rows)
                logger.info("RunLog appended into workbook: %s", cfg.workbook_path)
            except Exception:
                logger.exception("Failed to write RunLog to workbook (macros preserved).")

        # Cleanup temps
        runner._cleanup_temp_dirs()

        logger.info("All done. Success: %d / %d. Log: %s", total_ok, len(files), final_log)
        # Exit code 0 if all OK, else 1
        sys.exit(0 if total_ok == len(files) else 1)

    except Exception as e:
        logger.exception("Fatal error")
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()