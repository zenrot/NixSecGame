{ lib, runCommand, python3, fetchurl, writeText }:

let
  catalog = builtins.fromJSON (builtins.readFile ./repository/catalog.json);
  archives = lib.mapAttrs (_: game: {
    inherit (game) sha256;
    archive = fetchurl { inherit (game) url sha256; };
  }) catalog;
  sources = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [ ./gameguard ./repository ];
  };
in
runCommand "gameguard-trusted-baselines" { nativeBuildInputs = [ python3 ]; } ''
  export PYTHONPATH="${sources}"
  python3 ${./scripts/build_baselines.py} ${writeText "gameguard-archives.json" (builtins.toJSON archives)} "$out"
''
