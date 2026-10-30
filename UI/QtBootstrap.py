"""兼容中文项目路径：Qt wheel 内置的插件路径可能被转换为问号。"""
import os
import PyQt5
from PyQt5 import QtCore


def configure_qt():
    plugins = os.path.join(os.path.dirname(PyQt5.__file__), 'Qt5', 'plugins')
    if os.path.isdir(plugins):
        QtCore.QCoreApplication.addLibraryPath(plugins)


configure_qt()
