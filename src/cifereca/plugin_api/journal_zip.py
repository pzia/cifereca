"""Public opt-in plugin API for the versioned raw journal ZIP reader (#3913).

Pure stdlib implementation lives at cifereca.journal_zip_contract. Importing
this submodule adds no application initialization or network calls of its own.
"""
from cifereca.journal_zip_contract import (
    JOURNAL_ZIP_SCHEMA_VERSION, JOURNAL_ZIP_FORMAT, RAW_JOURNAL_FORMAT,
    RAW_MEMBER, MANIFEST_MEMBER, README_MEMBER, DAILY_MEMBERS,
    MAX_READER_JSON_BYTES, MAX_READER_EVENTS, JournalZipError,
    read_raw_journal_zip,
)

__all__ = [
    "JOURNAL_ZIP_SCHEMA_VERSION", "JOURNAL_ZIP_FORMAT", "RAW_JOURNAL_FORMAT",
    "RAW_MEMBER", "MANIFEST_MEMBER", "README_MEMBER", "DAILY_MEMBERS",
    "MAX_READER_JSON_BYTES", "MAX_READER_EVENTS",
    "JournalZipError", "read_raw_journal_zip",
]
