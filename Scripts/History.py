"""按账号隔离的本地题目历史，不保存登录凭据或模型密钥。"""
import datetime
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from Scripts.Utils import get_config_dir


class HistoryStore:
    def __init__(self, path=None):
        self.path = os.fspath(path or os.path.join(get_config_dir(), 'history.sqlite3'))
        self.lock = threading.RLock()
        self._memory = sqlite3.connect(':memory:', check_same_thread=False) if self.path == ':memory:' else None
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, course TEXT NOT NULL, received_at REAL NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS account_time ON questions(account_id, received_at DESC)')

    @contextmanager
    def _connect(self):
        db = self._memory or sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            if db is not self._memory:
                db.close()

    def record(self, review):
        with self.lock, self._connect() as db:
            with review.lock:
                payload = {key: getattr(review, key, None) for key in (
                    'id', 'account_id', 'lesson_id', 'problem_id', 'course', 'received_at', 'problem',
                    'problemtype', 'answers', 'result', 'llm_state', 'llm_error', 'status', 'submission_state')}
                encoded = json.dumps(payload, ensure_ascii=False)
            db.execute('INSERT INTO questions(id,account_id,course,received_at,payload) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, course=excluded.course',
                       (review.id, review.account_id, review.course, review.received_at, encoded))

    def list(self, account_id, keyword='', course='', status='', date='', limit=200):
        clauses, args = ['account_id=?'], [str(account_id)]
        if course:
            clauses.append('course LIKE ?')
            args.append('%' + course + '%')
        if date:
            try:
                start = datetime.datetime.strptime(date, '%Y-%m-%d').timestamp()
                clauses.append('received_at>=? AND received_at<?')
                args.extend((start, start + 86400))
            except ValueError:
                return []
        with self.lock, self._connect() as db:
            rows = db.execute('SELECT payload FROM questions WHERE ' + ' AND '.join(clauses) + ' ORDER BY received_at DESC', args).fetchall()
        result = []
        for row in rows:
            item = json.loads(row[0])
            if keyword and keyword.casefold() not in json.dumps(item, ensure_ascii=False).casefold():
                continue
            if status and status not in (item.get('llm_state'), item.get('submission_state')):
                continue
            result.append(item)
            if len(result) >= min(max(1, int(limit)), 5000):
                break
        return result

    def get(self, record_id, account_id):
        with self.lock, self._connect() as db:
            row = db.execute('SELECT payload FROM questions WHERE id=? AND account_id=?', (record_id, str(account_id))).fetchone()
        return json.loads(row[0]) if row else None

    def clear(self, account_id):
        with self.lock, self._connect() as db:
            db.execute('DELETE FROM questions WHERE account_id=?', (str(account_id),))
