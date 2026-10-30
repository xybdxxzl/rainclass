"""生成离线界面预览，不连接课堂、不读取用户配置、不提交答案。"""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtWidgets, QtGui
from UI.MainWindow import MainWindow_Ui
from UI.Config import Config_Ui
from UI.AnswerDialog import AnswerDialog
from Scripts.AnswerReview import AnswerReview
from Scripts.Utils import get_initial_data
from UI.Theme import STYLE

app = QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
app.setStyle('Fusion')
app.setStyleSheet(STYLE)
out = Path(__file__).parent / 'previews'
out.mkdir(exist_ok=True)
with patch.object(MainWindow_Ui, 'check_config', return_value=get_initial_data()), \
     patch.object(MainWindow_Ui, 'check_login', return_value=(True, {'name': '演示用户'})):
    main = QtWidgets.QMainWindow()
    ui = MainWindow_Ui()
    ui.setupUi(main)
    ui.add_course(['Web前端开发', 'HTML 与 CSS', '张老师', '14:00–15:35'], 0)
    ui.add_message('Web前端开发已加入监听列表', 0)
    ui.add_message('答案已生成，请在弹窗中核对，15 秒后自动提交。', 0)
    sample = AnswerReview('Web前端开发', {'body': 'HTML 中，哪个元素用于创建超链接？',
                         'options': [{'key':'A','value':'div'}, {'key':'B','value':'a'},
                                     {'key':'C','value':'span'}, {'key':'D','value':'p'}]},
                          1, ['B'], 30, result={'confidence':0.9,'explanation':'a 元素用于定义超链接。','question_type':'单选题'})
    ui.workspace.add_review(sample)
    main.show()
    app.processEvents()
    main.grab().save(str(out / 'main.png'))
    ui.workspace.detail_toggle.setChecked(True)
    ui.workspace.detail_output.setPlainText('超链接由 a 元素定义。href 属性指定跳转目标。')
    app.processEvents()
    main.grab().save(str(out / 'main-details.png'))
    ui.workspace.detail_toggle.setChecked(False)
    settings = QtWidgets.QDialog()
    config = Config_Ui()
    config.setupUi(settings)
    config.load_config(get_initial_data())
    config.llm_on.setChecked(True)
    config.provider_select.setCurrentIndex(1)
    settings.show()
    app.processEvents()
    settings.grab().save(str(out / 'settings.png'))
    config.tabs.setCurrentIndex(1)
    app.processEvents()
    settings.grab().save(str(out / 'settings-answer.png'))
    config.tabs.setCurrentIndex(2)
    app.processEvents()
    settings.grab().save(str(out / 'settings-notices.png'))
    config.tabs.setCurrentIndex(0)
    settings.resize(640, 480)
    app.processEvents()
    settings.grab().save(str(out / 'settings-small.png'))
    review = AnswerReview('Web前端开发', {'body': '<p>HTML 中，哪个元素用于创建超链接？</p>',
                          'options': [{'key': 'A', 'value': '&lt;div&gt;'}, {'key': 'B', 'value': '&lt;a&gt;'},
                                      {'key': 'C', 'value': '&lt;span&gt;'}, {'key': 'D', 'value': '&lt;p&gt;'}]}, 1, ['B'], 15)
    dialog = AnswerDialog(review)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(out / 'answer.png'))
    dialog.reject()
    settings.reject()
    main.close()
print(out)
