import io
import os
import re
import json
import tempfile
import zipfile
import subprocess
import pandas as pd
from pypdf import PdfWriter
import streamlit as st

# Instalación automática de Chromium
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

def obtener_cookies_google():
    if "google_session" in st.secrets and "cookies" in st.secrets["google_session"]:
        try:
            return json.loads(st.secrets["google_session"]["cookies"])
        except Exception:
            pass
    if os.path.exists("cookies_google.json"):
        try:
            with open("cookies_google.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

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

def obtener_o_crear_pestaña_drive(context, page_drive):
    if page_drive is None or page_drive.is_closed():
        page_drive = context.new_page()
        page_drive.goto(URL_DRIVE_FOLDER, wait_until="commit", timeout=45000)
    return page_drive

def buscar_y_descargar_drive_web(context, page_drive, codigo_guia):
    guia_limpia = limpiar_guia(codigo_guia)
    try:
        page_drive = obtener_o_crear_pestaña_drive(context, page_drive)
        page_drive.bring_to_front()

        if URL_DRIVE_FOLDER not in page_drive.url:
            page_drive.goto(URL_DRIVE_FOLDER, wait_until="commit", timeout=45000)

        input_busqueda = page_drive.locator('input[name="q"], input[aria-label*="Buscar"], input[aria-label*="Search"]').first
        input_busqueda.wait_for(state="visible", timeout=25000)
        input_busqueda.click()

        page_drive.keyboard.press("Control+A")
        page_drive.keyboard.press("Backspace")
        input_busqueda.fill(guia_limpia)
        page_drive.keyboard.press("Enter")

        page_drive.wait_for_timeout(3000)

        selector_pdf = page_drive.locator(f'text=/{guia_limpia}.*\\.pdf/i, text=/.*\\.pdf.*{guia_limpia}/i').first
        if selector_pdf.count() == 0:
            selector_pdf = page_drive.locator(f'text=/{guia_limpia}/i').first

        if selector_pdf.count() > 0:
            selector_pdf.scroll_into_view_if_needed()
            box = selector_pdf.bounding_box()
            if box:
                center_x = box["x"] + box["width"] / 2
                center_y = box["y"] + box["height"] / 2
                page_drive.mouse.click(center_x, center_y)
                page_drive.wait_for_timeout(500)

            file_id = selector_pdf.evaluate(f"""el => {{
                const extraerDeUrl = (str) => {{
                    if (!str) return null;
                    const m = str.match(/(?:\\/d\\/|id=)([a-zA-Z0-9_-]{{25,45}})/);
                    return m ? m[1] : null;
                }};

                let curr = el;
                while (curr && curr !== document.body) {{
                    const idAttr = curr.getAttribute('data-target-id') || 
                                   curr.getAttribute('data-id') || 
                                   curr.getAttribute('data-legacy-id');
                    if (idAttr && idAttr.length >= 25) return idAttr;

                    const href = curr.getAttribute('href');
                    const idFromHref = extraerDeUrl(href);
                    if (idFromHref) return idFromHref;

                    curr = curr.parentElement;
                }}

                const filaActiva = document.querySelector('[aria-selected="true"], [role="row"][aria-selected="true"]');
                if (filaActiva) {{
                    const idFila = filaActiva.getAttribute('data-target-id') || 
                                   filaActiva.getAttribute('data-id') || 
                                   filaActiva.getAttribute('data-legacy-id');
                    if (idFila && idFila.length >= 25) return idFila;

                    const enlaceInterno = filaActiva.querySelector('a[href]');
                    if (enlaceInterno) {{
                        const idHref = extraerDeUrl(enlaceInterno.getAttribute('href'));
                        if (idHref) return idHref;
                    }}
                }}

                return null;
            }}""")

            if file_id:
                url_descarga_directa = f"https://drive.google.com/uc?export=download&id={file_id}"
                try:
                    with page_drive.expect_download(timeout=60000) as download_info:
                        page_drive.evaluate(f"window.location.href = '{url_descarga_directa}'")

                    download_file = download_info.value
                    with open(download_file.path(), "rb") as f:
                        bytes_pdf = f.read()

                    return {
                        "nombre": download_file.suggested_filename if download_file.suggested_filename else f"{guia_limpia}.pdf",
                        "stream": io.BytesIO(bytes_pdf)
                    }, page_drive

                except Exception:
                    response = context.request.get(url_descarga_directa)
                    if response.status == 200:
                        bytes_pdf = response.body()
                        return {
                            "nombre": f"{guia_limpia}.pdf",
                            "stream": io.BytesIO(bytes_pdf)
                        }, page_drive

        return None, page_drive

    except Exception:
        return None, page_drive

# Interfaz Web
st.title("📑 Generador Automático de Testigos")
st.write("Ingresa tus credenciales de E-Entrega y sube el archivo de guías.")

st.subheader("1. Credenciales E-Entrega")
col1, col2 = st.columns(2)
with col1:
    usr_eentrega = st.text_input("Correo E-Entrega", value="analista.ml@codess.org.co")
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

                with st.spinner("⏳ Extrayendo datos y consolidando archivos PDF... Por favor espera."):
                    progress_bar = st.progress(0)

                    with sync_playwright() as p:
                        browser = p.chromium.launch(
                            headless=True,
                            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
                        )
                        
                        context = browser.new_context(accept_downloads=True)

                        # Cargar las cookies autenticadas de Google Drive
                        cookies_google = obtener_cookies_google()
                        if cookies_google:
                            try:
                                context.add_cookies(cookies_google)
                            except Exception:
                                pass

                        page_eentrega = context.new_page()
                        page_drive = None

                        page_eentrega.goto(URL_LOGIN_EENTREGA)
                        page_eentrega.locator("#user").fill(usr_eentrega.strip())
                        page_eentrega.locator("#pass").fill(pass_eentrega.strip())
                        page_eentrega.locator("#login").click()
                        page_eentrega.wait_for_load_state("networkidle")

                        page_eentrega.wait_for_selector('span[lan="MENU_STATUS"]', timeout=30000)
                        page_eentrega.locator('span[lan="MENU_STATUS"]').click()
                        page_eentrega.wait_for_load_state("networkidle")
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
                                pdf_drive, page_drive = buscar_y_descargar_drive_web(context, page_drive, guia_afiliado_limpia)
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
                                        pdf_drive, page_drive = buscar_y_descargar_drive_web(context, page_drive, guia_limpia)
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
