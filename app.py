import io
import os
import re
import tempfile
import zipfile
import subprocess
import pandas as pd
from pypdf import PdfWriter
import streamlit as st

# Instalación de dependencias del sistema
try:
    subprocess.run(["playwright", "install", "chromium"], check=True)
except Exception:
    pass

from playwright.sync_api import sync_playwright
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

st.set_page_config(
    page_title="Consolidador de Testigos y PDFs",
    page_icon="📑",
    layout="centered"
)

URL_LOGIN_EENTREGA = "https://codess.e-entrega.co/index.php"

# --- Conexión con Google Drive API usando User OAuth Credentials ---

def obtener_servicio_drive():
    """Autentica a través de OAuth Refresh Token utilizando los permisos del usuario corporativo."""
    if "google_oauth" in st.secrets:
        creds_info = st.secrets["google_oauth"]
        creds = Credentials(
            token=None,
            refresh_token=creds_info["refresh_token"],
            token_uri=creds_info["token_uri"],
            client_id=creds_info["client_id"],
            client_secret=creds_info["client_secret"],
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        return build("drive", "v3", credentials=creds)
    return None

def buscar_y_descargar_drive_api(service, codigo_guia):
    """Busca y descarga un archivo PDF en cualquier Unidad Compartida usando permisos del usuario."""
    guia_limpia = str(codigo_guia).strip().replace(" ", "")
    if not service or not guia_limpia:
        return None

    try:
        query = f"name contains '{guia_limpia}' and mimeType = 'application/pdf' and trashed = false"
        resultados = service.files().list(
            q=query,
            fields="files(id, name)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()

        archivos = resultados.get("files", [])
        if archivos:
            file_id = archivos[0]["id"]
            nombre_archivo = archivos[0]["name"]

            request = service.files().get_media(fileId=file_id)
            stream_descarga = io.BytesIO()
            downloader = MediaIoBaseDownload(stream_descarga, request)
            
            done = False
            while not done:
                _, done = downloader.next_chunk()

            stream_descarga.seek(0)
            return {"nombre": nombre_archivo, "stream": stream_descarga}

    except Exception:
        pass

    return None

# --- Funciones de Apoyo y E-Entrega ---

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

# --- Interfaz Web en Streamlit ---

st.title("📑 Generador Automático de Testigos")
st.write("Ingresa tus credenciales de E-Entrega y sube el archivo con las guías.")

st.subheader("1. Credenciales E-Entrega")
col1, col2 = st.columns(2)
with col1:
    usr_eentrega = st.text_input("Correo E-Entrega", placeholder="ejemplo@codess.org.co")
with col2:
    pass_eentrega = st.text_input("Contraseña E-Entrega", type="password")

st.subheader("2. Archivo Excel/CSV")
archivo_subido = st.file_uploader("Selecciona el archivo Excel o CSV con las guías", type=["csv", "xlsx"])

if archivo_subido is not None:
    if not (usr_eentrega.strip() and pass_eentrega.strip()):
        st.warning("⚠️ Ingresa tus credenciales de E-Entrega para continuar.")
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

                with st.spinner("⏳ Procesando guías y consultando Drive... Por favor espera."):
                    progress_bar = st.progress(0)
                    drive_service = obtener_servicio_drive()

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
                                pdf_drive = buscar_y_descargar_drive_api(drive_service, guia_afiliado_limpia)
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
