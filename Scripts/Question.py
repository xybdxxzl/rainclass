"""雨课堂题面及图文选项的解析与合并。"""
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin


class _QuestionHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.images = []
        self.has_text = False

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            attrs = dict(attrs)
            self.images.append((attrs.get('data-src') or '').strip() or attrs.get('src') or '')
            if attrs.get('alt'):
                self.text.append(attrs['alt'])
        elif tag in ('br', 'p', 'div', 'li'):
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag in ('p', 'div', 'li'):
            self.text.append('\n')

    def handle_data(self, data):
        self.text.append(data)
        self.has_text = self.has_text or bool(data.strip())


def _parse_html(value):
    parser = _QuestionHTML()
    parser.feed(str(value or ''))
    parser.close()
    return ''.join(parser.text).strip(), parser.images, parser.has_text


def strip_html(value):
    text, images, _ = _parse_html(value)
    return text, [source for source in images if source.strip()]


def as_list(value):
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def _image_urls(sources):
    urls = {}
    for source in sources:
        if isinstance(source, dict):
            candidates = (source.get(key) for key in ('url', 'src', 'data-src'))
            source = next((url for url in candidates if isinstance(url, str) and url.strip()), '')
        if isinstance(source, str) and source.strip():
            source = unescape(source).strip()
            try:
                source = urljoin('https://www.yuketang.cn/', source)
            except ValueError:
                pass
            urls[source] = None
    # 题干或选项含有图片标记，但暂时没有可用的地址。
    return tuple(urls) or (('',) if sources else ())


@dataclass(frozen=True)
class QuestionPart:
    html: str
    text: str
    images: tuple
    has_text: bool


def _read_part(data, *fields):
    html = text = ''
    has_text = False
    sources = []
    for value in dict.fromkeys(str(data.get(field) or '') for field in fields):
        if not value.strip():
            continue
        plain, html_images, includes_text = _parse_html(value)
        if not html or (includes_text and not has_text):
            html, text, has_text = value, plain, includes_text
        sources.extend(html_images)
    sources.extend(as_list(data.get('images')))
    return QuestionPart(html, text, _image_urls(sources), has_text)


def question_parts(problem):
    stem = _read_part(problem, 'body', 'content', 'title')
    options = {}
    for index, raw in enumerate(as_list(problem.get('options'))):
        option = raw if isinstance(raw, dict) else {'value': raw}
        key = str(option.get('key') or '').strip().upper() or chr(65 + index)
        options[key] = _read_part(option, 'value', 'content')
    return stem, options


def image_signature(problem):
    """以图片归属和地址比较题面，忽略同一组图片的顺序变化。"""
    stem, options = question_parts(problem)
    return frozenset((key, url) for key, part in {'': stem, **options}.items() for url in part.images)


def _merged_html(older, newer):
    if newer and newer.has_text:
        return newer.html
    return (older.html if older else '') or (newer.html if newer else '')


def merge_problem(older, newer):
    """采用新题面的文字，保留下发课件和后续推送提供的图片。"""
    old_stem, old_options = question_parts(older)
    new_stem, new_options = question_parts(newer)
    options = []
    for key in dict.fromkeys([*old_options, *new_options]):
        previous, incoming = old_options.get(key), new_options.get(key)
        value = _merged_html(previous, incoming)
        images = _image_urls((previous.images if previous else ()) + (incoming.images if incoming else ()))
        options.append({'key': key, 'value': value, 'images': list(images)})
    return {
        'problemType': (newer.get('problemType') or older.get('problemType')
                        or newer.get('type') or older.get('type')),
        'body': _merged_html(old_stem, new_stem),
        'images': list(_image_urls(old_stem.images + new_stem.images)),
        'options': options,
        'blanks': newer.get('blanks') or older.get('blanks') or [],
    }
