import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PyQt5 import QtWidgets, QtCore
from UI.AnswerDialog import AnswerDialog
from UI.Config import Config_Ui
from Scripts.AnswerReview import AnswerReview
from Scripts.Utils import get_initial_data

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class UITests(unittest.TestCase):
    def make_review(self, kind=1, answers=None, seconds=15):
        return AnswerReview('Web前端开发', {'body': '哪个元素表示超链接？', 'options': [
            {'key': 'A', 'value': 'div'}, {'key': 'B', 'value': 'a'}]}, kind, answers or ['A'], seconds)

    def test_edit_and_submit(self):
        review = self.make_review()
        dialog = AnswerDialog(review)
        dialog.choices[1][1].click()
        dialog.submit.click()
        self.assertEqual(review.answers, ['B'])
        self.assertTrue(review.event.is_set())
        self.assertFalse(review.cancelled)

    def test_timer_submits_modified_text(self):
        review = self.make_review(5, ['初始答案'], seconds=0.02)
        dialog = AnswerDialog(review)
        dialog.editors[0].setPlainText('修改后的答案')
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(180, loop.quit)
        loop.exec_()
        self.assertTrue(review.event.is_set())
        self.assertEqual(review.answers, ['修改后的答案'])

    def test_close_cancels(self):
        review = self.make_review()
        dialog = AnswerDialog(review)
        dialog.show()
        dialog.close()
        self.assertTrue(review.cancelled)

    def test_multiple_dialogs_are_independent(self):
        a, b = self.make_review(), self.make_review()
        da, db = AnswerDialog(a), AnswerDialog(b)
        da.reject()
        self.assertFalse(b.event.is_set())
        db.reject()

    def test_provider_switch_separates_keys(self):
        config = Config_Ui()
        dialog = QtWidgets.QDialog()
        config.setupUi(dialog)
        data = get_initial_data()
        data['llm_config'].update(base_url='https://api.deepseek.com', api_key='deepseek-key', model='my-model')
        config.load_config(data)
        config.provider_select.setCurrentIndex(4)
        self.assertEqual(config.llm_api_key_input.text(), '')
        self.assertIn('googleapis.com', config.llm_base_url_input.text())
        config.llm_api_key_input.setText('gemini-key')
        config.provider_select.setCurrentIndex(1)
        self.assertEqual(config.llm_api_key_input.text(), 'deepseek-key')
        self.assertEqual(config.llm_model_input.text(), 'my-model')

    def test_main_window_without_network(self):
        from UI.MainWindow import MainWindow_Ui
        with patch.object(MainWindow_Ui, 'check_config', return_value=get_initial_data()), \
             patch.object(MainWindow_Ui, 'check_login', return_value=(True, {'name': '测试用户'})):
            main = QtWidgets.QMainWindow()
            ui = MainWindow_Ui()
            ui.setupUi(main)
            self.assertIn('雨课堂助手', main.windowTitle())
            ui.is_active = True
            review = self.make_review()
            ui.show_answer_review(review)
            self.assertEqual(len(ui.review_dialogs), 1)
            ui.cancel_reviews()
            self.assertTrue(review.cancelled)
            main.close()
