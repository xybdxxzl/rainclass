import tempfile
import threading
from pathlib import Path
import unittest
from Scripts.AnswerReview import AnswerReview
from Scripts.History import HistoryStore


class HistoryTests(unittest.TestCase):
    def test_history_survives_reopen_and_isolates_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'history.db'
            store = HistoryStore(path)
            for account in ('a', 'b'):
                review = AnswerReview('数学', {'body':'求导题目'}, 5, [], 0,
                    account_id=account, lesson_id='lesson', problem_id='same', history=store)
                review.set_state(llm_state='timeout', llm_error='请求超时')
            reopened = HistoryStore(path)
            rows = reopened.list('a', keyword='求导', course='数学', status='timeout')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['llm_error'], '请求超时')
            self.assertIsNone(reopened.get(rows[0]['id'], 'b'))
            self.assertEqual(reopened.list('a', keyword='不存在'), [])
            self.assertEqual(len(reopened.list('b')), 1)

    def test_in_memory_store_supports_worker_threads(self):
        store = HistoryStore(':memory:')
        review = AnswerReview('课', {'body':'题目'}, 5, [], 0, account_id='a', history=store)
        worker = threading.Thread(target=lambda: review.set_state(llm_state='failed'))
        worker.start(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(store.list('a')[0]['llm_state'], 'failed')
        store.clear('a')
        self.assertEqual(store.list('a'), [])

    def test_latest_manual_state_is_saved_and_no_credentials_are_stored(self):
        store = HistoryStore(':memory:')
        review = AnswerReview('课', {'body':'题目'}, 5, [], 0, account_id='a', history=store)
        review.update(['人工答案'])
        review.apply_model({'answers':['模型建议']}, 0, 10)
        item = store.get(review.id, 'a')
        self.assertEqual(item['answers'], ['人工答案'])
        self.assertEqual(item['result']['answers'], ['模型建议'])
        self.assertNotIn('api_key', item)
        self.assertNotIn('sessionid', item)
