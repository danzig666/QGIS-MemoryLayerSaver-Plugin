"""Regression tests, to run with the Python of a QGIS installation (no display needed):

    python-qgis tests/test_regressions.py <dir containing MemoryLayerSaver> <work dir>
"""

import os
import shutil
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

PLUGIN_PARENT, WORK = sys.argv[1], Path(sys.argv[2])
WORK.mkdir(parents=True, exist_ok=True)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QGIS_CUSTOM_CONFIG_PATH"] = str(WORK / "profile")

from qgis.PyQt.QtCore import (  # noqa: E402
    QCoreApplication,
    QDataStream,
    QDate,
    QDateTime,
    QDir,
    QEvent,
    QFile,
    QIODevice,
    QMetaType,
    QSettings,
    QTime,
    QTimeZone,
)
from qgis.PyQt.QtWidgets import QMainWindow, QMenu, QMessageBox  # noqa: E402

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(WORK / "settings"))
QCoreApplication.setOrganizationName("MLSTests")
QCoreApplication.setApplicationName("MLSTests")

from qgis.core import (  # noqa: E402
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsReferencedGeometry,
    QgsSettings,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMessageBar  # noqa: E402

app = QgsApplication([], True)
app.initQgis()
assert "MLSTests" in QgsSettings().fileName(), QgsSettings().fileName()


class RecordingBar(QgsMessageBar):
    def __init__(self, parent):
        super().__init__(parent)
        self.messages = []

    def pushMessage(self, *args):
        self.messages.append(str(args))

    def pushWidget(self, widget, *args):
        self.messages.append(widget.text() if hasattr(widget, "text") else str(widget))
        return widget


class Iface:
    def __init__(self):
        self.win = QMainWindow()
        self.menu = QMenu(self.win)
        self.bar = RecordingBar(self.win)

    def mainWindow(self):
        return self.win

    def pluginMenu(self):
        return self.menu

    def messageBar(self):
        return self.bar


import qgis.utils  # noqa: E402

iface = qgis.utils.iface = Iface()
sys.path.insert(0, PLUGIN_PARENT)

dialogs = []
answer = {"value": QMessageBox.StandardButton.No}


def fake_box(kind):
    def box(*args, **kwargs):
        dialogs.append((kind, str(args[1:])))
        return answer["value"]

    return staticmethod(box)


for kind in ("information", "warning", "critical", "about", "question"):
    setattr(QMessageBox, kind, fake_box(kind))
slot_errors = []
sys.excepthook = lambda t, v, tb: slot_errors.append(f"{t.__name__}: {v}")

from MemoryLayerSaver import layer_tools  # noqa: E402
from MemoryLayerSaver.memory_layer_saver import MemoryLayerSaver  # noqa: E402
from MemoryLayerSaver.mldata import FORMAT_V2, FORMAT_V3, MldataError  # noqa: E402
from MemoryLayerSaver.reader import Reader  # noqa: E402
from MemoryLayerSaver.settings import FORMAT_ALWAYS_V2, FORMAT_ALWAYS_V3, FORMAT_AUTO, Settings  # noqa: E402
from MemoryLayerSaver.settings_dialog import SettingsDialog  # noqa: E402
from MemoryLayerSaver.writer import Writer  # noqa: E402

proj = QgsProject.instance()
plugin = MemoryLayerSaver()
plugin.initGui()


def layer(name, n, fields="a:integer&field=s:string(20)", add=True):
    lyr = QgsVectorLayer(f"Point?crs=EPSG:4326&field={fields}", name, "memory")
    feats = []
    for i in range(n):
        f = QgsFeature(lyr.fields())
        f.setAttribute(0, i)
        f.setGeometry(QgsGeometry.fromWkt(f"POINT({i} {i})"))
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    lyr.updateExtents()
    if add:
        proj.addMapLayer(lyr)
    return lyr


def new_project(name, ext="qgz"):
    d = Path(tempfile.mkdtemp(dir=WORK))
    proj.clear()
    return str(d / f"{name}.{ext}")


def get(name):
    return proj.mapLayersByName(name)[0]


def counts():
    result = {}
    for lyr in proj.mapLayers().values():
        with_filter = lyr.subsetString()
        result[lyr.name()] = lyr.dataProvider().featureCount() if not with_filter else None
    return result


def reopen(path):
    proj.clear()
    assert proj.read(path), proj.error()


def entries(path):
    with zipfile.ZipFile(path) as z:
        return {i.filename: i.file_size for i in z.infolist()}


def entry_bytes(path, suffix="layers.mldata"):
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith(suffix):
                return z.read(name)
    return None


def replace_mldata(path, fn):
    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for i in src.infolist():
            data = src.read(i.filename)
            dst.writestr(i, fn(data) if i.filename.endswith("layers.mldata") else data)
    shutil.move(tmp, path)


def scan_bytes(data):
    p = Path(tempfile.mkdtemp(dir=WORK)) / "scan.mldata"
    p.write_bytes(data)
    with Reader(str(p)) as r:
        return r.version, {i["name"]: i["features"] for i in r.scan()}, list(r.errors)


def edit_add_feature(lyr):
    lyr.startEditing()
    lyr.addFeature(QgsFeature(lyr.fields()))
    assert lyr.commitChanges(), lyr.commitErrors()


tests = []


def test(fn):
    tests.append(fn)
    return fn


@test
def bug1_failed_write_keeps_previous_file():
    new_project("t")
    a, b = layer("A", 5), layer("B", 5)
    f = str(WORK / "direct.mldata")
    with Writer(f) as w:
        w.write_layers([a, b])
    good = Path(f).read_bytes()

    class Boom:
        def id(self):
            return "boom"

        def dataProvider(self):
            raise OSError("simulated")

    try:
        with Writer(f) as w:
            w.write_layers([a, Boom(), b])
        raise AssertionError("should have raised")
    except OSError:
        pass
    assert Path(f).read_bytes() == good


@test
def bug1_failed_save_is_reported_and_stays_pending():
    path = new_project("t")
    layer("A", 5)
    proj.write(path)
    good = entry_bytes(path)
    original = Writer.write_layers
    Writer.write_layers = lambda self, layers: (_ for _ in ()).throw(OSError("disk full"))
    try:
        edit_add_feature(get("A"))
        proj.write(path)
    finally:
        Writer.write_layers = original
    assert entry_bytes(path) == good
    assert plugin.has_modified_layers
    assert any("disk full" in m for m in iface.bar.messages), iface.bar.messages
    assert not slot_errors, slot_errors


@test
def bug2_length_overflow_auto_v3_keeps_everything():
    path = new_project("t")
    layer("A", 5)
    c = layer("C", 5, "t:string(40000)")
    assert c.dataProvider().fields()[0].length() == 40000
    proj.write(path)
    assert not slot_errors, slot_errors
    assert entry_bytes(path)[20:24] == b"\x00\x00\x00\x03"
    reopen(path)
    assert counts() == {"A": 5, "C": 5}
    assert get("C").fields()[0].length() == 40000


@test
def bug2_length_overflow_forced_v2_clamps():
    Settings.set_file_format(FORMAT_ALWAYS_V2)
    path = new_project("t")
    layer("A", 5)
    layer("C", 5, "t:string(40000)")
    proj.write(path)
    assert not slot_errors, slot_errors
    assert entry_bytes(path)[20:24] == b"\x00\x00\x00\x02"
    reopen(path)
    assert counts() == {"A": 5, "C": 5}
    assert get("C").fields()[0].length() == 32767


@test
def bug2_geometry_field_roundtrip():
    """Qt6: overflowed the type id. Qt5: silently lost the values."""
    path = new_project("t")
    g = QgsVectorLayer("Point?crs=EPSG:4326", "G", "memory")
    g.dataProvider().addAttributes([QgsField("g", QMetaType.Type.User, "geometry")])
    g.updateFields()
    f = QgsFeature(g.fields())
    f.setAttributes([QgsReferencedGeometry(QgsGeometry.fromWkt("POINT(1 2)"), QgsCoordinateReferenceSystem("EPSG:4326"))])
    g.dataProvider().addFeatures([f])
    proj.addMapLayer(g)
    proj.write(path)
    assert not slot_errors, slot_errors
    reopen(path)
    value = next(get("G").getFeatures())[0]
    assert get("G").fields()[0].typeName() == "geometry"
    assert isinstance(value, QgsReferencedGeometry), value
    assert value.asWkt() == "Point (1 2)" and value.crs().authid() == "EPSG:4326", value


@test
def bug3_and_4_truncated_file_is_reported_and_protected():
    path = new_project("t")
    layer("A", 50), layer("B", 50), layer("C", 50)
    proj.write(path)
    good = entry_bytes(path)
    half = good[: len(good) // 2]
    replace_mldata(path, lambda d: half)
    reopen(path)
    assert plugin.load_failed
    assert any(k == "warning" for k, _ in dialogs), dialogs
    loaded = sum(lyr.dataProvider().featureCount() for lyr in proj.mapLayers().values())
    assert 0 < loaded < 150

    # no edit: nothing happens, and an edit answered with No keeps the file
    proj.write(path)
    assert entry_bytes(path) == half
    edit_add_feature(next(lyr for lyr in proj.mapLayers().values() if lyr.featureCount()))
    proj.write(path)
    assert entry_bytes(path) == half
    assert plugin.has_modified_layers

    # answering Yes keeps what could not be loaded aside
    answer["value"] = QMessageBox.StandardButton.Yes
    proj.write(path)
    assert not plugin.load_failed
    assert entry_bytes(path) != half
    assert entry_bytes(path, "layers.mldata.failed-load.bak") == half


@test
def bug3_corrupt_magic_and_empty_attachment():
    path = new_project("t")
    layer("A", 5)
    proj.write(path)
    replace_mldata(path, lambda d: b"garbage" * 10)
    reopen(path)
    assert plugin.load_failed
    proj.clear()
    # projects saved by older versions hold an empty attachment: this is not an error
    replace_mldata(path, lambda d: b"")
    dialogs.clear()
    reopen(path)
    assert not plugin.load_failed and not dialogs, dialogs


@test
def bug5_populated_layer_is_skipped_not_fatal():
    new_project("t")
    a, b = layer("A", 5), layer("B", 5)
    f = str(WORK / "b5.mldata")
    with Writer(f) as w:
        w.write_layers([a, b])
    empty = QgsVectorLayer("Point?crs=EPSG:4326", "B2", "memory")
    with Reader(f) as r:
        r.read_layers({a.id(): a, b.id(): empty})
    assert a.featureCount() == 5 and empty.featureCount() == 5


@test
def bug6_filter_change_is_saved():
    path = new_project("t")
    layer("A", 10)
    proj.write(path)
    reopen(path)
    assert not proj.isDirty()
    assert not plugin.has_modified_layers
    get("A").setSubsetString('"a" < 3')
    assert plugin.has_modified_layers
    proj.write(path)
    reopen(path)
    assert get("A").subsetString() == '"a" < 3'
    assert get("A").featureCount() == 3
    get("A").setSubsetString("")
    assert get("A").featureCount() == 10


@test
def bug6_provider_edits_are_detected():
    path = new_project("t")
    layer("A", 3)
    proj.write(path)
    reopen(path)
    a = get("A")
    f = QgsFeature(a.fields())
    f.setGeometry(QgsGeometry.fromWkt("POINT(9 9)"))
    a.dataProvider().addFeatures([f])
    a.dataProvider().addAttributes([QgsField("z", QMetaType.Type.Int)])
    proj.write(path)
    reopen(path)
    assert counts() == {"A": 4}
    assert [x.name() for x in get("A").fields()] == ["a", "s", "z"]


@test
def bug6_value_only_provider_edit_needs_always_rewrite():
    path = new_project("t")
    layer("A", 3)
    proj.write(path)
    reopen(path)
    a = get("A")
    a.dataProvider().changeAttributeValues({1: {0: 42}})
    Settings.set_always_rewrite(True)
    proj.write(path)
    reopen(path)
    assert sorted(f["a"] for f in get("A").getFeatures()) == [1, 2, 42]


@test
def bug6_committed_rename_is_saved():
    path = new_project("t")
    layer("A", 3)
    proj.write(path)
    reopen(path)
    a = get("A")
    a.startEditing()
    a.renameAttribute(0, "renamed")
    assert a.commitChanges()
    assert plugin.has_modified_layers
    proj.write(path)
    reopen(path)
    assert [x.name() for x in get("A").fields()] == ["renamed", "s"]


@test
def bug6_storage_switch_rewrites_stale_file():
    path = new_project("t")
    Settings.set_mldata_embedded(False)
    layer("A", 3)
    proj.write(path)
    Settings.set_mldata_embedded(True)
    edit_add_feature(get("A"))
    proj.write(path)
    dlg = SettingsDialog()
    dlg.checkbox.setChecked(False)
    dlg.accept()
    proj.write(path)
    reopen(path)
    assert counts() == {"A": 4}


@test
def bug6_no_change_means_no_rewrite():
    path = new_project("t")
    a = layer("A", 10)
    a.setSubsetString('"a" < 3')
    proj.write(path)
    reopen(path)
    target = plugin.memory_layer_file()
    stamp = Path(target).stat().st_mtime_ns
    assert not plugin.needs_rewrite(plugin.memory_layers())
    proj.write(path)
    assert Path(plugin.memory_layer_file()).stat().st_mtime_ns == stamp


@test
def bug7_filtered_layer_in_edit_mode_keeps_all_features():
    path = new_project("t")
    a = layer("A", 10)
    a.setSubsetString('"a" < 3')
    a.startEditing()
    seen = []
    a.subsetStringChanged.connect(lambda: seen.append("subset"))
    a.repaintRequested.connect(lambda *x: seen.append("repaint"))
    a.dataChanged.connect(lambda: seen.append("data"))
    proj.write(path)
    assert not seen, seen
    assert a.subsetString() == '"a" < 3' and a.featureCount() == 3
    a.rollBack()
    assert scan_bytes(entry_bytes(path))[1] == {"A": 10}
    reopen(path)
    a = get("A")
    assert a.subsetString() == '"a" < 3'
    a.setSubsetString("")
    assert a.featureCount() == 10


@test
def bug8_only_committed_data_is_saved():
    path = new_project("t")
    layer("A", 5), layer("B", 5)
    proj.write(path)
    reopen(path)
    a = get("A")
    a.startEditing()
    a.deleteFeatures([f.id() for f in a.getFeatures()][:3])
    edit_add_feature(get("B"))
    iface.bar.messages.clear()
    proj.write(path)
    assert any("Uncommitted" in m for m in iface.bar.messages), iface.bar.messages
    a.rollBack()
    reopen(path)
    assert counts() == {"A": 5, "B": 6}


@test
def bug8_uncommitted_field_delete_keeps_values():
    path = new_project("t")
    a = layer("A", 3)
    a.dataProvider().changeAttributeValues({f.id(): {1: "keep"} for f in a.getFeatures()})
    a.startEditing()
    a.deleteAttribute(1)
    proj.write(path)
    a.rollBack()
    reopen(path)
    assert [f["s"] for f in get("A").getFeatures()] == ["keep"] * 3


@test
def bug9_about_link():
    plugin.show_about()
    text = next(t for k, t in dialogs if k == "about")
    assert "/issues/issues" not in text and "/issues>" in text, text


@test
def bug9_no_attachment_without_memory_layers():
    path = new_project("t")
    proj.write(path)
    assert not [e for e in entries(path) if "mldata" in e], entries(path)
    proj.write(path)
    assert not [e for e in entries(path) if "mldata" in e], entries(path)


@test
def bug9_removed_layers_leave_the_file():
    path = new_project("t")
    a = layer("A", 20)
    proj.write(path)
    proj.removeMapLayer(a.id())
    proj.write(path)
    # Deleted layers stay available in the backup for one save, then nothing is left
    assert entry_bytes(path) is None
    assert scan_bytes(entry_bytes(path, ".bak"))[1] == {"A": 20}
    assert not any(k == "warning" for k, _ in dialogs), dialogs
    proj.write(path)
    assert not [e for e in entries(path) if "mldata" in e], entries(path)


def make_permanent(lyr):
    """What the Make Permanent action of QGIS does: write a file, then change the data source"""
    target = str(Path(tempfile.mkdtemp(dir=WORK)) / "moved.gpkg")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    assert QgsVectorFileWriter.writeAsVectorFormatV3(lyr, target, proj.transformContext(), options)[0] == 0
    lyr.setDataSource(target, lyr.name(), "ogr")
    assert lyr.dataProvider().name() == "ogr"


@test
def moved_layers_leave_no_data_in_the_project():
    path = new_project("t")
    a, b = layer("A", 5000), layer("B", 5000)
    proj.write(path)
    size = os.path.getsize(path)
    make_permanent(a)
    make_permanent(b)
    proj.write(path)
    assert not [e for e in entries(path) if "mldata" in e], entries(path)
    assert os.path.getsize(path) < size / 2, (os.path.getsize(path), size)
    reopen(path)
    assert get("A").featureCount() == 5000 and not plugin.load_failed
    assert not dialogs, dialogs


@test
def partly_moved_layers_leave_only_the_others():
    path = new_project("t")
    a, b = layer("A", 5000), layer("B", 5000)
    proj.write(path)
    edit_add_feature(b)
    proj.write(path)
    assert entry_bytes(path, ".bak") is not None
    size = os.path.getsize(path)
    make_permanent(a)
    proj.write(path)
    assert scan_bytes(entry_bytes(path))[1] == {"B": 5001}
    assert entry_bytes(path, ".bak") is None
    assert os.path.getsize(path) < size / 2, (os.path.getsize(path), size)
    reopen(path)
    assert get("B").featureCount() == 5001
    # Backups come back with the next change
    edit_add_feature(get("B"))
    proj.write(path)
    assert scan_bytes(entry_bytes(path, ".bak"))[1] == {"B": 5001}


@test
def moved_and_deleted_layers_keep_the_backup_of_the_deleted_one():
    path = new_project("t")
    a, b, c = layer("A", 5), layer("B", 5), layer("C", 5)
    proj.write(path)
    make_permanent(a)
    proj.removeMapLayer(b.id())
    proj.write(path)
    assert scan_bytes(entry_bytes(path))[1] == {"C": 5}
    assert scan_bytes(entry_bytes(path, ".bak"))[1] == {"A": 5, "B": 5, "C": 5}


def save_without_plugin(path):
    proj.writeProject.disconnect(plugin.save_data)
    try:
        assert proj.write(path)
    finally:
        proj.writeProject.connect(plugin.save_data)


@test
def stale_data_of_older_versions_is_cleaned_up():
    # What upstream leaves in a project whose last memory layer is gone
    path = new_project("t")
    a = layer("A", 20)
    proj.write(path)
    make_permanent(a)
    save_without_plugin(path)
    assert scan_bytes(entry_bytes(path))[1] == {"A": 20}
    reopen(path)
    assert not plugin.load_failed and not dialogs, dialogs
    proj.write(path)
    assert entry_bytes(path) is None
    assert scan_bytes(entry_bytes(path, ".bak"))[1] == {"A": 20}
    proj.write(path)
    assert not [e for e in entries(path) if "mldata" in e], entries(path)

    # With another layer remaining
    path = new_project("t2")
    a, b = layer("A", 20), layer("B", 3)
    proj.write(path)
    make_permanent(a)
    save_without_plugin(path)
    reopen(path)
    assert get("B").featureCount() == 3
    proj.write(path)
    assert scan_bytes(entry_bytes(path))[1] == {"B": 3}


@test
def nothing_left_with_legacy_storage_and_without_backups():
    Settings.set_mldata_embedded(False)
    path = new_project("t")
    a = layer("A", 5)
    proj.write(path)
    assert Path(path + ".mldata").exists()
    make_permanent(a)
    proj.write(path)
    assert not Path(path + ".mldata").exists() and not Path(path + ".mldata.bak").exists()

    Settings.set_mldata_embedded(True)
    Settings.set_keep_backup(False)
    path = new_project("t2")
    a = layer("A", 5)
    proj.write(path)
    proj.removeMapLayer(a.id())
    proj.write(path)
    assert not [e for e in entries(path) if "mldata" in e], entries(path)


@test
def discarding_data_still_asks_after_a_failed_load():
    path = new_project("t")
    a = layer("A", 50)
    proj.write(path)
    good = entry_bytes(path)
    replace_mldata(path, lambda d: d[: len(d) // 2])
    reopen(path)
    assert plugin.load_failed
    damaged = entry_bytes(path)
    make_permanent(get("A"))
    proj.write(path)
    assert entry_bytes(path) == damaged, "kept when answering No"
    answer["value"] = QMessageBox.StandardButton.Yes
    proj.write(path)
    assert entry_bytes(path) is None
    assert entry_bytes(path, "layers.mldata.failed-load.bak") == damaged
    assert good != damaged


@test
def bug9_unload_disconnects_and_deletes_menu():
    other = MemoryLayerSaver()
    other.initGui()
    menu_count = len(iface.menu.findChildren(QMenu, "memory_layer_saver_menu"))
    other.unload()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(iface.menu.findChildren(QMenu, "memory_layer_saver_menu")) == menu_count - 1
    other.has_modified_layers = "sentinel"
    proj.clear()
    assert other.has_modified_layers == "sentinel"


@test
def bug9_settings_dialog():
    received = []
    original = SettingsDialog.__init__

    def spy(self, parent=None):
        received.append(parent)
        original(self, parent)

    import MemoryLayerSaver.memory_layer_saver as module

    exec_original = SettingsDialog.exec
    SettingsDialog.__init__ = spy
    SettingsDialog.exec = lambda self: self.reject() or 0
    try:
        module.SettingsDialog = SettingsDialog
        plugin.show_settings()
    finally:
        SettingsDialog.__init__ = original
        SettingsDialog.exec = exec_original
    assert received == [iface.win]
    dialog = SettingsDialog()
    assert dialog.checkbox.text() == "Embed mldata file"


def list_fields_layer():
    a = QgsVectorLayer("Point?crs=EPSG:4326", "A", "memory")
    a.dataProvider().addAttributes(
        [
            QgsField("li", QMetaType.Type.QVariantList, "integerlist", 0, 0, "", QMetaType.Type.Int),
            QgsField("ls", QMetaType.Type.QStringList, "stringlist", 0, 0, "", QMetaType.Type.QString),
        ]
    )
    a.updateFields()
    f = QgsFeature(a.fields())
    f.setAttributes([[1, 2, 3], ["x", "y"]])
    a.dataProvider().addFeatures([f])
    proj.addMapLayer(a)
    return a


@test
def bug9_subtype_survives_v2_and_v3():
    for file_format, version in ((FORMAT_AUTO, 2), (FORMAT_ALWAYS_V3, 3)):
        Settings.set_file_format(file_format)
        path = new_project("t")
        list_fields_layer()
        proj.write(path)
        assert entry_bytes(path)[23] == version
        reopen(path)
        a = get("A")
        assert a.fields()[0].subType() == QMetaType.Type.Int, a.fields()[0].subType()
        assert a.fields()[1].subType() == QMetaType.Type.QString
        assert next(a.getFeatures()).attributes() == [[1, 2, 3], ["x", "y"]]


def datetime_layer():
    a = layer("A", 0, "d:datetime")
    values = [
        QDateTime(QDate(2026, 6, 1), QTime(12, 0), QTimeZone(18000)),
        QDateTime(QDate(2026, 6, 1), QTime(12, 0), QTimeZone.utc()),
        QDateTime(QDate(2026, 6, 1), QTime(12, 0)),
    ]
    feats = []
    for value in values:
        f = QgsFeature(a.fields())
        f.setAttributes([value])
        feats.append(f)
    f = QgsFeature(a.fields())
    feats.append(f)
    a.dataProvider().addFeatures(feats)
    return values


@test
def bug9_datetime_instant_in_v2():
    path = new_project("t")
    values = datetime_layer()
    proj.write(path)
    reopen(path)
    after = [f["d"] for f in get("A").getFeatures()]
    assert [x.toMSecsSinceEpoch() for x in after[:3]] == [v.toMSecsSinceEpoch() for v in values]
    assert not after[3] or after[3].isNull()


@test
def bug9_datetime_offset_in_v3():
    Settings.set_file_format(FORMAT_ALWAYS_V3)
    path = new_project("t")
    values = datetime_layer()
    proj.write(path)
    reopen(path)
    after = [f["d"] for f in get("A").getFeatures()]
    assert [x.toMSecsSinceEpoch() for x in after[:3]] == [v.toMSecsSinceEpoch() for v in values]
    assert after[0].offsetFromUtc() == 18000 and after[1].offsetFromUtc() == 0


@test
def bug9_feature_ids_in_v3():
    Settings.set_file_format(FORMAT_ALWAYS_V3)
    path = new_project("t")
    a = layer("A", 5)
    a.dataProvider().deleteFeatures([1, 3])
    before = [(f.id(), f["a"]) for f in a.getFeatures()]
    proj.write(path)
    reopen(path)
    assert [(f.id(), f["a"]) for f in get("A").getFeatures()] == before
    assert get("A").featureCount() == 3


@test
def values_roundtrip_all_types_both_formats():
    fields = "i:integer&field=l:int8&field=d:double&field=s:string&field=b:bool&field=dd:date&field=t:time"
    for file_format in (FORMAT_AUTO, FORMAT_ALWAYS_V3):
        Settings.set_file_format(file_format)
        path = new_project("t")
        a = layer("A", 0, fields)
        f = QgsFeature(a.fields())
        expected = [7, 2**40 + 1, 0.1, "é中", True, QDate(2001, 2, 3), QTime(1, 2, 3, 4)]
        f.setAttributes(expected)
        f.setGeometry(QgsGeometry.fromWkt("POINT(1 2)"))
        nulls = QgsFeature(a.fields())
        a.dataProvider().addFeatures([f, nulls])
        proj.write(path)
        reopen(path)
        got = list(get("A").getFeatures())
        assert got[0].attributes() == expected, got[0].attributes()
        assert got[0].geometry().asWkt() == "Point (1 2)"
        assert all(not v or isinstance(v, QVariantNull) for v in got[1].attributes()) and not got[1].hasGeometry()


try:
    from qgis.PyQt.QtCore import QVariant

    QVariantNull = QVariant
except ImportError:
    QVariantNull = type(None)


@test
def v3_damaged_layer_does_not_affect_the_next_ones():
    Settings.set_file_format(FORMAT_ALWAYS_V3)
    path = new_project("t")
    a, b, c = layer("A", 50), layer("B", 50), layer("C", 50)
    f = str(WORK / "v3.mldata")
    with Writer(f, FORMAT_V3, 3) as w:
        w.write_layers([a, b, c])
    data = bytearray(Path(f).read_bytes())
    # Damage the features of B, enough of them to break the structure and not only a value
    with Reader(f) as r:
        assert r.layer_count == 3
    marker = data.find(b.id().encode("utf-16-be"))
    assert marker > 0
    tail = marker + 2 * len(b.id()) + 8 + 700
    data[tail : tail + 200] = b"\xff" * 200
    Path(f).write_bytes(bytes(data))
    targets = {x.id(): QgsVectorLayer("Point?crs=EPSG:4326", x.name(), "memory") for x in (a, b, c)}
    try:
        with Reader(f) as r:
            r.read_layers(targets)
        raise AssertionError("should have raised")
    except MldataError as e:
        assert b.id() in str(e), e
    assert targets[a.id()].featureCount() == 50
    assert targets[c.id()].featureCount() == 50
    assert targets[b.id()].featureCount() < 50


@test
def v3_truncation_at_layer_boundary_is_detected():
    new_project("t")
    a, b = layer("A", 5), layer("B", 5)
    one, two = str(WORK / "one.mldata"), str(WORK / "two.mldata")
    with Writer(one, FORMAT_V3, 2) as w:
        w.write_layer(a)
    try:
        with Reader(one) as r:
            r.read_layers([QgsVectorLayer("Point", "x", "memory")])
        raise AssertionError("should have raised")
    except MldataError as e:
        assert "2 layers expected" in str(e), e


@test
def future_format_is_refused_and_protected():
    path = new_project("t")
    layer("A", 5)
    proj.write(path)
    future = lambda d: d[:20] + b"\x00\x00\x00\x63" + d[24:]  # noqa: E731
    replace_mldata(path, future)
    reopen(path)
    assert plugin.load_failed
    assert any("update the plugin" in t for _k, t in dialogs), dialogs


@test
def v1_file_is_still_readable():
    new_project("t")
    a = layer("A", 0)
    f = str(WORK / "v1.mldata")
    qf = QFile(f)
    qf.open(QIODevice.OpenModeFlag.WriteOnly)
    ds = QDataStream(qf)
    ds.setVersion(QDataStream.Version.Qt_4_5)
    ds.writeRawData(b"QGis.MemoryLayerData")
    ds.writeUInt32(1)
    ds.writeQString(a.id())
    ds.writeInt16(1)
    ds.writeQString("n")
    ds.writeInt16(int(QMetaType.Type.Int))
    ds.writeQString("integer")
    ds.writeInt16(0)
    ds.writeInt16(0)
    ds.writeQString("")
    wkb = QgsGeometry.fromWkt("POINT(3 4)").asWkb()
    for value in (11, 22):
        ds.writeBool(True)
        ds.writeQVariant(value)
        ds.writeUInt32(len(wkb))
        ds.writeRawData(wkb)
    ds.writeBool(False)
    qf.close()
    with Reader(f) as r:
        r.read_layers([a])
    assert [x["n"] for x in a.getFeatures()] == [11, 22]
    assert next(a.getFeatures()).geometry().asWkt() == "Point (3 4)"


@test
def v2_format_bytes_are_unchanged_in_structure():
    """A plain layer must stay in v2 so that older plugin versions can open the project"""
    path = new_project("t")
    layer("A", 5)
    proj.write(path)
    assert scan_bytes(entry_bytes(path))[0] == FORMAT_V2


@test
def backup_is_kept_inside_the_project():
    path = new_project("t")
    layer("A", 3)
    proj.write(path)
    first = entry_bytes(path)
    edit_add_feature(get("A"))
    proj.write(path)
    assert entry_bytes(path, "layers.mldata.bak") == first
    reopen(path)
    assert counts() == {"A": 4}
    edit_add_feature(get("A"))
    proj.write(path)
    assert scan_bytes(entry_bytes(path, "layers.mldata.bak"))[1] == {"A": 4}
    Settings.set_keep_backup(False)
    path = new_project("t2")
    layer("A", 3)
    proj.write(path)
    edit_add_feature(get("A"))
    proj.write(path)
    assert entry_bytes(path, ".bak") is None


@test
def layers_written_with_the_settings_on_removal():
    # An intended removal of one layer among others does not ask
    path = new_project("t")
    a, _b = layer("A", 3), layer("B", 3)
    proj.write(path)
    proj.removeMapLayer(a.id())
    proj.write(path)
    assert scan_bytes(entry_bytes(path))[1] == {"B": 3}
    assert not dialogs, dialogs


@test
def backups_are_off_by_default():
    Settings.get_settings().remove("MemoryLayerSaver/keepBackup")
    assert Settings.keep_backup() is False
    path = new_project("t")
    layer("A", 3)
    proj.write(path)
    edit_add_feature(get("A"))
    proj.write(path)
    assert scan_bytes(entry_bytes(path))[1] == {"A": 4}
    assert entry_bytes(path, ".bak") is None
    # Failed loads are kept whatever the setting
    replace_mldata(path, lambda d: d[: len(d) // 2])
    reopen(path)
    damaged = entry_bytes(path)
    edit_add_feature(get("A"))
    answer["value"] = QMessageBox.StandardButton.Yes
    proj.write(path)
    assert entry_bytes(path, "layers.mldata.failed-load.bak") == damaged


@test
def turning_backups_off_removes_the_existing_one():
    path = new_project("t")
    layer("A", 3)
    proj.write(path)
    edit_add_feature(get("A"))
    proj.write(path)
    assert entry_bytes(path, ".bak") is not None
    reopen(path)
    Settings.set_keep_backup(False)
    proj.write(path)
    assert entry_bytes(path, ".bak") is None
    assert scan_bytes(entry_bytes(path))[1] == {"A": 4}
    reopen(path)
    assert counts() == {"A": 4}


@test
def emptying_all_layers_asks_first():
    path = new_project("t")
    a = layer("A", 3)
    proj.write(path)
    good = entry_bytes(path)
    a.startEditing()
    a.deleteFeatures([f.id() for f in a.getFeatures()])
    a.commitChanges()
    proj.write(path)
    assert entry_bytes(path) == good
    assert any(k == "warning" for k, _ in dialogs), dialogs
    answer["value"] = QMessageBox.StandardButton.Yes
    proj.write(path)
    assert scan_bytes(entry_bytes(path))[1] == {"A": 0}


@test
def qgs_project_and_legacy_storage():
    path = new_project("t", "qgs")
    layer("A", 3)
    proj.write(path)
    assert Path(path[:-4] + "_attachments.zip").exists()
    reopen(path)
    assert counts() == {"A": 3}
    Settings.set_mldata_embedded(False)
    path = new_project("t2")
    layer("A", 3)
    proj.write(path)
    assert Path(path + ".mldata").exists()
    edit_add_feature(get("A"))
    proj.write(path)
    assert Path(path + ".mldata.bak").exists()
    reopen(path)
    assert counts() == {"A": 4}


@test
def warning_setting_is_restored_and_saved_layers_opt_out():
    settings = Settings.get_settings()
    settings.setValue("MemoryLayerSaver/memoryLayerSaveSetting", True)
    QgsSettings().setValue("askToSaveMemoryLayers", False, section=QgsSettings.Section.App)
    Settings.restore_ask_to_save_memory_layers()
    assert QgsSettings().value("askToSaveMemoryLayers", None, bool, section=QgsSettings.Section.App) is True
    assert not settings.contains("MemoryLayerSaver/memoryLayerSaveSetting")

    new_project("t")
    saved = layer("Saved", 1)
    excluded = QgsVectorLayer("Point?crs=EPSG:4326&field=a:integer", "Excluded", "memory")
    excluded.setCustomProperty("SaveMemoryProvider", False)
    proj.addMapLayer(excluded)
    assert saved.customProperty("skipMemoryLayersCheck") == 1
    assert excluded.customProperty("skipMemoryLayersCheck") is None
    proj.removeMapLayer(saved.id())


@test
def excluded_and_temporary_file_layers_are_warned_about_and_convertible():
    path = new_project("t")
    excluded = layer("Excluded", 0, add=False)
    excluded.dataProvider().addFeatures([QgsFeature(excluded.fields())])
    excluded.setCustomProperty("SaveMemoryProvider", False)
    proj.addMapLayer(excluded)

    source = layer("Src", 4, add=False)
    temp_gpkg = f"{QDir.tempPath()}/mls_test_{os.getpid()}.gpkg"
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    assert QgsVectorFileWriter.writeAsVectorFormatV3(source, temp_gpkg, proj.transformContext(), options)[0] == 0
    temp = QgsVectorLayer(temp_gpkg, "TempOutput", "ogr")
    assert temp.isValid(), temp_gpkg
    assert temp.isTemporary(), temp.source()
    proj.addMapLayer(temp)
    iface.bar.messages.clear()
    shown = []
    original = iface.bar.createMessage

    def spy(title, text):
        shown.append(text)
        return original(title, text)

    iface.bar.createMessage = spy
    try:
        proj.write(path)
        assert any("Excluded" in t and "TempOutput" in t for t in shown), shown
        shown.clear()
        proj.write(path)
        assert not shown, "warned twice"
    finally:
        del iface.bar.createMessage

    plugin.convert_temporary_layers()
    converted = get("TempOutput")
    assert converted.dataProvider().name() == "memory" and converted.featureCount() == 4
    assert proj.layerTreeRoot().findLayer(converted.id()) is not None
    assert Settings.is_saved_layer(converted)
    proj.write(path)
    reopen(path)
    assert get("TempOutput").featureCount() == 4
    proj.clear()
    QCoreApplication.processEvents()
    try:
        os.remove(temp_gpkg)
    except OSError:
        pass


@test
def export_to_geopackage_includes_filtered_features_and_can_switch():
    new_project("t")
    a, same_name = layer("A b", 10), layer("A b", 2)
    with_fid = layer("F", 2, "fid:integer&field=x:string")
    a.setSubsetString('"a" < 3')
    editing = layer("E", 1)
    editing.startEditing()
    gpkg = str(Path(tempfile.mkdtemp(dir=WORK)) / "out.gpkg")
    exported, errors = layer_tools.export_to_geopackage([a, same_name, with_fid, editing], gpkg)
    assert list(errors) == ["E"], errors
    assert set(exported.values()) == {"A_b", "A_b_2", "F"}, exported
    assert a.subsetString() == '"a" < 3' and a.featureCount() == 3
    check = QgsVectorLayer(f"{gpkg}|layername=A_b", "c", "ogr")
    assert check.isValid() and check.featureCount() == 10
    del check
    editing.rollBack()
    style_before = a.renderer().type()
    layer_tools.switch_to_geopackage(exported, gpkg)
    assert a.dataProvider().name() == "ogr" and a.featureCount() == 3 and a.subsetString() == '"a" < 3'
    assert same_name.isValid() and same_name.featureCount() == 2
    assert with_fid.isValid() and with_fid.featureCount() == 2
    assert a.renderer().type() == style_before
    assert not Settings.is_saved_layer(a)
    assert a.customProperty("skipMemoryLayersCheck") is None


@test
def recovery_scan_and_restore_from_bak_and_damaged_files():
    for file_format, expect_crs in ((FORMAT_AUTO, "EPSG:3857"), (FORMAT_ALWAYS_V3, "EPSG:4326")):
        Settings.set_file_format(file_format)
        path = new_project("t")
        proj.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        layer("A", 6), layer("B", 4, "x:integer")
        proj.write(path)
        archive_entries = layer_tools.mldata_entries(path)
        assert len(archive_entries) == 1, archive_entries
        folder = tempfile.mkdtemp(dir=WORK)
        f = layer_tools.extract_entry(path, archive_entries[0], folder)
        proj.clear()
        proj.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        with Reader(f) as r:
            infos = r.scan()
        assert {i["name"]: i["features"] for i in infos} == {"A": 6, "B": 4}, infos
        assert not any(i["error"] for i in infos)
        added = layer_tools.restore_layers(f, infos)
        assert sorted(x.name() for x in added) == ["A", "B"]
        restored = get("A")
        assert restored.featureCount() == 6
        assert restored.wkbType() == QgsWkbTypes.Type.Point if hasattr(QgsWkbTypes, "Type") else True
        assert next(restored.getFeatures()).geometry().asWkt() == "Point (0 0)"
        assert restored.crs().authid() == expect_crs, restored.crs().authid()
        assert Settings.is_saved_layer(restored)

    # Damaged: what precedes the damage is reported and restored
    new_project("t")
    a = layer("A", 50)
    f = str(WORK / "dmg.mldata")
    with Writer(f) as w:
        w.write_layers([a])
    data = Path(f).read_bytes()
    Path(f).write_bytes(data[: len(data) // 2])
    with Reader(f) as r:
        infos = r.scan()
    assert infos[0]["error"] and 0 < infos[0]["features"] < 50, infos
    proj.clear()
    try:
        layer_tools.restore_layers(f, infos)
    except MldataError:
        pass
    assert get("A").featureCount() == infos[0]["features"]


@test
def newer_legacy_file_is_flagged():
    path = new_project("t")
    layer("A", 3)
    proj.write(path)
    legacy = path + ".mldata"
    Path(legacy).write_bytes(entry_bytes(path))
    later = Path(path).stat().st_mtime + 60
    os.utime(legacy, (later, later))
    iface.bar.messages.clear()
    reopen(path)
    assert any("more recent" in m for m in iface.bar.messages), iface.bar.messages


@test
def large_layer_batches_and_progress():
    path = new_project("t")
    a = layer("A", 60000)
    proj.write(path)
    assert not slot_errors, slot_errors
    reopen(path)
    assert counts() == {"A": 60000}
    assert [f["a"] for f in get("A").getFeatures()][-1] == 59999


def reset():
    proj.clear()
    dialogs.clear()
    slot_errors.clear()
    iface.bar.messages.clear()
    answer["value"] = QMessageBox.StandardButton.No
    Settings.set_mldata_embedded(True)
    Settings.set_file_format(FORMAT_AUTO)
    Settings.set_always_rewrite(False)
    Settings.set_keep_backup(True)


print("QGIS", Qgis.QGIS_VERSION)
failed = 0
only = sys.argv[3:] or None
for fn in tests:
    if only and fn.__name__ not in only:
        continue
    reset()
    try:
        fn()
        print("PASS", fn.__name__)
    except Exception:
        failed += 1
        print("FAIL", fn.__name__)
        print("   ", traceback.format_exc(limit=5).replace("\n", "\n    "))
    sys.stdout.flush()
print(f"{len(tests) - failed}/{len(tests)} passed" if not only else f"{failed} failed")
sys.stdout.flush()
os._exit(1 if failed else 0)  # QGIS teardown is irrelevant to the results
