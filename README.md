# NixSecGame

Системная конфигурация NixOS 26.05 для ARM64-виртуальной машины в UTM. `flake.lock` фиксирует ревизию Nixpkgs для сборки на другой машине.

## Подготовка ВМ

Установите NixOS на ARM64 с UEFI и создайте пользователя `user`. Если Git ещё не установлен, откройте оболочку с ним командой `nix --extra-experimental-features "nix-command flakes" shell github:NixOS/nixpkgs/nixos-26.05#git`.

```sh
git clone https://github.com/zenrot/NixSecGame.git
cd NixSecGame
sudo install -d -m 0755 /etc/nixos/nixsecgame-machine
sudo install -m 0644 /etc/nixos/hardware-configuration.nix /etc/nixos/nixsecgame-machine/
sudo tee /etc/nixos/nixsecgame-machine/default.nix >/dev/null <<'NIX'
{ ... }:
{
  imports = [ ./hardware-configuration.nix ];
  users.users.user.openssh.authorizedKeys.keyFiles = [ ./authorized_keys ];
}
NIX
```

Запишите публичные ключи для входа под `user` в `/etc/nixos/nixsecgame-machine/authorized_keys`, по одному на строку. На действующей ВМ NixOS с ключами из системной конфигурации их можно скопировать из `/etc/ssh/authorized_keys.d/user`. При первой настройке нужны публичные ключи с компьютеров, с которых вы будете подключаться. Файлы этого каталога Nix читает при сборке и копирует в Nix store, поэтому храните там только параметры оборудования и публичные ключи.

## Сборка и активация

Из каталога репозитория выполните сборку под обычным пользователем и активируйте её через `sudo`:

```sh
nix --extra-experimental-features "nix-command flakes" build \
  .#nixosConfigurations.utm.config.system.build.toplevel \
  --override-input machine path:/etc/nixos/nixsecgame-machine \
  --no-write-lock-file
sudo nixos-rebuild switch --store-path "$(readlink -f result)"
```

Для отката выполните `sudo nixos-rebuild switch --rollback` или выберите предыдущую генерацию в меню UEFI-загрузчика.

## Обмен изменениями

На ВМ репозиторий находится в `~/NixSecGame`. Сначала получите изменения командой `git pull --ff-only`, затем повторите сборку и активацию. Общие параметры находятся в `hosts/utm/configuration.nix`. Их можно менять в своей копии репозитория и отправлять на GitHub через коммит или pull request.

Для обновления пакетов в ветке NixOS 26.05 выполните `nix flake update nixpkgs`, соберите конфигурацию и добавьте обновлённый `flake.lock` в коммит.
