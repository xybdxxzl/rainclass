"""兼容中文项目路径，并统一电脑端的项目字体渲染。"""
import os
import sys
import PyQt5
from PyQt5 import QtCore


def configure_qt():
    if sys.platform == 'win32' and QtCore.QCoreApplication.instance() is None:
        # 仅为当前进程选择 Qt 自带的 FreeType，保留用户明确指定的平台。
        os.environ.setdefault('QT_QPA_PLATFORM', 'windows:fontengine=freetype')
    plugins = os.path.join(os.path.dirname(PyQt5.__file__), 'Qt5', 'plugins')
    if os.path.isdir(plugins):
        QtCore.QCoreApplication.addLibraryPath(plugins)


configure_qt()
