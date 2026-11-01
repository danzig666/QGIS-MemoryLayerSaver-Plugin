from qgis.PyQt.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout

from .settings import FORMAT_ALWAYS_V2, FORMAT_ALWAYS_V3, FORMAT_AUTO, Settings


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Memory Layer Saver settings"))

        layout = QVBoxLayout(self)

        self.checkbox = QCheckBox(self.tr("Embed mldata file"), self)
        self.checkbox.setChecked(Settings.mldata_embedded())
        self.checkbox.setToolTip(
            self.tr(
                "If checked, the mldata file will be embedded inside the project file (qgz) "
                "or in the attachment.zip (qgs). "
                "Otherwise, it will be stored in a separate .mldata file beside the project file."
            )
        )

        self.backup_checkbox = QCheckBox(self.tr("Keep a backup of the previous data (.bak)"), self)
        self.backup_checkbox.setChecked(Settings.keep_backup())
        self.backup_checkbox.setToolTip(
            self.tr(
                "The previous data is kept next to the mldata file, and can be restored with "
                "'Restore layers from file'. This doubles the space memory layers take in the project. "
                "Turning this off removes the existing backup on the next project save."
            )
        )

        self.rewrite_checkbox = QCheckBox(self.tr("Rewrite the data on every project save"), self)
        self.rewrite_checkbox.setChecked(Settings.always_rewrite())
        self.rewrite_checkbox.setToolTip(
            self.tr(
                "Saving is otherwise skipped if no change was detected. Scripts changing only attribute values "
                "through the data provider cannot be detected."
            )
        )

        self.format_combo = QComboBox(self)
        self.format_combo.addItem(self.tr("Automatic (v2, v3 only for layers which need it)"), FORMAT_AUTO)
        self.format_combo.addItem(self.tr("Always v2 (readable by older plugin versions)"), FORMAT_ALWAYS_V2)
        self.format_combo.addItem(self.tr("Always v3 (keeps datetime offsets and feature ids)"), FORMAT_ALWAYS_V3)
        self.format_combo.setCurrentIndex(max(self.format_combo.findData(Settings.file_format()), 0))
        self.format_combo.setToolTip(
            self.tr(
                "Plugin versions older than 6.1 cannot open v3 files. With 'Always v2', field lengths "
                "above 32767 are reduced, and values of geometry fields are not kept."
            )
        )
        format_layout = QFormLayout()
        format_layout.addRow(self.tr("File format"), self.format_combo)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout.addWidget(self.checkbox)
        layout.addWidget(self.backup_checkbox)
        layout.addWidget(self.rewrite_checkbox)
        layout.addLayout(format_layout)
        layout.addStretch()
        layout.addWidget(button_box)

    def accept(self):
        Settings.set_mldata_embedded(self.checkbox.isChecked())
        Settings.set_keep_backup(self.backup_checkbox.isChecked())
        Settings.set_always_rewrite(self.rewrite_checkbox.isChecked())
        Settings.set_file_format(self.format_combo.currentData())
        super().accept()
