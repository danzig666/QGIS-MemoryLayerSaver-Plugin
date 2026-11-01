"""Ways out of memory layers: GeoPackage export, conversion of temporary files, recovery of mldata files"""

import re
import zipfile
from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeatureRequest,
    QgsProject,
    QgsProviderConnectionException,
    QgsProviderRegistry,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QFile, Qt
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

RESERVED_FILE_NAME = re.compile(r"CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]", re.IGNORECASE)
MAX_ERROR_LENGTH = 100
MAX_ERRORS_SHOWN = 5


def safe_name(name):
    """A layer name made safe for a file name, keeping accented letters"""
    base = re.sub(r"[^\w-]+", "_", name).strip("_-")[:100].rstrip("_-") or "layer"
    return f"{base}_layer" if RESERVED_FILE_NAME.fullmatch(base) else base


def unique_table_name(name, used):
    base = safe_name(name).replace("-", "_")
    # These prefixes are reserved by the GeoPackage format
    if base.lower().startswith(("gpkg_", "rtree_", "sqlite_")):
        base = f"layer_{base}"
    candidate = base
    index = 1
    while candidate.lower() in used:
        index += 1
        candidate = f"{base}_{index}"
    used.add(candidate.lower())
    return candidate


def unique_file_path(folder, name):
    """A path in folder which does not exist yet, based on the name of a layer"""
    base = safe_name(name)
    candidate = Path(folder) / f"{base}.gpkg"
    index = 1
    while candidate.exists():
        index += 1
        candidate = Path(folder) / f"{base}_{index}.gpkg"
    return str(candidate)


def unique_column_name(wanted, used):
    candidate = wanted
    index = 1
    while candidate.lower() in used:
        index += 1
        candidate = f"{wanted}_{index}"
    return candidate


def geopackage_options(layer, table, overwrite_file):
    """Writer options keeping all attributes of a layer, whatever the names of its fields"""
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = table
    options.actionOnExistingFile = (
        QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        if overwrite_file
        else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
    )

    # Column names are not case sensitive in a GeoPackage
    names = []
    used = set()
    for field in layer.fields():
        name = unique_column_name(field.name(), used)
        used.add(name.lower())
        names.append(name)
    options.attributes = list(range(len(names)))
    options.attributesExportNames = names

    # A field named fid would become the primary key, so it would have to hold unique integers. It stays
    # a regular column with its values, next to a key column of its own. The same goes for the geometry column.
    layer_options = []
    if "fid" in used:
        layer_options.append("FID=" + unique_column_name("gpkg_fid", used))
    if "geom" in used:
        layer_options.append("GEOMETRY_NAME=" + unique_column_name("geometry", used))
    options.layerOptions = layer_options
    return options


def discard_table(path, table, whole_file):
    """Remove what a failed export left behind"""
    try:
        if whole_file:
            QFile.remove(path)
        else:
            QgsProviderRegistry.instance().providerMetadata("ogr").createConnection(path, {}).dropVectorTable("", table)
    except QgsProviderConnectionException as e:
        log_warning(f"Cannot remove the incomplete table {table} from {path}: {e}")


def export_layer(layer, path, table, overwrite_file):
    """Export the whole content of a layer, whatever its filter. Returns the error, if any."""
    context = QgsProject.instance().transformContext()
    options = geopackage_options(layer, table, overwrite_file)
    with unfiltered_provider(layer):
        expected = layer.dataProvider().featureCount()
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, context, options)
    if result[0] == QgsVectorFileWriter.WriterError.NoError:
        written = QgsVectorLayer(f"{path}|layername={table}", "check", "ogr")
        count = written.featureCount() if written.isValid() else -1
        del written
        if count == expected:
            return ""
        error = f"{count} of {expected} features were written"
    else:
        error = result[1]
    log_warning(f"Cannot export layer {layer.name()} to {path}: {error}")
    return error


def export_to_geopackage(layers, path):
    """Export layers to the tables of a single GeoPackage, replacing the file.

    Returns the {layer: (path, table)} of the exported layers, and the {layer name: reason} of the others."""
    exported = {}
    errors = {}
    used = set()
    for layer in layers:
        if layer.isEditable():
            errors[layer.name()] = "the layer is being edited"
            continue
        table = unique_table_name(layer.name(), used)
        error = export_layer(layer, path, table, not exported)
        if error:
            errors[layer.name()] = error
            discard_table(path, table, not exported)
        else:
            exported[layer] = (path, table)
    return exported, errors


def export_to_separate_geopackages(layers, folder):
    """Export each layer to a GeoPackage of its own in a folder, leaving the existing files alone.

    Returns the {layer: (path, table)} of the exported layers, and the {layer name: reason} of the others."""
    exported = {}
    errors = {}
    for layer in layers:
        if layer.isEditable():
            errors[layer.name()] = "the layer is being edited"
            continue
        path = unique_file_path(folder, layer.name())
        table = unique_table_name(layer.name(), set())
        error = export_layer(layer, path, table, True)
        if error:
            errors[layer.name()] = error
            discard_table(path, table, True)
        else:
            exported[layer] = (path, table)
    return exported, errors


def short_error(error):
    """Writers report the same error for each feature: only keep the first one, and how many there are"""
    lines = [line.strip() for line in error.splitlines() if line.strip()]
    if not lines:
        return ""
    if lines[0].endswith(":") and len(lines) > 1:
        lines = lines[1:]
    text = lines[0] if len(lines[0]) <= MAX_ERROR_LENGTH else lines[0][:MAX_ERROR_LENGTH] + "..."
    return f"{text} ({len(lines)} times)" if len(lines) > 1 else text


def export_errors_summary(errors):
    """A few lines about the layers which were not exported, for a dialog which has to fit the screen"""
    lines = [f"{name}: {short_error(error)}" for name, error in list(errors.items())[:MAX_ERRORS_SHOWN]]
    if len(errors) > MAX_ERRORS_SHOWN:
        lines.append(f"... and {len(errors) - MAX_ERRORS_SHOWN} more")
    return "\n".join(lines)


def export_errors_details(errors):
    return "\n\n".join(f"{name}:\n{error}" for name, error in errors.items())


def switch_to_geopackage(exported):
    """Make exported layers read from their GeoPackage, keeping their style and filter"""
    for layer, (path, table) in exported.items():
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
