# Env template: .NET 8 SDK + HEADLESS GUI RUNTIME (MonoGame DesktopGL apps — the murder editor).
# Layered ON TOP of the dotnet8 env: adds a virtual X server (Xvfb) + software OpenGL (Mesa
# llvmpipe) + SDL/OpenAL system deps, so a DesktopGL app can LAUNCH and render frames inside the
# container — replicating the host's "can actually run the editor" environment for runtime
# verification (operator 2026-07-09: "launching the editor is where the cascading errors occur").
#
# Usage in checks/tasks:  xvfb-run -a timeout 25 dotnet run --project src/Murder.Editor
#   (exit 124 = still running when the timeout hit = LAUNCHED; early exit = a runtime failure)
#
# Build (context ./little-coder):
#   docker build -f docker/envs/dotnet8-gui.Dockerfile -t little-coder-open-terminal:dotnet8-gui .
ARG BASE=little-coder-open-terminal:dotnet8
FROM ${BASE}

USER root

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xvfb xauth \
      libgl1 libglu1-mesa libgl1-mesa-dri libegl1 \
      libsdl2-2.0-0 libopenal1 \
      libx11-6 libxi6 libxrandr2 libxcursor1 libxinerama1 libxss1 \
      fontconfig \
 && rm -rf /var/lib/apt/lists/*

# Software GL (no GPU in the container) + quiet SDL audio in headless runs.
ENV LIBGL_ALWAYS_SOFTWARE=1 \
    GALLIUM_DRIVER=llvmpipe \
    SDL_AUDIODRIVER=dummy
