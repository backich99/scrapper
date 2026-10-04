# setup/ — Provisión de la Raspberry Pi

Scripts para dejar lista la Raspberry Pi 4 (Raspberry Pi OS **Bookworm**, red por NetworkManager) antes de instalar la aplicación.

Datos de referencia:

- **IP:** `10.10.0.246` (ya reservada por DHCP en el router; estos scripts añaden un fallback estático a la misma IP)
- **Usuario:** `karlos`
- **Gateway:** `10.10.0.1` · **DNS:** `10.10.0.210`

> Ninguna contraseña se guarda en el repositorio. El acceso es por **clave SSH**.

## Orden de ejecución

Copia el proyecto a la Raspberry (o clónalo) y ejecuta, en este orden:

```bash
# 1. Actualizar el sistema
sudo bash setup/01-update.sh
sudo reboot            # recomendado

# 2. Habilitar SSH (el copiado de la clave se hace desde TU equipo, ver el propio script)
sudo bash setup/02-ssh-key.sh
#    ...desde tu equipo: ssh-copy-id karlos@10.10.0.246  + alias 'tvbox' en ~/.ssh/config
#    cuando confirmes acceso por clave:
sudo bash setup/02-ssh-key.sh --harden

# 3. sudo sin contraseña para karlos
sudo bash setup/03-sudo-nopasswd.sh

# 4. Fallback de IP estática (misma IP que la reserva DHCP)
sudo bash setup/04-static-ip.sh

# 5. Entorno mínimo + kiosk (Xorg, mpv, venv Python, autostart de la UI)
sudo bash setup/05-kiosk.sh

# 6. (modelo nuevo) Desactivar el scraper horario si estaba instalado
sudo bash setup/06-scraper-timer.sh
```

## Qué hace cada script

| Script                    | Acción                                                                          |
|---------------------------|---------------------------------------------------------------------------------|
| `01-update.sh`            | `apt update` + `full-upgrade` + `autoremove`.                                   |
| `02-ssh-key.sh`           | Habilita SSH. Con `--harden` deshabilita el login por contraseña (solo clave).  |
| `03-sudo-nopasswd.sh`     | Crea `/etc/sudoers.d/010_karlos-nopasswd` (validado con `visudo`).              |
| `04-static-ip.sh`         | Fallback IP estática `10.10.0.246` vía `nmcli` (gw `10.10.0.1`, dns `10.10.0.210`). |
| `05-kiosk.sh`             | Instala Xorg mínimo + mpv + venv, escribe `~/.xinitrc` e instala `kiosk.service`. |
| `06-scraper-timer.sh`     | Desactiva/elimina el scraper horario (modelo nuevo: scraping on-demand al pulsar). |

## Unidades systemd (plantillas)

- `kiosk.service` — arranca la UI Kivy a pantalla completa en HDMI0 al encender.
- `scrapper.service` / `scrapper.timer` — **obsoletos** (modelo antiguo de scraping horario). El tablero actual usa la agenda de ESPN y scrapea StreamEast on-demand al pulsar una tarjeta; el paso `06` los elimina.

Los placeholders `__USER__`, `__PROJECT_DIR__` y `__VENV_DIR__` se sustituyen automáticamente al instalarlas con los scripts `05` y `06`.

## Notas

- Ejecuta `--harden` (paso 2) **solo** tras confirmar que entras por clave, para no quedarte fuera.
- `sudo` sin contraseña reduce la seguridad: úsalo consciente del riesgo, en LAN de confianza.
- Para volver a DHCP: `sudo nmcli connection modify "<conexión>" ipv4.method auto && sudo nmcli connection up "<conexión>"`.
- Los pasos 5 y 6 asumen que ya existe `requirements.txt` y el paquete `ui`/`scraper`. Si aún no están implementados, el kiosk y el timer quedarán instalados pero fallarán al ejecutarse hasta que se implemente la app (siguiente fase).
