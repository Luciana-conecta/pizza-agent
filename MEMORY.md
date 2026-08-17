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
