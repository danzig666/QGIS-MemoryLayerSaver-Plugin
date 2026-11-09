import configparser
import tempfile
from pathlib import Path

from qgis import utils as qgis_utils
from qgis.core import Qgis, QgsApplication, QgsProject, QgsSettings
from qgis.PyQt.QtCore import QFile, QFileInfo
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QPushButton, QStyle, QWidget
from qgis.utils import iface

from .layer_connector import LayerConnector
from .layer_tools import (
    LayerInfoDialog,
    RestoreDialog,
    convert_to_memory_layer,
    export_errors_details,
    export_errors_summary,
    export_to_geopackage,
    export_to_separate_geopackages,
    extract_entry,
    mldata_entries,
    restore_layers,
    switch_to_geopackage,
    temporary_file_layers,
)
from .mldata import FORMAT_V2, FORMAT_V3, MldataError, needs_v3
from .reader import Reader
from .settings import FORMAT_ALWAYS_V2, FORMAT_ALWAYS_V3, SKIP_MEMORY_LAYERS_CHECK_KEY, Settings
from .settings_dialog import SettingsDialog
from .toolbox import Progress, log, log_error, log_warning, unfiltered_provider
from .writer import Writer

DIR_PLUGIN_ROOT: Path = Path(__file__).parent

# The plugin this one was forked from, which works on the same data
ORIGINAL_PLUGIN_ID = "MemoryLayerSaver"
MLDATA_NAME = "layers.mldata"
BACKUP_SUFFIX = ".bak"
# Keeps what could not be loaded out of the way of the regular backups
FAILED_LOAD_SUFFIX = ".failed-load.bak"
# Below these, saving and loading are too quick for a progress indicator to be useful
PROGRESS_MIN_FEATURES = 50000
PROGRESS_MIN_BYTES = 5000000


class MemoryLayerSaver(LayerConnector):
    def __init__(self):
        # These are used by connect_layer, which runs when attaching in the parent constructor
        self.has_modified_layers = False
        self.load_failed = False
        self._busy = False
        self._fingerprints = {}
        self._stored_legacy_mode = None
        self._moved_layer_ids = set()
        self._has_orphaned_data = False
        self._warned_temporary_layers = set()
        self._warned_original_plugin = False
        super().__init__()
        proj = QgsProject.instance()
        self.has_modified_layers = proj.isDirty()

        proj.readProject.connect(self.load_data)
        proj.writeProject.connect(self.save_data)
        proj.cleared.connect(self.on_cleared)

    def initGui(self):  # noqa
        self.menu = iface.pluginMenu().addMenu(QIcon(str(DIR_PLUGIN_ROOT / "icon.svg")), self.tr("Memory layer saver"))
        self.menu.setObjectName("memory_layer_saver_menu")

        self.about_action = self.menu.addAction(
            self.menu.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation), self.tr("About")
        )
        self.about_action.setObjectName("memory_layer_saver_about")
        self.about_action.triggered.connect(self.show_about)

        self.info_action = self.menu.addAction(
            QIcon(str(DIR_PLUGIN_ROOT / "icon.svg")), self.tr("Display memory layer information")
        )
        self.info_action.setObjectName("memory_layer_saver_info")
        self.info_action.triggered.connect(self.show_info)

        self.export_action = self.menu.addAction(self.tr("Export memory layers to GeoPackage..."))
        self.export_action.setObjectName("memory_layer_saver_export")
        self.export_action.triggered.connect(self.export_layers)

        self.convert_action = self.menu.addAction(self.tr("Convert temporary file layers to memory layers"))
        self.convert_action.setObjectName("memory_layer_saver_convert")
        self.convert_action.triggered.connect(self.convert_temporary_layers)

        self.restore_action = self.menu.addAction(self.tr("Restore layers from file..."))
        self.restore_action.setObjectName("memory_layer_saver_restore")
        self.restore_action.triggered.connect(self.restore_from_file)

        self.settings_action = self.menu.addAction(
            QgsApplication.getThemeIcon("mActionOptions.svg"), self.tr("Settings")
        )
        self.settings_action.setObjectName("memory_layer_saver_settings")
        self.settings_action.triggered.connect(self.show_settings)

        Settings.restore_ask_to_save_memory_layers()
        self.stand_aside_for_original_plugin()
        log("MemoryLayerSaver loaded")

    def tr(self, message, *args, **kwargs):
        """Get the translation for a string using Qt translation API."""
        return QgsApplication.translate("MemoryLayerSaver", message, *args, **kwargs)

    def unload(self):
        iface.pluginMenu().removeAction(self.menu.menuAction())
        self.menu.deleteLater()
        self.detach()
        proj = QgsProject.instance()
        proj.readProject.disconnect(self.load_data)
        proj.writeProject.disconnect(self.save_data)
        proj.cleared.disconnect(self.on_cleared)
        log("MemoryLayerSaver unloaded")

    def on_cleared(self):
        """Called when the project is cleared (new project)"""
        self.has_modified_layers = False
        self.load_failed = False
        self._fingerprints = {}
        self._stored_legacy_mode = None
        self._moved_layer_ids = set()
        self._has_orphaned_data = False
        self._warned_temporary_layers = set()

    def layer_signals(self, layer):
        return (
            layer.committedAttributesDeleted,
            layer.committedAttributesAdded,
            layer.committedFeaturesRemoved,
            layer.committedFeaturesAdded,
            layer.committedAttributeValuesChanges,
            layer.committedGeometriesChanges,
            # There is no specific signal for the renaming of fields
            layer.afterCommitChanges,
            layer.subsetStringChanged,
        )

    def connect_layer(self, layer):
        if Settings.is_saved_layer(layer):
            for signal in self.layer_signals(layer):
                signal.connect(self.set_project_dirty)
            layer.dataSourceChanged.connect(self.on_data_source_changed)
            # The warning of QGIS on exit is pointless for this layer. It stays on for anything else.
            layer.setCustomProperty(SKIP_MEMORY_LAYERS_CHECK_KEY, 1)
            # Connect layer will be called when a layer is added to the project
            # So we set the has_modified_layers flag to ensure the mldata file will be
            # updated when the project is saved
            self.has_modified_layers = True
        elif Settings.is_memory_layer(layer):
            layer.removeCustomProperty(SKIP_MEMORY_LAYERS_CHECK_KEY)

    def disconnect_layer(self, layer):
        try:
            for signal in self.layer_signals(layer):
                signal.disconnect(self.set_project_dirty)
            layer.dataSourceChanged.disconnect(self.on_data_source_changed)
            layer.removeCustomProperty(SKIP_MEMORY_LAYERS_CHECK_KEY)
            # Disconnect layer will be called when a layer is removed from the project
            # So we set the has_modified_layers flag to ensure the mldata file will be
            # updated when the project is saved
            self.has_modified_layers = True
        except (AttributeError, TypeError):  # layer was not previously connected
            pass

    def load_data(self):
        """Load the memory layers from the .mldata file"""
        if self.stand_aside_for_original_plugin():
            return
        self.load_failed = False
        filepath = self.memory_layer_file()
        layers = list(self.memory_layers())
        orphaned = False
        # Projects saved by older plugin versions may hold an empty attachment
        if QFileInfo(filepath).size() > 0 and layers:
            log(f"Loading memory layers from {filepath} ({len(layers)} layers)")
            progress = None
            self._busy = True
            try:
                if QFileInfo(filepath).size() >= PROGRESS_MIN_BYTES:
                    progress = Progress(self.tr("Loading memory layers"))
                with Reader(filepath, progress) as reader:
                    reader.read_layers(layers)
                    orphaned = bool(set(reader.layer_ids) - {layer.id() for layer in layers})
            except Exception as e:
                # From there on the file holds more than the layers, it must not be overwritten
                self.load_failed = True
                log_error(f"Error reloading memory layers: {e}")
                QMessageBox.warning(
                    iface.mainWindow(),
                    self.tr("Error reloading memory layers"),
                    self.tr(
                        "Some memory layers could not be loaded and may be incomplete. The file was not changed, "
                        "and will not be overwritten unless you confirm it when saving the project.\n\n"
                        "What can be read can be restored with 'Restore layers from file'.\n\n{0}"
                    ).format(e),
                )
            finally:
                self._busy = False
                if progress:
                    progress.finish()

        elif QFileInfo(filepath).size() > 0:
            orphaned = self.file_has_layers(filepath)

        self.warn_about_mismatching_legacy_file()
        self.set_stored_state(layers)
        # Older plugin versions left the data of the layers which were not memory layers any more
        self._has_orphaned_data = orphaned

    def save_data(self):
        """Write the layers to the .mldata file"""
        if self._busy or self.stand_aside_for_original_plugin():
            return

        layers = list(self.memory_layers())
        self.warn_about_temporary_layers()
        self.warn_about_uncommitted_edits(layers)

        filepath = self.memory_layer_file(fallback_to_legacy=False)
        has_data = bool(filepath) and QFileInfo(filepath).size() > 0

        if not layers:
            self.discard_data(filepath, has_data)
            return
        # Check if the mldata file exists and if any memory layer has been modified
        if has_data and not self.needs_rewrite(layers):
            return
        if has_data and not self.may_overwrite(filepath, layers):
            # Stay flagged: the changes are still not stored anywhere
            self.has_modified_layers = True
            return

        # If mldata file do not exist in the attached files, create it
        created = False
        if not Settings.legacy_mode() and not filepath:
            QgsProject.instance().createAttachedFile(MLDATA_NAME)
            created = True

        filepath = self.memory_layer_file()
        log(f"Saving memory layers to {filepath} ({len(layers)} layers)")
        progress = None
        self._busy = True
        try:
            # Moved data is safe in its new place, and must not stay here
            if has_data and Settings.keep_backup() and (self.has_deleted_layers() or not self._moved_layer_ids):
                self.backup_file(filepath, BACKUP_SUFFIX)
            else:
                self.remove_data_file(BACKUP_SUFFIX)
            total = sum(self.total_features(layer) for layer in layers)
            if total >= PROGRESS_MIN_FEATURES:
                progress = Progress(self.tr("Saving memory layers"), total)
            with Writer(filepath, self.format_version(layers), len(layers), progress) as writer:
                writer.write_layers(layers)
        except Exception as e:
            # The previous file is still there, unchanged
            self.has_modified_layers = True
            log_error(f"Error saving memory layers: {e}")
            if created:
                self.remove_empty_attachment(filepath)
            iface.messageBar().pushMessage(
                self.tr("Memory Layer Saver"),
                self.tr("The memory layers could not be saved: {0}").format(e),
                Qgis.MessageLevel.Critical,
                0,
            )
            return
        finally:
            self._busy = False
            if progress:
                progress.finish()

        self.load_failed = False
        self.set_stored_state(layers)

    def stand_aside_for_original_plugin(self):
        """Both plugins work on the same data, and would overwrite the work of each other: while the
        original plugin is active, it is left in charge. The data stays readable by both."""
        if ORIGINAL_PLUGIN_ID not in qgis_utils.active_plugins:
            return False
        log_warning("The original Memory Layer Saver plugin is active: this fork does nothing")
        if not self._warned_original_plugin:
            self._warned_original_plugin = True
            widget = iface.messageBar().createMessage(
                self.tr("Memory Layer Saver"),
                self.tr(
                    "The original Memory Layer Saver plugin is also active. "
                    "Until it is disabled, this fork does nothing and its fixes do not apply."
                ),
            )
            button = QPushButton(self.tr("Disable the original plugin"), widget)
            button.clicked.connect(self.disable_original_plugin)
            widget.layout().addWidget(button)
            iface.messageBar().pushWidget(widget, Qgis.MessageLevel.Warning, 0)
        return True

    def disable_original_plugin(self):
        """Take over from the original plugin, keeping what happened in the project until then"""
        qgis_utils.unloadPlugin(ORIGINAL_PLUGIN_ID)
        QgsSettings().setValue(f"PythonPlugins/{ORIGINAL_PLUGIN_ID}", False)
        iface.messageBar().clearWidgets()
        self._warned_original_plugin = False
        modified = self.has_modified_layers
        # Layers which are already loaded are left as they are
        self.load_data()
        self.has_modified_layers = self.has_modified_layers or modified

    @staticmethod
    def total_features(layer):
        with unfiltered_provider(layer):
            return layer.dataProvider().featureCount()

    @staticmethod
    def format_version(layers):
        file_format = Settings.file_format()
        if file_format == FORMAT_ALWAYS_V2:
            return FORMAT_V2
        if file_format == FORMAT_ALWAYS_V3 or any(needs_v3(layer) for layer in layers):
            return FORMAT_V3
        return FORMAT_V2

    @staticmethod
    def fingerprint(layer):
        """Catches what no signal reports, like most of the edits made directly on the data provider"""
        dp = layer.dataProvider()
        return dp.featureCount(), dp.extent().toString(), tuple(field.name() for field in dp.fields())

    def set_stored_state(self, layers):
        """Remember what the file holds, to detect the changes"""
        self.has_modified_layers = False
        self._stored_legacy_mode = Settings.legacy_mode()
        self._fingerprints = {layer.id(): self.fingerprint(layer) for layer in layers}
        self._moved_layer_ids = set()
        self._has_orphaned_data = False

    def has_deleted_layers(self):
        """Whether the file holds layers which are gone from the project, not moved to a file of their own"""
        return self._has_orphaned_data or bool(set(self._fingerprints) - self._moved_layer_ids - self.current_ids())

    def current_ids(self):
        return {layer.id() for layer in self.memory_layers()}

    def discard_data(self, filepath, has_data):
        """No memory layer is left: nothing has to stay in the project. Data of deleted layers
        is kept in the backup until the next save, as they cannot be found anywhere else."""
        if not has_data:
            self.remove_data_file("")
            self.remove_data_file(BACKUP_SUFFIX)
            return
        if not self.may_overwrite(filepath, []):
            self.has_modified_layers = True
            return
        if Settings.keep_backup() and self.has_deleted_layers():
            self.backup_file(filepath, BACKUP_SUFFIX)
        else:
            self.remove_data_file(BACKUP_SUFFIX)
        self.remove_data_file("")
        self.load_failed = False
        self.set_stored_state([])

    def needs_rewrite(self, layers):
        return (
            self.has_modified_layers
            or self._has_orphaned_data
            or Settings.always_rewrite()
            or (not Settings.keep_backup() and self.data_file(BACKUP_SUFFIX))
            or self._stored_legacy_mode != Settings.legacy_mode()
            or self._fingerprints != {layer.id(): self.fingerprint(layer) for layer in layers}
        )

    def may_overwrite(self, filepath, layers):
        """Whether the existing data may be replaced. Does not ask for an intended removal of the layers."""
        if self.load_failed:
            if not self.confirm_overwrite(
                self.tr(
                    "The data of this project could not be loaded completely when it was opened. "
                    "Saving the memory layers now would replace it with what was loaded.\n\n"
                    "Replace it? If you choose No, the file stays as it is and "
                    "the changes made to memory layers are not saved."
                )
            ):
                self.push_not_saved_warning()
                return False
            self.backup_file(filepath, FAILED_LOAD_SUFFIX)
            return True

        if layers and not sum(self.total_features(layer) for layer in layers) and self.file_has_features(filepath):
            if not self.confirm_overwrite(
                self.tr(
                    "All memory layers of this project are empty, but the file saved with it holds features. "
                    "Replace the file with the empty layers?"
                )
            ):
                self.push_not_saved_warning()
                return False
        return True

    def confirm_overwrite(self, message):
        answer = QMessageBox.warning(
            iface.mainWindow(),
            self.tr("Memory Layer Saver"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def push_not_saved_warning(self):
        iface.messageBar().pushMessage(
            self.tr("Memory Layer Saver"),
            self.tr("The memory layers were not saved, the existing file was kept."),
            Qgis.MessageLevel.Warning,
            0,
        )

    @staticmethod
    def file_has_features(filepath):
        try:
            with Reader(filepath) as reader:
                infos = reader.scan()
                return bool(reader.errors) or any(info["features"] for info in infos)
        except MldataError:
            # Unreadable does not mean empty
            return True

    @staticmethod
    def file_has_layers(filepath):
        try:
            with Reader(filepath) as reader:
                return reader.has_layers()
        except MldataError:
            # Nothing can be told about it, it is left alone
            return False

    def data_file(self, suffix):
        """The path of a file of the plugin for this project, None if there is none"""
        if Settings.legacy_mode():
            path = self.legacy_memory_layer_file() + suffix
            return path if QFileInfo(path).exists() else None
        name = MLDATA_NAME + suffix
        return next((f for f in QgsProject.instance().attachedFiles() if f.endswith(name)), None)

    def remove_data_file(self, suffix):
        path = self.data_file(suffix)
        if not path:
            return
        if Settings.legacy_mode():
            QFile.remove(path)
        else:
            QgsProject.instance().removeAttachedFile(path)

    def backup_file(self, filepath, suffix):
        """Copy a mldata file aside, in the same place as the file itself"""
        if Settings.legacy_mode():
            target = filepath + suffix
        else:
            name = MLDATA_NAME + suffix
            target = next((f for f in QgsProject.instance().attachedFiles() if f.endswith(name)), None)
            target = target or QgsProject.instance().createAttachedFile(name)
        QFile.remove(target)
        if not QFile.copy(filepath, target):
            log_warning(f"Cannot back up {filepath} to {target}")

    @staticmethod
    def remove_empty_attachment(filepath):
        if filepath and not Settings.legacy_mode() and QFileInfo(filepath).size() == 0:
            QgsProject.instance().removeAttachedFile(filepath)

    def warn_about_temporary_layers(self):
        """Warn once about the temporary layers which would be lost with the project"""
        layers = [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if layer.isTemporary()
            and not Settings.is_saved_layer(layer)
            and not (Settings.is_memory_layer(layer) and layer.featureCount() == 0)
        ]
        names = {layer.id() for layer in layers} - self._warned_temporary_layers
        if not names:
            return
        self._warned_temporary_layers |= names
        widget = iface.messageBar().createMessage(
            self.tr("Memory Layer Saver"),
            self.tr("These temporary layers are not saved with the project: {0}").format(
                ", ".join(layer.name() for layer in layers)
            ),
        )
        if temporary_file_layers():
            button = QPushButton(self.tr("Convert to memory layers"), widget)
            button.clicked.connect(self.convert_temporary_layers)
            widget.layout().addWidget(button)
        iface.messageBar().pushWidget(widget, Qgis.MessageLevel.Warning, 0)

    def warn_about_uncommitted_edits(self, layers):
        names = [layer.name() for layer in layers if layer.isEditable() and layer.isModified()]
        if names:
            iface.messageBar().pushMessage(
                self.tr("Memory Layer Saver"),
                self.tr("Uncommitted edits are not saved with the project: {0}").format(", ".join(names)),
                Qgis.MessageLevel.Warning,
                10,
            )

    def warn_about_mismatching_legacy_file(self):
        """A separate file more recent than the project is likely the work of an older plugin version,
        which does not know about the embedded data."""
        proj = QgsProject.instance()
        if Settings.legacy_mode() or not proj.fileName() or not self.memory_layer_file(fallback_to_legacy=False):
            return
        legacy = QFileInfo(self.legacy_memory_layer_file())
        if legacy.exists() and legacy.lastModified() > QFileInfo(proj.fileName()).lastModified():
            log_warning(f"{legacy.filePath()} is more recent than the data embedded in the project")
            iface.messageBar().pushMessage(
                self.tr("Memory Layer Saver"),
                self.tr(
                    "{0} is more recent than the project: it may have been edited with an older version of the "
                    "plugin. Those changes are not loaded, use 'Restore layers from file' to get them."
                ).format(legacy.fileName()),
                Qgis.MessageLevel.Warning,
                0,
            )

    def memory_layers(self):
        """Return a list of all memory layers in the project"""
        return [layer for layer in QgsProject.instance().mapLayers().values() if Settings.is_saved_layer(layer)]

    def legacy_memory_layer_file(self):
        """Returns the path to the legacy .mldata file"""
        return QgsProject.instance().fileName() + ".mldata"

    def memory_layer_file(self, fallback_to_legacy=True):
        """Returns the path to the .mldata file"""
        name = QgsProject.instance().fileName()
        if not name:
            return ""

        # Legacy mode (old QGIS versions or mldata stored in a separate file)
        if Settings.legacy_mode():
            return self.legacy_memory_layer_file()

        # Find the layers.mldata file in the attached files
        # Note attached files are prefixed with a random string hence the use of endswith
        for attachment in QgsProject.instance().attachedFiles():
            if attachment.endswith(MLDATA_NAME):
                return attachment

        # The layers.mldata was not found in the attached files
        if fallback_to_legacy:
            return self.legacy_memory_layer_file()

    def set_project_dirty(self):
        """Set project as dirty when a memory layer is modified"""
        if self._busy:
            return
        self.has_modified_layers = True
        QgsProject.instance().setDirty(True)

    def on_data_source_changed(self):
        # If a temporary layer is made permanent, its data source will change
        # At this point, the layer is no longer a memory layer, so we disconnect from it
        layer = self.sender()
        if not Settings.is_saved_layer(layer):
            self._moved_layer_ids.add(layer.id())
            self.disconnect_layer(layer)

    def show_info(self):
        """Display some information about the memory layers"""
        dialog = self.info_dialog()
        if dialog is None:
            QMessageBox.information(
                iface.mainWindow(), "Memory Layer Saver", self.tr("This project contains no memory layers to be saved")
            )
        else:
            dialog.exec()

    def info_dialog(self):
        layers = self.memory_layers()
        if not layers:
            return None
        version = self.format_version(layers)
        sizes = []
        progress = None
        total = sum(self.total_features(layer) for layer in layers)
        if total >= PROGRESS_MIN_FEATURES:
            progress = Progress(self.tr("Measuring memory layers"))
        try:
            for i, layer in enumerate(layers):
                try:
                    sizes.append(Writer.layer_size(layer, version))
                except Exception as e:  # noqa: BLE001
                    # The save would report it, this is only information
                    log_warning(f"Cannot measure layer {layer.name()}: {e}")
                    sizes.append(None)
                if progress:
                    progress.set_value((i + 1) * 100 // len(layers))
        finally:
            if progress:
                progress.finish()
        return LayerInfoDialog(layers, sizes, iface.mainWindow())

    def export_layers(self):
        """Export the memory layers to one GeoPackage or to one per layer, and offer to use them instead"""
        layers = self.memory_layers()
        if not layers:
            QMessageBox.information(
                iface.mainWindow(),
                self.tr("Memory Layer Saver"),
                self.tr("This project contains no memory layers to be saved"),
            )
            return

        separate = len(layers) > 1 and self.ask_for_separate_files(len(layers))
        if separate is None:
            return
        if separate:
            target = QFileDialog.getExistingDirectory(iface.mainWindow(), self.tr("Export memory layers"))
            if not target:
                return
            exported, errors = export_to_separate_geopackages(layers, target)
        else:
            target, _filter = QFileDialog.getSaveFileName(
                iface.mainWindow(), self.tr("Export memory layers"), "", self.tr("GeoPackage (*.gpkg)")
            )
            if not target:
                return
            if not target.lower().endswith(".gpkg"):
                target += ".gpkg"
            exported, errors = export_to_geopackage(layers, target)

        message = self.tr("{0} layers exported to {1}.").format(len(exported), target)
        if errors:
            message += "\n\n" + self.tr("Not exported:") + "\n" + export_errors_summary(errors)
        if not exported:
            self.export_result_box(message, errors, False).exec()
            return
        message += "\n\n" + self.tr("Make the exported layers of this project use the GeoPackage from now on?")
        if self.export_result_box(message, errors, True).exec() == QMessageBox.StandardButton.Yes:
            switch_to_geopackage(exported)
            QgsProject.instance().setDirty(True)

    def ask_for_separate_files(self, count):
        """Whether to export to one file per layer. None if the user cancels."""
        box = QMessageBox(iface.mainWindow())
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Export memory layers"))
        box.setText(self.tr("How should the {0} memory layers be exported?").format(count))
        single = box.addButton(self.tr("One GeoPackage"), QMessageBox.ButtonRole.AcceptRole)
        separate = box.addButton(self.tr("One file per layer"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(single)
        box.exec()
        if box.clickedButton() == single:
            return False
        return True if box.clickedButton() == separate else None

    def export_result_box(self, message, errors, ask):
        """The complete errors go to the details, which scroll: the dialog itself has to fit the screen"""
        box = QMessageBox(iface.mainWindow())
        box.setWindowTitle(self.tr("Memory Layer Saver"))
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Warning if errors else QMessageBox.Icon.Question)
        if errors:
            box.setDetailedText(export_errors_details(errors))
        if ask:
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        else:
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
        return box

    def convert_temporary_layers(self):
        """Turn the layers stored in temporary files into memory layers, which are saved with the project"""
        layers = temporary_file_layers()
        failed = [layer.name() for layer in layers if convert_to_memory_layer_safe(layer) is None]
        message = self.tr("{0} layers converted to memory layers.").format(len(layers) - len(failed))
        if failed:
            message += " " + self.tr("Failed: {0}").format(", ".join(failed))
        iface.messageBar().pushMessage(
            self.tr("Memory Layer Saver"),
            message,
            Qgis.MessageLevel.Warning if failed else Qgis.MessageLevel.Info,
            10,
        )

    def restore_from_file(self):
        """Add the content of a mldata file to the project, as new memory layers"""
        path, _filter = QFileDialog.getOpenFileName(
            iface.mainWindow(),
            self.tr("Restore layers from file"),
            QFileInfo(QgsProject.instance().fileName()).path(),
            self.tr("Memory layer data or QGIS project (*.mldata *.bak *.qgz *.zip);;All files (*)"),
        )
        if not path:
            return
        with tempfile.TemporaryDirectory() as folder:
            try:
                if path.lower().endswith((".qgz", ".zip")):
                    entries = mldata_entries(path)
                    if not entries:
                        raise MldataError(self.tr("{0} holds no memory layer data").format(path))
                    entry = entries[0]
                    if len(entries) > 1:
                        entry, ok = QInputDialog.getItem(
                            iface.mainWindow(), self.tr("Restore layers from file"), self.tr("File"), entries, 0, False
                        )
                        if not ok:
                            return
                    path = extract_entry(path, entry, folder)
                with Reader(path) as reader:
                    infos = reader.scan()
                    version = reader.version
                dialog = RestoreDialog(path, infos, version, iface.mainWindow())
                if dialog.exec() and dialog.selected_infos():
                    restore_layers(path, dialog.selected_infos())
            except (MldataError, OSError) as e:
                QMessageBox.warning(iface.mainWindow(), self.tr("Restore layers from file"), str(e))

    def show_about(self):
        """Display the about message box"""
        # Used to display plugin icon in the about message box
        bogus = QWidget(iface.mainWindow())
        bogus.setWindowIcon(QIcon(str(DIR_PLUGIN_ROOT / "icon.svg")))

        # Get plugin metadata
        cfg = configparser.ConfigParser()
        cfg.read(DIR_PLUGIN_ROOT / "metadata.txt")

        name = cfg.get("general", "name")
        version = cfg.get("general", "version")
        repository = cfg.get("general", "repository")
        tracker = cfg.get("general", "tracker")
        homepage = cfg.get("general", "homepage")
        forked_from = cfg.get("general", "forkedFrom")
        QMessageBox.about(
            bogus,
            self.tr("About {0}").format(name),
            "<b>Version</b> {}<br><br>"
            "<b>{}</b> : <a href={}>GitHub</a><br>"
            "<b>{}</b> : <a href={}>GitHub</a><br>"
            "<b>{}</b> : <a href={}>GitHub</a><br><br>"
            "{} <a href={}>Memory Layer Saver</a>".format(
                version,
                self.tr("Source code"),
                repository,
                self.tr("Report issues"),
                tracker,
                self.tr("Documentation"),
                homepage,
                self.tr("This is a fork of"),
                forked_from,
            ),
        )
        bogus.deleteLater()

    def show_settings(self):
        """Show the settings dialog"""
        before = (Settings.legacy_mode(), Settings.file_format())
        if SettingsDialog(iface.mainWindow()).exec() and before != (Settings.legacy_mode(), Settings.file_format()):
            self.has_modified_layers = True
            QgsProject.instance().setDirty(True)


def convert_to_memory_layer_safe(layer):
    try:
        return convert_to_memory_layer(layer)
    except (RuntimeError, ValueError) as e:
        log_error(f"Cannot convert {layer.name()} to a memory layer: {e}")
        return None
