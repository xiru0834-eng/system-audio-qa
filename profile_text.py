from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from pypdf import PdfReader

MAX_TEXT = 48000
MAX_FILE = 20 * 1024 * 1024
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_profile(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE:
        raise ValueError("资料文件超过 20 MB。可复制相关段落后直接粘贴。")
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        data = path.read_bytes()
        encodings = ("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
        for encoding in encodings:
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("无法识别文本编码。请保存为 UTF-8 或直接粘贴文字。")
    elif suffix == ".docx":
        with ZipFile(path) as archive:
            part = archive.getinfo("word/document.xml")
            if part.file_size > MAX_FILE:
                raise ValueError("文档展开后过大。请选择与面试相关的段落。")
            document = ElementTree.fromstring(archive.read(part))
        text = "\n".join(
            "".join(node.text or "" for node in paragraph.iter(f"{WORD}t"))
            for paragraph in document.iter(f"{WORD}p")
        )
    elif suffix == ".pdf":
        with path.open("rb") as file:
            reader = PdfReader(file)
            if reader.is_encrypted:
                raise ValueError("PDF 已加密。请使用可打开的副本或粘贴文字。")
            pages = []
            length = 0
            for page in reader.pages:
                content = page.extract_text() or ""
                length += len(content) + 1
                if length > MAX_TEXT:
                    raise ValueError("资料文本超过 48,000 字符。请导入相关章节。")
                pages.append(content)
            text = "\n".join(pages)
    else:
        raise ValueError("请选择 TXT、Markdown、DOCX 或 PDF。")
    text = text.strip()
    if not text:
        raise ValueError("没有读到文本。扫描版 PDF 可以先识别文字，再复制粘贴。")
    if len(text) > MAX_TEXT:
        raise ValueError("资料文本超过 48,000 字符。请导入相关章节。")
    return text
