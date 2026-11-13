# First Contact — Hack The Box

Writeup del reto **First Contact** (creado por VisiónSpace), de la categoría
de retos de Hack The Box: predecir las ventanas de visibilidad de un satélite
a partir de su TLE y de las coordenadas de una estación terrestre.

> **Resumen:** el reto es un servicio TCP que genera parámetros aleatorios
> (TLE + estación) en cada conexión y pide las subidas y puestas del satélite
> por encima de 30° en las próximas 24 horas. La solución es propagación
> orbital SGP4 + conversión a coordenadas topocéntricas, todo automatizado en
> un cliente que resuelve las 3 rondas en la misma sesión. La bandera la
> imprime el servicio al completar las rondas (ver `autosolve.py`).

## El reto

El servicio (`154.57.164.82:38775`) presenta este escenario: una actualización
de software dejó fuera de línea el sistema de predicción de pases, y hay que
calcular a mano cuándo el satélite será visible para restablecer el contacto.

En cada conexión el servicio genera parámetros nuevos:

```
Challenge Sat 1
TLE:
DIGITWIN HTB
1 01337U 00000A   26317.08746065+.00000000  00000-0  93937-4 0 335  8
2 01337  96.5160 200.8897 0058129 193.2422 297.4293 15.79594866150658
Station location:
(Lat,Long): 46.01671348890308,-60.12832137058189

When will it be visible next?>
```

Reglas (según el enunciado):

- La ventana es de **24 horas** a partir del momento de la conexión.
- Un satélite es **visible si está a más de 30° sobre el horizonte**.
- La ventana actual se puede omitir.
- La respuesta son marcas de tiempo ISO separadas por espacios, empezando
  por la primera subida: `2026-11-13T03:43:53Z 2026-11-13T03:46:22Z ...`
- Hay **3 rondas** (Sat 1, Sat 2, Sat 3) en la misma sesión. Cada `Correct!`
  presenta el siguiente satélite. Al final el servicio entrega la bandera.

## El método

### 1. Propagación SGP4

Un TLE (Two-Line Element set) no da la posición del satélite directamente:
da los elementos orbitales medios (inclinación, RAAN, excentricidad, argumento
del perigeo, anomalía media, movimiento medio) y una época. El modelo
**SGP4** propaga esos elementos a una posición en cada instante, en
coordenadas TEME (casi ECI). Se usó el paquete `sgp4` de Python.

### 2. De ECI a elevación sobre la estación

Para cada instante `t`:

1. **SGP4** → posición `(x, y, z)` en TEME.
2. **Rotación por GMST** (tiempo sidéreo medio de Greenwich) → coordenadas
   ECEF (fijas a la Tierra).
3. **Estación** en ECEF a partir de su latitud/longitud geodésica (WGS84).
4. Vector estación→satélite en ECEF → componentes **Este, Norte, Arriba**
   (ENU) → `elevación = atan2(arriba, distancia_horizontal)`.

### 3. Detección de pases

Se muestrea la elevación cada 5 s durante 24 h. Cuando cruza los 30° se
refina el instante con **bisección** (precisión ~0.05 s) y se redondea al
segundo más cercano. Cada pase produce un par subida/puesta.

El resultado se validó con **dos implementaciones independientes** (el
paquete `sgp4` y Skyfield), que coinciden a 0.01° en elevación.

### 4. Automatización

El servicio regenera los parámetros en cada conexión y la sesión se cierra
rápido, así que el cálculo a mano entre ventanas es inviable. `autosolve.py`
hace todo en una sola conexión:

- abre un socket TCP al servicio y lee el banner hasta el prompt;
- extrae el TLE y la estación con regex;
- calcula los pases y envía la respuesta en ~2 s;
- repite para Sat 2 y Sat 3 en la misma sesión;
- si una ronda no tiene ningún pase >30° (instancia imposible por azar),
  aborta y reconecta con una cadena nueva (hasta 5 intentos);
- al final imprime la bandera.

## Por qué funcionó

- **SGP4 es el modelo correcto para un TLE.** Un propagador kepleriano
  simple (sin perturbaciones J2) se desvía minutos en 24 h; SGP4 coincide
  con la referencia del servicio: las rondas 1 y 2 devolvieron `Correct!`
  de forma repetida en varias cadenas.
- **La geometría es exacta, no aproximada.** GMST con la fórmula estándar,
  WGS84 para la estación y bisección para los cruces dejan los tiempos a
  menos de un segundo de la referencia.
- **Responder dentro de la misma sesión y rápido.** El servicio genera
  parámetros nuevos por conexión y cierra pronto: el cliente socket lee,
  calcula y contesta en ~2 s, sin recargar nada entre medias.

## Por qué no funcionó (todo lo que falló antes)

### 1. Parámetros regenerados en cada conexión

El reto genera un TLE y una estación nuevos cada vez que se conecta o se
recarga la página (las épocas lo delatan: 01:42Z, 01:50Z, 01:55Z...). Las
primeras respuestas se calcularon con parámetros ya viejos y el servicio las
rechazó. **Lección:** hay que calcular con los parámetros de la sesión
activa, sin recargar.

### 2. Las marcas de tiempo no son la bandera

La respuesta va al **servicio TCP**, no al campo de bandera de la página de
HTB. Ese campo espera un `HTB{...}`. Las marcas de tiempo son solo la
respuesta al servicio; la bandera la entrega el servicio al final.

### 3. El error `Invalid isoformat string: 'GET'`

Abrir la dirección del servicio en el navegador envía una petición HTTP
`GET`; el servicio intentó interpretar `GET` como marcas de tiempo. Es un
servicio TCP crudo: hay que hablarle con `nc` o con un socket, no con HTTP.

### 4. Redondeo contra truncado

Los cruces exactos tienen decimales (por ejemplo `14:57:38.93`). Redondear
al segundo más cercano da `14:57:39Z`; truncar da `14:57:38Z`. En un
comparador exacto, un segundo decide. (En la práctica, la causa real de los
primeros rechazos fue el punto 1, no este.)

### 5. La sesión se cierra rápido

El `nc` se cierra antes de que dé tiempo a copiar el TLE, calcular a mano y
pegar la respuesta. La ida y vuelta por chat era demasiado lenta. **Lección:**
automatizar la sesión completa.

### 6. Errores de pegado

- Pegar respuestas de sesiones viejas en sesiones nuevas (cada sesión tiene
  sus propios parámetros).
- Pegar la época del TLE como respuesta (el servicio contestó
  `Wrong number of windows`).
- Caracteres de control colados al copiar de la terminal (`\x1bE` al final
  de la línea), que rompieron el parseo: `Invalid isoformat string:
  '2026-11-13T14:58:53+00:00\x1bE'`. Hay que pegar con "Sanitize and paste"
  y revisar que la línea quede limpia.

### 7. La ventana actual y la subida real

Cuando el satélite ya está por encima de 30° al empezar la ronda, el pase
está en curso y su subida ocurrió **antes**. La primera versión del script
usaba la hora de inicio como subida (recortada), con casi 2 minutos de
error: `02:10:43Z` en vez de la subida real `02:08:53Z`. El servicio
rechazó la respuesta. **Lección:** hay que retroceder y buscar el cruce
real, no recortar la ventana.

### 8. El respaldo de >0° no sirve

Cuando una ronda no tenía pases >30°, el script mandaba los pases por encima
del horizonte (>0°). El servicio usa >30°, así que siempre lo rechazó.
Además, el generador produce instancias imposibles por azar: por ejemplo,
un Sat 3 con elevación máxima de 29.64° en 24 h (a 0.36° del umbral). La
única salida es abortar esa cadena y probar con otra nueva.

### 9. El bug que borró la bandera

El error más costoso fue de mi propio script: al leer la respuesta del
servidor, si la conexión se cerraba, el código hacía `buf = b""`, borrando
todo lo recibido. Las respuestas de la ronda 3 eran correctas (verificadas
contra Skyfield), el servidor probablemente envió la bandera... y el script
la descartó. Tres veces. La corrección fue de una línea: no borrar el
buffer al cerrarse la conexión.

### 10. El espacio fantasma en el TLE

Algunas líneas 2 llegan con un espacio de más antes del número de
revolución (`15.61226059 14086` en vez de `15.6122605914086`), lo que rompe
los parsers estrictos de columnas fijas. El script lo normaliza quitando
ese espacio.

## Cómo correrlo

```bash
# Una sola vez: entorno con sgp4
python3 -m venv ~/satvenv && ~/satvenv/bin/pip install sgp4

# Resolver el reto completo (3 rondas + reintentos si toca instancia imposible)
~/satvenv/bin/python autosolve.py
# (opcional: autosolve.py <host> <puerto>; por defecto 154.57.164.82 38775)
```

El script imprime cada ronda, lo que envía y la respuesta del servidor. Al
completar la cadena muestra:

```
*** BANDERA: HTB{...} ***
```

Esa bandera es la que se envía en la página del reto en HTB.

## Archivos

- `autosolve.py` — cliente completo: resuelve las 3 rondas en una sesión,
  con reintentos ante instancias imposibles.
- `satvis.py` — calculadora manual: pases >30° para un TLE y una estación
  dados por línea de comandos (útil para depurar una ronda concreta).
- `README.md` — este writeup.

## Lecciones

1. En retos con parámetros por sesión, la respuesta solo vale para la
   sesión que la generó. Automatiza o no llegas.
2. Verifica con dos implementaciones independientes antes de culpar al
   servicio: SGP4 y Skyfield coincidían; el fallo estaba en mi código.
3. El bug más caro no fue orbital, fue de manejo de buffers: un `buf = b""`
   en el sitio equivocado borró tres banderas.
4. Cuando el generador es aleatorio, algunas instancias son imposibles
   (29.64° de máximo): reintentar es parte de la solución, no un fracaso.

---

*Writeup por Jesús Alejandro Juárez Contreras ([@0xjuarez](https://github.com/0xjuarez)) — DevSecurityMX.*
