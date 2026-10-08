"""Readable Obsidian Markdown, full translation, never summarization."""
import re


def quote(text):
    return "\n".join("> " + line for line in text.splitlines())


def label(text):
    return re.sub(r"[\r\n#*`\[\]<>]", " ", str(text)).strip()


def render_transcript(units, translations):
    if len(units) != len(translations):
        raise ValueError("TRANSLATION_COUNT_MISMATCH")
    blocks = []
    for unit, translation in zip(units, translations):
        if translation["id"] != unit["id"]:
            raise ValueError("TRANSLATION_ID_MISMATCH")
        blocks.append(f"### {label(unit['timestamp'])} · {label(unit['speaker'])}\n\n"
                      f"**원문 · {label(translation['language']).upper()}**\n\n{unit['text']}")
        if translation["translation_ko"] is not None:
            blocks.append("> [!note] 한국어 번역\n" + quote(translation["translation_ko"]))
    return "\n\n".join(blocks) + "\n"


def render_appendix(original, translations):
    translated = [row["translation_ko"] for row in translations if row["translation_ko"] is not None]
    if not translated:
        return original
    return original + "\n\n---\n\n## 한국어 전체 번역\n\n" + "\n\n".join(translated) + "\n"
