from pathlib import Path

from PIL import Image

from monitor import fetcher, state as state_mod
from monitor.screenshot import split_tall_image

API_SAMPLE = [
    {
        "id": 7102,
        "date": "2026-10-01T10:26:00",
        "date_gmt": "2026-10-01T03:26:00",
        "link": "https://vientimtphcm.vn/luu-tru/7102",
        "title": {"rendered": "L&#7883;ch ti&#7871;p c&ocirc;ng d&acirc;n &#8220;Qu&yacute; IV&#8221;"},
        "content": {
            "rendered": '<figure><img loading="lazy" src="https://vientimtphcm.vn/wp-content/uploads/2026/10/a-1024x768.png" '
            'srcset="https://vientimtphcm.vn/wp-content/uploads/2026/10/a-300x200.png 300w, '
            'https://vientimtphcm.vn/wp-content/uploads/2026/10/a.png 1600w"></figure>'
            '<p><img src="/wp-content/uploads/2026/10/b-768x1024.jpg"></p>'
        },
    },
    {"id": 7088, "date": "2026-09-30T13:26:05", "link": "x", "title": {"rendered": "B"}, "content": {"rendered": ""}},
]

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>Lịch tiếp công dân</title><link>https://vientimtphcm.vn/luu-tru/7102</link>
<pubDate>Thu, 01 Oct 2026 03:26:00 +0000</pubDate><guid isPermaLink="false">http://vientimtphcm.vn/?p=7102</guid>
<content:encoded><![CDATA[<img src="https://vientimtphcm.vn/wp-content/uploads/x-1024x500.jpg">]]></content:encoded></item>
</channel></rss>"""


def test_parse_api():
    posts = fetcher.parse_api(API_SAMPLE)
    assert posts[0].id == 7102
    assert posts[0].title == "Lịch tiếp công dân “Quý IV”"
    assert posts[0].date_str == "01/10/2026 10:26"
    assert posts[0].image_urls == [
        "https://vientimtphcm.vn/wp-content/uploads/2026/10/a.png",
        "https://vientimtphcm.vn/wp-content/uploads/2026/10/b.jpg",
    ]
    assert posts[1].date_str == "30/09/2026 13:26"


def test_parse_rss():
    posts = fetcher.parse_rss(RSS_SAMPLE)
    assert len(posts) == 1 and posts[0].id == 7102
    assert posts[0].date_str == "01/10/2026 10:26"
    assert posts[0].image_urls == ["https://vientimtphcm.vn/wp-content/uploads/x.jpg"]


def test_state_roundtrip(tmp_path: Path):
    p = tmp_path / "s.json"
    st = state_mod.State()
    st.mark_seen(5)
    st.mark_seen(5)
    st.initialized = True
    state_mod.save(st, p)
    st2 = state_mod.load(p)
    assert st2.seen_ids == [5] and st2.initialized and st2.is_seen(5)
    p.write_text("{broken", encoding="utf-8")
    assert state_mod.load(p).seen_ids == []


def test_split_tall_image(tmp_path: Path):
    src = tmp_path / "post.png"
    Image.new("RGB", (1700, 15000), "white").save(src)
    parts = split_tall_image(src)
    assert len(parts) > 1
    total = 0
    for part in parts:
        w, h = Image.open(part).size
        assert w + h <= 10000 and h / w <= 20
        total += h
    assert total == 15000


def test_split_short_image(tmp_path: Path):
    src = tmp_path / "cat.png"
    Image.new("RGB", (1700, 1500), "white").save(src)
    parts = split_tall_image(src)
    assert len(parts) == 1 and parts[0].suffix == ".jpg"
