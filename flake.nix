{
  description = "Mast, a browser for deep work that lets you block distractions for good";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      # The published release (nix/release.json), ready to install:
      #   environment.systemPackages = [ inputs.mast.packages.x86_64-linux.default ];
      packages.${system} = rec {
        mast = pkgs.callPackage ./nix/package.nix { };
        default = mast;
      };

      # Or as pkgs.mast, built against your own nixpkgs.
      overlays.default = final: prev: { mast = final.callPackage ./nix/package.nix { }; };
    };
}
