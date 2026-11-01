from qgis.core import Qgis, QgsMapLayer, QgsSettings

# Used by QGIS to prompt user to save memory layers on exit.
ASK_TO_SAVE_MEMORY_LAYER_KEY = "askToSaveMemoryLayers"
# Used by MLS up to 6.0 to save the original value of ASK_TO_SAVE_MEMORY_LAYER_KEY.
BACKUP_KEY = "MemoryLayerSaver/memoryLayerSaveSetting"
# Can be used to disable saving on a per-layer basis.
SAVE_LAYER_KEY = "SaveMemoryProvider"
# Layer property QGIS looks at to leave a memory layer out of its warning on exit.
SKIP_MEMORY_LAYERS_CHECK_KEY = "skipMemoryLayersCheck"
# Wheter the mldata is embedded in the attachment.zip(qgs)/the project file (qgz)
# or stored in a separate .mldata file (legacy). This can be changed from
# the settings dialog, to export a project that can be opened in older QGIS versions (< 3.22).
MLDATA_EMBEDDED = "MemoryLayerSaver/mldataEmbedded"
# Rewrite the mldata on every project save, whether or not a change was detected.
ALWAYS_REWRITE = "MemoryLayerSaver/alwaysRewrite"
# Keep the previous content of the mldata in a .bak file when rewriting it.
KEEP_BACKUP = "MemoryLayerSaver/keepBackup"
# Which format to write: FORMAT_AUTO, FORMAT_ALWAYS_V2 or FORMAT_ALWAYS_V3.
FILE_FORMAT = "MemoryLayerSaver/fileFormat"

FORMAT_AUTO = "auto"
FORMAT_ALWAYS_V2 = "v2"
FORMAT_ALWAYS_V3 = "v3"


class Settings:
    @classmethod
    def get_settings(cls):
        settings = QgsSettings()
        settings.beginGroup("MemoryLayerSaver", QgsSettings.Section.Plugins)
        return settings

    @classmethod
    def restore_ask_to_save_memory_layers(cls):
        """Up to 6.0, the plugin disabled the warning of QGIS globally. Undo that once: the warning
        must stay on for what the plugin does not save, and saved layers now opt out individually."""
        settings = cls.get_settings()
        if settings.contains(BACKUP_KEY):
            original = settings.value(BACKUP_KEY, True, bool)
            QgsSettings().setValue(ASK_TO_SAVE_MEMORY_LAYER_KEY, original, section=QgsSettings.Section.App)
            settings.remove(BACKUP_KEY)

    @classmethod
    def mldata_embedded(cls):
        return cls.get_settings().value(MLDATA_EMBEDDED, True, bool)

    @classmethod
    def set_mldata_embedded(cls, value):
        cls.get_settings().setValue(MLDATA_EMBEDDED, value)

    @classmethod
    def always_rewrite(cls):
        return cls.get_settings().value(ALWAYS_REWRITE, False, bool)

    @classmethod
    def set_always_rewrite(cls, value):
        cls.get_settings().setValue(ALWAYS_REWRITE, value)

    @classmethod
    def keep_backup(cls):
        return cls.get_settings().value(KEEP_BACKUP, True, bool)

    @classmethod
    def set_keep_backup(cls, value):
        cls.get_settings().setValue(KEEP_BACKUP, value)

    @classmethod
    def file_format(cls):
        return cls.get_settings().value(FILE_FORMAT, FORMAT_AUTO, str)

    @classmethod
    def set_file_format(cls, value):
        cls.get_settings().setValue(FILE_FORMAT, value)

    @classmethod
    def legacy_mode(cls):
        """Whether to use the legacy .mldata file format"""
        return Qgis.QGIS_VERSION_INT < 32200 or not cls.mldata_embedded()

    @staticmethod
    def is_memory_layer(layer):
        if layer.type() != QgsMapLayer.LayerType.VectorLayer:
            return False
        data_provider = layer.dataProvider()
        return data_provider is not None and data_provider.name() == "memory"

    @classmethod
    def is_saved_layer(cls, layer):
        if not cls.is_memory_layer(layer):
            return False
        return layer.customProperty(SAVE_LAYER_KEY, True) in [True, "true", "True"]
