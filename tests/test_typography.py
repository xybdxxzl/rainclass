import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PyQt5 import QtGui, QtWidgets
from UI.Theme import STYLE, prepare_fonts
from UI.Typography import ui_font
from UI.MainWindow import MainWindow_Ui
from Scripts.AnswerReview import AnswerReview

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
prepare_fonts(app)


class TypographyTests(unittest.TestCase):
    def test_real_weights_and_chinese_glyphs_are_available(self):
        for weight, name in ((QtGui.QFont.Normal, 'Regular'), (QtGui.QFont.Medium, 'Medium'), (QtGui.QFont.Bold, 'Bold')):
            font = ui_font(15, weight)
            info = QtGui.QFontInfo(font)
            self.assertEqual(info.family(), 'Noto Sans SC')
            self.assertEqual(info.styleName(), name)
            raw = QtGui.QRawFont.fromFont(font)
            self.assertTrue(all(raw.supportsCharacter(ord(c)) for c in '题目答案课A9'))

    def test_answers_use_a_readable_size_for_their_length(self):
        main = QtWidgets.QMainWindow()
        ui = MainWindow_Ui()
        ui.setupUi(main, preview=True)
        main.show()
        choice = AnswerReview('课程', {'body': '问题', 'options': [{'key': 'A', 'value': '选项'}]}, 1, ['A'], 30, auto_submit=False)
        ui.workspace.add_review(choice)
        app.processEvents()
        self.assertEqual(ui.workspace.answer.font().pixelSize(), 36)
        blank = AnswerReview('课程', {'body': '填空'}, 4, ['这个较长的答案需要完整显示并允许自然换行。'], 30, auto_submit=False)
        ui.workspace.add_review(blank)
        ui.workspace.pending.setCurrentIndex(1)  # 新题不会抢占当前编辑，手动切换后检查排版。
        app.processEvents()
        self.assertEqual(ui.workspace.answer.font().pixelSize(), 16)
        self.assertIn('完整显示', ui.workspace.answer.text())
        main.close()

    def test_normal_window_keeps_four_simple_options_in_view(self):
        main = QtWidgets.QMainWindow()
        ui = MainWindow_Ui()
        ui.setupUi(main, preview=True)
        review = AnswerReview('课程', {'body': '请选择正确选项。', 'options': [{'key': x, 'value': '选项 ' + x} for x in 'ABCD']}, 1, ['A'], 30, auto_submit=False)
        ui.workspace.add_review(review)
        main.show()
        app.processEvents()
        self.assertEqual(ui.workspace.question.verticalScrollBar().maximum(), 0)
        self.assertEqual(len(ui.workspace.choice_controls), 4)
        self.assertEqual(ui.workspace.manual_scroll.verticalScrollBar().maximum(), 0)
        main.close()
