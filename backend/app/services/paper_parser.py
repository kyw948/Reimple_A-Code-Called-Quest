import re
import shutil
import uuid
from pathlib import Path
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
    figure_count: int = 0
    figure_token: str | None = None


def parse_arxiv(arxiv_url: str) -> PaperParseResponse:
    try:
        arxiv_id = extract_arxiv_id(arxiv_url)
    except ValueError as exc:
        raise AppError("INVALID_ARXIV_URL", "올바른 arXiv URL이 아닙니다.") from exc

    try:
        metadata_response = requests.get(f"http://export.arxiv.org/api/query?id_list={arxiv_id}", timeout=30)
        metadata_response.raise_for_status()
        metadata = parse_arxiv_xml(metadata_response.text)

        pdf_response = requests.get(f"https://arxiv.org/pdf/{arxiv_id}.pdf", timeout=60)
        pdf_response.raise_for_status()
        pdf_bytes = pdf_response.content
        content = extract_pdf_text(pdf_bytes)
        figure_token, figure_count = save_figures_for_parse(pdf_bytes)
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
        figure_count=figure_count,
        figure_token=figure_token,
    )


def parse_pdf_upload(pdf_bytes: bytes, filename: str) -> PaperParseResponse:
    content = extract_pdf_text(pdf_bytes)
    title = _guess_pdf_title(content, filename)
    abstract = _extract_abstract(content)
    figure_token, figure_count = save_figures_for_parse(pdf_bytes)

    return PaperParseResponse(
        title=title,
        abstract=abstract,
        authors=[],
        year=None,
        content=content,
        source="pdf",
        url=None,
        figure_count=figure_count,
        figure_token=figure_token,
    )


def extract_figures(pdf_bytes: bytes, max_figures: int = 10) -> list[dict]:
    """Fallback: PDF에 포함된 래스터 이미지를 추출합니다."""
    try:
        import fitz
    except ImportError as exc:
        raise AppError("PDF_PARSE_FAILED", "PyMuPDF is required to extract PDF figures.") from exc

    figures = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            for page_num, page in enumerate(doc):
                for img in page.get_images(full=True):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    if not base_image:
                        continue
                    width = int(base_image.get("width") or 0)
                    height = int(base_image.get("height") or 0)
                    if width < 200 or height < 200:
                        continue
                    figures.append(
                        {
                            "page": page_num + 1,
                            "width": width,
                            "height": height,
                            "image_bytes": base_image["image"],
                            "ext": base_image.get("ext") or "png",
                        }
                    )
                    if len(figures) >= max_figures:
                        return figures
        finally:
            doc.close()
    except Exception as exc:
        raise AppError("PDF_PARSE_FAILED", "PDF figure extraction failed.") from exc
    return figures


def save_figures_for_parse(pdf_bytes: bytes) -> tuple[str | None, int]:
    """Parse 단계에서는 PDF만 임시 저장합니다. 관련 figure는 planning 이후 렌더링합니다."""
    token = str(uuid.uuid4())
    tmp_root = _paper_base_dir() / "_tmp" / token
    tmp_root.mkdir(parents=True, exist_ok=True)
    (tmp_root / "paper.pdf").write_bytes(pdf_bytes)
    return token, 0


def promote_figures_for_project(project_id: str, figure_token: str | None) -> int:
    if not figure_token:
        return 0
    source_root = _paper_base_dir() / "_tmp" / figure_token
    if not source_root.exists():
        return 0

    destination_root = _paper_base_dir() / project_id
    if destination_root.exists():
        shutil.rmtree(destination_root)
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_root), str(destination_root))
    return _count_project_figures(project_id)


def render_figure_page(pdf_bytes: bytes, page_number: int, dpi: int = 200) -> bytes:
    """특정 페이지를 PNG 이미지로 렌더링합니다. 벡터 그래픽도 함께 포함됩니다."""
    try:
        import fitz
    except ImportError as exc:
        raise AppError("PDF_PARSE_FAILED", "PyMuPDF is required to render PDF pages.") from exc

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_idx = page_number - 1
        if page_idx < 0 or page_idx >= len(doc):
            page_idx = 0
        page = doc[page_idx]
        zoom = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return pix.tobytes("png")
    finally:
        doc.close()


def find_figure_page_exact(pdf_bytes: bytes, figure_number: int | str | None) -> int:
    if figure_number is None:
        return 1
    number = str(figure_number).strip()
    if not number:
        return 1

    try:
        import fitz
    except ImportError as exc:
        raise AppError("PDF_PARSE_FAILED", "PyMuPDF is required to search PDF pages.") from exc

    patterns = [f"Figure {number}", f"Fig. {number}", f"FIGURE {number}", f"Fig {number}"]
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page_num in range(len(doc)):
            text = doc[page_num].get_text()
            lower_text = text.lower()
            if any(pattern.lower() in lower_text for pattern in patterns):
                return page_num + 1
    finally:
        doc.close()
    return 1


def save_relevant_figures_for_project(project_id: str, overall_plan: dict) -> int:
    pdf_path = get_project_pdf_path(project_id)
    if not pdf_path.exists():
        return _count_project_figures(project_id)

    pdf_bytes = pdf_path.read_bytes()
    figure_dir = get_project_figure_dir(project_id)
    figure_dir.mkdir(parents=True, exist_ok=True)

    architecture_figure = overall_plan.get("architecture_figure")
    saved = 0
    if architecture_figure not in (None, "", "null"):
        saved += _render_numbered_figure(pdf_bytes, figure_dir, architecture_figure, alias_zero=True)

    components = overall_plan.get("components")
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict):
                continue
            related_figure = component.get("related_figure")
            if related_figure in (None, "", "null", architecture_figure):
                continue
            saved += _render_numbered_figure(pdf_bytes, figure_dir, related_figure, alias_zero=False)

    return _count_project_figures(project_id) if saved else _count_project_figures(project_id)


def _render_numbered_figure(pdf_bytes: bytes, figure_dir: Path, figure_number: int | str, alias_zero: bool) -> int:
    page_number = find_figure_page_exact(pdf_bytes, figure_number)
    png_bytes = render_figure_page(pdf_bytes, page_number)
    normalized_number = _normalize_figure_number(figure_number)
    (figure_dir / f"figure_{normalized_number}.png").write_bytes(png_bytes)
    if alias_zero:
        (figure_dir / "0.png").write_bytes(png_bytes)
    return 1


def get_project_pdf_path(project_id: str) -> Path:
    return _paper_base_dir() / project_id / "paper.pdf"


def get_project_figure_dir(project_id: str) -> Path:
    return _paper_base_dir() / project_id / "figures"


def get_project_figure_path(project_id: str, index: int) -> Path:
    if index < 0:
        raise AppError("FIGURE_NOT_FOUND", "Figure not found.")
    figure_dir = get_project_figure_dir(project_id)
    candidate_names = [f"{index}.png", f"figure_{index}.png"]
    if index == 0:
        candidate_names.extend(path.name for path in sorted(figure_dir.glob("figure_*.png")))
    for name in candidate_names:
        path = figure_dir / name
        if path.exists():
            return path
    raise AppError("FIGURE_NOT_FOUND", "Figure not found.")


def _count_project_figures(project_id: str) -> int:
    figure_dir = get_project_figure_dir(project_id)
    if not figure_dir.exists():
        return 0
    return len([path for path in figure_dir.iterdir() if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}])


def _normalize_figure_number(figure_number: int | str) -> str:
    raw = str(figure_number).strip()
    match = re.search(r"\d+", raw)
    return match.group(0) if match else "0"


def parse_multipart_pdf(content_type: str, body: bytes) -> tuple[bytes, str]:
    boundary_match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not boundary_match:
        raise AppError("PDF_PARSE_FAILED", "PDF 업로드 요청 형식이 올바르지 않습니다.")

    boundary = ("--" + boundary_match.group(1)).encode("utf-8")
    separator = bytes([13, 10, 13, 10])
    trailing = bytes([13, 10, 45])
    for part in body.split(boundary):
        if b'name="file"' not in part:
            continue
        if separator not in part:
            continue
        raw_headers, raw_content = part.split(separator, 1)
        content = raw_content.rstrip(trailing)
        headers = raw_headers.decode("latin-1", errors="ignore")
        filename_match = re.search(r'filename="([^"]+)"', headers)
        filename = filename_match.group(1) if filename_match else "paper.pdf"
        if not content:
            raise AppError("PDF_PARSE_FAILED", "PDF 파일이 비어 있습니다.")
        return content, filename

    raise AppError("PDF_PARSE_FAILED", "업로드된 PDF 파일을 찾지 못했습니다.")


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

    full_text = chr(10).join(text_parts).strip()
    if not full_text:
        raise AppError("PDF_PARSE_FAILED", "PDF에서 텍스트를 추출하지 못했습니다.")

    if len(full_text) > MAX_PAPER_CONTENT_CHARS:
        return full_text[:MAX_PAPER_CONTENT_CHARS] + chr(10) + chr(10) + "[... 내용 일부 생략 ...]"
    return full_text


def _guess_pdf_title(content: str, filename: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:300]
    return filename


def _extract_abstract(content: str) -> str:
    pattern = r"abstract\s*([\s\S]{0,2500}?)(?:\n\s*(?:1\.|introduction)\b)"
    match = re.search(pattern, content, re.IGNORECASE)
    if not match:
        return ""
    return _normalize_space(match.group(1))[:1500]


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _paper_base_dir() -> Path:
    base = Path.home() / ".codepractice" / "papers"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_extension(extension: str) -> str:
    normalized = extension.lower().lstrip(".")
    if normalized == "jpeg":
        return "jpeg"
    if normalized in {"png", "jpg", "webp"}:
        return normalized
    return "png"
