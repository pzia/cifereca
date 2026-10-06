# Source from Bash. No system-wide GTK installation is performed.
CIFERECA_WORK_BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CIFERECA_WORK_ROOT="$CIFERECA_WORK_BASE/runtime/root"
export PATH="$CIFERECA_WORK_ROOT/usr/bin:$PATH"
export LD_LIBRARY_PATH="$CIFERECA_WORK_ROOT/usr/lib/x86_64-linux-gnu:$CIFERECA_WORK_ROOT/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export GI_TYPELIB_PATH="$CIFERECA_WORK_ROOT/usr/lib/x86_64-linux-gnu/girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
export PYTHONPATH="$CIFERECA_WORK_ROOT/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
export XDG_DATA_DIRS="$CIFERECA_WORK_ROOT/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export XDG_CACHE_HOME="$CIFERECA_WORK_BASE/runtime/cache"
export XDG_CONFIG_HOME="$CIFERECA_WORK_BASE/runtime/config"
export XDG_DATA_HOME="$CIFERECA_WORK_BASE/runtime/data"
export XDG_STATE_HOME="$CIFERECA_WORK_BASE/runtime/state"
export XDG_RUNTIME_DIR="$CIFERECA_WORK_BASE/runtime/xdg"
export TMPDIR="$CIFERECA_WORK_BASE/runtime/tmp"
export GDK_BACKEND=x11
export GSK_RENDERER=cairo
export GDK_DEBUG=gl-disable
export GDK_SCALE=1
export GTK_A11Y=none
mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_RUNTIME_DIR" "$TMPDIR" "$CIFERECA_WORK_BASE/runtime/home/EBooks"
chmod 700 "$XDG_RUNTIME_DIR"
