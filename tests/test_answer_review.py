import unittest
from concurrent.futures import ThreadPoolExecutor
from Scripts.AnswerReview import AnswerReview, review_seconds


class ReviewTests(unittest.TestCase):
    def test_budget(self):
        self.assertEqual(review_seconds(120, 5), 15)
        self.assertEqual(review_seconds(20, 10), 5)
        self.assertEqual(review_seconds(10, 8), 0)
        self.assertEqual(review_seconds(-1, 300), 15)

    def test_finish_once_even_when_racing(self):
        review = AnswerReview('课程', {}, 1, ['A'], 15)
        review.update(['B'])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: review.finish(), range(20)))
        self.assertEqual(sum(results), 1)
        self.assertFalse(review.update(['C']))
        self.assertEqual(review.answers, ['B'])

    def test_cancel_wakes_worker(self):
        review = AnswerReview('课程', {}, 1, ['A'], 15)
        review.finish(cancel=True)
        self.assertTrue(review.event.wait(0))
        self.assertTrue(review.cancelled)
