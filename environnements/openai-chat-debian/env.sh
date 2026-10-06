# Source from Bash; all native libraries and GI typelibs stay within this project.
GTK4_POC_BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
GTK4_POC_ROOT="$GTK4_POC_BASE/runtime/root"
export PATH="$GTK4_POC_ROOT/usr/bin:$PATH"
export LD_LIBRARY_PATH="$GTK4_POC_ROOT/usr/lib/x86_64-linux-gnu:$GTK4_POC_ROOT/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export GI_TYPELIB_PATH="$GTK4_POC_ROOT/usr/lib/x86_64-linux-gnu/girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
export PYTHONPATH="$GTK4_POC_ROOT/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONNOUSERSITE=1
export XDG_DATA_DIRS="$GTK4_POC_ROOT/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export XDG_CACHE_HOME="$GTK4_POC_BASE/runtime/cache"
export XDG_CONFIG_HOME="$GTK4_POC_BASE/runtime/config"
export XDG_RUNTIME_DIR="$GTK4_POC_BASE/runtime/xdg"
export TMPDIR="$GTK4_POC_BASE/runtime/tmp"
export GDK_BACKEND=x11
export GSK_RENDERER=cairo
export GDK_SCALE=1
export GTK_A11Y=none
mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_RUNTIME_DIR" "$TMPDIR"
chmod 700 "$XDG_RUNTIME_DIR"
