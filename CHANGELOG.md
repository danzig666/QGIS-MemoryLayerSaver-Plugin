# CHANGELOG

This is the changelog of a fork of [Memory Layer Saver](https://github.com/YoannQDQ/qgis-memory-layer-saver). Versions up to 6.0.1 are the original project,
and all issue numbers below refer to its issue tracker.

## [6.2.2] - 2026-11-09
- Files holding unset attribute values (`QgsUnsetAttributeValue`, left by some edits) can be loaded again. Qt cannot
  store these values, so the file only held their type: reading stopped there, and every layer after it in a v2 file
  stayed empty. They are now read as NULL, and saved as NULL

## [6.2.1] - 2026-11-01
GeoPackage export:
- Layers with a field named `fid` now export whatever it holds: duplicates, empty values, text or decimals. It stays
  a regular column with its values, next to a separate key column. Empty values were silently replaced
- Fields named `geom`, or with names which only differ by case, no longer make the export fail
- A failed export no longer leaves an incomplete table behind, and a layer is only switched to a complete one
- Choice between one GeoPackage and one file per layer. File names are made safe, and existing files are not replaced
- The dialog no longer outgrows the screen when there are errors: the complete errors are in its details

## [6.2.0] - 2026-11-01
- New plugin ID, `MemoryLayerSaverDanzig`. The plugin had the same ID as the original one, so QGIS merged the two
  into a single entry when plugin repositories offered both. Existing projects and settings keep working. To upgrade
  from an earlier version of this fork, uninstall it first
- While the original plugin is active, this fork does nothing and offers to disable it, as both work on the same data

## [6.1.1] - 2026-11-01
- Make clear this is a fork: name, description, authors, About dialog and README
- All links now point to the fork

## [6.1.0] - 2026-11-01
### Data safety
- Writing is atomic: a failed save leaves the previous file untouched, and is reported
- Truncated or corrupt files are detected instead of silently leaving layers empty
- After a failed load, the file is only overwritten if the user confirms it, and is kept aside (`.failed-load.bak`)
- The user is asked before empty layers replace features
- New setting to keep the previous data in a `.bak` file (off by default, as it doubles the space taken in the project)
- Fixes issue #13 - Fields of length or precision above 32767, and user type fields on Qt6, no longer break the save
- Saving is no longer affected by edit sessions: only committed data is saved. This fixes filtered layers in edit mode
  losing the hidden features, uncommitted deletions being saved, and uncommitted field deletions erasing their values
- Saving no longer resets the filter of layers, so it does not trigger repaints nor signals
- Datetimes with an offset or in UTC no longer move to another instant (v2 files keep the instant, v3 files also the offset)
- Values of geometry fields are kept (in v3 files)
### Change detection
- Filter changes, field renames, storage mode changes and most data provider edits now cause a rewrite
- New setting to rewrite on every save, for scripts changing only attribute values through the data provider
### File format
- New v3 format: per-layer size so that a damaged layer does not affect the others, int32 sizes, field subtypes,
  feature ids, datetime offsets, layer name, CRS and geometry type, plugin version. v2 stays the default: v3 is only
  used for layers which v2 cannot store, or on request. All formats stay readable
- Field subtypes of list fields are restored from v2 files
- Issue #14 - Warn when a separate .mldata file is more recent than the data embedded in the project, and tell
  when a file requires a newer plugin version
### Features
- Issue #18 - The warning of QGIS on exit is no longer disabled globally, so it stays on for what is not saved:
  temporary file outputs of algorithms, and memory layers excluded from saving. They are also pointed out on project save
- Convert temporary file layers to memory layers
- Issue #11 - Export all memory layers to a GeoPackage, and optionally switch the project to it
- Issue #15 - Restore layers from a .mldata or .bak file or a project archive, including damaged ones
- Progress in the task manager when saving or loading large layers
- Saving is about 1.4 times faster
### Fixes
- Fix the 'Report issues' link in the about dialog
- Unloading the plugin no longer leaves the instance connected to the project, nor the menu behind
- Projects without memory layers no longer get an empty attachment
- The project shrinks when layers leave it, and what older versions left behind is cleaned up on save. With backups
  on, data of deleted layers stays in the backup until the next rewrite
- The settings dialog has a parent and a title, and its typo is fixed

## [6.0.1] - 2026-03-03
- QGIS4 Compatibility

## [6.0.0] - 2025-06-14
- Qt6 Compatibility

## [5.0.2] - 2024-01-27
- Fixes issue #16 - Corrupted mldata when making layer permanent

## [5.0.1] - 2023-07-10
- Fixes issue #12 - Qgis.versionInt() unavailable for QGIS < 3.12

## [5.0.0] - 2023-06-07
- Do not rewrite the memory layers if they have not changed
- Fixes issue #9 - Embbed the mldata file in the project (requires QGIS >= 3.22)
- Add about dialog

## [4.0.4] - 2018-10-22
- Enhancement from fuzzysolutions to improve speed of saving

## [4.0.3] - 2018-09-12
- Disable scratch layer not saved warning when MemoryLayerSaver plugin is installed

## [4.0.2] - 2018-08-04
- Fixes issue #6 - exception thrown by redundant debugging code

## [4.0.1] - 2018-02-11
- Tidying up code

## [4.0] - 2018-02-03
- QGIS3 implementation
