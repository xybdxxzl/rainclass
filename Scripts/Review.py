"""期末复习：只读历史采集、按账号归档，与课堂提交流程独立。"""
import base64
import copy
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import requests


ORIGIN = 'https://www.yuketang.cn'
BEIJING = dt.timezone(dt.timedelta(hours=8))


class ReviewError(RuntimeError):
    pass


class Cancelled(ReviewError):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled('已取消；已采集内容保留，可重新采集补齐')


def missing_official(question):
    return question.get('type') != 3 and not question.get('official_answer')


def plain(value):
    parser = RichText()
    parser.feed(str(value or ''))
    return ''.join(parser.text).strip()


class RichText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.images = [], []
        self.blocked = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.blocked += 1
        if self.blocked:
            return
        attrs = dict(attrs)
        if tag == 'img' and (attrs.get('data-src') or attrs.get('src')):
            self.images.append(attrs.get('data-src') or attrs['src'])
        if tag in ('br', 'p', 'div', 'li'):
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.blocked = max(0, self.blocked - 1)
        elif tag in ('p', 'div', 'li'):
            self.text.append('\n')

    def handle_data(self, value):
        if not self.blocked:
            self.text.append(value)


def day(value):
    if not value:
        return ''
    try:
        if isinstance(value, (int, float)) or re.fullmatch(r'\d+(\.\d+)?', str(value)):
            value = float(value)
            return dt.datetime.fromtimestamp(value / 1000 if value > 10**11 else value, BEIJING).date().isoformat()
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, OverflowError, OSError):
        return ''


def parse_course_url(value):
    parsed = urlsplit(value.strip())
    match = re.fullmatch(r'/m/v2/course/normalcourse/logs/(\d+)/(\d+)/?', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname != 'www.yuketang.cn' or not match:
        raise ReviewError('请填写雨课堂课程学习日志地址：/m/v2/course/normalcourse/logs/课程ID/班级ID')
    return {'course_id': match[1], 'classroom_id': match[2], 'name': '手动添加的课程'}


def lesson_rows(data, start, end):
    """官网 activities 为按日期分组的数组；只接受课堂类型 3/14。"""
    activities = data.get('activities')
    if not isinstance(activities, list):
        raise ReviewError('历史课次数据结构已变化：未找到 activities')
    found = {}

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if str(value.get('type')) in ('3', '14'):
                lesson_id = value.get('courseware_id') or value.get('lesson_id')
                if not lesson_id:
                    raise ReviewError('历史课堂缺少课次编号，无法保证收集完整')
                date = day(value.get('create_time'))
                if not date:
                    raise ReviewError('历史课堂缺少有效日期，无法按学期筛选')
                if start <= date <= end:
                    found[str(lesson_id)] = {'id': str(lesson_id), 'title': plain(value.get('title')) or '未命名课堂', 'date': date}
    visit(activities)
    return sorted(found.values(), key=lambda row: (row['date'], row['id']))


class ReviewClient:
    """以下端点和参数来自官网公开前端 1.2.354；只发起 GET 请求。"""
    def __init__(self, sessionid, account_id, cancel=None):
        self.cancel = cancel or threading.Event()
        self.account_id = str(account_id)
        self.http = requests.Session()
        self.http.trust_env = False
        self.http.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': ORIGIN + '/web?index'})
        self.http.cookies.set('sessionid', sessionid, domain='www.yuketang.cn', path='/')
        self.media = requests.Session()
        self.media.trust_env = False
        self._image_cache = {}

    def close(self):
        self.http.close()
        self.media.close()

    def get(self, path, **params):
        for attempt in range(3):
            check_cancel(self.cancel)
            try:
                response = self.http.get(ORIGIN + path, params=params, timeout=(8, 20), allow_redirects=False)
                if response.status_code in (301, 302, 401) or (response.status_code == 403 and path == '/api/v3/user/basic-info'):
                    raise PermissionError('登录已失效，请重新登录')
                if response.status_code == 403:
                    raise ReviewError('该历史资源无访问权限，请在官网确认；其他课次将继续采集')
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        if self.cancel.wait(attempt + 1):
                            check_cancel(self.cancel)
                        continue
                if response.status_code != 200:
                    raise ReviewError('雨课堂接口返回 HTTP %s' % response.status_code)
                result = response.json()
                if not isinstance(result, dict):
                    raise ReviewError('雨课堂接口返回格式异常')
                code = result.get('code', result.get('errcode', result.get('error_code', 0)))
                if code in (50000, 50001, 401) or (code == 403 and path == '/api/v3/user/basic-info'):
                    raise PermissionError('雨课堂登录已失效或访问被拒绝')
                if code == 403:
                    raise ReviewError('该历史资源无访问权限，请在官网确认；其他课次将继续采集')
                if code != 0 or result.get('success') is False:
                    raise ReviewError('雨课堂拒绝读取，错误码：%s' % code)
                if 'data' not in result:
                    raise ReviewError('雨课堂接口缺少 data，可能需要重新登录')
                return result['data']
            except (requests.RequestException, ValueError):
                if attempt == 2:
                    raise ReviewError('网络请求失败或返回内容不是有效 JSON') from None
        raise ReviewError('读取失败')

    def verify(self):
        info = self.get('/api/v3/user/basic-info')
        if not isinstance(info, dict) or str(info.get('id')) != self.account_id:
            raise PermissionError('登录账号发生变化，请关闭复习窗口后重新打开')

    def courses(self):
        self.verify()
        rows = self.get('/v/course_meta/learning_list/')
        if not isinstance(rows, list):
            raise ReviewError('课程列表结构已变化，请使用课程学习日志地址添加课程')
        courses = []
        for row in rows:
            nested = row.get('course') or {}
            cid, classroom = row.get('course_id') or nested.get('id'), row.get('classroom_id') or row.get('id')
            if cid and classroom:
                courses.append({'course_id': str(cid), 'classroom_id': str(classroom),
                    'name': plain(row.get('course_name') or nested.get('name') or '未命名课程'),
                    'classroom': plain(row.get('classroom_name') or row.get('name') or '')})
        if rows and not courses:
            raise ReviewError('课程列表缺少课程/班级编号，请使用课程地址添加')
        return courses

    def lessons(self, course, start, end):
        data = self.get('/v/course_meta/classroom_logs', course_id=course['course_id'],
                        classroom_id=course['classroom_id'], activity_type=-1)
        if not isinstance(data, dict):
            raise ReviewError('历史课次响应格式异常')
        # 当前官网一次返回所有日志；若未来返回分页，不把第一页当作完整学期。
        if data.get('has_more') or data.get('hasMore') or data.get('next') or data.get('next_cursor'):
            raise ReviewError('平台历史日志出现分页，本版无法确认完整性，请勿将本次扫描视为全部历史')
        return lesson_rows(data, start, end)

    def lesson(self, lesson_id):
        info = self.get('/api/v3/classroom-report/student/lesson-info', lesson_id=lesson_id)
        stats = self.get('/api/v3/classroom-report/student/detail', lesson_id=lesson_id)
        if not isinstance(info, dict) or not isinstance(stats, dict):
            raise ReviewError('课次详情返回格式异常')
        if info.get('oldVersion'):
            raise ReviewError('该课堂使用旧版报告，暂不支持自动采集')
        if not isinstance(stats.get('presentation'), list) or not isinstance((stats.get('problem') or {}).get('studentList'), list):
            raise ReviewError('课次详情缺少课件或习题列表，未将其当作空课堂')
        return info, stats

    def presentation(self, lesson_id, presentation_id):
        data = self.get('/api/v3/classroom-report/student/ppt', lesson_id=lesson_id, presentationId=presentation_id)
        if not isinstance(data, dict) or not isinstance(data.get('slideList'), list):
            raise ReviewError('课件页列表格式异常')
        return data['slideList']

    def question(self, lesson_id, identity_id, problem_id):
        data = self.get('/api/v3/classroom-report/problem/student-answer', lesson_id=lesson_id,
                        identityId=identity_id, problemId=problem_id)
        if not isinstance(data, dict) or not isinstance(data.get('content'), dict):
            raise ReviewError('题目详情缺少题面内容')
        return data

    def image(self, url, folder):
        check_cancel(self.cancel)
        parsed = urlsplit(urljoin(ORIGIN, str(url)))
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ReviewError('图片地址无效或不是 HTTPS')
        if not any(parsed.hostname == domain or parsed.hostname.endswith('.' + domain)
                   for domain in ('yuketang.cn', 'xuetangx.com', 'rainclassroom.com')):
            raise ReviewError('图片不在已支持的雨课堂资源域名内')
        cached = self._image_cache.get(parsed.geturl())
        if cached and (Path(folder) / cached).is_file():
            return cached
        # 媒体单独会话，不附带账号 Cookie；不把签名 URL 保存到归档。
        try:
            with self.media.get(parsed.geturl(), timeout=(8, 20), stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise ReviewError('图片下载失败（HTTP %s），可重新采集刷新地址' % response.status_code)
                if not response.headers.get('Content-Type', '').lower().startswith('image/'):
                    raise ReviewError('资源不是图片，未保存')
                content = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    check_cancel(self.cancel)
                    content.extend(chunk)
                    if len(content) > 20 * 1024 * 1024:
                        raise ReviewError('图片超过 20 MB，未保存')
        except requests.RequestException:
            raise ReviewError('图片下载网络异常，可重新采集') from None
        if not content:
            raise ReviewError('图片内容为空')
        from PyQt5.QtGui import QImage
        picture = QImage.fromData(bytes(content))
        if picture.isNull() or picture.width() * picture.height() > 80_000_000:
            raise ReviewError('图片损坏或像素尺寸过大，未保存')
        name = hashlib.sha256(content).hexdigest() + '.img'
        target = Path(folder) / 'assets' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix('.part')
            temporary.write_bytes(content)
            temporary.replace(target)
        relative = 'assets/' + name
        self._image_cache[parsed.geturl()] = relative
        return relative


class ReviewStore:
    def __init__(self, root, account_id):
        if not account_id:
            raise ReviewError('请先登录账号')
        self.account_id = str(account_id)
        self.root = Path(root) / hashlib.sha256(self.account_id.encode()).hexdigest()[:24]
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'review.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS lessons (classroom TEXT NOT NULL, lesson TEXT NOT NULL, date TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(classroom,lesson))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, record):
        with self.connect() as db:
            db.execute('INSERT INTO lessons VALUES(?,?,?,?) ON CONFLICT(classroom,lesson) DO UPDATE SET date=excluded.date,payload=excluded.payload',
                (record['classroom_id'], record['id'], record['date'], json.dumps(record, ensure_ascii=False)))

    def list(self, classroom='', start='0001-01-01', end='9999-12-31'):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM lessons WHERE (?="" OR classroom=?) AND date>=? AND date<=? ORDER BY date,lesson',
                              (classroom, classroom, start, end)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, classroom, lesson):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM lessons WHERE classroom=? AND lesson=?', (classroom, lesson)).fetchone()
        return json.loads(row[0]) if row else None


class ReviewCollector:
    def __init__(self, client, store, progress=None):
        self.client, self.store = client, store
        self.progress = progress or (lambda message: None)
        self.cancel = client.cancel

    def collect(self, courses, start, end):
        if not start or not end or start > end:
            raise ReviewError('学期起止日期无效')
        self.client.verify()
        summary = {'lessons': 0, 'complete': 0, 'partial': 0, 'failed': 0, 'questions': 0, 'polls': 0, 'missing_answers': 0, 'images': 0, 'errors': []}
        for course in courses:
            check_cancel(self.cancel)
            self.progress('扫描课程：' + course['name'])
            try:
                lessons = self.client.lessons(course, start, end)
            except (PermissionError, Cancelled):
                raise
            except ReviewError as error:
                summary['failed'] += 1
                summary['errors'].append(course['name'] + '：' + str(error))
                continue
            self.progress('%s：日期范围内发现 %d 个课次' % (course['name'], len(lessons)))
            for lesson in lessons:
                check_cancel(self.cancel)
                self.progress('采集：%s / %s / %s' % (course['name'], lesson['date'], lesson['title']))
                record = dict(lesson, course=course['name'], classroom_id=course['classroom_id'], course_id=course['course_id'],
                              presentations=[], questions=[], issues=[], status='partial', updated_at=dt.datetime.now(BEIJING).isoformat())
                try:
                    self._lesson(record)
                    record['status'] = 'partial' if record['issues'] else 'complete'
                except (PermissionError, Cancelled):
                    record['issues'].append('采集被取消或账号访问已失效，请重新采集补齐')
                    self._save_partial(record)
                    raise
                except ReviewError as error:
                    record['issues'].append(str(error))
                    record['status'] = 'failed'
                self._save_partial(record)
                summary['lessons'] += 1
                summary[record['status']] += 1
                summary['questions'] += len(record['questions'])
                summary['missing_answers'] += sum(missing_official(q) for q in record['questions'])
                summary['polls'] += sum(q.get('type') == 3 for q in record['questions'])
                summary['images'] += sum(bool(p.get('image')) for pres in record['presentations'] for p in pres['slides'])
                summary['errors'].extend(record['date'] + ' ' + record['title'] + '：' + issue for issue in record['issues'])
        return summary

    def _save_partial(self, record):
        # 失败的刷新不能抹去上次已保存的资料；仍明确保留本次失败状态。
        previous = self.store.get(record['classroom_id'], record['id'])
        if previous:
            for key in ('presentations', 'questions'):
                prior_by_id = {item['id']: item for item in previous.get(key, [])}
                old = dict(prior_by_id) if record['status'] != 'complete' else {}
                for item in record[key]:
                    prior = prior_by_id.get(item['id'])
                    if prior and item.get('error'):
                        item = dict(prior, error=item['error'])
                    elif prior and key == 'questions' and prior.get('ai_answer') and all(item.get(field) == prior.get(field) for field in ('body', 'image', 'options', 'type')):
                        item = dict(item, ai_answer=prior['ai_answer'], ai_explanation=prior.get('ai_explanation', ''))
                    old[item['id']] = item
                record[key] = list(old.values())
        self.store.save(record)

    def _image(self, url, record, context='题面或课件页'):
        if not url:
            record['issues'].append(context + '缺少图片地址')
            return ''
        try:
            return self.client.image(url, self.store.root)
        except (PermissionError, Cancelled):
            raise
        except ReviewError as error:
            record['issues'].append(context + '：' + str(error))
            return ''

    def _rich(self, value, record):
        if isinstance(value, list):
            text, images = [], []
            for item in value:
                t, pics = self._rich(item, record)
                text.append(t)
                images.extend(pics)
            return '\n'.join(t for t in text if t), images
        if isinstance(value, dict):
            if value.get('type') in ('image', 'img') and value.get('url'):
                return '', [self._image(value['url'], record)]
            return self._rich(value.get('content') or value.get('text') or '', record)
        parser = RichText()
        parser.feed(str(value or ''))
        return ''.join(parser.text).strip(), [self._image(url, record) for url in parser.images]

    def _lesson(self, record):
        info, stats = self.client.lesson(record['id'])
        for pres in stats['presentation']:
            check_cancel(self.cancel)
            pid = pres.get('presentationId')
            if not pid:
                record['issues'].append('课件缺少编号')
                continue
            entry = {'id': str(pid), 'name': plain(pres.get('name') or pres.get('title') or '课件'), 'slides': []}
            record['presentations'].append(entry)
            try:
                slides = self.client.presentation(record['id'], pid)
                for slide in slides:
                    check_cancel(self.cancel)
                    # 学生页面只展示 visible=true 的页，不保存隐藏页。
                    if slide.get('visible') not in (True, 1, '1'):
                        if 'visible' not in slide:
                            record['issues'].append('课件页缺少可见性标记，未下载该页')
                        continue
                    entry['slides'].append({'page': slide.get('index'), 'image': self._image(slide.get('cover'), record, '%s / P%s' % (entry['name'], slide.get('index'))),
                                            'has_problem': bool(slide.get('hasProblem'))})
                if pres.get('totalCount') and not entry['slides']:
                    record['issues'].append(entry['name'] + '：没有可访问页面')
            except (PermissionError, Cancelled):
                raise
            except ReviewError as error:
                entry['error'] = str(error)
                record['issues'].append(entry['name'] + '：' + str(error))
        questions = stats['problem']['studentList']
        seen = set()
        for summary in questions:
            check_cancel(self.cancel)
            pid = summary.get('problemId')
            if not pid:
                record['issues'].append('习题列表中有题目缺少编号')
                continue
            if str(pid) in seen:
                continue
            seen.add(str(pid))
            question = {'id': str(pid), 'type': summary.get('problem_type', summary.get('problemType')), 'page': summary.get('idx', summary.get('index')),
                        'official_answer': [], 'personal_answer': [], 'body': '', 'images': [], 'options': []}
            record['questions'].append(question)
            try:
                identity = info.get('identityId')
                if not identity:
                    raise ReviewError('缺少本账号课堂身份编号，未请求其他学生的答案')
                detail = self.client.question(record['id'], identity, pid)
                content, personal = detail['content'], detail.get('problem') or {}
                question['type'] = int(summary.get('problem_type') or summary.get('problemType') or content.get('problemType') or 0)
                question['page'] = content.get('index', question['page'])
                question['body'], question['body_images'] = self._rich(content.get('body') or content.get('title'), record)
                question['image'] = self._image(content.get('cover'), record) if content.get('cover') else ''
                for index, option in enumerate(content.get('options') or []):
                    option = option if isinstance(option, dict) else {'value': str(option)}
                    text, images = self._rich(option.get('value') or option.get('content'), record)
                    question['options'].append({'key': str(option.get('key') or chr(65 + index)), 'text': text, 'images': images})
                # 与官网学生详情一致：content.answer 用于选择、填空的官方答案。
                answer = content.get('answer')
                if question['type'] in (1, 2, 4) and answer:
                    question['official_answer'] = answer if isinstance(answer, list) else [str(answer)]
                question['official_explanation'], question['explanation_images'] = self._rich(content.get('remarkRich') or content.get('remark'), record)
                result = personal.get('result')
                if isinstance(result, dict):
                    question['personal_answer'] = [plain(result.get('content'))] if result.get('content') else []
                    for pic in result.get('pics') or []:
                        url = pic.get('pic') if isinstance(pic, dict) else pic
                        question['images'].append(self._image(url, record, '原页码 P%s 的个人作答附件' % question['page']))
                elif isinstance(result, list):
                    question['personal_answer'] = result
                elif result is not None:
                    question['personal_answer'] = [str(result)]
                if not question['body'] and not question['image']:
                    raise ReviewError('题面缺失，不能作为完整题目导出')
            except (PermissionError, Cancelled):
                raise
            except (ReviewError, ValueError, TypeError):
                question['error'] = '题目详情获取或解析失败，可重新采集'
                record['issues'].append('题目 %s：%s' % (pid, question['error']))


def supplement_ai(store, records, config, cancel, progress=None):
    """仅在用户主动勾选并启动后调用；从不修改官方答案和个人作答。"""
    from Scripts.LLM import ask_llm, route_config
    if not config.get('enabled') or not all(config.get(key) for key in ('api_key', 'base_url', 'model')):
        raise ReviewError('请先在设置中启用并配置大模型')
    generated, failed = 0, 0
    progress = progress or (lambda message: None)
    for original in records:
        record = copy.deepcopy(original)
        for question in record.get('questions', []):
            check_cancel(cancel)
            if question.get('official_answer') or question.get('ai_answer') or question.get('error'):
                continue
            if question.get('type') not in (1, 2, 4, 5):
                continue
            progress('AI 补充：%s / 原页码 %s' % (record['title'], question.get('page', '?')))
            try:
                problem = {'body': question.get('body', ''), 'options': [dict(key=o['key'], value=o['text']) for o in question.get('options', [])], 'images': []}
                for path in [question.get('image')] + question.get('body_images', []) + [p for o in question.get('options', []) for p in o.get('images', [])]:
                    if path:
                        local = (store.root / path).resolve()
                        if store.root.resolve() not in local.parents:
                            raise ReviewError('题目图片路径不属于当前账号归档')
                        from PyQt5 import QtCore, QtGui
                        picture = QtGui.QImage(str(local))
                        if picture.isNull():
                            raise ReviewError('题目图片缺失，请重新采集')
                        buffer = QtCore.QBuffer()
                        buffer.open(QtCore.QIODevice.WriteOnly)
                        picture.save(buffer, 'PNG')
                        problem['images'].append('data:image/png;base64,' + base64.b64encode(bytes(buffer.data())).decode())
                ask_type = question['type']
                if ask_type in (1, 2) and not problem['options']:
                    # 图片题没有结构化选项时，只请求可读参考答案，绝不虚构选项用于校验。
                    problem['body'] = '这是一道%s。请识别题面，给出选项字母及解释，作为复习参考。\n%s' % ('单选题' if ask_type == 1 else '多选题', problem['body'])
                    ask_type = 5
                budget = max(5, min(120, float(config.get('timeout', 30))))
                result = ask_llm(problem, ask_type, route_config(problem, config), time_budget=budget, return_details=True)
                check_cancel(cancel)
                if not result or not result.get('answers'):
                    raise ReviewError('未返回有效参考答案')
                question['ai_answer'] = result['answers']
                question['ai_explanation'] = result.get('explanation', '')
                question.pop('ai_error', None)
                generated += 1
            except Cancelled:
                raise
            except Exception:
                question['ai_error'] = '模型未能生成有效参考答案，可重试'
                failed += 1
            store.save(record)
    return {'generated': generated, 'failed': failed}
