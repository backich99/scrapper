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
8. CUANDO el timer de actualización se dispara Y hay una reproducción activa
   ENTONCES el sistema NO DEBERÁ aplicar la actualización ni reiniciar servicios,
   y DEBERÁ marcar la actualización como **pendiente**.
9. CUANDO una reproducción activa termina (el usuario pulsa Detener o el stream
   finaliza) Y existe una actualización pendiente ENTONCES el sistema DEBERÁ
   aplicarla de inmediato (fetch + aplicar + reiniciar), sin esperar al siguiente
   tick del timer.
10. SI una actualización quedó pendiente y la reproducción nunca termina ENTONCES
    el siguiente tick del timer DEBERÁ reintentarla, respetando siempre la regla
    de no interrumpir una reproducción activa.

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
5. CUANDO se solicita actualizar el plugin Y hay una reproducción activa ENTONCES
   el sistema NO DEBERÁ reconstruir ni reiniciar el servicio del plugin, aplicando
   la misma regla de pendiente/aplazamiento del Requisito 1 (criterios 8-10).

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

### Requisito 4 — Listado de partidos NFL de la jornada desde el plugin

**Historia de usuario:** Como usuario frente a la pantalla de 7", quiero ver los
partidos de la jornada NFL en curso, con los de hoy y en vivo destacados, para
elegir cuál ver y también anticipar los que vienen (p. ej. el MNF del lunes).

**Definición de jornada:** la jornada NFL va de **martes a lunes** (arranca con el
TNF del jueves y cierra con el Monday Night Football del lunes). La "jornada en
curso" es la ventana martes 00:00 → lunes 23:59 (en la zona del kiosk) que contiene
el día de hoy.

#### Criterios de aceptación

1. CUANDO la UI se abre o refresca ENTONCES el sistema DEBERÁ obtener del plugin los
   partidos de NFL de la jornada en curso (ventana martes→lunes que contiene hoy).
2. CUANDO se muestran los partidos de la jornada ENTONCES la UI DEBERÁ ordenarlos
   priorizando los **en vivo** y los **de hoy** en la parte superior, y mostrar los
   demás de la jornada como **próximos** debajo (modelo híbrido).
3. CUANDO el plugin devuelve partidos ENTONCES la UI DEBERÁ mostrar un botón grande
   por partido, reutilizando la interfaz actual (agenda/scores/teams/logos).
4. CUANDO un partido fue marcado previamente como "no disponible" (ver Requisito 6)
   ENTONCES la UI DEBERÁ indicarlo visualmente en el menú.
5. SI el plugin no está disponible o no devuelve partidos ENTONCES el sistema DEBERÁ
   intentar el fallback del scraper Python existente (ver Requisito 5).
6. La UI DEBERÁ refrescar la lista de partidos de forma periódica o bajo demanda sin
   requerir reinicio de la aplicación.

### Requisito 5 — Fallback al scraper Python existente

**Historia de usuario:** Como usuario, quiero que si el plugin no entrega datos, el
sistema use el scraper que ya existe como respaldo, para no quedarme sin partidos.

#### Criterios de aceptación

1. CUANDO el plugin no devuelve partidos NFL de la jornada ENTONCES el sistema DEBERÁ
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

### Requisito 9 — Controles de reproducción en la pantalla táctil

**Historia de usuario:** Como usuario, mientras un partido se reproduce en la TV,
quiero controlar la reproducción desde la pantalla de 7", para detenerlo o cambiar
de fuente sin levantarme ni tocar la TV.

#### Criterios de aceptación

1. CUANDO hay una reproducción activa ENTONCES la pantalla de 7" DEBERÁ mostrar,
   como mínimo, dos botones grandes: **Detener** y **Siguiente streaming**.
2. CUANDO el usuario pulsa **Detener** ENTONCES el sistema DEBERÁ detener la
   instancia de reproducción (mpv) y volver a la lista de partidos.
3. CUANDO el usuario pulsa **Siguiente streaming** ENTONCES el sistema DEBERÁ
   saltar a la siguiente fuente de la lista ya resuelta (Requisito 7) sin
   re-consultar el plugin.
4. CUANDO se agotan las fuentes alternativas ENTONCES el botón **Siguiente
   streaming** DEBERÁ indicar que no hay más fuentes (deshabilitado o aviso) y
   permitir volver a la lista con **Detener**.
5. (Opcional) La pantalla PODRÁ mostrar un indicador de la fuente actual respecto
   al total disponible (p. ej. "fuente 2 de 5").

### Requisito 10 — Bloqueo de actualizaciones durante reproducción (lock de estado)

**Historia de usuario:** Como usuario viendo un partido, no quiero que una
actualización automática reinicie servicios y me corte el stream.

#### Criterios de aceptación

1. CUANDO comienza una reproducción ENTONCES el sistema DEBERÁ registrar un estado
   de **reproducción activa** accesible por los procesos de actualización.
2. CUANDO termina la reproducción (Detener o fin del stream) ENTONCES el sistema
   DEBERÁ limpiar ese estado de reproducción activa.
3. MIENTRAS exista el estado de reproducción activa ENTONCES ningún proceso de
   actualización (repo propio o plugin) DEBERÁ reiniciar servicios ni reconstruir
   el plugin.
4. SI el estado de reproducción activa queda obsoleto por un cierre inesperado de
   la aplicación ENTONCES el sistema DEBERÁ poder detectarlo y no bloquear
   actualizaciones de forma indefinida (p. ej. validando que el proceso de
   reproducción siga vivo).

### Requisito 11 — Soporte de fixtures/horarios vía datos de nflverse

**Historia de usuario:** Como usuario, quiero que la lista de partidos NFL muestre
nombres de equipo y horarios fiables, para identificar bien qué partido es cuál y
a qué hora, aun cuando el plugin no traiga esos datos.

**Fuente de datos:** CSV público de nflverse (`nfldata/data/games.csv`), leído
directamente con `requests` + `csv` (sin librerías adicionales). Contiene, por
partido: `game_id`, `season`, `week`, `gameday` (fecha), `gametime` (hora, en zona
ET), `weekday`, `home_team`, `away_team`, `home_score`, `away_score`, entre otros.
Verificado: `gameday`, `gametime` y equipos vienen poblados para partidos pasados
y futuros de la temporada en curso.

#### Criterios de aceptación

1. CUANDO se lista NFL ENTONCES el sistema PODRÁ consultar el CSV de nflverse para
   obtener los partidos de la **jornada en curso**, definida como la ventana
   **martes 00:00 → lunes 23:59** (en la zona del kiosk) que contiene el día de hoy
   (comparando `gameday`). Esta ventana define qué partidos pertenecen a la jornada
   (Requisito 4).
2. CUANDO un partido del plugin se empareja con un partido del CSV (por código o
   nombre de equipo) ENTONCES el sistema DEBERÁ completar/mejorar los datos que al
   plugin le falten: nombres de equipo y `start_time` (derivado de
   `gameday` + `gametime`).
3. CUANDO se usa `gametime` ENTONCES el sistema DEBERÁ interpretarlo en zona ET y
   convertirlo a la zona horaria del kiosk antes de mostrarlo. (El formato de
   `gametime` no incluye zona explícita; nflverse usa ET — a verificar en
   implementación.)
4. El sistema NO DEBERÁ depender de los marcadores (`home_score`/`away_score`) de
   esta fuente para estado en vivo: nflverse se actualiza con retraso (no es tiempo
   real). El estado live y la autoridad del kickoff los provee el plugin
   (Requisito 6).
5. El sistema DEBERÁ cachear localmente el CSV descargado y reutilizarlo durante
   **una semana** (los horarios de nflverse cambian poco una vez fijados), evitando
   descargarlo en cada refresco. Una actualización manual (`update-now`) PODRÁ
   forzar la re-descarga del CSV.
6. SI el CSV de nflverse no está disponible (red, 404) ENTONCES el sistema DEBERÁ
   seguir funcionando con los datos del plugin (degradación suave) y registrar el
   fallo en el log.
7. El enriquecimiento DEBERÁ aplicarse únicamente a la lista de partidos; MIENTRAS
   haya una reproducción activa, la vista de lista queda cubierta por los controles
   de reproducción (Requisito 9), por lo que este requisito no afecta la pantalla
   durante el streaming.
