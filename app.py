import io
import os
import re
import tempfile
import zipfile
import subprocess
import pandas as pd
from pypdf import PdfWriter
import streamlit as st

# Instalación de dependencias del sistema (Chromium)
try:
    subprocess.run(["playwright", "install", "chromium"], check=True)
except Exception:
    pass

from playwright.sync_api import sync_playwright
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# --- Configuración de la Página ---
st.set_page_config(
    page_title="Consolidador de Testigos - Codess",
    page_icon="📑",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Estilos CSS Personalizados Modernos ---
st.markdown("""
    <style>
    /* Estilos Generales y Colores Primarios */
    :root {
        --primary-color: #1E88E5;
        --background-color: #F8FAFC;
        --card-background: #FFFFFF;
    }
    
    .stApp {
        background-color: var(--background-color);
    }
    
    /* Encabezado Principal */
    .header-container {
        background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%);
        padding: 2.5rem 2rem;
        border-radius: 16px;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);
    }
    
    .header-title {
        font-size: 2.2rem;
        font-weight: 700;
        margin: 0;
        color: #F8FAFC;
    }
    
    .header-subtitle {
        font-size: 1.05rem;
        color: #94A3B8;
        margin-top: 0.5rem;
    }
    
    /* Tarjetas de Contenedor */
    .css-card {
        background-color: #FFFFFF;
        padding: 1.8rem;
        border-radius: 12px;
        border: 1px solid #E2E8F0;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
        margin-bottom: 1.5rem;
    }
    
    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #FFFFFF;
        border-right: 1px solid #E2E8F0;
    }
    
    /* Botón Principal */
    .stButton>button {
        width: 100%;
        background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%);
        color: white;
        font-weight: 600;
        padding: 0.75rem 1.5rem;
        border-radius: 8px;
        border: none;
        transition: all 0.2s ease;
        box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2);
    }
    
    .stButton>button:hover {
        transform: translateY(-1px);
        box-shadow: 0 6px 16px rgba(37, 99, 235, 0.3);
    }
    </style>
""", unsafe_allow_html=True)

URL_LOGIN_EENTREGA = "https://codess.e-entrega.co/index.php"

# --- Conexión y Diagnóstico de Google Drive API ---

def obtener_servicio_drive():
    """Autentica con OAuth2 utilizando el refresh_token del usuario corporativo."""
    if "google_oauth" in st.secrets:
        try:
            creds_info = st.secrets["google_oauth"]
            creds = Credentials(
                token=None,
                refresh_token=creds_info["refresh_token"],
                token_uri=creds_info.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=creds_info["client_id"],
                client_secret=creds_info["client_secret"],
                scopes=["https://www.googleapis.com/auth/drive.readonly"]
            )
            return build("drive", "v3", credentials=creds)
        except Exception as e:
            st.sidebar.error(f"Error cargando credenciales: {e}")
            return None
    return None

def verificar_conexion_drive(service):
    """Verifica en el sidebar si la conexión con Drive está lista y funcionando."""
    if service:
        try:
            about = service.about().get(fields="user").execute()
            email_conectado = about.get("user", {}).get("emailAddress", "Usuario")
            st.sidebar.success(f"🟢 **Drive Conectado**\n\n`{email_conectado}`")
            return True
        except Exception as e:
            st.sidebar.error(f"🔴 **Error de Conexión:**\n\n{e}")
            return False
    else:
        st.sidebar.warning("⚠️ Sin configuración de `[google_oauth]`")
        return False

def buscar_y_descargar_drive_api(service, codigo_guia):
    """Busca y descarga un archivo PDF en carpetas y Unidades Compartidas."""
    if not service or not codigo_guia:
        return None

    guia_limpia = re.sub(r'[^0-9a-zA-Z]', '', str(codigo_guia).strip())
    if not guia_limpia:
        return None

    try:
        query = f"name contains '{guia_limpia}' and mimeType = 'application/pdf' and trashed = false"
        
        resultados = service.files().list(
            q=query,
            fields="files(id, name)",
            corpora="allDrives",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=10
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

    except Exception as e:
        st.write(f"⚠️ Error al consultar la guía {guia_limpia} en Drive: {e}")

    return None

# --- Funciones Auxiliares ---

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

# --- Panel Lateral (Sidebar) ---
with st.sidebar:
    st.image("https://img.icons8.com/color/96/google-drive--v1.png", width=50)
    st.title("Panel de Control")
    st.markdown("---")
    drive_service = obtener_servicio_drive()
    conexion_ok = verificar_conexion_drive(drive_service)
    st.markdown("---")
    st.caption("🤖 **Codess Automation Tool**\nVersion 2.0 - Cloud")

# --- Encabezado Principal ---
st.markdown("""
    <div class="header-container">
        <h1 class="header-title">Consolidador de Testigos y Evidencias</h1>
        <p class="header-subtitle">Gestión automatizada de guías en E-Entrega y Google Drive</p>
    </div>
""", unsafe_allow_html=True)

# --- Sección de Entradas de Usuario ---
col_left, col_right = st.columns(2)

with col_left:
    st.markdown("### 🔐 Credenciales E-Entrega")
    usr_eentrega = st.text_input("Correo electrónico", placeholder="ejemplo@codess.org.co")
    pass_eentrega = st.text_input("Contraseña", type="password")

with col_right:
    st.markdown("### 📄 Carga de Datos")
    archivo_subido = st.file_uploader("Subir Excel o CSV con Guías", type=["csv", "xlsx"])

st.markdown("<br>", unsafe_allow_html=True)

# --- Botón de Ejecución y Procesamiento ---
if archivo_subido is not None:
    if not (usr_eentrega.strip() and pass_eentrega.strip()):
        st.warning("⚠️ Ingresa tus credenciales de E-Entrega para habilitar el procesamiento.")
    else:
        st.info(f"📁 Archivo cargado correctamente: **{archivo_subido.name}**")

        if st.button("🚀 Iniciar Procesamiento Automático"):
            with tempfile.TemporaryDirectory() as dir_trabajo:
                ruta_input = os.path.join(dir_trabajo, archivo_subido.name)
                with open(ruta_input, "wb") as f:
                    f.write(archivo_subido.getbuffer())

                if archivo_subido.name.endswith(".csv"):
                    df = pd.read_csv(ruta_input, sep=";", dtype=str)
                else:
                    df = pd.read_excel(ruta_input, dtype=str)

                entregas_afiliado, entregas_eps, entregas_empleado, entregas_arl = [], [], [], []

                status_container = st.status("🔄 **Ejecutando automatización...**", expanded=True)
                
                with status_container:
                    st.write("🌐 Conectando a E-Entrega con Chromium en la nube...")
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

                            st.write(f"🔎 Procesando registro {idx + 1}/{total_filas} - Documento: **{NUMERO_DOCUMENTO}**")
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
                                        try:
                                            with page_eentrega.expect_download(timeout=30000) as download_info:
                                                archivos_adjuntos.nth(i).click()

                                            download = download_info.value
                                            with open(download.path(), "rb") as f:
                                                bytes_pdf = f.read()

                                            archivos_adjuntos_afiliado.append({
                                                "nombre": download.suggested_filename,
                                                "stream": io.BytesIO(bytes_pdf)
                                            })
                                        except Exception:
                                            pass

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
                                        pdf_drive = buscar_y_descargar_drive_api(drive_service, guia_limpia)
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

                    # Guardar informe Excel
                    df["Entrega_Afiliado"] = entregas_afiliado
                    df["Entrega_EPS"] = entregas_eps
                    df["Entrega_Empleado"] = entregas_empleado
                    df["Entrega_ARL"] = entregas_arl

                    ruta_excel_salida = os.path.join(dir_trabajo, "Resultados_PDF", "Resultado_Entregas.xlsx")
                    os.makedirs(os.path.dirname(ruta_excel_salida), exist_ok=True)
                    df.to_excel(ruta_excel_salida, index=False)

                    # Comprimir a ZIP
                    ruta_zip_salida = os.path.join(dir_trabajo, "Resultados_PDF.zip")
                    carpeta_a_zipear = os.path.join(dir_trabajo, "Resultados_PDF")

                    with zipfile.ZipFile(ruta_zip_salida, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        for root, dirs, files in os.walk(carpeta_a_zipear):
                            for file in files:
                                path_absoluto = os.path.join(root, file)
                                path_relativo = os.path.relpath(path_absoluto, carpeta_a_zipear)
                                zipf.write(path_absoluto, arcname=path_relativo)

                status_container.update(label="🎉 **¡Proceso completado con éxito!**", state="complete", expanded=False)

                # --- Muestras e Indicadores Visuales ---
                st.markdown("### 📊 Resumen del Procesamiento")
                m1, m2, m3 = st.columns(3)
                m1.metric("Total Registros", len(df))
                m2.metric("Con Evidencia Drive", entregas_empleado.count("Encontrado en Drive") + entregas_afiliado.count("Encontrado en Drive"))
                m3.metric("Con Evidencia E-Entrega", total_filas - entregas_afiliado.count("No encontrado en Drive"))

                st.markdown("<br>", unsafe_allow_html=True)
                
                with open(ruta_zip_salida, "rb") as f_zip:
                    st.download_button(
                        label="📦 Descargar Archivos Consolidados (ZIP)",
                        data=f_zip.read(),
                        file_name="Resultados_PDF.zip",
                        mime="application/zip"
                    )
