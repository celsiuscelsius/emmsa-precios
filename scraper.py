from playwright.sync_api import sync_playwright
from datetime import datetime
from supabase import create_client
import pytz
import json
import os
import time
import urllib.request
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("❌ Error: No se encontraron las claves de Supabase.")
    exit(1)

def archivo_ya_existe(nombre_archivo):
    """
    Verifica si el archivo existe haciendo un ping directo a su URL pública.
    NO necesita listar el bucket — compatible con bucket sin política de listado.
    """
    url = f"{SUPABASE_URL}/storage/v1/object/public/precios/{nombre_archivo}"
    try:
        req = urllib.request.Request(url, method="HEAD")
        urllib.request.urlopen(req)
        print(f"✓ El archivo {nombre_archivo} ya existe en Supabase.")
        print("No hay datos nuevos todavía. Finalizando sin hacer scraping.")
        return True
    except:
        return False

def parsear_float(texto):
    """Convierte texto a float de forma segura."""
    try:
        return float(texto.strip().replace(",", "."))
    except:
        return None

def extraer_productos(filas):
    """Extrae productos de las filas de la tabla con promedio oficial de EMMSA."""
    productos = []
    for fila in filas[1:]:
        celdas = fila.query_selector_all("td")
        textos = [c.inner_text().strip() for c in celdas]

        if len(celdas) < 4:
            continue
        try:
            nombre   = textos[0]
            variedad = textos[1]
            p_min    = parsear_float(textos[2])
            p_max    = parsear_float(textos[3])

            # Promedio oficial de EMMSA (columna 5)
            p_avg = None
            if len(textos) >= 5 and textos[4] != '':
                p_avg = parsear_float(textos[4])

            # Si no hay promedio oficial, calcular como respaldo
            if p_avg is None and p_min is not None and p_max is not None:
                p_avg = round((p_min + p_max) / 2, 2)

            es_numero = nombre.replace('/', '').replace(' ', '').isdigit()

            if nombre and not es_numero and p_min is not None and p_max is not None:
                productos.append({
                    "nombre":   nombre,
                    "variedad": variedad,
                    "min":      p_min,
                    "max":      p_max,
                    "avg":      p_avg
                })
        except Exception as e:
            print(f"  ⚠ Fila descartada: {e} → {textos}")

    return productos

def scrape_emmsa():
    lima = pytz.timezone("America/Lima")
    ahora = datetime.now(lima)
    hoy = ahora.strftime("%d/%m/%Y")
    nombre_archivo = ahora.strftime("%Y-%m-%d") + ".json"

    print(f"Fecha real de Lima: {hoy}")
    print(f"Archivo objetivo: {nombre_archivo}")

    # Verificar si ya tenemos datos de hoy — sin listar el bucket
    if archivo_ya_existe(nombre_archivo):
        exit(0)

    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    MAX_INTENTOS = 3
    productos = []

    for intento in range(1, MAX_INTENTOS + 1):
        print(f"\nIntento {intento} de {MAX_INTENTOS}...")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox"]
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                page = context.new_page()

                page.goto(
                    "https://old.emmsa.com.pe/emmsa_spv/rpEstadistica/rpt_precios-diarios-web.php",
                    timeout=90000,
                    wait_until="domcontentloaded"
                )

                print("Esperando 10 segundos para que la web cargue completamente...")
                page.wait_for_timeout(10000)
                page.wait_for_selector("input[name='chkChanging']", timeout=30000)

                fecha_pagina = page.input_value("input[name='txtfecha1']")
                print(f"Fecha que tenía la página: {fecha_pagina}")

                page.fill("input[name='txtfecha1']", "")
                page.type("input[name='txtfecha1']", hoy)
                print(f"✓ Fecha corregida a: {hoy}")

                page.wait_for_timeout(5000)
                page.check("input[name='chkChanging']")
                print("✓ Checkbox 'Todos' marcado")

                page.wait_for_timeout(5000)
                page.click("button:has-text('Consultar')")
                print("✓ Consultando, esperando resultados...")

                page.wait_for_load_state("networkidle", timeout=60000)
                print("Pausa de 5 segundos finales para procesar la tabla...")
                page.wait_for_timeout(5000)

                filas = page.query_selector_all("table tr")
                print(f"Filas encontradas: {len(filas)}")

                # Diagnóstico de columnas
                for fila in filas[1:2]:
                    celdas = fila.query_selector_all("td")
                    if celdas:
                        print(f"  Columnas detectadas: {[c.inner_text().strip() for c in celdas]}")

                productos_intento = extraer_productos(filas)
                browser.close()

                print(f"Productos extraídos en este intento: {len(productos_intento)}")

                if productos_intento:
                    productos = productos_intento
                    print(f"✓ {len(productos)} productos extraídos correctamente")
                    break
                else:
                    print("⚠ La tabla cargó pero sin productos. EMMSA aún no publicó datos.")
                    if intento < MAX_INTENTOS:
                        espera = 30 * intento
                        print(f"Esperando {espera} segundos antes de reintentar...")
                        time.sleep(espera)

        except Exception as e:
            print(f"⚠ Error en intento {intento}: {e}")
            if intento < MAX_INTENTOS:
                espera = 30 * intento
                print(f"Esperando {espera} segundos antes de reintentar...")
                time.sleep(espera)
            else:
                print("❌ Se agotaron los intentos.")
                exit(1)

    if not productos:
        print("⚠ No se encontraron productos. EMMSA no tiene datos aún.")
        exit(0)

    resultado = {"fecha": hoy, "productos": productos}

    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"✓ Guardado localmente: {nombre_archivo}")

    print("Subiendo a Supabase...")
    with open(nombre_archivo, "rb") as f:
        supabase.storage.from_("precios").upload(
            path=nombre_archivo,
            file=f,
            file_options={"content-type": "application/json", "upsert": "true"}
        )
    with open(nombre_archivo, "rb") as f:
        supabase.storage.from_("precios").upload(
            path="latest.json",
            file=f,
            file_options={"content-type": "application/json", "upsert": "true"}
        )

    print(f"✓ Subido como {nombre_archivo} y latest.json")
    print(f"\nURL: {SUPABASE_URL}/storage/v1/object/public/precios/{nombre_archivo}")

    print("\nVista previa (primeros 5):")
    for prod in productos[:5]:
        print(f"  {prod['nombre']} ({prod['variedad']}) → min:{prod['min']} / max:{prod['max']} / avg:{prod['avg']}")

if __name__ == "__main__":
    scrape_emmsa()
