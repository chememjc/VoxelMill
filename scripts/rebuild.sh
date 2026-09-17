#!/bin/sh
# Rebuild and atomically install the extension. Never truncate the library inode:
# a running slicer can still have its executable pages mapped (SIGBUS otherwise).
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TASK_ROOT"
TASK_BUILD=$(.venv/bin/python -c 'import sysconfig; print("build/cp" + str(__import__("sys").version_info.major) + str(__import__("sys").version_info.minor) + "-cp" + str(__import__("sys").version_info.major) + str(__import__("sys").version_info.minor) + "-" + sysconfig.get_platform().replace("-", "_"))')
TASK_SITE=$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"] + "/voxelmill")')
TASK_SUFFIX=$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')
ninja -C "$TASK_BUILD" "$@"
TASK_TEMP=$(mktemp "$TASK_SITE/.native-install.XXXXXX")
trap 'rm -f "$TASK_TEMP"' EXIT HUP INT TERM
cp "$TASK_BUILD/_native$TASK_SUFFIX" "$TASK_TEMP"
chmod 755 "$TASK_TEMP"
mv -f "$TASK_TEMP" "$TASK_SITE/_native$TASK_SUFFIX"
