# Env template: .NET 8 SDK (MonoGame-Engine / murder / MonoGame projects).
# Layered ON TOP of the security base (git-proxy splice etc.) — adds toolchain only.
# Registry egress the toolchain needs at task time: api.nuget.org (say to bot-pm:
#   "let the workers reach api.nuget.org").
#
# Build (context ./little-coder):
#   docker build -f docker/envs/dotnet8.Dockerfile -t little-coder-open-terminal:dotnet8 .
ARG BASE=little-coder-open-terminal:local
FROM ${BASE}

USER root

# .NET runtime prerequisites (Debian 13 base). The exact libicu major differs per Debian
# release — resolve it at build time instead of pinning.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl libgcc-s1 libgssapi-krb5-2 libssl3t64 libstdc++6 zlib1g \
 && apt-get install -y --no-install-recommends \
      "$(apt-cache search --names-only '^libicu[0-9]+$' | cut -d' ' -f1 | sort -V | tail -1)" \
 && rm -rf /var/lib/apt/lists/*

# Official distro-agnostic installer (packages.microsoft.com doesn't track new Debian
# releases promptly). Pinned to the 8.0 channel; SDK lands in /usr/share/dotnet.
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
 && bash /tmp/dotnet-install.sh --channel 8.0 --install-dir /usr/share/dotnet \
 && ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet \
 && rm /tmp/dotnet-install.sh \
 && dotnet --info

ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
