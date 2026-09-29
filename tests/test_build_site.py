import json
import shutil
import unittest
from pathlib import Path

from scripts.build_site import BuildError, build, discover_reports, normalize_authors, repair_fragmented_authors


class BuildSiteTests(unittest.TestCase):
    scratch = Path("tests/.scratch-site")

    def setUp(self):
        shutil.rmtree(self.scratch, ignore_errors=True)
        self.source = self.scratch / "out" / "json"
        self.site = self.scratch / "site"
        self.output = self.scratch / "dist"
        self.source.mkdir(parents=True)
        self.site.mkdir()
        for name in ("index.html", "app.js", "styles.css"):
            (self.site / name).write_text(name, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.scratch, ignore_errors=True)

    def write_report(self, report_date, report=None):
        path = self.source / report_date[:7] / f"{report_date}-output.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({} if report is None else report), encoding="utf-8")
        return path

    def test_discovery_and_manifest_are_newest_first(self):
        old = self.write_report("2025-01-31")
        new = self.write_report("2025-02-03")
        self.assertEqual(discover_reports(self.source), [("2025-02-03", new), ("2025-01-31", old)])
        manifest = build(self.source, self.output, self.site)
        self.assertEqual(manifest["latest"], "2025-02-03")
        self.assertEqual(manifest["dates"], [
            {"date": "2025-02-03", "path": "data/2025-02/2025-02-03.json"},
            {"date": "2025-01-31", "path": "data/2025-01/2025-01-31.json"},
        ])
        copied = json.loads((self.output / "data/2025-02/2025-02-03.json").read_text(encoding="utf-8"))
        self.assertEqual(copied, {})

    def test_build_normalizes_text_without_changing_source(self):
        source = {"2502.00001": {
            "title": r"A paper by \v{Z}eljko",
            "abstract": r"Math $M_V=-2.7$ stays unchanged.",
            "authors": [r"\v{Z}eljko Ivezi\'c"],
        }}
        path = self.write_report("2025-02-03", source)
        manifest = build(self.source, self.output, self.site)
        built = json.loads((self.output / manifest["dates"][0]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(built["2502.00001"]["title"], "A paper by Željko")
        self.assertEqual(built["2502.00001"]["authors"], ["Željko Ivezić"])
        self.assertEqual(built["2502.00001"]["abstract"], r"Math $M_V=-2.7$ stays unchanged.")
        built_text = (self.output / manifest["dates"][0]["path"]).read_text(encoding="utf-8")
        self.assertNotIn("\n  ", built_text)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), source)

    def test_fragmented_historical_affiliations_are_repaired(self):
        fragmented = [
            "Joel C. Roediger (Canadian Space Agency", "Saint-Hubert", "QC", "Canada)",
            "Nitya Kallivayalil (Department of Astronomy", "University of Virginia", "USA)",
        ]
        self.assertEqual(repair_fragmented_authors(fragmented), [
            "Joel C. Roediger (Canadian Space Agency, Saint-Hubert, QC, Canada)",
            "Nitya Kallivayalil (Department of Astronomy, University of Virginia, USA)",
        ])
        authors, affiliations, references = normalize_authors(fragmented)
        self.assertEqual(authors, ["Joel C. Roediger", "Nitya Kallivayalil"])
        self.assertEqual(affiliations, [
            "Canadian Space Agency, Saint-Hubert, QC, Canada",
            "Department of Astronomy, University of Virginia, USA",
        ])
        self.assertEqual(references, [[1], [2]])

    def test_affiliations_are_extracted_conservatively(self):
        authors, affiliations, references = normalize_authors([
            "Ting Li (the S5 Collaboration)",
            "Denis Erkal (the S5 Collaboration)",
            "Jane Doe (Jr.)",
            "John Smith (University of Example)",
            "Alex Roe (Private note)",
            "Bea Poe (Private note)",
            "Joel Roediger (Canadian Space Agency, Saint-Hubert, QC, Canada)",
            "Wei Zhang (张伟)",
        ])
        self.assertEqual(authors, ["Ting Li", "Denis Erkal", "Jane Doe (Jr.)", "John Smith", "Alex Roe", "Bea Poe", "Joel Roediger", "Wei Zhang (张伟)"])
        self.assertEqual(affiliations, ["the S5 Collaboration", "University of Example", "Private note", "Canadian Space Agency, Saint-Hubert, QC, Canada"])
        self.assertEqual(references, [[1], [1], [], [2], [3], [3], [4], []])

    def test_personal_annotations_override_affiliation_signals(self):
        values = [
            "Jane Doe (Jr.)",
            "John Roe (Jr.)",
            "张伟 (Wei Zhang)",
            "李伟 (Wei Zhang)",
            "Ada Lovelace (ORCID: 0000-0002-1825-0097)",
            "Grace Hopper (on behalf of the US Navy)",
            "Jiaqi (Martin)",
            "Yixiao Zhou (Martin)",
            "Lawrence Bissell (a)",
            "Walter Seifert (a)",
        ]
        authors, affiliations, references = normalize_authors(values)
        self.assertEqual(authors, values)
        self.assertEqual(affiliations, [])
        self.assertEqual(references, [[] for _ in values])

    def test_institutional_acronyms_are_affiliations(self):
        authors, affiliations, references = normalize_authors([
            "Ada Lovelace (ESO)",
            "Grace Hopper (INAF - Osservatorio Astronomico di Brera)",
            "Katherine Johnson (European Space Agency)",
        ])
        self.assertEqual(authors, ["Ada Lovelace", "Grace Hopper", "Katherine Johnson"])
        self.assertEqual(affiliations, ["ESO", "INAF - Osservatorio Astronomico di Brera", "European Space Agency"])
        self.assertEqual(references, [[1], [2], [3]])

    def test_invalid_json_fails_without_replacing_existing_output(self):
        self.write_report("2025-02-03").write_text("not json", encoding="utf-8")
        self.output.mkdir()
        marker = self.output / "keep"
        marker.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(BuildError, "cannot read valid JSON"):
            build(self.source, self.output, self.site)
        self.assertTrue(marker.is_file())

    def test_invalid_paper_shape_fails_clearly(self):
        self.write_report("2025-02-03", {"2502.00001": {"title": "Missing fields"}})
        with self.assertRaisesRegex(BuildError, "string 'abstract'"):
            build(self.source, self.output, self.site)


if __name__ == "__main__":
    unittest.main()
