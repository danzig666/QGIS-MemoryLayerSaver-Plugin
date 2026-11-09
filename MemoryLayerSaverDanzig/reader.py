import re

from qgis.core import Qgis, QgsFeature, QgsFeatureSink, QgsField, QgsGeometry, QgsWkbTypes
from qgis.PyQt.QtCore import QDataStream, QFile, QIODevice, QMetaType

from .mldata import (
    EMPTY_USER_TYPES,
    FORMAT_V3,
    MAGIC,
    NATIVE_SUBTYPES,
    READABLE_FORMATS,
    STREAM_TYPE_HASH,
    STREAM_TYPE_LIST,
    STREAM_TYPE_MAP,
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
        """The slow way to read the attributes of a feature, for when Qt cannot read one of them."""
        self._file.seek(start)
        self._dstream.resetStatus()
        return [self.read_value_slowly() for _i in range(count)]

    def read_value_slowly(self):
        """Values of some user types were written without any content (see EMPTY_USER_TYPES): Qt
        reads their type and refuses them, they are read as NULL here, also inside lists and maps.
        Anything else Qt cannot read is an error."""
        ds = self._dstream
        position = self._file.pos()
        type_id = ds.readUInt32()
        ds.readInt8()  # is null
        self.check_status("features")
        if type_id in STREAM_USER_TYPE_IDS:
            name = bytes(ds.readBytes() or b"").rstrip(b"\0")
            self.check_status("features")
            if name in EMPTY_USER_TYPES:
                return None
            raise MldataError(f"{self._filename}: values of type {name.decode(errors='replace')} cannot be read")
        if type_id in (STREAM_TYPE_LIST, STREAM_TYPE_MAP, STREAM_TYPE_HASH):
            count = ds.readUInt32()
            self.check_status("features")
            if count > self._size:
                raise MldataError(f"{self._filename} is truncated or corrupt (list size)")
            if type_id == STREAM_TYPE_LIST:
                return [self.read_value_slowly() for _i in range(count)]
            result = {}
            for _i in range(count):
                key = ds.readQString()
                result[key] = self.read_value_slowly()
            return result
        self._file.seek(position)
        value = ds.readQVariant()
        self.check_status("features")
        return value

    def read_layer(self, layer):
        log("Reading layer " + layer.id())
        dp = layer.dataProvider()
        definition = self.read_definition()
        dp.deleteAttributes(dp.attributeIndexes())
        dp.addAttributes(definition["fields"])
        layer.updateFields()
        fields = dp.fields()

        # Entries are (feature, is a placeholder)
        batch = []
        placeholder_ids = []
        self._problems = []
        pending = 0
        next_fid = 1
        try:
            for fid, values, wkb in self.read_features(fields):
                # The memory provider numbers features by itself: placeholders fill the gaps in the ids
                if fid is not None and next_fid is not None:
                    if pending + fid - next_fid > MAX_FID_PLACEHOLDERS:
                        log_warning(f"Layer {layer.name()}: feature ids are too sparse to be restored")
                        next_fid = None
                    else:
                        for _gap in range(next_fid, fid):
                            pending += 1
                            batch.append((QgsFeature(fields), True))
                        next_fid = fid + 1
                feat = QgsFeature(fields)
                feat.setAttributes(values)
                if wkb:
                    geom = QgsGeometry()
                    geom.fromWkb(wkb)
                    feat.setGeometry(geom)
                batch.append((feat, False))
                if len(batch) >= BATCH_SIZE:
                    placeholder_ids += self.flush(dp, batch, fields)
                    batch = []
        finally:
            # Whatever precedes an error is worth having
            if batch:
                placeholder_ids += self.flush(dp, batch, fields)
            if placeholder_ids:
                dp.deleteFeatures(placeholder_ids)
            if not layer.setSubsetString(definition["subset"]):
                log_warning(f"Cannot restore the filter of layer {layer.name()}: {definition['subset']}")
            layer.updateFields()
            layer.updateExtents()
            if self._problems:
                # Not an error of the stream: the next layers are still read, and the load is reported as failed
                shown = "; ".join(self._problems[:5])
                more = f" (and {len(self._problems) - 5} more)" if len(self._problems) > 5 else ""
                self.errors.append(
                    (layer.id(), f"{len(self._problems)} features needed changes to load: {shown}{more}")
                )
                log_warning(f"Layer {layer.name()}: {self.errors[-1][1]}")

    def flush(self, dp, batch, fields):
        """Add a batch of features, and return the ids the placeholders got.

        When the provider refuses a feature, it keeps those before it and drops the others without
        numbering them: they are added again one by one, the refused ones with their incompatible
        values made NULL, as losing a value is better than losing the feature."""
        before = dp.featureCount()
        ok, added = dp.addFeatures([feat for feat, _placeholder in batch], QgsFeatureSink.Flag.FastInsert)
        if ok:
            return [feat.id() for feat, (_f, placeholder) in zip(added, batch) if placeholder]
        kept = dp.featureCount() - before
        if not 0 <= kept <= len(batch):
            raise MldataError(f"Some features were refused by the memory provider: {dp.lastError()}")
        ids = [feat.id() for feat, (_f, placeholder) in zip(added[:kept], batch[:kept]) if placeholder]
        for feat, placeholder in batch[kept:]:
            ok, added = dp.addFeatures([feat], QgsFeatureSink.Flag.FastInsert)
            if not ok:
                error = dp.lastError()
                feat = self.compatible_feature(feat, fields)
                ok, added = dp.addFeatures([feat], QgsFeatureSink.Flag.FastInsert)
                if not ok:
                    feat.setAttributes([None] * len(fields))
                    ok, added = dp.addFeatures([feat], QgsFeatureSink.Flag.FastInsert)
                    error += " (all its values were dropped)"
                self._problems.append(error)
                if not ok:
                    raise MldataError(f"A feature was refused by the memory provider: {dp.lastError()}")
            if placeholder:
                ids.append(added[0].id())
        return ids

    @staticmethod
    def compatible_feature(feat, fields):
        values = []
        for field, value in zip(fields, feat.attributes()):
            try:
                converted = field.convertCompatible(value)
            except (TypeError, ValueError):
                converted = None
            values.append(converted)
        result = QgsFeature(feat)
        result.setAttributes(values)
        return result

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
