"""Normalize a selected author without changing the search field."""
import re


def author_search_text(value: str) -> str:
    text = re.sub(r"\b(?:auteur|auteurs|author)\b", " ", str(value), flags=re.I)
    text = text.replace(",", " ")
    text = re.sub(r"\(\s*\)", " ", text)
    return re.sub(r"\s+", " ", text).strip(" :.;–-")
