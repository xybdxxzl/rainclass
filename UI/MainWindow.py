"""电脑端主窗口：课堂工作台、设置入口与后台信号。"""
from PyQt5 import QtCore, QtGui, QtWidgets
from UI.Login import Login_Ui
from UI.Config import Config_Ui
from Scripts.Utils import *
from Scripts.Monitor import monitor
import os
import json
import datetime
import threading
import itertools
from UI.AnswerDialog import AnswerDialog
from UI.Theme import apply_theme, UI_EDITION
from UI.Components import ElidingLabel
from UI.Workspace import Workspace
from UI.HistoryDialog import HistoryDialog
from Scripts.History import HistoryStore
    
class MainWindow_Ui(QtCore.QObject):
    # 需要建立信号槽，解决无法在线程中修改UI值问题
    add_message_signal = QtCore.pyqtSignal(str,int)
    add_course_signal = QtCore.pyqtSignal(list,int)
    del_course_signal = QtCore.pyqtSignal(int)
    review_answer_signal = QtCore.pyqtSignal(object)
    question_signal = QtCore.pyqtSignal(object)
    metrics_signal = QtCore.pyqtSignal(object)
    state_signal = QtCore.pyqtSignal(object)

    def setupUi(self, MainWindow, preview=False):
        self.preview_mode = preview
        self.table_index = []
        self.course_sequence = itertools.count()
        self.is_active = False
        self.review_dialogs = set()
        self.account_id, self.user_name = '', ''
        self.history_store = HistoryStore(':memory:') if preview else HistoryStore()
        self.main_window = MainWindow
        MainWindow.setObjectName('MainWindow')
        MainWindow.resize(780, 600)
        MainWindow.setMinimumSize(720, 520)
        MainWindow.setWindowIcon(QtGui.QIcon(resource_path('UI/Image/app-icon.ico')))
        MainWindow.setWindowTitle('雨课堂助手 · 课堂工作台')
        self.Window = QtWidgets.QWidget(MainWindow)
        self.Window.setObjectName('Window')
        MainWindow.setCentralWidget(self.Window)
        self.verticalLayout = QtWidgets.QVBoxLayout(self.Window)
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.verticalLayout.setSpacing(0)

        self.Menu = QtWidgets.QFrame()
        self.Menu.setObjectName('appHeader')
        self.Menu.setMinimumHeight(60)
        header = QtWidgets.QHBoxLayout(self.Menu)
        header.setContentsMargins(18, 8, 18, 8)
        header.setSpacing(10)
        self.label = QtWidgets.QLabel()
        self.label.setFixedSize(32, 32)
        self.label.setPixmap(QtGui.QPixmap(resource_path('UI/Image/app-icon.png')))
        self.label.setScaledContents(True)
        header.addWidget(self.label)
        brand = QtWidgets.QVBoxLayout()
        brand.setSpacing(0)
        self.label_2 = QtWidgets.QLabel('雨课堂助手')
        self.label_2.setObjectName('appTitle')
        brand.addWidget(self.label_2)
        subtitle = QtWidgets.QLabel('课堂动态 · 答案核对')
        subtitle.setObjectName('appSubtitle')
        brand.addWidget(subtitle)
        header.addLayout(brand)
        edition = QtWidgets.QLabel(UI_EDITION)
        edition.setObjectName('edition')
        edition.setMaximumHeight(26)
        header.addWidget(edition)
        header.addStretch()
        self.active_btn = QtWidgets.QPushButton('开始监听')
        self.active_btn.setObjectName('headerAction')
        self.login_btn = QtWidgets.QPushButton('登录账号')
        self.login_btn.setObjectName('headerSecondary')
        self.config_btn = QtWidgets.QPushButton('设置')
        self.config_btn.setObjectName('headerSecondary')
        self.history_btn = QtWidgets.QPushButton('历史题目')
        self.history_btn.setObjectName('headerSecondary')
        self.history_btn.clicked.connect(self.show_history)
        for button in (self.active_btn, self.login_btn, self.history_btn, self.config_btn):
            button.setMinimumHeight(34)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            header.addWidget(button)
        self.verticalLayout.addWidget(self.Menu)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 4)
        body_layout.setSpacing(8)
        self.verticalLayout.addWidget(body, 1)

        self.Table = QtWidgets.QGroupBox('正在监听的课程')
        self.tableWidget = QtWidgets.QTableWidget(0, 4)
        self.tableWidget.setHorizontalHeaderLabels(['课程名', '课程标题', '教师', '上课时间'])
        self.tableWidget.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tableWidget.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tableWidget.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.tableWidget.verticalHeader().hide()
        self.tableWidget.setAlternatingRowColors(True)
        self.tableWidget.setShowGrid(False)
        self.tableWidget.verticalHeader().setDefaultSectionSize(32)
        table_layout = QtWidgets.QVBoxLayout(self.Table)
        table_layout.addWidget(self.tableWidget)
        self.Table.setMaximumHeight(120)
        self.Table.hide()

        self.Output = QtWidgets.QGroupBox('运行日志')
        self.output_textarea = QtWidgets.QTextBrowser()
        self.output_textarea.setMinimumHeight(40)
        self.output_textarea.document().setMaximumBlockCount(1500)
        output_layout = QtWidgets.QVBoxLayout(self.Output)
        output_layout.addWidget(self.output_textarea)
        self.Output.setMaximumHeight(120)
        self.Output.hide()

        self.workspace = Workspace(self)
        body_layout.addWidget(self.workspace, 1)
        body_layout.addWidget(self.Table)
        log_row = QtWidgets.QHBoxLayout()
        self.log_toggle = QtWidgets.QToolButton()
        self.log_toggle.setText('运行日志')
        self.log_toggle.setProperty('role', 'link')
        self.log_toggle.setCheckable(True)
        self.log_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.log_toggle.setArrowType(QtCore.Qt.RightArrow)
        self.log_toggle.toggled.connect(self.toggle_logs)
        log_row.addWidget(self.log_toggle)
        self.latest_log = ElidingLabel('就绪，等待课堂动态')
        self.latest_log.setObjectName('muted')
        log_row.addWidget(self.latest_log, 1)
        body_layout.addLayout(log_row)
        body_layout.addWidget(self.Output)

        self.statusbar = QtWidgets.QStatusBar(MainWindow)
        MainWindow.setStatusBar(self.statusbar)
        self.statusbar.showMessage('先核对答案，再决定提交方式')
        self.statusbar.setToolTip('当前项目：' + os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.config_btn.clicked.connect(self.show_config)
        self.login_btn.clicked.connect(self.show_login)
        self.active_btn.clicked.connect(self.active_clicked)
        self.add_message_signal.connect(self.add_message)
        self.add_course_signal.connect(self.add_course)
        self.del_course_signal.connect(self.del_course)
        self.review_answer_signal.connect(self.show_answer_review)
        self.question_signal.connect(self.workspace.show_question)
        self.metrics_signal.connect(self.workspace.update_metrics)
        self.state_signal.connect(self.update_state)
        QtWidgets.qApp.aboutToQuit.connect(self.cancel_reviews)
        apply_theme(MainWindow)

        self.config = get_initial_data() if preview else self.check_config(get_config_dir(), get_config_path())
        self.workspace.load_config()
        self.add_message_signal.emit('电脑端界面：' + UI_EDITION, 0)
        self.add_message_signal.emit('初始化完成', 0)
        if preview:
            self.workspace.connection.setText('● 界面预览')
            self.statusbar.showMessage('界面预览 · 示例数据，不连接课堂')
            self.active_btn.setEnabled(False)
            self.login_btn.setEnabled(False)
        else:
            status, user_info = self.check_login()
            if status:
                self.apply_account(user_info)
                self.add_message_signal.emit('登录成功，当前登录用户：' + user_info['name'], 0)
            else:
                self.workspace.connection.setText('● 未登录')
                self.show_login()

    def toggle_courses(self, expanded):
        if expanded:
            self.log_toggle.setChecked(False)
        self.Table.setVisible(expanded)

    def toggle_logs(self, expanded):
        if expanded:
            self.workspace.courses_btn.setChecked(False)
        self.log_toggle.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        self.Output.setVisible(expanded)

    def add_course(self, row, row_count):
        # 添加课程
        # 注意：在非UI运行线程中调用该方法请对add_course_signal发送信号
        visual_row = self.tableWidget.rowCount()
        self.tableWidget.insertRow(visual_row)
        # 装填当前行各列数据
        for i in range(len(row)):
            content = QtWidgets.QTableWidgetItem(str(row[i]))
            self.tableWidget.setItem(visual_row,i,content)

        # 存储各行PersistentModelIndex，便于后续删除操作
        model_index = self.tableWidget.indexFromItem(content)
        per_model_index = QtCore.QPersistentModelIndex(model_index)
        self.table_index.append((row_count,per_model_index))
        self.update_course_status()

    def del_course(self, index):
        # 删除课程
        # 注意：在非UI运行线程中调用该方法请对del_course_signal发送信号
        for row_count,per_model_index in self.table_index:
            # 搜索传入index所对应的QPersistentModelIndex，并进行删除
            # QPersistentModelIndex对象的索引在删除过程中不会被已删除元素影响
            if row_count == index:
                self.tableWidget.removeRow(per_model_index.row())
                self.table_index.remove((row_count,per_model_index))
                self.update_course_status()

    def update_course_status(self):
        rows = []
        for i in range(self.tableWidget.rowCount()):
            name, teacher = self.tableWidget.item(i,0), self.tableWidget.item(i,2)
            if name:
                rows.append('%s · %s' % (name.text(), teacher.text() if teacher else '教师未知'))
        self.workspace.course.setText('；'.join(rows) if rows else '暂无正在进行的课程')
        self.workspace.course.setToolTip('\n'.join(rows) if rows else '暂无正在监听的课程')

    def show_answer_review(self, review):
        if self.account_id and getattr(review, 'account_id', '') and review.account_id != self.account_id:
            review.finish(cancel=True)
            return
        if not self.is_active and not review.event.is_set():
            review.finish(cancel=True)
        review.display_duration = max(1, (review.deadline if review.auto_submit else (review.question_deadline or review.deadline)) - __import__('time').monotonic())
        self.workspace.add_review(review)
        QtWidgets.QApplication.alert(self.main_window)

    def apply_account(self, user_info):
        account_id = str(user_info.get('id') or user_info.get('user_id') or user_info.get('uid') or '')
        if self.account_id and account_id and self.account_id != account_id:
            self.workspace.reviews.clear()
            self.workspace.active = None
            self.workspace.pending_question = None
            self.workspace.pending.clear()
            self.workspace.pending.addItem('暂无收到的题目', None)
            self.workspace.manual_scroll.hide()
            self.workspace.question.setPlainText('账号已切换，等待新账号的课堂题目。')
            self.workspace.monitor_details.clear()
            self.workspace.lesson_states.clear()
        self.account_id = account_id
        self.user_name = str(user_info.get('name') or '未提供姓名')
        self.workspace.connection.setText('当前用户：%s · 账号已验证' % self.user_name)
        self.workspace.connection.setStyleSheet('color: #1c765b;')
        self.login_btn.setText('更换账号')
        self.workspace.refresh()

    def update_state(self, data):
        if data.get('generation') and data['generation'] != getattr(self, 'monitor_generation', None):
            return
        if self.account_id and data.get('account_id') and str(data['account_id']) != self.account_id:
            return
        if data.get('kind') == 'monitor' and data.get('state') in ('expired', 'stopped'):
            self.is_active = False
            self.active_btn.setEnabled(True)
            self.active_btn.setText('开始监听')
        if data.get('kind') == 'account':
            if data.get('status') in ('invalid', 'expired', 'failed'):
                self.workspace.connection.setText('当前用户：%s · 登录已失效' % (self.user_name or '未知'))
                self.workspace.connection.setStyleSheet('color: #a7352f;')
            elif data.get('name'):
                self.apply_account({'id': data.get('account_id', self.account_id), 'name': data['name']})
        self.workspace.update_state(data)
        review = data.get('review')
        if review:
            dialog = self.review_dialog_for(review)
            if dialog and not dialog.isActiveWindow():
                dialog.set_answers(review.answers)

    def show_history(self):
        dialog = HistoryDialog(self.history_store, self.account_id, self.user_name, self.main_window)
        dialog.exec_()

    def review_dialog_for(self, review):
        return next((dialog for dialog in self.review_dialogs if dialog.review is review), None)

    def open_review_dialog(self, review):
        if review.event.is_set():
            return
        dialog = self.review_dialog_for(review)
        if dialog:
            dialog.raise_()
            dialog.activateWindow()
            return
        dialog = AnswerDialog(review, self.main_window)
        self.review_dialogs.add(dialog)
        dialog.finished.connect(lambda: self.review_dialogs.discard(dialog))
        dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        QtWidgets.QApplication.alert(self.main_window)

    def cancel_reviews(self):
        self.is_active = False
        for review in self.workspace.reviews:
            if not review.event.is_set():
                review.finish(cancel=True)
        for dialog in list(self.review_dialogs):
            dialog.reject()

    def show_config(self):
        # 展示配置对话框
        dialog = QtWidgets.QDialog(self.main_window)
        config_ui = Config_Ui()
        config_ui.setupUi(dialog, preview=self.preview_mode)
        # 加载配置文件
        config_ui.load_config(self.config)
        # 这里需要刷新一次自定义延迟部分的Widget
        config_ui.enable_delay_custom()
        # 刷新大模型配置部分的Widget启用状态
        config_ui.enable_llm_config()
        if dialog.exec_():
            # Config_Ui 成功保存后原位更新共享配置，现有课程立即读取新设置。
            self.add_message_signal.emit('设置已保存，后续题目将使用新配置', 0)
            self.workspace.load_config()

    def save_runtime_config(self):
        if self.preview_mode:
            return
        path = get_config_path()
        output = QtCore.QSaveFile(path)
        try:
            if not output.open(QtCore.QIODevice.WriteOnly):
                raise OSError('无法打开配置文件')
            payload = json.dumps(self.config, ensure_ascii=True, indent=2).encode('utf-8')
            if output.write(payload) != len(payload) or not output.commit():
                raise OSError('无法写入配置文件')
        except OSError:
            self.add_message_signal.emit('设置保存失败，本次更改仅在当前运行中有效', 0)

    def show_login(self, _bool=False, rtn_message=""):
        if self.is_active:
            self.deactive()
            QtCore.QTimer.singleShot(200, self.login_when_stopped)
            return
        # 展示登录对话框
        # rtn_message用于展示上次扫码登录失败的信息
        dialog = QtWidgets.QDialog()
        login_ui = Login_Ui()
        login_ui.setupUi(dialog)
        # 加载配置文件，主要用于加载sessionid
        login_ui.load_config(self.config)

        # 将最下方登录返回值栏设置rtn_message
        login_ui.login_return.setText(rtn_message)

        # 登录成功返回1，其他情况返回0
        success = dialog.exec_()
        if success:
            config_route = get_config_path()
            with open(config_route,"r") as f:
                self.config =  json.load(f)
        # 删除涉及登录的线程
        login_ui.close_all()
        # 再次检测登录状态
        status, user_info = self.check_login()
        if status and success:
            self.apply_account(user_info)
            self.workspace.load_config()
            self.add_message_signal.emit("登录成功，当前登录用户："+user_info["name"],0)
            self.login_btn.setText("重新登录")
        if not status:
            self.workspace.connection.setText('未登录 · 请登录雨课堂')
            self.workspace.connection.setStyleSheet('color: #a7352f;')
            self.statusbar.showMessage('账号尚未验证，请重新登录')

    def login_when_stopped(self):
        if getattr(self, 'monitor_t', None) and self.monitor_t.is_alive():
            QtCore.QTimer.singleShot(200, self.login_when_stopped)
            return
        self.show_login()
        
    def check_config(self, dir_route, config_route):
        # 检查配置文件
        # 检查目录是否存在
        if not os.path.exists(dir_route):
            os.makedirs(dir_route)
        # 检查配置文件存在性及可用性
        if not os.path.exists(config_route):
            initial_data = get_initial_data()
            legacy = os.path.join(os.environ.get('APPDATA', ''), 'RainClassroomAssistant', 'config.json')
            if os.path.isfile(legacy):
                try:
                    with open(legacy, 'r', encoding='utf-8-sig') as old:
                        initial_data.update(json.load(old))
                except (OSError, ValueError):
                    pass
            f = open(config_route,"w+")
            json.dump(initial_data,f)
            f.close()
            self.add_message_signal.emit("没有检测到配置文件，已自动创建",0)
            return initial_data
        else:
            try:
                with open(config_route,"r") as f:
                    data = json.load(f)
                    # 补齐旧配置中缺失的新配置项
                    for key, value in get_initial_data().items():
                        if key not in data:
                            data[key] = value
                    self.add_message_signal.emit("配置文件已读取",0)
                    return data
            except:
                with open(config_route,"w+") as f:
                    initial_data = get_initial_data()
                    json.dump(initial_data,f)
                    self.add_message_signal.emit("配置文件读取失败，已重新生成",0)
                    return initial_data

    def check_login(self):
        # 检查登录状态
        try:
            code, user_info = get_user_info(self.config["sessionid"])
        except Exception:
            return False, {}
        if code == 50000:
            return False,user_info
        elif code == 0:
            return True,user_info
        return False, user_info
            
    def add_message(self, message, type=0):
        # 新增输出信息，并尝试语音播报
        time = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S] ")
        self.output_textarea.append(__import__('html').escape(time + message))
        self.latest_log.setText(time[-11:] + message)
        self.latest_log.setToolTip(time + message)
        if message == '网络异常，监听中断':
            self.workspace.monitor_state.setText('· 网络异常，正在重连')
        elif message == '网络已恢复，监听开始':
            self.workspace.monitor_state.setText('· 网络恢复，正在检查课堂')
        if not type == 0:
            self.audio(message,type)

    def active_clicked(self):
        # 启动按钮被点击
        if self.is_active:
            self.deactive()
        else:
            self.active()

    def active(self):
        # 启动
        if self.is_active:
            return
        self.is_active = True
        self.monitor_t = threading.Thread(target=monitor,args=(self,),daemon=True)
        self.monitor_t.start()
        self.active_btn.setText("暂停监听")
        self.workspace.monitor_state.setText("· 正在检测课程")
        self.add_message_signal.emit("启动成功",0)
    
    def deactive(self):
        # 停止
        self.active_btn.setText("停止中...")
        self.active_btn.setEnabled(False)
        # 强制刷新UI
        QtWidgets.qApp.processEvents()
        self.cancel_reviews()
        self._finish_stop()

    def _finish_stop(self):
        if self.monitor_t.is_alive():
            QtCore.QTimer.singleShot(200, self._finish_stop)
            return
        self.active_btn.setEnabled(True)
        self.active_btn.setText("开始监听")
        self.workspace.monitor_state.setText("· 已暂停")
        self.add_message_signal.emit("停止成功",0)

    def audio(self, message, type):
        '''
        type
        0: 默认，未分类音频
        1: 自动发送弹幕成功
        2: 他人弹幕发送
        3: 收到题目
        4: 自动答题情况
        5: 自己被点名
        6: 他人被点名
        7: 课程相关
        8: 网络断开/重连
        '''
        # 尝试播放语音提示
        audio_on = self.config["audio_on"]
        if audio_on:
            audio_type = self.config["audio_config"]["audio_type"]
            if \
            (type == 1 and audio_type["send_danmu"]) or \
            (type == 2 and audio_type["others_danmu"]) or \
            (type == 3 and audio_type["receive_problem"]) or \
            (type == 4 and audio_type["answer_result"]) or \
            (type == 5 and audio_type["im_called"]) or \
            (type == 6 and audio_type["others_called"]) or \
            (type == 7 and audio_type["course_info"]) or \
            (type == 8 and audio_type["network_info"]) :
                threading.Thread(target=say_something,args=(message,),daemon=True).start()
