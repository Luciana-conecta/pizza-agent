# Memoria del proyecto

Registro de decisiones, correcciones y ajustes no obvios que se hicieron en sesiones de chat
con un asistente de IA sobre este repo. No es un changelog de código (para eso está `git log`):
acá va el **porqué** de una decisión, una corrección de rumbo, o una restricción de negocio que
no se puede deducir leyendo el código solo.

Antes de tocar `db.py`, `session_store.py`, `tools/` o los prompts de `main.py`, revisá las
entradas relevantes acá abajo.

Formato de cada entrada: fecha, qué se decidió/corrigió, por qué, y qué archivos toca.

---

## 2026-08-17 — Refactor: resultados estructurados, tools admin, formatters, máquina de estados, validación

**Qué**: `db.py` ahora devuelve `Result{success, data, error}` en vez de `None`/valores crudos.
Las 7 tools administrativas viejas (que pedían `cliente_id`, un dato que el LLM no tiene) se
reemplazaron por 4 tools de alto nivel que reciben el nombre del cliente y lo resuelven
internamente (si hay 2+ coincidencias, no adivinan — piden aclarar). El formato de texto para
Telegram se movió a `formatters.py` (funciones puras, deterministas). `OrderDraft` tiene ahora
una máquina de estados explícita (`transition_to()`) en vez de asignaciones de `status` a mano
desde 3 archivos distintos. Antes de confirmar un pedido, `order_validation.py` corre una
validación dura (cantidades, teléfono, dirección, disponibilidad/precio real del producto) —
el `ready_to_confirm` que devuelve el LLM es solo una sugerencia para mostrar los botones, nunca
la autorización real.

**Por qué**: las tools viejas obligaban al LLM a encadenar 2+ tool calls para resolver un
`cliente_id` que no conocía, con riesgo de que el modelo (Llama 3.1 70b) inventara uno. Dejar
que el LLM redactara el texto final a partir de datos crudos de la DB arriesgaba que cambiara
cifras o nombres. La única validación antes de confirmar era "¿hay teléfono y dirección?" —
insuficiente.

**Excluido a propósito de esta iteración** (decisión explícita, no un olvido): capa LLM que
reformula texto con descarte si cambian las cifras; idempotencia de confirmación con
identificador único + verificación transaccional (hoy solo hay un lock por chat, que mitiga el
caso común pero no es una garantía dura); escritura atómica de `data/sessions.json` + lock
global de archivo; logging estructurado por etapa; suite de tests con pytest.

**Diseño completo**: ver el plan en `C:\Users\SAMSUNG\.claude\plans\goofy-swimming-waffle.md`
si sigue disponible, o esta misma entrada como resumen.

**Archivos**: `result.py` (nuevo), `formatters.py` (nuevo), `order_validation.py` (nuevo),
`db.py`, `session_store.py`, `tools/admin_tools.py`, `tools/order_tools.py`, `tools/menu_tool.py`,
`tools/tracking_tool.py`, `main.py`, `telegram_bot.py`, `config.py`.

## 2026-08-17 — Fix: el bot respondía "no hay una función que responda a este mensaje" ante un saludo

**Qué**: se reescribió `COMMON_AGENT_RULES` en `main.py`. Antes citaba textualmente la frase
prohibida entre comillas ("nunca respondas 'no hay una función...'"); ahora da solo ejemplos
positivos de saludo, sin citar la frase mala.

**Por qué**: con modelos como Llama 3.1 70b, poner una frase prohibida citada dentro del prompt
es un anti-patrón conocido — el modelo tiende a repetirla en vez de evitarla, porque queda como
el texto más "cercano" en el contexto. Esto no era un bug introducido por el refactor de arriba
(el código de tools ni se llegó a ejecutar en el caso reportado); ya pasaba antes.

**Archivos**: `main.py` (`COMMON_AGENT_RULES`).

## 2026-08-17 — Latencia alta: timeout de 30s hacía que cada colgue de NVIDIA costara 30s

**Qué**: se bajó `timeout` de 30 a 15 segundos en `_nvidia_llm()` (`main.py`).

**Por qué**: en logs de producción se vio que NVIDIA a veces deja un request colgado sin
responder ni fallar (silencio, no un error). `main.py` usa `custom_openai=True`, que rutea al
cliente nativo de OpenAI dentro de crewAI — ese cliente reintenta solo (`max_retries=2` por
defecto) pero **espera el timeout completo antes de reintentar**. Con `timeout=30`, cada colgue
costaba ~30s antes de que el reintento (que resuelve en 1-2s) se disparara; en un turno se vieron
2 colgues seguidos = 76s de espera total para una sola respuesta. Las llamadas que sí responden
tardan 2-10s, así que 15s da margen sin esperar de más. Si sigue habiendo colgues frecuentes,
bajar más el timeout achica el costo pero no elimina la causa (esa es del lado de NVIDIA).

**Archivos**: `main.py` (`_nvidia_llm`).

## 2026-08-17 — Falta función para listar pedidos por estado (ej. "pedidos pendientes")

**Qué**: se agregó `obtener_pedidos_por_estado(estado)` — nueva función en `db.py`, formatter
`format_orders_by_status` en `formatters.py`, y tool `ObtenerPedidosPorEstadoTool` en
`admin_tools.py` (agregada a `_admin_agent()` en `main.py`). Lista todos los pedidos de un
estado dado (`pendiente`, `en_preparacion`, `en_camino`, `entregado`, `cancelado`) de cualquier
cliente, ordenados del más viejo al más nuevo.

**Por qué**: la dueña preguntó "¿cuáles son los pedidos pendientes?" y el bot respondió
correctamente que no había una función para eso — ninguna de las tools admin (ni las 7 viejas
ni las 4 nuevas del refactor de hoy) listaba pedidos a través de todos los clientes, todas eran
por cliente puntual. No era el bug del prompt (ver entrada anterior de "no hay una función..."),
era un hueco real de funcionalidad.

**Archivos**: `db.py`, `formatters.py`, `tools/admin_tools.py`, `main.py`.

## 2026-08-17 — El bot alucinaba respuestas del menú en medio de un pedido (ruteo pegajoso)

**Qué**: se agregó el comando `/cancelar` en `telegram_bot.py` para que el cliente pueda
resetear un pedido a medias sin depender de que expire solo (30 min) o de tocar la DB a mano.
Se agregó `InfoLookupTool` a las tools del agente de pedidos (antes solo el agente de FAQ podía
responder horarios/dirección) y se reforzó su backstory: si preguntan por productos/precios/
horarios en medio de un pedido, tiene que llamar a la herramienta correspondiente antes de
responder, nunca inventar una lista de opciones.

**Por qué**: la dueña preguntó "qué pizzas tiene disponible" con un pedido de prueba sin
confirmar/cancelar en su chat. `classify_intent` en `main.py` tiene "ruteo pegajoso": si hay un
draft en `building`/`awaiting_confirmation`, TODO mensaje de ese chat se rutea al agente de
pedidos sin importar el contenido — ni siquiera se clasifica. El agente de pedidos sí tiene
`consultar_menu` entre sus tools, pero en ese caso no la llamó y alucinó una respuesta sin
opciones reales; al repreguntar "cuáles opciones", devolvió directamente la pregunta del
usuario reformulada. Esto es una falla de cumplimiento de instrucciones del modelo (Llama 3.1
70b vía NVIDIA), no un bug de ruteo en sí — el ruteo hizo lo que tenía que hacer (mandarlo al
agente correcto para seguir el pedido), pero ese agente no respetó "siempre usá la herramienta".

**Sigue abierto**: el refuerzo de prompt + la tool nueva son mitigaciones, no una garantía —
ya se vieron varias fallas de cumplimiento distintas de este modelo en esta misma sesión
(repetir una frase prohibida citada, alucinar sin llamar una tool disponible, hacer eco de la
pregunta del usuario). Si después de este cambio se repite el patrón de "no llama la tool
disponible", el problema probablemente esté en la confiabilidad del tool-calling de
`meta/llama-3.1-70b-instruct` vía NVIDIA para esta combinación de tools + `response_format`, no
en el prompt — ahí valdría la pena evaluar otro modelo para el agente de pedidos.

**Archivos**: `telegram_bot.py` (`/cancelar`), `main.py` (`_order_agent`).
