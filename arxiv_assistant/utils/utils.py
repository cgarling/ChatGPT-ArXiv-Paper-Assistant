import dataclasses
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union


class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return super().default(o)


@dataclass
class Paper:
    # paper class should track the list of authors, paper title, abstract, arxiv id
    authors: List[str]
    title: str
    abstract: str
    arxiv_id: str

    # add a hash function using arxiv_id
    def __hash__(self):
        return hash(self.arxiv_id)


def is_earlier(ts1, ts2):
    # compares two arxiv ids, returns true if ts1 is older than ts2
    return int(ts1.replace(".", "")) < int(ts2.replace(".", ""))


def batched(items, batch_size):
    # takes a list and returns a list of list with batch_size
    return [items[i: i + batch_size] for i in range(0, len(items), batch_size)]


def normalize_whitespace(string):
    """Replace multiple whitespaces with a single space."""
    return re.sub(r'\s+', ' ', string).strip()

_LATEX_ACCENTS = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
    "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b",
    "c": "\u0327", "k": "\u0328", "r": "\u030a",
}
_LATEX_LETTERS = {r"\L": "Ł", r"\l": "ł", r"\O": "Ø", r"\o": "ø", r"\AA": "Å", r"\aa": "å", r"\AE": "Æ", r"\ae": "æ", r"\OE": "Œ", r"\oe": "œ", r"\ss": "ß"}
_LATEX_ACCENT_PATTERN = re.compile(r"\\(?:([uvHckr])\s*\{([^{}])\}|(['`^\"~=\.])\s*(?:\{([^{}])\}|([A-Za-z])))")

def split_outside_parentheses(string):
    """Split comma-separated text while preserving commas inside parentheses."""
    parts, start, depth = [], 0, 0
    for index, character in enumerate(string):
        if character == "(":
            depth += 1
        elif character == ")" and depth:
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(string[start:index].strip())
            start = index + 1
    parts.append(string[start:].strip())
    return [part for part in parts if part]


def latex_text_to_unicode(string):
    """Convert common LaTeX text accents and letters, preserving unknown commands."""
    def replace_accent(match):
        accent = match.group(1) or match.group(3)
        character = match.group(2) or match.group(4) or match.group(5)
        return unicodedata.normalize("NFC", character + _LATEX_ACCENTS[accent])

    string = _LATEX_ACCENT_PATTERN.sub(replace_accent, string)
    for command, character in sorted(_LATEX_LETTERS.items(), key=lambda item: len(item[0]), reverse=True):
        string = re.sub(re.escape(command) + r"(?:\{\}|(?=\s|$|[^A-Za-z]))", character, string)
    return unicodedata.normalize("NFC", string)

def align_markdown_table(table_string: str, alignments: Union[Optional[str], List[Optional[str]], Tuple[Optional[str]]] = None) -> str:
    """
    Set the alignment of a markdown table.
    :param table_string: The markdown table string to align.
    :param alignments: The alignment directions. Can be "left", "center", or "right". (None denotes no change)
                       If `alignments` is a list, it should be the same length as the number of columns in the table.
    :return: The aligned markdown table string.
    """
    lines = table_string.split("\n")
    format_line = lines[1]  # "|:-----:|-----|:-----|-----:|"
    format_contents = format_line.split("|")[1:-1]  # [":-----:", "-----", ":-----", "-----:"]
    num_columns = format_line.count("|") - 1

    if not isinstance(alignments, (tuple, list)):
        alignments = [alignments] * num_columns

    new_format_contents = []
    for i in range(num_columns):
        if alignments[i] is None:
            new_format_contents.append(format_contents[i])
        elif alignments[i] == "left":
            new_format_contents.append(":" + "-" * (len(format_contents[i]) - 1))
        elif alignments[i] in ("center", "centre"):
            new_format_contents.append(":" + "-" * (len(format_contents[i]) - 2) + ":")
        elif alignments[i] == "right":
            new_format_contents.append("-" * (len(format_contents[i]) - 1) + ":")
        else:
            raise ValueError(f"Invalid alignment: {alignments[i]}")
    new_format_line = "|".join([""] + new_format_contents + [""])
    new_lines = [lines[0], new_format_line] + lines[2:]
    new_table_string = "\n".join(new_lines)

    return new_table_string
