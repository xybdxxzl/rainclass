import re
import json
import base64
import time
import requests
import urllib3
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

# 开启抓包工具/代理时证书校验常会失败，统一忽略SSL校验并压制告警
VERIFY_SSL = False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 雨课堂题型: 1=单选 2=多选 3=投票 4=填空 5=主观
TYPE_DESC = {
    1: "单选题",
    2: "多选题",
    4: "填空题",
    5: "简答题",
}

SYSTEM_PROMPT = (
    "你是一名答题助手。请根据题目和选项给出正确答案。\n"
    "只输出 JSON，不要输出任何其他内容，格式如下：\n"
    '- 单选题/多选题：{"choices":["A"]} 或 {"choices":["A","C"]}\n'
    '- 填空题：按空的顺序输出 {"blanks":["空1答案","空2答案"]}\n'
    '- 简答题：输出 {"answer":"完整答案"}，用简洁中文分点回答，必要时给出推导过程\n'
    "如果题目附有图片，请结合图片内容作答。"
    "另外添加 question_type（题型文字）、confidence（0至1的自评数值，无法判断时为null）、"
    "explanation（100字以内的简短解析）。置信度不是经过校准的准确率。"
)

RESULT_SCHEMA = {
    'type': 'object',
    'properties': {
        'question_type': {'type': 'string'},
        'choices': {'type': 'array', 'items': {'type': 'string'}},
        'blanks': {'type': 'array', 'items': {'type': 'string'}},
        'answer': {'type': 'string'},
        'confidence': {'type': ['number', 'null']},
        'explanation': {'type': 'string'},
    },
    'required': ['question_type', 'choices', 'blanks', 'answer', 'confidence', 'explanation'],
    'additionalProperties': False,
}


class _QuestionHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            url = attrs.get("data-src") or attrs.get("src")
            if url:
                self.images.append(url)
            if attrs.get("alt"):
                self.text.append(attrs["alt"])
        if tag in ("br", "p", "div", "li"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "div", "li"):
            self.text.append("\n")

    def handle_data(self, data):
        self.text.append(data)


def strip_html(html_text):
    parser = _QuestionHTML()
    parser.feed(str(html_text or ""))
    return "".join(parser.text).strip(), parser.images


def _download_image_b64(url, deadline=None):
    # 内嵌图片无需下载；相对链接以雨课堂为基准。
    url = unescape(url).strip()
    if url.startswith("data:image/"):
        return url
    url = urljoin("https://www.yuketang.cn/", url)
    try:
        remaining = deadline - time.monotonic() if deadline is not None else 15
        if remaining <= 0:
            raise RuntimeError("大模型作答时间预算已耗尽")
        r = requests.get(url, timeout=min(15, remaining), proxies={"http": None, "https": None}, verify=VERIFY_SSL)
        if r.status_code == 200:
            content_type = r.headers.get("Content-Type", "image/jpeg").split(";")[0].lower()
            if not r.content or not content_type.startswith("image/"):
                return None
            b64 = base64.b64encode(r.content).decode()
            return "data:%s;base64,%s" % (content_type, b64)
    except Exception:
        pass
    return None


def _as_list(value):
    # 字段可能是None、单值或非list，统一成list，避免遍历崩溃
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def has_images(problem):
    if _as_list(problem.get('images')) or strip_html(problem.get('body') or problem.get('content') or problem.get('title') or '')[1]:
        return True
    for option in _as_list(problem.get('options')):
        if isinstance(option, dict):
            if _as_list(option.get('images')) or strip_html(option.get('value') or option.get('content') or '')[1]:
                return True
        elif strip_html(option)[1]:
            return True
    return False


def route_config(problem, config):
    selected = dict(config)
    if has_images(problem) and config.get('vision_model', '').strip():
        selected['model'] = config['vision_model'].strip()
        if selected['model'] != config.get('model'):
            selected['pricing'] = {}
    return selected


def _build_user_content(problem, problemtype, deadline=None):
    # 组装发送给大模型的用户消息（文本+图片）
    # 题面字段名以课件结构为准，这里再做一次兜底，兼容WS推送里可能出现的别名
    body_text, imgs = strip_html(problem.get("body") or problem.get("content") or problem.get("title") or "")
    # 部分接口图片在images字段中
    for img in _as_list(problem.get("images")):
        if isinstance(img, dict) and img.get("url"):
            imgs.append(img["url"])
        elif isinstance(img, str):
            imgs.append(img)

    lines = ["题型：%s" % TYPE_DESC.get(problemtype, problemtype),
             "题目：%s" % body_text]
    option_images = []
    for index, opt in enumerate(_as_list(problem.get("options"))):
        if isinstance(opt, dict):
            key = opt.get("key") or chr(65 + index)
            text, images = strip_html(opt.get("value") or opt.get("content") or "")
            for img in _as_list(opt.get("images")):
                url = img.get("url") if isinstance(img, dict) else img
                if isinstance(url, str) and url:
                    images.append(url)
            lines.append("%s. %s" % (key, text))
            option_images.extend(("选项%s图片" % key, url) for url in images)
        else:
            text, images = strip_html(opt)
            key = chr(65 + index)
            lines.append("%s. %s" % (key, text))
            option_images.extend(("选项%s图片" % key, url) for url in images)
    if problemtype == 4:
        blanks = _as_list(problem.get("blanks"))
        lines.append("本题共有%d个空，请按顺序作答" % (len(blanks) if blanks else 1))

    content = [{"type": "text", "text": "\n".join(lines)}]
    seen = set()
    for label, url in [("题干图片", url) for url in imgs] + option_images:
        if (label, url) in seen:
            continue
        seen.add((label, url))
        b64 = _download_image_b64(url, deadline=deadline)
        if not b64:
            raise RuntimeError("%s加载失败，请检查图片链接或在雨课堂查看原题" % label)
        content.append({"type": "text", "text": label})
        content.append({"type": "image_url", "image_url": {"url": b64}})
    return content


def _parse_answer(content, problemtype):
    # 从大模型输出中解析JSON答案
    if not isinstance(content, str):
        return None
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        # 大模型输出不是合法JSON（如带了额外说明文字）
        return None
    if not isinstance(data, dict):
        return None
    if problemtype == 5:
        answer = data.get("answer")
        return [answer.strip()] if isinstance(answer, str) and answer.strip() else None
    if problemtype == 4:
        blanks = data.get("blanks")
        return [str(x) for x in blanks] if isinstance(blanks, list) and blanks else None
    choices = data.get("choices")
    if choices and isinstance(choices, list):
        # 统一大写并按字母排序，与原answers格式一致
        return sorted([str(c).strip().upper() for c in choices])
    return None


def parse_result(content, problemtype, problem):
    """解析并验证结果；不可信答案绝不进入提交路径。"""
    answers = _parse_answer(content, problemtype)
    if not answers:
        return None
    m = re.search(r"\{.*\}", content, re.S)
    try:
        data = json.loads(m.group(0))
    except (ValueError, AttributeError):
        return None
    if problemtype in (1, 2):
        options = _as_list(problem.get('options'))
        allowed = {str((o.get('key') or chr(65 + i)) if isinstance(o, dict) else chr(65+i)).upper()
                   for i, o in enumerate(options)}
        if not allowed or len(set(answers)) != len(answers) or any(a not in allowed for a in answers):
            return None
        if problemtype == 1 and len(answers) != 1:
            return None
    elif problemtype == 4:
        blanks = _as_list(problem.get('blanks'))
        raw = data.get('blanks')
        if not isinstance(raw, list) or any(x is None or isinstance(x, (dict,list,bool)) for x in raw):
            return None
        if (blanks and len(answers) != len(blanks)) or any(not str(x).strip() for x in answers):
            return None
    confidence = data.get('confidence')
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        confidence = None
    explanation = data.get('explanation', '')
    if not isinstance(explanation, str):
        explanation = ''
    return {'answers': answers, 'question_type': TYPE_DESC.get(problemtype, str(problemtype)),
            'confidence': confidence, 'explanation': explanation.strip()[:100]}


def ask_llm(problem, problemtype, llm_cfg, time_budget=None, on_retry=None, return_details=False):
    """
    调用大模型API回答问题。
    成功返回答案列表（选择题/填空题/简答题），失败返回None。
    """
    try:
        # WS推送的题型可能是字符串，先归一为int，否则会在TYPE_DESC中查不到而静默失败
        problemtype = int(problemtype)
    except (TypeError, ValueError):
        return None
    if problemtype not in TYPE_DESC:
        return None
    base_url = (llm_cfg.get("base_url") or "").rstrip("/")
    if base_url.endswith("/chat/completions"):
        # 用户可能直接粘贴了完整端点，避免拼成 .../chat/completions/chat/completions
        base_url = base_url[:-len("/chat/completions")]
    api_key = llm_cfg.get("api_key", "")
    model = llm_cfg.get("model", "")
    if not base_url or not api_key or not model:
        return None

    problem = problem or {}
    if not (problem.get("body") or problem.get("content") or problem.get("title")
            or problem.get("options") or problem.get("images")):
        # 题面为空，发出去也没法作答，不浪费一次请求
        return None

    # 图片下载、请求和重试共享预算，课堂调用方另行预留提交时间。
    timeout = max(1, float(llm_cfg.get("timeout", 30)))
    if urljoin(base_url + "/", "/").lower() == "https://api.deepseek.com/":
        timeout = max(30, timeout)
    budget = max(0, float(time_budget)) if time_budget is not None else timeout * 3 + 3
    deadline = time.monotonic() + budget
    if budget <= 0:
        raise RuntimeError("大模型作答时间预算已耗尽")
    user_prompt = str(llm_cfg.get('system_prompt') or '').strip()
    prompt = SYSTEM_PROMPT + ('\n补充要求：' + user_prompt if user_prompt else '') + '\n始终按上述 JSON 格式输出。'
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": _build_user_content(problem, problemtype, deadline=deadline)},
        ],
    }
    host = (urlsplit(base_url).hostname or '').lower()
    mode = llm_cfg.get('structured_mode', 'auto')
    if mode == 'schema':
        payload['response_format'] = {'type':'json_schema','json_schema':{
            'name':'classroom_answer','strict':True,'schema':RESULT_SCHEMA}}
    elif mode == 'json' or (mode == 'auto' and (host == 'api.deepseek.com' or host.endswith('.aliyuncs.com'))):
        payload['response_format'] = {'type': 'json_object'}
    started = time.monotonic()
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("大模型作答时间预算已耗尽")
        r = None
        try:
            r = requests.post(
                base_url + "/chat/completions",
                headers={"Authorization": "Bearer %s" % api_key},
                json=payload,
                timeout=(min(10, remaining / 2), min(timeout, remaining / 2)),
                proxies={"http": None, "https": None},
                verify=VERIFY_SSL,
            )
            r.raise_for_status()
            break
        except requests.RequestException as e:
            transient = isinstance(e, (requests.Timeout, requests.ConnectionError)) or (
                r is not None and r.status_code in (429, 500, 502, 503, 504))
            delay = attempt + 1
            remaining = deadline - time.monotonic()
            detail = "HTTP %s：%s" % (r.status_code, r.text[:300]) if r is not None else str(e)
            if not transient or attempt == 2 or remaining <= delay + 1:
                raise RuntimeError("请求大模型接口失败（已尝试%d次）：%s" % (attempt + 1, detail)) from e
            if on_retry:
                on_retry("大模型请求暂时失败，%s秒后重试（第%d/3次，剩余约%d秒）" % (
                    delay, attempt + 2, remaining))
            if r is not None:
                r.close()
            time.sleep(delay)
    if time.monotonic() >= deadline:
        raise RuntimeError("大模型响应超过作答时间预算，未采用答案")
    try:
        response = r.json()
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        # 把401/404等错误响应体带出来，便于排查（裸HTTPError没有正文）
        raise RuntimeError("大模型返回内容无法解析（HTTP %s）：%s" % (r.status_code, r.text[:300]))
    if not return_details:
        return _parse_answer(content, problemtype)
    result = parse_result(content, problemtype, problem)
    if result:
        result['latency_ms'] = int((time.monotonic() - started) * 1000)
        usage = response.get('usage')
        result['usage'] = usage if isinstance(usage, dict) else None
        result['model'] = model
        result['pricing'] = llm_cfg.get('pricing') or {}
    return result


def stream_explanation(problem, problemtype, answers, llm_cfg, question, on_chunk):
    """单独请求可见解题说明；不会改变题目答案或提交状态。"""
    base = (llm_cfg.get('base_url') or '').rstrip('/')
    if base.endswith('/chat/completions'):
        base = base[:-len('/chat/completions')]
    key, model = llm_cfg.get('api_key', ''), llm_cfg.get('model', '')
    if not base or not key or not model:
        raise RuntimeError('请先配置模型接口、模型名称和密钥')
    content = _build_user_content(problem, problemtype, deadline=time.monotonic()+60)
    content.append({'type': 'text', 'text': '当前参考答案：%s\n用户问题：%s' % ('、'.join(answers), question or '请详细解释这道题。')})
    payload = {'model': model, 'stream': True, 'temperature': 0, 'max_tokens': 800,
               'messages': [{'role':'system','content':'请给出简洁、可读的解题说明；不要更改或提交答案。'},
                            {'role':'user','content':content}]}
    if (urlsplit(base).hostname or '').lower() == 'api.deepseek.com':
        payload['stream_options'] = {'include_usage': True}
    started = time.monotonic()
    timeout = max(1, min(600, int(llm_cfg.get('timeout', 30))))
    if (urlsplit(base).hostname or '').lower() == 'api.deepseek.com':
        timeout = max(30, timeout)
    usage = None
    seen = False
    try:
        with requests.post(base+'/chat/completions', headers={'Authorization':'Bearer %s' % key},
                           json=payload, timeout=(min(10,timeout),timeout), stream=True,
                           proxies={'http':None,'https':None}, verify=VERIFY_SSL) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if time.monotonic() - started > 120:
                    raise RuntimeError('解析请求超过时间限制')
                if isinstance(line, bytes):
                    line = line.decode('utf-8', errors='replace')
                if not line or not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if isinstance(chunk.get('usage'), dict):
                    usage = chunk['usage']
                choices = chunk.get('choices') or []
                if choices:
                    text = (choices[0].get('delta') or {}).get('content')
                    if isinstance(text, str) and text:
                        seen = True
                        on_chunk(text)
    except requests.RequestException as e:
        raise RuntimeError('解析请求失败：%s' % e) from e
    if not seen:
        raise RuntimeError('接口未返回可显示的流式解析')
    return {'model':model,'latency_ms':int((time.monotonic()-started)*1000),
            'usage':usage,'pricing':llm_cfg.get('pricing') or {}}
