import re
import xml.etree.ElementTree as ET

import requests
from pydantic import BaseModel, Field

from app.core.errors import AppError


MAX_PAPER_CONTENT_CHARS = 100_000


class PaperParseRequest(BaseModel):
    arxiv_url: str


class PaperParseResponse(BaseModel):
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    year: str | None = None
    content: str
    source: str
    url: str | None = None


def parse_arxiv(arxiv_url: str) -> PaperParseResponse:
    try:
        arxiv_id = extract_arxiv_id(arxiv_url)
    except ValueError as exc:
        raise AppError("INVALID_ARXIV_URL", "올바른 arXiv URL을 입력하세요.") from exc

    try:
        metadata_response = requests.get(f"http://export.arxiv.org/api/query?id_list={arxiv_id}", timeout=30)
        metadata_response.raise_for_status()
        metadata = parse_arxiv_xml(metadata_response.text)

        pdf_response = requests.get(f"https://arxiv.org/pdf/{arxiv_id}.pdf", timeout=60)
        pdf_response.raise_for_status()
        content = extract_pdf_text(pdf_response.content)
    except AppError:
        raise
    except ValueError as exc:
        raise AppError("PAPER_NOT_FOUND", "arXiv에서 논문을 찾을 수 없습니다.") from exc
    except requests.RequestException as exc:
        raise AppError("PAPER_NOT_FOUND", "arXiv 논문을 가져오지 못했습니다.") from exc

    return PaperParseResponse(
        title=metadata["title"],
        abstract=metadata["abstract"],
        authors=metadata["authors"],
        year=metadata.get("year"),
        content=content,
        source="arxiv",
        url=arxiv_url,
    )


def parse_pdf_upload(pdf_bytes: bytes, filename: str) -> PaperParseResponse:
    content = extract_pdf_text(pdf_bytes)
    title = _guess_pdf_title(content, filename)
    abstract = _extract_abstract(content)

    return PaperParseResponse(
        title=title,
        abstract=abstract,
        authors=[],
        year=None,
        content=content,
        source="pdf",
        url=None,
    )


def parse_multipart_pdf(content_type: str, body: bytes) -> tuple[bytes, str]:
    boundary_match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not boundary_match:
        raise AppError("PDF_PARSE_FAILED", "PDF 업로드 요청 형식이 올바르지 않습니다.")

    boundary = ("--" + boundary_match.group(1)).encode("utf-8")
    for part in body.split(boundary):
        if b'name="file"' not in part:
            continue
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, raw_content = part.split(b"\r\n\r\n", 1)
        content = raw_content.rstrip(b"\r\n-")
        headers = raw_headers.decode("latin-1", errors="ignore")
        filename_match = re.search(r'filename="([^"]+)"', headers)
        filename = filename_match.group(1) if filename_match else "paper.pdf"
        if not content:
            raise AppError("PDF_PARSE_FAILED", "PDF 파일이 비어 있습니다.")
        return content, filename

    raise AppError("PDF_PARSE_FAILED", "PDF 파일을 선택하세요.")


def extract_arxiv_id(url: str) -> str:
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", url)
    if match:
        return match.group(1)
    raise ValueError(f"Invalid arXiv URL: {url}")


def parse_arxiv_xml(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise ValueError("Paper not found on arXiv")

    title_node = entry.find("atom:title", ns)
    abstract_node = entry.find("atom:summary", ns)
    published_node = entry.find("atom:published", ns)

    return {
        "title": _normalize_space(title_node.text if title_node is not None else ""),
        "abstract": _normalize_space(abstract_node.text if abstract_node is not None else ""),
        "authors": [
            _normalize_space(name.text or "")
            for author in entry.findall("atom:author", ns)
            if (name := author.find("atom:name", ns)) is not None
        ],
        "year": (published_node.text or "")[:4] if published_node is not None else None,
    }


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        import fitz
    except ImportError as exc:
        raise AppError("PDF_PARSE_FAILED", "PyMuPDF가 설치되어 있지 않아 PDF를 읽을 수 없습니다.") from exc

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            text_parts = [page.get_text() for page in doc]
        finally:
            doc.close()
    except Exception as exc:
        raise AppError("PDF_PARSE_FAILED", "PDF 텍스트 추출에 실패했습니다.") from exc

    full_text = "\n".join(text_parts).strip()
    if not full_text:
        raise AppError("PDF_PARSE_FAILED", "PDF에서 텍스트를 추출하지 못했습니다.")

    if len(full_text) > MAX_PAPER_CONTENT_CHARS:
        return full_text[:MAX_PAPER_CONTENT_CHARS] + "\n\n[... 이후 내용 생략 ...]"
    return full_text


def _guess_pdf_title(content: str, filename: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:300]
    return filename


def _extract_abstract(content: str) -> str:
    match = re.search(r"abstract\s*([\s\S]{0,2500}?)(?:\n\s*(?:1\.|introduction)\b)", content, re.IGNORECASE)
    if not match:
        return ""
    return _normalize_space(match.group(1))[:1500]


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
