import unittest

from arxiv_assistant.utils.utils import latex_text_to_unicode, split_outside_parentheses


class LatexTextNormalizationTests(unittest.TestCase):
    def test_common_accents_and_letters(self):
        self.assertEqual(latex_text_to_unicode(r"\v{Z}eljko Ivezi\'c"), "Željko Ivezić")
        self.assertEqual(latex_text_to_unicode(r"Fran\c{c}ois Hoefl\"ich"), "François Hoeflïch")
        self.assertEqual(latex_text_to_unicode(r"Garc\'ia Mu\~noz"), "García Muñoz")
        self.assertEqual(latex_text_to_unicode(r"Micha\l{} Bejger and \O{}sterg\aa{}rd"), "Michał Bejger and Østergård")

    def test_author_split_preserves_affiliation_commas(self):
        value = "Ada Lovelace (Example University, London, UK), Grace Hopper (US Navy)"
        self.assertEqual(split_outside_parentheses(value), [
            "Ada Lovelace (Example University, London, UK)", "Grace Hopper (US Navy)",
        ])

    def test_unknown_commands_and_math_are_preserved(self):
        value = r"SQuIGG$\vec{L}$E and $M_V=-2.7$ with \unknown{Name}"
        self.assertEqual(latex_text_to_unicode(value), value)


if __name__ == "__main__":
    unittest.main()
