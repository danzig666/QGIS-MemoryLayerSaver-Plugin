"""Ways out of memory layers: GeoPackage export, conversion of temporary files, recovery of mldata files"""

import re
import zipfile
from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeatureRequest,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qgis.PyQt.QtXml import QDomDocument

from .reader import Reader, wkb_type_name
from .settings import Settings
from .toolbox import log_warning, unfiltered_provider


def unique_table_name(name, used):
    base = re.sub(r"[^\w]+", "_", name).strip("_") or "layer"
    candidate = base
    index = 1
    while candidate.lower() in used:
        index += 1
        candidate = f"{base}_{index}"
    used.add(candidate.lower())
    return candidate


def export_to_geopackage(layers, path):
    """Export the whole content of layers to a GeoPackage, whatever their filter.

    Returns the {layer: table name} of the exported layers, and the {layer name: reason} of the others."""
    exported = {}
    errors = {}
    used = set()
    context = QgsProject.instance().transformContext()
    for layer in layers:
        if layer.isEditable():
            errors[layer.name()] = "the layer is being edited"
            continue
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = unique_table_name(layer.name(), used)
        options.actionOnExistingFile = (
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            if exported
            else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        )
        with unfiltered_provider(layer):
            result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, context, options)
        if result[0] == QgsVectorFileWriter.WriterError.NoError:
            exported[layer] = options.layerName
        else:
            errors[layer.name()] = result[1]
    return exported, errors


def switch_to_geopackage(exported, path):
    """Make exported layers read from the GeoPackage, keeping their style and filter"""
    for layer, table in exported.items():
        subset = layer.subsetString()
        layer.setDataSource(f"{path}|layername={table}", layer.name(), "ogr")
        if subset and not layer.setSubsetString(subset):
            log_warning(f"Cannot apply the filter of layer {layer.name()} to its GeoPackage table: {subset}")


def temporary_file_layers():
    """Vector layers stored in a temporary folder, for instance the outputs of GDAL or GRASS algorithms"""
    return [
        layer
        for layer in QgsProject.instance().mapLayers().values()
        if layer.isTemporary() and isinstance(layer, QgsVectorLayer) and not Settings.is_memory_layer(layer)
    ]


def convert_to_memory_layer(layer):
    """Replace a layer with a memory layer holding its features, at the same place in the layer tree"""
    project = QgsProject.instance()
    memory_layer = layer.materialize(QgsFeatureRequest())
    if memory_layer is None or not memory_layer.isValid():
        return None
    memory_layer.setName(layer.name())
    style = QDomDocument()
    layer.exportNamedStyle(style)
    memory_layer.importNamedStyle(style)

    root = project.layerTreeRoot()
    node = root.findLayer(layer.id())
    project.addMapLayer(memory_layer, False)
    if node is None:
        root.addLayer(memory_layer)
    else:
        parent = node.parent()
        parent.insertLayer(parent.children().index(node), memory_layer)
    project.removeMapLayer(layer.id())
    return memory_layer


def mldata_entries(path):
    """Names of the mldata files inside a project archive"""
    with zipfile.ZipFile(path) as archive:
        return [name for name in archive.namelist() if ".mldata" in name or name.endswith("layers.mldata")]


def extract_entry(path, entry, folder):
    with zipfile.ZipFile(path) as archive:
        target = Path(folder) / Path(entry).name
        target.write_bytes(archive.read(entry))
    return str(target)


def restore_layers(path, infos):
    """Add memory layers to the project from the layers of an mldata file described by Reader.scan().

    Returns the added layers. Raises MldataError if a selected layer could only be partially read."""
    project = QgsProject.instance()
    targets = {}
    for info in infos:
        existing = project.mapLayer(info["id"])
        if "crs" in info:
            crs = QgsCoordinateReferenceSystem.fromWkt(info["crs"])
        else:
            # v1 and v2 files do not store the CRS
            crs = existing.crs() if existing is not None else project.crs()
        layer = QgsVectorLayer(wkb_type_name(info.get("wkb_type", 0)), info["name"], "memory")
        layer.setCrs(crs)
        targets[info["id"]] = layer
    added = list(targets.values())
    project.addMapLayers(added)
    try:
        with Reader(path) as reader:
            reader.read_layers(targets)
    finally:
        for layer in added:
            layer.triggerRepaint()
    return added


class RestoreDialog(QDialog):
    """Show the content of an mldata file and let the user choose the layers to restore"""

    def __init__(self, path, infos, reader_version, parent=None):
        super().__init__(parent)
        self.infos = infos
        self.setWindowTitle(self.tr("Restore layers from file"))
        self.resize(640, 360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("{0} (format v{1})").format(path, reader_version), self))
        self.table = QTableWidget(len(infos), 4, self)
        self.table.setHorizontalHeaderLabels(
            [self.tr("Layer"), self.tr("Features"), self.tr("Fields"), self.tr("Status")]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, info in enumerate(infos):
            name = QTableWidgetItem(info["name"])
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(str(info["features"])))
            self.table.setItem(row, 2, QTableWidgetItem(str(len(info.get("fields", [])))))
            status = self.tr("Damaged, partially readable") if info["error"] else self.tr("OK")
            item = QTableWidgetItem(status)
            item.setToolTip(info["error"])
            self.table.setItem(row, 3, item)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_infos(self):
        return [
            info
            for row, info in enumerate(self.infos)
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]
