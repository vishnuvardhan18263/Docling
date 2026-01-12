from docling.document_converter import DocumentConverter
import sys
sys.stdout.reconfigure(encoding='utf-8')
source = ""
def ExtractInvData(source:str)->str:
    converter = DocumentConverter()
    result = converter.convert(source)
    document_text = result.document.export_to_markdown()
    return document_text