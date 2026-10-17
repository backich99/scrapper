# Requisitos — NFL Kiosk bajo demanda

## Introducción

Este documento define los requisitos para reconfigurar el kiosk de Raspberry Pi
de forma que el scraping y la resolución de streams de **NFL** se delegue al
addon **live-sport-plugin** (corriendo como servicio Node local en la Pi), y que
el dispositivo pueda **actualizarse automáticamente** desde el repositorio propio
(`backich99/scrapper`) y de forma controlada desde el fork del plugin
(`backich99/live-sport-plugin`, con `upstream` a `rajhodedara/live-sport-plugin`).

El objetivo de negocio es doble, según lo expresado por el usuario:

1. **Dejar de modificar el script de scraping cada vez que una fuente cambia de
   formato.** La fragilidad del scraper actual (selectores/regex sobre HTML) se
   sustituye por el motor del plugin, cuyos arreglos de providers llegan vía
   actualización desde su `upstream`, sin editar código propio.
2. **Poder cambiar de fuente cuando la mejor se pausa desde el origen.** El plugin
   devuelve varias fuentes rankeadas; la UI debe permitir saltar a otra sin
   re-resolver.

### Alcance

- **Dentro de alcance:** NFL únicamente. Reutilización de la UI actual. Scraping
  y resolución bajo demanda vía plugin. Fallback al scraper Python existente.
  Auto-update del repo propio (horario + manual) y actualización controlada del
  submódulo del plugin. Resolución configurable (default 1080p). Timeout de 30s.
- **Fuera de alcance (por decisión del usuario):** Liga MX, NCAA, y el menú
  jerárquico de selección de deporte. Se conserva el flujo de UML de NFL actual.

### Decisiones ya tomadas (contexto de la conversación)

- live-sport-plugin corre como **servicio Node local** en `localhost:7000`.
- El fork `backich99/live-sport-plugin` ya está configurado como submódulo
  (`origin` = fork, `upstream` = `rajhodedara/live-sport-plugin`).
- Auto-update del repo propio: **pull cada hora** (systemd timer) + **script
  manual** para forzarlo. Rama `main`. Reinicio de servicios tras aplicar.
- Al tocar un partido: **auto-reproducir el mejor stream disponible**, topado a
  una resolución máxima **configurable** (default **1080p**, la TV no es 4K).
- Si el stream se pausa/congela: poder **saltar a la siguiente fuente** sin
  re-resolver.
- Timeout de resolución: **30s**; al expirar, volver a la pantalla anterior y
  marcar ese partido/stream como **no disponible**.
- El scraper Python actual se **conserva como respaldo** (fallback), no se elimina.

---

## Requisitos

### Requisito 1 — Auto-update del repositorio propio

**Historia de usuario:** Como administrador del kiosk, quiero que la Raspberry se
actualice automáticamente desde `backich99/scrapper` y también poder forzar la
actualización manualmente, para desplegar cambios sin intervención manual rutinaria
pero con control cuando estoy trabajando.

#### Criterios de aceptación

1. CUANDO el systemd timer de actualización se dispara (cada hora) ENTONCES el
   sistema DEBERÁ ejecutar un `git fetch` sobre el remoto `origin` en la rama `main`.
2. CUANDO el `fetch` detecta commits nuevos en `origin/main` ENTONCES el sistema
   DEBERÁ aplicar los cambios (fast-forward) sobre el checkout local.
3. CUANDO no hay commits nuevos ENTONCES el sistema NO DEBERÁ reiniciar servicios
   ni modificar el checkout.
4. CUANDO se aplican cambios ENTONCES el sistema DEBERÁ reiniciar los servicios
   afectados (UI del kiosk y servicio del plugin) de forma que tomen el código nuevo.
5. CUANDO el administrador ejecuta el script manual de actualización ENTONCES el
   sistema DEBERÁ realizar el mismo flujo (fetch + aplicar + reiniciar) de inmediato,
   independientemente del timer.
6. SI el checkout local tiene cambios sin confirmar que impiden un fast-forward
   ENTONCES el sistema DEBERÁ abortar la actualización sin dejar el repo en estado
   inconsistente, y registrar el motivo en el log.
7. CUANDO ocurre un fallo durante la actualización (red, git, reinicio de servicio)
   ENTONCES el sistema DEBERÁ registrar el error en el log y conservar el estado
   anterior en ejecución (no dejar el kiosk caído).

### Requisito 2 — Actualización controlada del plugin desde upstream

**Historia de usuario:** Como administrador, quiero traer los arreglos de providers
del plugin de terceros de forma controlada (no un pull ciego), para que los cambios
de formato de las fuentes se resuelvan vía actualización en lugar de editar código.

#### Criterios de aceptación

1. CUANDO el administrador inicia una actualización del plugin ENTONCES el sistema
   DEBERÁ traer los cambios desde el remoto `upstream` (`rajhodedara/live-sport-plugin`)
   del submódulo.
2. CUANDO se integran cambios del `upstream` ENTONCES el sistema DEBERÁ reflejar el
   nuevo commit del submódulo en el repositorio propio (`backich99/scrapper`) como
   un cambio versionado y deliberado.
3. El sistema NO DEBERÁ actualizar el submódulo del plugin de forma automática
   dentro del ciclo del timer horario del Requisito 1 (la actualización del plugin
   es un acto deliberado y separado).
4. CUANDO el submódulo cambia de commit ENTONCES el proceso de actualización DEBERÁ
   reconstruir (build) el plugin antes de reiniciar su servicio.

### Requisito 3 — Plugin como servicio Node local

**Historia de usuario:** Como kiosk, quiero que el addon live-sport-plugin corra
como servicio local persistente, para poder consultar partidos y streams de NFL por
HTTP sin depender de servicios externos ni de un navegador.

#### Criterios de aceptación

1. CUANDO la Raspberry arranca ENTONCES el sistema DEBERÁ iniciar el servicio del
   plugin de forma automática (systemd) y mantenerlo en ejecución.
2. CUANDO el servicio del plugin falla o se detiene inesperadamente ENTONCES systemd
   DEBERÁ reiniciarlo automáticamente.
3. El servicio del plugin DEBERÁ escuchar en un puerto local (default `7000`),
   accesible desde la capa Python en `localhost`.
4. CUANDO el plugin aún no está listo para atender peticiones ENTONCES la capa
   Python DEBERÁ tratarlo como fuente no disponible (ver Requisito 5, fallback) en
   lugar de fallar de forma dura.
5. El build del plugin DEBERÁ completarse (incluyendo assets WASM) antes de que su
   servicio se marque como activo.

### Requisito 4 — Listado de partidos NFL de hoy desde el plugin

**Historia de usuario:** Como usuario frente a la pantalla de 7", quiero ver los
partidos de NFL de hoy, para elegir cuál ver.

#### Criterios de aceptación

1. CUANDO la UI se abre o refresca ENTONCES el sistema DEBERÁ obtener del plugin la
   lista de partidos de NFL correspondientes al día actual.
2. CUANDO el plugin devuelve partidos ENTONCES la UI DEBERÁ mostrar un botón grande
   por partido, reutilizando la interfaz actual (agenda/scores/teams/logos).
3. CUANDO un partido fue marcado previamente como "no disponible" (ver Requisito 6)
   ENTONCES la UI DEBERÁ indicarlo visualmente en el menú.
4. SI el plugin no está disponible o no devuelve partidos ENTONCES el sistema DEBERÁ
   intentar el fallback del scraper Python existente (ver Requisito 5).
5. La UI DEBERÁ refrescar la lista de partidos de forma periódica o bajo demanda sin
   requerir reinicio de la aplicación.

### Requisito 5 — Fallback al scraper Python existente

**Historia de usuario:** Como usuario, quiero que si el plugin no entrega datos, el
sistema use el scraper que ya existe como respaldo, para no quedarme sin partidos.

#### Criterios de aceptación

1. CUANDO el plugin no devuelve partidos NFL de hoy ENTONCES el sistema DEBERÁ
   recurrir al scraper Python existente (`scraper/`) como fuente de respaldo.
2. CUANDO el plugin no devuelve streams para un partido seleccionado ENTONCES el
   sistema DEBERÁ intentar resolver ese partido mediante el scraper Python existente.
3. El scraper Python existente DEBERÁ conservarse funcional y NO DEBERÁ eliminarse
   del repositorio.
4. CUANDO se usa el fallback ENTONCES el sistema DEBERÁ registrarlo en el log para
   trazabilidad.

### Requisito 6 — Reproducción del mejor stream con tope de resolución configurable

**Historia de usuario:** Como usuario, quiero que al tocar un partido se reproduzca
automáticamente el mejor stream disponible dentro de un límite de resolución, para
no desperdiciar ancho de banda en una TV que no es 4K.

#### Criterios de aceptación

1. CUANDO el usuario toca un partido ENTONCES el sistema DEBERÁ solicitar al plugin
   los streams disponibles para ese partido, rankeados.
2. CUANDO existe más de un stream ENTONCES el sistema DEBERÁ seleccionar
   automáticamente el mejor stream disponible cuya resolución no exceda el tope
   configurado.
3. El tope de resolución DEBERÁ ser configurable en un archivo de configuración
   (ej. `config/kiosk.yaml`), con valor por defecto **1080p**.
4. CUANDO se selecciona el stream ENTONCES el sistema DEBERÁ reproducirlo a pantalla
   completa en la TV de 65" (HDMI1) mediante el reproductor existente (mpv).
5. SI ningún stream cumple el tope de resolución ENTONCES el sistema DEBERÁ
   seleccionar el stream disponible más cercano por debajo del tope, o el de menor
   resolución si todos lo exceden, antes de declarar el partido no reproducible.
6. SI la resolución del stream no se entrega en **30 segundos** ENTONCES el sistema
   DEBERÁ abortar, volver a la pantalla anterior y marcar ese partido/stream como
   **no disponible** en el menú.

### Requisito 7 — Cambio de fuente ante pausas del origen

**Historia de usuario:** Como usuario, quiero poder saltar a otra fuente cuando la
que estoy viendo se pausa desde el origen, para seguir viendo el partido sin volver
a buscar.

#### Criterios de aceptación

1. CUANDO el sistema resuelve los streams de un partido ENTONCES DEBERÁ conservar la
   lista completa de fuentes rankeadas disponibles (no solo la elegida).
2. CUANDO el usuario solicita cambiar de fuente ("siguiente fuente") ENTONCES el
   sistema DEBERÁ reproducir el siguiente stream de la lista ya resuelta, sin
   re-consultar el plugin.
3. CUANDO el usuario cambia de fuente ENTONCES el sistema DEBERÁ detener la instancia
   de reproducción actual antes de iniciar la nueva.
4. CUANDO se agotan las fuentes de la lista ENTONCES el sistema DEBERÁ indicarlo al
   usuario y permitir volver a la pantalla de partidos.
5. El cambio de fuente DEBERÁ respetar el mismo tope de resolución configurado
   (Requisito 6).

### Requisito 8 — Configuración centralizada

**Historia de usuario:** Como administrador, quiero ajustar los parámetros clave del
kiosk desde un archivo de configuración, para no tener que modificar código.

#### Criterios de aceptación

1. El sistema DEBERÁ leer de un archivo de configuración, como mínimo: el tope de
   resolución (default 1080p), la URL/puerto local del plugin (default
   `http://localhost:7000`), y el timeout de resolución (default 30s).
2. CUANDO el archivo de configuración define un valor ENTONCES el sistema DEBERÁ
   usarlo en lugar del valor por defecto.
3. SI el archivo de configuración falta o un valor es inválido ENTONCES el sistema
   DEBERÁ usar valores por defecto seguros y registrar una advertencia.
