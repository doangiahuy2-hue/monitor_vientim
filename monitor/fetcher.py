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
from urllib.parse import urljoin

import httpx

from . import config

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"|([\w-]+)\s*=\s*\'([^\']*)\'')
_SIZE_SUFFIX_RE = re.compile(r"-\d+x\d+(?=\.(?:jpe?g|png|gif|webp)$)", re.I)


@dataclass
class Post:
    id: int
    title: str
    link: str
    date: datetime | None
    image_urls: list[str] = field(default_factory=list)

    @property
    def date_str(self) -> str:
        if not self.date:
            return ""
        return self.date.astimezone(config.TZ).strftime("%d/%m/%Y %H:%M")


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", s or ""))).strip()


def extract_images(content_html: str, base: str = config.BASE_URL) -> list[str]:
    """Lấy link ảnh gốc (độ phân giải cao nhất) trong nội dung bài."""
    urls: list[str] = []
    for tag in _IMG_RE.findall(content_html or ""):
        attrs = {}
        for m in _ATTR_RE.finditer(tag):
            k = (m.group(1) or m.group(3)).lower()
            attrs[k] = m.group(2) if m.group(1) else m.group(4)
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
        src = _SIZE_SUFFIX_RE.sub("", html.unescape(src))  # bỏ hậu tố -1024x768 → ảnh gốc
        src = urljoin(base + "/", src)
        if src not in urls:
            urls.append(src)
    return urls


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(config.FETCH_TIMEOUT_SEC, connect=20),
        headers={"User-Agent": config.USER_AGENT, "Accept-Language": "vi,en;q=0.8"},
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


def parse_api(data: list[dict]) -> list[Post]:
    posts = []
    for item in data:
        date = None
        if item.get("date_gmt"):
            date = datetime.fromisoformat(item["date_gmt"]).replace(tzinfo=timezone.utc)
        elif item.get("date"):
            date = datetime.fromisoformat(item["date"]).replace(tzinfo=config.TZ)
        content = (item.get("content") or {}).get("rendered", "")
        posts.append(
            Post(
                id=int(item["id"]),
                title=clean_text((item.get("title") or {}).get("rendered", "")),
                link=item.get("link", ""),
                date=date,
                image_urls=extract_images(content),
            )
        )
    return posts


def fetch_api(client: httpx.Client) -> list[Post]:
    params = {
        "categories": config.CATEGORY_ID,
        "per_page": 10,
        "orderby": "date",
        "order": "desc",
        "_fields": "id,date,date_gmt,link,title,content",
    }
    r = client.get(config.API_URL, params=params)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError("API trả về dữ liệu không phải danh sách")
    return parse_api(data)


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
        posts.append(
            Post(
                id=int(m.group(1)),
                title=clean_text(item.findtext("title") or ""),
                link=link.strip(),
                date=date,
                image_urls=extract_images(content),
            )
        )
    return posts


def fetch_rss(client: httpx.Client) -> list[Post]:
    r = client.get(config.RSS_URL)
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
