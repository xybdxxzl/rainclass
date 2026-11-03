"""题目收到即建立的可编辑记录；模型建议与人工答案相互独立。"""
import hashlib
import threading
import time
import uuid


class AnswerReview:
    def __init__(self, course, problem, problemtype, answers, seconds, auto_submit=True,
                 question_deadline=None, result=None, account_id='', lesson_id='', problem_id='',
                 history=None, on_change=None):
        self.course, self.problem, self.problemtype = course, problem, problemtype
        self.answers = list(answers or [])
        self.deadline = time.monotonic() + seconds
        self.event = threading.Event()
        self.lock = threading.RLock()
        self.cancelled = False
        self.auto_submit = bool(auto_submit and self.answers)
        self.auto_requested = auto_submit
        self.question_deadline = question_deadline
        self.result = result or {}
        self.revision = 0
        self.valid_input = bool(self.answers)
        self.account_id, self.lesson_id, self.problem_id = str(account_id), str(lesson_id), str(problem_id)
        self.id = hashlib.sha256(('%s|%s|%s' % (account_id, lesson_id, problem_id)).encode()).hexdigest() if problem_id else uuid.uuid4().hex
        self.received_at, self.started = time.time(), time.monotonic()
        self.llm_state = 'success' if self.answers else 'pending'
        self.llm_error = ''
        self.status = '已收到题目，可立即作答'
        self.submission_state = 'waiting'
        self.can_answer = problemtype in (1, 2, 4, 5) and bool(problem.get('body') or problem.get('content') or problem.get('title') or problem.get('images') or self.answers)
        if problemtype in (1, 2) and not problem.get('options') and not self.answers:
            self.can_answer = False
        self.llm_started = self.llm_deadline = None
        self.history, self.on_change = history, on_change
        self.retry_model = self.retry_question = None
        self.display_duration = max(1, seconds)

    def persist(self):
        if self.history is not None:
            self.history.record(self)
        if self.on_change is not None:
            self.on_change(self)

    def set_state(self, **fields):
        with self.lock:
            for key, value in fields.items():
                setattr(self, key, value)
        self.persist()

    def update(self, answers):
        with self.lock:
            if self.event.is_set() or (self.question_deadline is not None and time.monotonic() >= self.question_deadline):
                return False
            self.answers = list(answers)
            self.revision += 1
            self.valid_input = bool(self.answers) and all(str(x).strip() for x in self.answers)
            self.auto_submit = False
            self.auto_requested = False
        self.persist()
        return True

    def mark_invalid(self):
        with self.lock:
            if self.event.is_set():
                return
            self.valid_input = False
            self.auto_submit = False
            self.auto_requested = False
            self.revision += 1
        self.persist()

    def pause_auto(self):
        with self.lock:
            if self.event.is_set():
                return False
            self.auto_submit = False
            self.auto_requested = False
        self.persist()
        return True

    def apply_model(self, result, revision, seconds):
        with self.lock:
            if self.event.is_set() or (self.question_deadline is not None and time.monotonic() >= self.question_deadline):
                return False
            self.result = result
            self.llm_state = 'success'
            self.llm_error = ''
            self.status = '模型建议已生成；人工选择优先'
            if self.revision == revision and self.revision == 0:
                self.answers = list(result['answers'])
                self.valid_input = bool(self.answers)
                self.auto_submit = bool(self.auto_requested and seconds > 0)
                self.deadline = time.monotonic() + max(0, seconds)
                self.display_duration = max(1, seconds)
        self.persist()
        return True

    def update_if_unchanged(self, answers, result, revision):
        with self.lock:
            if (self.event.is_set() or self.revision != revision or not self.valid_input
                    or (self.problemtype == 4 and self.answers and len(answers) != len(self.answers))):
                return False
            self.answers = list(answers)
            self.result = result
            self.revision += 1
        self.persist()
        return True

    def finish(self, cancel=False):
        with self.lock:
            if self.event.is_set() or (not cancel and (not self.valid_input or not self.can_answer)):
                return False
            if not cancel and self.question_deadline is not None and time.monotonic() >= self.question_deadline:
                return False
            self.cancelled = cancel
            self.submission_state = ('expired' if self.question_deadline is not None and time.monotonic() >= self.question_deadline else 'cancelled') if cancel else 'submitting'
            self.status = '本题已取消' if cancel else '正在提交当前答案'
            if self.llm_state in ('pending', 'processing', 'retrying'):
                self.llm_state = 'cancelled'
            self.event.set()
        self.persist()
        return True

    def finish_if_auto(self):
        with self.lock:
            if self.event.is_set() or not self.auto_submit or not self.valid_input:
                return False
            if self.question_deadline is not None and time.monotonic() >= self.question_deadline:
                return False
            self.cancelled = False
            self.submission_state = 'submitting'
            self.status = '正在提交当前答案'
            self.event.set()
        self.persist()
        return True


def review_seconds(limit, elapsed, configured=15):
    seconds = max(1, int(configured))
    return seconds if limit == -1 else max(0, min(seconds, limit - elapsed - 5))
