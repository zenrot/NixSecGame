{
  description = "NixSecGame for an ARM64 UTM guest";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    machine = {
      url = "path:./hosts/utm/example";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, machine, ... }:
    let
      system = "aarch64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      gameguard = pkgs.callPackage ./package.nix { };
    in {
      packages.${system}.default = gameguard;

      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [ (python3.withPackages (ps: [ ps.cyclonedx-python-lib ps.jsonschema ])) pegasus-frontend scummvm strace binutils pax-utils ];
      };

      nixosConfigurations.utm = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          { system.configurationRevision = self.rev or null; }
          { environment.systemPackages = [ gameguard ]; }
          ./hosts/utm/configuration.nix
          (machine + "/default.nix")
        ];
      };
    };
}
