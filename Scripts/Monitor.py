"""课程发现与连接状态；线程启动不代表课堂推送已就绪。"""
import threading
import time
import uuid
from Scripts.Utils import get_on_lesson
from Scripts.Classes import Lesson


def monitor(main_ui):
    lessons = {}
    lock = threading.RLock()
    sessionid = main_ui.config.get('sessionid', '')
    generation = uuid.uuid4().hex
    main_ui.monitor_generation = generation
    expired = False
    def state(**data):
        if main_ui.config.get('sessionid') != sessionid or main_ui.monitor_generation != generation:
            return
        signal = getattr(main_ui, 'state_signal', None)
        if signal is not None:
            signal.emit(dict(kind='monitor', generation=generation, account_id=str(getattr(main_ui, 'account_id', '')), **data))
    def active():
        return main_ui.is_active and main_ui.config.get('sessionid') == sessionid and main_ui.monitor_generation == generation
    def pause(seconds):
        deadline = time.monotonic() + seconds
        while active() and time.monotonic() < deadline:
            time.sleep(.2)
        return active()
    def finished(lesson):
        with lock:
            lessons.pop(str(lesson.lessonid), None)
    try:
        state(state='scanning', status='正在检测在课课程')
        while active():
            retry = 30
            try:
                found = get_on_lesson(sessionid)
                state(last_check=time.time())
                if not found:
                    state(state='waiting', status='课程检测正常，暂无正在上课的课程')
                for row in found:
                    lesson_id = str(row['lessonId'])
                    with lock:
                        if lesson_id in lessons:
                            continue
                    lesson = Lesson(row['lessonId'], row['courseName'], row['classroomId'], main_ui)
                    with lock:
                        lessons[lesson_id] = lesson
                    state(state='connecting', status='正在加入课堂', course=row['courseName'], lesson_id=lesson_id)
                    threading.Thread(target=lesson.start_lesson, args=(finished,), daemon=True).start()
            except PermissionError:
                expired = True
                main_ui.is_active = False
                state(state='expired', status='登录已失效，请重新登录')
                signal = getattr(main_ui, 'state_signal', None)
                if signal is not None:
                    signal.emit({'kind': 'account', 'status': 'expired'})
                main_ui.add_message_signal.emit('登录已失效，课堂监听已暂停', 8)
                break
            except Exception:
                retry = 5
                state(state='reconnecting', status='课程检测失败，5 秒后重试；已有课堂连接单独显示状态')
                main_ui.add_message_signal.emit('课程检测失败，正在重试', 0)
            if not pause(retry):
                break
    finally:
        with lock:
            pending = list(lessons.values())
        for lesson in pending:
            lesson.closed = True
            for review in list(lesson.reviews.values()):
                review.finish(cancel=True)
            websocket = getattr(lesson, 'wsapp', None)
            if websocket is not None:
                websocket.close()
        state(state='expired' if expired else 'stopped', status='登录已失效，请重新登录' if expired else '监听已停止')
