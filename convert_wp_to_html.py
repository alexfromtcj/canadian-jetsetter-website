#!/usr/bin/env python3
"""
Convert WordPress XML export to static HTML blog post files and posts.json index.

Full run  (POST_OFFSET=0, MAX_POSTS=None):
  - Regenerates posts.json from scratch (all published posts)
  - Generates all HTML files

Partial run (any other combination):
  - Generates only the selected HTML files
  - Upserts those entries into the existing posts.json (prepending if newest)

Usage: python3 convert_wp_to_html.py
"""

import html
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

XML_PATH = "TCJ Website Assets/mp4s/thecanadianjetsettercom.WordPress.2026-04-10.xml"
TEMPLATE_PATH = "blog-post-template.html"
OUTPUT_DIR = "posts"
POSTS_JSON = "posts.json"

POST_OFFSET = 0     # skip this many posts (0-indexed)
MAX_POSTS = None    # how many HTML files to generate; None = all

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "excerpt": "http://wordpress.org/export/1.2/excerpt/",
    "wp":      "http://wordpress.org/export/1.2/",
    "dc":      "http://purl.org/dc/elements/1.1/",
}

# Inline style applied to every body <img> so images are full-width and stacked.
IMG_STYLE = "max-width:100%; width:100%; height:auto; display:block; margin: 1rem 0;"


# ── Content cleaning ──────────────────────────────────────────────────────────

def strip_gutenberg_comments(content):
    """Remove <!-- wp:* --> and <!-- /wp:* --> block comments, keep inner HTML."""
    return re.sub(r"<!--\s*/?wp:.*?-->", "", content, flags=re.DOTALL).strip()


def unwrap_column_and_gallery_blocks(content):
    """
    Unwrap wp-block-columns / wp-block-column divs and wp-block-gallery figures
    so that any side-by-side images become sequential full-width block elements.
    Iterates because blocks can be nested.
    """
    for _ in range(10):
        prev = content
        content = re.sub(
            r'<div[^>]*\bwp-block-column\b[^>]*>(.*?)</div>',
            r'\1', content, flags=re.DOTALL,
        )
        content = re.sub(
            r'<div[^>]*\bwp-block-columns\b[^>]*>(.*?)</div>',
            r'\1', content, flags=re.DOTALL,
        )
        content = re.sub(
            r'<figure[^>]*\bwp-block-gallery\b[^>]*>(.*?)</figure>',
            r'\1', content, flags=re.DOTALL,
        )
        if content == prev:
            break
    return content


def apply_img_styles(content):
    """Inject IMG_STYLE onto every <img> tag; merge with existing style if present."""
    def patch_img(m):
        tag = m.group(0)
        if re.search(r'\bstyle\s*=', tag):
            return re.sub(
                r'(style\s*=\s*")([^"]*?)(")',
                lambda s: s.group(1) + s.group(2).rstrip(";") + "; " + IMG_STYLE + s.group(3),
                tag,
            )
        return re.sub(r'(\s*/?>)$', f' style="{IMG_STYLE}"\\1', tag)

    return re.sub(r'<img\b[^>]*/?>',  patch_img, content, flags=re.DOTALL)


def clean_body_content(raw_content):
    """Full content-cleaning pipeline for post body HTML."""
    content = strip_gutenberg_comments(raw_content)
    content = unwrap_column_and_gallery_blocks(content)
    content = apply_img_styles(content)
    return content


def content_to_plain_text(html_str, max_len=160):
    """Strip all markup and decode entities; return trimmed plain text up to max_len."""
    text = re.sub(r"<!--.*?-->", "", html_str, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)          # decode &amp; &#x27; &nbsp; etc.
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


# ── Template helpers ──────────────────────────────────────────────────────────

def format_date(date_str):
    """Format '2026-04-09 12:41:03' as 'April 9, 2026'."""
    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    return dt.strftime("%B %-d, %Y")


def fix_relative_paths(page_html):
    """
    Prefix local relative paths with ../ so they resolve correctly
    from the posts/ subdirectory.
    Skips: http(s)://, //, #anchors, already-relative ../, and data: URIs.
    """
    def prefix(m):
        attr, path = m.group(1), m.group(2)
        return f'{attr}="../{path}"'
    return re.sub(
        r'(src|href)="(?!https?://|//|#|\.\./|data:)([^"]+)"',
        prefix, page_html,
    )


def wp_title_for_attr(title):
    """
    Make a WP CDATA title safe for use in an HTML attribute.
    WP titles already carry HTML entities (e.g. &amp;); only raw " needs escaping.
    """
    return title.replace('"', "&quot;")


def build_featured_img_tag(src, alt_text):
    safe_alt = wp_title_for_attr(alt_text)
    return (
        f'<img\n'
        f'          src="{src}"\n'
        f'          alt="{safe_alt}"\n'
        f'          class="featured-image"\n'
        f'          loading="eager"\n'
        f'        />'
    )


def generate_post_html(template, post_data):
    """Inject post data into the template and return the final HTML string."""
    title           = post_data["title"]           # WP CDATA: already HTML-entity-encoded
    excerpt         = post_data["excerpt"]         # plain text
    formatted_date  = post_data["formatted_date"]
    featured_img_url = post_data["featured_img_url"]
    content         = post_data["content"]

    excerpt_attr = html.escape(excerpt, quote=True)

    page = template

    # 1. <title>
    page = re.sub(
        r"<title>.*?</title>",
        lambda m: f"<title>{title} | The Canadian Jetsetter</title>",
        page, count=1, flags=re.DOTALL,
    )

    # 2. <meta name="description">
    page = re.sub(
        r'<meta name="description" content=".*?" />',
        lambda m: f'<meta name="description" content="{excerpt_attr}" />',
        page, count=1, flags=re.DOTALL,
    )

    # 3. Post date label + h1 title (injected between date and featured image)
    page = re.sub(
        r'<p class="post-date-label">.*?</p>',
        lambda m: (
            f'<p class="post-date-label">{formatted_date}</p>\n'
            f'        <h1 class="post-title">{title}</h1>'
        ),
        page, count=1,
    )

    # 4. Featured image
    if featured_img_url:
        page = re.sub(
            r'<img\s[^>]*class="featured-image"[^>]*/?>',
            lambda m: build_featured_img_tag(featured_img_url, title),
            page, count=1, flags=re.DOTALL,
        )

    # 5. Replace entire article-body inner content
    def replace_body(m):
        return m.group(1) + "\n\n" + content + "\n\n        " + m.group(2)

    page = re.sub(
        r'(<div class="article-body">).*?(</div><!-- /article-body -->)',
        replace_body, page, count=1, flags=re.DOTALL,
    )

    return page


# ── posts.json ────────────────────────────────────────────────────────────────

def extract_post_meta(item, attachment_map):
    """Return a dict of post metadata for posts.json."""
    # Title: decode HTML entities → plain Unicode text
    title_el = item.find("title")
    title_raw = (title_el.text or "").strip() if title_el is not None else ""
    title = html.unescape(title_raw)

    slug_el = item.find("wp:post_name", NS)
    slug = (slug_el.text or "post").strip() if slug_el is not None else "post"

    date_el = item.find("wp:post_date", NS)
    date_str = (date_el.text or "1970-01-01 00:00:00") if date_el is not None else "1970-01-01 00:00:00"
    date = date_str[:10]  # YYYY-MM-DD

    # Excerpt: WordPress excerpt field, falling back to content-derived plain text
    excerpt_el = item.find("excerpt:encoded", NS)
    excerpt_raw = (excerpt_el.text or "").strip() if excerpt_el is not None else ""
    content_el = item.find("content:encoded", NS)
    raw_content = (content_el.text or "") if content_el is not None else ""
    if excerpt_raw:
        excerpt = html.unescape(re.sub(r"<[^>]+>", "", excerpt_raw).strip())
    else:
        excerpt = content_to_plain_text(raw_content)

    # Featured image
    featured_image = ""
    for meta in item.findall("wp:postmeta", NS):
        key_el = meta.find("wp:meta_key", NS)
        val_el = meta.find("wp:meta_value", NS)
        if (
            key_el is not None and key_el.text == "_thumbnail_id"
            and val_el is not None and val_el.text
        ):
            featured_image = attachment_map.get(val_el.text, "")
            break

    # Categories (domain="category") and tags (domain="post_tag")
    categories, tags = [], []
    for cat in item.findall("category"):
        domain = cat.get("domain", "")
        name = (cat.text or "").strip()
        if not name:
            continue
        if domain == "category":
            if name not in categories:
                categories.append(name)
        elif domain == "post_tag":
            if name not in tags:
                tags.append(name)

    return {
        "slug":          slug,
        "title":         title,
        "date":          date,
        "excerpt":       excerpt,
        "featured_image": featured_image,
        "categories":    categories,
        "tags":          tags,
    }


def generate_posts_json(all_posts, attachment_map):
    """Regenerate posts.json from scratch using all published posts."""
    entries = [extract_post_meta(item, attachment_map) for _, item in all_posts]
    with open(POSTS_JSON, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print(f"  Wrote {len(entries)} entries → {POSTS_JSON}")


def upsert_posts_json(html_posts, attachment_map):
    """
    For partial runs: read the existing posts.json, remove any entries whose
    slugs match the newly generated posts, insert the new entries, re-sort by
    date descending, and write back.
    """
    # Load existing JSON (or start fresh if missing)
    existing = []
    if os.path.exists(POSTS_JSON):
        with open(POSTS_JSON, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                existing = []

    new_entries = [extract_post_meta(item, attachment_map) for _, item in html_posts]
    new_slugs = {e["slug"] for e in new_entries}

    # Remove stale copies of the same slugs, prepend the fresh entries
    merged = new_entries + [e for e in existing if e["slug"] not in new_slugs]
    merged.sort(key=lambda e: e["date"], reverse=True)

    with open(POSTS_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"  Upserted {len(new_entries)} entr{'y' if len(new_entries)==1 else 'ies'} → {POSTS_JSON} ({len(merged)} total)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # ── Load template ────────────────────────────────────────
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    # ── Parse XML ────────────────────────────────────────────
    tree = ET.parse(XML_PATH)
    root = tree.getroot()

    # Build attachment ID → URL map
    attachment_map = {}
    for item in root.findall("./channel/item"):
        post_type = item.find("wp:post_type", NS)
        post_id   = item.find("wp:post_id",   NS)
        if post_type is not None and post_type.text == "attachment" and post_id is not None:
            att_url_el = item.find("wp:attachment_url", NS)
            link_el    = item.find("link")
            url = (
                (att_url_el.text or "").strip() if att_url_el is not None
                else (link_el.text or "").strip() if link_el is not None
                else ""
            )
            if url:
                attachment_map[post_id.text] = url

    # Collect ALL published posts, sorted newest-first
    all_posts = []
    for item in root.findall("./channel/item"):
        post_type = item.find("wp:post_type", NS)
        status    = item.find("wp:status",    NS)
        if (
            post_type is not None and post_type.text == "post"
            and status is not None and status.text == "publish"
        ):
            date_el  = item.find("wp:post_date", NS)
            date_str = date_el.text if date_el is not None else "1970-01-01 00:00:00"
            all_posts.append((date_str, item))

    all_posts.sort(key=lambda x: x[0], reverse=True)

    is_full_run = (POST_OFFSET == 0 and MAX_POSTS is None)

    # ── Step 1: posts.json ───────────────────────────────────
    if is_full_run:
        print(f"Step 1 — Generating posts.json ({len(all_posts)} posts)…")
        generate_posts_json(all_posts, attachment_map)
        print()

    # ── Step 2: HTML files ───────────────────────────────────
    html_posts = all_posts[POST_OFFSET:]
    if MAX_POSTS is not None:
        html_posts = html_posts[:MAX_POSTS]

    print(f"Step 2 — Generating {len(html_posts)} HTML file(s)…")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for date_str, item in html_posts:
        title_el = item.find("title")
        title = (title_el.text or "Untitled").strip() if title_el is not None else "Untitled"

        slug_el = item.find("wp:post_name", NS)
        slug = (slug_el.text or "post").strip() if slug_el is not None else "post"

        excerpt_el  = item.find("excerpt:encoded", NS)
        excerpt_raw = (excerpt_el.text or "").strip() if excerpt_el is not None else ""

        content_el  = item.find("content:encoded", NS)
        raw_content = (content_el.text or "") if content_el is not None else ""
        content     = clean_body_content(raw_content)

        if excerpt_raw:
            excerpt_plain = html.unescape(re.sub(r"<[^>]+>", "", excerpt_raw).strip())
        else:
            excerpt_plain = content_to_plain_text(raw_content)

        # Featured image
        featured_img_url = ""
        for meta in item.findall("wp:postmeta", NS):
            key_el = meta.find("wp:meta_key", NS)
            val_el = meta.find("wp:meta_value", NS)
            if (
                key_el is not None and key_el.text == "_thumbnail_id"
                and val_el is not None and val_el.text
            ):
                featured_img_url = attachment_map.get(val_el.text, "")
                break

        post_data = {
            "title":            title,
            "excerpt":          excerpt_plain,
            "formatted_date":   format_date(date_str),
            "featured_img_url": featured_img_url,
            "content":          content,
        }

        page_html = generate_post_html(template, post_data)
        page_html = fix_relative_paths(page_html)

        output_path = os.path.join(OUTPUT_DIR, f"{slug}.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(page_html)

        print(f"  {output_path}  [{format_date(date_str)}]")

    # ── For partial runs: upsert posts.json ──────────────────
    if not is_full_run:
        print()
        print("Updating posts.json…")
        upsert_posts_json(html_posts, attachment_map)

    print()
    print(f"Done. {len(html_posts)} HTML file(s) in {OUTPUT_DIR}/")
    if is_full_run:
        print(f"      {len(all_posts)} entries in {POSTS_JSON}")


if __name__ == "__main__":
    main()
