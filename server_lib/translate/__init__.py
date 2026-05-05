"""Translation backend — shared by Tools (brain.py) and HTTP handlers.

Single source of truth: every code path (chat tool call, workflow node,
scheduled task, sidebar UI) ends up in the same functions here.
"""

from .detect import detect_language, LANG_NAMES
from .glossary import (
    list_glossaries, load_glossary, save_glossary, delete_glossary,
    glossary_to_system_block, GLOSSARY_DIR,
)
from .text import translate_text
from .document import translate_document_file, SUPPORTED_EXTS as DOCUMENT_EXTS
from .media import (
    transcribe_and_translate,
    translate_segments,
    write_output_files as write_media_output_files,
    to_srt, to_vtt, to_txt, to_bilingual_txt,
    SUPPORTED_EXTS as MEDIA_EXTS,
)
from .jobs import REGISTRY as JOB_REGISTRY, TranslateJob
from .live import REGISTRY as LIVE_REGISTRY, LiveSession

__all__ = [
    "detect_language",
    "LANG_NAMES",
    "list_glossaries",
    "load_glossary",
    "save_glossary",
    "delete_glossary",
    "glossary_to_system_block",
    "GLOSSARY_DIR",
    "translate_text",
    "translate_document_file",
    "DOCUMENT_EXTS",
    "transcribe_and_translate",
    "translate_segments",
    "write_media_output_files",
    "to_srt",
    "to_vtt",
    "to_txt",
    "to_bilingual_txt",
    "MEDIA_EXTS",
    "JOB_REGISTRY",
    "TranslateJob",
    "LIVE_REGISTRY",
    "LiveSession",
]
