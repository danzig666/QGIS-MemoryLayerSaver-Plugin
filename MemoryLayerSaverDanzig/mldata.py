"""Constants and helpers shared by the mldata reader and writer.

File layout, after the MAGIC bytes and the format version (int32):

v1  layers: id, fields (int16 sizes), features
v2  layers: id, subset string, fields (int16 sizes), features
v3  plugin version, layer count, then per layer: id, payload size (int64), and a payload made of
    subset string, name, CRS, WKB type, fields (int32 sizes, subtype), features (with feature id).
    Written with a newer stream version, which keeps datetime offsets.

v1 and v2 use the Qt_4_5 stream version.
"""

from qgis.core import QgsGeometry, QgsReferencedGeometry
from qgis.PyQt.QtCore import QDataStream, QMetaType

MAGIC = b"QGis.MemoryLayerData"
FORMAT_V2 = 2
FORMAT_V3 = 3
READABLE_FORMATS = (1, 2, 3)

STREAM_VERSION_LEGACY = QDataStream.Version.Qt_4_5
STREAM_VERSION_V3 = QDataStream.Version.Qt_5_15

INT16_MIN = -32768
INT16_MAX = 32767


# Values of these user types were written without any content, as Qt has no stream operator for them.
# QgsUnsetAttributeValue (a default value not resolved yet) ends up in the attributes of some features.
EMPTY_USER_TYPES = (b"QgsUnsetAttributeValue",)
# The type ids which announce a user type, followed by its name: Qt 4 streams, then Qt 5 streams
STREAM_USER_TYPE_IDS = (127, 1024)

try:
    from qgis.core import QgsUnsetAttributeValue
except ImportError:  # QGIS < 3.28
    QgsUnsetAttributeValue = None


class MldataError(ValueError):
    """An mldata file cannot be read, or not entirely"""


def fits_int16(value):
    return INT16_MIN <= int(value) <= INT16_MAX


def is_geometry_field(field):
    """Whether a field holds geometries as values. These are user types, whose id depends on Qt"""
    return field.typeName().lower() == "geometry"


def needs_v3(layer):
    """Whether a layer holds something v2 cannot store at all: the older plugin versions
    could not save these layers either, so moving them to v3 breaks nothing that worked."""
    for field in layer.dataProvider().fields():
        if is_geometry_field(field):
            return True
        if not (fits_int16(field.length()) and fits_int16(field.precision()) and fits_int16(field.type())):
            return True
    return False


def is_unset_value(value):
    return QgsUnsetAttributeValue is not None and isinstance(value, QgsUnsetAttributeValue)


def encode_geometry_value(value):
    if isinstance(value, QgsReferencedGeometry):
        return {"wkb": value.asWkb(), "crs": value.crs().toWkt()}
    if isinstance(value, QgsGeometry):
        return {"wkb": value.asWkb(), "crs": ""}
    return value


def decode_geometry_value(value):
    if not isinstance(value, dict) or "wkb" not in value:
        return value
    from qgis.core import QgsCoordinateReferenceSystem

    geometry = QgsGeometry()
    geometry.fromWkb(bytes(value["wkb"]))
    return QgsReferencedGeometry(geometry, QgsCoordinateReferenceSystem.fromWkt(value.get("crs", "")))


# The type names of the memory provider which imply a subtype, used to restore it from v1 and v2 files
NATIVE_SUBTYPES = {
    "integerlist": QMetaType.Type.Int,
    "integer64list": QMetaType.Type.LongLong,
    "doublelist": QMetaType.Type.Double,
    "stringlist": QMetaType.Type.QString,
    "map": QMetaType.Type.QString,
}
