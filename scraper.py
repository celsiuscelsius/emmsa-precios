from playwright.sync_api import sync_playwright
from datetime import datetime
from supabase import create_client
import pytz
import json
import os
import time
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("❌ Error: No se encontraron las claves de Supabase.")
    exit(1)

def scrape_emmsa():
    lima = pytz.timezone("America/Lima")
    ahora = datetime.now(lima)
    hoy = ahora.strftime("%d/%m/%Y")
    nombre_archivo = ahora.strftime("%Y-%m-%d") + ".json"
    print(f"Fecha real de Lima: {hoy}")
    print(f"Archivo a guardar: {nombre_archivo}")

    MAX_INTENTOS = 3
    productos = []

    for intento in range(1, MAX_INTENTOS + 1):
        print(f"\nIntento {intento} de {MAX_INTENTOS}...")
        try:
            with sync_playwright() as p:
                headless = os.getenv("GITHUB_ACTIONS") == "true"
                browser = p.chromium.launch(
                    headless=headless,
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

                page.wait_for_selector("input[name='chkChanging']", timeout=30000)

                fecha_pagina = page.input_value("input[name='txtfecha1']")
                print(f"Fecha que tenía la página: {fecha_pagina}")

                page.fill("input[name='txtfecha1']", "")
                page.type("input[name='txtfecha1']", hoy)
                print(f"✓ Fecha corregida a: {hoy}")

                page.wait_for_timeout(500)
                page.check("input[name='chkChanging']")
                print("✓ Checkbox 'Todos' marcado")

                page.wait_for_timeout(1500)
                page.click("button:has-text('Consultar')")
                print("✓ Consultando, esperando resultados...")

                page.wait_for_load_state("networkidle", timeout=60000)
                page.wait_for_timeout(3000)

                filas = page.query_selector_all("table tr")
                print(f"Filas encontradas: {len(filas)}")

                for fila in filas[1:]:
                    celdas = fila.query_selector_all("td")
                    if len(celdas) >= 4:
                        try:
                            nombre     = celdas[0].inner_text().strip()
                            variedad   = celdas[1].inner_text().strip()
                            precio_min = float(celdas[2].inner_text().strip().replace(",", "."))
                            precio_max = float(celdas[3].inner_text().strip().replace(",", "."))
                            precio_avg = round((precio_min + precio_max) / 2, 2)

                            es_numero = nombre.replace('/', '').replace(' ', '').isdigit()

                            if nombre and not es_numero:
                                productos.append({
                                    "nombre":   nombre,
                                    "variedad": variedad,
                                    "min":      precio_min,
                                    "max":      precio_max,
                                    "avg":      precio_avg
                                })
                        except:
                            pass

                browser.close()

            if productos:
                print(f"✓ {len(productos)} productos extraídos correctamente")
                break  # salir del loop si todo fue bien

        except Exception as e:
            print(f"⚠ Error en intento {intento}: {e}")
            if intento < MAX_INTENTOS:
                espera = 30 * intento  # espera 30s, luego 60s
                print(f"Esperando {espera} segundos antes de reintentar...")
                time.sleep(espera)
            else:
                print("❌ Se agotaron los intentos.")
                exit(1)

    if not productos:
        print("⚠ No se encontraron productos.")
        exit(1)

    resultado = {"fecha": hoy, "productos": productos}

    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"✓ Guardado localmente: {nombre_archivo}")

    print("Subiendo a Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

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
    print(f"\nURL de hoy: {SUPABASE_URL}/storage/v1/object/public/precios/{nombre_archivo}")

    print("\nVista previa (primeros 5):")
    for prod in productos[:5]:
        print(f"  {prod['nombre']} ({prod['variedad']}) → min: {prod['min']} / max: {prod['max']} / prom: {prod['avg']}")

if __name__ == "__main__":
    scrape_emmsa()
