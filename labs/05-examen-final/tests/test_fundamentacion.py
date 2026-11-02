"""Chequeo de fechas no fundamentadas: determinista, sin LLM."""

from fundamentacion import fechas, fechas_no_fundamentadas


def test_extrae_formatos_de_fecha():
    assert fechas("El parcial es el jueves 26 de noviembre de 2026.") == {(26, 11)}
    assert fechas('{"fecha": "2026-11-26", "evento": "Segundo parcial"}') == {(26, 11)}
    assert fechas("Será el 23/11.") == {(23, 11)}


def test_ignora_formato_tildes_y_espacios_duros():
    assert fechas("el **lunes 23 de Noviembre**") == {(23, 11)}


def test_fecha_inventada_se_detecta():
    fuentes = ["El segundo parcial se realizará el jueves 26 de noviembre de 2026 a las 6:00 p. m."]
    respuesta = "El simulacro es el lunes 23 de noviembre de 2026."
    assert fechas_no_fundamentadas(respuesta, fuentes) == {(23, 11)}


def test_fecha_presente_en_fuentes_no_se_marca():
    fuentes = ["El segundo parcial se realizará el jueves 26 de noviembre de 2026."]
    assert fechas_no_fundamentadas("El segundo parcial es el 26 de noviembre.", fuentes) == set()


def test_fecha_del_calendario_iso_cuenta_como_fuente():
    fuentes = ['[{"fecha": "2026-12-03", "evento": "Entrega del proyecto final"}]']
    assert fechas_no_fundamentadas("La entrega es el 3 de diciembre.", fuentes) == set()


def test_respuesta_sin_fechas():
    assert fechas_no_fundamentadas("Necesitas iniciar sesión con /login.", []) == set()
