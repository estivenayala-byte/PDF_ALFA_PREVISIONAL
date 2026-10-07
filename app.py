import io
import os
import re
import tempfile
import zipfile
import subprocess
import requests
import pandas as pd
from pypdf import PdfWriter
import streamlit as st

# Instalación de Chromium en la nube
try:
    subprocess.run(["playwright", "install", "chromium"], check=True)
except Exception:
    pass

from playwright.sync_api import sync_playwright

st.set_page_config(
    page_title="Consolidador de Testigos y PDFs",
    page_icon="📑",
    layout="centered"
)

URL_LOGIN_EENTREGA = "https://codess.e-entrega.co/index.php"
URL_DRIVE_FOLDER = "https://drive.google.com/drive/folders/1apUji6mHZ2z_Fm1q4OK3Y6tUoeuu-diX"

# Funciones de Limpieza
def es_guia_valida(valor):
    if not valor or pd.isna(valor):
        return False
    texto = str(valor).strip().upper()
    return texto not in ["", "N/A", "NA", "NAN", "NONE", "NULL", "-"]

def limpiar_guia(valor):
    if not valor:
        return ""
    return str(valor).strip().replace(" ", "")

def limpiar_nombre_carpeta(nombre):
    if not nombre or pd.isna(nombre):
        return "SIN_SERVICIO"
    texto = str(nombre).strip().upper()
    texto_limpio = re.sub(r'[\\/*?:"<>|]', "_", texto)
    return texto_limpio if texto_limpio else "SIN_SERVICIO"

def descargar_pdf_drive_directo(codigo_guia):
    """
    Descarga directamente desde Google Drive sin requerir login ni sufrir bloqueos por IP.
    """
    guia_limpia = limpiar_guia(codigo_guia)
    
    # Intentar búsqueda en el índice de vistas previas / descargas de Google
    search_url = f"https://drive.google.com/embeddedfolderview?id=1apUji6mHZ2z_Fm1q4OK3Y6tUoeuu-diX#list"
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        resp = session.get(search_url, timeout=15)
        
        # Buscar el ID del archivo que contenga el código de la guía
        matches = re.findall(r'data-id="([a-zA-Z0-9_-]{25,45})"[^>]*>.*?(' + re.escape(guia_limpia) + r'[^<]*\.pdf)', resp.text, re.IGNORECASE | re.DOTALL)
        
        if not matches:
            # Búsqueda secundaria en el HTML renderizado
            matches_id = re.findall(r'/file/d/([a-zA-Z0-9_-]{25,45})', resp.text)
            for file_id in set(matches_id):
                url_dl = f"https://drive.google.com/uc?export=download&id={file_id}"
                head_resp = session.get(url_dl, stream=True, timeout=10)
                cd = head_resp.headers.get('Content-Disposition', '')
                if guia_limpia in cd or guia_limpia in head_resp.text:
                    pdf_bytes = session.get(url_dl).content
                    return {"nombre": f"{guia_limpia}.pdf", "stream": io.BytesIO(pdf_bytes)}

        if matches:
            file_id, nombre_archivo = matches[0][0], matches[0][1]
            url_dl = f"https://drive.google.com/uc?export=download&id={file_id}"
            pdf_bytes = session.get(url_dl).content
            return {"nombre": nombre_archivo if nombre_archivo else f"{guia_limpia}.pdf", "stream": io.BytesIO(pdf_bytes)}

    except Exception:
        pass
    
    return None

def esperar_modal_generando_testigo(page):
    try:
        page.wait_for_selector('text="Generando testigo, espera un momento..."', state="visible", timeout=5000)
        page.wait_for_selector('text="Generando testigo, espera un momento..."', state="detached", timeout=60000)
    except Exception:
        pass

def obtener_evento_tabla(page):
    try:
        page.wait_for_selector("#tablaestados tbody tr", timeout=5000)
        texto = page.locator("#tablaestados tbody tr").first.locator("td").nth(4).inner_text().strip()
        return texto if texto else "Sin Estado"
    except Exception:
        return "No encontrado"

def descargar_testigo_en_memoria(page, reintentos=3):
    for intento in range(1, reintentos + 1):
        try:
            page.wait_for_selector("#ToolTables_tablaestados_1", state="visible", timeout=10000)
            with page.expect_download(timeout=60000) as download_info:
                page.locator("#ToolTables_tablaestados_1").click()

            esperar_modal_generando_testigo(page)
            page.wait_for_load_state("networkidle")

            download_file = download_info.value
            with open(download_file.path(), "rb") as f:
                bytes_pdf = f.read()

            return {
                "nombre": download_file.suggested_filename,
                "stream": io.BytesIO(bytes_pdf)
            }
        except Exception:
            esperar_modal_generando_testigo(page)
            try:
                page.locator("#btn_Buscar").click()
                page.wait_for_load_state("networkidle")
            except Exception:
                pass
    return None

# Interfaz Web (Streamlit)
st.title("📑 Generador Automático de Testigos")
st.write("Ingresa tus credenciales de E-Entrega y sube el archivo con las guías.")

st.subheader("1. Credenciales E-Entrega")
col1, col2 = st.columns(2)
with col1:
    usr_eentrega = st.text_input("Correo E-Entrega", placeholder="ejemplo@codess.org.co")
with col2:
    pass_eentrega = st.text_input("Contraseña E-Entrega", type="password")

st.subheader("2. Archivo Excel/CSV")
archivo_subido = st.file_uploader("Selecciona el archivo con las guías", type=["csv", "xlsx"])

if archivo_subido is not None:
    if not (usr_eentrega.strip() and pass_eentrega.strip()):
        st.warning("⚠️ Debes ingresar tus credenciales de E-Entrega.")
    else:
        st.success(f"Archivo listo: **{archivo_subido.name}**")

        if st.button("🚀 Iniciar Procesamiento", type="primary"):
            with tempfile.TemporaryDirectory() as dir_trabajo:
                ruta_input = os.path.join(dir_trabajo, archivo_subido.name)
                with open(ruta_input, "wb") as f:
                    f.write(archivo_subido.getbuffer())

                if archivo_subido.name.endswith(".csv"):
                    df = pd.read_csv(ruta_input, sep=";", dtype=str)
                else:
                    df = pd.read_excel(ruta_input, dtype=str)

                entregas_afiliado, entregas_eps, entregas_empleado, entregas_arl = [], [], [], []

                with st.spinner("⏳ Extrayendo datos y procesando guías... Por favor espera."):
                    progress_bar = st.progress(0)

                    with sync_playwright() as p:
                        browser = p.chromium.launch(
                            headless=True,
                            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
                        )
                        context = browser.new_context(accept_downloads=True)
                        page_eentrega = context.new_page()

                        # Login en E-Entrega
                        page_eentrega.goto(URL_LOGIN_EENTREGA)
                        page_eentrega.locator("#user").fill(usr_eentrega.strip())
                        page_eentrega.locator("#pass").fill(pass_eentrega.strip())
                        page_eentrega.locator("#login").click()
                        
                        login_exitoso = False
                        try:
                            page_eentrega.wait_for_selector('span[lan="MENU_STATUS"], .alert, #login_error', timeout=15000)
                            if page_eentrega.locator('span[lan="MENU_STATUS"]').is_visible():
                                login_exitoso = True
                        except Exception:
                            pass

                        if not login_exitoso:
                            st.error("❌ No se pudo iniciar sesión en E-Entrega. Verifica tus credenciales.")
                            browser.close()
                            st.stop()

                        page_eentrega.locator('span[lan="MENU_STATUS"]').click()
                        page_eentrega.wait_for_selector('text="Filtros avanzados"', timeout=15000)
                        page_eentrega.get_by_text("Filtros avanzados").click()

                        total_filas = len(df)
                        for idx, row in df.iterrows():
                            NUMERO_DOCUMENTO = str(row["DOCUMENTO"]).strip()
                            GUIA_AFILIADO = str(row.get("GUIA AFILIADO", "")).strip()
                            GUIA_EPS = str(row.get("GUIA EPS", "")).strip()
                            GUIA_EMPLEADOR = str(row.get("GUIA EMPLEADOR", "")).strip()
                            GUIA_ARL = str(row.get("GUIA ARL", "")).strip()
                            NOMBRE_SERVICIO = limpiar_nombre_carpeta(row.get("SERVICIO", ""))

                            progress_bar.progress((idx + 1) / total_filas)

                            if not es_guia_valida(GUIA_AFILIADO):
                                entregas_afiliado.append("N/A - Guía Inválida")
                                entregas_eps.append("N/A - Omitido por Afiliado")
                                entregas_empleado.append("N/A - Omitido por Afiliado")
                                entregas_arl.append("N/A - Omitido por Afiliado")
                                continue

                            archivos_adjuntos_afiliado = []
                            testigo_afiliado = None

                            guia_afiliado_limpia = limpiar_guia(GUIA_AFILIADO)
                            if len(guia_afiliado_limpia) > 6:
                                pdf_drive = descargar_pdf_drive_directo(guia_afiliado_limpia)
                                if pdf_drive:
                                    testigo_afiliado = pdf_drive
                                    entregas_afiliado.append("Encontrado en Drive")
                                else:
                                    entregas_afiliado.append("No encontrado en Drive")
                            else:
                                page_eentrega.bring_to_front()
                                page_eentrega.locator("#message").fill(guia_afiliado_limpia)
                                page_eentrega.locator("#btn_Buscar").click()
                                page_eentrega.wait_for_load_state("networkidle")

                                txt_afiliado = obtener_evento_tabla(page_eentrega)
                                entregas_afiliado.append(txt_afiliado)

                                try:
                                    page_eentrega.locator(".btnVerMensaje").first.click()
                                    page_eentrega.wait_for_selector(".modal-body, #modalMensaje, .modal-content", state="visible", timeout=10000)

                                    archivos_adjuntos = page_eentrega.locator('text=/.+\\.pdf/i')
                                    for i in range(archivos_adjuntos.count()):
                                        with page_eentrega.expect_download(timeout=30000) as download_info:
                                            archivos_adjuntos.nth(i).click()

                                        download = download_info.value
                                        with open(download.path(), "rb") as f:
                                            bytes_pdf = f.read()

                                        archivos_adjuntos_afiliado.append({
                                            "nombre": download.suggested_filename,
                                            "stream": io.BytesIO(bytes_pdf)
                                        })

                                    page_eentrega.get_by_role("button", name="Aceptar").click()
                                    page_eentrega.wait_for_timeout(500)
                                except Exception:
                                    pass

                                testigo_afiliado = descargar_testigo_en_memoria(page_eentrega)

                            guia_testigos = [("EPS", GUIA_EPS), ("EMPLEADOR", GUIA_EMPLEADOR), ("ARL", GUIA_ARL)]
                            otros_testigos = []

                            for nombre_entidad, codigo_guia in guia_testigos:
                                if es_guia_valida(codigo_guia):
                                    guia_limpia = limpiar_guia(codigo_guia)
                                    if len(guia_limpia) > 6:
                                        pdf_drive = descargar_pdf_drive_directo(guia_limpia)
                                        if pdf_drive:
                                            otros_testigos.append(pdf_drive)
                                            txt_estado = "Encontrado en Drive"
                                        else:
                                            txt_estado = "No encontrado en Drive"
                                    else:
                                        page_eentrega.bring_to_front()
                                        page_eentrega.locator("#message").fill(guia_limpia)
                                        page_eentrega.locator("#btn_Buscar").click()
                                        page_eentrega.wait_for_load_state("networkidle")

                                        txt_estado = obtener_evento_tabla(page_eentrega)

                                        testigo_entidad = descargar_testigo_en_memoria(page_eentrega)
                                        if testigo_entidad:
                                            otros_testigos.append(testigo_entidad)

                                    if nombre_entidad == "EPS": entregas_eps.append(txt_estado)
                                    elif nombre_entidad == "EMPLEADOR": entregas_empleado.append(txt_estado)
                                    elif nombre_entidad == "ARL": entregas_arl.append(txt_estado)
                                else:
                                    if nombre_entidad == "EPS": entregas_eps.append("N/A")
                                    elif nombre_entidad == "EMPLEADOR": entregas_empleado.append("N/A")
                                    elif nombre_entidad == "ARL": entregas_arl.append("N/A")

                            archivos_oficio = [f for f in archivos_adjuntos_afiliado if "OFICIO" in f["nombre"].upper()]
                            otros_adjuntos_afiliado = [f for f in archivos_adjuntos_afiliado if f not in archivos_oficio]

                            lista_ordenada_streams = (
                                archivos_oficio +
                                otros_adjuntos_afiliado +
                                ([testigo_afiliado] if testigo_afiliado else []) +
                                otros_testigos
                            )

                            if lista_ordenada_streams:
                                merger = PdfWriter()
                                for item in lista_ordenada_streams:
                                    if item and item.get("stream"):
                                        merger.append(item["stream"])

                                carpeta_servicio = os.path.join(dir_trabajo, "Resultados_PDF", NOMBRE_SERVICIO)
                                os.makedirs(carpeta_servicio, exist_ok=True)

                                ruta_pdf_final = os.path.join(carpeta_servicio, f"{NUMERO_DOCUMENTO}.pdf")
                                merger.write(ruta_pdf_final)
                                merger.close()

                        browser.close()

                    df["Entrega_Afiliado"] = entregas_afiliado
                    df["Entrega_EPS"] = entregas_eps
                    df["Entrega_Empleado"] = entregas_empleado
                    df["Entrega_ARL"] = entregas_arl

                    ruta_excel_salida = os.path.join(dir_trabajo, "Resultados_PDF", "Resultado_Entregas.xlsx")
                    os.makedirs(os.path.dirname(ruta_excel_salida), exist_ok=True)
                    df.to_excel(ruta_excel_salida, index=False)

                    ruta_zip_salida = os.path.join(dir_trabajo, "Resultados_PDF.zip")
                    carpeta_a_zipear = os.path.join(dir_trabajo, "Resultados_PDF")

                    with zipfile.ZipFile(ruta_zip_salida, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        for root, dirs, files in os.walk(carpeta_a_zipear):
                            for file in files:
                                path_absoluto = os.path.join(root, file)
                                path_relativo = os.path.relpath(path_absoluto, carpeta_a_zipear)
                                zipf.write(path_absoluto, arcname=path_relativo)

                st.success("🎉 ¡Proceso finalizado con éxito!")

                with open(ruta_zip_salida, "rb") as f_zip:
                    st.download_button(
                        label="📦 Descargar Resultados_PDF.zip",
                        data=f_zip.read(),
                        file_name="Resultados_PDF.zip",
                        mime="application/zip"
                    )
