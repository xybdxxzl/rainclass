"""答题线程与界面共享的待确认答案；只允许完成一次。"""
import threading
import time


class AnswerReview:
    def __init__(self, course, problem, problemtype, answers, seconds, auto_submit=True, question_deadline=None, result=None):
        self.course, self.problem, self.problemtype = course, problem, problemtype
        self.answers = list(answers)
        self.deadline = time.monotonic() + seconds
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.cancelled = False
        self.auto_submit = auto_submit
        self.question_deadline = question_deadline
        self.result = result or {}
        self.revision = 0
        self.valid_input = True

    def update(self, answers):
        with self.lock:
            if self.event.is_set():
                return False
            self.answers = list(answers)
            self.revision += 1
            self.valid_input = True
            return True

    def mark_invalid(self):
        with self.lock:
            if not self.event.is_set():
                self.valid_input = False
                self.revision += 1

    def pause_auto(self):
        with self.lock:
            if self.event.is_set():
                return False
            self.auto_submit = False
            return True

    def update_if_unchanged(self, answers, result, revision):
        with self.lock:
            if (self.event.is_set() or self.revision != revision or not self.valid_input
                    or (self.problemtype == 4 and len(answers) != len(self.answers))):
                return False
            self.answers = list(answers)
            self.result = result
            self.revision += 1
            return True

    def finish(self, cancel=False):
        with self.lock:
            if self.event.is_set():
                return False
            self.cancelled = cancel
            self.event.set()
            return True

    def finish_if_auto(self):
        with self.lock:
            if self.event.is_set() or not self.auto_submit:
                return False
            self.event.set()
            return True


def review_seconds(limit, elapsed, configured=15):
    seconds = max(1, int(configured))
    return seconds if limit == -1 else max(0, min(seconds, limit - elapsed - 5))
