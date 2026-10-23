from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

# ───────────────────────── Configuración ─────────────────────────
st.set_page_config(page_title="Control de caldera · Kenzo Jeans", page_icon="🔥", layout="wide")

TZ = ZoneInfo("America/Bogota")
NIVELES = ["Bajo", "Normal", "Alto"]

# AJUSTAR: unidad y rango de operación de la presión de vapor.
# Los valores de abajo son de EJEMPLO, reemplázalos por los de tu caldera.
PRESION_UNIDAD = "psi"
PRESION_MIN = 80.0
PRESION_MAX = 120.0

conn = st.connection("supabase", type="sql")


def consultar(sql, params=None, ttl=0):
    return conn.query(sql, params=params, ttl=ttl)


def ejecutar(sql, params):
    with conn.session as s:
        s.execute(text(sql), params)
        s.commit()


def avisar(tipo, mensaje):
    """Guarda un mensaje para mostrarlo después del st.rerun()."""
    st.session_state["aviso"] = (tipo, mensaje)


def mostrar_aviso():
    if "aviso" in st.session_state:
        tipo, mensaje = st.session_state.pop("aviso")
        getattr(st, tipo)(mensaje)


def ahora():
    return datetime.now(TZ).replace(second=0, microsecond=0)


# ───────────────────────── Consultas SQL ─────────────────────────
SQL_CONSUMO = """
SELECT fecha, operario, hora_encendido, gas_fin - gas_inicio AS consumo_gas
FROM jornadas
WHERE fecha BETWEEN :d1 AND :d2
ORDER BY fecha
"""

SQL_LECTURAS = """
SELECT l.fecha_hora, l.presion_vapor, l.nivel_agua, j.operario
FROM lecturas l
JOIN jornadas j ON j.id = l.jornada_id
WHERE j.fecha BETWEEN :d1 AND :d2
ORDER BY l.fecha_hora
"""

SQL_PURGAS = """
SELECT j.fecha, COUNT(p.id) AS purgas
FROM jornadas j
LEFT JOIN purgas_fondo p ON p.jornada_id = j.id
WHERE j.fecha BETWEEN :d1 AND :d2
GROUP BY j.fecha
ORDER BY j.fecha
"""


# ───────────────────────── Pestaña: registro ─────────────────────────
def pestana_registro():
    mostrar_aviso()
    hoy = ahora()
    fecha = st.date_input("Fecha de la jornada", value=hoy.date(), max_value=hoy.date())

    jor = consultar("SELECT * FROM jornadas WHERE fecha = :f", {"f": fecha})

    # 1) Sin jornada: abrirla
    if jor.empty:
        st.info("Todavía no hay jornada para esta fecha. Ábrela para empezar a registrar.")
        with st.form("apertura"):
            operario = st.text_input("Operario")
            hora_enc = st.time_input("Hora de encendido", value=hoy.time())
            gas_ini = st.number_input("Lectura inicial del medidor de gas", min_value=0.0, step=1.0)
            obs = st.text_area("Observaciones", height=70)
            if st.form_submit_button("Abrir jornada", type="primary"):
                if not operario.strip():
                    st.error("Escribe el nombre del operario.")
                else:
                    ejecutar(
                        """INSERT INTO jornadas (fecha, operario, hora_encendido, gas_inicio, observaciones)
                           VALUES (:f, :o, :h, :g, :obs)""",
                        {"f": fecha, "o": operario.strip(), "h": hora_enc, "g": gas_ini, "obs": obs.strip() or None},
                    )
                    avisar("success", "Jornada abierta.")
                    st.rerun()
        return

    # 2) Jornada existente
    j = jor.iloc[0]
    jid = int(j["id"])
    cerrada = pd.notna(j["gas_fin"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Operario", j["operario"])
    c2.metric("Encendido", str(j["hora_encendido"])[:5] if pd.notna(j["hora_encendido"]) else "—")
    c3.metric("Gas inicial", f"{j['gas_inicio']:g}")
    c4.metric("Estado", "Cerrada" if cerrada else "Abierta")
    if cerrada:
        st.success(f"Jornada cerrada. Consumo de gas: {j['gas_fin'] - j['gas_inicio']:g}")

    if not cerrada:
        col_a, col_b = st.columns(2)

        with col_a:
            st.subheader("Nueva lectura")
            with st.form("lectura", clear_on_submit=True):
                presion = st.number_input(f"Presión de vapor ({PRESION_UNIDAD})", min_value=0.0, step=0.5)
                nivel = st.selectbox("Nivel de agua", NIVELES, index=1)
                obs_l = st.text_input("Observaciones (opcional)")
                if st.form_submit_button("Guardar lectura", type="primary"):
                    ejecutar(
                        """INSERT INTO lecturas (jornada_id, fecha_hora, presion_vapor, nivel_agua, observaciones)
                           VALUES (:j, :fh, :p, :n, :o)""",
                        {"j": jid, "fh": ahora().replace(tzinfo=None), "p": presion, "n": nivel, "o": obs_l.strip() or None},
                    )
                    alertas = []
                    if presion < PRESION_MIN or presion > PRESION_MAX:
                        alertas.append(f"presión fuera de rango ({PRESION_MIN:g}–{PRESION_MAX:g} {PRESION_UNIDAD})")
                    if nivel != "Normal":
                        alertas.append(f"nivel de agua {nivel.lower()}")
                    if alertas:
                        avisar("warning", "Lectura guardada, pero revisa: " + " y ".join(alertas) + ".")
                    else:
                        avisar("success", "Lectura guardada.")
                    st.rerun()

        with col_b:
            st.subheader("Purga de fondo")
            with st.form("purga", clear_on_submit=True):
                hora_p = st.time_input("Hora de la purga", value=ahora().time())
                if st.form_submit_button("Registrar purga", type="primary"):
                    ejecutar(
                        "INSERT INTO purgas_fondo (jornada_id, hora_purga) VALUES (:j, :h)",
                        {"j": jid, "h": hora_p},
                    )
                    avisar("success", "Purga registrada.")
                    st.rerun()

        st.subheader("Cerrar jornada")
        with st.form("cierre"):
            gas_fin = st.number_input(
                "Lectura final del medidor de gas", min_value=0.0, value=float(j["gas_inicio"]), step=1.0
            )
            obs_c = st.text_area("Observaciones de cierre", height=70)
            if st.form_submit_button("Cerrar jornada"):
                if gas_fin < float(j["gas_inicio"]):
                    st.error("La lectura final no puede ser menor que la inicial.")
                else:
                    ejecutar(
                        """UPDATE jornadas
                           SET gas_fin = :g,
                               observaciones = CONCAT_WS(' | ', NULLIF(observaciones, ''), NULLIF(:o, ''))
                           WHERE id = :j""",
                        {"g": gas_fin, "o": obs_c.strip(), "j": jid},
                    )
                    avisar("success", "Jornada cerrada.")
                    st.rerun()

    # Detalle del día
    st.divider()
    st.subheader("Registros del día")
    lect = consultar(
        """SELECT fecha_hora AS "Hora", presion_vapor AS "Presión", nivel_agua AS "Nivel de agua",
                  observaciones AS "Observaciones"
           FROM lecturas WHERE jornada_id = :j ORDER BY fecha_hora""",
        {"j": jid},
    )
    purg = consultar(
        'SELECT hora_purga AS "Hora de purga" FROM purgas_fondo WHERE jornada_id = :j ORDER BY hora_purga',
        {"j": jid},
    )
    t1, t2 = st.columns([3, 1])
    with t1:
        st.dataframe(lect, hide_index=True, use_container_width=True)
    with t2:
        st.dataframe(purg, hide_index=True, use_container_width=True)


# ───────────────────────── Pestaña: dashboard ─────────────────────────
def pestana_dashboard():
    hoy = ahora().date()
    rango = st.date_input("Periodo", value=(hoy - timedelta(days=29), hoy), max_value=hoy)
    if len(rango) < 2:
        st.caption("Selecciona la fecha final para ver el periodo.")
        return
    d1, d2 = rango
    p = {"d1": d1, "d2": d2}

    consumo = consultar(SQL_CONSUMO, p, ttl=60)
    lect = consultar(SQL_LECTURAS, p, ttl=60)
    purg = consultar(SQL_PURGAS, p, ttl=60)

    if consumo.empty and lect.empty:
        st.info("No hay datos en este periodo.")
        return

    fuera = int(((lect["presion_vapor"] < PRESION_MIN) | (lect["presion_vapor"] > PRESION_MAX)).sum()) if not lect.empty else 0
    nivel_bajo = int((lect["nivel_agua"] == "Bajo").sum()) if not lect.empty else 0
    cons_prom = consumo["consumo_gas"].dropna().mean() if not consumo.empty else None

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Consumo de gas promedio por día", f"{cons_prom:,.1f}" if pd.notna(cons_prom) else "—")
    k2.metric("Última presión", f"{lect['presion_vapor'].iloc[-1]:g} {PRESION_UNIDAD}" if not lect.empty else "—")
    k3.metric("Lecturas de presión fuera de rango", fuera)
    k4.metric("Lecturas con nivel de agua bajo", nivel_bajo)

    g1, g2 = st.columns(2)
    with g1:
        st.subheader("Consumo de gas por día")
        c = consumo.dropna(subset=["consumo_gas"])
        if c.empty:
            st.caption("Aún no hay jornadas cerradas en este periodo.")
        else:
            fig = px.bar(c, x="fecha", y="consumo_gas", labels={"fecha": "", "consumo_gas": "Consumo"})
            st.plotly_chart(fig, use_container_width=True)
    with g2:
        st.subheader("Purgas de fondo por día")
        fig = px.bar(purg, x="fecha", y="purgas", labels={"fecha": "", "purgas": "Purgas"})
        st.plotly_chart(fig, use_container_width=True)

    if not lect.empty:
        st.subheader("Presión de vapor")
        fig = px.line(lect, x="fecha_hora", y="presion_vapor", markers=True,
                      labels={"fecha_hora": "", "presion_vapor": f"Presión ({PRESION_UNIDAD})"})
        fig.add_hline(y=PRESION_MIN, line_dash="dash", line_color="#c0392b")
        fig.add_hline(y=PRESION_MAX, line_dash="dash", line_color="#c0392b")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Nivel de agua")
        fig = px.scatter(
            lect, x="fecha_hora", y="nivel_agua", color="nivel_agua",
            category_orders={"nivel_agua": NIVELES},
            color_discrete_map={"Bajo": "#c0392b", "Normal": "#2e8b57", "Alto": "#e0a100"},
            labels={"fecha_hora": "", "nivel_agua": ""},
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with st.expander("Ver las consultas SQL de esta pantalla"):
        for nombre, sql in [("Consumo por día", SQL_CONSUMO), ("Lecturas (con JOIN)", SQL_LECTURAS), ("Purgas por día (LEFT JOIN + GROUP BY)", SQL_PURGAS)]:
            st.caption(nombre)
            st.code(sql.strip(), language="sql")


# ───────────────────────── Principal ─────────────────────────
st.title("Control de parámetros de caldera")
tab_reg, tab_dash = st.tabs(["Registro", "Dashboard"])
with tab_reg:
    pestana_registro()
with tab_dash:
    pestana_dashboard()
