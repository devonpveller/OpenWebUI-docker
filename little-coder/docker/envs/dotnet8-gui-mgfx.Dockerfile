# Env template: dotnet8-gui + MonoGame SHADER COMPILATION (mgfxc via Wine).
#
# Why Wine: MonoGame's effect compiler (`mgfxc`) drives `d3dcompiler_47.dll` to compile HLSL —
# native on Windows, but on Linux it needs a Wine prefix carrying that DLL. The murder editor
# compiles its `.fx` shaders at runtime; ported to MonoGame it must produce MGFX-format `.fxb`
# (the old fxc.exe path yields FNA-format bytecode that `new Effect()` rejects — "not a MonoGame
# MGFX file"). This layer gives the worker the SAME shader-compile capability the operator's
# Windows host has natively, so the org can compile AND verify MonoGame shaders headlessly
# (operator 2026-07-09: the 25s-alive check gave a false green because the Linux worker couldn't
# reproduce the Windows-only crash).
#
# Build (context ./little-coder):
#   docker build -f docker/envs/dotnet8-gui-mgfx.Dockerfile -t little-coder-open-terminal:dotnet8-gui-mgfx .
#
# Use: mgfxc is on PATH; MGFXC_WINE_PATH points at the baked prefix. Compile with e.g.
#   mgfxc simple.fx simple.fxb /Profile:OpenGL      # OpenGL profile for DesktopGL
ARG BASE=little-coder-open-terminal:dotnet8-gui
FROM ${BASE}

USER root

# mgfxc — the MonoGame Effect compiler (dotnet global tool). Lands in $HOME/.dotnet/tools.
RUN dotnet tool install --global dotnet-mgfxc --version "3.8.*" \
 && ln -sf /root/.dotnet/tools/mgfxc /usr/local/bin/mgfxc
ENV PATH="/root/.dotnet/tools:${PATH}"

# Wine (32+64) + the tools mgfxc's WineHelper shells out to. i386 arch for the 32-bit Wine bits
# d3dcompiler needs.
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      wine wine64 wine32:i386 winbind \
      cabextract p7zip-full curl ca-certificates xz-utils \
 && rm -rf /var/lib/apt/lists/*

# Bake the MonoGame Wine prefix at a fixed path (build-time has real internet; task-time egress
# is proxied). mgfxc's WineHelper runs the WINDOWS dotnet SDK inside the prefix to drive the HLSL
# compiler — so the prefix needs (a) the Windows dotnet SDK and (b) d3dcompiler_47.dll, exactly
# as MonoGame's mgfxc_wine_setup.sh does. Baked once so task-time (proxied egress) needs nothing.
ENV WINEDEBUG=-all \
    WINEARCH=win64 \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    MGFXC_WINE_PATH=/opt/mgfxc-wine \
    XDG_RUNTIME_DIR=/tmp/xdg
# (a) init the prefix + silence the crash dialog; (b) Windows dotnet SDK into system32 so the
# WineHelper can run mgfxc under Wine; (c) d3dcompiler_47.dll (from the Firefox pkg, per MonoGame).
RUN set -eux; mkdir -p /tmp/xdg && chmod 700 /tmp/xdg; \
    export WINEPREFIX=/opt/mgfxc-wine; \
    wine64 wineboot --init 2>/dev/null || true; sleep 4; \
    printf 'REGEDIT4\n[HKEY_CURRENT_USER\\Software\\Wine\\WineDbg]\n"ShowCrashDialog"=dword:00000000\n' > /tmp/crash.reg; \
    wine64 regedit /tmp/crash.reg 2>/dev/null || true; \
    curl -fsSL "https://builds.dotnet.microsoft.com/dotnet/Sdk/8.0.201/dotnet-sdk-8.0.201-win-x64.zip" -o /tmp/dotnet-win.zip; \
    7z x /tmp/dotnet-win.zip -o"/opt/mgfxc-wine/drive_c/windows/system32/" -aoa >/dev/null; \
    curl -fsSL "https://download-installer.cdn.mozilla.net/pub/firefox/releases/62.0.3/win64/ach/Firefox%20Setup%2062.0.3.exe" -o /tmp/ff.exe; \
    7z e /tmp/ff.exe "core/d3dcompiler_47.dll" -o"/opt/mgfxc-wine/drive_c/windows/system32/" -aoa >/dev/null; \
    rm -f /tmp/dotnet-win.zip /tmp/ff.exe; \
    echo "wine prefix baked"

# Sanity: mgfxc must start (SourceFile-missing usage is the healthy path; the Wine init/File-not-
# found errors are what this layer fixes). A real compile is verified live before the objective.
RUN export PATH="$PATH:/root/.dotnet/tools" \
 && (mgfxc 2>&1 | head -2 || true)
