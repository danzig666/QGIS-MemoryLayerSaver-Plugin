# Memory Layer Saver

QGIS plugin that makes data in memory provider vector layers persistent, so that they are restored
when a project is closed and reopened.

## Where the data is stored

The data is stored in a `layers.mldata` file:

- by default embedded in the project: inside the `.qgz` archive, or in `<project>_attachments.zip` for a `.qgs` project;
- with "Embed mldata file" turned off in the settings, in a `<project file>.mldata` file beside the project file.

The previous content is kept in a `.bak` file in the same place, unless turned off in the settings. Nothing is kept for
layers made permanent, as their data is safe in their new file. Once no memory layer is left, these files are removed
from the project: at once if the layers were made permanent, on the save after that if they were deleted.

## What is saved

- Only committed data is saved. Edits still in an edit session are left out, and pointed out when saving the project.
- A layer can be excluded from saving by setting its `SaveMemoryProvider` custom property to `false`.
- Excluded layers, and outputs of algorithms stored in temporary files, are not saved. QGIS warns about them on exit,
  and the plugin on project save.
- The file is only rewritten if a change is detected. Scripts changing only attribute values through the data
  provider cannot be detected: for those, turn on "Rewrite the data on every project save" in the settings.

## Menu (Plugins > Memory layer saver)

- **Display memory layer information**: the layers saved with the project.
- **Export memory layers to GeoPackage**: to share a project or use it with QGIS Server, which does not load the plugin.
  The project can then be switched to the GeoPackage.
- **Convert temporary file layers to memory layers**: makes outputs of GDAL or GRASS algorithms persistent.
- **Restore layers from file**: adds layers from a `.mldata` or `.bak` file or from a project archive to the project,
  including what can be read from a damaged file.

## File formats

| Format | Written | Holds |
| ------ | ------- | ----- |
| v1 | never | Read only |
| v2 | by default | Readable by older plugin versions. Limited to 32767 for lengths and precisions. Datetimes are stored in local time, without their offset. No CRS, feature ids, or values of geometry fields |
| v3 | for layers which v2 cannot store, or if chosen in the settings | Requires plugin 6.1. Everything above, and a damaged layer does not affect the others |

Feature ids are only kept with v3: in v2, the memory provider renumbers features on load, so anything keyed on `$id`
changes after reopening the project.

## Packaging

Zip the contents of the MemoryLayerSaver directory to create the plugin, i.e.

    zip -r MemoryLayerSaver MemoryLayerSaver

## Tests

The tests need no display, and run with the Python of a QGIS installation:

    python-qgis tests/test_regressions.py <repository folder> <empty work folder>

## License

Distributed under the terms of the [`BSD` license](LICENSE).


*Copyright ©2009 Chris Crook (ccrook@linz.govt.nz)*
