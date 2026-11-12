import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PyQt5 import QtCore, QtGui, QtWidgets
from Scripts.Review import ReviewStore
from Scripts.ReviewExport import build_html, export_book
from Scripts.Utils import get_initial_data
from UI.ReviewDialog import ReviewDialog


APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class ReviewExportUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / 'assets' / 'sample.png'
        self.image.parent.mkdir()
        image = QtGui.QImage(1280, 720, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor('#ddeaf7'))
        self.assertTrue(image.save(str(self.image)))
        self.record = {'id': 'lesson', 'classroom_id': 'class', 'course': '软件课<script>bad()</script>', 'title': '课堂', 'date': '2026-11-10',
            'status': 'partial', 'issues': ['一项下载失败'], 'presentations': [{'id': 'p', 'name': '课件', 'slides': [{'page': 19, 'image': 'assets/sample.png'}]}],
            'questions': [{'id': 'q', 'type': 1, 'page': 19, 'image': 'assets/sample.png', 'official_answer': ['A'], 'personal_answer': ['B'], 'ai_answer': ['C'], 'ai_explanation': '仅供参考'}]}

    def tearDown(self):
        self.temp.cleanup()

    def test_offline_html_has_answer_appendix_and_no_remote_images(self):
        target = self.root / 'export' / '复习.html'
        export_book(target, [self.record], self.root, personal=True)
        content = target.read_text(encoding='utf-8')
        self.assertLess(content.index('第 1 题 ·'), content.index('答案与解析附录'))
        self.assertIn('官方答案：</b>A', content)
        self.assertIn('个人作答（不作为标准答案）：</b>B', content)
        self.assertIn('AI 参考答案（待核对）：</b>C', content)
        self.assertNotIn('<script>', content)
        self.assertNotIn('src="https://', content)
        self.assertTrue((target.parent / '复习_assets' / 'sample.png').is_file())
        self.assertNotIn('官方答案：</b>', build_html([self.record], self.root, answers=False))

    def test_missing_images_and_missing_answers_are_explicit(self):
        record = copy.deepcopy(self.record)
        record['questions'][0]['official_answer'] = []
        record['questions'][0]['image'] = '../../outside.png'
        content = build_html([record], self.root)
        self.assertIn('未获取到已公布的官方答案', content)
        self.assertIn('图片文件缺失', content)
        self.assertIn('未完成项', content)

    def test_inline_answers_follow_each_question_without_courseware(self):
        record = copy.deepcopy(self.record)
        record['questions'].append({'id': 'q2', 'type': 5, 'page': 20, 'body': '第二题题干',
            'official_explanation': '教师解析', 'ai_answer': ['参考答案']})
        content = build_html([record], self.root, courseware=False, answer_position='inline', personal=True)
        self.assertNotIn('课件：', content)
        self.assertNotIn('答案与解析附录', content)
        self.assertNotIn('答案见附录', content)
        self.assertLess(content.index('第 1 题 ·'), content.index('官方答案：</b>A'))
        self.assertLess(content.index('个人作答（不作为标准答案）：</b>B'), content.index('第 2 题 ·'))
        self.assertLess(content.index('第二题题干'), content.index('未获取到已公布的官方答案'))
        self.assertIn('教师解析', content)
        self.assertIn('AI 参考答案（待核对）：</b>参考答案', content)

    def test_courseware_only_excludes_question_and_answer_sections(self):
        content = build_html([self.record], self.root, questions=False)
        self.assertIn('课件：', content)
        self.assertNotIn('课堂习题', content)
        self.assertNotIn('第 1 题', content)
        self.assertNotIn('官方答案：</b>', content)
        from Scripts.Review import ReviewError
        with self.assertRaises(ReviewError):
            build_html([self.record], self.root, questions=False, courseware=False)

    def test_dialog_exports_separate_content_and_inline_answers(self):
        with patch('UI.ReviewDialog.get_config_dir', return_value=str(self.root)):
            dialog = ReviewDialog(get_initial_data(), 'account')
        dialog.store.save(self.record)
        dialog.start.setDate(QtCore.QDate(2026, 8, 1))
        dialog.end.setDate(QtCore.QDate(2027, 1, 31))
        def begin(kind, callback):
            callback()
        with patch.object(dialog, 'begin', side_effect=begin), \
                patch('UI.ReviewDialog.QtWidgets.QFileDialog.getSaveFileName', return_value=(str(self.root / 'out.pdf'), '')), \
                patch('UI.ReviewDialog.export_book') as exporter:
            dialog.export('pdf')
            self.assertEqual(exporter.call_args.kwargs, {'questions': True, 'answer_position': 'inline'})
            self.assertTrue(exporter.call_args.args[3])
            self.assertFalse(exporter.call_args.args[4])
            dialog.content.setCurrentIndex(1)
            dialog.export('pdf')
            self.assertFalse(exporter.call_args.kwargs['questions'])
            self.assertFalse(exporter.call_args.args[3])
            self.assertTrue(exporter.call_args.args[4])
        dialog.close()

    def test_pdf_export_is_nonempty_and_atomic(self):
        target = self.root / '复习.pdf'
        export_book(target, [self.record], self.root)
        self.assertTrue(target.read_bytes().startswith(b'%PDF'))
        self.assertGreater(target.stat().st_size, 1000)
        self.assertIn(b'/ToUnicode', target.read_bytes())
        self.assertFalse(target.with_suffix('.pdf.part').exists())

    def test_worker_pdf_export_and_cancellation(self):
        target = self.root / '后台导出.pdf'
        errors = []
        def work():
            try:
                export_book(target, [self.record], self.root)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=work)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(target.read_bytes().startswith(b'%PDF'))
        cancel = threading.Event()
        cancel.set()
        from Scripts.Review import Cancelled
        with self.assertRaises(Cancelled):
            export_book(self.root / '取消导出.pdf', [self.record], self.root, cancel=cancel)
        self.assertFalse((self.root / '取消导出.pdf').exists())

    def test_dialog_filters_dates_courses_and_cancels_before_closing(self):
        with patch('UI.ReviewDialog.get_config_dir', return_value=str(self.root)):
            dialog = ReviewDialog(get_initial_data(), 'account', '测试账号')
        dialog.add_course({'name': '软件课', 'course_id': 'c', 'classroom_id': 'class'})
        dialog.store.save(self.record)
        dialog.start.setDate(QtCore.QDate(2026, 8, 1))
        dialog.end.setDate(QtCore.QDate(2027, 1, 31))
        dialog.reload()
        self.assertEqual(len(dialog.records), 1)
        self.assertEqual(dialog.content.currentData(), 'questions')
        self.assertEqual(dialog.answer_position.currentData(), 'inline')
        dialog.content.setCurrentIndex(1)
        self.assertFalse(dialog.answers.isEnabled())
        self.assertFalse(dialog.answer_position.isEnabled())
        dialog.content.setCurrentIndex(0)
        self.assertTrue(dialog.answer_position.isEnabled())
        dialog.answers.setChecked(False)
        self.assertFalse(dialog.personal.isEnabled())
        dialog.courses.item(0).setCheckState(QtCore.Qt.Unchecked)
        self.assertEqual(len(dialog.records), 0)
        dialog.running = True
        dialog.reject()
        self.assertTrue(dialog.cancel.is_set())
        dialog.running = False
        dialog.close()


if __name__ == '__main__':
    unittest.main()
