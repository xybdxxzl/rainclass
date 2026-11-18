"""官网前端字段构造的契约样例；不声称是真实账号接口响应。"""
import copy
import datetime as dt
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from Scripts.Review import (BEIJING, Cancelled, ReviewClient, ReviewCollector, ReviewError,
                            ReviewStore, lesson_rows, parse_course_url, supplement_ai, missing_official)


COURSE = {'course_id': 'c', 'classroom_id': 'class-a', 'name': '软件设计'}


class FakeClient:
    def __init__(self):
        self.cancel = threading.Event()
        self.calls = []
        self.fail_question = False
        self.fail_lesson = False

    def verify(self):
        self.calls.append('verify')

    def lessons(self, course, start, end):
        return [{'id': 'lesson', 'date': '2026-11-10', 'title': '工厂方法'}]

    def lesson(self, lesson_id):
        if self.fail_lesson:
            raise ReviewError('网络失败')
        return {'identityId': 'my-identity'}, {'presentation': [{'presentationId': 'p', 'name': '课件', 'totalCount': 1}],
            'problem': {'studentList': [{'problemId': 'q1', 'problem_type': 1, 'idx': 19},
                                       {'problemId': 'q2', 'problem_type': 5, 'idx': 20},
                                       {'problemId': 'q1', 'problem_type': 1, 'idx': 19}]}}

    def presentation(self, lesson_id, presentation_id):
        return [{'index': 19, 'cover': 'question-image', 'visible': True, 'hasProblem': True},
                {'index': 1, 'cover': 'hidden-image', 'visible': False, 'hasProblem': False}]

    def image(self, url, folder):
        self.calls.append(('image', url))
        return 'assets/sample.img'

    def question(self, lesson, identity, pid):
        self.calls.append(('question', identity, pid))
        if self.fail_question and pid == 'q1':
            raise ReviewError('获取失败')
        if pid == 'q1':
            return {'content': {'index': 19, 'cover': 'question-image', 'answer': ['A'], 'body': '<p>题干</p>'},
                    'problem': {'result': ['B'], 'score': 100, 'correct': True}}
        return {'content': {'index': 20, 'cover': 'subjective-image', 'remarkRich': '<p>教师提示</p>'},
                'problem': {'result': {'content': '我的作答', 'pics': [{'pic': 'personal-photo'}]}, 'score': -1}}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ReviewStore(self.temp.name, 'account-a')
        self.client = FakeClient()

    def tearDown(self):
        self.temp.cleanup()

    def collect(self):
        return ReviewCollector(self.client, self.store).collect([COURSE], '2026-08-01', '2027-01-31')

    def test_official_personal_and_subjective_answers_are_separate(self):
        report = self.collect()
        self.assertEqual(report['questions'], 2)
        self.assertEqual(report['missing_answers'], 1)
        a, b = self.store.list()[0]['questions']
        self.assertEqual(a['official_answer'], ['A'])
        self.assertEqual(a['personal_answer'], ['B'])
        self.assertEqual(b['official_answer'], [])
        self.assertEqual(b['personal_answer'], ['我的作答'])
        self.assertEqual(b['official_explanation'], '教师提示')
        self.assertIn(('question', 'my-identity', 'q1'), self.client.calls)
        self.assertNotIn(('image', 'hidden-image'), self.client.calls)
        self.assertEqual(a['type'], 1)
        self.assertEqual(b['type'], 5)

    def test_recollection_is_idempotent_and_failure_preserves_prior_material(self):
        self.collect()
        self.collect()
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.store.list()[0]['questions']), 2)
        self.client.fail_question = True
        result = self.collect()
        self.assertEqual(result['partial'], 1)
        self.assertEqual(self.store.list()[0]['questions'][0]['official_answer'], ['A'])
        self.assertTrue(self.store.list()[0]['questions'][0]['error'])
        self.client.fail_lesson = True
        self.assertEqual(self.collect()['failed'], 1)
        self.assertEqual(len(self.store.list()[0]['questions']), 2)

    def test_accounts_and_classrooms_are_isolated(self):
        self.collect()
        other_account = ReviewStore(self.temp.name, 'account-b')
        self.assertEqual(other_account.list(), [])
        self.assertEqual(self.store.list('another-classroom'), [])
        self.assertEqual(self.store.list(start='2027-01-01'), [])
        self.assertNotIn('sessionid', self.store.list()[0])

    def test_cancellation_checkpoints_partial_lesson(self):
        original = self.client.presentation
        def cancel_on_slides(*args):
            result = original(*args)
            self.client.cancel.set()
            return result
        self.client.presentation = cancel_on_slides
        with self.assertRaises(Cancelled):
            self.collect()
        self.assertEqual(self.store.list()[0]['status'], 'partial')
        self.assertTrue(self.store.list()[0]['issues'])

    def test_semester_boundaries_are_inclusive_in_beijing_time_and_deduplicated(self):
        stamp = dt.datetime(2026, 8, 1, tzinfo=BEIJING).timestamp() * 1000
        data = {'activities': [[{'type': 3, 'courseware_id': 'a', 'create_time': stamp, 'title': '开学'},
                                {'type': 3, 'courseware_id': 'a', 'create_time': stamp}],
                               [{'type': 14, 'courseware_id': 'b', 'create_time': '2027-01-31'}],
                               [{'type': 2, 'courseware_id': 'ignore', 'create_time': stamp}],
                               [{'type': 3, 'courseware_id': 'old', 'create_time': stamp - 1}]]}
        self.assertEqual([r['id'] for r in lesson_rows(data, '2026-08-01', '2027-01-31')], ['a', 'b'])
        with self.assertRaises(ReviewError):
            lesson_rows({'activities': [[{'type': 3, 'courseware_id': 'a'}]]}, '2026-08-01', '2027-01-31')

    def test_unknown_log_schema_and_pagination_do_not_report_complete(self):
        with self.assertRaises(ReviewError):
            lesson_rows({}, '2026-08-01', '2027-01-31')
        client = ReviewClient('dummy', 'a')
        try:
            with patch.object(client, 'get', return_value={'activities': [], 'has_more': True}):
                with self.assertRaises(ReviewError):
                    client.lessons(COURSE, '2026-08-01', '2027-01-31')
        finally:
            client.close()

    def test_resource_forbidden_is_distinct_from_expired_login(self):
        client = ReviewClient('dummy', 'a')
        try:
            with patch.object(client.http, 'get', return_value=SimpleNamespace(status_code=403)):
                with self.assertRaises(ReviewError):
                    client.get('/api/v3/classroom-report/student/detail', lesson_id='lesson')
                with self.assertRaises(PermissionError):
                    client.verify()
        finally:
            client.close()

    def test_polls_do_not_count_as_missing_standard_answers(self):
        self.assertFalse(missing_official({'type': 3, 'official_answer': []}))
        self.assertTrue(missing_official({'type': 5, 'official_answer': []}))
        self.assertFalse(missing_official({'type': 1, 'official_answer': ['A']}))

    def test_course_failure_does_not_stop_other_courses(self):
        original = self.client.lessons
        def courses(course, start, end):
            if course['classroom_id'] == 'class-a':
                raise ReviewError('课程读取失败')
            return original(course, start, end)
        self.client.lessons = courses
        other = dict(COURSE, classroom_id='class-b', name='第二门课')
        result = ReviewCollector(self.client, self.store).collect([COURSE, other], '2026-08-01', '2027-01-31')
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['complete'], 1)
        self.assertEqual(len(self.store.list('class-b')), 1)
        self.assertEqual(self.store.list('class-a'), [])

    def test_empty_personal_photo_is_not_reported_as_missing_courseware(self):
        original = self.client.question
        def question(*args):
            result = original(*args)
            if args[-1] == 'q2':
                result['problem']['result']['pics'] = [{'pic': None}]
            return result
        self.client.question = question
        report = self.collect()
        self.assertEqual(report['images'], 1)
        self.assertEqual(report['partial'], 1)
        self.assertIn('原页码 P20 的个人作答附件缺少图片地址', report['errors'][0])

    def test_course_url_is_strict_and_credentials_never_reach_asset_session(self):
        course = parse_course_url('https://www.yuketang.cn/m/v2/course/normalcourse/logs/12/34')
        self.assertEqual(course['classroom_id'], '34')
        for url in ['https://evil.test/m/v2/course/normalcourse/logs/12/34', 'http://www.yuketang.cn/m/v2/course/normalcourse/logs/12/34']:
            with self.assertRaises(ReviewError):
                parse_course_url(url)
        client = ReviewClient('sensitive-dummy', 'a')
        try:
            self.assertFalse(client.media.cookies)
            self.assertNotIn('Cookie', client.media.headers)
            with self.assertRaises(ReviewError):
                client.image('https://127.0.0.1/private', self.store.root)
            with patch.object(client, 'get', return_value={'id': 'other'}):
                with self.assertRaises(PermissionError):
                    client.verify()
        finally:
            client.close()

    def test_ai_only_supplements_missing_answers_without_changing_sources(self):
        self.collect()
        record = self.store.list()[0]
        # 此测试仅测来源与幂等性，不读取虚拟图片。
        record['questions'][1]['image'] = ''
        self.store.save(record)
        config = {'enabled': True, 'api_key': 'test', 'base_url': 'https://example.test', 'model': 'test'}
        with patch('Scripts.LLM.ask_llm', return_value={'answers': ['AI 建议'], 'explanation': 'AI 解析'}) as ask:
            result = supplement_ai(self.store, self.store.list(), config, threading.Event())
            self.assertEqual(result['generated'], 1)
            self.assertEqual(ask.call_count, 1)
            supplement_ai(self.store, self.store.list(), config, threading.Event())
            self.assertEqual(ask.call_count, 1)
        a, b = self.store.list()[0]['questions']
        self.assertEqual(a['official_answer'], ['A'])
        self.assertEqual(b['official_answer'], [])
        self.assertEqual(b['ai_answer'], ['AI 建议'])
        self.assertEqual(b['personal_answer'], ['我的作答'])


if __name__ == '__main__':
    unittest.main()
