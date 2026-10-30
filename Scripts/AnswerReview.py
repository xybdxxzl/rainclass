"""答题线程与界面共享的待确认答案；只允许完成一次。"""
import threading
import time


class AnswerReview:
    def __init__(self, course, problem, problemtype, answers, seconds):
        self.course, self.problem, self.problemtype = course, problem, problemtype
        self.answers = list(answers)
        self.deadline = time.monotonic() + seconds
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.cancelled = False

    def update(self, answers):
        with self.lock:
            if self.event.is_set():
                return False
            self.answers = list(answers)
            return True

    def finish(self, cancel=False):
        with self.lock:
            if self.event.is_set():
                return False
            self.cancelled = cancel
            self.event.set()
            return True


def review_seconds(limit, elapsed, configured=15):
    seconds = max(1, int(configured))
    return seconds if limit == -1 else max(0, min(seconds, limit - elapsed - 5))
