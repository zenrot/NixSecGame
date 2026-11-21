{ lib, stdenvNoCC, makeWrapper, python3, scummvm, strace, binutils, pax-utils, systemd, bpftools, dbus }:

let
  python = python3.withPackages (ps: [ ps.cyclonedx-python-lib ps.jsonschema ]);
in
stdenvNoCC.mkDerivation {
  pname = "gameguard";
  version = "0.1.0";

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [ ./gameguard ./repository ./tests ./scripts/network_helper.py ];
  };

  nativeBuildInputs = [ makeWrapper python ];
  dontBuild = true;
  doCheck = true;

  checkPhase = ''
    runHook preCheck
    python3 -B -m unittest discover -s tests
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib" "$out/bin"
    cp -r gameguard repository "$out/lib/"
    cp scripts/network_helper.py "$out/lib/"
    substituteInPlace "$out/lib/gameguard/network.py" \
      --replace-fail '@systemd_run@' '${systemd}/bin/systemd-run' \
      --replace-fail '@systemctl@' '${systemd}/bin/systemctl' \
      --replace-fail '@dbus_run_session@' '${dbus}/bin/dbus-run-session' \
      --replace-fail '@bpftool@' '${bpftools}/bin/bpftool' \
      --replace-fail '@package_helper@' "$out/bin/gameguard-network"
    makeWrapper "${python}/bin/python3" "$out/bin/gameguard-network" \
      --add-flags "-I $out/lib/network_helper.py"
    makeWrapper "${python}/bin/python3" "$out/bin/gameguard" \
      --set PYTHONPATH "$out/lib" \
      --add-flags "-m gameguard" \
      --prefix PATH : "${lib.makeBinPath [ scummvm strace binutils pax-utils ]}"
    runHook postInstall
  '';

  meta.platforms = [ "aarch64-linux" ];
}
