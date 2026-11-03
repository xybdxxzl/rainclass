import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from Scripts.AnswerReview import AnswerReview
from Scripts.History import HistoryStore
from Scripts.Utils import get_initial_data
from UI.MainWindow import MainWindow_Ui
from UI.Config import Config_Ui
from UI.HistoryDialog import HistoryDialog


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class LiveWorkspaceUITests(unittest.TestCase):
    def make_ui(self):
        main = QtWidgets.QMainWindow()
        ui = MainWindow_Ui()
        ui.setupUi(main, preview=True)
        ui.is_active = True
        self.addCleanup(main.close)
        return main, ui

    def make_review(self, problem_id='1', kind=1, problem=None):
        return AnswerReview('测试课程', problem or {'body':'收到即可选择', 'options':[{'key':'A','value':'甲'}, {'key':'B','value':'乙'}]},
                            kind, [], 15, account_id='user-1', lesson_id='lesson-1', problem_id=problem_id)

    def test_model_pending_and_timeout_keep_manual_choices_available(self):
        main, ui = self.make_ui()
        review = self.make_review()
        ui.show_answer_review(review)
        self.assertEqual(len(ui.workspace.choice_controls), 2)
        self.assertFalse(ui.workspace.submit.isEnabled())
        ui.workspace.choice_controls[1][1].click()
        self.assertEqual(review.answers, ['B'])
        self.assertTrue(ui.workspace.submit.isEnabled())
        review.set_state(llm_state='timeout', llm_error='模型超过30秒仍未响应')
        ui.workspace.refresh()
        self.assertIn('超时', ui.workspace.model_status.text())
        self.assertTrue(ui.workspace.submit.isEnabled())
        ui.workspace.submit.click()
        self.assertTrue(review.event.is_set())
        self.assertFalse(review.cancelled)

    def test_new_questions_and_model_updates_do_not_take_editor_focus(self):
        main, ui = self.make_ui()
        first, second = self.make_review('1'), self.make_review('2')
        ui.show_answer_review(first)
        ui.workspace.choice_controls[0][1].click()
        ui.show_answer_review(second)
        self.assertIs(ui.workspace.active, first)
        self.assertEqual(ui.workspace.pending.count(), 2)
        first.result = {'answers':['B'], 'explanation':'模型建议'}
        first.llm_state = 'success'
        ui.update_state({'kind':'question', 'review':first})
        self.assertEqual(first.answers, ['A'])
        self.assertTrue(ui.workspace.choice_controls[0][1].isChecked())
        self.assertIn('B', ui.workspace.confidence.text())
        ui.workspace.use_suggestion.click()
        self.assertEqual(first.answers, ['B'])
        ui.workspace.pending.setCurrentIndex(1)
        self.assertIs(ui.workspace.active, second)

    def test_blank_text_question_can_be_filled_before_model(self):
        main, ui = self.make_ui()
        review = self.make_review(kind=4, problem={'body':'填写两个答案', 'blanks':['','']})
        ui.show_answer_review(review)
        self.assertEqual(len(ui.workspace.answer_editors), 2)
        ui.workspace.answer_editors[0].setPlainText('第一空')
        self.assertFalse(ui.workspace.submit.isEnabled())
        ui.workspace.answer_editors[1].setPlainText('第二空')
        self.assertEqual(review.answers, ['第一空', '第二空'])
        self.assertTrue(ui.workspace.submit.isEnabled())

    def test_history_reopens_with_failed_question_and_account_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'history.sqlite3')
            store = HistoryStore(path)
            review = self.make_review()
            review.history = store
            review.set_state(llm_state='timeout', llm_error='已超时', status='模型已超时')
            dialog = HistoryDialog(HistoryStore(path), 'user-1', '张三')
            self.addCleanup(dialog.close)
            self.assertEqual(len(dialog.records), 1)
            self.assertIn('收到即可选择', dialog.detail.toPlainText())
            self.assertIn('已超时', dialog.detail.toPlainText())
            dialog.keyword.setText('不存在的题目')
            dialog.reload()
            self.assertEqual(len(dialog.records), 0)
            other = HistoryDialog(store, 'another-user')
            self.addCleanup(other.close)
            self.assertEqual(len(other.records), 0)

    def test_account_name_and_monitor_information_are_separate(self):
        main, ui = self.make_ui()
        ui.apply_account({'id':'user-1', 'name':'张三'})
        ui.update_state({'kind':'monitor', 'state':'reconnecting', 'status':'正在重连课堂', 'last_message':1, 'received_count':0})
        self.assertIn('张三', ui.workspace.connection.text())
        self.assertIn('重连', ui.workspace.monitor_state.text())
        self.assertIn('尚未收到题目', ui.workspace.activity_status.text())

    def test_multiple_courses_keep_reconnect_state_and_unique_question_count(self):
        main, ui = self.make_ui()
        ui.apply_account({'id':'user-1', 'name':'张三'})
        ui.update_state({'kind':'monitor', 'lesson_id':'a', 'course':'课程甲', 'state':'listening', 'last_message':10})
        ui.update_state({'kind':'monitor', 'lesson_id':'b', 'course':'课程乙', 'state':'reconnecting', 'last_message':5})
        ui.update_state({'kind':'monitor', 'lesson_id':'a', 'last_message':15, 'received_count':99})
        self.assertIn('1 门已连接', ui.workspace.monitor_state.text())
        self.assertIn('1 门连接中或重连', ui.workspace.monitor_state.text())
        self.assertEqual(ui.workspace.monitor_details['last_message'], 15)
        ui.show_answer_review(self.make_review())
        ui.workspace.refresh()
        self.assertIn('已收 1 题', ui.workspace.activity_status.text())
        ui.update_state({'kind':'account', 'account_id':'old-user', 'name':'旧账号', 'status':'verified'})
        self.assertIn('张三', ui.workspace.connection.text())

    def test_model_timeout_status_stays_visible_above_scroll_area(self):
        main, ui = self.make_ui()
        review = self.make_review()
        review.set_state(llm_state='timeout', llm_error='模型超过30秒仍未响应，可继续手动作答')
        ui.show_answer_review(review)
        main.show()
        app.processEvents()
        status = ui.workspace.model_status
        self.assertTrue(status.isVisible())
        self.assertIn('超时', status.text())
        self.assertFalse(ui.workspace.right_scroll.isAncestorOf(status))
        self.assertTrue(status.parentWidget().rect().contains(status.geometry()))
        ui.monitor_generation = 'new-run'
        ui.update_state({'kind':'monitor', 'generation':'old-run', 'state':'stopped'})
        self.assertTrue(ui.is_active)

    def test_unsaved_connection_config_is_used_and_late_result_invalidated(self):
        dialog = QtWidgets.QDialog()
        config = Config_Ui()
        config.setupUi(dialog)
        config.load_config(get_initial_data())
        self.addCleanup(dialog.close)
        config.llm_on.setChecked(True)
        config.llm_base_url_input.setText('https://example.test/v1')
        config.llm_api_key_input.setText('dummy-key')
        config.llm_model_input.setText('unsaved-model')
        with patch('Scripts.LLM.test_connection', return_value={'ok':True,'model':'unsaved-model','message':'响应有效','latency_ms':25}) as test:
            config.start_connection_test()
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(100, loop.quit)
            loop.exec_()
            self.assertEqual(test.call_args.args[0]['model'], 'unsaved-model')
            self.assertIn('测试通过', config.test_result.text())
        previous = config._test_generation
        config.llm_model_input.setText('changed-model')
        config.finish_connection_test(previous, {'ok':True,'model':'unsaved-model'})
        self.assertIn('重新测试', config.test_result.text())


if __name__ == '__main__':
    unittest.main()
