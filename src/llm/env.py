"""Lectura minima de un archivo .env, sin dependencias.

Por que existe
--------------

Nada en el sistema cargaba ``.env``, asi que ``GeminiLLMClient`` moria con
"GEMINI_API_KEY must be set" antes siquiera de abrir el estate, aunque la clave
estuviera ahi. Y cuando alguien la exportaba a mano, el valor venia envuelto en
comillas dobles: 55 caracteres empezando por ``"AQ`` en lugar de los 53 reales.
Medido contra la API en vivo:

    tal cual (55 chars)        -> 400 API_KEY_INVALID
    sin comillas (53 chars)    -> 200 OK

Es decir: la clave era correcta y el error decia lo contrario. Por eso quitar las
comillas no es cosmetico.

No se añade ``python-dotenv`` como dependencia: esto son veinte lineas de stdlib
y el proyecto se instala con cuatro paquetes.

Reglas
------

* Nunca pisa una variable que ya exista en el entorno. Lo que el operador exporto
  a mano gana sobre el archivo.
* Nunca imprime ni registra un valor. El archivo contiene credenciales.
* Un archivo ausente no es un error: devuelve una lista vacia.
"""

from __future__ import annotations

import os
from pathlib import Path


def parse_env_text(text: str) -> dict[str, str]:
    """Convierte el contenido de un .env en pares clave/valor.

    Soporta ``export KEY=value``, comentarios con ``#``, lineas en blanco y
    valores entre comillas simples o dobles.
    """

    values: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export "):].lstrip()

        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue

        value = value.strip()

        # Aqui esta el bug medido: un valor entre comillas se pasaba entero,
        # comillas incluidas, y el proveedor lo rechazaba como clave invalida.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        values[key] = value

    return values


def load_env_file(path: str | Path = ".env") -> list[str]:
    """Carga ``path`` en ``os.environ`` sin pisar lo ya definido.

    Devuelve los NOMBRES de las claves cargadas -- nunca los valores, para que
    quien llame pueda informar sin filtrar una credencial.
    """

    env_path = Path(path)
    if not env_path.is_file():
        return []

    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return []

    loaded: list[str] = []

    for key, value in parse_env_text(text).items():
        if key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)

    return sorted(loaded)
