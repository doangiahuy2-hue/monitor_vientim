"""Phát hiện bài mới: WP REST API (nhanh ~0,7 s) → fallback RSS."""
from __future__ import annotations

import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from . import config

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
_DOC_TAG_RE = re.compile(r"<(?:a|iframe)\b[^>]*>", re.I)
_DOC_EXT_RE = re.compile(r"\.(?:pdf|docx?|xlsx?|zip|rar)$", re.I)
_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"|([\w-]+)\s*=\s*\'([^\']*)\'')
_SIZE_SUFFIX_RE = re.compile(r"-\d+x\d+(?=\.(?:jpe?g|png|gif|webp)$)", re.I)


@dataclass
class Post:
    id: int
    title: str
    link: str
    date: datetime | None
    image_urls: list[str] = field(default_factory=list)
    category_name: str = ""
    category_url: str = ""
    content_html: str = ""
    document_urls: list[str] = field(default_factory=list)

    @property
    def date_str(self) -> str:
        if not self.date:
            return ""
        return self.date.astimezone(config.TZ).strftime("%d/%m/%Y %H:%M")


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", s or ""))).strip()


def _parse_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for m in _ATTR_RE.finditer(tag):
        k = (m.group(1) or m.group(3)).lower()
        attrs[k] = m.group(2) if m.group(1) is not None else m.group(4)
    return attrs


def _normalize_image_url(src: str, base: str = config.BASE_URL) -> str:
    clean = _SIZE_SUFFIX_RE.sub("", html.unescape(src.strip()))
    return urljoin(base + "/", clean)


def extract_images(content_html: str, base: str = config.BASE_URL) -> list[str]:
    """Lấy link ảnh gốc (độ phân giải cao nhất) trong nội dung bài."""
    urls: list[str] = []
    for tag in _IMG_RE.findall(content_html or ""):
        attrs = _parse_attrs(tag)
        src = attrs.get("data-orig-file") or attrs.get("data-src") or attrs.get("src") or ""
        srcset = attrs.get("srcset", "")
        if srcset:  # chọn ảnh rộng nhất trong srcset
            best, best_w = "", 0
            for part in srcset.split(","):
                bits = part.strip().split()
                if len(bits) == 2 and bits[1].endswith("w") and bits[1][:-1].isdigit():
                    if int(bits[1][:-1]) > best_w:
                        best, best_w = bits[0], int(bits[1][:-1])
            src = best or src
        if not src or src.startswith("data:"):
            continue
        src = _normalize_image_url(src, base)  # bỏ hậu tố -1024x768 → ảnh gốc
        if src not in urls:
            urls.append(src)
    return urls


def extract_documents(content_html: str, base: str = config.BASE_URL) -> list[str]:
    """Lấy danh sách link file tài liệu đính kèm (.pdf, .doc, .docx, .xls, .xlsx, .zip, .rar)."""
    urls: list[str] = []
    for tag in _DOC_TAG_RE.findall(content_html or ""):
        attrs = _parse_attrs(tag)
        is_iframe = tag[:7].lower().startswith("<iframe")
        raw = (attrs.get("src") if is_iframe else attrs.get("href")) or ""
        raw = html.unescape(raw.strip())
        if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(base + "/", raw)
        if _DOC_EXT_RE.search(urlsplit(full_url).path) and full_url not in urls:
            urls.append(full_url)
    return urls


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(config.FETCH_TIMEOUT_SEC, connect=20),
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept-Language": "vi,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        follow_redirects=True,
    )


def _with_retry(fn, what: str, attempts: int = 3):
    delay = 5
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("%s thất bại (lần %d/%d): %s", what, i, attempts, e)
            if i < attempts:
                time.sleep(delay)
                delay *= 3
    raise RuntimeError(f"{what} thất bại sau {attempts} lần: {last}") from last


def _parse_iso_dt(value: str | None, default_tz=config.TZ) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=default_tz)
    except ValueError:
        return None


def _extract_featured_image(
    item: dict,
    post_id: int,
    post_date: datetime | None,
    base: str = config.BASE_URL,
) -> str | None:
    """Lấy ảnh đại diện nếu được tải lên riêng cho bài viết (tránh banner mặc định cũ)."""
    media_list = item.get("_embedded", {}).get("wp:featuredmedia", [])
    if not isinstance(media_list, list) or not media_list:
        return None
    media = media_list[0]
    if not isinstance(media, dict) or media.get("code"):
        return None
    if media.get("media_type") and media.get("media_type") != "image":
        return None
    src = (media.get("source_url") or "").strip()
    if not src or src.startswith("data:"):
        return None

    link = media.get("link") or ""
    belongs_to_post = f"/luu-tru/{post_id}/" in link or media.get("post") == post_id
    near_post_date = False
    if not belongs_to_post and post_date is not None:
        media_dt = _parse_iso_dt(media.get("date"))
        if media_dt is not None:
            near_post_date = abs((media_dt - post_date).total_seconds()) <= 30 * 86400
    if not (belongs_to_post or near_post_date):
        return None
    return _normalize_image_url(src, base)


def parse_api(data: list[dict]) -> list[Post]:
    posts = []
    for item in data:
        post_id = int(item["id"])
        date = _parse_iso_dt(item.get("date_gmt"), timezone.utc) or _parse_iso_dt(item.get("date"), config.TZ)
        content = (item.get("content") or {}).get("rendered", "")

        cat_name = ""
        cat_url = ""
        terms = item.get("_embedded", {}).get("wp:term", [])
        if terms and terms[0]:
            cat_names = [clean_text(t.get("name", "")) for t in terms[0]]
            cat_name = ", ".join(n for n in cat_names if n)
            cat_url = terms[0][0].get("link", "")

        image_urls = extract_images(content)
        featured_img = _extract_featured_image(item, post_id, date)
        if featured_img and featured_img not in image_urls:
            image_urls.insert(0, featured_img)

        posts.append(
            Post(
                id=post_id,
                title=clean_text((item.get("title") or {}).get("rendered", "")),
                link=item.get("link", ""),
                date=date,
                image_urls=image_urls,
                category_name=cat_name,
                category_url=cat_url,
                content_html=content,
                document_urls=extract_documents(content),
            )
        )
    return posts


def _query_api(client: httpx.Client, orderby: str, per_page: int) -> list[Post]:
    params: dict[str, str | int] = {
        "per_page": per_page,
        "orderby": orderby,
        "order": "desc",
        "_embed": "wp:term,wp:featuredmedia",
        "_fields": "id,date,date_gmt,modified,modified_gmt,link,title,content,categories,featured_media,_links,_embedded",
    }
    cats = config.get_categories_param()
    if cats:
        params["categories"] = cats

    r = client.get(config.API_URL, params=params, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError("API trả về dữ liệu không phải danh sách")
    return parse_api(data)


def fetch_api(client: httpx.Client) -> list[Post]:
    posts_by_id: dict[int, Post] = {
        p.id: p for p in _query_api(client, "date", config.FETCH_PER_PAGE)
    }
    try:
        for p in _query_api(client, "id", min(10, config.FETCH_PER_PAGE)):
            posts_by_id.setdefault(p.id, p)
    except Exception as e:  # noqa: BLE001 — không để truy vấn phụ làm hỏng lượt kiểm tra
        log.warning("Truy vấn phụ orderby=id lỗi (giữ kết quả theo date): %s", e)
    return sorted(posts_by_id.values(), key=lambda p: p.id, reverse=True)


_NS = {"content": "http://purl.org/rss/1.0/modules/content/"}
_GUID_ID_RE = re.compile(r"[?&]p=(\d+)")
_LINK_ID_RE = re.compile(r"/(\d+)/?$")


def parse_rss(xml_text: str) -> list[Post]:
    root = ET.fromstring(xml_text)
    posts = []
    for item in root.iter("item"):
        guid = item.findtext("guid") or ""
        link = item.findtext("link") or ""
        m = _GUID_ID_RE.search(guid) or _LINK_ID_RE.search(link)
        if not m:
            continue
        date = None
        if item.findtext("pubDate"):
            try:
                date = parsedate_to_datetime(item.findtext("pubDate"))
            except (TypeError, ValueError):
                pass
        content = item.findtext("content:encoded", default="", namespaces=_NS)
        category = clean_text(item.findtext("category") or "")
        posts.append(
            Post(
                id=int(m.group(1)),
                title=clean_text(item.findtext("title") or ""),
                link=link.strip(),
                date=date,
                image_urls=extract_images(content),
                category_name=category,
                content_html=content,
                document_urls=extract_documents(content),
            )
        )
    return posts


def fetch_rss(client: httpx.Client) -> list[Post]:
    cats = config.get_categories_param()
    feed_url = config.RSS_URL
    if cats and "," not in cats and int(cats) == 17:
        feed_url = f"{config.CATEGORY_URL}/feed"
    r = client.get(feed_url, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    return parse_rss(r.text)


def fetch_latest_posts() -> list[Post]:
    """Trả về danh sách bài mới nhất (mới → cũ). Raise nếu mọi nguồn đều lỗi."""
    with _client() as client:
        try:
            posts = _with_retry(lambda: fetch_api(client), "REST API")
            source = "api"
        except Exception as e:  # noqa: BLE001
            log.error("REST API lỗi, chuyển sang RSS: %s", e)
            posts = _with_retry(lambda: fetch_rss(client), "RSS")
            source = "rss"
    posts.sort(key=lambda p: p.id, reverse=True)
    log.info("Lấy %d bài qua %s (mới nhất: %s)", len(posts), source, posts[0].id if posts else "-")
    return posts
