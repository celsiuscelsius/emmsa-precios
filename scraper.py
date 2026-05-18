from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
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

def archivo_tiene_datos(nombre_archivo):
    """
    Verifica que el archivo exista en Supabase Y tenga productos reales.
    Retorna True solo si hay al menos 1 producto.
    """
    url = f"{SUPABASE_URL}/storage/v1/object/public/precios/{nombre_archivo}"
    try:
        contenido = urllib.request.urlopen(url).read()
        datos = json.loads(contenido)
        return len(datos.get("productos", [])) > 0
    except:
        return False

def parsear_float(texto):
    try:
        return float(texto.strip().replace(",", "."))
    except:
        return None

def extraer_productos(filas):
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

            p_avg = None
            if len(textos) >= 5 and textos[4] != '':
                p_avg = parsear_float(textos[4])

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

def scrape_fecha(fecha_dt, supabase):
    hoy = fecha_dt.strftime("%d/%m/%Y")
    nombre_archivo = fecha_dt.strftime("%Y-%m-%d") + ".json"

    print(f"\n{'='*50}")
    print(f"Scrapeando fecha: {hoy} → {nombre_archivo}")
    print(f"{'='*50}")

    if archivo_tiene_datos(nombre_archivo):
        print(f"✓ {nombre_archivo} ya existe con datos reales. Saltando.")
        return True

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
                    print("⚠ Tabla sin productos. EMMSA no publicó datos para esta fecha.")
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

    if not productos:
        print(f"⚠ Sin datos reales para {hoy} (domingo, feriado o aún no publicado).")
        return False

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
    print(f"✓ Subido: {nombre_archivo}")
    print(f"URL: {SUPABASE_URL}/storage/v1/object/public/precios/{nombre_archivo}")

    print("\nVista previa (primeros 5):")
    for prod in productos[:5]:
        print(f"  {prod['nombre']} ({prod['variedad']}) → min:{prod['min']} / max:{prod['max']} / avg:{prod['avg']}")

    return True

def obtener_dias_faltantes(fecha_hoy, max_dias_atras=7):
    """
    Busca desde HOY hacia atrás 7 días.
    Solo marca como faltante si NO tiene datos reales (ignora JSONs vacíos).
    """
    faltantes = []
    for i in range(0, max_dias_atras):
        fecha = fecha_hoy - timedelta(days=i)
        nombre_archivo = fecha.strftime("%Y-%m-%d") + ".json"
        if archivo_tiene_datos(nombre_archivo):
            print(f"✓ Con datos reales: {nombre_archivo} (hace {i} día(s))")
        else:
            print(f"⚠ Sin datos reales: {nombre_archivo} (hace {i} día(s))")
            faltantes.append(fecha)

    faltantes.reverse()
    return faltantes

def scrape_emmsa():
    lima = pytz.timezone("America/Lima")
    ahora = datetime.now(lima)

    print(f"Fecha y hora actual Lima: {ahora.strftime('%d/%m/%Y %H:%M:%S')}")

    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    print("\n🔍 Verificando días con datos reales (hoy + últimos 6 días)...")
    dias_faltantes = obtener_dias_faltantes(ahora, max_dias_atras=7)

    if dias_faltantes:
        print(f"\n📅 {len(dias_faltantes)} día(s) sin datos reales. Intentando scrapear...")
        for fecha_faltante in dias_faltantes:
            exito = scrape_fecha(fecha_faltante, supabase)
            if not exito:
                print(f"  ⚠ {fecha_faltante.strftime('%d/%m/%Y')} no tiene datos (domingo/feriado/no publicado).")
            time.sleep(10)
    else:
        print("✓ Todos los días tienen datos reales.")

    # ── SIEMPRE actualizar latest.json con el día más reciente que tenga datos ──
    print("\n📌 Actualizando latest.json con la fecha más reciente con datos reales...")
    for i in range(0, 7):
        fecha_candidata = ahora - timedelta(days=i)
        nombre_candidato = fecha_candidata.strftime("%Y-%m-%d") + ".json"
        if archivo_tiene_datos(nombre_candidato):
            print(f"✓ Fecha más reciente con datos: {nombre_candidato}")
            url = f"{SUPABASE_URL}/storage/v1/object/public/precios/{nombre_candidato}"
            contenido = urllib.request.urlopen(url).read()
            supabase.storage.from_("precios").upload(
                path="latest.json",
                file=contenido,
                file_options={"content-type": "application/json", "upsert": "true"}
            )
            print(f"✓ latest.json actualizado con datos de: {fecha_candidata.strftime('%d/%m/%Y')}")
            break
    else:
        print("⚠ No se encontró ningún día con datos reales en los últimos 7 días.")

    print("\n✅ Proceso completado.")

if __name__ == "__main__":
    scrape_emmsa()
