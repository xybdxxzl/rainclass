import re
import json
import base64
import time
import requests
import urllib3
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

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
)


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


def ask_llm(problem, problemtype, llm_cfg, time_budget=None, on_retry=None):
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
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_content(problem, problemtype, deadline=deadline)},
        ],
    }
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
        content = r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        # 把401/404等错误响应体带出来，便于排查（裸HTTPError没有正文）
        raise RuntimeError("大模型返回内容无法解析（HTTP %s）：%s" % (r.status_code, r.text[:300]))
    return _parse_answer(content, problemtype)
