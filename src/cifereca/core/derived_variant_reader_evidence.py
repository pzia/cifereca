"""Private conservative KEPUB-specific local reader evidence (#6937).

A suffix or format claim is insufficient. Observe attributable text spans
inside every spine document; this is not a Kobo device rendering receipt.
Image-only or unsupported layouts remain unqualified, never damaged by review.
"""
import re

_XHTML = "{http://www.w3.org/1999/xhtml}"
_ID = re.compile(r"kobo[.][0-9]+[.][0-9]+", re.ASCII)


def kepub_text_spans_observed(page):
    seen = set()
    for node in page.iter(_XHTML + "span"):
        if "koboSpan" not in node.get("class", "").split():
            continue
        key = node.get("id", "")
        if _ID.fullmatch(key) is None or key in seen:
            return False
        seen.add(key)
        if not any(text.strip() for text in node.itertext()):
            return False
    return bool(seen)
