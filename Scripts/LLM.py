import re
import json
import base64
import time
import threading
import requests
import urllib3
from html import unescape
from queue import Empty, Queue
from random import randrange
from urllib.parse import urljoin, urlsplit

from Scripts.Question import as_list, question_parts, strip_html

# 开启抓包工具/代理时证书校验常会失败，统一忽略SSL校验并压制告警
VERIFY_SSL = False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_EDGE = 1600
REENCODE_IMAGE_BYTES = 512 * 1024
MAX_RETRY_IMAGE_BYTES = 8 * 1024 * 1024

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
    "另外添加 question_type（题型文字）、confidence（0至1的自评数值，无法判断时为null）、"
    "explanation（100字以内的简短解析）。置信度不是经过校准的准确率。"
)

IMAGE_PROMPT = (
    '\n先读题图和各选项图中的关键文字、数字或图表标注，再结合作答。'
    '如有关键图像信息看不清，输出 image_unreadable 为 true、答案字段留空，并在 explanation 说明哪里读不清。'
    '图像清晰时输出 image_unreadable 为 false。若按题意本身无法确定，使用正常答案格式并说明依据。'
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


class ImageLoadError(RuntimeError):
    pass


class ImageUnreadableError(RuntimeError):
    pass


def _encode_image(content, prefer_original=False):
    # 使用桌面端已有的 Qt 图像解码器，普通小图保留原始清晰度。
    from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
    from PyQt5.QtGui import QImage, QImageReader, QPainter
    from UI.QtBootstrap import configure_qt

    configure_qt()
    if len(content) > MAX_IMAGE_BYTES:
        raise ImageLoadError('过大，请在雨课堂查看原图')
    source = QBuffer()
    source.setData(QByteArray(content))
    source.open(QIODevice.ReadOnly)
    reader = QImageReader(source)
    reader.setAutoTransform(True)
    size = reader.size()
    image_format = bytes(reader.format()).lower()
    if not size.isValid() or size.isEmpty():
        raise ImageLoadError('加载失败，图片格式无效')
    if size.width() * size.height() > 40_000_000:
        raise ImageLoadError('过大，请在雨课堂查看原图')
    mime = {b'png': 'image/png', b'jpeg': 'image/jpeg', b'gif': 'image/gif', b'webp': 'image/webp'}.get(image_format)
    edge = max(size.width(), size.height())
    if mime and ((edge <= MAX_IMAGE_EDGE and len(content) <= REENCODE_IMAGE_BYTES)
                 or (prefer_original and len(content) <= MAX_RETRY_IMAGE_BYTES)
                 or (image_format in (b'gif', b'webp') and reader.imageCount() > 1)):
        return 'data:%s;base64,%s' % (mime, base64.b64encode(content).decode('ascii'))
    if edge > MAX_IMAGE_EDGE:
        reader.setScaledSize(size.scaled(QSize(MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Qt.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ImageLoadError('加载失败，无法读取图片内容')
    if image.hasAlphaChannel():
        background = QImage(image.size(), QImage.Format_RGB32)
        background.fill(Qt.white)
        painter = QPainter(background)
        painter.drawImage(0, 0, image)
        painter.end()
        image = background
    output = QBuffer()
    output.open(QIODevice.WriteOnly)
    if not image.save(output, 'JPEG', 85):
        raise ImageLoadError('处理失败，无法压缩图片')
    return 'data:image/jpeg;base64,' + base64.b64encode(bytes(output.data())).decode('ascii')


def _download_image_b64(url, deadline=None, *, prefer_original=False):
    # 内嵌图片和下载的图片经过同样的尺寸检查。
    url = unescape(url).strip()
    if url.startswith('data:image/'):
        header, _, encoded = url.partition(',')
        if len(encoded) > (MAX_IMAGE_BYTES * 4 // 3) + 4:
            raise ImageLoadError('过大，请在雨课堂查看原图')
        try:
            if not header.endswith(';base64'):
                raise ValueError('invalid data URL')
            content = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ImageLoadError('加载失败，内嵌图片格式无效') from None
    else:
        url = urljoin('https://www.yuketang.cn/', url)
        if urlsplit(url).scheme not in ('http', 'https'):
            raise ImageLoadError('加载失败，图片链接无效')
        remaining = deadline - time.monotonic() if deadline is not None else 8
        if remaining <= 0:
            raise ImageLoadError('下载超时')
        timeout = urllib3.Timeout(total=remaining, connect=min(4, remaining))
        content = bytearray()
        try:
            with requests.get(url, timeout=timeout, stream=True,
                              proxies={"http": None, "https": None}, verify=VERIFY_SSL) as response:
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if deadline is not None and time.monotonic() >= deadline:
                        raise ImageLoadError('下载超时')
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_BYTES:
                        raise ImageLoadError('过大，请在雨课堂查看原图')
        except requests.RequestException as error:
            if isinstance(error, requests.Timeout) or any(isinstance(cause, urllib3.exceptions.ReadTimeoutError)
                                                           for cause in error.args):
                raise ImageLoadError('下载超时') from error
            raise ImageLoadError('加载失败，请检查图片链接') from error
    return _encode_image(content, prefer_original=prefer_original)


def _prepare_images(references, deadline, *, prefer_original=False):
    # 所有图片共用时限，至少留一半剩余时间给模型。
    now = time.monotonic()
    image_budget = 8 if deadline is None else min(8, (deadline - now) / 2)
    if image_budget <= 0:
        raise ImageLoadError('题目图片准备超时')
    image_deadline = now + image_budget
    jobs, completed = Queue(), Queue()
    cancelled = threading.Event()
    for url in references:
        jobs.put(url)

    def load_images():
        while not cancelled.is_set() and time.monotonic() < image_deadline:
            try:
                url = jobs.get_nowait()
            except Empty:
                return
            try:
                image = (_download_image_b64(url, deadline=image_deadline, prefer_original=True)
                         if prefer_original else _download_image_b64(url, deadline=image_deadline))
                if not image:
                    raise ImageLoadError('加载失败，请在雨课堂核对原图')
            except Exception as error:
                completed.put((url, None, error))
                return
            completed.put((url, image, None))

    # DNS 查询可能晚于网络超时返回，图片线程不能阻止应用退出。
    for _ in range(min(4, len(references))):
        threading.Thread(target=load_images, daemon=True).start()
    images = {}
    try:
        for _ in references:
            url, image, error = completed.get(timeout=max(0, image_deadline - time.monotonic()))
            if error is not None:
                detail = str(error) if isinstance(error, ImageLoadError) else '处理失败，请在雨课堂核对原图'
                raise ImageLoadError('、'.join(references[url]) + detail) from error
            images[url] = image
    except Empty:
        raise ImageLoadError('题目图片下载或处理超时，请在雨课堂核对原图') from None
    finally:
        cancelled.set()
    return images


def has_images(problem):
    stem, options = question_parts(problem)
    return bool(stem.images or any(part.images for part in options.values()))


def route_config(problem, config, *, parts=None):
    selected = dict(config)
    vision_model = (config.get('vision_model') or '').strip()
    if vision_model:
        stem, options = parts if parts is not None else question_parts(problem)
        if stem.images or any(part.images for part in options.values()):
            selected['model'] = vision_model
            if selected['model'] != config.get('model'):
                selected['pricing'] = {}
    return selected


def _build_user_content(problem, problemtype, deadline=None, *, parts=None, prefer_original=False):
    stem, options = parts if parts is not None else question_parts(problem)
    lines = ["题型：%s" % TYPE_DESC.get(problemtype, problemtype), "题目：%s" % stem.text]
    lines.extend("%s. %s" % (key, option.text) for key, option in options.items())
    if problemtype == 4:
        lines.append("本题共有%d个空，请按顺序作答" % (len(as_list(problem.get("blanks"))) or 1))

    references = {}
    labeled_parts = [('题干图片', stem)] + [('选项%s图片' % key, option) for key, option in options.items()]
    for label, part in labeled_parts:
        for url in part.images:
            if not url:
                raise ImageLoadError(label + '未获取到图片地址，请重新获取题面')
            references.setdefault(url, []).append(label)

    content = [{"type": "text", "text": "\n".join(lines)}]
    if references:
        images = _prepare_images(references, deadline, prefer_original=prefer_original)
        for url, labels in references.items():
            content.append({"type": "text", "text": '、'.join(labels)})
            content.append({"type": "image_url", "image_url": {"url": images[url]}})
    return content


def _answer_data(content):
    if not isinstance(content, str):
        return {}
    match = re.search(r"\{.*\}", content, re.S)
    if match is None:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_answer(content, problemtype):
    return _answer_from_data(_answer_data(content), problemtype)


def _answer_from_data(data, problemtype):
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
    data = _answer_data(content)
    if has_images(problem):
        _check_image_answer(data, content, problemtype)
    return _validate_result(data, problemtype, problem)


def _check_image_answer(data, raw_content, problemtype):
    unreadable = data.get('image_unreadable')
    if unreadable is False:
        return
    answers = _answer_from_data(data, problemtype) if data else [raw_content]
    if unreadable is True or any(
        str(answer).strip().rstrip('。.!！') in ('无法确定', '无法判断', '无法识别', '图片看不清', '看不清图片')
        for answer in (answers or [])
    ):
        raise ImageUnreadableError('模型未能读清题图，请核对原图后手动作答')


def _validate_result(data, problemtype, problem):
    answers = _answer_from_data(data, problemtype)
    if not answers:
        return None
    if problemtype in (1, 2):
        options = as_list(problem.get('options'))
        allowed = {str((o.get('key') or chr(65 + i)) if isinstance(o, dict) else chr(65+i)).upper()
                   for i, o in enumerate(options)}
        if not allowed or len(set(answers)) != len(answers) or any(a not in allowed for a in answers):
            return None
        if problemtype == 1 and len(answers) != 1:
            return None
    elif problemtype == 4:
        blanks = as_list(problem.get('blanks'))
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


def ask_llm(problem, problemtype, llm_cfg, time_budget=None, on_retry=None, return_details=False, active=None):
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
    problem = problem or {}
    parts = question_parts(problem)
    llm_cfg = route_config(problem, llm_cfg, parts=parts)
    base_url = (llm_cfg.get("base_url") or "").rstrip("/")
    if base_url.endswith("/chat/completions"):
        # 用户可能直接粘贴了完整端点，避免拼成 .../chat/completions/chat/completions
        base_url = base_url[:-len("/chat/completions")]
    api_key = llm_cfg.get("api_key", "")
    model = llm_cfg.get("model", "")
    if not base_url or not api_key or not model:
        return None

    if not (parts[0].html or parts[0].images or parts[1]):
        # 题面为空，发出去也没法作答，不浪费一次请求
        return None

    # 图片下载、请求和重试共享预算，课堂调用方另行预留提交时间。
    timeout = max(1, float(llm_cfg.get("timeout", 30)))
    if urljoin(base_url + "/", "/").lower() == "https://api.deepseek.com/":
        timeout = max(30, timeout)
    budget = max(0, float(time_budget)) if time_budget is not None else max(1, float(llm_cfg.get('total_timeout', llm_cfg.get('timeout', 30))))
    deadline = time.monotonic() + budget
    if budget <= 0:
        raise RuntimeError("大模型作答时间预算已耗尽")
    user_content = _build_user_content(problem, problemtype, deadline=deadline, parts=parts)
    image_parts = [part for part in user_content if part['type'] == 'image_url']
    user_prompt = str(llm_cfg.get('system_prompt') or '').strip()
    prompt = SYSTEM_PROMPT + (IMAGE_PROMPT if image_parts else '')
    prompt += ('\n补充要求：' + user_prompt if user_prompt else '') + '\n始终按上述 JSON 格式输出。'
    payload = {"model": model, "temperature": 0}
    host = (urlsplit(base_url).hostname or '').lower()
    if image_parts:
        # 仅对百炼已支持切换思考模式的视觉模型使用服务商参数。
        dashscope = (host in ('dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com')
                     or host.endswith('.maas.aliyuncs.com'))
        model_name = model.lower()
        hybrid_vision = model_name.startswith((
            'qwen3.8-max', 'qwen3.8-flash', 'qwen3.7-max', 'qwen3.7-plus', 'qwen3.7-flash',
            'qwen3.6-plus', 'qwen3.6-flash', 'qwen3.5-plus', 'qwen3.5-flash', 'qwen3-vl-plus'))
        if dashscope and hybrid_vision and 'thinking' not in model_name:
            payload['enable_thinking'] = False
    mode = llm_cfg.get('structured_mode', 'auto')
    if mode == 'schema':
        schema = RESULT_SCHEMA
        if image_parts:
            schema = dict(schema, properties=dict(schema['properties'], image_unreadable={'type': 'boolean'}),
                          required=[*schema['required'], 'image_unreadable'])
        payload['response_format'] = {'type':'json_schema','json_schema':{
            'name':'classroom_answer','strict':True,'schema':schema}}
    elif mode == 'json' or (mode == 'auto' and (host == 'api.deepseek.com' or host.endswith('.aliyuncs.com'))):
        payload['response_format'] = {'type': 'json_object'}
    request_usages = []

    def request(content):
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": content}]
        response, raw = _request_completion(base_url, api_key, dict(payload, messages=messages),
                                            timeout, deadline, on_retry, active)
        usage = response.get('usage')
        request_usages.append(usage if isinstance(usage, dict) else None)
        data = _answer_data(raw)
        if image_parts:
            _check_image_answer(data, raw, problemtype)
        return data

    started = time.monotonic()
    try:
        data = request(user_content)
    except ImageUnreadableError as error:
        # 经压缩的题图可能丢失细字。仅在同一时限内拿到不同的原图时再请求一次。
        may_be_compressed = any(part['image_url']['url'].startswith('data:image/jpeg;') for part in image_parts)
        if (not may_be_compressed or time.monotonic() >= deadline
                or (active is not None and not active())):
            raise
        try:
            original_content = _build_user_content(problem, problemtype, deadline=deadline,
                                                   parts=parts, prefer_original=True)
        except ImageLoadError:
            raise error from None
        if original_content == user_content or time.monotonic() >= deadline:
            raise
        if on_retry:
            on_retry('模型未能读清题图，正在使用清晰原图重试')
        data = request(original_content)

    if not return_details:
        return _answer_from_data(data, problemtype)
    result = _validate_result(data, problemtype, problem)
    if result:
        result['latency_ms'] = int((time.monotonic() - started) * 1000)
        result['usage'] = _combined_usage(request_usages)
        result['model'] = model
        result['pricing'] = llm_cfg.get('pricing') or {}
    return result


def _combined_usage(usages):
    if len(usages) == 1:
        return usages[0]
    if not all(usages):
        return None

    total = {}
    for field in ('total_tokens', 'prompt_tokens', 'completion_tokens'):
        counts = [usage.get(field) for usage in usages]
        if all(type(count) is int for count in counts):
            total[field] = sum(counts)
    cached = []
    for usage in usages:
        count = usage.get('prompt_cache_hit_tokens')
        details = usage.get('prompt_tokens_details')
        if count is None and isinstance(details, dict):
            count = details.get('cached_tokens')
        cached.append(count)
    if all(type(count) is int for count in cached):
        total['prompt_cache_hit_tokens'] = sum(cached)
    return total or None


def _request_completion(base_url, api_key, payload, timeout, deadline, on_retry, active):
    for attempt in range(3):
        if active is not None and not active():
            raise RuntimeError("题面已更新或题目已结束")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("大模型作答时间预算已耗尽")
        r = None
        try:
            r = requests.post(
                base_url + "/chat/completions",
                headers={"Authorization": "Bearer %s" % api_key},
                json=payload,
                timeout=urllib3.Timeout(total=remaining, connect=min(10, remaining), read=timeout),
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
    return response, content


def stream_explanation(problem, problemtype, answers, llm_cfg, question, on_chunk):
    """单独请求可见解题说明；不会改变题目答案或提交状态。"""
    base = (llm_cfg.get('base_url') or '').rstrip('/')
    if base.endswith('/chat/completions'):
        base = base[:-len('/chat/completions')]
    parts = question_parts(problem)
    llm_cfg = route_config(problem, llm_cfg, parts=parts)
    key, model = llm_cfg.get('api_key', ''), llm_cfg.get('model', '')
    if not base or not key or not model:
        raise RuntimeError('请先配置模型接口、模型名称和密钥')
    content = _build_user_content(problem, problemtype, deadline=time.monotonic()+60, parts=parts)
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


def classify_error(error):
    """将接口错误变成可以直接展示的类别，不向界面泄露完整请求。"""
    chain, current = [], error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__
    text = str(error).lower()
    for item in chain:
        if isinstance(item, ImageUnreadableError):
            return 'image_unreadable', str(item)
        if isinstance(item, ImageLoadError):
            return 'image', str(item)
    if any(isinstance(item, requests.Timeout) for item in chain) or any(word in text for word in ('timeout', 'timed out', '超时', '预算已耗尽', '超过作答时间预算')):
        return 'timeout', '模型请求超时，可继续手动作答或重试'
    if '401' in text or '403' in text:
        return 'auth', '接口鉴权失败，请检查 API Key 和权限'
    if '404' in text:
        return 'configuration', '接口地址或模型不可用，请检查配置'
    if '429' in text:
        return 'rate_limit', '服务商限流或额度不足，请稍后重试并检查账号额度'
    if any(isinstance(item, requests.ConnectionError) for item in chain):
        return 'network', '无法连接模型服务，请检查网络和接口地址'
    if ('image' in text or '图片' in text) and any(word in text for word in ('not support', 'unsupported', '不支持')):
        return 'image', '当前模型不支持该图片输入，请检查“图片题模型”设置'
    if '图片' in text:
        return 'image', '题目图片获取失败，请核对原图后重试'
    if any(word in text for word in ('解析', '格式', '有效答案')):
        return 'format', '模型返回格式异常，未获得有效答案'
    return 'failed', '模型请求失败，请检查配置或稍后重试'


def bounded_call(callback, seconds):
    """为包含图片、请求及重试的完整操作设置总时限；迟到结果不会被采用。"""
    done = threading.Event()
    outcome = {}
    def work():
        try:
            outcome['value'] = callback()
        except Exception as error:
            outcome['error'] = error
        finally:
            done.set()
    threading.Thread(target=work, daemon=True).start()
    if not done.wait(max(0, float(seconds))):
        raise requests.Timeout('模型整体等待超时')
    if 'error' in outcome:
        raise outcome['error']
    return outcome.get('value')


def test_connection(config, vision=False):
    """独立测试生成与答案解析，可使用本地合成的图片。"""
    started = time.monotonic()
    cfg = dict(config)
    cfg['enabled'] = True
    response = {'ok': False, 'status': 'configuration', 'message': '',
                'model': cfg.get('model', ''), 'tested_at': time.time(), 'latency_ms': 0}
    if not all(str(cfg.get(key) or '').strip() for key in ('base_url', 'api_key', 'model')):
        response['message'] = '请填写接口地址、API Key 和模型名称'
        return response
    if vision:
        from PyQt5.QtCore import QBuffer, QIODevice, Qt
        from PyQt5.QtGui import QImage

        colors = [(Qt.blue, '蓝色'), (Qt.red, '红色'), (Qt.green, '绿色'), (Qt.yellow, '黄色')]
        correct = randrange(len(colors))
        image = QImage(256, 256, QImage.Format_RGB32)
        image.fill(colors[correct][0])
        output = QBuffer()
        output.open(QIODevice.WriteOnly)
        if not image.save(output, 'PNG'):
            response.update(status='image', message='无法生成测试图片')
            return response
        url = 'data:image/png;base64,' + base64.b64encode(bytes(output.data())).decode('ascii')
        problem = {'body': '连接测试：请选择图中的颜色。', 'images': [url],
                   'options': [{'key': key, 'value': color} for key, (_, color) in zip('ABCD', colors)]}
        cfg = route_config(problem, cfg)
        response['model'] = cfg['model']
    else:
        problem = {'body': '连接测试：请选择数字 1。', 'options': [{'key': 'A', 'value': '1'}, {'key': 'B', 'value': '2'}]}
    try:
        budget = max(1, min(600, float(cfg.get('total_timeout', cfg.get('timeout', 30)))))
        result = bounded_call(lambda: ask_llm(problem, 1, cfg, time_budget=budget, return_details=True), budget)
        if not result or not result.get('answers'):
            raise RuntimeError('模型未返回有效答案，返回格式异常')
        if vision and result['answers'] != ['ABCD'[correct]]:
            response.update(status='image', message='模型有响应，但没有识别出测试图片的颜色')
        else:
            response.update(ok=True, status='success', message='图片识别和答案解析均通过' if vision else '连接成功，模型生成和答案解析均通过')
    except Exception as error:
        kind, message = classify_error(error)
        response.update(status=kind, message=message)
    response['latency_ms'] = round((time.monotonic() - started) * 1000)
    return response
