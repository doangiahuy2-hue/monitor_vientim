from datetime import datetime
from pathlib import Path

from PIL import Image

from monitor import config, fetcher, main as main_mod, screenshot, state as state_mod
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


API_SAMPLE_WITH_EMBED = [
    {
        "id": 7109,
        "date": "2026-10-09T07:55:00",
        "date_gmt": "2026-10-09T00:55:00",
        "link": "https://vientimtphcm.vn/luu-tru/7109",
        "title": {"rendered": "Ng\u00e0y S\u1ee9c kho\u1ebb T\u00e2m th\u1ea7n th\u1ebf gi\u1edbi (10/10)"},
        "content": {"rendered": ""},
        "_embedded": {
            "wp:term": [[
                {
                    "id": 19,
                    "name": "Tin t\u1ee9c - S\u1ef1 ki\u1ec7n",
                    "slug": "tin-tuc-su-kien",
                    "link": "https://vientimtphcm.vn/luu-tru/category/tin-tuc-su-kien",
                }
            ]]
        },
    }
]


def test_parse_api_with_embedded_category():
    posts = fetcher.parse_api(API_SAMPLE_WITH_EMBED)
    assert posts[0].id == 7109
    assert posts[0].category_name == "Tin tức - Sự kiện"
    assert posts[0].category_url == "https://vientimtphcm.vn/luu-tru/category/tin-tuc-su-kien"


def test_categories_param(monkeypatch):
    assert config.CATEGORIES == "all"
    assert config.get_categories_param() is None
    monkeypatch.setattr(config, "CATEGORIES", "17,19")
    assert config.get_categories_param() == "17,19"
    monkeypatch.setattr(config, "CATEGORIES", "all")
    assert config.get_categories_param() is None
    monkeypatch.setattr(config, "CATEGORIES", "")
    assert config.get_categories_param() is None


def test_parse_api_featured_media_and_multi_categories():
    sample = [
        {
            "id": 7197,
            "date": "2026-11-16T10:15:25",
            "date_gmt": "2026-11-16T03:15:25",
            "link": "https://vientimtphcm.vn/luu-tru/7197",
            "title": {"rendered": "Góc nhỏ yêu thương"},
            "content": {
                "rendered": '<img src="https://vientimtphcm.vn/wp-content/uploads/2026/11/2-1-724x1024.png">'
            },
            "_embedded": {
                "wp:featuredmedia": [
                    {
                        "id": 7203,
                        "date": "2026-11-16T10:15:13",
                        "link": "https://vientimtphcm.vn/luu-tru/7197/1-61",
                        "media_type": "image",
                        "source_url": "https://vientimtphcm.vn/wp-content/uploads/2026/11/1.png",
                    }
                ],
                "wp:term": [[
                    {"id": 21, "name": "Công tác xã hội- CSKH", "link": "https://vientimtphcm.vn/luu-tru/category/tro-giup-xa-hoi"},
                    {"id": 19, "name": "Tin tức - Sự kiện", "link": "https://vientimtphcm.vn/luu-tru/category/tin-tuc-su-kien"},
                ]],
            },
        },
        {
            "id": 7179,
            "date": "2026-11-11T09:07:06",
            "date_gmt": "2026-11-11T02:07:06",
            "link": "https://vientimtphcm.vn/luu-tru/7179",
            "title": {"rendered": "Chào mừng"},
            "content": {"rendered": '<img src="https://vientimtphcm.vn/wp-content/uploads/2026/11/slide-1024x576.jpg">'},
            "_embedded": {
                "wp:featuredmedia": [
                    {
                        "id": 2673,
                        "date": "2021-01-28T11:04:24",
                        "link": "https://vientimtphcm.vn/luu-tru/2645/thong-bao-2",
                        "media_type": "image",
                        "source_url": "https://vientimtphcm.vn/wp-content/uploads/2021/01/thong-bao.jpg",
                    }
                ],
            },
        },
        {
            "id": 7183,
            "date": "2026-11-16T08:17:25",
            "link": "https://vientimtphcm.vn/luu-tru/7183",
            "title": {"rendered": "Khảo sát"},
            "content": {"rendered": ""},
            "_embedded": {
                "wp:featuredmedia": [{"code": "rest_forbidden", "data": {"status": 401}}],
            },
        },
    ]
    posts = fetcher.parse_api(sample)
    assert posts[0].image_urls == [
        "https://vientimtphcm.vn/wp-content/uploads/2026/11/1.png",
        "https://vientimtphcm.vn/wp-content/uploads/2026/11/2-1.png",
    ]
    assert posts[0].category_name == "Công tác xã hội- CSKH, Tin tức - Sự kiện"
    assert posts[1].image_urls == ["https://vientimtphcm.vn/wp-content/uploads/2026/11/slide.jpg"]
    assert posts[2].image_urls == []


def test_extract_documents():
    html_snippet = """
    <p><a href="/wp-content/uploads/2026/08/thong-bao.pdf">Tải PDF</a></p>
    <p><a href="https://vientimtphcm.vn/wp-content/uploads/2026/08/thong-bao.pdf" download>Tải xuống</a></p>
    <p><a href="https://vientimtphcm.vn/wp-content/uploads/2026/08/mau-don.docx?v=1">Mẫu đơn</a></p>
    <p><a href="https://vientimtphcm.vn/luu-tru/7197">Bài viết</a></p>
    <p><a href="#top">Lên đầu</a></p>
    <iframe src="https://vientimtphcm.vn/wp-content/uploads/2026/08/bang-gia.xlsx"></iframe>
    """
    docs = fetcher.extract_documents(html_snippet)
    assert docs == [
        "https://vientimtphcm.vn/wp-content/uploads/2026/08/thong-bao.pdf",
        "https://vientimtphcm.vn/wp-content/uploads/2026/08/mau-don.docx?v=1",
        "https://vientimtphcm.vn/wp-content/uploads/2026/08/bang-gia.xlsx",
    ]


def test_render_fallback_html():
    html_doc = screenshot.render_fallback_html(
        title='Thông báo <Khẩn> & "Mới"',
        content_html="<p>Nội dung chi tiết</p>",
        date_str="16/11/2026 10:15",
        category_name="Thông báo",
        post_url="https://vientimtphcm.vn/luu-tru/7197",
    )
    assert 'id="fallback-article"' in html_doc
    assert "Thông báo &lt;Khẩn&gt; &amp; &quot;Mới&quot;" in html_doc
    assert "Nội dung chi tiết" in html_doc
    assert "16/11/2026 10:15" in html_doc


def test_detect_outputs(tmp_path: Path, monkeypatch):
    st_file = tmp_path / "state.json"
    out_file = tmp_path / "gh_output.txt"
    st = state_mod.State(seen_ids=[7187], initialized=True, last_heartbeat_date="2026-11-23")
    state_mod.save(st, st_file)

    monkeypatch.setattr(config, "STATE_FILE", st_file)
    monkeypatch.setattr(config, "HEARTBEAT_HOUR", 8)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    monkeypatch.setattr(main_mod, "now_vn", lambda: datetime(2026, 11, 23, 8, 5, tzinfo=config.TZ))

    # Không có bài mới, đã heartbeat hôm nay -> has_new=false, should_run=false
    monkeypatch.setattr(
        fetcher,
        "fetch_latest_posts",
        lambda: [fetcher.Post(id=7187, title="A", link="x", date=datetime(2026, 11, 10, tzinfo=config.TZ))],
    )
    assert main_mod.detect() is False
    assert "has_new=false\nshould_run=false\n" in out_file.read_text(encoding="utf-8")

    # Có bài mới (kể cả ID nhỏ hơn max nhưng mới đăng gần đây) -> has_new=true, should_run=true
    out_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        fetcher,
        "fetch_latest_posts",
        lambda: [
            fetcher.Post(id=7183, title="Mới", link="y", date=datetime(2026, 11, 16, tzinfo=config.TZ)),
            fetcher.Post(id=7187, title="A", link="x", date=datetime(2026, 11, 10, tzinfo=config.TZ)),
        ],
    )
    assert main_mod.detect() is True
    assert "has_new=true\nshould_run=true\n" in out_file.read_text(encoding="utf-8")

    # Không có bài mới, nhưng chưa gửi heartbeat hôm nay -> has_new=false, should_run=true
    out_file.write_text("", encoding="utf-8")
    st.last_heartbeat_date = "2026-11-22"
    state_mod.save(st, st_file)
    monkeypatch.setattr(
        fetcher,
        "fetch_latest_posts",
        lambda: [fetcher.Post(id=7187, title="A", link="x", date=datetime(2026, 11, 10, tzinfo=config.TZ))],
    )
    assert main_mod.detect() is False
    assert "has_new=false\nshould_run=true\n" in out_file.read_text(encoding="utf-8")


def test_fetch_api_merges_date_and_id(monkeypatch):
    calls = []

    def fake_query(client, orderby, per_page):
        calls.append((orderby, per_page))
        if orderby == "date":
            return [fetcher.Post(id=7197, title="A", link="u1", date=None)]
        raise RuntimeError("orderby=id timeout")

    monkeypatch.setattr(fetcher, "_query_api", fake_query)
    posts = fetcher.fetch_api(None)
    assert [p.id for p in posts] == [7197]
    assert calls[0][0] == "date"

    def fake_query_ok(client, orderby, per_page):
        if orderby == "date":
            return [fetcher.Post(id=7180, title="OldDate", link="u1", date=None)]
        return [fetcher.Post(id=7199, title="Backdated", link="u2", date=None)]

    monkeypatch.setattr(fetcher, "_query_api", fake_query_ok)
    posts = fetcher.fetch_api(None)
    assert [p.id for p in posts] == [7199, 7180]


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
