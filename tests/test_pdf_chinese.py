"""Offline regression tests for Chinese glyphs in generated PDFs.

Run with pytest, or with python -m unittest tests.test_pdf_chinese.
"""

import tempfile
import unittest
import unicodedata
from pathlib import Path
from unittest.mock import patch

import fitz

from fonts import FONTS_DIR, resolve_font, glyph_fallback_font
from pdf_utils import make_side_by_side_pdf, write_translated_pdf


class TestChineseFonts(unittest.TestCase):
    def assert_covers(self, font_bytes, text):
        font = fitz.Font(fontbuffer=font_bytes)
        for char in text:
            if not char.isspace():
                self.assertNotEqual(font.has_glyph(ord(char), fallback=False), 0, char)

    def test_simplified_traditional_and_mixed_text(self):
        for text in ("中文翻译，测试正常。", "繁體中文翻譯：測試正常。", "PDF 中文 123，café"):
            with self.subTest(text=text):
                alias, data = resolve_font("Helvetica", 0, {}, text=text)
                self.assertEqual(alias, "sourcehan_sans_cn_r")
                self.assert_covers(data, text)

    def test_full_embedded_latin_font_does_not_hide_chinese(self):
        data = (FONTS_DIR / "LiberationSans-Regular.ttf").read_bytes()
        alias, resolved = resolve_font("Arial", 0, {"arial": data}, text="中文译文")
        self.assertEqual(alias, "sourcehan_sans_cn_r")
        self.assert_covers(resolved, "中文译文")

    def test_chinese_without_bundled_liberation_fonts(self):
        with patch("fonts.Path.exists", return_value=False):
            alias, data = resolve_font("Arial", 16, {}, text="中文译文")
        self.assertEqual(alias, "unicode_cjk")
        self.assert_covers(data, "中文译文")

    def test_latin_font_style_is_preserved(self):
        alias, data = resolve_font("ArialBold", 16, {}, text="Résumé translated")
        self.assertEqual(alias, "lsans_b")
        self.assert_covers(data, "Résumé translated")

    def test_unicode_embedded_font_with_reserved_source_name(self):
        data = fitz.Font("cjk").buffer
        alias, resolved = resolve_font("Helvetica", 0, {"helvetica": data}, text="PDF translation")
        self.assertTrue(alias.startswith("emb_"))
        self.assert_covers(resolved, "中文译文")
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_font(fontname=alias, fontbuffer=resolved)
            page.insert_text((30, 40), "中文译文", fontname=alias)
            self.assertIn("中文译文", page.get_text())

    def test_chinese_minus_uses_supplemental_font(self):
        text = "中文温度：−5 °C"
        # Exercise the final fallback even when Source Han covers the minus.
        with patch("fonts.Path.exists", return_value=False):
            alias, data = resolve_font("Helvetica", 0, {}, text=text)
        self.assertEqual(alias, "unicode_cjk")
        self.assertIsNotNone(glyph_fallback_font(alias, data, text))


class TestChinesePdf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "source.pdf"
        self.output = Path(self.tmp.name) / "translated.pdf"
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=500)
            page.insert_text((30, 45), "Source document", fontsize=12)
            doc.save(self.source)

    def write_blocks(self, blocks):
        write_translated_pdf(str(self.source), [blocks], str(self.output), {})

    def assert_text_roundtrip(self, path, expected):
        with fitz.open(path) as doc:
            text = "".join(page.get_text() for page in doc)
            # MuPDF can map a shared CJK glyph to a canonically equivalent
            # compatibility ideograph (e.g. U+FA08 instead of U+884C).
            # NFC preserves math symbols and full-width punctuation, unlike NFKC.
            compact = "".join(unicodedata.normalize("NFC", text).split())
            for phrase in expected:
                self.assertIn("".join(unicodedata.normalize("NFC", phrase).split()), compact)
            self.assertNotIn("\ufffd", text)
            self.assertNotIn("\x00", text)
            # Verify the CJK font file is actually embedded, not just referenced.
            fonts = [font for page in doc for font in page.get_fonts()]
            chinese_fonts = [
                font for font in fonts
                if any(name in font[3].replace(" ", "")
                       for name in ("SourceHan", "DroidSansFallback", "LXGW"))
            ]
            self.assertTrue(chinese_fonts)
            self.assertTrue(any(doc.extract_font(font[0])[3] for font in chinese_fonts))
            self.assertGreater(len(doc[0].get_pixmap().samples), 0)

    def test_mixed_blocks_same_source_font_and_side_by_side(self):
        phrases = ["English heading", "中文翻译，显示正常。", "繁體中文：測試通過。", "是"]
        blocks = [(30, 30 + i * 65, 360, 65 + i * 65, text, 12, "Helvetica", 0, False)
                  for i, text in enumerate(phrases)]
        self.write_blocks(blocks)
        self.assert_text_roundtrip(self.output, phrases)
        sbs = Path(self.tmp.name) / "side-by-side.pdf"
        make_side_by_side_pdf(str(self.source), str(self.output), str(sbs))
        self.assert_text_roundtrip(sbs, phrases)

    def test_table_cell_and_wrapped_paragraph(self):
        cell = "中文表格"
        paragraph = "这是没有空格的中文长段落，用于验证自动换行后所有文字都能正确显示。" * 2
        blocks = [
            (30, 30, 100, 55, cell, 12, "Helvetica", 0, True),
            (30, 100, 390, 170, paragraph, 12, "Helvetica", 0, False),
        ]
        self.write_blocks(blocks)
        self.assert_text_roundtrip(self.output, [cell, paragraph])
        with fitz.open(self.output) as doc:
            lines = [line for block in doc[0].get_text("dict")["blocks"]
                     if block["type"] == 0 for line in block["lines"]]
            self.assertGreater(len(lines), 2)

    def test_font_registration_failure_keeps_chinese(self):
        real_insert = fitz.Page.insert_font

        def fail_latin_font(page, *args, **kwargs):
            if kwargs.get("fontname") == "lsans_r":
                raise RuntimeError("Source font cannot be registered")
            return real_insert(page, *args, **kwargs)

        with patch.object(fitz.Page, "insert_font", fail_latin_font):
            self.write_blocks([
                (30, 30, 360, 65, "English text", 12, "Helvetica", 0, False),
                (30, 100, 360, 135, "中文译文", 12, "Helvetica", 0, False),
            ])
        self.assert_text_roundtrip(self.output, ["English text", "中文译文"])

    def test_last_resort_insert_text_keeps_chinese(self):
        with patch.object(fitz.Page, "insert_textbox", return_value=-1):
            self.write_blocks([(30, 30, 360, 65, "中文译文", 12, "Helvetica", 0, False)])
        self.assert_text_roundtrip(self.output, ["中文译文"])

    def test_translation_pipeline_chinese(self):
        from pipeline import translate_pdf_sync

        for target, phrase in (("zh", "中文翻译，显示正常。"), ("zh-tw", "繁體中文翻譯，顯示正常。")):
            with self.subTest(target=target), patch("tempfile.tempdir", self.tmp.name):
                translated, sbs, html = translate_pdf_sync(
                    str(self.source), "en", target, "Mock",
                    call_fn=lambda text, source, language: phrase,
                    translation_threads=1,
                )
                self.assert_text_roundtrip(translated, [phrase])
                self.assert_text_roundtrip(sbs, [phrase])
                self.assertIn(phrase, Path(html).read_text(encoding="utf-8"))

    def test_chinese_math_symbols_roundtrip(self):
        phrases = ["中文温度：−5 °C", "繁體計算：10 − 3 = 7，∑與∫", "中文表格：−25"]
        self.write_blocks([
            (30, 30, 360, 65, phrases[0], 12, "Helvetica", 0, False),
            (30, 100, 360, 135, phrases[1], 12, "Helvetica", 0, False),
            (30, 170, 180, 200, phrases[2], 12, "Helvetica", 0, True),
        ])
        self.assert_text_roundtrip(self.output, phrases)

    def test_chinese_minus_small_box_fallback(self):
        phrase = "中文数值：−5\n第二行：−3"
        self.write_blocks([(30, 30, 180, 31, phrase, 12, "Helvetica", 0, True)])
        self.assert_text_roundtrip(self.output, [phrase])

    def test_chinese_math_symbols_wrap(self):
        phrase = "这是中文数学文本：−5 与 ∑，用于检查没有空格时的自动换行。" * 3
        self.write_blocks([(30, 30, 390, 160, phrase, 12, "Helvetica", 0, False)])
        self.assert_text_roundtrip(self.output, [phrase])

    def test_translation_pipeline_chinese_minus(self):
        from pipeline import translate_pdf_sync

        phrase = "中文温度：−5 °C"
        with patch("tempfile.tempdir", self.tmp.name):
            translated, sbs, html = translate_pdf_sync(
                str(self.source), "en", "zh", "Mock",
                call_fn=lambda text, source, target: phrase, translation_threads=1,
            )
            self.assert_text_roundtrip(translated, [phrase])
            self.assert_text_roundtrip(sbs, [phrase])
            self.assertIn(phrase, Path(html).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
