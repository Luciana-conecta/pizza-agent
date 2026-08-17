# Pizza Agent

Bot de Telegram para una pizzería, construido con [crewAI](https://crewai.com) (Flow) y NVIDIA
como LLM. Responde preguntas de menú/horarios, toma pedidos, permite rastrear un pedido propio,
escala quejas a la dueña, y le permite a la dueña consultar datos de clientes por chat.

Es un proyecto hermano de `request_router` (no lo modifica ni depende de él).

## 1. Requisitos

- Python >=3.10, <3.14
- [uv](https://docs.astral.sh/uv/) para manejar dependencias
- Acceso a la base Postgres que ya tiene las tablas `productos`, `clientes`, `pedidos` y
  `pedido_detalles`

## 2. Migración de base de datos (una sola vez)

Este proyecto necesita poder reconocer a un cliente que vuelve a escribir por Telegram sin
pedirle el teléfono en cada pedido. Para eso hace falta una columna nueva en `clientes`:

```sql
ALTER TABLE clientes ADD COLUMN telegram_chat_id BIGINT UNIQUE;
```

Corré esto una vez contra tu base (por ejemplo desde pgAdmin) antes de usar el bot.

## 3. Configuración

```bash
cp .env.example .env
```

Completá `.env` con:

- `NVIDIA_API_KEY`: la misma que ya usás en `request_router`.
- `TELEGRAM_BOT_TOKEN`: hablale a [@BotFather](https://t.me/BotFather) en Telegram, `/newbot`,
  y te da el token.
- `OWNER_TELEGRAM_CHAT_ID`: tu chat_id de Telegram (el de la dueña). Para conseguirlo, hablale
  a tu bot una vez y mirá los logs del bot (`uv run bot`, va a imprimir el chat_id en la consola
  al recibir tu mensaje), o usá [@userinfobot](https://t.me/userinfobot).
- `DATABASE_URL`: cadena de conexión a Postgres, por ejemplo
  `postgresql://usuario:password@localhost:5432/nombre_db`.

Si el local todavía no tiene horarios/dirección/zona de delivery cargados, editá
`src/pizza_agent/config.py` con esos datos (no están en la base de datos).

## 4. Instalar dependencias

```bash
uv sync
```

## 5. Probar sin Telegram

```bash
uv run kickoff
```

Corre el Flow una vez con un mensaje de prueba y muestra el resultado por consola. Sirve para
confirmar que las llaves y la conexión a la base están bien antes de conectar Telegram.

## 6. Correr el bot localmente

```bash
uv run bot
```

Andá a Telegram y escribile a tu bot. Con `Ctrl+C` lo parás.

## 7. Desplegar en el VPS (Ubuntu/Debian)

1. Copiá el proyecto al servidor (por ejemplo `git clone` si lo subiste a un repo, o `scp -r`).
2. En el servidor, dentro de la carpeta del proyecto:
   ```bash
   uv sync
   ```
3. Creá `.env` directamente en el servidor con los mismos valores del paso 3 (nunca subas `.env`
   a un repo).
4. Corré la migración de la sección 2 contra la base de producción, si todavía no la corriste.
5. Copiá el archivo de servicio y activalo:
   ```bash
   sudo cp deploy/pizza-agent-bot.service /etc/systemd/system/pizza-agent-bot.service
   sudo nano /etc/systemd/system/pizza-agent-bot.service   # ajustá User= y las rutas si hace falta
   sudo systemctl daemon-reload
   sudo systemctl enable --now pizza-agent-bot
   ```
6. Ver logs en vivo:
   ```bash
   journalctl -u pizza-agent-bot -f
   ```

El bot corre en modo *polling* (se conecta él hacia Telegram), así que no hace falta abrir
puertos, dominio ni HTTPS. La conexión a Postgres es local (`localhost`), así que tampoco hace
falta exponer el puerto de la base.

## 8. Actualizar el estado de un pedido

Los pedidos que crea el bot entran como `pendiente`. Para avanzarlos, dos opciones:

- A mano en pgAdmin, editando la columna `estado` de `pedidos`.
- Con el comando del bot (solo funciona desde el chat de la dueña):
  ```
  /estado 42 en_preparacion
  ```

## 9. Consultas administrativas (solo la dueña)

Desde el chat configurado en `OWNER_TELEGRAM_CHAT_ID`, se le puede preguntar al bot en lenguaje
natural, por ejemplo:

- "¿cuánto gastó [nombre]?"
- "¿quién es mi cliente más frecuente?"
- "¿cuál fue la última compra de [nombre]?"

Desde cualquier otro chat, esas preguntas no devuelven datos de otros clientes.

## Estructura del proyecto

```
src/pizza_agent/
├── main.py            # Flow de CrewAI: clasificar -> enrutar -> resolver
├── telegram_bot.py     # Proceso del bot (long-running, polling)
├── session_store.py    # Borrador de pedido por chat, persistido en data/sessions.json
├── db.py               # Acceso a Postgres (productos, clientes, pedidos, pedido_detalles)
├── config.py            # Horarios, dirección, zona/costo de delivery
└── tools/
    ├── menu_tool.py     # Consultar menú e información del local
    ├── tracking_tool.py # Rastrear un pedido propio
    ├── order_tools.py   # Armar el borrador del pedido
    └── admin_tools.py   # Consultas de clientes/ventas, solo para la dueña
```
