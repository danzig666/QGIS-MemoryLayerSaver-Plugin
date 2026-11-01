from qgis.PyQt.QtCore import QDataStream, QIODevice, QMetaType, QSaveFile

from .mldata import (
    FORMAT_V2,
    FORMAT_V3,
    INT16_MAX,
    INT16_MIN,
    MAGIC,
    STREAM_VERSION_LEGACY,
    STREAM_VERSION_V3,
    encode_geometry_value,
    is_geometry_field,
)
from .toolbox import log, log_warning, plugin_version, unfiltered_provider


class Writer:
    """Write the committed data of memory layers. The target is only replaced by commit()."""

    def __init__(self, filename, version=FORMAT_V2, layer_count=0, progress=None):
        self._filename = filename
        self._version = version
        self._layer_count = layer_count
        self._progress = progress
        self._file = None
        self._dstream = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.close()

    def open(self):
        self._file = QSaveFile(self._filename)
        if not self._file.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(f"Cannot open {self._filename}: {self._file.errorString()}")
        self._dstream = QDataStream(self._file)
        self._dstream.setVersion(STREAM_VERSION_LEGACY)
        for c in MAGIC:
            self._dstream.writeUInt8(c)
        # Version of MLD format
        self._dstream.writeUInt32(self._version)
        if self._version >= FORMAT_V3:
            self._dstream.setVersion(STREAM_VERSION_V3)
            self._dstream.writeQString(plugin_version())
            self._dstream.writeInt32(self._layer_count)

    def commit(self):
        """Replace the target file with what has been written"""
        self.check_status()
        if not self._file.commit():
            error = self._file.errorString()
            self._dstream = None
            self._file = None
            raise OSError(f"Cannot write {self._filename}: {error}")
        self._dstream = None
        self._file = None

    def close(self):
        """Discard what has been written, the target file stays untouched"""
        if self._file is not None:
            self._file.cancelWriting()
        self._dstream = None
        self._file = None

    def check_status(self):
        if self._dstream.status() != QDataStream.Status.Ok:
            raise OSError(f"Cannot write {self._filename}: {self._file.errorString()}")

    def write_layers(self, layers):
        for layer in layers:
            self.write_layer(layer)
            self.check_status()

    def write_layer(self, layer):
        log("Writing layer " + layer.id())
        if not self._dstream:
            raise ValueError("Layer stream not open for reading")
        ds = self._dstream
        ds.writeQString(layer.id())
        if self._version < FORMAT_V3:
            self.write_layer_content(layer)
            return

        # The size of the payload is known once it is written
        size_position = self._file.pos()
        ds.writeInt64(0)
        start = self._file.pos()
        self.write_layer_content(layer)
        end = self._file.pos()
        self._file.seek(size_position)
        ds.writeInt64(end - start)
        self._file.seek(end)

    def write_layer_content(self, layer):
        ds = self._dstream
        dp = layer.dataProvider()
        v3 = self._version >= FORMAT_V3
        ds.writeQString(layer.subsetString())
        if v3:
            ds.writeQString(layer.name())
            ds.writeQString(layer.crs().toWkt())
            ds.writeInt32(int(dp.wkbType()))

        fields = dp.fields()
        ds.writeInt32(len(fields)) if v3 else ds.writeInt16(len(fields))
        for field in fields:
            ds.writeQString(field.name())
            if v3:
                ds.writeInt32(int(field.type()))
                ds.writeQString(field.typeName())
                ds.writeInt32(int(field.length()))
                ds.writeInt32(int(field.precision()))
                ds.writeQString(field.comment())
                ds.writeInt32(int(field.subType()))
            else:
                ds.writeInt16(self.type_id_v2(layer, field))
                ds.writeQString(field.typeName())
                ds.writeInt16(self.clamped(layer, field, "length", field.length()))
                ds.writeInt16(self.clamped(layer, field, "precision", field.precision()))
                ds.writeQString(field.comment())

        # Attribute values Qt cannot stream as they are in this format version
        if v3:
            encoded = [(i, encode_geometry_value) for i, field in enumerate(fields) if is_geometry_field(field)]
        else:
            encoded = [(i, self.datetime_v2) for i, field in enumerate(fields) if field.type() == QMetaType.Type.QDateTime]

        progress = self._progress
        written = 0
        # The provider only holds committed data, and the filter must not hide features from the file
        with unfiltered_provider(layer):
            for feat in dp.getFeatures():
                ds.writeBool(True)
                if v3:
                    ds.writeInt64(feat.id())
                values = feat.attributes()
                for i, encode in encoded:
                    values[i] = encode(values[i])
                for value in values:
                    ds.writeQVariant(value)
                geom = feat.geometry()
                if not geom:
                    ds.writeUInt32(0)
                else:
                    wkb = geom.asWkb()
                    ds.writeUInt32(len(wkb))
                    ds.writeRawData(wkb)
                written += 1
                if progress and not written % 1000:
                    progress.advance(1000)
        if progress:
            progress.advance(written % 1000)
        ds.writeBool(False)

    @staticmethod
    def datetime_v2(value):
        """The Qt_4_5 stream drops the offset and keeps the clock time, which would move the value
        to another instant when read back. Local time keeps the instant."""
        if hasattr(value, "toLocalTime") and value.isValid():
            return value.toLocalTime()
        return value

    @staticmethod
    def clamped(layer, field, what, value):
        """Lengths and precisions which do not fit the v2 format lose their declared value, not the data"""
        value = int(value)
        clamped = max(INT16_MIN, min(INT16_MAX, value))
        if clamped != value:
            log_warning(f"{layer.name()}.{field.name()}: {what} {value} stored as {clamped}, as v2 files are limited")
        return clamped

    @staticmethod
    def type_id_v2(layer, field):
        value = int(field.type())
        if INT16_MIN <= value <= INT16_MAX:
            return value
        log_warning(f"{layer.name()}.{field.name()}: type {value} cannot be stored in a v2 file")
        return int(QMetaType.Type.UnknownType)
