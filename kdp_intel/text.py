"""Small, deterministic text tools shared by the writer, checker and typesetter."""

from __future__ import annotations

import re

CITE = re.compile(r"\[\[S:([a-z0-9]+-[0-9a-f]{6})\]\]")

# Italian numbers: 1.401,53 · 652.000 · 16,4% · 2025 · 90
_NUMBER = re.compile(r"(?<![\w/])\d{1,3}(?:\.\d{3})+(?:,\d+)?|(?<![\w/.])\d+(?:,\d+)?")
_LAW = re.compile(
    r"\b(?:D\.?\s?Lgs\.?|D\.?\s?L\.?|D\.?P\.?R\.?|L\.|[Ll]egge|art\.|[Aa]rticolo|[Cc]ircolare|"
    r"[Rr]egolamento\s+\(UE\)|[Dd]ecreto)\s*(?:n\.\s*)?\d+(?:/\d{2,4})?",
)
_MONTHS = (
    "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
)
_DATE = re.compile(rf"\b\d{{1,2}}°?\s+(?:{_MONTHS})\s+\d{{4}}\b|\b\d{{1,2}}/\d{{1,2}}/\d{{4}}\b", re.I)
_UNIT = re.compile(
    rf"\s*(?:%|€|°|euro\b|per cento|giorni|mesi|anni|rate|ore|settimane|punti|(?:{_MONTHS}))",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÀ-Ý«\"(])")
# Abbreviations that end in a dot without ending a sentence.
_ABBREV = re.compile(r"\b(?:art|artt|n|nn|D\.Lgs|D\.L|L|lett|co|comma|pag|pp|ecc|es|cfr|Sig|Dott|Avv)\.$")


_MAGNITUDE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mila|milioni|milione|miliardi|miliardo)\b", re.I)
_FACTOR = {"mila": 1_000, "milione": 1_000_000, "milioni": 1_000_000,
           "miliardo": 1_000_000_000, "miliardi": 1_000_000_000}


def _magnitudes(text: str) -> list[str]:
    """ "652mila" -> "652000", "1,2 milioni" -> "1200000" (as ISTAT writes them)."""
    out = []
    for value, word in _MAGNITUDE.findall(text):
        n = float(value.replace(".", "").replace(",", ".")) if "," in value else float(value)
        out.append(str(round(n * _FACTOR[word.lower()])))
    return out


def strip_cites(text: str) -> str:
    return re.sub(r"\s*\[\[S:[^\]]+\]\]", "", text).strip()


def cites(text: str) -> list[str]:
    return list(dict.fromkeys(CITE.findall(text)))


def sentences(text: str) -> list[str]:
    parts, out = _SENTENCE.split(text), []
    for part in parts:
        if out and _ABBREV.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return [s.strip() for s in out if s.strip()]


def numbers(text: str) -> list[str]:
    """Numbers in canonical form ("1401.53", "652000", "16.4"), citations removed."""
    body = strip_cites(text)
    body = _LAW.sub(lambda m: re.sub(r"\d", "#", m.group(0)), body)  # "art. 13" is a reference
    magnitudes = _magnitudes(body)  # "652mila" is the number 652000, not 652
    body = _MAGNITUDE.sub(lambda m: re.sub(r"\d", "#", m.group(0)), body)
    found = []
    for m in _NUMBER.finditer(body):
        raw = m.group(0)
        canon = raw.replace(".", "").replace(",", ".")
        # "i 3 errori più comuni" is prose; "8 rate", "5%" and "1° gennaio" are facts.
        if canon.isdigit() and int(canon) <= 10 and not _UNIT.match(body, m.end()):
            continue
        if canon not in found:
            found.append(canon)
    return found + [m for m in magnitudes if m not in found]


def law_refs(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", m.group(0)) for m in _LAW.finditer(strip_cites(text))]


def signals(text: str) -> list[str]:
    body = strip_cites(text)
    out = []
    if numbers(body):
        out.append("number")
    if "%" in body:
        out.append("percent")
    if "€" in body or re.search(r"\beuro\b", body, re.I):
        out.append("money")
    if _LAW.search(body):
        out.append("law")
    if _DATE.search(body):
        out.append("date")
    return out


def canonical_numbers_in(source: str) -> set[str]:
    """Every number a source contains, in the same canonical form."""
    found = set()
    for raw in _NUMBER.findall(source):
        found.add(raw.replace(".", "").replace(",", "."))
        found.add(raw.replace(",", "."))  # sources sometimes write 1,401.53 or 16.4
    found.update(_magnitudes(source))
    return found


def normalise_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()
