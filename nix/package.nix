# Mast for NixOS: the published Linux build (the release's tarball), pointed at Nix's
# libraries instead of a normal Linux filesystem's. nix/release.json says which release;
# packaging/publish.sh rewrites it with every release, so `nix flake update` brings the
# newest one.
{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  makeWrapper,
  wrapGAppsHook3,
  addDriverRunpath,
  xdg-utils,
  alsa-lib,
  at-spi2-atk,
  at-spi2-core,
  cairo,
  cups,
  dbus,
  expat,
  glib,
  gtk3,
  libdrm,
  libgbm,
  libGL,
  libnotify,
  libpulseaudio,
  libva,
  libxkbcommon,
  nspr,
  nss,
  pango,
  pipewire,
  systemd,
  vulkan-loader,
  wayland,
  libx11,
  libxcb,
  libxcomposite,
  libxdamage,
  libxext,
  libxfixes,
  libxrandr,
  libxshmfence,
}:

let
  release = lib.importJSON ./release.json;

  # Linked by the browser, or opened by it while it runs (GPU, video decoding, audio,
  # Wayland, notifications), so they're on its library path either way.
  libs = [
    alsa-lib
    at-spi2-atk
    at-spi2-core
    cairo
    cups
    dbus
    expat
    glib
    gtk3
    libdrm
    libgbm
    libGL
    libnotify
    libpulseaudio
    libva
    libxkbcommon
    nspr
    nss
    pango
    pipewire
    systemd
    vulkan-loader
    wayland
    libx11
    libxcb
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxrandr
    libxshmfence
  ];
in
stdenv.mkDerivation {
  pname = "mast";
  inherit (release) version;

  src = fetchurl { inherit (release) url hash; };

  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
    wrapGAppsHook3
  ];
  buildInputs = libs;

  dontUnpack = true;
  dontBuild = true;
  # One wrapper, made below, with GTK's settings and the library path together.
  dontWrapGApps = true;

  installPhase = ''
    runHook preInstall

    mkdir -p $out/lib/mast
    tar -xJf $src -C $out/lib/mast
    cd $out/lib/mast

    # The setuid sandbox helper can't be setuid in the Nix store; NixOS allows the
    # user-namespace sandbox Chromium uses instead.
    rm chrome-sandbox
    # Qt dialogs aren't used (Mast's file dialogs are GTK's); they'd need Qt here.
    rm libqt5_shim.so* libqt6_shim.so*
  '' + lib.optionalString (lib.versionOlder release.version "0.1.1") ''
    # 0.1.0's update check doesn't know Nix installs yet and would offer install.sh.
    # Without this file it doesn't run (Settings > About then shows Chromium's version).
    rm mast-version
  '' + ''

    install -Dm644 mast.desktop $out/share/applications/mast.desktop
    substituteInPlace $out/share/applications/mast.desktop \
      --replace-fail /opt/mast/chrome $out/bin/mast
    for s in 24 48 64 128 256; do
      install -Dm644 product_logo_$s.png $out/share/icons/hicolor/''${s}x$s/apps/mast.png
    done

    runHook postInstall
  '';

  postFixup = ''
    makeWrapper $out/lib/mast/chrome $out/bin/mast \
      "''${gappsWrapperArgs[@]}" \
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath libs}:${addDriverRunpath.driverLink}/lib \
      --prefix PATH : ${lib.makeBinPath [ xdg-utils ]}
  '';

  meta = {
    description = "A browser for deep work that lets you block distractions for good";
    homepage = "https://github.com/mast-browser/mast";
    changelog = "https://github.com/mast-browser/mast/releases/tag/v${release.version}";
    license = lib.licenses.gpl3Only;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
    mainProgram = "mast";
  };
}
