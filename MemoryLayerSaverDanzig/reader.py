import re

from qgis.core import Qgis, QgsFeature, QgsFeatureSink, QgsField, QgsGeometry, QgsWkbTypes
from qgis.PyQt.QtCore import QDataStream, QFile, QIODevice, QMetaType

from .mldata import (
    EMPTY_USER_TYPES,
    FORMAT_V3,
    MAGIC,
    NATIVE_SUBTYPES,
    READABLE_FORMATS,
    STREAM_USER_TYPE_IDS,
    STREAM_VERSION_LEGACY,
    STREAM_VERSION_V3,
    MldataError,
    decode_geometry_value,
    is_geometry_field,
)
from .toolbox import log, log_warning, plugin_version, version_tuple

BATCH_SIZE = 5000
# Above this number of placeholders, feature ids are not worth restoring
MAX_FID_PLACEHOLDERS = 1000000
MAX_FIELDS = 100000


class Reader:
    def __init__(self, filename, progress=None):
        self._filename = filename
        self._progress = progress
        self._file = None
        self._dstream = None
        self._version = None
        self._size = 0
        self._layer_end = None
        self.plugin_version = ""
        self.layer_count = None
        self.layer_ids = []
        self.errors = []

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def open(self):
        self._file = QFile(self._filename)
        if not self._file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise MldataError("Cannot open " + self._filename)
        self._size = self._file.size()
        self._dstream = QDataStream(self._file)
        self._dstream.setVersion(STREAM_VERSION_LEGACY)
        for c in MAGIC:
            ct = self._dstream.readUInt8()
            if ct != c:
                raise MldataError(self._filename + " is not a valid memory layer data file")
        version = self._dstream.readInt32()
        if version not in READABLE_FORMATS:
            raise MldataError(
                f"{self._filename} uses format {version}, which this version of the MemoryLayerSaver plugin "
                f"({plugin_version()}) cannot read. Please update the plugin."
            )
        self._version = version
        if version >= FORMAT_V3:
            self._dstream.setVersion(STREAM_VERSION_V3)
            self.plugin_version = self._dstream.readQString()
            self.layer_count = self._dstream.readInt32()
            self.check_status("header")
            if version_tuple(self.plugin_version) > version_tuple(plugin_version()):
                log_warning(f"{self._filename} was written by the newer plugin version {self.plugin_version}")

    def close(self):
        try:
            self._dstream.setDevice(None)
            self._file.close()
        except (AttributeError, RuntimeError):
            pass
        self._dstream = None
        self._file = None

    @property
    def version(self):
        return self._version

    def has_layers(self):
        return not self._dstream.atEnd()

    def check_status(self, where):
        if self._dstream.status() != QDataStream.Status.Ok:
            raise MldataError(f"{self._filename} is truncated or corrupt ({where})")

    def read_layers(self, layers):
        """Read a file into layers, given as a list or as a {file layer id: layer} dict.

        Everything readable is loaded. Raises MldataError at the end if anything was not."""
        targets = layers if isinstance(layers, dict) else {layer.id(): layer for layer in layers}

        def read(layer_id):
            target = targets.get(layer_id)
            if target is None:
                log(f"Unknown layer {layer_id} in project. Skipping.")
                self.skip_layer()
            elif target.dataProvider().featureCount() > 0:
                log_warning(f"Memory layer {target.id()} is already loaded. Skipping.")
                self.skip_layer()
            else:
                self.read_layer(target)

        self.for_each_layer(read)
        if self.errors:
            details = "\n".join(f"{layer_id}: {error}" if layer_id else error for layer_id, error in self.errors)
            raise MldataError(details)

    def scan(self):
        """Describe the content of the file without loading it. Errors are reported per layer."""
        result = []

        def scan_layer(layer_id):
            info = {"id": layer_id, "name": layer_name_from_id(layer_id), "features": 0, "error": ""}
            result.append(info)
            info.update(self.read_definition())
            for _fid, _values, wkb in self.read_features(info["fields"]):
                info["features"] += 1
                if wkb and "wkb_type" not in info:
                    geom = QgsGeometry()
                    geom.fromWkb(wkb)
                    info["wkb_type"] = geom.wkbType()

        self.for_each_layer(scan_layer)
        for layer_id, error in self.errors:
            for info in result:
                if info["id"] == layer_id:
                    info["error"] = error
        return result

    def for_each_layer(self, handler):
        """Run handler(layer id) with the stream on the content of each layer. Damaged layers are
        skipped when the format allows it (v3); otherwise everything after the damage is out of reach."""
        ds = self._dstream
        if not ds:
            raise MldataError("Layer stream not open for reading")
        self.errors = []
        seen = 0
        while not ds.atEnd():
            layer_id = None
            self._layer_end = None
            try:
                layer_id = ds.readQString()
                if self._version >= FORMAT_V3:
                    size = ds.readInt64()
                    self.check_status("layer header")
                    if size < 0 or self._file.pos() + size > self._size:
                        raise MldataError(f"{self._filename} is truncated or corrupt (layer size)")
                    self._layer_end = self._file.pos() + size
                self.check_status("layer header")
                seen += 1
                self.layer_ids.append(layer_id)
                handler(layer_id)
                self.check_status(f"layer {layer_id}")
            except (MldataError, TypeError, ValueError, OverflowError) as e:
                log_warning(f"Error reading layer {layer_id}: {e}")
                self.errors.append((layer_id, str(e)))
                if self._layer_end is None:
                    self.errors.append((None, "The layers stored after this point cannot be read from this file"))
                    return
                ds.resetStatus()
            if self._layer_end is not None:
                self._file.seek(self._layer_end)
            if self._progress:
                self._progress.set_value(self._file.pos() * 100 // max(self._size, 1))
        if self.layer_count is not None and seen != self.layer_count:
            self.errors.append((None, f"{self.layer_count} layers expected, {seen} found: the file is truncated"))

    def read_definition(self):
        """Read everything which comes before the features of a layer"""
        ds = self._dstream
        v3 = self._version >= FORMAT_V3
        definition = {"subset": ds.readQString() if self._version > 1 else ""}
        if v3:
            definition["name"] = ds.readQString()
            definition["crs"] = ds.readQString()
            definition["wkb_type"] = ds.readInt32()
        nattr = ds.readInt32() if v3 else ds.readInt16()
        self.check_status("layer definition")
        if not 0 <= nattr <= MAX_FIELDS:
            raise MldataError(f"{self._filename} is truncated or corrupt ({nattr} fields)")
        fields = []
        for _i in range(nattr):
            name = ds.readQString()
            qtype = ds.readInt32() if v3 else ds.readInt16()
            typename = ds.readQString()
            length = ds.readInt32() if v3 else ds.readInt16()
            precision = ds.readInt32() if v3 else ds.readInt16()
            comment = ds.readQString()
            subtype = ds.readInt32() if v3 else None
            self.check_status("field definition")
            fields.append(self.make_field(name, qtype, typename, length, precision, comment, subtype))
        definition["fields"] = fields
        return definition

    @staticmethod
    def make_field(name, qtype, typename, length, precision, comment, subtype):
        # User type ids depend on the Qt version the file was written with
        if typename.lower() == "geometry":
            field_type = QMetaType.Type.User
        else:
            try:
                field_type = QMetaType.Type(qtype)
            except (TypeError, ValueError):
                # Fallback en cas d'échec
                field_type = QMetaType.Type.UnknownType
                log(f"Unable to convert type {qtype} for field {name}, using UnknownType")

        if subtype is None:
            field_subtype = NATIVE_SUBTYPES.get(typename.lower(), QMetaType.Type.UnknownType)
        else:
            try:
                field_subtype = QMetaType.Type(subtype)
            except (TypeError, ValueError):
                field_subtype = QMetaType.Type.UnknownType
        return QgsField(name, field_type, typename, int(length), int(precision), comment, field_subtype)

    def read_features(self, fields):
        """Iterate over the (feature id, attributes, wkb) of a layer. Only complete features are returned."""
        ds = self._dstream
        v3 = self._version >= FORMAT_V3
        ok = QDataStream.Status.Ok
        attr = range(len(fields))
        geometry_fields = [i for i, field in enumerate(fields) if is_geometry_field(field)]
        # This loop is where nearly all the time goes when reading
        read_variant = ds.readQVariant
        while True:
            if not ds.readBool():
                # A truncated file ends the same way
                self.check_status("features")
                return
            fid = ds.readInt64() if v3 else None
            values_start = self._file.pos()
            values = [read_variant() for _i in attr]
            if ds.status() != ok:
                values = self.read_values_one_by_one(values_start, len(fields))
            wkb_size = ds.readUInt32()
            if wkb_size > self._size:
                raise MldataError(f"{self._filename} is truncated or corrupt (geometry size)")
            wkb = ds.readRawData(wkb_size) if wkb_size else None
            if ds.status() != ok:
                self.check_status("features")
            for i in geometry_fields:
                values[i] = decode_geometry_value(values[i])
            yield fid, values, wkb

    def read_values_one_by_one(self, start, count):
        """The slow way to read the attributes of a feature, for when Qt cannot read one of them.

        Values of some user types were written without any content (see EMPTY_USER_TYPES): Qt
        reads their type and refuses them, they are read as NULL here. Anything else is an error."""
        ds = self._dstream
        self._file.seek(start)
        ds.resetStatus()
        values = []
        for _i in range(count):
            position = self._file.pos()
            type_id = ds.readUInt32()
            ds.readInt8()  # is null
            if type_id in STREAM_USER_TYPE_IDS:
                name = bytes(ds.readBytes() or b"").rstrip(b"\0")
                if name in EMPTY_USER_TYPES:
                    self.check_status("features")
                    values.append(None)
                    continue
                self.check_status("features")
                raise MldataError(f"{self._filename}: values of type {name.decode(errors='replace')} cannot be read")
            self._file.seek(position)
            values.append(ds.readQVariant())
            self.check_status("features")
        return values

    def read_layer(self, layer):
        log("Reading layer " + layer.id())
        dp = layer.dataProvider()
        definition = self.read_definition()
        dp.deleteAttributes(dp.attributeIndexes())
        dp.addAttributes(definition["fields"])
        layer.updateFields()
        fields = dp.fields()

        batch = []
        placeholders = []
        next_fid = 1
        try:
            for fid, values, wkb in self.read_features(fields):
                # The memory provider numbers features by itself: placeholders fill the gaps in the ids
                if fid is not None and next_fid is not None:
                    if len(placeholders) + fid - next_fid > MAX_FID_PLACEHOLDERS:
                        log_warning(f"Layer {layer.name()}: feature ids are too sparse to be restored")
                        next_fid = None
                    else:
                        for gap in range(next_fid, fid):
                            placeholders.append(gap)
                            batch.append(QgsFeature(fields))
                        next_fid = fid + 1
                feat = QgsFeature(fields)
                feat.setAttributes(values)
                if wkb:
                    geom = QgsGeometry()
                    geom.fromWkb(wkb)
                    feat.setGeometry(geom)
                batch.append(feat)
                if len(batch) >= BATCH_SIZE:
                    self.flush(dp, batch)
                    batch = []
        finally:
            # Whatever precedes an error is worth having
            if batch:
                self.flush(dp, batch)
            if placeholders:
                dp.deleteFeatures(placeholders)
            if not layer.setSubsetString(definition["subset"]):
                log_warning(f"Cannot restore the filter of layer {layer.name()}: {definition['subset']}")
            layer.updateFields()
            layer.updateExtents()

    @staticmethod
    def flush(dp, batch):
        if not dp.addFeatures(batch, QgsFeatureSink.Flag.FastInsert)[0]:
            raise MldataError(f"Some features were refused by the memory provider: {dp.lastError()}")

    def skip_layer(self):
        # In v3, for_each_layer goes straight to the end of the layer
        if self._layer_end is None:
            for _feature in self.read_features(self.read_definition()["fields"]):
                pass


def layer_name_from_id(layer_id):
    """Layer ids are made of the layer name and of an uuid"""
    return re.sub(r"_[0-9a-fA-F]{8}(_[0-9a-fA-F]{4}){3}_[0-9a-fA-F]{12}$", "", layer_id)


def wkb_type_name(wkb_type):
    return QgsWkbTypes.displayString(Qgis.WkbType(int(wkb_type))) or "NoGeometry"
