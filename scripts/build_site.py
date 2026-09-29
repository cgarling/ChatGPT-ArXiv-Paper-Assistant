#!/usr/bin/env python3
"""Build the dependency-free archive site from dated JSON reports."""

import argparse
import json
import re
import shutil
import sys
import unicodedata
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arxiv_assistant.utils.utils import latex_text_to_unicode

DATE_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})-output\.json$")
SITE_FILES = ("index.html", "app.js", "styles.css")
AFFILIATION_WORDS = re.compile(r"\b(collaboration|consortium|university|universidad|université|universität|institute|instituto|institut|institution|observatory|observatorio|laboratory|laboratorio|lab|centre|center|department|departamento|college|academy|agency|agencies|survey|project|team)\b", re.IGNORECASE)
PERSONAL_ANNOTATION = re.compile(r"^(?:jr\.?|sr\.?|i{2,4}|[a-z]|\d+)$|\b(?:orcid|corresponding author|co-?first author|equal contribution|contributed equally|deceased|present address|personal title|on behalf of)\b", re.IGNORECASE)
INSTITUTIONAL_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
AUTHOR_SUFFIX = re.compile(r"^(.*?)\s+\(([^()]*)\)\s*$")


class BuildError(ValueError):
    pass


def discover_reports(source: Path) -> list[tuple[str, Path]]:
    if not source.is_dir():
        raise BuildError(f"JSON source directory does not exist: {source}")

    reports: dict[str, Path] = {}
    for path in source.rglob("*.json"):
        match = DATE_FILE.fullmatch(path.name)
        if not match:
            raise BuildError(f"unexpected JSON filename (expected YYYY-MM-DD-output.json): {path}")
        report_date = match.group(1)
        try:
            date.fromisoformat(report_date)
        except ValueError as error:
            raise BuildError(f"invalid report date in filename: {path}") from error
        if report_date in reports:
            raise BuildError(f"duplicate report date {report_date}: {reports[report_date]} and {path}")
        reports[report_date] = path

    if not reports:
        raise BuildError(f"no dated JSON reports found in {source}")
    return sorted(reports.items(), reverse=True)


def validate_report(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as stream:
            report = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError(f"cannot read valid JSON from {path}: {error}") from error

    if not isinstance(report, dict):
        raise BuildError(f"report must be a JSON object keyed by arXiv ID: {path}")
    for arxiv_id, paper in report.items():
        if not isinstance(arxiv_id, str) or not arxiv_id:
            raise BuildError(f"report contains an invalid arXiv ID key: {path}")
        if not isinstance(paper, dict):
            raise BuildError(f"paper {arxiv_id!r} must be a JSON object: {path}")
        for field in ("title", "abstract"):
            if not isinstance(paper.get(field), str):
                raise BuildError(f"paper {arxiv_id!r} has no string {field!r}: {path}")
        authors = paper.get("authors")
        if not isinstance(authors, list) or not all(isinstance(author, str) for author in authors):
            raise BuildError(f"paper {arxiv_id!r} has invalid 'authors' (expected strings): {path}")
        for field in ("RELEVANCE", "NOVELTY", "SCORE"):
            if field in paper and (isinstance(paper[field], bool) or not isinstance(paper[field], (int, float))):
                raise BuildError(f"paper {arxiv_id!r} has non-numeric {field!r}: {path}")
    return report


def repair_fragmented_authors(authors: list[str]) -> list[str]:
    repaired, fragments, depth = [], [], 0
    for author in authors:
        fragments.append(author)
        depth += author.count("(") - author.count(")")
        if depth <= 0:
            repaired.append(", ".join(fragments))
            fragments = []
            depth = 0
    if fragments:
        repaired.extend(fragments)
    return repaired


def contains_non_latin_letter(value: str) -> bool:
    return any(character.isalpha() and "LATIN" not in unicodedata.name(character, "") for character in value)


def normalize_authors(authors: list[str]) -> tuple[list[str], list[str], list[list[int]]]:
    parsed = []
    authors = repair_fragmented_authors(authors)
    counts = {}
    for author in authors:
        author = latex_text_to_unicode(author)
        match = AUTHOR_SUFFIX.fullmatch(author)
        suffix = match.group(2).strip() if match else None
        parsed.append((match.group(1).strip(), suffix) if match else (author, None))
        if suffix:
            counts[suffix.casefold()] = counts.get(suffix.casefold(), 0) + 1

    affiliations = []
    affiliation_numbers = {}
    cleaned = []
    references = []
    for author, suffix in parsed:
        acronym = suffix and INSTITUTIONAL_ACRONYM.search(suffix)
        native_name = suffix and "," not in suffix and not suffix.isupper() and (contains_non_latin_letter(author) or contains_non_latin_letter(suffix))
        short_alias = suffix and suffix.istitle() and len(suffix.split()) <= 3 and counts[suffix.casefold()] <= 2 and not AFFILIATION_WORDS.search(suffix)
        personal = suffix and (PERSONAL_ANNOTATION.search(suffix) or native_name or short_alias)
        extract = suffix and not personal and ("," in suffix or counts[suffix.casefold()] >= 2 or AFFILIATION_WORDS.search(suffix) or acronym and (suffix.isupper() or len(suffix) >= 20))
        cleaned.append(author if extract else f"{author} ({suffix})" if suffix else author)
        if extract:
            key = suffix.casefold()
            if key not in affiliation_numbers:
                affiliations.append(suffix)
                affiliation_numbers[key] = len(affiliations)
            references.append([affiliation_numbers[key]])
        else:
            references.append([])
    return cleaned, affiliations, references


def normalize_report_text(report: dict) -> dict:
    for paper in report.values():
        paper["title"] = latex_text_to_unicode(paper["title"])
        paper["authors"], paper["affiliations"], paper["author_affiliations"] = normalize_authors(paper["authors"])
    return report


def build(source: Path, output: Path, site: Path) -> dict:
    reports = discover_reports(source)
    missing = [name for name in SITE_FILES if not (site / name).is_file()]
    if missing:
        raise BuildError(f"missing site asset(s) in {site}: {', '.join(missing)}")

    validated = [(report_date, path, normalize_report_text(validate_report(path))) for report_date, path in reports]
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for name in SITE_FILES:
        shutil.copy2(site / name, output / name)

    entries = []
    for report_date, _source_path, report in validated:
        relative_path = Path("data") / report_date[:7] / f"{report_date}.json"
        destination = output / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        entries.append({"date": report_date, "path": relative_path.as_posix()})

    manifest = {"latest": entries[0]["date"], "dates": entries}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("out/json"), help="directory containing dated report JSON")
    parser.add_argument("--output", type=Path, default=Path("dist"), help="clean build output directory")
    parser.add_argument("--site", type=Path, default=Path("site"), help="directory containing static app assets")
    args = parser.parse_args()
    try:
        manifest = build(args.source, args.output, args.site)
    except BuildError as error:
        parser.error(str(error))
    print(f"Built {len(manifest['dates'])} reports; latest is {manifest['latest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
