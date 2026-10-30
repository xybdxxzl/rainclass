import json
import unittest
from unittest.mock import Mock, patch
import requests
from Scripts.Classes import Lesson
from Scripts.Utils import get_initial_data


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.lesson = Lesson.__new__(Lesson)
        self.lesson.config = get_initial_data()
        self.lesson.lessonname = '课程'
        self.lesson.headers = {}
        self.lesson.add_message = Mock()
        self.lesson.main_ui = Mock()

    @patch('Scripts.Classes.time.sleep')
    @patch('Scripts.Classes.requests.post')
    def test_review_submission_has_no_extra_delay(self, post, sleep):
        post.return_value = Mock(text='{"code":0}')
        self.assertTrue(self.lesson.answer_questions('id', 1, ['B'], 120, elapsed=10, immediate=True))
        self.assertEqual(json.loads(post.call_args.kwargs['data'])['result'], ['B'])
        sleep.assert_not_called()
        post.assert_called_once()

    @patch('Scripts.Classes.requests.post', side_effect=requests.ReadTimeout('slow'))
    def test_uncertain_submission_does_not_retry(self, post):
        self.assertFalse(self.lesson.answer_questions('id', 1, ['B'], 120, immediate=True))
        post.assert_called_once()
        self.assertIn('未确认', self.lesson.add_message.call_args.args[0])

    @patch('Scripts.Classes.requests.post')
    def test_expired_answer_never_submitted(self, post):
        self.assertFalse(self.lesson.answer_questions('id', 1, ['B'], 10, elapsed=11, immediate=True))
        post.assert_not_called()
