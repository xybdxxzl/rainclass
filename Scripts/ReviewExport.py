"""用本地图片分别导出课件、习题，支持逐题答案或答案附录。"""
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from PyQt5 import QtCore, QtGui
from Scripts.Review import ReviewError, check_cancel


def text(value):
    if isinstance(value, list):
        return '；'.join(text(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value if value is not None else '')


def build_html(records, root, answers=True, courseware=True, personal=False, asset_dir=None, cancel=None,
               questions=True, answer_position='appendix'):
    if not records:
        raise ReviewError('当前范围没有已保存的课堂资料，请先采集')
    if not courseware and not questions:
        raise ReviewError('请至少选择课件或习题')
    if answer_position not in ('inline', 'appendix'):
        raise ReviewError('不支持的答案位置')
    root = Path(root).resolve()
    escaped = lambda value: html.escape(text(value)).replace('\n', '<br>')

    def image(relative, compact=False):
        if cancel is not None:
            check_cancel(cancel)
        if not relative:
            return '<p class="missing">图片未获取</p>'
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            return '<p class="missing">图片文件缺失，请重新采集</p>'
        picture = QtGui.QImage(str(path))
        if picture.isNull():
            return '<p class="missing">图片无法解码，请重新采集</p>'
        if asset_dir is not None:
            Path(asset_dir).mkdir(parents=True, exist_ok=True)
            target = Path(asset_dir) / path.name
            shutil.copyfile(path, target)
            src = Path(asset_dir).name + '/' + path.name
        else:
            src = QtCore.QUrl.fromLocalFile(str(path)).toString()
        width, height = (580, 500) if compact else (660, 760)
        scale = min(width / picture.width(), height / picture.height(), 1)
        return '<p style="line-height:100%%;margin-top:8px;margin-bottom:8px"><img src="%s" width="%d" height="%d"></p>' % (html.escape(src, quote=True), picture.width() * scale, picture.height() * scale)

    def answer_block(question):
        parts = ['<p><b>官方答案：</b>%s</p>' % escaped('投票题，不适用标准答案' if question.get('type') == 3 else question.get('official_answer') or '未获取到已公布的官方答案')]
        if question.get('official_answer'):
            parts.append('<p class="meta">答案来源：雨课堂题目详情。</p>')
        if question.get('official_explanation') or question.get('explanation_images'):
            parts.append('<p><b>教师提供的解析：</b>%s</p>' % escaped(question.get('official_explanation', '')))
            parts.extend(image(p) for p in question.get('explanation_images', []))
        if question.get('ai_answer'):
            parts.append('<p><b>AI 参考答案（待核对）：</b>%s</p><p>%s</p>' % (escaped(question['ai_answer']), escaped(question.get('ai_explanation', ''))))
        elif question.get('ai_error'):
            parts.append('<p class="missing">AI：%s</p>' % escaped(question['ai_error']))
        if personal:
            parts.append('<p><b>个人作答（不作为标准答案）：</b>%s</p>' % escaped(question.get('personal_answer') or '无文字作答'))
            parts.extend(image(p) for p in question.get('images', []))
        return ''.join(parts)

    title = '期末复习册' if courseware and questions else ('课堂习题与答案' if questions and answers else '课堂习题' if questions else '课件归档')
    result = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>%s</title>' % title,
        '<style>body{font-family:"Microsoft YaHei","Noto Sans SC",sans-serif;color:#172c42;font-size:11pt;line-height:1.5}',
        'h1{font-size:24pt}h2{font-size:18pt;color:#164b75}h3{font-size:13pt}.missing{color:#9b4438}',
        '.meta{color:#596c7a;font-size:10pt}.appendix{page-break-before:always}.lesson{page-break-before:always}',
        'img{max-width:100%;height:auto}a{color:#164b75}</style></head><body><h1>' + title + '</h1>',
        '<p>课件仅含当前账号可访问的页面，不代表教师原始完整 PPT。</p>' if courseware else '<p>习题按课程与课次汇集，保留题目原页码。</p>',
        '<p>官方答案、个人作答和 AI 参考答案分别标注。AI 参考答案未经教师核验。</p>' if questions and answers else '',
        '<p class="meta">本册包含 %d 个课次；%d 个课次有未完成或失败项。</p>' % (len(records), sum(r.get('status') != 'complete' for r in records)),
        '<h2>目录</h2>']
    ordered = sorted((r for r in records if courseware or r.get('questions')),
                     key=lambda r: (r.get('course', ''), r.get('date', ''), r['id']))
    if not ordered:
        result.append('<p>当前归档范围没有课堂习题。</p>')
    for i, record in enumerate(ordered):
        if cancel is not None:
            check_cancel(cancel)
        result.append('<p><a href="#lesson-%d">%s / %s / %s</a></p>' % (i, escaped(record['course']), escaped(record['date']), escaped(record['title'])))
    numbered = []
    for i, record in enumerate(ordered):
        result.append('<div class="lesson"><a name="lesson-%d"></a><h2>%s / %s</h2><h3>%s</h3>' %
                      (i, escaped(record['course']), escaped(record['date']), escaped(record['title'])))
        for issue in record.get('issues', []):
            result.append('<p class="missing">未完成项：%s</p>' % escaped(issue))
        if courseware:
            for pres_index, presentation in enumerate(record.get('presentations', [])):
                result.append('<h3%s>课件：%s</h3>' % (' style="page-break-before:always"' if pres_index else '', escaped(presentation['name'])))
                if presentation.get('error'):
                    result.append('<p class="missing">%s</p>' % escaped(presentation['error']))
                for slide_index, slide in enumerate(presentation.get('slides', [])):
                    result.append('<div%s><p class="meta">原页码 P%s</p>%s</div>' %
                        (' style="page-break-before:always"' if slide_index else '', escaped(slide.get('page')), image(slide.get('image'))))
        if not questions:
            result.append('</div>')
            continue
        result.append('<h3%s>课堂习题（%d 道）</h3>' % (' style="page-break-before:always"' if courseware else '', len(record.get('questions', []))))
        for question_index, question in enumerate(record.get('questions', [])):
            number = len(numbered) + 1
            numbered.append((number, record, question))
            result.append('<div%s><a name="question-%d"></a><h3>第 %d 题 · %s · 原页码 P%s</h3>' %
                          (' style="page-break-before:always"' if question_index else '', number, number, {1:'单选', 2:'多选', 3:'投票', 4:'填空', 5:'主观'}.get(question.get('type'), '题型待确认'), escaped(question.get('page'))))
            if question.get('error'):
                result.append('<p class="missing">%s</p>' % escaped(question['error']))
            if question.get('image'):
                result.append(image(question['image'], compact=answers and answer_position == 'inline'))
            else:
                result.append('<p>%s</p>' % escaped(question.get('body') or '题面未获取'))
                result.extend(image(p) for p in question.get('body_images', []))
                for option in question.get('options', []):
                    result.append('<p>%s. %s</p>' % (escaped(option['key']), escaped(option['text'])))
                    result.extend(image(p) for p in option.get('images', []))
            if answers:
                if answer_position == 'inline':
                    result.append('<div class="answer">' + answer_block(question) + '</div>')
                else:
                    result.append('<p class="meta">答案见附录第 %d 题。</p>' % number)
            result.append('</div>')
        result.append('</div>')
    if answers and questions and answer_position == 'appendix':
        result.append('<div class="appendix"><h1>答案与解析附录</h1>')
        for number, record, question in numbered:
            result.append('<h3>第 %d 题</h3><p class="meta">%s / %s / %s / 原页码 P%s</p>' %
                (number, escaped(record['course']), escaped(record['date']), escaped(record['title']), escaped(question.get('page'))))
            result.append(answer_block(question))
        result.append('</div>')
    result.append('</body></html>')
    return ''.join(result)


def export_book(path, records, root, answers=True, courseware=True, personal=False, cancel=None,
                questions=True, answer_position='appendix'):
    """PDF 使用已有 Qt 打印库，不增加依赖；HTML 与同名资源目录一起移动。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == '.html':
        content = build_html(records, root, answers, courseware, personal, path.parent / (path.stem + '_assets'), cancel,
                             questions, answer_position)
        if cancel is not None:
            check_cancel(cancel)
        temporary = path.with_suffix('.html.part')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
    elif path.suffix.lower() == '.pdf':
        if not records:
            raise ReviewError('当前范围没有已保存的课堂资料，请先采集')
        if cancel is not None:
            check_cancel(cancel)
        # Qt 字体缓存和 Windows 驱动在跨线程渲染时可能不稳定，独立进程绘制。
        with tempfile.TemporaryDirectory(prefix='rain-review-pdf-') as folder:
            request = Path(folder) / 'request.json'
            request.write_text(json.dumps({'path': str(path.resolve()), 'root': str(Path(root).resolve()),
                'records': records, 'answers': answers, 'courseware': courseware, 'personal': personal,
                'questions': questions, 'answer_position': answer_position}, ensure_ascii=False), encoding='utf-8')
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
            command = [sys.executable, '--render-review', str(request)] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'Scripts.ReviewExport', '--render-request', str(request)]
            process = subprocess.Popen(command,
                cwd=str(Path(__file__).resolve().parents[1]), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            try:
                while process.poll() is None:
                    if cancel is not None and cancel.wait(.1):
                        process.terminate()
                        process.wait(timeout=5)
                        path.with_suffix('.pdf.part').unlink(missing_ok=True)
                        check_cancel(cancel)
                    elif cancel is None:
                        time.sleep(.05)
                if process.returncode != 0:
                    raise ReviewError('PDF 渲染进程失败，请检查字体、图片和目标文件是否被占用')
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
    else:
        raise ReviewError('仅支持 PDF 或 HTML 导出')


def render_request(request_path):
    """供后台子进程调用；不读取登录配置、不进行任何网络请求。"""
    from PyQt5 import QtWidgets
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    app = QtWidgets.QApplication([])
    from UI.Theme import prepare_fonts
    prepare_fonts(app)
    _render_pdf(Path(request['path']), request['records'], Path(request['root']),
                request['answers'], request['courseware'], request['personal'],
                request.get('questions', True), request.get('answer_position', 'appendix'))
    return 0


def _render_pdf(path, records, root, answers, courseware, personal, questions=True, answer_position='appendix'):
    content = build_html(records, root, answers, courseware, personal, questions=questions, answer_position=answer_position)
    document = QtGui.QTextDocument()
    document.setDefaultFont(QtGui.QFont('Microsoft YaHei', 11))
    document.setHtml(content)
    temporary = path.with_suffix('.pdf.part')
    writer = QtGui.QPdfWriter(str(temporary))
    writer.setResolution(96)
    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    writer.setPageMargins(QtCore.QMarginsF(14, 14, 14, 14), QtGui.QPageLayout.Millimeter)
    writer.setTitle('期末复习册')
    document.print_(writer)
    del writer
    if not temporary.exists() or temporary.stat().st_size < 100:
        raise ReviewError('PDF 输出失败，请检查保存位置')
    temporary.replace(path)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--render-request':
        raise SystemExit(render_request(sys.argv[2]))
    raise SystemExit('该模块仅由期末复习导出功能调用')
