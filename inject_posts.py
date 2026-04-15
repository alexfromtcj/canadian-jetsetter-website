#!/usr/bin/env python3
"""
inject_posts.py — embed posts.json data into blog.html and index.html.

1. blog.html  — injects all 173 posts as window.POSTS_DATA in <head> so the
                blog index works when opened via file:// (no web server needed).

2. index.html — rewrites the 3-card 'Latest From The Blog' expanding-card row
                with the 3 newest posts from posts.json.

Both operations use marker comments so re-running is fully idempotent.

Usage:
    python3 inject_posts.py
"""

import html as htmlmod
import json
import os
import re

POSTS_JSON  = "posts.json"
BLOG_HTML   = "blog.html"
INDEX_HTML  = "index.html"

MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

# Fallback background colours for cards with no featured image
CARD_COLORS = ['#1a0808', '#0d1625', '#0a1f10']


# ── Helpers ───────────────────────────────────────────────────────────────────

def esc(s):
    """Escape plain text for safe HTML content / attribute insertion."""
    return (str(s)
            .replace('&',  '&amp;')
            .replace('<',  '&lt;')
            .replace('>',  '&gt;')
            .replace('"',  '&quot;'))


def fmt_date(date_str):
    """'2026-04-09' → 'Apr 9, 2026'"""
    y, m, d = date_str.split('-')
    return f"{MONTHS[int(m) - 1]} {int(d)}, {y}"


def pick_category(post):
    cats = [c for c in post.get('categories', []) if c != 'Uncategorized']
    return cats[0] if cats else 'Blog'


def find_matching_close_div(html, start_pos):
    """
    Return the index just after the </div> that closes the <div> at start_pos.
    start_pos must point at the '<' of the opening tag.
    """
    depth = 1
    pos   = start_pos + 4          # skip past '<div'
    while pos < len(html):
        next_open  = html.find('<div',  pos)
        next_close = html.find('</div>', pos)
        if next_close == -1:
            return -1
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 4
        else:
            depth -= 1
            pos = next_close + len('</div>')
            if depth == 0:
                return pos
    return -1


# ── Step 1: blog.html — inline POSTS_DATA in <head> ──────────────────────────

def inject_blog_html(posts):
    with open(BLOG_HTML, 'r', encoding='utf-8') as f:
        html = f.read()

    # Remove previous injection
    html = re.sub(
        r'\n?[ \t]*<!-- posts-data-start -->.*?<!-- posts-data-end -->[ \t]*\n?',
        '',
        html,
        flags=re.DOTALL,
    )

    posts_json = json.dumps(posts, ensure_ascii=False, separators=(',', ':'))
    block = (
        '  <!-- posts-data-start -->\n'
        '  <script>var POSTS_DATA=' + posts_json + ';</script>\n'
        '  <!-- posts-data-end -->'
    )

    if '</head>' not in html:
        raise ValueError('</head> not found in blog.html')

    html = html.replace('</head>', block + '\n</head>', 1)

    with open(BLOG_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

    # Verify ordering
    head_end   = html.find('</head>')
    data_start = html.find('var POSTS_DATA')
    order = 'BEFORE' if data_start < head_end else 'AFTER — ERROR'
    print(f"  blog.html  : {len(posts_json) // 1024} KB injected into <head> "
          f"(POSTS_DATA {order} </head>)")


# ── Step 2: index.html — update latest-blog expanding-card row ───────────────

def build_card(post, idx):
    """Return the HTML for one .blog-exp-card article."""
    is_active = ' is-active' if idx == 0 else ''
    color     = CARD_COLORS[idx]
    img       = post.get('featured_image', '')
    if img:
        bg = f'background-image:url(\'{img}\'); background-color:{color};'
    else:
        bg = f'background-color:{color};'

    category = pick_category(post)
    date     = fmt_date(post['date'])
    title    = post['title']    # plain text in posts.json
    excerpt  = post['excerpt']
    slug     = post['slug']

    return (
        f'          <article class="blog-exp-card{is_active}" style="{bg}">\n'
        f'            <div class="blog-exp-overlay"></div>\n'
        f'            <div class="blog-exp-content">\n'
        f'              <span class="blog-tag">{esc(category)}</span>\n'
        f'              <p class="blog-exp-date">{esc(date)}</p>\n'
        f'              <h3 class="blog-exp-title">{esc(title)}</h3>\n'
        f'              <p class="blog-exp-excerpt">{esc(excerpt)}</p>\n'
        f'              <a href="posts/{esc(slug)}.html" class="blog-exp-read-more">'
        f'Read More &#8594;</a>\n'
        f'            </div>\n'
        f'          </article>'
    )


def inject_index_html(posts):
    latest = posts[:3]

    cards    = '\n\n'.join(build_card(p, i) for i, p in enumerate(latest))
    new_block = (
        '        <!-- latest-blog-start -->\n'
        '        <div class="blog-exp-row">\n\n'
        + cards + '\n\n'
        '        </div>\n'
        '        <!-- latest-blog-end -->'
    )

    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html = f.read()

    if '<!-- latest-blog-start -->' in html:
        # Idempotent replacement using markers
        html = re.sub(
            r'[ \t]*<!-- latest-blog-start -->.*?<!-- latest-blog-end -->',
            new_block,
            html,
            flags=re.DOTALL,
        )
    else:
        # First run: locate the div by class and replace it
        marker = '<div class="blog-exp-row">'
        start  = html.find(marker)
        if start == -1:
            print('  index.html : WARNING — blog-exp-row div not found, skipping')
            return

        # Walk back to include any leading whitespace / newline
        line_start = html.rfind('\n', 0, start) + 1
        end = find_matching_close_div(html, start)
        if end == -1:
            print('  index.html : WARNING — could not find closing </div>, skipping')
            return

        html = html[:line_start] + new_block + html[end:]

    with open(INDEX_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

    titles = [p['title'][:55] for p in latest]
    print(f"  index.html : latest-blog row updated with 3 posts:")
    for i, t in enumerate(titles, 1):
        print(f"    {i}. {t}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with open(POSTS_JSON, 'r', encoding='utf-8') as f:
        posts = json.load(f)
    print(f"posts.json : {len(posts)} entries  (newest: {posts[0]['date']})\n")

    inject_blog_html(posts)
    inject_index_html(posts)

    print('\nDone.')


if __name__ == '__main__':
    main()
