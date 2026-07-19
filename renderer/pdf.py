from core.config import settings
from core.logger import get_logger
from core.exceptions import RenderError

logger = get_logger(__name__)


def _playwright_pdf_format() -> str:
    fmt = (getattr(settings, "pdf_page_format", None) or "letter").strip().lower()
    if fmt in ("a4", "a_4"):
        return "A4"
    return "Letter"


def _viewport_for_playwright_format(pdf_fmt: str) -> dict:
    """与纸张版心宽度（96dpi）对齐，减少与浏览器打印预览的缩放差。"""
    if pdf_fmt == "A4":
        return {"width": 794, "height": 1123}
    return {"width": 816, "height": 1056}


def _resolved_pdf_format(page_format: str | None) -> str:
    """page_format: None=读 settings；'a4' / 'letter' 强制纸张（Chromium 的 format + 视口）。"""
    if page_format is None:
        return _playwright_pdf_format()
    pf = page_format.strip().lower()
    if pf in ("a4", "a_4"):
        return "A4"
    if pf in ("letter", "us_letter"):
        return "Letter"
    return _playwright_pdf_format()


def html_to_pdf(html_content: str, *, page_format: str | None = None) -> bytes:
    """
    HTML → PDF。默认走 Chromium（Playwright），与 Chrome「打印 → 另存为 PDF」同一引擎。
    page_format: 传 'a4' 可强制 A4（求职信）；None 时用 settings.pdf_page_format（简历多为 Letter）。

    环境变量：PDF_ENGINE=chromium | weasyprint | weasyprint_first
    """
    engine = (settings.pdf_engine or "chromium").strip().lower().replace("-", "_")

    if engine in ("weasyprint", "weasy"):
        return _pdf_weasyprint_only(html_content)
    if engine in ("weasyprint_first", "weasy_first", "legacy"):
        return _pdf_weasyprint_then_chromium(html_content, page_format=page_format)
    return _pdf_chromium_then_weasyprint(html_content, page_format=page_format)


def _pdf_chromium_then_weasyprint(html_content: str, *, page_format: str | None = None) -> bytes:
    try:
        return _html_to_pdf_chromium(html_content, page_format=page_format)
    except Exception as e:
        logger.warning("Chromium PDF 失败，回退 WeasyPrint: %s", e)
        try:
            return _html_to_pdf_weasyprint(html_content)
        except Exception as e2:
            raise RenderError(f"PDF 渲染失败（Chromium 与 WeasyPrint 均失败）: {e} | {e2}") from e2


def _pdf_weasyprint_then_chromium(html_content: str, *, page_format: str | None = None) -> bytes:
    try:
        return _html_to_pdf_weasyprint(html_content)
    except Exception as e:
        logger.warning("WeasyPrint 失败，尝试 Chromium: %s", e)
        return _html_to_pdf_chromium(html_content, page_format=page_format)


def _pdf_weasyprint_only(html_content: str) -> bytes:
    try:
        return _html_to_pdf_weasyprint(html_content)
    except ImportError as e:
        raise RenderError("已配置 PDF_ENGINE=weasyprint 但未安装 WeasyPrint") from e
    except Exception as e:
        raise RenderError(f"WeasyPrint PDF 失败: {e}") from e


def _html_to_pdf_weasyprint(html_content: str) -> bytes:
    from weasyprint import HTML  # type: ignore

    return HTML(string=html_content).write_pdf(presentational_hints=True)


def _html_to_pdf_chromium(html_content: str, *, page_format: str | None = None) -> bytes:
    """
    Headless Chromium：先切到 print 媒体再灌 HTML，与 Chrome「打印 → 另存为 PDF」一致。
    pdf_print_background=False 时等同未勾选「背景图形」，避免灰底页边。
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as e:
        raise RenderError("未安装 Playwright，无法使用 Chromium 导出 PDF（pip install playwright && playwright install chromium）") from e

    pdf_fmt = _resolved_pdf_format(page_format)
    print_bg = bool(getattr(settings, "pdf_print_background", False))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    viewport=_viewport_for_playwright_format(pdf_fmt),
                    device_scale_factor=1,
                )
                # 先启用打印媒体，首屏即按 @media print / @page 排版（与浏览器打印预览一致）
                page.emulate_media(media="print")
                page.set_content(html_content, wait_until="load", timeout=60_000)
                try:
                    page.evaluate("() => document.fonts.ready")
                except Exception:
                    pass
                pdf_bytes = page.pdf(
                    format=pdf_fmt,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                    print_background=print_bg,
                    prefer_css_page_size=True,
                    scale=1.0,
                )
            finally:
                browser.close()
        logger.info(
            "PDF 渲染成功（Chromium，print 媒体，format=%s，print_background=%s）",
            pdf_fmt,
            print_bg,
        )
        return pdf_bytes
    except Exception as e:
        raise RenderError(f"Chromium PDF 渲染失败: {e}") from e
