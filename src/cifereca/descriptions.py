"""Readable catalogue descriptions; no HTML execution or network access.

The provider contract exposes plain text. Preserve paragraphs/list boundaries
rather than passing arbitrary HTML to GTK/Pango. This Core implementation is
exported through the public Plugin API for provider reuse.
"""
from html import unescape
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET

MAX_DESCRIPTION_CHARS = 262144
BLOCKS = {'p', 'div', 'section', 'article', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'dl', 'dt', 'dd', 'table', 'tr', 'pre'}
HIDDEN = {'script', 'style', 'iframe', 'object', 'template', 'noscript'}
MARKUP = re.compile(r'</?(?:p|div|br|ul|ol|li|b|strong|em|i|span|h[1-6]|script|style|a|section|blockquote)\b', re.I)

class _Description(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []
    def handle_starttag(self, tag, attrs):
        tag = tag.split(':')[-1].lower()
        if self.hidden:
            if tag in HIDDEN:self.hidden.append(tag)
            return
        if tag in HIDDEN:self.hidden.append(tag);return
        if tag in BLOCKS:self.parts.append('\n\n')
        elif tag == 'br':self.parts.append('\n')
        elif tag == 'li':self.parts.append('\n• ')
        elif tag in {'td','th'}:self.parts.append(' · ')
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)
    def handle_endtag(self, tag):
        tag = tag.split(':')[-1].lower()
        if self.hidden:
            if tag == self.hidden[-1]:self.hidden.pop()
            return
        if tag in BLOCKS:self.parts.append('\n\n')
    def handle_data(self, data):
        if not self.hidden:self.parts.append(data)

def description_text(value):
    if value is None:return None
    text = str(value)
    truncated = len(text) > MAX_DESCRIPTION_CHARS
    text = text[:MAX_DESCRIPTION_CHARS]
    # Some APIs encode the complete markup once, rather than just text entities.
    decoded = unescape(text)
    if not MARKUP.search(text) and MARKUP.search(decoded):text = decoded
    parser = _Description();parser.feed(text);parser.close()
    lines = [re.sub(r'[^\S\n]+', ' ', line).strip() for line in ''.join(parser.parts).replace('\r\n','\n').replace('\r','\n').split('\n')]
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    if truncated:result += '\n… [résumé tronqué]'
    return result or None

def description_element(node):
    """Atom text/html or XHTML content, including nested block boundaries."""
    if node is None:return None
    if len(node):
        value = (node.text or '') + ''.join(ET.tostring(child, encoding='unicode') for child in node)
    else:value = node.text or ''
    return description_text(value)
