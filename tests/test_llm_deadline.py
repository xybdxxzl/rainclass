"""验证收到题目即能手答，模型处理与提交相互独立。"""
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import requests
from Scripts.Classes import Lesson
from Scripts.AnswerReview import AnswerReview, review_seconds
from Scripts.History import HistoryStore
from Scripts.Utils import get_initial_data
from Scripts.LLM import bounded_call, test_connection as check_connection


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = get_initial_data()
        config.update(sessionid='test-session', audio_on=False)
        self.ui = SimpleNamespace(config=config, is_active=True, history_store=HistoryStore(Path(self.temp.name)/'history.db'),
            state_signal=Mock(), review_answer_signal=Mock(), question_signal=Mock(), metrics_signal=Mock())
        self.lesson = Lesson.__new__(Lesson)
        self.lesson.main_ui, self.lesson.config = self.ui, config
        self.lesson.sessionid, self.lesson.user_uid = 'test-session', 'account-a'
        self.lesson.lessonid, self.lesson.lessonname = 'lesson-a', '课程'
        self.lesson.closed = False
        self.lesson.reviews, self.lesson.problems_dict = {}, {}
        self.lesson.presentation_ids, self.lesson.answered_problem_ids = set(), set()
        self.lesson.history = self.ui.history_store
        self.lesson.add_message = Mock()
        self.lesson.answer_questions = Mock(return_value=True)
        self.problem = {'problemType': 1, 'body': '题目', 'options': [{'key':'A','value':'甲'}, {'key':'B','value':'乙'}]}
        self.addCleanup(self.stop)

    def stop(self):
        self.ui.is_active = False
        for review in self.lesson.reviews.values():
            review.finish(cancel=True)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_disabled_model_still_allows_immediate_manual_submission(self):
        with patch('Scripts.Classes.ask_llm') as model:
            review = self.lesson.start_answer('p1', 120, self.problem)
            self.assertIs(self.ui.review_answer_signal.emit.call_args.args[0], review)
            self.assertTrue(review.can_answer)
            self.assertFalse(review.valid_input)
            self.assertTrue(review.update(['B']))
            self.assertFalse(review.auto_submit)
            self.assertTrue(review.finish())
            self.wait_for(lambda: self.lesson.answer_questions.call_count == 1)
            self.assertEqual(self.lesson.answer_questions.call_args.args[:3], ('p1', 1, ['B']))
            model.assert_not_called()

    def test_model_timeout_does_not_disable_manual_answer(self):
        self.ui.config['llm_config'].update(enabled=True, api_key='test')
        with patch('Scripts.Classes.bounded_call', side_effect=requests.Timeout('slow')):
            review = self.lesson.start_answer('p2', 120, self.problem)
            self.wait_for(lambda: review.llm_state == 'timeout')
            self.assertTrue(review.can_answer)
            review.update(['A'])
            self.assertTrue(review.finish())
            self.wait_for(lambda: self.lesson.answer_questions.call_count == 1)
            self.assertIn('超时', review.llm_error)

    def test_manual_submission_while_model_is_running_and_late_result_ignored(self):
        self.ui.config['llm_config'].update(enabled=True, api_key='test')
        entered, release = threading.Event(), threading.Event()
        def model(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {'answers':['A']}
        with patch('Scripts.Classes.ask_llm', side_effect=model):
            review = self.lesson.start_answer('p3', 120, self.problem)
            self.assertTrue(entered.wait(1))
            review.update(['B'])
            self.assertTrue(review.finish())
            self.wait_for(lambda: self.lesson.answer_questions.call_count == 1)
            release.set()
            time.sleep(.05)
            self.assertEqual(review.answers, ['B'])
            self.assertEqual(review.result, {})
            self.assertFalse(review.finish())
            self.lesson.answer_questions.assert_called_once()

    def test_missing_question_and_unsupported_type_are_visible_and_recorded(self):
        review = self.lesson.start_answer('missing', 60, {})
        unsupported = self.lesson.start_answer('vote', 60, {'body':'投票题', 'problemType':3})
        self.assertFalse(review.can_answer)
        self.assertFalse(unsupported.can_answer)
        self.assertIsNotNone(review.retry_question)
        self.assertEqual(self.ui.review_answer_signal.emit.call_count, 2)
        self.assertEqual(len(self.ui.history_store.list('account-a')), 2)

    def test_repeated_push_backfills_missing_content_without_new_record(self):
        review = self.lesson.start_answer('same', 60, {})
        again = self.lesson.start_answer('same', 60, self.problem)
        self.assertIs(review, again)
        self.assertTrue(review.can_answer)
        self.ui.review_answer_signal.emit.assert_called_once()
        self.assertEqual(len(self.ui.history_store.list('account-a')), 1)

    def test_expired_question_is_kept_but_never_submitted(self):
        review = self.lesson.start_answer('expired', 0, self.problem)
        self.assertTrue(review.event.is_set())
        self.assertEqual(review.submission_state, 'expired')
        self.assertFalse(review.finish())
        self.lesson.answer_questions.assert_not_called()
        self.assertEqual(self.ui.history_store.list('account-a')[0]['submission_state'], 'expired')

    def test_edited_draft_not_overwritten_by_model_suggestion(self):
        review = AnswerReview('课程', self.problem, 1, [], 0)
        review.update(['B'])
        self.assertTrue(review.apply_model({'answers':['A']}, 0, 15))
        self.assertEqual(review.answers, ['B'])
        self.assertEqual(review.result['answers'], ['A'])
        self.assertFalse(review.auto_submit)

    def test_total_deadline_returns_before_blocking_model_does(self):
        release = threading.Event()
        started = time.monotonic()
        try:
            with self.assertRaises(requests.Timeout):
                bounded_call(lambda: release.wait(2), .03)
            self.assertLess(time.monotonic() - started, .5)
        finally:
            release.set()

    def test_configuration_test_has_no_submission_or_history_side_effects(self):
        with patch('Scripts.LLM.ask_llm', return_value={'answers':['A']}):
            result = check_connection({'base_url':'https://example.test', 'api_key':'test', 'model':'model'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['model'], 'model')
        self.assertEqual(self.ui.history_store.list('account-a'), [])
        self.lesson.answer_questions.assert_not_called()

    def test_old_account_state_is_not_sent_to_new_account(self):
        self.ui.config['sessionid'] = 'new-account-session'
        self.lesson._state(kind='account', account_id='account-a', name='旧用户')
        self.ui.state_signal.emit.assert_not_called()

    def test_unknown_submission_from_previous_run_is_not_repeated(self):
        previous = AnswerReview('课程', self.problem, 1, ['A'], 10,
            account_id='account-a', lesson_id='lesson-a', problem_id='old', history=self.ui.history_store)
        previous.set_state(submission_state='unknown')
        review = self.lesson.start_answer('old', 120, self.problem)
        self.assertEqual(review.submission_state, 'unknown')
        self.assertTrue(review.event.is_set())
        self.assertFalse(review.can_answer)
        self.assertFalse(review.finish())
        self.lesson.answer_questions.assert_not_called()

    def test_failure_hint_and_review_budget(self):
        self.lesson._llm_fallback_hint(120, elapsed=8)
        self.assertIn('112秒', self.lesson.add_message.call_args.args[0])
        self.assertEqual(review_seconds(120, 5), 15)
        self.assertEqual(review_seconds(10, 8), 0)

