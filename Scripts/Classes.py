import requests
import threading
import time
import random
import websocket
import json
import uuid
import math
from Scripts.Utils import get_user_info, dict_result, calculate_waittime
from Scripts.LLM import ask_llm, route_config, bounded_call, classify_error
from Scripts.AnswerReview import AnswerReview, review_seconds
from Scripts.History import HistoryStore

wss_url = "wss://www.yuketang.cn/wsapp/"
class Lesson:
    def __init__(self,lessonid,lessonname,classroomid,main_ui):
        self.classroomid = classroomid
        self.lessonid = lessonid
        self.lessonname = lessonname
        self.sessionid = main_ui.config["sessionid"]
        self.generation = getattr(main_ui, 'monitor_generation', None)
        self.headers = {
            "Cookie":"sessionid=%s" % self.sessionid,
            "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:97.0) Gecko/20100101 Firefox/97.0",
        }
        self.receive_danmu = {}
        self.sent_danmu_dict = {}
        self.danmu_dict = {}
        # 按problemId缓存的题面结构（题面只存在于课件中，答案一律交由大模型生成）
        self.problems_dict = {}
        # 本次运行内已派发过作答的题目id，用于防止重复提交
        self.answered_problem_ids = set()
        self.reviews = {}
        self.presentation_ids = set()
        self.closed = False
        self.unlocked_problem = []
        self.classmates_ls = []
        self.add_message = main_ui.add_message_signal.emit
        self.add_course = main_ui.add_course_signal.emit
        self.del_course = main_ui.del_course_signal.emit
        self.config = main_ui.config
        code, rtn = get_user_info(self.sessionid)
        if code != 0:
            raise PermissionError('登录已失效，请重新登录')
        self.user_uid = rtn["id"]
        self.user_uname = rtn["name"]
        self.main_ui = main_ui
        self.history = getattr(main_ui, 'history_store', None) or HistoryStore()
        self._state(kind='account', account_id=str(self.user_uid), name=self.user_uname, status='verified')

    def _state(self, **data):
        if self.main_ui.config.get('sessionid') != self.sessionid:
            return
        if getattr(self, 'generation', None) is not None and self.generation != getattr(self.main_ui, 'monitor_generation', None):
            return
        signal = getattr(self.main_ui, 'state_signal', None)
        if signal is not None:
            data.setdefault('course', self.lessonname)
            data.setdefault('lesson_id', str(self.lessonid))
            data.setdefault('account_id', str(self.user_uid))
            data.setdefault('generation', getattr(self, 'generation', None))
            try:
                signal.emit(data)
            except RuntimeError:
                pass  # 退出时窗口可能已经释放，历史仍保存在本机。

    def _changed(self, review):
        self._state(kind='question', review=review)

    def _running(self):
        return (self.main_ui.is_active and not self.closed and self.main_ui.config.get('sessionid') == self.sessionid
                and (getattr(self, 'generation', None) is None or self.generation == getattr(self.main_ui, 'monitor_generation', None)))

    def _get_ppt(self,presentationid):
        # 获取课程各页ppt
        r = requests.get(url="https://www.yuketang.cn/api/v3/lesson/presentation/fetch?presentation_id=%s" % (presentationid),headers=self.headers,proxies={"http": None,"https":None}, timeout=10)
        r.raise_for_status()
        return dict_result(r.text)["data"]

    def get_problems(self,presentationid):
        # 获取课程ppt中的题目
        data = self._get_ppt(presentationid)
        return [problem["problem"] for problem in data["slides"] if "problem" in problem.keys()]

    def _cache_problems(self,presentationid):
        # 缓存题面结构（problemType/body/options/blanks/images等），按problemId索引
        # 注意：课件中的answers字段不再用于答案匹配，一切答案均由大模型生成
        self.presentation_ids.add(presentationid)
        for problem in self.get_problems(presentationid):
            self.problems_dict[str(problem["problemId"])] = problem
        for review in list(self.reviews.values()):
            if not review.can_answer and not review.event.is_set():
                self._refresh_question(review, {})

    def _cache_async(self, presentationid):
        def work():
            try:
                self._cache_problems(presentationid)
            except Exception:
                self._state(kind='monitor', state='warning', status='课堂已连接，课件题面获取失败，可重试')
                for review in list(self.reviews.values()):
                    if not review.can_answer and not review.event.is_set():
                        review.set_state(status='已收到题目，题面获取失败，可重新获取')
        threading.Thread(target=work, daemon=True).start()

    def _extract_problem(self,data,problemid):
        # 合并WebSocket报文与课件缓存，得到统一的题目结构
        # WS推送/回包很可能只有sid+limit，题面以课件缓存为准；WS若带了更全的字段则优先
        ws = data.get("problem") if isinstance(data.get("problem"),dict) else data
        cached = self.problems_dict.get(str(problemid), self.problems_dict.get(problemid, {}))

        def pick(key):
            for src in (ws,cached):
                value = src.get(key)
                if value:
                    return value
            return None

        return {
            "problemType": pick("problemType") or pick("type"),
            "body": pick("body") or pick("content") or pick("title") or "",
            "options": pick("options") or [],
            "blanks": pick("blanks") or [],
            "images": pick("images") or [],
        }

    def answer_questions(self,problemid,problemtype,answer,limit,elapsed=0,immediate=False):
        # 回答问题
        # elapsed: 生成答案（如请求大模型）已经消耗的时间，需要从等待时长中扣除，避免超过题目时限
        if limit != -1 and elapsed >= limit:
            self._submission_state(problemid, 'expired', '题目已截止，未提交')
            self.add_message("%s题目已超时，未提交答案，请前往雨课堂查看" % self.lessonname,4)
            return False
        if answer and problemtype != 3:
            wait_time = 0 if immediate else max(0, calculate_waittime(int(limit), self.config["answer_config"]["answer_delay"]["type"], self.config["answer_config"]["answer_delay"]["custom"]["time"]) - int(elapsed))
            if wait_time != 0:
                meg = "%s检测到问题，将在%s秒后自动回答，答案为%s" % (self.lessonname,wait_time,answer)
                # threading.Thread(target=say_something,args=(meg,)).start()
                self.add_message(meg,3)
                time.sleep(wait_time)
            else:
                meg = "%s检测到问题，将立即自动回答，答案为%s" % (self.lessonname,answer)
                self.add_message(meg,3)
                # threading.Thread(target=say_something,args=(meg,)).start()
            data = {"problemId":problemid,"problemType":problemtype,"dt":int(time.time()),"result":answer}
            try:
                remaining = 10 if limit == -1 else max(.1, min(10, limit - elapsed))
                r = requests.post(url="https://www.yuketang.cn/api/v3/lesson/problem/answer",headers=self.headers,data=json.dumps(data),proxies={"http": None,"https":None}, timeout=remaining)
                r.raise_for_status()
                return_dict = dict_result(r.text)
                if 'code' not in return_dict:
                    raise ValueError('服务器未返回提交结果')
            except (requests.RequestException, ValueError, TypeError) as e:
                self._submission_state(problemid, 'unknown', '提交结果未确认，请到雨课堂核对；不会重复提交')
                self.add_message('%s提交结果未确认：%s，请到雨课堂核对，程序不会重复提交' % (self.lessonname, e), 4)
                return False
            if return_dict["code"] == 0:
                self._submission_state(problemid, 'submitted', '答案提交成功')
                meg = "%s自动回答成功" % self.lessonname
                self.add_message(meg,4)
                self.main_ui.metrics_signal.emit({'submitted': 1})
                # threading.Thread(target=say_something,args=(meg,)).start()
                return True
            else:
                self._submission_state(problemid, 'failed', '答案提交失败，请到雨课堂核对')
                meg = "%s自动回答失败，原因：%s" % (self.lessonname,str(return_dict.get("msg") or '服务器拒绝本次答案').replace("_"," "))
                self.add_message(meg,4)
                # threading.Thread(target=say_something,args=(meg,)).start()
                return False
        else:
            if limit == -1:
                meg = "%s的问题没有找到答案，该题不限时，请尽快前往雨课堂回答" % (self.lessonname)
            else:
                meg = "%s的问题没有找到答案，请在%s秒内前往雨课堂回答" % (self.lessonname,limit)
            # threading.Thread(target=say_something,args=(meg,)).start()
            self.add_message(meg,4)
            return False

    def _submission_state(self, problemid, state, status):
        review = getattr(self, 'reviews', {}).get(str(problemid))
        if review is not None:
            review.set_state(submission_state=state, status=status)
    
    def on_open(self, wsapp):
        self._state(kind='monitor', state='connected', status='课堂连接已建立，正在初始化', last_message=time.time())
        self.handshark = {"op":"hello","userid":self.user_uid,"role":"student","auth":self.auth,"lessonid":self.lessonid}
        wsapp.send(json.dumps(self.handshark))

    def checkin_class(self):
        r = requests.post(url="https://www.yuketang.cn/api/v3/lesson/checkin",headers=self.headers,data=json.dumps({"source":5,"lessonId":self.lessonid}),proxies={"http": None,"https":None}, timeout=10)
        r.raise_for_status()
        set_auth = r.headers.get("Set-Auth",None)
        times = 1
        while not set_auth and times <= 3:
            set_auth = r.headers.get("Set-Auth",None)
            times += 1
            time.sleep(1)
        self.headers["Authorization"] = "Bearer %s" % set_auth
        return dict_result(r.text)["data"]["lessonToken"]

    def on_message(self, wsapp, message):
        try:
            data = dict_result(message)
            op = data.get('op')
        except (ValueError, TypeError):
            self._state(kind='monitor', state='warning', status='收到无法解析的课堂消息，等待后续消息')
            return
        self._state(kind='monitor', last_message=time.time())
        if op == "hello":
            self._state(kind='monitor', state='listening', status='课堂已就绪，正在等待题目')
            presentations = list(set([slide["pres"] for slide in data.get("timeline", []) if slide.get("type")=="slide" and slide.get('pres') is not None]))
            current_presentation = data.get("presentation")
            if current_presentation not in presentations:
                presentations.append(current_presentation)
            for presentationid in presentations:
                if presentationid is not None:
                    self._cache_async(presentationid)
            self.unlocked_problem = data.get("unlockedproblem", [])
            for problemid in self.unlocked_problem:
                self._current_problem(wsapp, problemid)
        elif op == "unlockproblem":
            # 原始报文，供实测核对推送字段
            self.add_message("RAW %s: %s" % (op,json.dumps(data,ensure_ascii=False)),0)
            pushed = data.get('problem') or {}
            self.start_answer(pushed.get('sid') or pushed.get('problemId') or data.get('problemid'), pushed.get('limit'), problem=pushed)
        elif op == "lessonfinished":
            meg = "%s下课了" % self.lessonname
            # threading.Thread(target=say_something,args=(meg,)).start()
            self.add_message(meg,7)
            self.closed = True
            wsapp.close()
        elif op == "presentationupdated":
            self._cache_async(data["presentation"])
        elif op == "presentationcreated":
            self._cache_async(data["presentation"])
        elif op == "newdanmu" and self.config["auto_danmu"]:
            current_content = data["danmu"].lower()
            uid = data["userid"]
            sent_danmu_user = User(uid)
            if sent_danmu_user in self.classmates_ls:
                for i in self.classmates_ls:
                    if i == sent_danmu_user:
                        meg = "%s课程的%s%s发送了弹幕：%s" %(self.lessonname,i.sno,i.name,data["danmu"])
                        self.add_message(meg,2)
                        break
            else:
                self.classmates_ls.append(sent_danmu_user)
                sent_danmu_user.get_userinfo(self.classroomid,self.headers)
                meg = "%s课程的%s%s发送了弹幕：%s" %(self.lessonname,sent_danmu_user.sno,sent_danmu_user.name,data["danmu"])
                self.add_message(meg,2)
            now = time.time()
            # 收到一条弹幕，尝试取出其之前的所有记录的列表，取不到则初始化该内容列表
            try:
                same_content_ls = self.danmu_dict[current_content]
            except KeyError:
                self.danmu_dict[current_content] = []
                same_content_ls = self.danmu_dict[current_content]
            # 清除超过60秒的弹幕记录
            for i in same_content_ls:
                if now - i > 60:
                    same_content_ls.remove(i)
            # 如果当前的弹幕没被发过，或者已发送时间超过60秒
            if current_content not in self.sent_danmu_dict.keys() or now - self.sent_danmu_dict[current_content] > 60:
                if len(same_content_ls) + 1 >= self.config["danmu_config"]["danmu_limit"]:
                    self.send_danmu(current_content)
                    same_content_ls = []
                    self.sent_danmu_dict[current_content] = now
                else:
                    same_content_ls.append(now)
        elif op == "callpaused":
            meg = "%s点名了，点到了：%s" % (self.lessonname, data["name"])
            if self.user_uname == data["name"]:
                self.add_message(meg,5)
            else:
                self.add_message(meg,6)
        # 程序在上课中途运行，由_current_problem发送的已解锁题目数据，得到的返回值。
        # 此处需要筛选未到期的题目进行回答。
        elif op == "probleminfo":
            # 原始报文，供实测核对回包字段
            self.add_message("RAW %s: %s" % (op,json.dumps(data,ensure_ascii=False)),0)
            problemid = data.get("problemid")
            try:
                limit = float(data.get('limit'))
            except (TypeError, ValueError):
                limit = None
            now = data.get("now")
            dt = data.get("dt")
            try:
                time_left = -1 if limit == -1 else max(0, limit - (float(now) - float(dt)) / 1000)
            except (TypeError, ValueError):
                time_left = None
            if time_left is None:
                self.start_answer(problemid, None, problem=self._extract_problem(data, problemid))
                return
            # 筛选未到期题目
            if time_left > 0 or limit == -1:
                self.start_answer(problemid,time_left,problem=self._extract_problem(data,problemid))
            else:
                meg = "%s检测到已过期的题目，已忽略" % (self.lessonname)
                self.add_message(meg,0)
                self.start_answer(problemid, 0, problem=self._extract_problem(data, problemid))

    def _llm_fallback_hint(self, limit, elapsed=0):
        # 找不到答案时的原有提示
        if limit == -1:
            meg = "%s的问题没有找到答案，该题不限时，请尽快前往雨课堂回答" % (self.lessonname)
        elif limit <= elapsed:
            meg = "%s题目已超时，未提交答案，请前往雨课堂查看" % self.lessonname
        else:
            meg = "%s的问题没有找到答案，请在%s秒内前往雨课堂回答" % (self.lessonname,max(0, int(limit - elapsed)))
        self.add_message(meg,4)

    def _answer_by_llm(self, problem, problemid, problemtype, limit, review=None):
        # 作答上下文已提前存在；此线程只生成建议，不等候人工提交。
        review = review or self.reviews.get(str(problemid))
        if review is None or review.event.is_set() or not review.can_answer:
            return
        llm_cfg = route_config(problem, self.config.get('llm_config', {}))
        if not llm_cfg.get('enabled') or not self.config.get('auto_answer', True):
            review.set_state(llm_state='disabled', status='模型答题未开启，可直接手动作答')
            return
        if not all(str(llm_cfg.get(key) or '').strip() for key in ('base_url', 'api_key', 'model')):
            review.set_state(llm_state='failed', llm_error='模型配置不完整，请检查接口地址、密钥和模型名称', status='模型配置不完整，可继续手动作答')
            return
        with review.lock:
            if review.llm_state in ('processing', 'retrying'):
                return
            revision = review.revision
            budget = max(1, min(600, float(llm_cfg.get('total_timeout', llm_cfg.get('timeout', 30)))))
            if review.question_deadline is not None:
                budget = min(budget, max(0, review.question_deadline - time.monotonic() - 5))
            review.llm_started = time.monotonic()
            review.llm_deadline = review.llm_started + budget
            review.llm_state = 'processing'
            review.status = '题目已收到，可立即作答；模型处理中'
            review.llm_error = ''
        review.persist()
        if budget <= 0:
            review.set_state(llm_state='timeout', status='剩余时间不足以请求模型，请立即手动作答')
            return
        self.add_message('%s题目已收到，模型正在后台生成建议' % self.lessonname, 0)
        def on_retry(message):
            if not review.event.is_set() and review.llm_state in ('processing', 'retrying') and time.monotonic() < review.llm_deadline:
                review.set_state(llm_state='retrying', status=message)
        try:
            result = bounded_call(lambda: ask_llm(problem, problemtype, llm_cfg,
                time_budget=budget, on_retry=on_retry, return_details=True), budget)
            if not self._running() or review.event.is_set():
                return
            details = result if isinstance(result, dict) else {'answers': result}
            if not details.get('answers'):
                raise RuntimeError('模型未返回有效答案，返回格式异常')
            if time.monotonic() >= review.llm_deadline:
                raise requests.Timeout('模型整体等待超时')
            lower = max(1, int(self.config.get('answer_delay_min', self.config.get('answer_review_seconds', 15))))
            upper = max(lower, int(self.config.get('answer_delay_max', lower)))
            seconds = review_seconds(limit, time.monotonic() - review.started, random.randint(lower, upper))
            review.apply_model(details, revision, seconds)
            self.main_ui.metrics_signal.emit({'result': details})
        except Exception as error:
            if review.event.is_set() or not self._running():
                return
            kind, message = classify_error(error)
            review.set_state(llm_state='timeout' if kind == 'timeout' else 'failed',
                llm_error=message, status=message)
            self.main_ui.metrics_signal.emit({'unknown': 1})
            self.add_message('%s：%s' % (self.lessonname, message), 4)

    def _start_model(self, review):
        with review.lock:
            if (not self._running() or review.event.is_set() or not review.can_answer
                    or getattr(review, '_model_running', False)):
                return
            review._model_running = True
        def work():
            try:
                self._answer_by_llm(review.problem, review.problem_id, review.problemtype, review.limit, review)
            finally:
                with review.lock:
                    review._model_running = False
        threading.Thread(target=work, daemon=True).start()

    def _wait_review(self, review):
        # 与模型请求分开，模型挂起期间也可以立即提交人工答案。
        while not review.event.wait(.1):
            if not self._running():
                review.finish(cancel=True)
            elif review.question_deadline is not None and time.monotonic() >= review.question_deadline:
                review.finish(cancel=True)
                review.set_state(submission_state='expired', status='题目已结束，未提交')
            elif review.auto_submit and time.monotonic() >= review.deadline:
                review.finish_if_auto()
        if review.cancelled or not self._running():
            return
        self.answer_questions(review.problem_id, review.problemtype, review.answers, review.limit,
            elapsed=time.monotonic() - review.started, immediate=True)

    def _refresh_question(self, review, pushed):
        if review.event.is_set():
            return
        problem = self._extract_problem(pushed, review.problem_id)
        # 新报文可能只有 id，不得擦除先前已经收到的题干。
        for key, value in review.problem.items():
            if not problem.get(key):
                problem[key] = value
        try:
            problemtype = int(problem.get('problemType'))
        except (ValueError, TypeError):
            problemtype = None
        has_content = bool(problem.get('body') or problem.get('content') or problem.get('title') or problem.get('images') or problem.get('options'))
        can_answer = (problemtype in (1, 2, 4, 5) and has_content and review.time_known
                      and not review.problem_id.startswith('unknown:'))
        if problemtype in (1, 2) and not problem.get('options'):
            can_answer = False
        was_ready = review.can_answer
        review.set_state(problem=problem, problemtype=problemtype, can_answer=can_answer,
            llm_state=review.llm_state if can_answer else 'not_requested',
            status='已收到题目，可立即作答' if can_answer else (
                '已收到题目，但缺少有效截止时间，请在雨课堂核对' if not review.time_known else
                '已收到题目，题型暂不支持，请到雨课堂处理' if problemtype not in (None, 1, 2, 4, 5) else
                '已收到题目，题面或选项未获取完整，可重新获取'))
        if can_answer and not was_ready:
            self._start_model(review)

    def _retry_question(self, review):
        if review.event.is_set() or not self._running():
            return
        review.set_state(status='已收到题目，正在重新获取题面')
        try:
            if getattr(self, 'wsapp', None) is not None:
                self._current_problem(self.wsapp, review.problem_id)
            for presentationid in list(self.presentation_ids):
                self._cache_problems(presentationid)
            self._refresh_question(review, {})
            if not review.can_answer:
                review.set_state(status='已收到题目，题面仍未完整，请稍后重试或在雨课堂查看')
        except Exception:
            review.set_state(status='已收到题目，题面重新获取失败，请检查网络后重试')

    def start_answer(self, problemid, limit, problem=None):
        # 所有入口先记录接收事实，之后才决定是否能够调用模型。
        if isinstance(limit, str):
            try:
                limit = float(limit)
            except ValueError:
                limit = None
        problemid = str(problemid) if problemid is not None else 'unknown:' + uuid.uuid4().hex
        existing = self.reviews.get(problemid)
        if existing is not None:
            if not existing.time_known and isinstance(limit, (int, float)) and math.isfinite(limit) and (limit == -1 or limit >= 0):
                existing.time_known = True
                existing.limit = limit
                existing.started = time.monotonic()
                existing.question_deadline = None if limit == -1 else existing.started + limit
            if not existing.can_answer:
                self._refresh_question(existing, problem or {})
            return existing
        extracted = self._extract_problem(problem or {}, problemid)
        try:
            problemtype = int(extracted.get('problemType'))
        except (TypeError, ValueError):
            problemtype = None
        time_known = isinstance(limit, (int, float)) and math.isfinite(limit) and (limit == -1 or limit >= 0)
        started = time.monotonic()
        review = AnswerReview(self.lessonname, extracted, problemtype, [], 0,
            auto_submit=self.config.get('auto_submit', True),
            question_deadline=None if limit == -1 or not time_known else started + limit,
            account_id=self.user_uid, lesson_id=self.lessonid, problem_id=problemid,
            history=self.history, on_change=self._changed)
        review.started, review.limit, review.time_known = started, limit if time_known else -1, time_known
        review.can_answer = False  # 完整性检查在发出接收信号后统一执行。
        review.retry_model = lambda: self._start_model(review)
        review.retry_question = lambda: threading.Thread(target=self._retry_question, args=(review,), daemon=True).start()
        self.reviews[problemid] = review
        self.answered_problem_ids.add(problemid)
        previous = self.history.get(review.id, review.account_id)
        if previous and previous.get('submission_state') in ('submitted', 'submitting', 'unknown'):
            review.answers = list(previous.get('answers') or [])
            review.result = previous.get('result') or {}
            review.received_at = previous.get('received_at') or review.received_at
            review.llm_state = previous.get('llm_state') if previous.get('llm_state') == 'success' else 'cancelled'
            review.submission_state = 'submitted' if previous['submission_state'] == 'submitted' else 'unknown'
            review.status = '本题已有提交成功记录，仅供查看' if review.submission_state == 'submitted' else '上次提交结果未确认，请到雨课堂核对；不会重复提交'
            review.event.set()
        review.persist()
        self.main_ui.review_answer_signal.emit(review)
        self._state(kind='monitor', last_question=review.received_at, received_count=len(self.reviews))
        self.add_message('%s收到题目，已显示，可在助手中查看' % self.lessonname, 3)
        if review.event.is_set():
            return review
        cached = self.problems_dict.get(problemid, {})
        if cached.get('result') is not None:
            review.event.set()
            review.set_state(submission_state='submitted', llm_state='not_requested', status='平台已标记本题已作答')
            return review
        if time_known and limit != -1 and limit <= 0:
            review.finish(cancel=True)
            review.set_state(submission_state='expired', llm_state='not_requested', status='收到题目时已截止，仅供查看')
            return review
        self._refresh_question(review, problem or {})
        threading.Thread(target=self._wait_review, args=(review,), daemon=True).start()
        return review

    def _current_problem(self, wsapp, promblemid):
        # 为获取已解锁的问题详情信息，向wsapp发送probleminfo
        query_problem = {"op":"probleminfo","lessonid":self.lessonid,"problemid":promblemid,"msgid":1}
        wsapp.send(json.dumps(query_problem))
    
    def on_error(self, wsapp, error):
        if self._running():
            self._state(kind='monitor', state='reconnecting', status='课堂连接异常，正在准备重连')

    def on_close(self, wsapp, status_code, message):
        if self._running():
            self._state(kind='monitor', state='reconnecting', status='课堂连接已断开，5 秒后重连')

    def on_pong(self, wsapp, data):
        if self._running():
            self._state(kind='monitor', last_check=time.time())

    def start_lesson(self, callback):
        index = None
        try:
            while self._running():
                try:
                    self._state(kind='monitor', state='connecting', status='正在签到并连接课堂')
                    self.auth = self.checkin_class()
                    if index is None:
                        info = self.get_lesson_info()
                        index = next(self.main_ui.course_sequence)
                        self.add_course([self.lessonname, info.get('title', ''), info.get('teacher', {}).get('name', ''),
                            time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info.get('startTime', 0) / 1000))], index)
                    self.wsapp = websocket.WebSocketApp(url=wss_url, header=self.headers,
                        on_open=self.on_open, on_message=self.on_message, on_error=self.on_error,
                        on_close=self.on_close, on_pong=self.on_pong)
                    self.wsapp.run_forever(ping_interval=20, ping_timeout=10)
                except Exception:
                    if self._running():
                        self._state(kind='monitor', state='reconnecting', status='课堂连接失败，5 秒后重试')
                until = time.monotonic() + 5
                while self._running() and time.monotonic() < until:
                    time.sleep(.2)
        finally:
            self.closed = True
            for review in list(self.reviews.values()):
                review.finish(cancel=True)
            if index is not None:
                self.del_course(index)
            self._state(kind='monitor', state='stopped', status='本课堂监听已结束')
            callback(self)

    def send_danmu(self,content):
        url = "https://www.yuketang.cn/api/v3/lesson/danmu/send"
        data = {
            "extra": "",
            "fromStart": "50",
            "lessonId": self.lessonid,
            "message": content,
            "requiredCensor": False,
            "showStatus": True,
            "target": "",
            "userName": "",
            "wordCloud": True
        }
        r = requests.post(url=url,headers=self.headers,data=json.dumps(data),proxies={"http": None,"https":None}, timeout=10)
        if dict_result(r.text)["code"] == 0:
            meg = "%s弹幕发送成功！内容：%s" % (self.lessonname,content)
        else:
            meg = "%s弹幕发送失败！内容：%s" % (self.lessonname,content)
        self.add_message(meg,1)
    
    def get_lesson_info(self):
        url = "https://www.yuketang.cn/api/v3/lesson/basic-info"
        r = requests.get(url=url,headers=self.headers,proxies={"http": None,"https":None}, timeout=10)
        return dict_result(r.text)["data"]
        

    def __eq__(self, other):
        return self.lessonid == other.lessonid

class User:
    def __init__(self, uid):
        self.uid = uid
    
    def get_userinfo(self, classroomid, headers):
        r = requests.get("https://www.yuketang.cn/v/course_meta/fetch_user_info_new?query_user_id=%s&classroom_id=%s" % (self.uid,classroomid),headers=headers,proxies={"http": None,"https":None}, timeout=10)
        data = dict_result(r.text)["data"]
        self.sno = data["school_number"]
        self.name = data["name"]
