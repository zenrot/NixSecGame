{
  description = "NixSecGame for an ARM64 UTM guest";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    machine = {
      url = "path:./hosts/utm/example";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, machine, ... }: {
    nixosConfigurations.utm = nixpkgs.lib.nixosSystem {
      system = "aarch64-linux";
      modules = [
        { system.configurationRevision = self.rev or null; }
        ./hosts/utm/configuration.nix
        (machine + "/default.nix")
      ];
    };
  };
}
