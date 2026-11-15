{ lib, stdenvNoCC, makeWrapper, python3, scummvm, strace, binutils, pax-utils }:

stdenvNoCC.mkDerivation {
  pname = "gameguard";
  version = "0.1.0";

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [ ./gameguard ./repository ./tests ];
  };

  nativeBuildInputs = [ makeWrapper python3 ];
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
    makeWrapper "${python3}/bin/python3" "$out/bin/gameguard" \
      --set PYTHONPATH "$out/lib" \
      --add-flags "-m gameguard" \
      --prefix PATH : "${lib.makeBinPath [ scummvm strace binutils pax-utils ]}"
    runHook postInstall
  '';

  meta.platforms = [ "aarch64-linux" ];
}
