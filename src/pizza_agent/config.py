"""Datos fijos del local. No están en la base de datos porque cambian poco.

Editá estos valores a mano cuando cambien horarios, dirección o zona de
delivery. Si más adelante hace falta que se editen sin tocar código, se
puede migrar esto a una tabla `configuracion(clave, valor)` en Postgres.
"""

HORARIOS = "Lunes a jueves 19:00 a 23:00, viernes y sábado 19:00 a 00:00"
DIRECCION = "Cambiar por la dirección real del local"
ZONA_DELIVERY = "Cambiar por las zonas donde hacen delivery"
COSTO_DELIVERY = "Cambiar por el costo de envío"
TELEFONO_LOCAL = "Cambiar por el teléfono del local"
