# Instrucciones para Claude Code en este repo

Este es un bot de Telegram para una pizzería en producción (no un prototipo) — ver `README.md`
para la arquitectura y `AGENTS.md` para referencia general de CrewAI.

## Antes de trabajar

Leé `MEMORY.md` completo. Ahí están las decisiones, correcciones y restricciones de negocio no
obvias que salieron de sesiones de chat anteriores sobre este repo (ej. por qué las tools admin
resuelven nombre→id en vez de pedir un id, por qué el LLM nunca decide el estado final de un
pedido). No repitas un enfoque que ya se descartó ahí, y no reinventes algo que ya está resuelto.

## Después de trabajar

Si en la sesión se corrigió un enfoque equivocado, se tomó una decisión de diseño no obvia, o se
resolvió un bug cuya causa no es evidente leyendo el código, agregá una entrada nueva al final de
`MEMORY.md` (fecha, qué, por qué, archivos). No dupliques ahí lo que ya cuenta `git log` — solo el
razonamiento y las restricciones que no se pueden deducir del diff solo.

No hace falta agregar una entrada por cada cambio chico o mecánico (typos, formateo, un rename
obvio) — es para decisiones y correcciones que alguien necesitaría saber para no repetir el
mismo error o la misma pregunta.
