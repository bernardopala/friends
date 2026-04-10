#!/usr/bin/env python3
"""Parsuje transkrypty Friends (HTML) do CSV per odcinek.

Wejscie:
- domyslnie: scripts_people/<sezon>/*.html

Wyjscie:
- domyslnie: scripts_people_csv/xxyy.csv
- kolumny: script_element, character, dialogue

script_element przyjmuje wartosci:
- heading
- dialogue
- action
- singing
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from bs4 import BeautifulSoup

SCRIPT_ELEMENTS = {"heading", "dialogue", "action", "singing"}
METADATA_LABELS = {
    "written by",
    "originally written by",
    "teleplay by",
    "story by",
    "directed by",
    "hosted by",
    "produced by",
    "final check by",
    "transcribed by",
    "transcript by",
    "minor additions and adjustments by",
    "with minor adjustments by",
    "with minot adjustments by",
    "with help from",
    "additional transcribing by",
    "dutch phrases by",
    "russian to roman alphabet",
    "russian to roman alphabet by",
}
SCENE_PREFIXES = (
    "scene",
)
THOUGHT_CUE_PATTERN = re.compile(
    r"(?i)(?:\([^)]*\bin (?:his|her) head\.?\s*\)|starts thinking to (?:him|her)\s*self)"
)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_paragraph_to_lines(paragraph_html: str) -> List[str]:
    """Dzieli zawartosc pojedynczego <p> na linie, respektujac <br>."""
    chunks = re.split(r"(?i)<br\s*/?>", paragraph_html)
    lines: List[str] = []
    for chunk in chunks:
        soup = BeautifulSoup(chunk, "html.parser")
        text = normalize_spaces(soup.get_text(" "))
        if text:
            lines.append(text)
    return lines


def split_embedded_headings(text: str) -> List[str]:
    """Rozcina linie typu '... [Scene: ...]' na osobne fragmenty."""
    line = normalize_spaces(text)
    if not line:
        return []

    parts = [line]
    patterns = [r"\[\s*Scene\s*:", r"(?<!\[)\bScene\s*:"]
    for pattern in patterns:
        new_parts: List[str] = []
        for part in parts:
            match = re.search(pattern, part, flags=re.IGNORECASE)
            if match and match.start() > 0:
                before = normalize_spaces(part[: match.start()])
                after = normalize_spaces(part[match.start() :])
                if before:
                    new_parts.append(before)
                if after:
                    new_parts.append(after)
            else:
                new_parts.append(part)
        parts = new_parts

    return parts


def is_metadata_label(text: str) -> bool:
    head = text.split(":", 1)[0].strip().lower()
    return head in METADATA_LABELS


def is_probably_glued_block(text: str) -> bool:
    """Wykrywa linie, w ktorych sklejono wiele kwestii/scen naraz."""
    if len(text) < 700:
        return False

    speaker_like = len(re.findall(r"\b[\w\[\]'.-]{2,30}\s*:", text))
    has_scene_marker = "[Scene:" in text or "[Scene " in text
    return speaker_like >= 8 and has_scene_marker


def classify_bracketed(text: str) -> str:
    """Rozroznia [Scene: ...] od pozostalych [..] didaskaliow."""
    inner = normalize_spaces(text[1:-1]).lower()
    if inner.startswith(SCENE_PREFIXES):
        return "heading"
    return "action"


def parse_dialogue_line(text: str) -> Tuple[str, str] | None:
    """Zwraca (character, dialogue) jesli linia wyglada na kwestie dialogowa."""
    match = re.match(r"^([^:]{1,80})\s*:\s*(.+)$", text)
    if not match:
        return None

    speaker = normalize_spaces(match.group(1))
    spoken = normalize_spaces(match.group(2))
    if not speaker or not spoken:
        return None
    if is_metadata_label(text):
        return None

    # Odrzucamy pseudo-mowcow typu "[Scene" z uszkodzonego HTML.
    if speaker.startswith("["):
        return None

    # Odrzucamy przypadki ewidentnie nie-dialogowe typu "Note: ...".
    if speaker.lower() in {"note", "transcriber's note"}:
        return None

    # Dopuszczamy postaci wielowyrazowe: "The Interviewer", "Rachel and Bonnie" itp.
    if not re.search(r"[A-Za-z]", speaker):
        return None

    return speaker, spoken


def classify_line(text: str) -> Tuple[str, str, str] | None:
    """Zwraca (script_element, character, dialogue) albo None gdy linia do pominiecia."""
    if not text:
        return None

    if is_probably_glued_block(text):
        return None

    # Nawet jesli znacznik jest lekko popsuty, Scene: traktujemy jako heading.
    if re.match(r"^\[\s*Scene\s*:", text, flags=re.IGNORECASE):
        return "heading", "", text
    if re.match(r"^Scene\s*:", text, flags=re.IGNORECASE):
        return "heading", "", f"[{text}]"

    if text.startswith("[") and text.endswith("]"):
        return classify_bracketed(text), "", text

    dialogue = parse_dialogue_line(text)
    if dialogue:
        character, spoken = dialogue
        return "dialogue", character, spoken

    if text.startswith("(") and text.endswith(")"):
        return "action", "", text

    # Pozostale linie narracyjne/didaskalia traktujemy jako action.
    return "action", "", text


def is_script_start(row: Tuple[str, str, str]) -> bool:
    element, character, _dialogue = row
    if element == "heading":
        return True
    if element == "dialogue" and character and "[" not in character and not is_metadata_label(f"{character}:"):
        return True
    return False


def iter_script_rows(html_text: str) -> Iterable[Tuple[str, str, str]]:
    soup = BeautifulSoup(html_text, "html.parser")
    body = soup.body or soup

    started = False
    for node in body.find_all(["p", "h3"]):
        if node.name == "p":
            # Niektore pliki maja zle domkniete <p>, przez co zewnetrzny <p>
            # zawiera wszystkie kolejne akapity. Przetwarzamy tylko "lisciowe" <p>.
            if node.find("p") is not None:
                continue
            lines = split_paragraph_to_lines(str(node))
        else:
            # W czesci zrodel znaczniki typu Opening/Commercial/Closing Credits
            # sa w <h3>, wiec musimy je uwzglednic.
            heading_text = normalize_spaces(node.get_text(" "))
            lines = [heading_text] if heading_text else []

        for line in lines:
            for fragment in split_embedded_headings(line):
                row = classify_line(fragment)
                if row is None:
                    continue

                # Odcinamy naglowek redakcyjny przed skryptem.
                if not started:
                    if is_script_start(row):
                        started = True
                    else:
                        continue

                element, character, dialogue = row
                if element not in SCRIPT_ELEMENTS:
                    continue
                yield element, character, dialogue




def iter_script_rows_from_raw_blocks(html_text: str) -> Iterable[Tuple[str, str, str]]:
    """Fallback parser for malformed HTML where BeautifulSoup loses dialogue text.

    It iterates raw <p>/<h3> blocks in source order and classifies extracted lines.
    """
    started = False
    block_pattern = re.compile(r"(?is)<(p|h3)\b[^>]*>(.*?)</\1>")

    for match in block_pattern.finditer(html_text):
        tag_name = match.group(1).lower()
        inner_html = match.group(2)

        if tag_name == "p":
            lines = split_paragraph_to_lines(inner_html)
        else:
            heading_text = normalize_spaces(BeautifulSoup(inner_html, "html.parser").get_text(" "))
            lines = [heading_text] if heading_text else []

        for line in lines:
            normalized_line = re.sub(r"\s+:", ":", line)
            for fragment in split_embedded_headings(normalized_line):
                row = classify_line(fragment)
                if row is None:
                    continue

                if not started:
                    if is_script_start(row):
                        started = True
                    else:
                        continue

                element, character, dialogue = row
                if element not in SCRIPT_ELEMENTS:
                    continue
                yield element, character, dialogue

def episode_codes_from_stem(stem: str, season_hint: str) -> List[str]:
    """Zwraca liste kodow xxyy dla danego pliku.

    - 0101 -> [0101]
    - 0212-0213 -> [0212-0213]
    - 0615-0616 -> [0615-0616]
    - 07outtakes -> []
    """
    if stem == "0212-0213" and season_hint == "02":
        return [stem]
    if stem == "0615-0616" and season_hint == "06":
        return [stem]

    four_digit_codes = re.findall(r"\d{4}", stem)
    if four_digit_codes:
        unique_codes = []
        seen = set()
        for code in four_digit_codes:
            if code[:2] != season_hint:
                continue
            if code not in seen:
                seen.add(code)
                unique_codes.append(code)
        return unique_codes

    m = re.match(r"^(\d{2})(\d{2})$", stem)
    if m and m.group(1) == season_hint:
        return [f"{m.group(1)}{m.group(2)}"]

    if stem.lower() == f"{season_hint}outtakes":
        return [f"{season_hint}outtakes"]

    return []


def output_filename_for_episode_code(episode_code: str) -> str:
    return f"{episode_code}.csv"


def apply_episode_specific_fixes(
    episode_code: str, rows: Sequence[Tuple[str, str, str]], html_text: str = ""
) -> List[Tuple[str, str, str]]:
    """Naklada reczne poprawki dla znanych bledow klasyfikacji."""
    fixed_rows = list(rows)

    # 0110: linie piosenki Phoebe sa czasem klasyfikowane jako action.
    # Chcemy oznaczyc je jako singing + character=Phoebe.
    if episode_code == "0110":
        target_csv_lines = {41, 42, 43, 44, 45, 48, 49, 50, 51, 54, 55}
        for csv_line_number in target_csv_lines:
            row_index = csv_line_number - 2  # -1 za naglowek CSV i -1 do indeksu 0-based
            if 0 <= row_index < len(fixed_rows):
                _element, _character, text = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", "Phoebe", text)

        # Ta kwestia to juz mowa Phoebe, nie spiew.
        dialogue_line_number = 56
        dialogue_row_index = dialogue_line_number - 2
        if 0 <= dialogue_row_index < len(fixed_rows):
            _element, _character, text = fixed_rows[dialogue_row_index]
            fixed_rows[dialogue_row_index] = ("dialogue", "Phoebe", text)

        # Oznaczenia "(Sung)" normalizujemy do akcji z jasnym opisem.
        for idx, (_element, _character, text) in enumerate(fixed_rows):
            if text == "(Sung)":
                fixed_rows[idx] = ("action", "", "(Phoebe sung)")

    # 0111: fragment piosenki Phoebe powinien byc oznaczony jako singing.
    if episode_code == "0111":
        singing_lines = {234, 235, 236, 237}
        for csv_line_number in singing_lines:
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, character, text = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", character, text)

    if episode_code == "0118":
        opening_scene_text = "The whole gang is helping Rachel mail out resumes while whistling the theme from The Bridge on the River Kwai."
        opening_scene = f"[Scene: Monica and Rachel's, {opening_scene_text}]"
        has_opening_scene = any(
            element == "heading" and "The whole gang is helping Rachel mail out resumes" in dialogue
            for element, _character, dialogue in fixed_rows
        )
        if not has_opening_scene:
            fixed_rows.insert(0, ("heading", "", opening_scene))

    if episode_code == "0119":
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                row[0] == "dialogue"
                and "transcriber's note" in row[1].lower()
            )
        ]

        for idx, (element, character, text) in enumerate(fixed_rows):
            scene_source = ""
            if element == "dialogue" and character.lower().startswith("(scene 4"):
                scene_source = f"{character}: {text}"
            elif text.lower().startswith("(scene 4:"):
                scene_source = text

            if not scene_source:
                continue

            m = re.match(r"^\(scene\s*\d+\s*:\s*(.+?)\)\s*$", normalize_spaces(scene_source), flags=re.IGNORECASE)
            if not m:
                continue

            scene_text = normalize_spaces(m.group(1))
            fixed_rows[idx] = ("heading", "", f"[Scene: {scene_text}]")
            break

    if episode_code == "0306":
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                row[0] == "dialogue"
                and (
                    "transcriber's note" in row[1].lower()
                    or "transcriber's note" in row[2].lower()
                )
            )
        ]

    if episode_code == "0314":
        for csv_line_number in {3, 11, 150, 152, 153, 160}:
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, character, text = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", character, text)

    if episode_code == "0401":
        for idx, (element, character, dialogue) in enumerate(fixed_rows):
            if element != "dialogue":
                continue
            if "(voice-over)" not in dialogue.lower():
                continue
            content = re.sub(r"\(voice-over\)\s*", "", dialogue, flags=re.IGNORECASE)
            fixed_rows[idx] = (element, character, f"<voiceover>{content}<\\voiceover>")

        csv_line_number = 201
        row_index = csv_line_number - 2
        if 0 <= row_index < len(fixed_rows):
            _element, character, dialogue = fixed_rows[row_index]
            fixed_rows[row_index] = ("singing", character, dialogue)

    if episode_code == "0507":
        for idx, (element, _character, dialogue) in enumerate(fixed_rows):
            if element == "action" and normalize_spaces(dialogue) == "Health Inspector:":
                fixed_rows[idx] = (
                    "dialogue",
                    "Health Inspector",
                    "Wow, Monica, if every restaurant is as clean as yours, I'd have a tough time making a living.",
                )
                break


    if episode_code == "0508" and html_text:
        # In 0508 broken HTML cuts some cue lines to just "Character:".
        malformed_cues = ("Past Life Phoebe", "French Phoebe")
        for cue in malformed_cues:
            match = re.search(
                rf"(?is)<p[^>]*>\s*{re.escape(cue)}:\s*</b>\s*(.*?)</p>",
                html_text,
            )
            if not match:
                continue

            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if not parsed_text:
                continue

            for idx, (element, character, dialogue) in enumerate(fixed_rows):
                if element not in {"dialogue", "action"} or character:
                    continue
                if normalize_spaces(dialogue) != f"{cue}:":
                    continue
                fixed_rows[idx] = ("dialogue", cue, parsed_text)
                break

    if episode_code == "0511" and html_text:
        malformed_cues = {
            "Jay Leno": r"(?is)<p[^>]*>\s*Jay Leno:\s*</b>\s*(.*?)</p>",
            "Woman": r"(?is)<p[^>]*>\s*Woman:\s*</b>\s*(.*?)</p>",
            "Chandler and Joey": r"(?is)<p[^>]*>\s*Chandler and\s*<b>\s*Joey:\s*</b>\s*(.*?)</p>",
        }

        for cue, pattern in malformed_cues.items():
            match = re.search(pattern, html_text)
            if not match:
                continue

            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if not parsed_text:
                continue

            for idx, (element, character, dialogue) in enumerate(fixed_rows):
                if element not in {"dialogue", "action"} or character:
                    continue
                if normalize_spaces(dialogue) != f"{cue}:":
                    continue
                fixed_rows[idx] = ("dialogue", cue, parsed_text)
                break


    if episode_code == "0512" and html_text:
        match = re.search(
            r"(?is)<p[^>]*>\s*Both:\s*</b>\s*(.*?)</p>",
            html_text,
        )
        if match:
            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if parsed_text:
                for idx, (element, character, dialogue) in enumerate(fixed_rows):
                    if element not in {"dialogue", "action"} or character:
                        continue
                    if normalize_spaces(dialogue) != "Both:":
                        continue
                    fixed_rows[idx] = ("dialogue", "Both", parsed_text)
                    break


    if episode_code == "0513" and html_text:
        match = re.search(
            r"(?is)<p[^>]*>\s*The Pastor:\s*</b>\s*(.*?)</p>",
            html_text,
        )
        if match:
            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if parsed_text:
                for idx, (element, character, dialogue) in enumerate(fixed_rows):
                    if element not in {"dialogue", "action"} or character:
                        continue
                    if normalize_spaces(dialogue) != "The Pastor:":
                        continue
                    fixed_rows[idx] = ("dialogue", "The Pastor", parsed_text)
                    break


    if episode_code == "0513":
        for csv_line_number in range(263, 267):
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, _character, text = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", "Frank Sr.", text)


    if episode_code == "0515" and html_text:
        match = re.search(
            r"(?is)<p[^>]*>\s*Party Guests:\s*</b>\s*(.*?)</p>",
            html_text,
        )
        if match:
            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if parsed_text:
                for idx, (element, character, dialogue) in enumerate(fixed_rows):
                    if element not in {"dialogue", "action"} or character:
                        continue
                    if normalize_spaces(dialogue) != "Party Guests:":
                        continue
                    fixed_rows[idx] = ("dialogue", "Party Guests", parsed_text)
                    break

    if episode_code == "0516" and html_text:
        malformed_cues = {
            "Chandler and Monica": r"(?is)<p[^>]*>\s*Chandler and\s*<b>\s*Monica:\s*</b>\s*(.*?)</p>",
            "Dream Monica": r"(?is)<p[^>]*>\s*Dream\s*<b>\s*Monica:\s*</b>\s*(.*?)</p>",
            "Dream Joey": r"(?is)<p[^>]*>\s*Dream\s*<b>\s*Joey:\s*</b>\s*(.*?)</p>",
        }

        for cue, pattern in malformed_cues.items():
            parsed_texts = []
            for match in re.finditer(pattern, html_text):
                text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
                if text:
                    parsed_texts.append(text)

            if not parsed_texts:
                continue

            target_indices = [
                idx
                for idx, (element, character, dialogue) in enumerate(fixed_rows)
                if element in {"dialogue", "action"}
                and not character
                and normalize_spaces(dialogue) == f"{cue}:"
            ]

            for idx, text in zip(target_indices, parsed_texts):
                fixed_rows[idx] = ("dialogue", cue, text)

    if episode_code == "0520" and html_text:
        malformed_cues = {
            "Joey and Ross": r"(?is)<p[^>]*>\s*Joey and\s*<b>\s*Ross:\s*</b>\s*(.*?)</p>",
            "Ross and Joey": r"(?is)<p[^>]*>\s*Ross and\s*<b>\s*Joey:\s*</b>\s*(.*?)</p>",
        }

        for cue, pattern in malformed_cues.items():
            match = re.search(pattern, html_text)
            if not match:
                continue

            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if not parsed_text:
                continue

            for idx, (element, character, dialogue) in enumerate(fixed_rows):
                if element not in {"dialogue", "action"} or character:
                    continue
                if normalize_spaces(dialogue) != f"{cue}:":
                    continue
                fixed_rows[idx] = ("dialogue", cue, parsed_text)
                break

    if episode_code == "0522" and html_text:
        malformed_cues = {
            "The Doctor": r"(?is)<p[^>]*>\s*The Doctor:\s*</b>\s*(.*?)</p>",
            "The Husband": r"(?is)<p[^>]*>\s*The Husband:\s*</b>\s*(.*?)</p>",
        }

        for cue, pattern in malformed_cues.items():
            parsed_texts = []
            for match in re.finditer(pattern, html_text):
                text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
                if text:
                    parsed_texts.append(text)

            if not parsed_texts:
                continue

            target_indices = [
                idx
                for idx, (element, character, dialogue) in enumerate(fixed_rows)
                if element in {"dialogue", "action"}
                and not character
                and normalize_spaces(dialogue) == f"{cue}:"
            ]

            for idx, text in zip(target_indices, parsed_texts):
                fixed_rows[idx] = ("dialogue", cue, text)

    if episode_code == "0523":
        blocked_dialogue_characters = {
            "part i written by",
            "part ii written by",
        }
        blocked_action_texts = {
            "transcribed by: eric aasen",
        }

        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                (row[0] == "dialogue" and row[1].lower() in blocked_dialogue_characters)
                or (row[0] == "action" and normalize_spaces(row[2]).lower() in blocked_action_texts)
            )
        ]

        match = re.search(
            r"(?is)<p[^>]*>\s*Ross and\s*<b>\s*Rachel:\s*</b>\s*</b>\s*(.*?)</p>",
            html_text,
        )
        if match:
            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if parsed_text:
                for idx, (element, character, dialogue) in enumerate(fixed_rows):
                    if element not in {"dialogue", "action"} or character:
                        continue
                    if normalize_spaces(dialogue) != "Ross and Rachel:":
                        continue
                    fixed_rows[idx] = ("dialogue", "Ross and Rachel", parsed_text)
                    break

        csv_line_number = 150
        row_index = csv_line_number - 2
        if 0 <= row_index < len(fixed_rows):
            _element, character, text = fixed_rows[row_index]
            fixed_rows[row_index] = ("singing", character, text)

    if episode_code == "0601" and html_text:
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                normalize_spaces(f"{row[1]}: {row[2]}" if row[1] else row[2])
                .lower()
                .replace("’", "'")
                .startswith("{transciber's note:")
            )
        ]

        malformed_cues = {
            "Chandler": r"(?is)<p[^>]*>\s*<b>\s*Chandler:\s*</b>\s*</b>\s*(.*?)</p>",
            "Monica": r"(?is)<p[^>]*>\s*<b>\s*Monica:\s*</b>\s*</b>\s*(.*?)</p>",
            "Ross": r"(?is)<p[^>]*>\s*<b>\s*Ross:\s*</b>\s*</b>\s*(.*?)</p>",
            "Rachel": r"(?is)<p[^>]*>\s*<b>\s*Rachel:\s*</b>\s*</b>\s*(.*?)</p>",
            "The Girls": r"(?is)<p[^>]*>\s*The Girls:\s*</b>\s*(.*?)</p>",
        }

        for cue, pattern in malformed_cues.items():
            parsed_texts = []
            for match in re.finditer(pattern, html_text):
                text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
                if text:
                    parsed_texts.append(text)

            if not parsed_texts:
                continue

            target_indices = [
                idx
                for idx, (element, character, dialogue) in enumerate(fixed_rows)
                if element in {"dialogue", "action"}
                and not character
                and normalize_spaces(dialogue) == f"{cue}:"
            ]

            for idx, text in zip(target_indices, parsed_texts):
                fixed_rows[idx] = ("dialogue", cue, text)

    if episode_code == "0602" and html_text:
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                normalize_spaces(f"{row[1]}: {row[2]}" if row[1] else row[2])
                .lower()
                .replace("’", "'")
                .startswith("{transciber's note:")
            )
        ]

    if episode_code == "0603":
        for csv_line_number in range(229, 235):
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, _character, dialogue = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", "Phoebe", dialogue)

    if episode_code == "0609":
        for idx, (element, character, dialogue) in enumerate(fixed_rows):
            if element != "dialogue":
                continue
            match = re.match(r"^(.+?)\s+(\[[^\]]+\])$", character)
            if not match:
                continue

            speaker = normalize_spaces(match.group(1))
            aside = normalize_spaces(match.group(2))
            if speaker != "Janine" or aside.lower() != "[to chandler]":
                continue

            fixed_rows[idx] = ("dialogue", speaker, f"{aside} {dialogue}")

    if episode_code in {"0615", "0616", "0615-0616"}:
        blocked_dialogue_characters = {
            "part i written by",
            "part ii written by",
            "parts i & ii transcribed by",
        }
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                row[0] == "dialogue"
                and (
                    normalize_spaces(row[1]).lower() in blocked_dialogue_characters
                    or normalize_spaces(row[1]).lower().replace("’", "'").startswith("{transcriber's note")
                )
            )
        ]

        malformed_cue_pattern = r"(?is)<p[^>]*>\s*<b>\s*Arthur:\s*</b>\s*</b>\s*(.*?)</p>"
        parsed_texts = []
        for match in re.finditer(malformed_cue_pattern, html_text):
            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if parsed_text:
                parsed_texts.append(parsed_text)

        if parsed_texts:
            target_indices = [
                idx
                for idx, (element, character, dialogue) in enumerate(fixed_rows)
                if element in {"dialogue", "action"}
                and not character
                and normalize_spaces(dialogue) == "Arthur:"
            ]
            for idx, text in zip(target_indices, parsed_texts):
                fixed_rows[idx] = ("dialogue", "Arthur", text)

        for csv_line_number in range(591, 596):
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, _character, dialogue = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", "Phoebe", dialogue)

    if episode_code == "0624":
        blocked_dialogue_characters = {
            "part i written by",
            "part ii written by",
            "parts i and ii transcribed by",
        }
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                row[0] == "dialogue"
                and (
                    normalize_spaces(row[1]).lower() in blocked_dialogue_characters
                    or normalize_spaces(row[1]).lower().replace("’", "'").startswith("{transcriber's note")
                )
            )
        ]

    if episode_code == "0621" and html_text:
        malformed_cues = {
            "Phoebe and Monica": r"(?is)<p[^>]*>\s*Phoebe and\s*<b>\s*Monica:\s*</b>\s*</b>\s*(.*?)</p>",
            "Monica and Phoebe": r"(?is)<p[^>]*>\s*Monica and\s*<b>\s*Phoebe:\s*</b>\s*</b>\s*(.*?)</p>",
        }

        for cue, pattern in malformed_cues.items():
            parsed_texts = []
            for match in re.finditer(pattern, html_text):
                parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
                if parsed_text:
                    parsed_texts.append(parsed_text)

            if not parsed_texts:
                continue

            target_indices = [
                idx
                for idx, (element, character, dialogue) in enumerate(fixed_rows)
                if element in {"dialogue", "action"}
                and not character
                and normalize_spaces(dialogue) == f"{cue}:"
            ]

            for idx, text in zip(target_indices, parsed_texts):
                fixed_rows[idx] = ("dialogue", cue, text)

    if episode_code == "0620":
        blocked_dialogue_characters = {
            "written by",
            "transcribed by",
            "with scenes taken from episodes transcribed by",
        }
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                row[0] == "dialogue"
                and normalize_spaces(row[1]).lower() in blocked_dialogue_characters
            )
        ]

        for csv_line_number in range(44, 48):
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, _character, dialogue = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", "Joey", dialogue)

    if episode_code == "0618" and html_text:
        malformed_cue_pattern = r"(?is)<p[^>]*>\s*Elizabeth:\s*</b>\s*(.*?)</p>"
        parsed_texts = []
        for match in re.finditer(malformed_cue_pattern, html_text):
            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if parsed_text:
                parsed_texts.append(parsed_text)

        if parsed_texts:
            target_indices = [
                idx
                for idx, (element, character, dialogue) in enumerate(fixed_rows)
                if element in {"dialogue", "action"}
                and not character
                and normalize_spaces(dialogue) == "Elizabeth:"
            ]
            for idx, text in zip(target_indices, parsed_texts):
                fixed_rows[idx] = ("dialogue", "Elizabeth", text)

    if episode_code == "0617":
        csv_line_number = 110
        row_index = csv_line_number - 2
        if 0 <= row_index < len(fixed_rows):
            element, character, dialogue = fixed_rows[row_index]
            if element == "dialogue" and character == "Joey":
                match = re.match(
                    r"^\(in his head\)\s*(.+?\?)\s*(\(.*\))$",
                    normalize_spaces(dialogue),
                )
                if match:
                    thought_text = match.group(1)
                    trailing_action = match.group(2)
                    fixed_rows[row_index] = (
                        element,
                        character,
                        f"<voiceover>{thought_text}<\\voiceover> {trailing_action}",
                    )

        malformed_cue_pattern = (
            r"(?is)<p[^>]*>\s*Janice(?:&#146;|’|')s Voice:\s*</b>\s*(.*?)</p>"
        )
        parsed_texts = []
        for match in re.finditer(malformed_cue_pattern, html_text):
            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if parsed_text:
                parsed_texts.append(parsed_text)

        if parsed_texts:
            target_indices = [
                idx
                for idx, (element, character, dialogue) in enumerate(fixed_rows)
                if element in {"dialogue", "action"}
                and not character
                and normalize_spaces(dialogue) in {"Janice’s Voice:", "Janice's Voice:"}
            ]
            for idx, text in zip(target_indices, parsed_texts):
                fixed_rows[idx] = ("dialogue", "Janice’s Voice", text)

        for csv_line_number in {253, 257}:
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, character, text = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", character, text)


    if episode_code == "0612" and html_text:
        match = re.search(r"(?is)<p[^>]*>\s*Joey:\s*</b>\s*(.*?)</p>", html_text)
        if match:
            parsed_text = normalize_spaces(BeautifulSoup(match.group(1), "html.parser").get_text(" "))
            if parsed_text:
                for idx, (element, character, dialogue) in enumerate(fixed_rows):
                    if element not in {"dialogue", "action"} or character:
                        continue
                    if normalize_spaces(dialogue) != "Joey:":
                        continue
                    fixed_rows[idx] = ("dialogue", "Joey", parsed_text)
                    break

    if episode_code == "0610":
        csv_line_number = 212
        row_index = csv_line_number - 2
        if 0 <= row_index < len(fixed_rows):
            element, character, dialogue = fixed_rows[row_index]
            if character in {"Joey’s Head", "Joey's Head"}:
                wrapped = dialogue
                if not (wrapped.startswith("<voiceover>") and wrapped.endswith("<\\voiceover>")):
                    wrapped = f"<voiceover>{wrapped}<\\voiceover>"
                fixed_rows[row_index] = (element, "Joey", wrapped)

    if episode_code == "0423":
        blocked_characters = {
            "part i written by",
            "part ii teleplay by",
            "part ii story by",
            "part i transcribed by",
            "part ii transcribed by",
        }
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                row[0] == "dialogue"
                and row[1].lower() in blocked_characters
            )
        ]

    if episode_code == "0422":
        for idx, (element, _character, dialogue) in enumerate(fixed_rows):
            if element != "action" or normalize_spaces(dialogue) not in {"All:", "The Guys:"}:
                continue

            next_text = ""
            if idx + 1 < len(fixed_rows):
                next_text = normalize_spaces(fixed_rows[idx + 1][2])

            if next_text.startswith("[Scene: Chandler and Joey's"):
                fixed_rows[idx] = (
                    "dialogue",
                    "All",
                    "I don’t have anything. (All of the rest of the women there hide their gifts behind their backs.)",
                )
            elif normalize_spaces(dialogue) == "The Guys:" and next_text.startswith("We know you took"):
                fixed_rows[idx] = ("dialogue", "The Guys", "Yeah!")

    if episode_code == "0421":
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                row[0] == "dialogue"
                and row[1].lower() in {"with help from", "episodes orginally transcribed by"}
            )
        ]

    if episode_code == "0419":
        for csv_line_number in {3, 5, 301, 305, 306}:
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, character, dialogue = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", character, dialogue)

        # W 0419 uszkodzony HTML rozbija "Both:" na osobny action bez tresci.
        for idx, (element, _character, dialogue) in enumerate(fixed_rows):
            if element != "action" or normalize_spaces(dialogue) != "Both:":
                continue

            next_text = ""
            if idx + 1 < len(fixed_rows):
                next_text = normalize_spaces(fixed_rows[idx + 1][2])

            if next_text.startswith("Hey! You’re back!"):
                fixed_rows[idx] = ("singing", "Joey and The Singing Man", "Sunshine is here! The sky is clear, the morning’s here!")
            elif next_text.startswith("I’ll see you tomorrow morning!"):
                fixed_rows[idx] = ("singing", "Joey and The Singing Man", "The dark of night has disappeared!!")

    if episode_code == "0417":
        # W 0417 uszkodzony HTML rozbija "Joey and Chandler:" na osobny action bez tresci.
        # Naprawiamy dwie kwestie z tej sceny.
        for idx, (element, character, dialogue) in enumerate(fixed_rows):
            if element != "action" or normalize_spaces(dialogue) != "Joey and Chandler:":
                continue

            next_text = ""
            if idx + 1 < len(fixed_rows):
                next_text = normalize_spaces(fixed_rows[idx + 1][2])

            if next_text == "We don’t know what could make this go away.":
                fixed_rows[idx] = ("dialogue", "Joey and Chandler", "(stopping her) Oh no-no-no-no!")
            elif next_text == "We still have porn.":
                fixed_rows[idx] = (
                    "dialogue",
                    "Joey and Chandler",
                    "Oh no-no-no! (Monica mutes the TV and they tentatively look behind them)",
                )

        for idx, (element, character, dialogue) in enumerate(fixed_rows):
            if element != "action" or normalize_spaces(dialogue) != "Ticket Agent:":
                continue

            next_text = ""
            if idx + 1 < len(fixed_rows):
                next_text = normalize_spaces(fixed_rows[idx + 1][2])

            if next_text.startswith("Emily! (Runs up.)"):
                fixed_rows[idx] = ("dialogue", "Ticket Agent", "(On the P.A.) This is the boarding call for Flight 009.")
            elif next_text.startswith("Well, that’ me."):
                fixed_rows[idx] = ("dialogue", "Ticket Agent", "This is the final boarding call for Flight 009.")

    if episode_code == "0412":
        for csv_line_number in range(285, 288):
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, _character, dialogue = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", "Phoebe", dialogue)

    if episode_code == "0410":
        for csv_line_number in range(246, 255):
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, _character, dialogue = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", "Phoebe", dialogue)

        csv_line_number = 255
        row_index = csv_line_number - 2
        if 0 <= row_index < len(fixed_rows):
            _element, _character, dialogue = fixed_rows[row_index]
            fixed_rows[row_index] = ("dialogue", "Phoebe", dialogue)

    if episode_code == "0407":
        csv_line_number = 29
        row_index = csv_line_number - 2
        if 0 <= row_index < len(fixed_rows):
            _element, character, dialogue = fixed_rows[row_index]
            fixed_rows[row_index] = ("singing", character, dialogue)

    if episode_code == "0405":
        csv_line_number = 43
        row_index = csv_line_number - 2
        if 0 <= row_index < len(fixed_rows):
            _element, character, dialogue = fixed_rows[row_index]
            fixed_rows[row_index] = ("singing", character, dialogue)

    if episode_code == "0404":
        for idx, (element, character, dialogue) in enumerate(fixed_rows):
            if element != "dialogue" or character != "Mr. Treeger":
                continue
            cleaned = re.sub(r'^\s*"\s*:\s*', '', dialogue)
            cleaned = re.sub(r'^\s*:\s*', '', cleaned)
            if cleaned != dialogue:
                fixed_rows[idx] = (element, character, cleaned)

    if episode_code == "0403":
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                row[0] == "dialogue"
                and row[1].lower() == "with help from"
                and row[2].lower() == "darcy partridge"
            )
        ]

    if episode_code == "0324":
        fixed_rows = [
            (element, "Robin Williams" if character == "Robin" else character, dialogue)
            for element, character, dialogue in fixed_rows
        ]

    if episode_code == "0323":
        csv_line_number = 253
        row_index = csv_line_number - 2
        if 0 <= row_index < len(fixed_rows):
            _element, character, dialogue = fixed_rows[row_index]
            fixed_rows[row_index] = ("singing", character, dialogue)

    if episode_code == "0316":
        for idx, (element, character, dialogue) in enumerate(fixed_rows):
            if normalize_spaces(dialogue) == "(Voice Over) Previously on Friends.":
                fixed_rows[idx] = (element, character, "<voiceover>Previously on Friends.<\\voiceover>")

            # W HTML jest to osobna linia akcji, ale parser potrafi rozbic ja
            # na character/text przez dwukropek w godzinie 8:30.
            if (
                element == "dialogue"
                and "Ross finds a clock" in character
                and "almost 8" in character
                and normalize_spaces(dialogue).startswith("30, and silently screams.)")
            ):
                fixed_rows[idx] = ("action", "", f"{character}:{dialogue}")

    if episode_code == "0122":
        opening_scene = "[Scene: Central Perk. Everyone is there.]"
        has_opening_scene = any(
            element == "heading" and "Central Perk. Everyone is there." in dialogue
            for element, _character, dialogue in fixed_rows
        )
        if not has_opening_scene:
            fixed_rows.insert(0, ("heading", "", opening_scene))

    if episode_code.startswith("02"):
        small_words = {"and", "or", "the", "of", "in", "on", "to", "a", "an"}
        action_markers = {
            "opening credits": "Opening Credits",
            "opening titles": "Opening Credits",
            "commercial break": "Commercial Break",
            "commercial": "Commercial Break",
            "closing credits": "Closing Credits",
            "closing titles": "Closing Credits",
            "credits": "Closing Credits",
            "end": "End",
        }

        for idx, (element, character, text) in enumerate(fixed_rows):
            if element == "dialogue" and character:
                letters_only = re.sub(r"[^A-Za-z]", "", character)
                if letters_only and letters_only.isupper():
                    titled = character.title()
                    parts = titled.split(" ")
                    for i in range(1, len(parts)):
                        if parts[i].lower() in small_words:
                            parts[i] = parts[i].lower()
                    fixed_rows[idx] = (element, " ".join(parts), text)

            if element == "action":
                normalized_text = normalize_spaces(text)
                canonical = action_markers.get(normalized_text.lower())
                if canonical:
                    fixed_rows[idx] = (element, character, canonical)
                    continue

            # W sezonie 02 poza headingami normalizujemy [..] -> (..),
            # rowniez gdy nawiasy kwadratowe sa tylko fragmentem tekstu.
            if element != "heading" and ("[" in text or "]" in text):
                current_element, current_character, _current_text = fixed_rows[idx]
                fixed_rows[idx] = (
                    current_element,
                    current_character,
                    text.replace("[", "(").replace("]", ")"),
                )

    if episode_code == "0205":
        for idx, (element, character, text) in enumerate(fixed_rows):
            if element != "heading":
                normalized_text = normalize_spaces(text)
                m = re.match(r"^\(\s*at\s+(.+?)\s*\)$", normalized_text, flags=re.IGNORECASE)
                if m:
                    scene_text = normalize_spaces(f"At {m.group(1)}")
                    fixed_rows[idx] = ("heading", "", f"[Scene: {scene_text}]")

    if episode_code == "0206":
        scene_like_actions = {
            "central perk",
            "chandler and joey are loaded down with baby stuff, and ben",
            "on the sidewalk outside central perk",
            "chez monica and rachel",
        }
        for idx, (element, _character, text) in enumerate(fixed_rows):
            if element == "heading":
                continue
            normalized_text = normalize_spaces(text)
            m = re.match(r"^\(\s*(.+?)\s*\)$", normalized_text)
            if not m:
                continue
            inner_text = normalize_spaces(m.group(1))
            if inner_text.lower() in scene_like_actions:
                fixed_rows[idx] = ("heading", "", f"[Scene: {inner_text}]")

    if episode_code == "0207":
        character_map = {
            "Chan": "Chandler",
            "Rach": "Rachel",
            "Phoe": "Phoebe",
            "Mnca": "Monica",
            "Mich": "Michael",
        }
        for idx, (element, character, text) in enumerate(fixed_rows):
            mapped_character = character_map.get(character)
            if mapped_character:
                fixed_rows[idx] = (element, mapped_character, text)

    if episode_code == "0208":
        character_map = {
            "Rach": "Rachel",
            "Mnca": "Monica",
            "Phoe": "Phoebe",
            "Chan": "Chandler",
            "Rtst": "Mr. Ratstatter",
            "Phoe/Mnca": "Phoebe and Monica",
            "Joey/Chan": "Joey and Chandler",
            "Chan, Joey, Ross": "Chandler and Joey and Ross",
        }
        for idx, (element, character, text) in enumerate(fixed_rows):
            mapped_character = character_map.get(character)
            if mapped_character:
                fixed_rows[idx] = (element, mapped_character, text)

        # Linia 56 w CSV to fragment piosenki Phoebe.
        singing_line_number = 56
        singing_row_index = singing_line_number - 2
        if 0 <= singing_row_index < len(fixed_rows):
            _element, character, text = fixed_rows[singing_row_index]
            fixed_rows[singing_row_index] = ("singing", character, text)

    if episode_code == "0209":
        for idx, (element, character, text) in enumerate(fixed_rows):
            if character == "CHANDLER, MONICA, and JOEY":
                fixed_rows[idx] = (element, "Chandler and Monica and Joey", text)

    if episode_code == "0210":
        opening_scene = "[Scene: The gang is walking to a newsstand late at night. Joey is anxiously in the lead.]"
        has_opening_scene = any(
            element == "heading" and normalize_spaces(dialogue) == normalize_spaces(opening_scene)
            for element, _character, dialogue in fixed_rows
        )
        if not has_opening_scene:
            fixed_rows.insert(0, ("heading", "", opening_scene))

        # W tym odcinku marker jest zapisany jako samo "Credits" na poczatku skryptu.
        has_opening_credits = any(
            element == "action" and normalize_spaces(dialogue).lower() == "opening credits"
            for element, _character, dialogue in fixed_rows
        )
        if not has_opening_credits:
            insert_at = next(
                (
                    idx
                    for idx, (element, _character, dialogue) in enumerate(fixed_rows)
                    if element == "heading"
                    and dialogue.startswith("[Scene: Chandler, Phoebe, Rachel, Monica comforting Joey")
                ),
                1 if fixed_rows else 0,
            )
            fixed_rows.insert(insert_at, ("action", "", "Opening Credits"))

        character_map = {
            "Rach": "Rachel",
            "Mnca": "Monica",
            "Phoe": "Phoebe",
            "Chan": "Chandler",
            "Fbob": "Fun Bobby",
            "Estl": "Estelle",
            "Russ": "Russ",
            "All": "All",
        }
        for idx, (element, character, text) in enumerate(fixed_rows):
            mapped_character = character_map.get(character)
            if mapped_character:
                fixed_rows[idx] = (element, mapped_character, text)

    if episode_code == "0211":
        scene_map = {
            "(at rachel and monica's)": "at Rachel and Monica's",
            "(at rachel and monica's": "at Rachel and Monica's",
            "(monica and rachel's)": "at Monica and Rachel's",
            "(central perk)": "at Central Perk",
            "(at the wedding)": "at the wedding",
            "(at the reception, monica and ross watch carol and susan getting their picture taken.)": "at the reception, Monica and Ross watch Carol and Susan getting their picture taken.",
            "(at monica and rachel's)": "at Monica and Rachel's",
        }

        for idx, (element, _character, text) in enumerate(fixed_rows):
            normalized_text = normalize_spaces(text)
            mapped_scene = scene_map.get(normalized_text.lower())
            if mapped_scene:
                fixed_rows[idx] = ("heading", "", f"[Scene: {mapped_scene}]")

        missing_opening_scene = "[Scene: at Ross's. Carol and Susan are picking Ben up]"
        has_missing_opening_scene = any(
            element == "heading" and normalize_spaces(dialogue) == normalize_spaces(missing_opening_scene)
            for element, _character, dialogue in fixed_rows
        )
        if not has_missing_opening_scene:
            fixed_rows.insert(0, ("heading", "", missing_opening_scene))

    if episode_code == "0217":
        singing_lines = {106, 110, 258, 259, 261, 263, 265, 267}
        for csv_line_number in singing_lines:
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, character, text = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", character, text)

    if episode_code == "0220":
        for idx, (element, character, text) in enumerate(fixed_rows):
            if character == "CAROL and SUSAN":
                fixed_rows[idx] = (element, "Carol and Susan", text)

    if episode_code == "0221":
        for idx, (element, character, text) in enumerate(fixed_rows):
            if character == "ROSS and CHANDLER":
                fixed_rows[idx] = (element, "Ross and Chandler", text)

    if episode_code == "0223":
        for idx, (element, character, text) in enumerate(fixed_rows):
            if character == "PHOEBE and RYAN":
                fixed_rows[idx] = (element, "Phoebe and Ryan", text)

    if episode_code in {"0212", "0213", "0212-0213"}:
        singing_lines = {
            100, 101, 102, 103, 104, 105, 106,
            135, 136, 137, 138, 139, 140,
            186, 187, 188, 189, 190,
            253, 254, 255, 256, 257,
        }
        for csv_line_number in singing_lines:
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, _character, text = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", "Phoebe", text)

    if episode_code in {"0212", "0213", "0212-0213"}:
        for idx, (element, character, text) in enumerate(list(fixed_rows)):
            if element != "dialogue" or character != "Phoebe":
                continue
            normalized_text = normalize_spaces(text)
            if "About 20 minutes." in normalized_text and "CLOSING CREDITS" in normalized_text.upper():
                cleaned_text = re.sub(r"\s*CLOSING\s+CREDITS\s*$", "", normalized_text, flags=re.IGNORECASE)
                fixed_rows[idx] = ("dialogue", "Phoebe", cleaned_text)

                next_is_closing = (
                    idx + 1 < len(fixed_rows)
                    and fixed_rows[idx + 1][0] == "action"
                    and normalize_spaces(fixed_rows[idx + 1][2]).lower() == "closing credits"
                )
                if not next_is_closing:
                    fixed_rows.insert(idx + 1, ("action", "", "Closing Credits"))
                break

    if episode_code == "0123":
        singing_lines = {80, 81, 82, 83, 84}
        for csv_line_number in singing_lines:
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, _character, text = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", "Phoebe", text)

    if episode_code == "0101":
        opening_scene = "[Scene: Central Perk, Chandler, Joey, Phoebe, and Monica are there.]"
        has_opening_scene = any(
            element == "heading" and dialogue == opening_scene
            for element, _character, dialogue in fixed_rows
        )
        if not has_opening_scene:
            fixed_rows.insert(0, ("heading", "", opening_scene))

        singing_lines = {10, 11, 12, 13}
        for csv_line_number in singing_lines:
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, character, text = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", character, text)

        # W zrodle HTML dwie kwestie sa sklejone w jednej linii Moniki.
        for idx, (element, character, text) in enumerate(list(fixed_rows)):
            if (
                element == "dialogue"
                and character == "Monica"
                and text == "Maybe. Joey: Wait. Your 'not a real date' tonight is with Paul the Wine Guy?"
            ):
                fixed_rows[idx] = ("dialogue", "Monica", "Maybe.")
                fixed_rows.insert(
                    idx + 1,
                    (
                        "dialogue",
                        "Joey",
                        "Wait. Your 'not a real date' tonight is with Paul the Wine Guy?",
                    ),
                )
                break

        # Linia 127 w CSV 0101 to spiew Phoebe (nie zwykly dialogue).
        singing_line_number = 127
        singing_row_index = singing_line_number - 2
        if 0 <= singing_row_index < len(fixed_rows):
            _element, character, text = fixed_rows[singing_row_index]
            fixed_rows[singing_row_index] = ("singing", character, text)

    if episode_code == "0103":
        opening_scene = "[Scene: Central Perk, everyone but Phoebe is there.]"
        has_opening_scene = any(
            element == "heading" and dialogue == opening_scene
            for element, _character, dialogue in fixed_rows
        )
        if not has_opening_scene:
            fixed_rows.insert(0, ("heading", "", opening_scene))

    if episode_code == "0104":
        opening_scene = "[Scene: Central Perk, everyone is there except Joey.]"
        has_opening_scene = any(
            element == "heading" and dialogue == opening_scene
            for element, _character, dialogue in fixed_rows
        )
        if not has_opening_scene:
            fixed_rows.insert(0, ("heading", "", opening_scene))

    if episode_code == "0105":
        has_opening_credits = any(
            element == "action" and dialogue == "Opening Credits"
            for element, _character, dialogue in fixed_rows
        )
        if not has_opening_credits:
            insert_at = None
            for idx, (element, _character, dialogue) in enumerate(fixed_rows):
                if element == "heading" and dialogue == "[Scene: Central Perk, all are there.]":
                    insert_at = idx
                    break
            if insert_at is None:
                insert_at = len(fixed_rows)
            fixed_rows.insert(insert_at, ("action", "", "Opening Credits"))

        has_commercial_break = any(
            element == "action" and dialogue == "Commercial Break"
            for element, _character, dialogue in fixed_rows
        )
        if not has_commercial_break:
            insert_at = None
            for idx, (element, _character, dialogue) in enumerate(fixed_rows):
                if element == "heading" and dialogue == "[Scene: Fancy restaurant, Joey and Bob are talking.]":
                    insert_at = idx
                    break
            if insert_at is None:
                insert_at = len(fixed_rows)
            fixed_rows.insert(insert_at, ("action", "", "Commercial Break"))

    if episode_code == "0106":
        opening_scene = "[Scene: A Theater, the gang is in the audience wating for a play of Joey's to start.]"
        has_opening_scene = any(
            element == "heading" and dialogue == opening_scene
            for element, _character, dialogue in fixed_rows
        )
        if not has_opening_scene:
            fixed_rows.insert(0, ("heading", "", opening_scene))

        singing_lines = {10, 11, 12, 13}
        for csv_line_number in singing_lines:
            row_index = csv_line_number - 2
            if 0 <= row_index < len(fixed_rows):
                _element, _character, text = fixed_rows[row_index]
                fixed_rows[row_index] = ("singing", "Joey", text)

    if episode_code == "0108":
        opening_scene = "[Scene: Chandler's Office, Chandler is on a coffee break. Shelley enters.]"
        has_opening_scene = any(
            element == "heading" and dialogue == opening_scene
            for element, _character, dialogue in fixed_rows
        )
        if not has_opening_scene:
            fixed_rows.insert(0, ("heading", "", opening_scene))

        # Usuwamy błędnie zmapowaną linię metadanych jako dialog.
        fixed_rows = [
            row
            for row in fixed_rows
            if not (
                row[0] == "dialogue"
                and row[1] == "With Help From"
                and normalize_spaces(row[2]) == "Rachel Stigge"
            )
        ]

        # Dopisujemy pierwszą kwestię Shelley, która bywa gubiona przez uszkodzony HTML.
        shelley_line = "Hey gorgeous, how's it going?"
        has_shelley_line = any(
            element == "dialogue" and character == "Shelley" and normalize_spaces(dialogue) == shelley_line
            for element, character, dialogue in fixed_rows
        )
        if not has_shelley_line:
            heading_idx = next(
                (
                    idx
                    for idx, (element, _character, dialogue) in enumerate(fixed_rows)
                    if element == "heading" and dialogue == opening_scene
                ),
                -1,
            )
            insert_at = heading_idx + 1 if heading_idx >= 0 else 0
            fixed_rows.insert(insert_at, ("dialogue", "Shelley", shelley_line))

    if episode_code == "0109":
        # W tym miejscu scena końcowa bywa rozbijana na 1 heading + 5 linii action.
        for idx in range(len(fixed_rows) - 5):
            first = fixed_rows[idx]
            chunk = fixed_rows[idx + 1 : idx + 6]
            if not (first[0] == "heading" and first[2].startswith("[Scene: The Subway, Joey sees his poster")):
                continue

            expected_prefixes = [
                "Bladder Control Problem",
                "Stop Wife Beating",
                "Hemorrhoids?",
                "Winner of 3 Tony Awards...",
                "He's finally happy with that and walks away.]",
            ]
            merged_suffixes = [
                "Bladder Control Problem,",
                "Stop Wife Beating,",
                "Hemorrhoids?,",
                "Winner of 3 Tony Awards...",
                "He's finally happy with that and walks away.]",
            ]
            if not all(chunk[i][0] == "action" and normalize_spaces(chunk[i][2]) == expected_prefixes[i] for i in range(5)):
                continue

            merged_text = normalize_spaces(
                " ".join(
                    [first[2], merged_suffixes[0], merged_suffixes[1], merged_suffixes[2], merged_suffixes[3], merged_suffixes[4]]
                )
            )
            fixed_rows[idx] = ("heading", "", merged_text)
            del fixed_rows[idx + 1 : idx + 6]
            break

    return fixed_rows


def ensure_missing_marker_actions(
    rows: Sequence[Tuple[str, str, str]], html_text: str
) -> List[Tuple[str, str, str]]:
    """Dopisuje brakujace akcje credits/break/end, gdy sa widoczne w HTML."""
    fixed_rows = list(rows)

    marker_patterns = {
        "Opening Credits": r"(?is)(?:\[\s*opening\s+credits\s*\]|>\s*opening\s+credits\s*<)",
        "Commercial Break": r"(?is)(?:\[\s*commercial\s+break\s*\]|>\s*commercial\s+break\s*<)",
        "Closing Credits": r"(?is)(?:\[\s*closing\s+credits\s*\]|>\s*closing\s+credits\s*<)",
        "End": r"(?is)(?:\[\s*end\s*\]|>\s*end\s*<)",
    }

    def has_action(label: str) -> bool:
        return any(
            element == "action" and normalize_spaces(text).lower() == label.lower()
            for element, _character, text in fixed_rows
        )

    for label in ["Opening Credits", "Commercial Break", "Closing Credits", "End"]:
        if not re.search(marker_patterns[label], html_text):
            continue
        if has_action(label):
            continue

        if label == "Opening Credits":
            insert_at = 1 if fixed_rows else 0
        elif label == "Commercial Break":
            insert_at = max(1, len(fixed_rows) // 2)
        elif label == "Closing Credits":
            end_idx = next(
                (
                    idx
                    for idx, (element, _character, text) in enumerate(fixed_rows)
                    if element == "action" and normalize_spaces(text).lower() == "end"
                ),
                len(fixed_rows),
            )
            insert_at = end_idx
        else:
            insert_at = len(fixed_rows)

        fixed_rows.insert(insert_at, ("action", "", label))

    return fixed_rows


def mark_internal_thoughts_with_italics(
    rows: Sequence[Tuple[str, str, str]], html_text: str, episode_code: str
) -> List[Tuple[str, str, str]]:
    """Oznacza tylko te fragmenty, ktore byly kursywa w HTML i sa mysla bohatera."""
    fixed_rows = list(rows)

    soup = BeautifulSoup(html_text, "html.parser")
    body = soup.body or soup

    # Mapujemy tekst wiersza CSV -> listy fragmentow italic z oryginalnego HTML.
    italic_chunks_by_text: dict[str, list[str]] = {}
    chandler_0107_italic_chunks: List[str] = []
    for paragraph in body.find_all("p"):
        if paragraph.find("p") is not None:
            continue

        paragraph_text = normalize_spaces(paragraph.get_text(" "))
        if not paragraph_text:
            continue

        italic_chunks: List[str] = []
        for tag in paragraph.find_all(["i", "em"]):
            chunk = normalize_spaces(tag.get_text(" "))
            if chunk:
                italic_chunks.append(chunk)

        if not italic_chunks:
            continue

        dialogue = parse_dialogue_line(paragraph_text)
        is_thought_cue = THOUGHT_CUE_PATTERN.search(paragraph_text) is not None
        is_0107_chandler_italic = (
            episode_code == "0107"
            and dialogue is not None
            and dialogue[0].strip().lower() == "chandler"
        )
        if not is_thought_cue and not is_0107_chandler_italic:
            continue

        candidate_texts: List[str] = [paragraph_text]
        if dialogue is not None:
            speaker, spoken = dialogue
            candidate_texts.append(spoken)
            if episode_code == "0107" and speaker.strip().lower() == "chandler":
                for chunk in italic_chunks:
                    if chunk not in chandler_0107_italic_chunks:
                        chandler_0107_italic_chunks.append(chunk)

        for candidate in candidate_texts:
            bucket = italic_chunks_by_text.setdefault(candidate, [])
            for chunk in italic_chunks:
                if chunk not in bucket:
                    bucket.append(chunk)

    for idx, (element, character, text_value) in enumerate(fixed_rows):
        if element not in {"dialogue", "action", "singing"}:
            continue
        is_thought_cue_row = THOUGHT_CUE_PATTERN.search(text_value) is not None
        is_0107_chandler_row = episode_code == "0107" and character.strip().lower() == "chandler"
        if not is_thought_cue_row and not is_0107_chandler_row:
            continue

        chunks = italic_chunks_by_text.get(text_value)
        if not chunks:
            for candidate_text, candidate_chunks in italic_chunks_by_text.items():
                if candidate_text in text_value or text_value in candidate_text:
                    chunks = candidate_chunks
                    break
        if not chunks and is_0107_chandler_row:
            matched_chunks = [chunk for chunk in chandler_0107_italic_chunks if chunk in text_value]
            if matched_chunks:
                chunks = matched_chunks
        if not chunks:
            continue

        updated = text_value
        for chunk in sorted(chunks, key=len, reverse=True):
            if f"<voiceover>{chunk}</voiceover>" in updated:
                continue
            if chunk in updated:
                updated = updated.replace(chunk, f"<voiceover>{chunk}</voiceover>", 1)

        fixed_rows[idx] = (element, character, updated)

    return fixed_rows


def write_episode_csv(output_file: Path, rows: Sequence[Tuple[str, str, str]]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["script_element", "character", "text"])
        writer.writerows(rows)


def season_and_episode_from_code(episode_code: str) -> Tuple[str, str]:
    if re.fullmatch(r"\d{4}", episode_code):
        return episode_code[:2], episode_code[2:]

    range_match = re.fullmatch(r"(\d{2})(\d{2})-(\d{2})(\d{2})", episode_code)
    if range_match and range_match.group(1) == range_match.group(3):
        return range_match.group(1), f"{range_match.group(2)}-{range_match.group(4)}"

    outtakes_match = re.fullmatch(r"(\d{2})outtakes", episode_code, flags=re.IGNORECASE)
    if outtakes_match:
        return outtakes_match.group(1), "outtakes"

    return "", episode_code


def write_all_csv(output_file: Path, rows: Sequence[Tuple[str, str, str, str, str]]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["season", "episode", "script_element", "character", "text"])
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generuje CSV per odcinek z HTML transkryptow Friends."
    )
    parser.add_argument(
        "--input-dir",
        default="scripts_people",
        help="Katalog z plikami HTML (domyslnie: scripts_people)",
    )
    parser.add_argument(
        "--output-dir",
        default="scripts_people_csv",
        help="Katalog wyjsciowy CSV (domyslnie: scripts_people_csv)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if not input_dir.exists():
        raise SystemExit(f"Input directory not found: {input_dir}")

    html_files = sorted(input_dir.glob("*/*.html"))
    if not html_files:
        raise SystemExit(f"No HTML files found under: {input_dir}")

    generated = 0
    skipped = 0
    all_rows: List[Tuple[str, str, str, str, str]] = []

    for html_file in html_files:
        season = html_file.parent.name
        stem = html_file.stem

        episode_codes = episode_codes_from_stem(stem, season)
        if not episode_codes:
            skipped += 1
            print(f"SKIP (no episode code): {html_file}")
            continue

        html_text = html_file.read_text(encoding="latin-1", errors="ignore")
        rows_bs4 = list(iter_script_rows(html_text))
        rows_raw = list(iter_script_rows_from_raw_blocks(html_text))

        # Preferujemy parser BS4, ale przy uszkodzonym HTML potrafi on zwrocic
        # bardzo slaba jakosc (np. "postacie" jako action zamiast dialogue).
        # Wtedy bierzemy fallback raw-blocks.
        should_use_raw = False
        bs4_dialogue_count = sum(1 for script_element, _, _ in rows_bs4 if script_element == "dialogue")
        raw_dialogue_count = sum(1 for script_element, _, _ in rows_raw if script_element == "dialogue")
        if "0116" in episode_codes or "0423" in episode_codes or "0510" in episode_codes:
            should_use_raw = True
        elif rows_raw and len(rows_raw) >= 20 and len(rows_bs4) <= max(5, len(rows_raw) // 4):
            should_use_raw = True
        elif rows_raw and raw_dialogue_count >= 20 and bs4_dialogue_count <= max(5, raw_dialogue_count // 3):
            should_use_raw = True

        rows = rows_raw if should_use_raw else rows_bs4

        for episode_code in episode_codes:
            episode_rows = apply_episode_specific_fixes(episode_code, rows, html_text)
            episode_rows = mark_internal_thoughts_with_italics(episode_rows, html_text, episode_code)
            episode_rows = ensure_missing_marker_actions(episode_rows, html_text)
            output_file = output_dir / output_filename_for_episode_code(episode_code)
            write_episode_csv(output_file, episode_rows)
            if episode_code == "0212-0213":
                for legacy_name in ("0212.csv", "0213.csv"):
                    legacy_file = output_dir / legacy_name
                    if legacy_file.exists():
                        legacy_file.unlink()
            if episode_code == "0615-0616":
                for legacy_name in ("0615.csv", "0616.csv"):
                    legacy_file = output_dir / legacy_name
                    if legacy_file.exists():
                        legacy_file.unlink()

            season_value, episode_value = season_and_episode_from_code(episode_code)
            for script_element, character, dialogue in episode_rows:
                all_rows.append((season_value, episode_value, script_element, character, dialogue))

            generated += 1
            print(f"OK: {html_file} -> {output_file}")

    all_csv_path = output_dir / "all.csv"
    write_all_csv(all_csv_path, all_rows)

    print(f"Generated CSV files: {generated}")
    print(f"Skipped source files: {skipped}")
    print(f"Generated all-episodes CSV: {all_csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
