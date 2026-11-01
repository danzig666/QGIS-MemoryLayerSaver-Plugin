# CHANGELOG

## [6.1.0] - 2026-11-01
### Data safety
- Writing is atomic: a failed save leaves the previous file untouched, and is reported
- Truncated or corrupt files are detected instead of silently leaving layers empty
- After a failed load, the file is only overwritten if the user confirms it, and is kept aside (`.failed-load.bak`)
- The previous data is kept in a `.bak` file (can be disabled), and the user is asked before empty layers replace features
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
- Projects without memory layers no longer get an empty attachment, and removed layers no longer stay in the file
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
