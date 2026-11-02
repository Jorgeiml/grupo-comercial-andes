"""Deterministic chunking of policy documents: one chunk per numbered section (no LLM involved)."""

import re
from dataclasses import dataclass
from pathlib import Path

_TITLE = re.compile(r"^# (.+)$", re.MULTILINE)
_CODE = re.compile(r"\*\*Código:\*\*\s*([A-Z]+-[A-Z]+-\d+)")
_VERSION = re.compile(r"\*\*Versión:\*\*\s*([\d.]+)")
_SECTION = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class PolicyChunk:
    doc_code: str
    title: str
    version: str
    section: str
    section_title: str
    source: str
    body: str

    @property
    def id(self) -> str:
        # Stable ID: re-running the ingestion updates chunks instead of duplicating them
        return f"{self.doc_code}#{self.section}"

    @property
    def citation(self) -> str:
        return f"{self.doc_code} §{self.section}"

    @property
    def text(self) -> str:
        # The document title goes first so a section like "Excepciones" is still searchable by topic
        return f"{self.title} ({self.doc_code}) — §{self.section} {self.section_title}\n\n{self.body}"

    def metadata(self) -> dict:
        return {
            "doc_code": self.doc_code,
            "title": self.title,
            "version": self.version,
            "section": self.section,
            "section_title": self.section_title,
            "source": self.source,
        }


def chunk_document(path: Path) -> list[PolicyChunk]:
    content = path.read_text(encoding="utf-8")
    title, code, version = _TITLE.search(content), _CODE.search(content), _VERSION.search(content)
    if not (title and code and version):
        raise ValueError(f"{path.name}: missing title, code or version header")

    sections = list(_SECTION.finditer(content))
    if not sections:
        raise ValueError(f"{path.name}: no numbered sections found")

    chunks = []
    for i, match in enumerate(sections):
        end = sections[i + 1].start() if i + 1 < len(sections) else len(content)
        chunks.append(
            PolicyChunk(
                doc_code=code[1],
                title=title[1].strip(),
                version=version[1],
                section=match[1],
                section_title=match[2].strip(),
                source=path.name,
                body=content[match.end():end].strip(),
            )
        )
    return chunks


def load_chunks(policies_dir: Path) -> list[PolicyChunk]:
    return [chunk for path in sorted(policies_dir.glob("*.md")) for chunk in chunk_document(path)]
