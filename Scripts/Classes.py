import requests
import threading
import time
import random
import websocket
import json
from Scripts.Utils import get_user_info, dict_result, calculate_waittime
from Scripts.LLM import ask_llm, route_config
from Scripts.AnswerReview import AnswerReview, review_seconds

wss_url = "wss://www.yuketang.cn/wsapp/"
class Lesson:
    def __init__(self,lessonid,lessonname,classroomid,main_ui):
        self.classroomid = classroomid
        self.lessonid = lessonid
        self.lessonname = lessonname
        self.sessionid = main_ui.config["sessionid"]
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
        self.unlocked_problem = []
        self.classmates_ls = []
        self.add_message = main_ui.add_message_signal.emit
        self.add_course = main_ui.add_course_signal.emit
        self.del_course = main_ui.del_course_signal.emit
        self.config = main_ui.config
        code, rtn = get_user_info(self.sessionid)
        self.user_uid = rtn["id"]
        self.user_uname = rtn["name"]
        self.main_ui = main_ui

    def _get_ppt(self,presentationid):
        # 获取课程各页ppt
        r = requests.get(url="https://www.yuketang.cn/api/v3/lesson/presentation/fetch?presentation_id=%s" % (presentationid),headers=self.headers,proxies={"http": None,"https":None})
        return dict_result(r.text)["data"]

    def get_problems(self,presentationid):
        # 获取课程ppt中的题目
        data = self._get_ppt(presentationid)
        return [problem["problem"] for problem in data["slides"] if "problem" in problem.keys()]

    def _cache_problems(self,presentationid):
        # 缓存题面结构（problemType/body/options/blanks/images等），按problemId索引
        # 注意：课件中的answers字段不再用于答案匹配，一切答案均由大模型生成
        for problem in self.get_problems(presentationid):
            self.problems_dict[problem["problemId"]] = problem

    def _extract_problem(self,data,problemid):
        # 合并WebSocket报文与课件缓存，得到统一的题目结构
        # WS推送/回包很可能只有sid+limit，题面以课件缓存为准；WS若带了更全的字段则优先
        ws = data.get("problem") if isinstance(data.get("problem"),dict) else data
        cached = self.problems_dict.get(problemid,{})

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
            self.add_message("%s题目已超时，未提交答案，请前往雨课堂查看" % self.lessonname,4)
            return False
        if answer and problemtype != 3:
            wait_time = calculate_waittime(limit, self.config["answer_config"]["answer_delay"]["type"], self.config["answer_config"]["answer_delay"]["custom"]["time"])
            wait_time = max(0,wait_time - int(elapsed))
            if immediate:
                wait_time = 0
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
                r = requests.post(url="https://www.yuketang.cn/api/v3/lesson/problem/answer",headers=self.headers,data=json.dumps(data),proxies={"http": None,"https":None}, timeout=10)
                r.raise_for_status()
                return_dict = dict_result(r.text)
            except (requests.RequestException, ValueError) as e:
                self.add_message('%s提交结果未确认：%s，请到雨课堂核对，程序不会重复提交' % (self.lessonname, e), 4)
                return False
            if return_dict["code"] == 0:
                meg = "%s自动回答成功" % self.lessonname
                self.add_message(meg,4)
                self.main_ui.metrics_signal.emit({'submitted': 1})
                # threading.Thread(target=say_something,args=(meg,)).start()
                return True
            else:
                meg = "%s自动回答失败，原因：%s" % (self.lessonname,return_dict["msg"].replace("_"," "))
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
    
    def on_open(self, wsapp):
        self.handshark = {"op":"hello","userid":self.user_uid,"role":"student","auth":self.auth,"lessonid":self.lessonid}
        wsapp.send(json.dumps(self.handshark))

    def checkin_class(self):
        r = requests.post(url="https://www.yuketang.cn/api/v3/lesson/checkin",headers=self.headers,data=json.dumps({"source":5,"lessonId":self.lessonid}),proxies={"http": None,"https":None})
        set_auth = r.headers.get("Set-Auth",None)
        times = 1
        while not set_auth and times <= 3:
            set_auth = r.headers.get("Set-Auth",None)
            times += 1
            time.sleep(1)
        self.headers["Authorization"] = "Bearer %s" % set_auth
        return dict_result(r.text)["data"]["lessonToken"]

    def on_message(self, wsapp, message):
        data = dict_result(message)
        op = data["op"]
        if op == "hello":
            presentations = list(set([slide["pres"] for slide in data["timeline"] if slide["type"]=="slide"]))
            current_presentation = data["presentation"]
            if current_presentation not in presentations:
                presentations.append(current_presentation)
            for presentationid in presentations:
                self._cache_problems(presentationid)
            self.unlocked_problem = data["unlockedproblem"]
            for problemid in self.unlocked_problem:
                self._current_problem(wsapp, problemid)
        elif op == "unlockproblem":
            # 原始报文，供实测核对推送字段
            self.add_message("RAW %s: %s" % (op,json.dumps(data,ensure_ascii=False)),0)
            self.start_answer(data["problem"]["sid"],data["problem"]["limit"],problem=data["problem"])
        elif op == "lessonfinished":
            meg = "%s下课了" % self.lessonname
            # threading.Thread(target=say_something,args=(meg,)).start()
            self.add_message(meg,7)
            wsapp.close()
        elif op == "presentationupdated":
            self._cache_problems(data["presentation"])
        elif op == "presentationcreated":
            self._cache_problems(data["presentation"])
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
            limit = data.get("limit",-1)
            now = data.get("now")
            dt = data.get("dt")
            if limit != -1 and now is not None and dt is not None:
                time_left = int(limit-(int(now) - int(dt))/1000)
            elif limit != -1:
                self.add_message('%s题目缺少时间信息，请在雨课堂查看' % self.lessonname, 0)
                return
            else:
                # 缺少计时字段时按不限时处理，避免KeyError中断WebSocket线程
                time_left = limit
            # 筛选未到期题目
            if time_left > 0 or limit == -1:
                self.start_answer(problemid,time_left,problem=self._extract_problem(data,problemid))
            else:
                meg = "%s检测到已过期的题目，已忽略" % (self.lessonname)
                self.add_message(meg,0)

    def _llm_fallback_hint(self, limit, elapsed=0):
        # 找不到答案时的原有提示
        if limit == -1:
            meg = "%s的问题没有找到答案，该题不限时，请尽快前往雨课堂回答" % (self.lessonname)
        elif limit <= elapsed:
            meg = "%s题目已超时，未提交答案，请前往雨课堂查看" % self.lessonname
        else:
            meg = "%s的问题没有找到答案，请在%s秒内前往雨课堂回答" % (self.lessonname,max(0, int(limit - elapsed)))
        self.add_message(meg,4)

    def _answer_by_llm(self, problem, problemid, problemtype, limit):
        # 大模型答题线程函数
        llm_cfg = route_config(problem, self.config.get("llm_config", {}))
        if not llm_cfg.get("enabled") or not problem:
            self._llm_fallback_hint(limit)
            return
        meg = "%s检测到问题，正在请求大模型作答" % self.lessonname
        self.add_message(meg,3)
        start = time.monotonic()
        self.main_ui.question_signal.emit({'course': self.lessonname, 'problem': problem,
                                           'type': problemtype, 'limit': limit, 'started': start})
        def on_retry(message):
            self.main_ui.metrics_signal.emit({'unknown': 1})
            self.add_message('%s：%s' % (self.lessonname, message), 0)
        try:
            result = ask_llm(
                problem, problemtype, llm_cfg,
                time_budget=max(0, limit - 5) if limit != -1 else None,
                on_retry=on_retry,
                return_details=True)
        except Exception as e:
            self.main_ui.metrics_signal.emit({'unknown': 1})
            self.main_ui.question_signal.emit({'course': self.lessonname, 'problem': problem, 'type': problemtype,
                                               'limit': limit, 'started': start, 'status': '模型请求失败，请查看日志'})
            meg = "%s大模型请求失败：%s" % (self.lessonname, e)
            self.add_message(meg,4)
            self._llm_fallback_hint(limit, elapsed=time.monotonic() - start)
            return
        elapsed = time.monotonic() - start
        details = result if isinstance(result, dict) else {'answers': result}
        answers = details.get('answers')
        if answers:
            self.main_ui.metrics_signal.emit({'result': details})
        if answers:
            meg = "%s大模型给出的答案为：%s" % (self.lessonname, answers)
            self.add_message(meg,4)
            lower = max(1, int(self.config.get('answer_delay_min', self.config.get('answer_review_seconds', 15))))
            upper = max(lower, int(self.config.get('answer_delay_max', lower)))
            seconds = review_seconds(limit, elapsed, random.randint(lower, upper))
            if not self.main_ui.is_active or seconds <= 0:
                self.add_message('%s已停止监听或剩余时间不足，未提交答案' % self.lessonname, 4)
                return
            review = AnswerReview(self.lessonname, problem, problemtype, answers, seconds,
                                  auto_submit=self.config.get('auto_submit', True),
                                  question_deadline=start + limit if limit != -1 else None, result=details)
            self.main_ui.review_answer_signal.emit(review)
            # 线程只等候数据，不操作窗口。GUI繁忙时仍可在截止前提交最近有效答案。
            while not review.event.wait(0.1):
                if not self.main_ui.is_active:
                    review.finish(cancel=True)
                elif review.question_deadline is not None and time.monotonic() >= review.question_deadline:
                    review.finish(cancel=True)
                elif review.auto_submit and time.monotonic() >= review.deadline:
                    review.finish_if_auto()
            if review.cancelled or not self.main_ui.is_active:
                self.add_message('%s本题已取消提交' % self.lessonname, 4)
                return
            self.answer_questions(problemid, problemtype, review.answers, limit,
                                  elapsed=time.monotonic() - start, immediate=True)
        else:
            self.main_ui.metrics_signal.emit({'unknown': 1})
            self.main_ui.question_signal.emit({'course': self.lessonname, 'problem': problem, 'type': problemtype,
                                               'limit': limit, 'started': start, 'status': '未获得有效答案，请手动处理'})
            meg = "%s大模型未能解析出有效答案" % self.lessonname
            self.add_message(meg,4)
            self._llm_fallback_hint(limit, elapsed=elapsed)

    def start_answer(self, problemid, limit, problem=None):
        if problemid is None:
            # 没有题目id就无法定位题面，也无法做去重记录
            self._llm_fallback_hint(limit)
            return
        if problemid in self.answered_problem_ids:
            # 该题在本次运行中已派发过作答，忽略重复推送
            return
        cached = self.problems_dict.get(problemid,{})
        if cached.get("result") is not None:
            # 课件中带有服务端「已作答」标记，说明启动前就已答过该题
            # 该情况理论上只会出现在中途启动监听时
            self.answered_problem_ids.add(problemid)
            return
        llm_cfg = self.config.get("llm_config", {})
        if not self.config["auto_answer"] or not llm_cfg.get("enabled"):
            # 仅提醒，不占用去重集合
            extracted = self._extract_problem(problem or {},problemid)
            self.main_ui.question_signal.emit({'course': self.lessonname, 'problem': extracted,
                                               'type': extracted.get('problemType'), 'limit': limit, 'started': time.monotonic(),
                                               'status': '仅提醒：模型答题未开启'})
            self._llm_fallback_hint(limit)
            return
        problem = self._extract_problem(problem or {},problemid)
        problemtype = problem.get("problemType")
        try:
            problemtype = int(problemtype)
        except (TypeError, ValueError):
            problemtype = None
        if problemtype not in (1,2,4,5):
            # 取不到题型或投票题，交由人工处理
            self._llm_fallback_hint(limit)
            return
        # 只在真正派发大模型作答前占位，防止重复提交
        self.answered_problem_ids.add(problemid)
        threading.Thread(target=self._answer_by_llm,args=(problem,problemid,problemtype,limit),daemon=True).start()

    
    def _current_problem(self, wsapp, promblemid):
        # 为获取已解锁的问题详情信息，向wsapp发送probleminfo
        query_problem = {"op":"probleminfo","lessonid":self.lessonid,"problemid":promblemid,"msgid":1}
        wsapp.send(json.dumps(query_problem))
    
    def start_lesson(self, callback):
        self.auth = self.checkin_class()
        rtn = self.get_lesson_info()
        teacher = rtn["teacher"]["name"]
        title = rtn["title"]
        timestamp = rtn["startTime"] // 1000
        time_str = time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(timestamp))
        index = next(self.main_ui.course_sequence)
        self.add_course([self.lessonname,title,teacher,time_str],index)
        self.wsapp = websocket.WebSocketApp(url=wss_url,header=self.headers,on_open=self.on_open,on_message=self.on_message)
        self.wsapp.run_forever()
        meg = "%s监听结束" % self.lessonname
        self.add_message(meg,7)
        self.del_course(index)
        # threading.Thread(target=say_something,args=(meg,)).start()
        return callback(self)
    
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
        r = requests.post(url=url,headers=self.headers,data=json.dumps(data),proxies={"http": None,"https":None})
        if dict_result(r.text)["code"] == 0:
            meg = "%s弹幕发送成功！内容：%s" % (self.lessonname,content)
        else:
            meg = "%s弹幕发送失败！内容：%s" % (self.lessonname,content)
        self.add_message(meg,1)
    
    def get_lesson_info(self):
        url = "https://www.yuketang.cn/api/v3/lesson/basic-info"
        r = requests.get(url=url,headers=self.headers,proxies={"http": None,"https":None})
        return dict_result(r.text)["data"]
        

    def __eq__(self, other):
        return self.lessonid == other.lessonid

class User:
    def __init__(self, uid):
        self.uid = uid
    
    def get_userinfo(self, classroomid, headers):
        r = requests.get("https://www.yuketang.cn/v/course_meta/fetch_user_info_new?query_user_id=%s&classroom_id=%s" % (self.uid,classroomid),headers=headers,proxies={"http": None,"https":None})
        data = dict_result(r.text)["data"]
        self.sno = data["school_number"]
        self.name = data["name"]
