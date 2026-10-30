import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtTest import QTest
from UI.Config import Config_Ui
from Scripts.Utils import get_initial_data

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dialog = QtWidgets.QDialog()
        self.ui = Config_Ui()
        self.ui.setupUi(self.dialog)
        self.config = get_initial_data()
        self.config['llm_config'].update(enabled=True, api_key='test-key')
        self.ui.load_config(self.config)

    def tearDown(self):
        self.dialog.close()

    def test_footer_remains_visible_at_small_heights(self):
        for width, height in ((780, 680), (640, 480), (500, 380)):
            self.dialog.resize(width, height)
            self.dialog.show()
            for tab in range(3):
                self.ui.tabs.setCurrentIndex(tab)
                app.processEvents()
                pos = self.ui.save.mapTo(self.dialog, self.ui.save.rect().bottomRight())
                self.assertTrue(self.ui.save.isVisible())
                self.assertLess(pos.y(), self.dialog.height())
                self.assertLess(pos.x(), self.dialog.width())

    def test_cancel_does_not_mutate_shared_settings(self):
        before = copy.deepcopy(self.config)
        self.ui.llm_api_key_input.setText('new-key')
        self.ui.provider_select.setCurrentIndex(4)
        self.ui.review_seconds_input.setValue(28)
        self.ui.cancel.click()
        self.assertEqual(self.config, before)

    def test_save_normalizes_endpoint_and_preserves_unknown_settings(self):
        self.config['extra'] = {'keep': 1}
        self.ui.load_config(self.config)
        self.ui.llm_base_url_input.setText(' https://api.deepseek.com/chat/completions/ ')
        self.ui.llm_api_key_input.setText(' test-key ')
        self.ui.review_seconds_input.setValue(28)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'data' / 'config.json'
            with patch('UI.Config.get_config_path', return_value=str(target)):
                self.assertTrue(self.ui.save_config(self.dialog))
            saved = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(saved['llm_config']['base_url'], 'https://api.deepseek.com')
            self.assertEqual(saved['llm_config']['api_key'], 'test-key')
            self.assertEqual(saved['answer_review_seconds'], 28)
            self.assertEqual(saved['extra'], {'keep': 1})
            self.assertEqual(self.config, saved)
            self.assertEqual(self.dialog.result(), QtWidgets.QDialog.Accepted)

    def test_invalid_inputs_do_not_save_or_change_config(self):
        before = copy.deepcopy(self.config)
        for url in ('not-a-url', 'ftp://example.com', 'https://example.com?key=test', 'https://u:p@example.com', 'https://example.com:bad'):
            self.ui.llm_base_url_input.setText(url)
            with patch('UI.Config.get_config_path') as path:
                self.assertFalse(self.ui.save_config(self.dialog))
                path.assert_not_called()
            self.assertEqual(self.config, before)
            self.assertTrue(self.ui.errors['url'].text())

    def test_missing_key_and_model_have_inline_errors(self):
        self.ui.llm_api_key_input.clear()
        self.ui.llm_model_input.clear()
        self.assertFalse(self.ui.validate())
        self.assertTrue(self.ui.errors['key'].text())
        self.assertTrue(self.ui.errors['model'].text())

    def test_save_failure_preserves_old_config(self):
        before = copy.deepcopy(self.config)
        with tempfile.TemporaryDirectory() as folder:
            with patch('UI.Config.get_config_path', return_value=folder):
                self.assertFalse(self.ui.save_config(self.dialog))
        self.assertEqual(self.config, before)
        self.assertIn('保存失败', self.ui.status.text())

    def test_disabled_model_allows_saving_other_preferences(self):
        self.ui.llm_on.setChecked(False)
        self.ui.llm_api_key_input.clear()
        self.assertTrue(self.ui.validate())

    def test_key_visibility_resets_when_switching_provider(self):
        self.ui.show_key.click()
        self.assertEqual(self.ui.llm_api_key_input.echoMode(), QtWidgets.QLineEdit.Normal)
        self.ui.provider_select.setCurrentIndex(4)
        self.assertEqual(self.ui.llm_api_key_input.echoMode(), QtWidgets.QLineEdit.Password)
        self.assertEqual(self.ui.llm_api_key_input.text(), '')

    def test_custom_provider_expands_address_and_retains_drafts(self):
        self.ui.provider_select.setCurrentIndex(0)
        self.assertTrue(self.ui.advanced_toggle.isChecked())
        self.ui.llm_base_url_input.setText('https://example.test/v1')
        self.ui.llm_model_input.setText('custom-model')
        self.ui.provider_select.setCurrentIndex(1)
        self.ui.provider_select.setCurrentIndex(0)
        self.assertEqual(self.ui.llm_model_input.text(), 'custom-model')
        self.assertEqual(self.ui.llm_base_url_input.text(), 'https://example.test/v1')

    def test_old_partial_config_gets_nested_defaults(self):
        self.ui.load_config({'llm_config': {'enabled': False}})
        self.assertEqual(self.ui.review_seconds_input.value(), 15)
        self.assertEqual(self.ui.llm_timeout_input.value(), 30)

    def test_keyboard_save(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'config.json'
            self.dialog.show()
            self.dialog.activateWindow()
            app.processEvents()
            with patch('UI.Config.get_config_path', return_value=str(target)):
                QTest.keyClick(self.dialog, QtCore.Qt.Key_S, QtCore.Qt.ControlModifier)
                app.processEvents()
            self.assertTrue(target.exists())
            self.assertEqual(self.dialog.result(), QtWidgets.QDialog.Accepted)

    def test_saved_profiles_survive_reopening(self):
        self.ui.provider_select.setCurrentIndex(4)
        self.ui.llm_api_key_input.setText('gemini-key')
        with tempfile.TemporaryDirectory() as folder:
            with patch('UI.Config.get_config_path', return_value=str(Path(folder) / 'config.json')):
                self.assertTrue(self.ui.save_config(self.dialog))
        fresh = Config_Ui()
        dialog = QtWidgets.QDialog()
        fresh.setupUi(dialog)
        fresh.load_config(self.config)
        self.assertEqual(fresh.llm_api_key_input.text(), 'gemini-key')
        fresh.provider_select.setCurrentIndex(1)
        self.assertEqual(fresh.llm_api_key_input.text(), 'test-key')
        dialog.close()
