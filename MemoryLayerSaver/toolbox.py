import configparser
from contextlib import contextmanager
from pathlib import Path

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsProxyProgressTask
from qgis.PyQt.QtCore import QCoreApplication, QEventLoop


def plugin_version():
    cfg = configparser.ConfigParser()
    cfg.read(Path(__file__).parent / "metadata.txt")
    return cfg.get("general", "version")


def version_tuple(version):
    """'6.1.0' -> (6, 1, 0). Anything unparsable sorts first."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return ()


@contextmanager
def unfiltered_provider(layer):
    """Clear the filter of a layer's provider without the layer noticing. Unlike
    QgsVectorLayer.setSubsetString, this is not refused when the layer is being edited."""
    dp = layer.dataProvider()
    subset = dp.subsetString()
    if not subset:
        yield
        return
    blocked = dp.blockSignals(True)
    try:
        dp.setSubsetString("", False)
        yield
    finally:
        dp.setSubsetString(subset, False)
        dp.blockSignals(blocked)


class Progress:
    """Show the progress of a long main thread operation in the task manager"""

    def __init__(self, title, total=100):
        self._total = max(total, 1)
        self._done = 0
        self._percent = -1
        self._task = QgsProxyProgressTask(title)
        QgsApplication.taskManager().addTask(self._task)

    def advance(self, count):
        self._done += count
        self.set_value(self._done * 100 // self._total)

    def set_value(self, percent):
        if percent != self._percent:
            self._percent = percent
            self._task.setProxyProgress(percent)
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def finish(self):
        self._task.finalize(True)


def log(msg, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(msg, "MemoryLayerSaver", level)


def log_info(msg):
    log(msg)


def log_warning(msg):
    log(msg, Qgis.MessageLevel.Warning)


def log_error(msg):
    log(msg, Qgis.MessageLevel.Critical)
