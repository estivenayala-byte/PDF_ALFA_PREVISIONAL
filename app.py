import io
import os
import re
import gc
import tempfile
import zipfile
import subprocess
from concurrent.futures import ThreadPoolExecutor
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
    page_title="Consolidador de Testigos - CODESS",
    page_icon="📑",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Estilos CSS Adaptativos usando Variables Nativas de Streamlit ---
st.markdown("""
    <style>
    :root {
        --codess-green: #5C9E31;
        --codess-green-dark: #427521;
        --codess-orange: #E67E22;
        --codess-orange-dark: #D35400;
    }

    .header-card {
        background-color: var(--secondary-background-color) !important;
        border: 1px solid rgba(128, 128, 128, 0.2) !important;
        border-left: 8px solid var(--codess-green) !important;
        padding: 1.5rem 2rem;
        border-radius: 16px;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.05);
        margin-bottom: 2rem;
        display: flex;
        align-items: center;
        gap: 2rem;
    }

    .header-logo-img {
        max-height: 65px;
        width: auto;
        object-fit: contain;
        background-color: transparent;
    }

    .header-text-container {
        display: flex;
        flex-direction: column;
        justify-content: center;
    }

    .header-title {
        color: var(--text-color) !important;
        font-size: 2.1rem;
        font-weight: 800;
        margin: 0;
        line-height: 1.2;
    }

    .header-subtitle {
        color: var(--text-color) !important;
        opacity: 0.8;
        font-size: 1rem;
        margin-top: 0.4rem;
        font-weight: 500;
    }

    .header-accent {
        color: var(--codess-orange) !important;
        font-weight: 700;
    }

    .stButton>button {
        width: 100%;
        background: linear-gradient(135deg, var(--codess-green) 0%, var(--codess-green-dark) 100%) !important;
        color: #FFFFFF !important;
        font-weight: 700 !important;
        font-size: 1.05rem !important;
        padding: 0.8rem 1.5rem !important;
        border-radius: 10px !important;
        border: none !important;
        box-shadow: 0 4px 12px rgba(92, 158, 49, 0.3) !important;
        transition: all 0.3s ease !important;
    }

    .stButton>button:hover {
        background: linear-gradient(135deg, var(--codess-orange) 0%, var(--codess-orange-dark) 100%) !important;
        box-shadow: 0 6px 18px rgba(230, 126, 34, 0.4) !important;
        transform: translateY(-2px);
    }

    [data-testid="stMetricValue"] {
        color: var(--codess-green) !important;
        font-weight: 800 !important;
    }
    </style>
""", unsafe_allow_html=True)

URL_LOGIN_EENTREGA = "https://codess.e-entrega.co/index.php"
NOMBRE_LOGO = "Logo Codess.png"

# --- Conexión y Diagnóstico de Google Drive API ---

def obtener_servicio_drive():
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
            pageSize=1
        ).execute()

        archivos = resultados.get("files", [])
        if archivos:
            file_id = archivos[0]["id"]
            nombre_archivo = archivos[0]["name"]

            # Descarga optimizada en bloques de 1 MB
            request = service.files().get_media(fileId=file_id)
            stream_descarga = io.BytesIO()
            downloader = MediaIoBaseDownload(stream_descarga, request, chunksize=1024*1024)
            
            done = False
            while not done:
                _, done = downloader.next_chunk()

            stream_descarga.seek(0)
            return {"nombre": nombre_archivo, "stream": stream_descarga}

    except Exception:
        pass

    return None

def buscar_varias_guias_drive_paralelo(service, lista_guias):
    if not service or not lista_guias:
        return {}
    
    resultados = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futuros = {
            executor.submit(buscar_y_descargar_drive_api, service, guia): guia 
            for guia in lista_guias if es_guia_valida(guia) and len(limpiar_guia(guia)) > 6
        }
        for futuro in futuros:
            guia_original = futuros[futuro]
            try:
                res = futuro.result()
                if res:
                    resultados[guia_original] = res
            except Exception:
                pass
    return resultados

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
        page.wait_for_selector('text="Generando testigo, espera un momento..."', state="detached", timeout=20000)
    except Exception:
        pass

def obtener_evento_tabla(page):
    try:
        page.wait_for_selector("#tablaestados tbody tr", timeout=5000)
        texto = page.locator("#tablaestados tbody tr").first.locator("td").nth(4).inner_text().strip()
        return texto if texto else "Sin Estado"
    except Exception:
        return "No encontrado"

def consultar_guia_eentrega(page, guia):
    try:
        page.wait_for_selector("#message", state="attached", timeout=12000)
        page.locator("#message").fill(guia)
        page.locator("#btn_Buscar").click()
        return True
    except Exception:
        return False

def descargar_testigo_en_memoria(page, reintentos=2):
    for intento in range(1, reintentos + 1):
        try:
            page.wait_for_selector("#ToolTables_tablaestados_1", state="visible", timeout=6000)
            with page.expect_download(timeout=25000) as download_info:
                page.locator("#ToolTables_tablaestados_1").click()

            esperar_modal_generando_testigo(page)

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
            except Exception:
                pass
    return None

# --- Panel Lateral (Sidebar) ---
with st.sidebar:
    st.subheader("🛠️ Estado de Servicios")
    drive_service = obtener_servicio_drive()
    conexion_ok = verificar_conexion_drive(drive_service)
    st.markdown("---")
    st.caption("🚀 **Corporación para el Desarrollo de la Seguridad Social**\n\nSistema Automático de Testigos v2.0")

# --- Encabezado Principal Adaptativo Nativamente ---
if os.path.exists(NOMBRE_LOGO):
    import base64
    with open(NOMBRE_LOGO, "rb") as image_file:
        encoded_logo = base64.b64encode(image_file.read()).decode()
    logo_html = f'<img src="data:image/png;base64,{encoded_logo}" class="header-logo-img" alt="Logo CODESS">'
else:
    logo_html = ""

st.markdown(f"""
    <div class="header-card">
        {logo_html}
        <div class="header-text-container">
            <h1 class="header-title">Consolidador de Testigos y Evidencias</h1>
            <p class="header-subtitle">Plataforma Automática <span class="header-accent">E-Entrega & Google Drive</span></p>
        </div>
    </div>
""", unsafe_allow_html=True)

# --- Sección de Entradas de Usuario ---
col_left, col_right = st.columns(2)

with col_left:
    st.markdown("#### 🔐 Credenciales E-Entrega")
    usr_eentrega = st.text_input("Correo electrónico corporativo", placeholder="ejemplo@codess.org.co")
    pass_eentrega = st.text_input("Contraseña E-Entrega", type="password")

with col_right:
    st.markdown("#### 📄 Archivo de Entrada")
    archivo_subido = st.file_uploader("Subir planilla en Excel o CSV con las guías", type=["csv", "xlsx"])

st.markdown("<br>", unsafe_allow_html=True)

# --- Botón de Ejecución y Procesamiento ---
if archivo_subido is not None:
    if not (usr_eentrega.strip() and pass_eentrega.strip()):
        st.warning("⚠️ Ingresa tus credenciales de E-Entrega para activar el botón de inicio.")
    else:
        st.success(f"Archivo listo para procesar: **{archivo_subido.name}**")

        if st.button("🚀 INICIAR PROCESAMIENTO AUTOMÁTICO"):
            with tempfile.TemporaryDirectory() as dir_trabajo:
                ruta_input = os.path.join(dir_trabajo, archivo_subido.name)
                with open(ruta_input, "wb") as f:
                    f.write(archivo_subido.getbuffer())

                if archivo_subido.name.endswith(".csv"):
                    df = pd.read_csv(ruta_input, sep=";", dtype=str)
                else:
                    df = pd.read_excel(ruta_input, dtype=str)

                entregas_afiliado, entregas_eps, entregas_empleado, entregas_arl = [], [], [], []

                status_container = st.status("⚡ **Ejecutando proceso optimizado de extracción...**", expanded=True)
                
                with status_container:
                    st.write("🌐 Iniciando motor de extracción Chromium...")
                    progress_bar = st.progress(0)

                    with sync_playwright() as p:
                        browser = p.chromium.launch(
                            headless=True,
                            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                        )
                        context = browser.new_context(accept_downloads=True)
                        page_eentrega = context.new_page()

                        # BLOQUEO SEGURO: Bloquear únicamente imágenes pesadas y fuentes (se mantiene CSS para no alterar el DOM)
                        page_eentrega.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,eot}", lambda route: route.abort())

                        # Login en E-Entrega
                        page_eentrega.goto(URL_LOGIN_EENTREGA)
                        page_eentrega.locator("#user").fill(usr_eentrega.strip())
                        page_eentrega.locator("#pass").fill(pass_eentrega.strip())
                        page_eentrega.locator("#login").click()

                        page_eentrega.wait_for_selector('span[lan="MENU_STATUS"]', timeout=30000)
                        page_eentrega.locator('span[lan="MENU_STATUS"]').click()
                        page_eentrega.get_by_text("Filtros avanzados").click()
                        page_eentrega.wait_for_selector("#message", state="attached", timeout=20000)

                        total_filas = len(df)
                        for idx, row in df.iterrows():
                            NUMERO_DOCUMENTO = str(row["DOCUMENTO"]).strip()
                            GUIA_AFILIADO = str(row.get("GUIA AFILIADO", "")).strip()
                            GUIA_EPS = str(row.get("GUIA EPS", "")).strip()
                            GUIA_EMPLEADOR = str(row.get("GUIA EMPLEADOR", "")).strip()
                            GUIA_ARL = str(row.get("GUIA ARL", "")).strip()
                            NOMBRE_SERVICIO = limpiar_nombre_carpeta(row.get("SERVICIO", ""))

                            st.write(f"🔎 Procesando registro **{idx + 1}/{total_filas}** — Documento: **{NUMERO_DOCUMENTO}**")
                            progress_bar.progress((idx + 1) / total_filas)

                            # Liberación periódica de RAM cada 15 registros para prevenir reinicios del servidor
                            if idx > 0 and idx % 15 == 0:
                                gc.collect()

                            if not es_guia_valida(GUIA_AFILIADO):
                                entregas_afiliado.append("N/A - Guía Inválida")
                                entregas_eps.append("N/A - Omitido por Afiliado")
                                entregas_empleado.append("N/A - Omitido por Afiliado")
                                entregas_arl.append("N/A - Omitido por Afiliado")
                                continue

                            # Búsqueda ultra-rápida paralela en Google Drive
                            guias_registro = [GUIA_AFILIADO, GUIA_EPS, GUIA_EMPLEADOR, GUIA_ARL]
                            resultados_drive = buscar_varias_guias_drive_paralelo(drive_service, guias_registro)

                            archivos_adjuntos_afiliado = []
                            testigo_afiliado = None

                            guia_afiliado_limpia = limpiar_guia(GUIA_AFILIADO)
                            if len(guia_afiliado_limpia) > 6:
                                pdf_drive = resultados_drive.get(GUIA_AFILIADO)
                                if pdf_drive:
                                    testigo_afiliado = pdf_drive
                                    entregas_afiliado.append("Encontrado en Drive")
                                else:
                                    entregas_afiliado.append("No encontrado en Drive")
                            else:
                                if consultar_guia_eentrega(page_eentrega, guia_afiliado_limpia):
                                    txt_afiliado = obtener_evento_tabla(page_eentrega)
                                    entregas_afiliado.append(txt_afiliado)

                                    try:
                                        page_eentrega.locator(".btnVerMensaje").first.click()
                                        page_eentrega.wait_for_selector(".modal-body, #modalMensaje, .modal-content", state="visible", timeout=5000)

                                        archivos_adjuntos = page_eentrega.locator('text=/.+\\.pdf/i')
                                        for i in range(archivos_adjuntos.count()):
                                            try:
                                                with page_eentrega.expect_download(timeout=15000) as download_info:
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
                                    except Exception:
                                        pass

                                    testigo_afiliado = descargar_testigo_en_memoria(page_eentrega)
                                else:
                                    entregas_afiliado.append("Error de Búsqueda Web")

                            guia_testigos = [("EPS", GUIA_EPS), ("EMPLEADOR", GUIA_EMPLEADOR), ("ARL", GUIA_ARL)]
                            otros_testigos = []

                            for nombre_entidad, codigo_guia in guia_testigos:
                                if es_guia_valida(codigo_guia):
                                    guia_limpia = limpiar_guia(codigo_guia)
                                    if len(guia_limpia) > 6:
                                        pdf_drive = resultados_drive.get(codigo_guia)
                                        if pdf_drive:
                                            otros_testigos.append(pdf_drive)
                                            txt_estado = "Encontrado en Drive"
                                        else:
                                            txt_estado = "No encontrado en Drive"
                                    else:
                                        if consultar_guia_eentrega(page_eentrega, guia_limpia):
                                            txt_estado = obtener_evento_tabla(page_eentrega)
                                            testigo_entidad = descargar_testigo_en_memoria(page_eentrega)
                                            if testigo_entidad:
                                                otros_testigos.append(testigo_entidad)
                                        else:
                                            txt_estado = "Error de Búsqueda Web"

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

                status_container.update(label="⚡ **¡Proceso completado a máxima velocidad y estabilidad!**", state="complete", expanded=False)

                # --- Resumen e Indicadores Visuales CODESS ---
                st.subheader("📊 Indicadores del Procesamiento")
                m1, m2, m3 = st.columns(3)
                m1.metric("Total Registros Procesados", len(df))
                m2.metric("Archivos Hallados en Drive", entregas_empleado.count("Encontrado en Drive") + entregas_afiliado.count("Encontrado en Drive"))
                m3.metric("Testigos Exitosos E-Entrega", total_filas - entregas_afiliado.count("No encontrado en Drive"))

                st.markdown("<br>", unsafe_allow_html=True)
                
                with open(ruta_zip_salida, "rb") as f_zip:
                    st.download_button(
                        label="📦 Descargar Resultados Consolidados (.ZIP)",
                        data=f_zip.read(),
                        file_name="Resultados_PDF_CODESS.zip",
                        mime="application/zip"
                    )
