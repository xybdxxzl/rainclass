"""不启动 Qt 或课堂连接，验证答题线程的时间预算与失败提示。"""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock
from Scripts.AnswerReview import AnswerReview, review_seconds


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'Scripts' / 'Classes.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        methods = [node for cls in tree.body if isinstance(cls, ast.ClassDef)
                   for node in cls.body if isinstance(node, ast.FunctionDef)
                   and node.name in ('_answer_by_llm', '_llm_fallback_hint')]
        namespace = {'time': Mock(), 'ask_llm': Mock(return_value=['A'])}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
        self.ns = namespace
        self.lesson = type('Lesson', (), {name: namespace[name] for name in
                           ('_answer_by_llm', '_llm_fallback_hint')})()
        self.lesson.config = {'llm_config': {'enabled': True}}
        self.lesson.lessonname = '课程'
        self.lesson.add_message = Mock()
        self.lesson.answer_questions = Mock()
        self.lesson.main_ui = Mock(is_active=True)
        self.ns['review_seconds'] = review_seconds
        self.ns['AnswerReview'] = AnswerReview
        self.lesson.main_ui.review_answer_signal.emit.side_effect = lambda review: review.finish()

    def test_submit_reserve_and_elapsed(self):
        self.ns['time'].monotonic.side_effect = [100, 108, 108]
        self.lesson._answer_by_llm({'body': '题目'}, 'id', 1, 120)
        self.assertEqual(self.ns['ask_llm'].call_args.kwargs['time_budget'], 115)
        self.lesson.answer_questions.assert_called_once_with('id', 1, ['A'], 120, elapsed=8, immediate=True)

    def test_edited_answer_submitted_once(self):
        self.ns['time'].monotonic.side_effect = [100, 108, 110]
        def edit(review):
            review.update(['C'])
            review.finish()
        self.lesson.main_ui.review_answer_signal.emit.side_effect = edit
        self.lesson._answer_by_llm({'body': '题目'}, 'id', 1, 120)
        self.lesson.answer_questions.assert_called_once_with('id', 1, ['C'], 120, elapsed=10, immediate=True)

    def test_cancel_does_not_submit(self):
        self.ns['time'].monotonic.side_effect = [100, 108]
        self.lesson.main_ui.review_answer_signal.emit.side_effect = lambda review: review.finish(cancel=True)
        self.lesson._answer_by_llm({'body': '题目'}, 'id', 1, 120)
        self.lesson.answer_questions.assert_not_called()

    def test_short_deadline_does_not_submit(self):
        self.ns['time'].monotonic.side_effect = [100, 108]
        self.lesson._answer_by_llm({'body': '题目'}, 'id', 1, 10)
        self.lesson.answer_questions.assert_not_called()

    def test_failure_hint_subtracts_elapsed(self):
        self.ns['time'].monotonic.side_effect = [100, 108]
        self.ns['ask_llm'].side_effect = RuntimeError('timeout')
        self.lesson._answer_by_llm({'body': '题目'}, 'id', 1, 120)
        self.assertIn('112秒', self.lesson.add_message.call_args.args[0])
        self.lesson.answer_questions.assert_not_called()

    def test_expired_hint(self):
        self.lesson._llm_fallback_hint(10, elapsed=11)
        self.assertIn('已超时', self.lesson.add_message.call_args.args[0])

    def test_unlimited_hint(self):
        self.lesson._llm_fallback_hint(-1, elapsed=500)
        self.assertIn('不限时', self.lesson.add_message.call_args.args[0])
