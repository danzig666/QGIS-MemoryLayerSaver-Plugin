# Memory Layer Saver (Danzig fork)

This is a fork of [Memory Layer Saver](https://github.com/YoannQDQ/qgis-memory-layer-saver) by Chris Crook and Yoann Quenach de Quivillic.
Please report problems with this version [here](https://github.com/danzig666/QGIS-MemoryLayerSaver-Plugin/issues), not to the original authors.

[![Tests](https://github.com/danzig666/QGIS-MemoryLayerSaver-Plugin/actions/workflows/tests.yml/badge.svg)](https://github.com/danzig666/QGIS-MemoryLayerSaver-Plugin/actions/workflows/tests.yml)

## What this fork changes

Compared to the original 6.0.1. Issue numbers refer to the [original project's tracker](https://github.com/YoannQDQ/qgis-memory-layer-saver/issues).

### Fixes

Data loss:

- A failed save no longer damages the existing file: writing is atomic, and the failure is reported.
- A truncated or corrupt file is reported on load, instead of silently leaving layers empty.
- After a failed load, saving no longer replaces the good data with what was loaded, unless you confirm it. This is a likely cause of the layers lost in #15.
- A filtered layer in edit mode no longer loses its hidden features when saved.
- Uncommitted edits are no longer saved: rolled back deletions no longer stay deleted, and an uncommitted field deletion no longer erases that field's values.
- Fields of length or precision above 32767 (#13), and geometry fields on Qt6, no longer break the save.
- Datetimes with an offset or in UTC no longer move to another instant when reopened.
- Field subtypes of list fields are no longer lost.

Changes that were not saved:

- Filter changes, field renames and most edits made through the data provider now cause a rewrite.
- Switching the storage mode no longer leaves an outdated file in use.

Other:

- The project shrinks when memory layers leave it: the data of layers made permanent or deleted no longer stays in it forever, and what older versions left behind is cleaned up on save.
- Projects without memory layers no longer get an empty attachment.
- Saving no longer repaints filtered layers.
- Fixed the "Report issues" link, the settings dialog opening without a parent and its typo, and the plugin leaving its menu and a connection behind when unloaded.
- Saving is about 1.4 times faster.

### New features

- **Export to GeoPackage:** export all memory layers and optionally switch the project to the GeoPackage, for instance to use it with QGIS Server (#11).
- **Restore from file:** restore layers from a `.mldata` or `.bak` file or a project archive, including what can be read from a damaged one (#15).
- **Convert temporary file layers** such as GDAL or GRASS outputs to memory layers (#18).
- **QGIS's warning about temporary layers** is no longer disabled globally: it stays on for what the plugin does not save (#18).
- **Warning for a newer separate `.mldata` file,** which is likely the work of an older plugin version (#14).
- **v3 file format, optional:** a damaged layer does not affect the others, and it keeps datetime offsets, feature ids and values of geometry fields. v2 stays the default.
- **Optional `.bak` backup** of the previous data, off by default.
- **"Rewrite on every save" setting,** for scripts changing only attribute values through the data provider.
- **Progress in the task manager** when saving or loading large layers.
- **Automated tests** on QGIS 3.40, 3.44 and 4.2.

Upgrading from the original plugin is safe: existing files are read as they are, and new projects stay in the v2 format older versions can open, unless a layer needs v3.

QGIS plugin that makes data in memory provider vector layers persistent, so that they are restored
when a project is closed and reopened.

## Where the data is stored

The data is stored in a `layers.mldata` file:

- by default embedded in the project: inside the `.qgz` archive, or in `<project>_attachments.zip` for a `.qgs` project;
- with "Embed mldata file" turned off in the settings, in a `<project file>.mldata` file beside the project file.

With "Keep a backup of the previous data" turned on in the settings (off by default), the previous content is kept in a
`.bak` file in the same place. Nothing is kept for layers made permanent, as their data is safe in their new file. Once
no memory layer is left, these files are removed from the project: at once if the layers were made permanent, on the
save after that if they were deleted. Whatever this setting, overwriting a file which could not be loaded keeps it in a
`.failed-load.bak` file.

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
