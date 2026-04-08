"""Wypisuje metadane odcinkow Friends z plikow HTML.

Dla kazdego pliku HTML w `scripts_people/<sezon>/` skrypt wypisuje:
- numer sezonu,
- numer odcinka (lub zakres, np. 17-18),
- tytul,
- caly blok informacji wystepujacy przed pierwsza scena/dialogiem,
- osobno wyciagniete pola redakcyjne:
  written by (takze originally written by),
  teleplay by,
  story by,
  directed by,
  hosted by,
  dutch phrases by,
  dutch phrases by,
  russian to roman alphabet.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

FIELD_ORDER = [
    "written by",
    "teleplay by",
    "story by",
    "directed by",
    "hosted by",
    "dutch phrases by",
    "russian to roman alphabet",
]


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_people_separators(value: str) -> str:
    """Ujednolica listy osob: and/&/+// -> przecinki."""
    text = _normalize_spaces(value)
    text = re.sub(r"\s*(?:&|\band\b|\+|/)\s*", ", ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    return text.strip(" ,")


def _split_people_names(value: str) -> List[str]:
    """Rozbija liste osob na osobne nazwiska/imiona.

    Uwaga: laczy skroty typu Jr./Sr. z poprzednim elementem.
    """
    normalized = _normalize_people_separators(value)
    parts = [p.strip() for p in normalized.split(",") if p.strip()]

    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
    merged: List[str] = []
    for part in parts:
        if part.lower() in suffixes and merged:
            merged[-1] = f"{merged[-1]}, {part}"
        else:
            merged.append(part)
    return merged


def _extract_title(raw_html: str, fallback: str) -> str:
    match = re.search(r"(?is)<title>(.*?)</title>", raw_html)
    if not match:
        return fallback
    return _normalize_spaces(html.unescape(match.group(1)))


def _normalize_the_one_casing(title: str) -> str:
    """Normalizuje kapitalizacje tytulow rozpoczynajacych sie od 'The one ...'."""
    t = _normalize_spaces(title)
    if not re.match(r"(?i)^the\s+one\b", t):
        return t

    # Zostaw apostrofy i laczniki; kapitalizuj kazdy czlon.
    words = t.split(" ")
    cased = [w[:1].upper() + w[1:].lower() if w else w for w in words]
    return " ".join(cased)


def _clean_episode_title(title: str) -> str:
    """Czyści techniczne prefiksy z tytułu, zostawiając część typu 'The One ...'."""
    t = _normalize_spaces(title)

    # Usuń wiodące 'Friends' (z opcjonalnymi separatorami).
    t = re.sub(r"(?i)^friends\b\s*[-:]*\s*", "", t)

    # Usuń wiodące kody odcinków (np. 922 -, 914, 10.12 -, 10.17-10.18 -).
    t = re.sub(r"^(?:\d{3,4}(?:-\d{3,4})?|\d{1,2}\.\d{2}(?:-\d{1,2}\.\d{2})?)\s*[-:]?\s*", "", t)

    # Czasem po pierwszym czyszczeniu zostaje jeszcze numer (np. po usunięciu 'Friends').
    t = re.sub(r"^(?:\d{3,4}(?:-\d{3,4})?|\d{1,2}\.\d{2}(?:-\d{1,2}\.\d{2})?)\s*[-:]?\s*", "", t)

    # Zamień skrót TOW na pełny początek tytułu.
    t = re.sub(r"(?i)^TOW\b\s*", "The One With ", t)

    # Jeśli tytuł zawiera "The One ..." po dodatkowym prefiksie, wytnij od tego miejsca.
    m = re.search(r"(?i)\b(the\s+one\b.*)$", t)
    if m:
        t = m.group(1)

    cleaned = _normalize_spaces(t)
    cleaned = cleaned if cleaned else _normalize_spaces(title)
    return _normalize_the_one_casing(cleaned)


def _html_to_lines(raw_html: str) -> List[str]:
    text = raw_html
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)

    # Zachowujemy sensowne podzialy linii.
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?i)</h[1-6]\s*>", "\n", text)

    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    lines: List[str] = []
    for line in text.splitlines():
        cleaned = _normalize_spaces(line)
        if cleaned:
            lines.append(cleaned)
    return lines


def _is_metadata_label(line: str) -> bool:
    """Sprawdza czy linia wyglada na etykiete metadanych, a nie dialog."""
    m = re.match(r"^([^:]{1,80}):", line)
    if not m:
        return False
    key = _normalize_spaces(m.group(1).lower())
    metadata_keys = {
        "written by",
        "originally written by",
        "written",
        "teleplay by",
        "teleplay",
        "story by",
        "story",
        "directed by",
        "directed",
        "hosted by",
        "dutch phrases by",
        "russian to roman alphabet",
        "russian to roman alphabet by",
        "transcribed by",
        "transcript by",
        "htmled by",
        "produced by",
        "with minor adjustments by",
        "dedicated to",
        "aired",
    }
    return key in metadata_keys


def _looks_like_dialogue_or_scene(line: str) -> bool:
    if re.match(r"^\[\s*Scene\b", line, re.IGNORECASE):
        return True
    # Wariant typu "RACHEL: ..."
    if re.match(r"^[A-Z][A-Z .\'\-]{1,35}:", line):
        return True
    # Wariant typu "Monica: ..." / "All: ...".
    # Odrzucamy etykiety metadanych typu "Teleplay:".
    if re.match(r"^(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}|All):", line) and not _is_metadata_label(line):
        return True
    return False


def _extract_pre_dialogue_block(raw_html: str, title: str) -> List[str]:
    lines = _html_to_lines(raw_html)

    # Pomijamy linie rowne tytulowi (czasem pojawiaja sie ponownie w <h1>).
    title_lc = title.lower()
    filtered_lines: List[str] = []
    for ln in lines:
        ln_lc = ln.lower()
        if ln_lc == title_lc:
            continue
        # Czasem tytul jest rozbity na 2 linie naglowka (np. "Friends" + "1017-1018 - ...").
        if ln_lc and ln_lc in title_lc:
            continue
        filtered_lines.append(ln)

    first_dialogue_idx = len(filtered_lines)
    for i, line in enumerate(filtered_lines):
        if _looks_like_dialogue_or_scene(line):
            first_dialogue_idx = i
            break

    pre_dialogue = filtered_lines[:first_dialogue_idx]

    # Usuwamy naglowki nawigacyjne, zostawiamy merytoryczny blok informacji.
    noise_patterns = [
        r"^OPENING CREDITS$",
        r"^CLOSING CREDITS$",
        r"^COMMERCIAL BREAK$",
        r"^THE END$",
        r"^END$",
    ]

    result: List[str] = []
    for line in pre_dialogue:
        if any(re.match(pat, line, re.IGNORECASE) for pat in noise_patterns):
            continue
        # Odfiltruj naglowki odcinka, zostaw tylko informacje redakcyjne.
        if ":" not in line and re.match(r"^(The One|Friends\b|\d{4}\s*-)", line, re.IGNORECASE):
            continue
        result.append(line)

    return result


def _extract_named_fields(block: List[str]) -> Dict[str, str]:
    """Wyciaga konkretne pola z bloku metadanych.

    `originally written by` jest mapowane do `written by`.
    Wpisy typu `Part I/II ...` sa mapowane do formatu `Osoba (part I/II)`.
    """
    out: Dict[str, List[str]] = {k: [] for k in FIELD_ORDER}

    field_re = re.compile(
        r"^(written by|originally written by|written|teleplay by|teleplay|story by|story|directed by|directed|hosted by|dutch phrases by|russian to roman alphabet(?: by)?)(?:\s*:)?\s*(.*)$",
        re.IGNORECASE,
    )

    for line in block:
        cleaned = _normalize_spaces(line)
        if not cleaned:
            continue

        part_label = ""
        part_match = re.match(r"^part\s*(i|ii|1|2)\s+(.+)$", cleaned, re.IGNORECASE)
        if part_match:
            part_raw = part_match.group(1).upper()
            if part_raw == "1":
                part_raw = "I"
            elif part_raw == "2":
                part_raw = "II"
            part_label = f"part {part_raw}"
            cleaned = _normalize_spaces(part_match.group(2))

        match = field_re.match(cleaned)
        if not match:
            continue

        raw_key = match.group(1).lower()
        value_raw = match.group(2).strip(" .")

        if raw_key in {"originally written by", "written"}:
            key = "written by"
        elif raw_key in {"teleplay"}:
            key = "teleplay by"
        elif raw_key in {"story"}:
            key = "story by"
        elif raw_key in {"directed"}:
            key = "directed by"
        elif raw_key.startswith("russian to roman alphabet"):
            key = "russian to roman alphabet"
        else:
            key = raw_key

        if not value_raw:
            continue

        # Dla odcinkow dwuczesciowych: przypisz part I/II przy kazdej osobie.
        if part_label and key in {"written by", "teleplay by", "story by"}:
            for person in _split_people_names(value_raw):
                out[key].append(f"{person} ({part_label})")
            continue

        value = _normalize_people_separators(value_raw)
        if value:
            out[key].append(value)

    # Zwracamy jako pojedyncze stringi (gdy wiele wpisow, laczymy separatorem).
    flattened: Dict[str, str] = {}
    for key in FIELD_ORDER:
        values = out.get(key, [])
        if values:
            flattened[key] = ", ".join(values)
        else:
            flattened[key] = ""
    return flattened


def _extract_title_from_raw_lines(raw_html: str, default_title: str) -> str:
    """Dodatkowy fallback tytulu skanujacy surowe linie strony."""
    for line in _html_to_lines(raw_html):
        candidate = _normalize_spaces(line)
        if not candidate:
            continue
        if re.search(r"(?i)\bthe\s+one\b", candidate):
            return candidate
    return default_title


def _extract_title_from_block(block: List[str], default_title: str) -> str:
    """Fallback tytulu na podstawie bloku informacyjnego."""
    if not block:
        return default_title

    for line in block:
        candidate = _normalize_spaces(line)
        if not candidate:
            continue

        # Preferuj linie z "The One ...".
        if re.search(r"(?i)\bthe\s+one\b", candidate):
            return candidate

        # Pomin linie metadanych typu "Written by:" itp.
        if re.match(
            r"^(written by|originally written by|written|teleplay by|teleplay|story by|story|directed by|directed|hosted by|dutch phrases by|russian to roman alphabet(?: by)?|transcribed by|transcript by|htmled by|produced by|with minor adjustments by|dedicated to|aired)\b",
            candidate,
            re.IGNORECASE,
        ):
            continue

        # Typowe linie tytulowe: "906 - ...", "912 - ...", "923-924 - ...", "TOW ..."
        if re.match(r"^(?:\d{3,4}(?:-\d{3,4})?\s*-\s*|TOW\b)", candidate, re.IGNORECASE):
            return candidate

    return default_title


def _parse_episode_number(filename_stem: str, season: str) -> str:
    # Przyklady: 0101, 0212-0213, 1017-1018, 07outtakes
    match = re.fullmatch(r"\d{2}(\d{2})(?:-\d{2}(\d{2}))?", filename_stem)
    if match:
        ep1 = str(int(match.group(1)))
        if match.group(2):
            ep2 = str(int(match.group(2)))
            return f"{ep1}-{ep2}"
        return ep1

    outtakes = re.fullmatch(rf"{season}outtakes", filename_stem, re.IGNORECASE)
    if outtakes:
        return "outtakes"

    return filename_stem


def iter_episode_metadata(
    root_dir: str = "scripts_people", season: Optional[str] = None
) -> Iterable[Tuple[str, str, str, List[str], Dict[str, str]]]:
    root = Path(root_dir)
    if not root.exists():
        raise FileNotFoundError(f"Nie znaleziono katalogu: {root_dir}")

    seasons = sorted([p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{2}", p.name)])
    if season is not None:
        season = season.zfill(2)
        seasons = [p for p in seasons if p.name == season]

    for season_dir in seasons:
        for html_file in sorted(season_dir.glob("*.html")):
            raw = html_file.read_text(encoding="latin-1", errors="ignore")
            title = _extract_title(raw, fallback=html_file.stem)
            episode_number = _parse_episode_number(html_file.stem, season=season_dir.name)
            block = _extract_pre_dialogue_block(raw, title=title)

            # Jesli <title> jest techniczne lub nie zawiera "The One", wezmij tytul z bloku.
            if (
                title.lower() in {"untitled document", html_file.stem.lower(), "friends"}
                or not re.search(r"(?i)\bthe\s+one\b", title)
            ):
                candidate = _extract_title_from_block(block, default_title=title)
                if candidate == title:
                    candidate = _extract_title_from_raw_lines(raw, default_title=title)
                title = candidate

            title = _clean_episode_title(title)

            fields = _extract_named_fields(block)
            yield season_dir.name, episode_number, title, block, fields


def _md_cell(value: str) -> str:
    return _normalize_spaces(value).replace("|", "\\|")


def _format_markdown_table(headers: List[str], values: List[str]) -> List[str]:
    """Buduje tabele markdown z wyrownanymi separatorami kolumn."""
    safe_headers = [_md_cell(h) for h in headers]
    safe_values = [_md_cell(v) for v in values]

    widths = [max(len(h), len(v)) for h, v in zip(safe_headers, safe_values)]

    header_line = "| " + " | ".join(h.ljust(w) for h, w in zip(safe_headers, widths)) + " |"
    row_line = "| " + " | ".join(v.ljust(w) for v, w in zip(safe_values, widths)) + " |"
    return [header_line, row_line]


def print_episode_headers(root_dir: str = "scripts_people", season: Optional[str] = None) -> None:
    """Wypisz metadane kazdego odcinka.

    Args:
        root_dir: Katalog z sezonami (`01`..`10`) i plikami HTML.
        season: Opcjonalnie numer sezonu, np. "2" lub "02".
    """
    headers = ["sezon", "odcinek", "tytul", *FIELD_ORDER]

    for season_no, episode_no, title, block, fields in iter_episode_metadata(root_dir=root_dir, season=season):
        print("Blok informacji przed dialogami:")
        if block:
            for line in block:
                print(f"  - {line}")
        else:
            print("  - (brak)")

        row_values = [
            season_no,
            episode_no,
            title,
            *[(fields.get(field, "") or "(brak)") for field in FIELD_ORDER],
        ]
        print("Tabela:")
        for line in _format_markdown_table(headers, row_values):
            print(line)


def export_episodes_csv(output_path: str, root_dir: str = "scripts_people") -> None:
    """Zapisz CSV dla wszystkich odcinkow we wszystkich sezonach.

    Kolumny: sezon, odcinek, tytul oraz pola z FIELD_ORDER.
    Blok informacyjny nie jest zapisywany do CSV.
    """
    headers = ["sezon", "odcinek", "tytul", *FIELD_ORDER]
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for season_no, episode_no, title, _block, fields in iter_episode_metadata(root_dir=root_dir, season=None):
            row = [
                season_no,
                episode_no,
                title,
                *[fields.get(field, "") for field in FIELD_ORDER],
            ]
            writer.writerow(row)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wypisz metadane odcinkow z HTML.")
    parser.add_argument("--root", default="scripts_people", help="Katalog glowny z sezonami.")
    parser.add_argument("--season", default=None, help="Opcjonalnie sezon, np. 02.")
    parser.add_argument("--csv-out", default=None, help="Sciezka pliku CSV do zapisu wszystkich odcinkow.")
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    if args.csv_out:
        export_episodes_csv(output_path=args.csv_out, root_dir=args.root)
        print(f"Zapisano CSV: {args.csv_out}")
    else:
        print_episode_headers(root_dir=args.root, season=args.season)
